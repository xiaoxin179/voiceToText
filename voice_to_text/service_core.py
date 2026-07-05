from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np

from .asr import AsrWorker, Transcript
from .audio_capture import AudioCapture, AudioChunk, AudioSource
from .audio_devices import get_default_microphone, get_default_system_loopback
from .text_converter import TextMode
from .video_transcription import VideoTranscriptionOptions, transcribe_platform_video

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class VideoServiceRequest:
    url: str
    model_size: str = "tiny"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "zh"
    text_mode: TextMode = "simplified"
    output_dir: Path = Path("transcripts")
    cookie_browser: str = ""
    optimize_with_deepseek: bool = False
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "VideoServiceRequest":
        return cls(
            url=str(data["url"]),
            model_size=str(data.get("model_size") or data.get("model") or "tiny"),
            device=str(data.get("device") or "cpu"),
            compute_type=str(data.get("compute_type") or "int8"),
            language=_normalize_language(data.get("language", "zh")),
            text_mode=_normalize_text_mode(data.get("text_mode", "simplified")),
            output_dir=Path(str(data.get("output_dir") or "transcripts")),
            cookie_browser=str(data.get("cookie_browser") or ""),
            optimize_with_deepseek=bool(data.get("optimize_with_deepseek", False)),
            deepseek_api_key=str(data.get("deepseek_api_key") or ""),
            deepseek_model=str(data.get("deepseek_model") or "deepseek-v4-flash"),
        )


@dataclass(frozen=True)
class SpeakerServiceRequest:
    seconds: float = 60.0
    source: AudioSource = "system"
    model_size: str = "tiny"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "zh"
    text_mode: TextMode = "simplified"
    output_dir: Path = Path("transcripts")
    chunk_seconds: float = 2.0
    min_rms: float = 0.006
    vad_filter: bool = True
    silence_stop_seconds: float = 0.0
    model_load_timeout_seconds: float = 180.0
    flush_timeout_seconds: float = 30.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SpeakerServiceRequest":
        source = str(data.get("source") or "system")
        if source not in ("mic", "system"):
            raise ValueError("source must be 'system' or 'mic'")
        return cls(
            seconds=float(data.get("seconds", data.get("duration_seconds", 60.0))),
            source=source,  # type: ignore[arg-type]
            model_size=str(data.get("model_size") or data.get("model") or "tiny"),
            device=str(data.get("device") or "cpu"),
            compute_type=str(data.get("compute_type") or "int8"),
            language=_normalize_language(data.get("language", "zh")),
            text_mode=_normalize_text_mode(data.get("text_mode", "simplified")),
            output_dir=Path(str(data.get("output_dir") or "transcripts")),
            chunk_seconds=float(data.get("chunk_seconds", 2.0)),
            min_rms=float(data.get("min_rms", 0.006)),
            vad_filter=bool(data.get("vad_filter", True)),
            silence_stop_seconds=float(data.get("silence_stop_seconds", 0.0)),
            model_load_timeout_seconds=float(data.get("model_load_timeout_seconds", 180.0)),
            flush_timeout_seconds=float(data.get("flush_timeout_seconds", 30.0)),
        )


@dataclass(frozen=True)
class TranscriptSegment:
    source: str
    text: str
    start_seconds: float
    end_seconds: float
    recognized_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "text": self.text,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "recognized_at": self.recognized_at,
        }


@dataclass(frozen=True)
class ServiceTranscriptionResult:
    kind: Literal["video", "speaker"]
    raw_text: str
    timestamped_text: str
    output_dir: Path
    raw_transcript_path: Path
    timestamped_transcript_path: Path
    audio_path: Path | None = None
    optimized_text: str | None = None
    optimized_transcript_path: Path | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    duration_seconds: float | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "raw_text": self.raw_text,
            "timestamped_text": self.timestamped_text,
            "output_dir": str(self.output_dir),
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "raw_transcript_path": str(self.raw_transcript_path),
            "timestamped_transcript_path": str(self.timestamped_transcript_path),
            "optimized_text": self.optimized_text,
            "optimized_transcript_path": str(self.optimized_transcript_path) if self.optimized_transcript_path else None,
            "segments": [segment.to_dict() for segment in self.segments],
            "duration_seconds": self.duration_seconds,
            "logs": self.logs,
        }


