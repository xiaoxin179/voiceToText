from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from voice_to_text.media_downloader import MediaDownloadOptions, _find_downloaded_media, _normalize_media_url, download_media
from voice_to_text.service_core import MediaDownloadServiceRequest


class MediaDownloaderTests(unittest.TestCase):
    def test_normalizes_douyin_modal_link(self) -> None:
        self.assertEqual(
            _normalize_media_url("https://www.douyin.com/jingxuan?modal_id=7660026038541405450"),
            "https://www.douyin.com/video/7660026038541405450",
        )

    def test_request_parses_headers_and_cookie_file(self) -> None:
        request = MediaDownloadServiceRequest.from_mapping(
            {
                "url": "https://example.com/video",
                "headers": ["Referer: https://example.com/", "Authorization: Bearer token"],
                "cookies_file": "C:/tmp/cookies.txt",
            }
        )
        self.assertEqual(request.headers[0], "Referer: https://example.com/")
        self.assertEqual(request.cookies_file, Path("C:/tmp/cookies.txt"))

    def test_rejects_two_cookie_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            cookies = Path(temporary_dir) / "cookies.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be used together"):
                download_media(
                    MediaDownloadOptions(
                        url="https://example.com/video",
                        cookie_browser="firefox",
                        cookies_file=cookies,
                    )
                )

    def test_uses_ytdlp_reported_download_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            output_dir = root / "downloads"
            downloaded = root / "video.mp4"
            downloaded.write_bytes(b"video")

            class Completed:
                returncode = 0
                stdout = f"{downloaded}\n"
                stderr = ""

            with patch("voice_to_text.media_downloader._yt_dlp_command_prefix", return_value=["yt-dlp"]), patch(
                "voice_to_text.media_downloader.subprocess.run", return_value=Completed()
            ):
                result = download_media(MediaDownloadOptions(url="https://example.com/video", output_dir=output_dir))

            self.assertEqual(result.media_path, downloaded)
            self.assertTrue(result.output_dir.is_dir())

    def test_queues_media_in_omniget_bridge(self) -> None:
        captured = {}

        class Response:
            def read(self) -> bytes:
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        def fake_urlopen(request, timeout: int):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        with patch("voice_to_text.media_downloader.urllib.request.urlopen", side_effect=fake_urlopen):
            result = download_media(
                MediaDownloadOptions(
                    url="https://www.douyin.com/jingxuan?modal_id=7660026038541405450",
                    backend="omniget",
                    omniget_endpoint="http://127.0.0.1:47720/",
                    omniget_token="test-token",
                    referer="https://www.douyin.com/",
                    headers=("X-Test: value",),
                )
            )

        self.assertEqual(result.status, "queued")
        self.assertEqual(result.backend, "omniget")
        self.assertIsNone(result.media_path)
        self.assertEqual(captured["url"], "http://127.0.0.1:47720/v1/enqueue")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(captured["body"]["url"], "https://www.douyin.com/video/7660026038541405450")
        self.assertEqual(captured["body"]["headers"], {"X-Test": "value"})

    def test_auto_backend_falls_back_to_ytdlp_when_omniget_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            downloaded = root / "video.mp4"
            downloaded.write_bytes(b"video")

            class Completed:
                returncode = 0
                stdout = f"{downloaded}\n"
                stderr = ""

            with patch("voice_to_text.media_downloader._yt_dlp_command_prefix", return_value=["yt-dlp"]), patch(
                "voice_to_text.media_downloader.subprocess.run", return_value=Completed()
            ):
                result = download_media(MediaDownloadOptions(url="https://example.com/video", output_dir=root))

        self.assertEqual(result.backend, "yt-dlp")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.media_path, downloaded)

    def test_finds_latest_media_when_ytdlp_does_not_print_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            old = output_dir / "old.webm"
            new = output_dir / "new.mp4"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            self.assertEqual(_find_downloaded_media("", output_dir), new)


if __name__ == "__main__":
    unittest.main()
