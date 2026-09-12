# Bilibili Audio Skill Design

## Goal

Publish a portable Agent Skill that lets Codex, Claude Code, and other Agent Skills-compatible tools download a Bilibili video as the highest available audio-quality M4A without exposing account credentials.

## Architecture

The repository is a standalone skill folder. `SKILL.md` is the concise, agent-facing workflow: discover dependencies, make one direct `yt-dlp` attempt, then use an optional browser-assisted recovery path when the site rejects direct playback API requests. `scripts/download_bilibili_audio.py` owns deterministic work that agents would otherwise reproduce inconsistently: URL validation, selection of the best audio-only format, M4A passthrough/remuxing, and `ffprobe` verification.

The script depends on locally installed `yt-dlp`, `ffmpeg`, and `ffprobe`; it must never persist cookies, tokens, or browser profiles. It accepts a public Bilibili URL and an optional output directory. A successful run prints a machine-readable record containing the output file, codec, bitrate, and duration.

## Files

| Path | Responsibility |
| --- | --- |
| `SKILL.md` | Trigger conditions, portable workflow, limits, and recovery guidance. |
| `scripts/download_bilibili_audio.py` | Download, non-lossy M4A packaging, and media inspection. |
| `tests/test_download_bilibili_audio.py` | Unit tests for URL validation, command construction, and inspection parsing. |
| `README.md` | Install and invocation examples for Codex, Claude Code, and compatible agents. |

## Data Flow

1. The Agent receives a Bilibili video URL and loads the skill.
2. The skill checks for Python, `yt-dlp`, `ffmpeg`, and `ffprobe`.
3. The script invokes `yt-dlp` once, preferring `ba[ext=m4a]` and otherwise the best available audio-only stream; it preserves audio rather than transcoding.
4. The script uses `ffprobe` to require a non-empty audio stream and a positive duration, then emits JSON describing the artifact.
5. For `HTTP 412`, expired signed media URLs, or premium-only quality, the skill gives a browser-capable Agent a safe, token-local recovery procedure. It does not copy cookies or tokens into logs or files.

## Failure Handling

* Missing dependencies: exit before starting the download and name the missing executable.
* Invalid or unsupported URL: exit with a clear validation error.
* `yt-dlp` failure: preserve its diagnostic, return a non-zero status, and do not retry variants indefinitely.
* Invalid output: fail after `ffprobe` and leave the original downloaded artifact untouched for diagnosis.
* Premium-only streams: use the best publicly available stream and report that account-gated formats were unavailable.

## Verification

Tests run without network access and prove validation, subprocess argument construction, failure propagation, and media-inspection behavior. A manual integration check with a public video verifies that the generated M4A contains AAC audio and a positive duration. The skill validator checks its manifest and description.
