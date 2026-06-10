from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .audio_capture import AudioChunk, AudioSource
from .cuda_runtime import add_cuda_dll_directories
from .model_cache import model_cache_summary
from .text_converter import TextConverter, TextMode


@dataclass
class Transcript:
    source: AudioSource
    text: str
    started_at: float
    ended_at: float
    recognized_at: float


@dataclass
class AsrDebugEvent:
    message: str
    created_at: float


class AsrWorker(threading.Thread):
    def __init__(
        self,
        *,
        input_queue: queue.Queue[AudioChunk],
        output_queue: queue.Queue[Transcript],
        debug_queue: queue.Queue[AsrDebugEvent] | None = None,
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "float16",
        language: Optional[str] = "zh",
        vad_filter: bool = True,
        min_rms: float = 0.006,
        text_mode: TextMode = "simplified",
    ) -> None:
        super().__init__(name="gpu-asr-worker", daemon=True)
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.debug_queue = debug_queue
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.vad_filter = vad_filter
        self.min_rms = min_rms
        self.text_mode = text_mode
        self._stop_event = threading.Event()
        self.model_ready = threading.Event()
        self._model = None
        self._text_converter: TextConverter | None = None
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def _debug(self, message: str) -> None:
        if self.debug_queue is not None:
            self.debug_queue.put(AsrDebugEvent(message, time.time()))

    def run(self) -> None:
        try:
            self._debug(
                f"loading model={self.model_size}, device={self.device}, "
                f"compute={self.compute_type}, language={self.language or 'auto'}, text_mode={self.text_mode}"
            )
            self._debug(model_cache_summary(self.model_size))
            self._text_converter = TextConverter(self.text_mode)
            self._model = self._load_model()
            self.model_ready.set()
            self._debug("model loaded")
        except Exception as exc:
            self.last_error = str(exc)
            self._debug(f"model load error: {exc}")
            return

        while not self._stop_event.is_set():
            try:
                chunk = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                text = self._transcribe(chunk)
            except Exception as exc:
                self.last_error = str(exc)
                text = f"[ASR error: {exc}]"
                self._debug(text)

            if text:
                self.output_queue.put(
                    Transcript(
                        source=chunk.source,
                        text=text,
                        started_at=chunk.started_at,
                        ended_at=chunk.ended_at,
                        recognized_at=time.time(),
                    )
                )

    def _load_model(self):
        if self.device == "cuda":
            add_cuda_dll_directories()

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: faster-whisper. Install it with "
                "`pip install faster-whisper` inside the conda environment."
            ) from exc

        return WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            local_files_only=True,
        )

    def _transcribe(self, chunk: AudioChunk) -> str:
        samples = chunk.samples.astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        if rms < self.min_rms:
            self._debug(f"{chunk.source} skipped, rms={rms:.5f} < min_rms={self.min_rms:.5f}")
            return ""

        self._debug(f"{chunk.source} transcribing, rms={rms:.5f}, duration={samples.size / chunk.sample_rate:.1f}s")
        segments, _ = self._model.transcribe(
            samples,
            language=self.language,
            vad_filter=self.vad_filter,
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        text = "".join(segment.text for segment in segments).strip()
        if self._text_converter is not None:
            text = self._text_converter.convert(text)
        self._debug(f"{chunk.source} result: {text or '[empty]'}")
        return text
