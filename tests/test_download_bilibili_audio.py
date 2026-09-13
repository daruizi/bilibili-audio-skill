import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from scripts.download_bilibili_audio import (
    EXIT_HTTP_412,
    EXIT_INVALID_URL,
    EXIT_MISSING_DEPENDENCY,
    EXIT_OK,
    EXIT_VERIFICATION_FAILED,
    build_download_command,
    find_missing_dependencies,
    inspect_media_json,
    main,
    validate_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VIDEO_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


def all_present(_name):
    return "/usr/bin/tool"


def spec_present(_name):
    return object()


class ValidateUrlTests(unittest.TestCase):
    def test_rejects_non_bilibili_and_non_bv_urls(self):
        invalid_urls = (
            "https://example.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/av170001",
            "https://www.bilibili.com/read/cv1",
            "https://b23.tv/",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_url(url)

    def test_accepts_bv_urls_with_part_query_and_short_links(self):
        for url in (VIDEO_URL + "?p=2", "https://m.bilibili.com/video/BV1xx411c7mD", "https://b23.tv/abc123"):
            with self.subTest(url=url):
                self.assertEqual(validate_url(url), url)


class BuildDownloadCommandTests(unittest.TestCase):
    def test_selects_m4a_audio_and_remuxes_to_m4a(self):
        output_directory = Path("downloads")

        command = build_download_command(VIDEO_URL, output_directory)

        self.assertEqual(command[:3], [sys.executable, "-m", "yt_dlp"])
        self.assertEqual(command[command.index("-f") + 1], "ba[ext=m4a]/ba")
        self.assertEqual(command[command.index("--remux-video") + 1], "m4a")
        self.assertEqual(command[command.index("--print") + 1], "after_move:filepath")
        self.assertIn("--no-playlist", command)
        self.assertEqual(command[command.index("-o") + 1], str(output_directory / "%(title)s.%(ext)s"))
        self.assertEqual(command[-1], VIDEO_URL)


class FindMissingDependenciesTests(unittest.TestCase):
    def test_names_every_missing_tool(self):
        missing = find_missing_dependencies(which=lambda _name: None, find_spec=lambda _name: None)

        self.assertEqual(missing, ["yt-dlp", "ffmpeg", "ffprobe"])

    def test_returns_empty_list_when_all_tools_exist(self):
        self.assertEqual(find_missing_dependencies(which=all_present, find_spec=spec_present), [])


class InspectMediaJsonTests(unittest.TestCase):
    def test_rejects_output_without_an_audio_stream(self):
        payload = {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video"}]}

        with self.assertRaises(ValueError):
            inspect_media_json(payload)

    def test_rejects_output_without_positive_duration(self):
        payload = {"format": {"duration": "0"}, "streams": [{"codec_type": "audio"}]}

        with self.assertRaises(ValueError):
            inspect_media_json(payload)

    def test_returns_media_record_for_audio_with_positive_duration(self):
        payload = {
            "format": {"duration": "12.5", "bit_rate": "200000"},
            "streams": [{"codec_type": "audio", "codec_name": "aac", "bit_rate": "192000"}, {"codec_type": "video"}],
        }

        media = inspect_media_json(payload)

        self.assertEqual(media.duration, 12.5)
        self.assertEqual(media.audio_stream_count, 1)
        self.assertEqual(media.codec, "aac")
        self.assertEqual(media.bit_rate, 192000)


class FakeRunner:
    """Stands in for subprocess.run: first call is yt-dlp, second is ffprobe."""

    def __init__(self, *results):
        self.results = list(results)
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        return self.results.pop(0)


def completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class MainTests(unittest.TestCase):
    def run_main(self, argv, runner, which=all_present, find_spec=spec_present):
        stdout, stderr = io.StringIO(), io.StringIO()
        stdout.reconfigure = lambda **_kwargs: None
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv, run=runner, which=which, find_spec=find_spec)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_invalid_url_exits_before_running_anything(self):
        runner = FakeRunner()

        code, _, stderr = self.run_main(["https://example.com/x"], runner)

        self.assertEqual(code, EXIT_INVALID_URL)
        self.assertEqual(runner.commands, [])
        self.assertIn("Bilibili", stderr)

    def test_missing_dependency_exits_before_download(self):
        runner = FakeRunner()

        code, _, stderr = self.run_main([VIDEO_URL], runner, which=lambda _name: None)

        self.assertEqual(code, EXIT_MISSING_DEPENDENCY)
        self.assertEqual(runner.commands, [])
        self.assertIn("ffmpeg", stderr)

    def test_http_412_returns_fallback_exit_code(self):
        runner = FakeRunner(completed(1, stderr="ERROR: HTTP Error 412: Precondition Failed"))

        with self.subTest("412"):
            code, _, stderr = self.run_main([VIDEO_URL, "--output-dir", "out-test"], runner)
        Path("out-test").rmdir()

        self.assertEqual(code, EXIT_HTTP_412)
        self.assertIn("browser-assisted fallback", stderr)

    def test_success_prints_verified_json_record(self):
        probe = {
            "format": {"duration": "212.1"},
            "streams": [{"codec_type": "audio", "codec_name": "aac", "bit_rate": "204000"}],
        }
        runner = FakeRunner(completed(0, stdout="out-test/Song.m4a\n"), completed(0, stdout=json.dumps(probe)))

        code, stdout, _ = self.run_main([VIDEO_URL, "--output-dir", "out-test"], runner)
        Path("out-test").rmdir()

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(runner.commands[1][0], "ffprobe")
        record = json.loads(stdout)
        self.assertEqual(record["file"], str(Path("out-test/Song.m4a")))
        self.assertEqual(record["codec"], "aac")
        self.assertEqual(record["bit_rate"], 204000)
        self.assertEqual(record["duration"], 212.1)

    def test_rejects_file_that_fails_ffprobe_verification(self):
        probe = {"format": {"duration": "0"}, "streams": []}
        runner = FakeRunner(completed(0, stdout="out-test/Song.m4a\n"), completed(0, stdout=json.dumps(probe)))

        code, _, stderr = self.run_main([VIDEO_URL, "--output-dir", "out-test"], runner)
        Path("out-test").rmdir()

        self.assertEqual(code, EXIT_VERIFICATION_FAILED)
        self.assertIn("Song.m4a", stderr)


class SkillManifestTests(unittest.TestCase):
    def test_skill_has_portable_trigger_and_fallback(self):
        body = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertTrue(body.startswith("---\nname: bilibili-audio\ndescription: "))
        self.assertIn("HTTP 412", body)
        self.assertIn("must not persist cookies", body)
        self.assertIn("scripts/download_bilibili_audio.py", body)
        self.assertIn("scripts/bili_browser_fetch.py", body)


if __name__ == "__main__":
    unittest.main()
