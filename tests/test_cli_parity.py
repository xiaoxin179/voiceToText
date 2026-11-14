"""Business parity regressions; no hardware, model download or paid API calls."""
from __future__ import annotations

import contextlib
import io
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from voice_to_text import cli, live_runtime, video_transcription as video
from voice_to_text.asr import AsrWorker
from voice_to_text.audio_capture import AudioCapture, AudioChunk
from voice_to_text.audio_devices import AudioDevice, select_input_device
from voice_to_text.operation_control import OperationCancelled
from voice_to_text.service_core import VideoServiceRequest, transcribe_video_url
from voice_to_text.task_store import TaskStore, read_task_artifact

MIC = AudioDevice(3, "microphone", "WASAPI", 1, 0, 16000, False)
SYSTEM = AudioDevice(7, "loopback", "WASAPI", 2, 0, 16000, True)


class TailCapture(threading.Thread):
    def __init__(self, *, source, device, output_queue, **kwargs):
        super().__init__(daemon=True)
        self.source, self.device, self.output_queue = source, device, output_queue
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.last_error = None
        self.dropped_chunks = 0

    def stop(self):
        self.stop_event.set()

    def run(self):
        self.ready.set()
        self.stop_event.wait()
        now = time.time()
        self.output_queue.put(AudioChunk(self.source, now, now + .01, 16000,
                                        np.ones(160, dtype=np.float32)))


class CliParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "tasks.db"
        self.store = TaskStore(self.db)

    def run_cli(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(args)
        return code, output.getvalue(), errors.getvalue()

    def task(self, *args):
        code, output, errors = self.run_cli(["task", *args, "--db", str(self.db)])
        return code, json.loads(output) if output else None, errors

    def test_batch_add_keeps_valid_tasks_and_reports_duplicate(self):
        self.store.add({"url": "https://example.com/a"})
        links = self.root / "links.txt"
        links.write_text("分享 https://example.com/a\nhttps://example.com/b\nhttps://example.com/b", encoding="utf-8-sig")
        code, result, _ = self.task("batch-add", "--urls-file", str(links), "--no-keep-audio",
                                    "--no-optimize", "--no-summarize", "--instructions", "保留术语",
                                    "--chunk-seconds", "4", "--output-dir", str(self.root / "results"))
        self.assertEqual(code, 1)
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        payload = result["added"][0]["payload"]
        self.assertEqual(payload["instructions"], "保留术语")
        self.assertEqual(payload["config"]["chunk_seconds"], 4)
        self.assertFalse(payload["optimize"])
        self.assertFalse(payload["summarize"])
        self.assertFalse(payload["keep_audio"])

    def test_batch_stdin_and_allow_duplicate(self):
        with patch("sys.stdin", io.StringIO("https://example.com/1\nhttps://example.com/2")):
            code, result, _ = self.task("batch-add", "--urls-file", "-")
        self.assertEqual(code, 0)
        self.assertEqual(len(result["added"]), 2)
        code, _, _ = self.task("add", "--url", "https://example.com/1", "--allow-duplicate")
        self.assertEqual(code, 0)

    def test_edit_flags_preserve_other_settings_and_old_list_contract(self):
        _, task, _ = self.task("add", "--url", "https://example.com/a", "--keep-audio")
        code, edited, _ = self.task("edit", task["id"], "--title", "访谈", "--chunk-seconds", "5")
        self.assertEqual(code, 0)
        self.assertTrue(edited["payload"]["keep_audio"])
        self.assertEqual(edited["payload"]["config"]["model"], "medium")
        self.assertEqual(edited["payload"]["config"]["chunk_seconds"], 5)
        self.assertEqual(self.task("list")[1][0]["id"], task["id"])
        result = self.task("list", "--query", "访谈", "--status", "queued", "--page", "1", "--page-size", "20")[1]
        self.assertEqual((result["total"], result["page_size"]), (1, 20))
        self.assertEqual(self.task("list", "--query", "不存在")[1]["total"], 0)

    def test_data_and_direct_flags_merge_without_losing_json_config(self):
        data = self.root / "task.json"
        data.write_text(json.dumps({"config": {"model": "small"}, "instructions": "JSON要求"}), encoding="utf-8-sig")
        code, task, _ = self.task("add", "--url", "https://example.com/a", "--data", str(data), "--chunk-seconds", "3")
        self.assertEqual(code, 0)
        self.assertEqual(task["payload"]["config"]["model"], "small")
        self.assertEqual(task["payload"]["config"]["chunk_seconds"], 3)
        self.assertEqual(task["payload"]["instructions"], "JSON要求")

    def create_artifacts(self):
        task = self.store.add({"url": "https://example.com/artifact", "output_dir": str(self.root),
                               "optimize": False, "summarize": False})
        claim = self.store.claim()
        output = Path(task["payload"]["output_dir"])
        output.mkdir()
        raw, timed = output / "raw.md", output / "time.md"
        raw.write_text("原始文字稿", encoding="utf-8-sig")
        timed.write_text("[00:01] 原始文字稿", encoding="utf-8")
        self.store.update(task["id"], claim["lease_token"], {"status": "completed", "result": {
            "raw_transcript_path": str(raw), "timestamped_transcript_path": str(timed),
            "integrity": {"complete": True}}})
        return task["id"], output

    def test_read_results_logs_open_and_retry(self):
        task_id, output = self.create_artifacts()
        code, text, _ = self.run_cli(["task", "result", task_id, "--kind", "raw", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(text.strip(), "原始文字稿")
        artifacts = self.task("result", task_id, "--json")[1]
        self.assertIsNone(artifacts["summary_path"])
        self.assertEqual(artifacts["raw_transcript_path"]["text"], "原始文字稿")
        self.assertGreater(len(self.task("logs", task_id, "--json")[1]["events"]), 1)
        with patch("voice_to_text.task_cli.os.startfile") as opened:
            self.assertEqual(self.task("open", task_id)[0], 0)
        opened.assert_called_once_with(str(output))
        retried = self.task("retry", task_id)[1]
        self.assertEqual(retried["status"], "queued")
        self.assertNotEqual(retried["payload"]["output_dir"], str(output))
        self.assertEqual(self.task("stop", task_id)[1]["status"], "cancelled")

    def test_result_reader_rejects_paths_outside_task_output(self):
        task = self.store.add({"url": "https://example.com/a", "output_dir": str(self.root / "output")})
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        task["result"]["raw_transcript_path"] = str(outside)
        with self.assertRaises(ValueError):
            read_task_artifact(task, "raw_transcript_path")

    def test_missing_result_and_invalid_payload_fail_cleanly(self):
        task = self.store.add({"url": "https://example.com/a"})
        self.assertEqual(self.task("result", task["id"])[0], 1)
        self.assertEqual(self.task("batch-add", "--url", "not a link")[0], 1)
        data = self.root / "list.json"
        data.write_text("[]", encoding="utf-8")
        self.assertEqual(self.task("add", "--data", str(data))[0], 1)

    def test_optimize_existing_file_and_usage_without_asr_or_download(self):
        source = self.root / "raw.md"
        source.write_text("已有稿", encoding="utf-8-sig")
        optimization = video.DeepSeekOptimizationResult("校对完成", video.DeepSeekTokenUsage(10, 5, 15))
        with patch.object(video, "optimize_transcript_with_deepseek", return_value=optimization) as optimize, \
                patch.object(video, "_download_audio") as download:
            code, text, _ = self.run_cli(["optimize-file", str(source), "--json"])
        self.assertEqual(code, 0)
        result = json.loads(text)
        self.assertEqual(result["optimized_token_usage"]["total_tokens"], 15)
        self.assertEqual(Path(result["optimized_transcript_path"]).read_text(encoding="utf-8"), "校对完成")
        self.assertEqual(source.read_text(encoding="utf-8-sig"), "已有稿")
        self.assertEqual(optimize.call_args.args[0], "已有稿")
        download.assert_not_called()

    def test_failed_optimization_preserves_previous_output_and_raw(self):
        source, output = self.root / "raw.md", self.root / "optimized.md"
        source.write_text("原始稿", encoding="utf-8")
        output.write_text("上次成功稿", encoding="utf-8")
        with patch.object(video, "optimize_transcript_with_deepseek", side_effect=RuntimeError("API failed")):
            code, _, _ = self.run_cli(["optimize-file", str(source), "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "上次成功稿")
        self.assertEqual(self.run_cli(["optimize-file", str(source), "--output", str(source)])[0], 1)

    def test_empty_optimization_fails_before_request(self):
        with patch.object(video, "_call_deepseek") as request:
            with self.assertRaises(ValueError):
                video.optimize_transcript_with_deepseek("  ", api_key="unused")
        request.assert_not_called()

    def test_cancelled_video_does_not_download(self):
        stop = threading.Event()
        stop.set()
        with patch.object(video, "_download_audio") as download:
            with self.assertRaises(OperationCancelled):
                video.transcribe_platform_video(video.VideoTranscriptionOptions("https://example.com/a"), stop_event=stop)
        download.assert_not_called()

    def test_cancellation_after_download_skips_asr(self):
        stop = threading.Event()
        def download(_url, folder, **kwargs):
            path = folder / "audio.mp3"
            path.write_bytes(b"fake audio")
            stop.set()
            return path
        with patch.object(video, "_download_audio", side_effect=download), patch.object(video, "_transcribe_audio") as asr:
            with self.assertRaises(OperationCancelled):
                video.transcribe_platform_video(video.VideoTranscriptionOptions("https://example.com/a", output_dir=self.root), stop_event=stop)
        asr.assert_not_called()

    def test_video_usage_survives_service_and_cli_json(self):
        fake = video.VideoTranscriptionResult("raw", "timed", "optimized", self.root / "audio.mp3",
                 self.root / "raw.md", self.root / "time.md", self.root / "optimized.md", video.DeepSeekTokenUsage(7, 3, 10))
        with patch("voice_to_text.service_core.transcribe_platform_video", return_value=fake):
            result = transcribe_video_url(VideoServiceRequest("https://example.com/a"))
        self.assertEqual(result.to_dict()["optimized_token_usage"]["total_tokens"], 10)
        with patch.object(cli, "transcribe_video_url", return_value=result) as transcribe:
            code, text, _ = self.run_cli(["transcribe-video", "https://example.com/a", "--device", "cpu", "--language", "auto", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["optimized_token_usage"]["total_tokens"], 10)
        self.assertIsNone(transcribe.call_args.args[0].language)
        self.assertEqual(transcribe.call_args.args[0].compute_type, "int8")

    def test_both_sources_use_independent_selected_devices(self):
        args = cli.build_parser().parse_args(["listen", "--mic-device-index", "3", "--system-device-index", "7"])
        with patch.object(cli, "select_input_device", side_effect=[MIC, SYSTEM]) as select:
            captures = cli._build_capture_threads(args, queue.Queue(), queue.Queue())
        self.assertEqual([c.device for c in captures], [MIC, SYSTEM])
        self.assertEqual([c.source for c in captures], ["mic", "system"])
        self.assertEqual([call.args for call in select.call_args_list], [("mic", 3), ("system", 7)])
        self.assertTrue(all(not c.is_alive() for c in captures))

    def test_wrong_device_type_is_rejected(self):
        with patch("voice_to_text.audio_devices.list_devices", return_value=[MIC, SYSTEM]):
            self.assertEqual(select_input_device("mic", 3), MIC)
            with self.assertRaises(ValueError):
                select_input_device("mic", 7)

    def test_invalid_listen_options_fail_before_capture(self):
        with patch.object(cli, "_build_capture_threads") as captures:
            for arguments in (["--chunk-seconds", "0"], ["--min-rms", "nan"], ["--flush-timeout-seconds", "-1"]):
                self.assertEqual(self.run_cli(["listen", *arguments])[0], 1)
        captures.assert_not_called()

    def test_queue_drop_accounting_does_not_block_drain(self):
        audio = queue.Queue(maxsize=1)
        capture = AudioCapture(source="mic", device=MIC, output_queue=audio)
        chunk = AudioChunk("mic", 0, 1, 16000, np.ones(160, dtype=np.float32))
        capture._put_latest_chunk(chunk)
        capture._put_latest_chunk(chunk)
        self.assertEqual(audio.unfinished_tasks, 1)
        audio.get_nowait()
        audio.task_done()
        self.assertEqual(audio.unfinished_tasks, 0)

    def run_live_cli(self, view="all"):
        captures, languages = [], []
        def capture(**kwargs):
            item = TailCapture(**kwargs)
            captures.append(item)
            return item
        def load(worker):
            languages.append(worker.language)
            return object()
        def pause(seconds):
            if captures and all(c.ready.is_set() for c in captures):
                raise KeyboardInterrupt
            time.sleep(seconds)
        clock = SimpleNamespace(monotonic=time.monotonic, strftime=time.strftime,
                                localtime=time.localtime, sleep=pause)
        with patch.object(cli, "AudioCapture", side_effect=capture), \
                patch.object(cli, "select_input_device", side_effect=[MIC, SYSTEM]), \
                patch.object(AsrWorker, "_load_model", load), \
                patch.object(AsrWorker, "_transcribe", side_effect=lambda chunk: chunk.source + " final"), \
                patch.object(cli, "time", clock):
            code, output, errors = self.run_cli(["listen", "--language", "auto", "--json", "--view", view,
                                                "--output-dir", str(self.root / "live")])
        return code, [json.loads(line) for line in output.splitlines()], captures, languages

    def test_live_cli_json_stop_drains_and_saves_both_sources(self):
        code, events, captures, languages = self.run_live_cli()
        self.assertEqual(code, 0)
        self.assertEqual(languages, [None])
        transcripts = [e for e in events if e["type"] == "transcript"]
        self.assertEqual({e["source"] for e in transcripts}, {"mic", "system"})
        self.assertTrue(events[-1]["complete"])
        self.assertEqual(events[-1]["phase"], "stopped")
        for channel in ("mic", "system"):
            text = Path(events[-1]["transcript_paths"][channel]).read_text(encoding="utf-8")
            self.assertIn(channel + " final", text)
        self.assertFalse(any(c.is_alive() for c in captures))

    def test_live_view_filter_keeps_both_output_files(self):
        code, events, _, _ = self.run_live_cli("mic")
        self.assertEqual(code, 0)
        self.assertEqual([e["source"] for e in events if e["type"] == "transcript"], ["mic"])
        self.assertIn("system final", (self.root / "live" / "transcript_system.md").read_text(encoding="utf-8"))

    def test_model_failure_starts_no_recording_and_reports_incomplete(self):
        with patch.object(cli, "select_input_device", side_effect=[MIC, SYSTEM]), \
                patch.object(AsrWorker, "_load_model", side_effect=RuntimeError("model missing")), \
                patch.object(cli, "AudioCapture", wraps=AudioCapture) as capture:
            code, output, errors = self.run_cli(["listen", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("model missing", errors)
        self.assertFalse(json.loads(output.splitlines()[-1])["complete"])
        # The device objects are constructed, but model failure prevents start().
        self.assertEqual(capture.call_count, 2)


class LiveLifecycleTests(unittest.TestCase):
    def runtime(self):
        audio, transcripts, debug = queue.Queue(), queue.Queue(), queue.Queue()
        asr = AsrWorker(input_queue=audio, output_queue=transcripts, debug_queue=debug)
        captures = [TailCapture(source="mic", device=MIC, output_queue=audio),
                    TailCapture(source="system", device=SYSTEM, output_queue=audio)]
        return live_runtime.RuntimeState(audio, transcripts, debug, asr, captures)

    def test_shared_runtime_waits_for_model_and_preserves_both_tail_chunks(self):
        loaded = threading.Event()
        def load(_worker):
            loaded.wait(2)
            return object()
        def recognize(_worker, chunk):
            time.sleep(.05)
            return chunk.source + " final"
        runtime = self.runtime()
        with patch.object(AsrWorker, "_load_model", load), patch.object(AsrWorker, "_transcribe", recognize):
            runtime.asr_worker.start()
            self.assertFalse(runtime.start_capture_if_ready())
            self.assertFalse(any(c.is_alive() for c in runtime.capture_threads))
            loaded.set()
            self.assertTrue(runtime.asr_worker.model_ready.wait(1))
            runtime.start_capture_if_ready()
            runtime.request_stop(2)
            self.assertTrue(runtime.stopped.wait(3))
        self.assertIsNone(runtime.shutdown_error)
        self.assertEqual(runtime.transcript_queue.qsize(), 2)
        self.assertEqual(runtime.audio_queue.unfinished_tasks, 0)
        self.assertFalse(runtime.asr_worker.is_alive())
        self.assertFalse(any(c.is_alive() for c in runtime.capture_threads))

    def test_stop_before_model_ready_never_starts_capture(self):
        runtime = self.runtime()
        with patch.object(AsrWorker, "_load_model", return_value=object()):
            runtime.asr_worker.start()
            runtime.request_stop(1)
            self.assertTrue(runtime.stopped.wait(2))
            runtime.start_capture_if_ready()
        self.assertFalse(runtime.capture_started)


class DesktopParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 is not installed")
        cls.app = QApplication.instance() or QApplication([])
        from voice_to_text import qt_app
        cls.qt = qt_app

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.dict(os.environ, {"VTT_TASK_DB": str(self.root / "tasks.db")}), \
                patch.object(self.qt.VoiceToTextWindow, "refresh_devices"):
            self.window = self.qt.VoiceToTextWindow()
        self.window.timer.stop()
        self.window.mic_combo.addItem("mic", MIC)
        self.window.system_combo.addItem("system", SYSTEM)

    def tearDown(self):
        runtime = self.window.runtime
        if runtime:
            runtime.request_stop(2)
            runtime.stopped.wait(3)
            self.window.poll_runtime()
        from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_desktop_uses_shared_start_stop_and_receives_final_chunks(self):
        with patch.object(self.qt, "AudioCapture", TailCapture), \
                patch.object(AsrWorker, "_load_model", return_value=object()), \
                patch.object(AsrWorker, "_transcribe", side_effect=lambda chunk: chunk.source + " final"):
            self.window.language_combo.setCurrentText("auto")
            self.window.start_listening()
            runtime = self.window.runtime
            self.assertIsInstance(runtime, live_runtime.RuntimeState)
            self.assertIsNone(runtime.asr_worker.language)
            runtime.asr_worker.model_ready.wait(1)
            self.window.poll_runtime()
            self.window.stop_listening()
            self.assertFalse(self.window.start_button.isEnabled())
            self.assertTrue(runtime.stopped.wait(3))
            self.window.poll_runtime()
        self.assertIsNone(self.window.runtime)
        self.assertIn("mic final", self.window.mic_text.toPlainText())
        self.assertIn("system final", self.window.system_text.toPlainText())
        self.assertTrue(self.window.start_button.isEnabled())

    def test_desktop_optimization_and_cli_use_same_file_writer(self):
        raw = self.root / "raw.md"
        raw.write_text("原始稿", encoding="utf-8")
        self.window.video_raw_text.setPlainText("原始稿")
        self.window.video_result = video.VideoTranscriptionResult("原始稿", "time", None, self.root / "audio.mp3",
                                                                 raw, self.root / "timed.md", None)
        optimization = video.DeepSeekOptimizationResult("校对稿", video.DeepSeekTokenUsage(4, 2, 6))
        with patch.object(video, "optimize_transcript_with_deepseek", return_value=optimization):
            self.window.start_video_agent_optimization()
            self.window.video_agent_runtime.thread.join(2)
            self.window.poll_video_agent_runtime()
        self.assertEqual(self.window.video_optimized_text.toPlainText(), "校对稿")
        self.assertEqual((self.root / "transcript_deepseek.md").read_text(encoding="utf-8"), "校对稿")
        self.assertEqual(self.window.video_result.optimized_token_usage.total_tokens, 6)

    def test_desktop_video_stop_signals_same_cancellation_event(self):
        started, cancelled = threading.Event(), threading.Event()
        def transcribe(options, progress, stop_event):
            started.set()
            stop_event.wait(2)
            if stop_event.is_set():
                cancelled.set()
            raise OperationCancelled("cancelled")
        self.window.video_url_input.setText("https://example.com/a")
        with patch.object(self.qt, "transcribe_platform_video", side_effect=transcribe):
            self.window.start_video_transcription()
            self.assertTrue(started.wait(1))
            runtime = self.window.video_runtime
            self.window.stop_video_transcription()
            runtime.thread.join(2)
            self.window.poll_video_runtime()
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.window.video_runtime)


if __name__ == "__main__":
    unittest.main()
