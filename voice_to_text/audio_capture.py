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
_PORTAUDIO_OPEN_LOCK = threading.Lock()


@dataclass
class AudioChunk:
    source: AudioSource
    started_at: float
    ended_at: float
    sample_rate: int
    samples: np.ndarray


@dataclass
class CaptureDebugEvent:
    source: AudioSource
    message: str
    created_at: float


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
        debug_queue: queue.Queue[CaptureDebugEvent] | None = None,
        chunk_seconds: float = 2.0,
        target_sample_rate: int = 16000,
    ) -> None:
        super().__init__(name=f"{source}-capture", daemon=True)
        self.source = source
        self.device = device
        self.output_queue = output_queue
        self.debug_queue = debug_queue
        self.chunk_seconds = chunk_seconds
        self.target_sample_rate = target_sample_rate
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _debug(self, message: str) -> None:
        if self.debug_queue is not None:
            self.debug_queue.put(CaptureDebugEvent(self.source, message, time.time()))

    def _channel_candidates(self) -> list[int]:
        candidates = [
            self.device.max_input_channels,
            2,
            1,
        ]
        unique: list[int] = []
        for channels in candidates:
            if channels > 0 and channels not in unique:
                unique.append(channels)
        return unique or [1]

    def run(self) -> None:
        pyaudio = _load_pyaudio()
        source_rate = self.device.default_sample_rate
        frames_per_buffer = int(source_rate * 0.1)
        frames_per_chunk = int(source_rate * self.chunk_seconds)

        pa = None
        stream = None
        try:
            with _PORTAUDIO_OPEN_LOCK:
                pa = pyaudio.PyAudio()
                channels = 0
                for candidate_channels in self._channel_candidates():
                    try:
                        self._debug(
                            f"opening device index={self.device.index}, rate={source_rate}, "
                            f"channels={candidate_channels}, chunk={self.chunk_seconds}s"
                        )
                        stream = pa.open(
                            format=pyaudio.paInt16,
                            channels=candidate_channels,
                            rate=source_rate,
                            input=True,
                            input_device_index=self.device.index,
                            frames_per_buffer=frames_per_buffer,
                        )
                        channels = candidate_channels
                        break
                    except OSError as exc:
                        self._debug(f"open failed with channels={candidate_channels}: {exc}")

                if stream is None:
                    self._debug("capture error: no supported channel count worked")
                if stream is None:
                    self._debug("capture error: no supported channel count worked")
                    return

            self._debug("capture started")
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
                    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
                    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
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
                    self._debug(f"chunk queued, rms={rms:.5f}, peak={peak:.3f}, queue={self.output_queue.qsize()}")
                    frames = []
                    frame_count = 0
                    started_at = time.time()
        except Exception as exc:
            self._debug(f"capture error: {exc}")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                stream.close()
            if pa is not None:
                pa.terminate()


def record_wav(device: AudioDevice, output_path: Path, seconds: float = 5.0) -> None:
    pyaudio = _load_pyaudio()
    sample_rate = device.default_sample_rate
    frames_per_buffer = 1024
    total_frames = int(sample_rate / frames_per_buffer * seconds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pyaudio.PyAudio() as pa:
        stream = None
        channels = 0
        for candidate_channels in [device.max_input_channels, 2, 1]:
            if candidate_channels <= 0:
                continue
            try:
                with _PORTAUDIO_OPEN_LOCK:
                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=candidate_channels,
                        rate=sample_rate,
                        input=True,
                        input_device_index=device.index,
                        frames_per_buffer=frames_per_buffer,
                    )
                channels = candidate_channels
                break
            except OSError:
                continue

        if stream is None:
            raise RuntimeError(f"Could not open audio device with a supported channel count: {device}")

        try:
            frames = [stream.read(frames_per_buffer, exception_on_overflow=False) for _ in range(total_frames)]
        finally:
            stream.stop_stream()
            stream.close()

    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(frames))
