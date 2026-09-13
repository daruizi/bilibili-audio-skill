# bilibili-audio skill

An Agent Skill for Claude Code, Codex, and other Agent Skills-compatible tools that downloads a Bilibili (B站) video's best audio stream as a lossless-remuxed, ffprobe-verified `.m4a` file.

- **Path A**: one `yt-dlp` attempt via `scripts/download_bilibili_audio.py` (best m4a audio-only stream, no transcoding, JSON result).
- **Path B**: when Bilibili answers `HTTP 412`, a browser-capable agent reads the stream URL from the logged-in page and hands it to `scripts/bili_browser_fetch.py`, a local download server. No cookies or tokens are persisted.

## Requirements

- Python 3.9+
- `yt-dlp` (`python -m pip install -U yt-dlp`)
- `ffmpeg` and `ffprobe` on `PATH`

## Install

Claude Code (personal skill):

```bash
git clone https://github.com/daruizi/bilibili-audio-skill.git ~/.claude/skills/bilibili-audio
```

Or per project: clone into `<project>/.claude/skills/bilibili-audio`.

Codex:

```bash
git clone https://github.com/daruizi/bilibili-audio-skill.git ~/.codex/skills/bilibili-audio
```

Restart the agent session so the skill is discovered.

## Use

Ask the agent, e.g. "把这个 B 站视频转成 m4a：https://www.bilibili.com/video/BV...". In Claude Code you can also type `/bilibili-audio <url>`.

Run the helper directly:

```bash
python scripts/download_bilibili_audio.py "https://www.bilibili.com/video/BV1GJ411x7h7" --output-dir ~/Downloads
```

Output on success:

```json
{"status": "ok", "file": ".../<title>.m4a", "codec": "aac", "bit_rate": 204000, "duration": 212.1}
```

Exit codes: `0` ok, `1` yt-dlp failure, `2` invalid URL, `3` missing dependency, `4` HTTP 412 (use Path B), `5` ffprobe verification failed.

## Test

```bash
python -m unittest discover -v
```

Tests are offline; subprocess calls are injected.
