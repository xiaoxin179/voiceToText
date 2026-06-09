from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from .audio_devices import AudioDevice, _load_pyaudio

AudioSource = Literal["mic", "system"]


@dataclass
class AudioChunk:
    source: AudioSource
    started_at: float
    ended_at: float
    sample_rate: int
    samples: np.ndarray


def bytes_to_mono_float32(data: bytes, channels: int) -> np.ndarray:
    pcm = np.frombuffer(data, dtype=np.int16)
    if pcm.size == 0:
        return np.empty(0, dtype=np.float32)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return (pcm.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int = 16000) -> np.ndarray:
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)

    duration = samples.size / float(source_rate)
    target_size = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, samples.size - 1, num=samples.size)
    target_positions = np.linspace(0.0, samples.size - 1, num=target_size)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


class AudioCapture(threading.Thread):
    def __init__(
        self,
        *,
        source: AudioSource,
        device: AudioDevice,
        output_queue: queue.Queue[AudioChunk],
        chunk_seconds: float = 2.0,
        target_sample_rate: int = 16000,
    ) -> None:
        super().__init__(name=f"{source}-capture", daemon=True)
        self.source = source
        self.device = device
        self.output_queue = output_queue
        self.chunk_seconds = chunk_seconds
        self.target_sample_rate = target_sample_rate
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        pyaudio = _load_pyaudio()
        source_rate = self.device.default_sample_rate
        channels = max(1, min(self.device.max_input_channels, 2))
        frames_per_buffer = int(source_rate * 0.1)
        frames_per_chunk = int(source_rate * self.chunk_seconds)

        with pyaudio.PyAudio() as pa:
            with pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=source_rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=frames_per_buffer,
            ) as stream:
                frames: list[bytes] = []
                frame_count = 0
                started_at = time.time()

                while not self._stop_event.is_set():
                    data = stream.read(frames_per_buffer, exception_on_overflow=False)
                    frames.append(data)
                    frame_count += frames_per_buffer

                    if frame_count >= frames_per_chunk:
                        ended_at = time.time()
                        mono = bytes_to_mono_float32(b"".join(frames), channels)
                        resampled = resample_linear(mono, source_rate, self.target_sample_rate)
                        self.output_queue.put(
                            AudioChunk(
                                source=self.source,
                                started_at=started_at,
                                ended_at=ended_at,
                                sample_rate=self.target_sample_rate,
                                samples=resampled,
                            )
                        )
                        frames = []
                        frame_count = 0
                        started_at = time.time()


def record_wav(device: AudioDevice, output_path: Path, seconds: float = 5.0) -> None:
    pyaudio = _load_pyaudio()
    sample_rate = device.default_sample_rate
    channels = max(1, min(device.max_input_channels, 2))
    frames_per_buffer = 1024
    total_frames = int(sample_rate / frames_per_buffer * seconds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pyaudio.PyAudio() as pa:
        with pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=device.index,
            frames_per_buffer=frames_per_buffer,
        ) as stream:
            frames = [stream.read(frames_per_buffer, exception_on_overflow=False) for _ in range(total_frames)]

    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(frames))
