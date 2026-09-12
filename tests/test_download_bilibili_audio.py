import sys
import unittest
from pathlib import Path

from scripts.download_bilibili_audio import (
    build_download_command,
    inspect_media_json,
    validate_url,
)


class ValidateUrlTests(unittest.TestCase):
    def test_rejects_non_bilibili_and_non_bv_urls(self):
        invalid_urls = (
            "https://example.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/av170001",
            "https://www.bilibili.com/read/cv1",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_url(url)


class BuildDownloadCommandTests(unittest.TestCase):
    def test_selects_m4a_audio_and_remuxes_to_m4a(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        output_directory = Path("downloads")

        command = build_download_command(url, output_directory)

        self.assertEqual(command[:3], [sys.executable, "-m", "yt_dlp"])
        self.assertEqual(command[command.index("-f") + 1], "ba[ext=m4a]/ba")
        self.assertEqual(command[command.index("--remux-video") + 1], "m4a")
        self.assertEqual(command[command.index("-o") + 1], str(output_directory / "%(title)s.%(ext)s"))
        self.assertEqual(command[-1], url)


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
            "format": {"duration": "12.5"},
            "streams": [{"codec_type": "audio"}, {"codec_type": "video"}],
        }

        media = inspect_media_json(payload)

        self.assertEqual(media.duration, 12.5)
        self.assertEqual(media.audio_stream_count, 1)


if __name__ == "__main__":
    unittest.main()
