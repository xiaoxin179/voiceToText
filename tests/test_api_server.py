from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from voice_to_text.api_server import VoiceToTextHandler
from voice_to_text.media_downloader import MediaDownloadResult
from http.server import ThreadingHTTPServer


class ApiServerTests(unittest.TestCase):
    def test_download_endpoint_returns_media_result(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), VoiceToTextHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            payload = json.dumps({"url": "https://example.com/video", "output_dir": "downloads"}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/download/video",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            fake_result = MediaDownloadResult(
                media_path=Path("downloads/media-test/video.mp4"),
                output_dir=Path("downloads/media-test"),
                title="Test video",
            )
            with patch("voice_to_text.api_server.download_video_url", return_value=fake_result):
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = json.loads(response.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertEqual(body["result"]["media_path"], "downloads\\media-test\\video.mp4")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
