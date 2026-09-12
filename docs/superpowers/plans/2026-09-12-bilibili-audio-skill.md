# Bilibili Audio Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a portable Agent Skill and tested Python helper that downloads public Bilibili audio as verified M4A.

**Architecture:** `SKILL.md` routes Agents through one direct download attempt and a browser-assisted fallback. A Python helper owns the deterministic download and validation behavior; unit tests inject subprocess results, never use network access.

**Tech Stack:** Markdown Agent Skills format, Python standard library, yt-dlp, ffmpeg/ffprobe, unittest.

---

### Task 1: Define the helper contract with failing tests

**Files:**
- Create: `tests/test_download_bilibili_audio.py`
- Create: `scripts/download_bilibili_audio.py`

- [ ] **Step 1: Write failing tests**

```python
def test_rejects_non_bilibili_url(self):
    with self.assertRaisesRegex(ValueError, "Bilibili video URL"):
        module.validate_url("https://example.com/video")

def test_builds_audio_only_yt_dlp_command(self):
    command = module.build_download_command("https://www.bilibili.com/video/BV1x", Path("out"))
    self.assertIn("ba[ext=m4a]/ba", command)
    self.assertIn("--remux-video", command)
```

- [ ] **Step 2: Run test to verify RED**

Run: `python -m unittest tests/test_download_bilibili_audio.py -v`

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement the minimal helper**

```python
def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in {"www.bilibili.com", "bilibili.com"} or "/video/BV" not in parsed.path:
        raise ValueError("Expected a public Bilibili video URL")
    return url

def build_download_command(url: str, output_dir: Path) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", "-f", "ba[ext=m4a]/ba", "--remux-video", "m4a", "-P", str(output_dir), url]
```

- [ ] **Step 4: Run test to verify GREEN**

Run: `python -m unittest tests/test_download_bilibili_audio.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add scripts/download_bilibili_audio.py tests/test_download_bilibili_audio.py && git commit -m "feat: add verified Bilibili audio downloader"`

### Task 2: Write and validate the portable skill

**Files:**
- Create: `SKILL.md`
- Create: `README.md`
- Modify: `tests/test_download_bilibili_audio.py`

- [ ] **Step 1: Add a failing skill-content check**

```python
def test_skill_has_portable_trigger_and_fallback(self):
    body = Path("SKILL.md").read_text(encoding="utf-8")
    self.assertIn("name: bilibili-audio", body)
    self.assertIn("HTTP 412", body)
    self.assertIn("must not persist cookies", body)
```

- [ ] **Step 2: Run it to verify RED**

Run: `python -m unittest tests/test_download_bilibili_audio.py -v`

Expected: FAIL because `SKILL.md` is absent.

- [ ] **Step 3: Add concise agent instructions and installation guidance**

```markdown
---
name: bilibili-audio
description: Use when a user wants to download, extract, or convert a Bilibili BV video into an M4A audio file.
---

# Bilibili Audio

Run `python scripts/download_bilibili_audio.py <url>`. Make one direct attempt; for HTTP 412, use a browser-capable Agent to obtain a fresh DASH audio URL without disclosing it in chat or files.
```

- [ ] **Step 4: Run all checks**

Run: `python -m unittest discover -v` and `python C:/Users/Jerryzhang/.codex/skills/.system/skill-creator/scripts/quick_validate.py .`

Expected: all tests pass and the validator accepts the skill manifest.

- [ ] **Step 5: Commit and push**

Run: `git add SKILL.md README.md tests/test_download_bilibili_audio.py && git commit -m "docs: publish portable Bilibili audio skill" && git push`

## Self-review

The plan covers the entry point, reusable automation, no-cookie behavior, direct and browser-assisted paths, dependency errors, and unit plus integration-ready verification. It contains no placeholders.
