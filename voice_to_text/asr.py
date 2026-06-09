from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .audio_capture import AudioChunk, AudioSource
from .cuda_runtime import add_cuda_dll_directories


@dataclass
class Transcript:
    source: AudioSource
    text: str
    started_at: float
    ended_at: float
    recognized_at: float


class AsrWorker(threading.Thread):
    def __init__(
        self,
        *,
        input_queue: queue.Queue[AudioChunk],
        output_queue: queue.Queue[Transcript],
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "float16",
        language: Optional[str] = "zh",
        vad_filter: bool = True,
        min_rms: float = 0.006,
    ) -> None:
        super().__init__(name="gpu-asr-worker", daemon=True)
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.vad_filter = vad_filter
        self.min_rms = min_rms
        self._stop_event = threading.Event()
        self._model = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._model = self._load_model()
        while not self._stop_event.is_set():
            try:
                chunk = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                text = self._transcribe(chunk)
            except Exception as exc:
                text = f"[ASR error: {exc}]"

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
        )

    def _transcribe(self, chunk: AudioChunk) -> str:
        samples = chunk.samples.astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        if rms < self.min_rms:
            return ""

        segments, _ = self._model.transcribe(
            samples,
            language=self.language,
            vad_filter=self.vad_filter,
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        return "".join(segment.text for segment in segments).strip()
