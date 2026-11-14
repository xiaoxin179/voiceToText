from __future__ import annotations

import argparse
import contextlib
import io
import json
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import numpy as np

from voice_to_text import api_server, service_core
from voice_to_text.asr import AsrWorker
from voice_to_text.audio_capture import AudioCapture, AudioChunk
from voice_to_text.audio_devices import AudioDevice
from voice_to_text.cli import build_parser, cmd_session


DEVICE = AudioDevice(7, "test-loopback", "WASAPI", 2, 0, 16000, True)


class FakeCapture(threading.Thread):
    def __init__(self, *, output_queue, **kwargs):
        super().__init__(daemon=True)
        self.output_queue = output_queue
        self.ready = threading.Event()
        self.last_error = None
        self.dropped_chunks = 0
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    def run(self):
        time.sleep(0.03)
        self.ready.set()
        self.stopped.wait()
        # The final chunk arrives after stop(), just like a partially-filled buffer.
        for _ in range(3):
            now = time.time()
            self.output_queue.put(AudioChunk("system", now, now + .01, 16000,
                                            np.ones(160, dtype=np.float32) * .1))


class SpeakerControlTests(unittest.TestCase):
    def test_slow_last_chunk_is_drained_and_every_chunk_is_preserved(self):
        phases = []
        def recognize(worker, chunk):
            time.sleep(.4)
            return "test speech"
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(service_core, "AudioCapture", FakeCapture), \
             patch.object(service_core, "get_default_system_loopback", return_value=DEVICE), \
             patch.object(AsrWorker, "_load_model", return_value=object()), \
             patch.object(AsrWorker, "_transcribe", recognize):
            result = service_core.transcribe_speaker(
                service_core.SpeakerServiceRequest(seconds=.05, output_dir=Path(temp), flush_timeout_seconds=5),
                state_callback=phases.append)
            self.assertTrue(result.integrity["complete"])
            self.assertEqual(result.integrity["captured_chunks"], 3)
            self.assertEqual(result.integrity["processed_chunks"], 3)
            self.assertEqual(len(result.segments), 3)
            self.assertEqual(phases, ["loading_model", "opening_device", "capturing", "draining"])

    def test_capture_failure_never_reports_ready(self):
        class BrokenCapture(FakeCapture):
            def run(self):
                self.last_error = "test device unavailable"
        phases = []
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(service_core, "AudioCapture", BrokenCapture), \
             patch.object(service_core, "get_default_system_loopback", return_value=DEVICE), \
             patch.object(AsrWorker, "_load_model", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "device unavailable"):
                service_core.transcribe_speaker(service_core.SpeakerServiceRequest(output_dir=Path(temp)),
                                                state_callback=phases.append)
        self.assertNotIn("capturing", phases)

    def test_incomplete_flush_is_explicit_and_audio_is_saved(self):
        def recognize(worker, chunk):
            time.sleep(.2)
            return "late"
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(service_core, "AudioCapture", FakeCapture), \
             patch.object(service_core, "get_default_system_loopback", return_value=DEVICE), \
             patch.object(AsrWorker, "_load_model", return_value=object()), \
             patch.object(AsrWorker, "_transcribe", recognize):
            result = service_core.transcribe_speaker(service_core.SpeakerServiceRequest(
                seconds=.03, output_dir=Path(temp), flush_timeout_seconds=.01))
            self.assertFalse(result.integrity["complete"])
            self.assertTrue(result.audio_path.exists())

    def test_full_capture_queue_counts_drops(self):
        q = queue.Queue(maxsize=1)
        capture = AudioCapture(source="system", device=DEVICE, output_queue=q)
        chunk = AudioChunk("system", 0, 1, 16000, np.zeros(16000, dtype=np.float32))
        capture._put_latest_chunk(chunk)
        capture._put_latest_chunk(chunk)
        self.assertEqual(capture.dropped_chunks, 1)

    def test_cli_has_session_controls_and_device_parameters(self):
        parser = build_parser()
        args = parser.parse_args(["transcribe-speaker", "--audio-device-index", "7", "--chunk-seconds", "4"])
        self.assertEqual(args.audio_device_index, 7)
        self.assertEqual(args.chunk_seconds, 4)
        self.assertEqual(parser.parse_args(["session", "wait-ready", "speaker_test"]).action, "wait-ready")

    def test_http_and_cli_start_ready_stop_complete_and_overlap_rejection(self):
        store = api_server.SessionStore()
        def fake_transcribe(request, progress, stop_event, state_callback):
            state_callback("capturing")
            stop_event.wait(5)
            return service_core.ServiceTranscriptionResult(
                kind="speaker", raw_text="test", timestamped_text="test", output_dir=Path("test"),
                raw_transcript_path=Path("test/raw.md"), timestamped_transcript_path=Path("test/time.md"),
                integrity={"complete": True})
        with patch.object(api_server, "STORE", store), \
             patch.object(api_server, "transcribe_speaker", side_effect=fake_transcribe):
            server = ThreadingHTTPServer(("127.0.0.1", 0), api_server.VoiceToTextHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}"
            def command(action, session_id=None):
                output = io.StringIO()
                args = argparse.Namespace(action=action, session_id=session_id, endpoint=endpoint,
                                          config=None, timeout=3, poll_seconds=.01)
                with contextlib.redirect_stdout(output):
                    code = cmd_session(args)
                # HTTP request logging also uses stdout in the same process.
                text = output.getvalue()
                return code, json.loads(text[text.index('{'):])
            try:
                _, start = command("start")
                sid = start["session_id"]
                _, ready = command("wait-ready", sid)
                self.assertTrue(ready["ready"])
                self.assertEqual(ready["config"]["chunk_seconds"], 10)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    command("start")
                self.assertEqual(error.exception.code, 400)
                command("stop", sid)
                code, result = command("wait", sid)
                self.assertEqual(code, 0)
                self.assertEqual(result["status"], "completed")
                self.assertFalse(result["ready"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
