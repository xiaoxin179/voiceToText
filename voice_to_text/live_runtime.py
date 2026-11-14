"""Shared lifecycle for desktop and CLI live transcription."""
from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass, field

from .asr import AsrWorker, Transcript
from .audio_capture import AudioCapture, AudioChunk


def normalize_language(language: str | None) -> str | None:
    value = (language or "").strip()
    return None if value in ("", "auto") else value


def compute_default(device: str) -> str:
    return "int8" if device == "cpu" else "float16"


def validate_live_options(chunk_seconds: float, min_rms: float) -> None:
    if not math.isfinite(chunk_seconds) or chunk_seconds <= 0:
        raise ValueError("chunk-seconds must be finite and positive")
    if not math.isfinite(min_rms) or min_rms < 0:
        raise ValueError("min-rms must be finite and nonnegative")


@dataclass
class RuntimeState:
    audio_queue: queue.Queue[AudioChunk]
    transcript_queue: queue.Queue[Transcript]
    debug_queue: queue.Queue
    asr_worker: AsrWorker
    capture_threads: list[AudioCapture]
    capture_started: bool = False
    last_loading_notice_at: float = 0.0
    stopping: bool = False
    stopped: threading.Event = field(default_factory=threading.Event)
    shutdown_error: str | None = None
    started_captures: list[AudioCapture] = field(default_factory=list)
    model_started_at: float = field(default_factory=time.monotonic)
    capture_started_at: float = 0.0

    def start_capture_if_ready(self, timeout: float = 180) -> bool:
        if self.stopping:
            return self.capture_started
        if self.asr_worker.last_error or not self.asr_worker.is_alive():
            raise RuntimeError(self.asr_worker.last_error or "ASR worker stopped")
        if self.capture_started:
            return True
        if not self.asr_worker.model_ready.is_set():
            if time.monotonic() - self.model_started_at >= timeout:
                raise RuntimeError("ASR model loading timed out")
            return False
        self.capture_started_at = time.monotonic()
        for capture in self.capture_threads:
            capture.start()
            self.started_captures.append(capture)
        self.capture_started = True
        return True

    def check_capture(self) -> None:
        for capture in self.started_captures:
            if capture.last_error or not capture.is_alive():
                raise RuntimeError(capture.last_error or f"{capture.source} capture stopped")
            if not capture.ready.is_set() and time.monotonic() - self.capture_started_at > 15:
                raise RuntimeError(f"{capture.source} device opening timed out")

    def stop(self, flush_timeout: float = 300) -> None:
        """Stop producers, preserve their tail, then drain ASR before stopping it."""
        try:
            for capture in self.started_captures:
                capture.stop()
            deadline = time.monotonic() + 5
            for capture in self.started_captures:
                capture.join(timeout=max(0, deadline - time.monotonic()))
            if any(c.is_alive() for c in self.started_captures):
                self.shutdown_error = "Audio capture did not stop in time; transcript may be incomplete"
            capture_errors = [c.last_error for c in self.started_captures if c.last_error]
            if capture_errors:
                self.shutdown_error = "; ".join(capture_errors)
            deadline = time.monotonic() + flush_timeout
            while self.audio_queue.unfinished_tasks and self.asr_worker.is_alive():
                if time.monotonic() >= deadline:
                    self.shutdown_error = "ASR flush timed out; transcript may be incomplete"
                    break
                time.sleep(.02)
            if self.asr_worker.last_error:
                self.shutdown_error = self.asr_worker.last_error
            if self.audio_queue.unfinished_tasks:
                self.shutdown_error = self.shutdown_error or "Unprocessed audio remains; transcript is incomplete"
            if any(c.dropped_chunks for c in self.started_captures):
                self.shutdown_error = self.shutdown_error or "Audio chunks were dropped; transcript is incomplete"
        except Exception as exc:
            self.shutdown_error = str(exc)
        finally:
            self.asr_worker.stop()
            self.asr_worker.join(timeout=3)
            if self.asr_worker.is_alive():
                self.shutdown_error = self.shutdown_error or "ASR worker is still shutting down"
            self.stopped.set()

    def request_stop(self, flush_timeout: float = 300) -> None:
        if self.stopping:
            return
        self.stopping = True
        # Keep the process alive long enough to finish saving final transcripts.
        threading.Thread(target=self.stop, args=(flush_timeout,), daemon=False).start()
