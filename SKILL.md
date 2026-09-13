---
name: bilibili-audio
description: Use when the user wants to download a Bilibili (哔哩哔哩/B站) video as an audio file, convert a bilibili.com/BV or b23.tv video to high-quality m4a audio, extract audio from a B站 link, or rip a video podcast to audio. Triggers on bilibili.com URLs, BV id 视频链接, "转音频", "转 m4a", "提取音频", "下载音频".
---

# Bilibili 视频转高品质 M4A 音频

B 站 DASH 音频是独立的 AAC 流（部分视频另有 FLAC 无损 / 杜比流）。本 skill **只做无损封装，绝不转码**，默认输出到 `~/Downloads`，文件名为视频标题。

下文 `<skill-dir>` 指本 `SKILL.md` 所在目录（Claude Code 加载 skill 时会显示 "Base directory for this skill"）。

## 依赖

- Python 3.9+
- `yt-dlp`：`python -m pip install -U yt-dlp`
- `ffmpeg` 与 `ffprobe`：Windows `winget install --id Gyan.FFmpeg -e`；macOS `brew install ffmpeg`；Linux 用包管理器

脚本启动时会自动检查，缺什么会直接报出名字（退出码 3）。

## 路径 A：一条命令直接下载（先走这条）

```bash
python "<skill-dir>/scripts/download_bilibili_audio.py" "<视频URL>" [--output-dir <目录>]
```

- 支持 `https://www.bilibili.com/video/BV...`（可带 `?p=N` 选分 P）、`m.bilibili.com`、`b23.tv` 短链。
- 自动选最高码率的 m4a 纯音频流（`ba[ext=m4a]/ba`），封装成 `.m4a`，再用 `ffprobe` 校验必须有音轨且时长 > 0。
- 成功时 stdout 输出一行 JSON：`{"status":"ok","file":...,"codec":"aac","bit_rate":...,"duration":...}`，把 `file` 和时长告诉用户即可。

| 退出码 | 含义 | 下一步 |
|---|---|---|
| 0 | 成功 | 汇报 JSON 结果 |
| 2 | 链接不是 B 站视频 | 请用户提供 BV 视频链接 |
| 3 | 缺依赖 | 按上面「依赖」安装后重试 |
| 4 | **HTTP 412**（B 站风控拒绝） | 立即转路径 B，**不要**反复换 cookie/UA 重试 |
| 1 | yt-dlp 其他失败 | 看 stderr；token/网络问题可重试一次，否则转路径 B |
| 5 | 文件未通过 ffprobe 校验 | 文件保留在原处供排查，转路径 B |

多个链接就逐个调用。大会员专享音质 yt-dlp 拿不到时，会下载公开可得的最高音质，需告知用户。

## 路径 B：浏览器取流 + 本地服务器（HTTP 412 时的兜底）

需要能操作已登录 B 站的浏览器的 Agent 工具（例如 Claude in Chrome 的 `navigate` + `javascript_tool`）。没有浏览器工具时，如实告诉用户路径 A 被风控、建议稍后重试。

原理：音频 CDN 直链靠 URL 内签名 token + `Referer` 头鉴权，**不靠 cookie**。页面里的 `window.__playinfo__` 含带 token 的直链，交给本地服务器在服务端下载，再 `ffmpeg -c copy` 封装。

**安全要求：Agent must not persist cookies, tokens, or browser profiles —— 不读取/导出 cookie，不把带 token 的直链写进聊天、日志或文件。**

### B1. 后台启动本地服务器

```bash
python "<skill-dir>/scripts/bili_browser_fetch.py" --info-file "<临时目录>/bili_srv_info.json" [--out-dir <目录>]
```

以后台方式运行；等 info 文件出现后读取其中的 `port` 与 `nonce`。`GET http://127.0.0.1:<port>/ping` 应返回 `ok`。服务器自动选端口、nonce 鉴权、断点续传、封装、ffprobe 时长校验，任务结束约 45 秒后自动退出。

### B2. 打开视频页并读元数据（只回传非敏感字段）

用浏览器工具导航到视频页，然后执行：

```js
(() => { const s=window.__INITIAL_STATE__||{}, v=s.videoData||{}, d=((window.__playinfo__||{}).data||{}).dash||{};
  return JSON.stringify({ title:v.title||document.title, duration:v.duration||0,
    pages:(v.pages||[]).length, owner:(v.owner||{}).name||'',
    audioBandwidths:(d.audio||[]).map(a=>a.bandwidth).sort((a,b)=>b-a),
    hasFlac:!!(d.flac&&d.flac.audio) }); })()
```

绝不在 JS 返回值里带 `baseUrl`（带 token 的 URL 会被浏览器扩展拦截，也不应进入对话）。

### B3. 页面顶级导航把直链交给本地服务器

替换 `<PORT>` / `<NONCE>` 后在页面执行（顶级导航不受页面 CSP / Private Network Access 限制，无需点击）：

```js
(() => { const PORT=<PORT>, K='<NONCE>';
  const v=(window.__INITIAL_STATE__||{}).videoData||{}, d=window.__playinfo__.data.dash;
  const src=(d.audio||[]).slice().sort((a,b)=>b.bandwidth-a.bandwidth)[0];
  location.href='http://127.0.0.1:'+PORT+'/save?k='+K+'&ext=m4a&dur='+(v.duration||0)
    +'&t='+encodeURIComponent(v.title||document.title)
    +'&a='+encodeURIComponent((v.owner||{}).name||'')
    +'&u='+encodeURIComponent(src.baseUrl);
  return 'sent m4a bw='+src.bandwidth; })()
```

多 P 视频：`__playinfo__` 只含当前 P，导航到 `视频URL?p=N` 后重复 B2/B3；同一服务器会排队处理。

### B4. 轮询直到完成

`GET http://127.0.0.1:<port>/status?k=<nonce>`，看每个 job 的 `phase`：`queued → downloading → remuxing → done`（或 `error`）。

- `done`：`final_path` 即成品，`duration` 应与页面时长一致。
- `message` 含 `WARNING`（时长明显偏短）= 可能是试看片段，需登录后重取。
- `error` 含 token expired / 403 / 410：刷新页面，重做 B3。

## 常见坑

| 坑 | 规避 |
|---|---|
| yt-dlp 时而 HTTP 412（风控时紧时松） | 路径 A 只试一次，412 立即转路径 B |
| `--cookies-from-browser` 在 Windows Chrome/Edge 上因 App-Bound Encryption 失败 | 不要碰 cookie，路径 B 不需要 |
| 页面内 `fetch` 到 127.0.0.1 被 CSP 拦截 | 用 `location.href` 顶级导航 |
| Windows PowerShell 5.1 命令内联中文路径会乱码 | 不在命令里写中文字面量，路径从 JSON 读取或交给 Python 处理 |