def transcribe_video_url(
    request: VideoServiceRequest,
    progress: ProgressCallback | None = None,
) -> ServiceTranscriptionResult:
    logs: list[str] = []

    def emit(message: str) -> None:
        logs.append(message)
        if progress:
            progress(message)

    result = transcribe_platform_video(
        VideoTranscriptionOptions(
            url=request.url,
            model_size=request.model_size,
            device=request.device,
            compute_type=request.compute_type,
            language=request.language,
            text_mode=request.text_mode,
            output_dir=request.output_dir,
            cookie_browser=request.cookie_browser,
            optimize_with_deepseek=request.optimize_with_deepseek,
            deepseek_api_key=request.deepseek_api_key,
            deepseek_model=request.deepseek_model,
        ),
        progress=emit,
    )
    return ServiceTranscriptionResult(
        kind="video",
        raw_text=result.raw_text,
        timestamped_text=result.timestamped_text,
        output_dir=result.raw_transcript_path.parent,
        audio_path=result.audio_path,
        raw_transcript_path=result.raw_transcript_path,
        timestamped_transcript_path=result.timestamped_transcript_path,
        optimized_text=result.optimized_text,
        optimized_transcript_path=result.optimized_transcript_path,
        logs=logs,
    )


def transcribe_speaker(
    request: SpeakerServiceRequest,
    progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
) -> ServiceTranscriptionResult:
    if request.seconds <= 0:
        raise ValueError("seconds must be greater than 0")
    if request.chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than 0")

    logs: list[str] = []
    stop_event = stop_event or threading.Event()
    work_dir = _create_work_dir(request.output_dir, "speaker")

    def emit(message: str) -> None:
        logs.append(message)
        if progress:
            progress(message)

    emit(f"准备监听{_source_label(request.source)}音频 {request.seconds:.1f}s")
    device = get_default_system_loopback() if request.source == "system" else get_default_microphone()
    emit(f"使用音频设备: {device.name}")

    capture_queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=12)
    asr_input_queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=8)
    transcript_queue: queue.Queue[Transcript] = queue.Queue()
    debug_queue: queue.Queue[Any] = queue.Queue()

    asr = AsrWorker(
        input_queue=asr_input_queue,
        output_queue=transcript_queue,
        debug_queue=debug_queue,
        model_size=request.model_size,
        device=request.device,
        compute_type=request.compute_type,
        language=request.language,
        vad_filter=request.vad_filter,
        min_rms=request.min_rms,
        text_mode=request.text_mode,
    )
    asr.start()
    _wait_for_asr_model(asr, request.model_load_timeout_seconds, emit, debug_queue)

    capture = AudioCapture(
        source=request.source,
        device=device,
        output_queue=capture_queue,
        debug_queue=debug_queue,
        chunk_seconds=request.chunk_seconds,
    )
    capture.start()
    started_at = time.time()
    emit("扬声器监听已启动" if request.source == "system" else "麦克风监听已启动")

    chunks: list[AudioChunk] = []
    transcripts: list[Transcript] = []
    heard_audio = False
    last_loud_at = time.monotonic()
    deadline = time.monotonic() + request.seconds

    try:
        while time.monotonic() < deadline and not stop_event.is_set():
            _drain_debug(debug_queue, emit)
            _drain_transcripts(transcript_queue, transcripts, emit)
            try:
                chunk = capture_queue.get(timeout=0.1)
            except queue.Empty:
                if _silence_limit_reached(request, heard_audio, last_loud_at):
                    emit("检测到持续静音，提前结束监听")
                    break
                continue

            chunks.append(chunk)
            rms = _chunk_rms(chunk)
            if rms >= request.min_rms:
                heard_audio = True
                last_loud_at = time.monotonic()
            _put_latest(asr_input_queue, chunk)
            if _silence_limit_reached(request, heard_audio, last_loud_at):
                emit("检测到持续静音，提前结束监听")
                break
    finally:
        capture.stop()
        capture.join(timeout=3.0)

    while True:
        try:
            chunk = capture_queue.get_nowait()
        except queue.Empty:
            break
        chunks.append(chunk)
        _put_latest(asr_input_queue, chunk)

    emit("监听结束，等待识别队列完成")
    flush_deadline = time.monotonic() + request.flush_timeout_seconds
    quiet_since: float | None = None
    while time.monotonic() < flush_deadline:
        _drain_debug(debug_queue, emit)
        before = len(transcripts)
        _drain_transcripts(transcript_queue, transcripts, emit)
        if len(transcripts) != before:
            quiet_since = time.monotonic()
        if asr_input_queue.empty():
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 1.0:
                break
        time.sleep(0.1)

    asr.stop()
    asr.join(timeout=3.0)
    _drain_debug(debug_queue, emit)
    _drain_transcripts(transcript_queue, transcripts, emit)

    if asr.last_error:
        raise RuntimeError(f"本地 Whisper 识别失败: {asr.last_error}")
    if not chunks:
        raise RuntimeError("没有采集到音频。请确认系统扬声器 loopback 设备可用。")

    audio_path = work_dir / "speaker.wav"
    _write_chunks_wav(chunks, audio_path)
    segments = _segments_from_transcripts(transcripts, started_at)
    raw_text = "\n".join(segment.text for segment in segments)
    timestamped_text = "\n".join(
        f"[{_format_seconds(segment.start_seconds)} -> {_format_seconds(segment.end_seconds)}] {segment.text}"
        for segment in segments
    )

    raw_path = work_dir / "transcript_raw.md"
    timestamped_path = work_dir / "transcript_timestamped.md"
    raw_path.write_text(raw_text, encoding="utf-8")
    timestamped_path.write_text(timestamped_text, encoding="utf-8")
    emit(f"音频已保存: {audio_path}")
    emit(f"文字稿已保存: {raw_path}")

    return ServiceTranscriptionResult(
        kind="speaker",
        raw_text=raw_text,
        timestamped_text=timestamped_text,
        output_dir=work_dir,
        audio_path=audio_path,
        raw_transcript_path=raw_path,
        timestamped_transcript_path=timestamped_path,
        segments=segments,
        duration_seconds=round(time.time() - started_at, 3),
        logs=logs,
    )


