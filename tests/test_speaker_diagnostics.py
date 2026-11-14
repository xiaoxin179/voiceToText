import argparse
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from voice_to_text import api_server, service_core
from voice_to_text.asr import AsrWorker
from voice_to_text.audio_capture import AudioChunk
from voice_to_text.audio_devices import AudioDevice
from voice_to_text.cli import build_parser, cmd_session, cmd_transcribe_speaker


DEVICE = AudioDevice(20, 'test loopback', 'Windows WASAPI', 2, 0, 16000, True)


def fake_capture(amplitudes):
    class Capture(threading.Thread):
        def __init__(self, *, output_queue, **kwargs):
            super().__init__(daemon=True)
            self.output_queue = output_queue
            self.ready = threading.Event()
            self.stopped = threading.Event()
            self.last_error = None
            self.dropped_chunks = 0

        def stop(self):
            self.stopped.set()

        def run(self):
            self.ready.set()
            for amplitude in amplitudes:
                now = time.time()
                self.output_queue.put(AudioChunk('system', now, now + 5, 16000,
                    np.full(80000, amplitude, dtype=np.float32)))
            self.stopped.wait()
    return Capture


class SpeakerDiagnosticsTests(unittest.TestCase):
    def run_capture(self, amplitudes, text='', min_rms=.001):
        model = SimpleNamespace(transcribe=lambda *args, **kwargs:
            ([SimpleNamespace(text=text)] if text else [], None))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(service_core, 'AudioCapture', fake_capture(amplitudes)), \
             patch.object(service_core, 'get_default_system_loopback', return_value=DEVICE), \
             patch.object(AsrWorker, '_load_model', return_value=model):
            result = service_core.transcribe_speaker(service_core.SpeakerServiceRequest(
                seconds=.05, min_rms=min_rms, output_dir=Path(directory)))
            self.assertTrue(result.audio_path.is_file())
            self.assertTrue(result.raw_transcript_path.is_file())
            return result

    def test_silent_chunks_are_complete_but_not_successful(self):
        result = self.run_capture([.00001, .00001])
        self.assertTrue(result.integrity['complete'])
        diagnostics = result.to_dict()['audio_diagnostics']
        self.assertEqual(diagnostics['status'], 'no_audio')
        self.assertFalse(diagnostics['audio_detected'])
        self.assertEqual(diagnostics['selected_device']['index'], 20)
        self.assertAlmostEqual(diagnostics['max_rms'], .00001, places=8)
        self.assertTrue(any('NO_AUDIO: 前10秒' in line for line in result.logs))

    def test_zero_threshold_does_not_make_digital_silence_valid(self):
        result = self.run_capture([0, 0], min_rms=0)
        self.assertEqual(result.audio_diagnostics['status'], 'no_audio')
        self.assertEqual(result.raw_text, '')

    def test_audible_input_without_recognized_speech_is_distinct(self):
        result = self.run_capture([.1])
        self.assertEqual(result.audio_diagnostics['status'], 'no_speech')
        self.assertTrue(result.audio_diagnostics['audio_detected'])
        self.assertFalse(result.audio_diagnostics['speech_detected'])

    def test_late_speech_recovers_after_initial_silence_warning(self):
        result = self.run_capture([0, 0, .1], text='有效语音')
        self.assertEqual(result.audio_diagnostics['status'], 'ok')
        self.assertEqual(result.raw_text, '有效语音')
        self.assertTrue(any('NO_AUDIO: 前10秒' in line for line in result.logs))
        self.assertTrue(any('AUDIO_DETECTED:' in line for line in result.logs))

    def test_cli_and_session_reject_empty_capture_but_keep_diagnostics(self):
        result = self.run_capture([0, 0])
        args = build_parser().parse_args(['transcribe-speaker', '--seconds', '10', '--json'])
        output = io.StringIO()
        with patch('voice_to_text.cli.transcribe_speaker', return_value=result), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cmd_transcribe_speaker(args), 3)
        self.assertEqual(json.loads(output.getvalue())['audio_diagnostics']['status'], 'no_audio')

        session = api_server.ServiceSession(id='speaker_silent', kind='speaker')
        api_server._start_session(session, lambda emit: result)
        session.thread.join(3)
        self.assertFalse(session.thread.is_alive())
        self.assertEqual(session.status, 'failed')
        self.assertTrue(session.error)
        self.assertEqual(session.result['audio_diagnostics']['status'], 'no_audio')
        args = argparse.Namespace(action='wait', session_id=session.id,
            endpoint='http://127.0.0.1:8765', config=None, timeout=3, poll_seconds=.01)
        response = io.BytesIO(json.dumps(session.to_dict(include_result=True)).encode())
        with patch('urllib.request.urlopen', return_value=response), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_session(args), 3)


if __name__ == '__main__':
    unittest.main()