def _normalize_language(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "auto" else text


def _normalize_text_mode(value: Any) -> TextMode:
    text = str(value or "simplified")
    if text not in ("original", "simplified", "traditional"):
        raise ValueError("text_mode must be original, simplified, or traditional")
    return text  # type: ignore[return-value]


def _create_work_dir(output_dir: Path, prefix: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = output_dir / f"{prefix}-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{prefix}-{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _wait_for_asr_model(
    asr: AsrWorker,
    timeout_seconds: float,
    emit: ProgressCallback,
    debug_queue: queue.Queue[Any],
) -> None:
    emit("加载本地 Whisper 模型")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _drain_debug(debug_queue, emit)
        if asr.model_ready.wait(timeout=0.2):
            emit("本地 Whisper 模型已就绪")
            return
        if asr.last_error:
            raise RuntimeError(f"本地 Whisper 模型加载失败: {asr.last_error}")
    raise TimeoutError(f"本地 Whisper 模型加载超时: {timeout_seconds:.0f}s")


def _drain_debug(debug_queue: queue.Queue[Any], emit: ProgressCallback) -> None:
    while True:
        try:
            event = debug_queue.get_nowait()
        except queue.Empty:
            return
        source = getattr(event, "source", "")
        message = getattr(event, "message", str(event))
        prefix = f"{source}: " if source else ""
        emit(f"{prefix}{message}")


def _drain_transcripts(
    transcript_queue: queue.Queue[Transcript],
    transcripts: list[Transcript],
    emit: ProgressCallback,
) -> None:
    while True:
        try:
            transcript = transcript_queue.get_nowait()
        except queue.Empty:
            return
        transcripts.append(transcript)
        emit(f"{transcript.source}: {transcript.text}")


def _put_latest(target_queue: queue.Queue[AudioChunk], chunk: AudioChunk) -> None:
    while True:
        try:
            target_queue.put_nowait(chunk)
            return
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                continue


def _chunk_rms(chunk: AudioChunk) -> float:
    samples = chunk.samples.astype(np.float32, copy=False)
    return float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0


def _silence_limit_reached(request: SpeakerServiceRequest, heard_audio: bool, last_loud_at: float) -> bool:
    return (
        request.silence_stop_seconds > 0
        and heard_audio
        and time.monotonic() - last_loud_at >= request.silence_stop_seconds
    )


def _segments_from_transcripts(transcripts: list[Transcript], session_started_at: float) -> list[TranscriptSegment]:
    ordered = sorted(transcripts, key=lambda item: item.started_at)
    return [
        TranscriptSegment(
            source=transcript.source,
            text=transcript.text,
            start_seconds=max(0.0, transcript.started_at - session_started_at),
            end_seconds=max(0.0, transcript.ended_at - session_started_at),
            recognized_at=transcript.recognized_at,
        )
        for transcript in ordered
        if transcript.text.strip()
    ]


def _write_chunks_wav(chunks: list[AudioChunk], output_path: Path) -> None:
    sample_rate = chunks[0].sample_rate
    samples = np.concatenate([chunk.samples.astype(np.float32, copy=False) for chunk in chunks])
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _format_seconds(value: float) -> str:
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _source_label(source: AudioSource) -> str:
    return "系统扬声器" if source == "system" else "麦克风"
