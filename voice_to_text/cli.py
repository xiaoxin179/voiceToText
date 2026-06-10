from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from .asr import AsrWorker, Transcript
from .audio_capture import AudioCapture, AudioChunk, record_wav
from .cuda_runtime import add_cuda_dll_directories, candidate_cuda_dll_dirs
from .audio_devices import (
    format_devices,
    get_default_microphone,
    get_default_system_loopback,
    list_devices,
    list_loopback_devices,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows dual-source voice-to-text MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check Python, dependencies, CUDA, and default devices")
    subparsers.add_parser("devices", help="List audio devices and loopback devices")

    record = subparsers.add_parser("record-test", help="Record short wav files for mic/system audio")
    record.add_argument("--seconds", type=float, default=5.0)
    record.add_argument("--out-dir", type=Path, default=Path("recordings"))
    record.add_argument("--source", choices=["mic", "system", "both"], default="both")

    transcribe = subparsers.add_parser("transcribe-file", help="Transcribe an audio file with faster-whisper")
    transcribe.add_argument("audio_file", type=Path)
    transcribe.add_argument("--model", default="medium")
    transcribe.add_argument("--device", default="cuda")
    transcribe.add_argument("--compute-type", default="float16")
    transcribe.add_argument("--language", default="zh")
    transcribe.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=True)

    listen = subparsers.add_parser("listen", help="Capture audio and transcribe with faster-whisper")
    listen.add_argument("--source", choices=["mic", "system", "both"], default="both")
    listen.add_argument("--chunk-seconds", type=float, default=2.0)
    listen.add_argument("--model", default="medium")
    listen.add_argument("--device", default="cuda")
    listen.add_argument("--compute-type", default="float16")
    listen.add_argument("--language", default="zh")
    listen.add_argument("--min-rms", type=float, default=0.006)

    return parser


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"


def cmd_doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    for package in ("pyaudiowpatch", "faster-whisper", "ctranslate2", "numpy", "soundfile"):
        print(f"{package}: {_package_version(package)}")

    try:
        import ctranslate2

        cuda_dirs = candidate_cuda_dll_dirs()
        print(f"CUDA DLL directories: {[str(path) for path in cuda_dirs] or 'not found in environment'}")
        cuda_types = ctranslate2.get_supported_compute_types("cuda")
        print(f"CTranslate2 CUDA compute types: {sorted(cuda_types)}")
    except Exception as exc:
        print(f"CTranslate2 CUDA check failed: {exc}")

    try:
        print(f"Default microphone: {get_default_microphone()}")
        print(f"Default system loopback: {get_default_system_loopback()}")
    except Exception as exc:
        print(f"Audio device check failed: {exc}")

    return 0


def cmd_devices() -> int:
    print("Audio devices:")
    print(format_devices(list_devices()))
    print()
    print("Loopback devices:")
    print(format_devices(list_loopback_devices()))
    print()
    print(f"Default microphone: {get_default_microphone()}")
    print(f"Default system loopback: {get_default_system_loopback()}")
    return 0


def cmd_record_test(args: argparse.Namespace) -> int:
    if args.source in ("mic", "both"):
        mic = get_default_microphone()
        mic_path = args.out_dir / "mic.wav"
        print(f"Recording microphone for {args.seconds}s -> {mic_path}")
        record_wav(mic, mic_path, args.seconds)

    if args.source in ("system", "both"):
        system = get_default_system_loopback()
        system_path = args.out_dir / "system.wav"
        print(f"Recording system audio for {args.seconds}s -> {system_path}")
        record_wav(system, system_path, args.seconds)

    print("Done.")
    return 0


def cmd_transcribe_file(args: argparse.Namespace) -> int:
    if not args.audio_file.exists():
        print(f"Audio file does not exist: {args.audio_file}", file=sys.stderr)
        return 1

    try:
        if args.device == "cuda":
            add_cuda_dll_directories()

        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: faster-whisper. Install it with "
            "`pip install faster-whisper` inside the conda environment."
        ) from exc

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type, local_files_only=True)
    segments, info = model.transcribe(
        str(args.audio_file),
        language=args.language or None,
        vad_filter=args.vad_filter,
        beam_size=1,
        condition_on_previous_text=False,
    )

    print(f"Language: {info.language} ({info.language_probability:.2f})")
    for segment in segments:
        print(f"[{segment.start:7.2f}s -> {segment.end:7.2f}s] {segment.text.strip()}")
    return 0


def _start_capture_threads(args: argparse.Namespace, audio_queue: queue.Queue[AudioChunk]) -> list[AudioCapture]:
    threads: list[AudioCapture] = []
    if args.source in ("mic", "both"):
        threads.append(
            AudioCapture(
                source="mic",
                device=get_default_microphone(),
                output_queue=audio_queue,
                chunk_seconds=args.chunk_seconds,
            )
        )
    if args.source in ("system", "both"):
        threads.append(
            AudioCapture(
                source="system",
                device=get_default_system_loopback(),
                output_queue=audio_queue,
                chunk_seconds=args.chunk_seconds,
            )
        )

    for thread in threads:
        thread.start()
    return threads


def cmd_listen(args: argparse.Namespace) -> int:
    audio_queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=6)
    transcript_queue: queue.Queue[Transcript] = queue.Queue()

    asr = AsrWorker(
        input_queue=audio_queue,
        output_queue=transcript_queue,
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language or None,
        min_rms=args.min_rms,
    )

    print("Loading ASR model. First run may download model files.")
    asr.start()
    capture_threads = _start_capture_threads(args, audio_queue)
    print("Listening. Press Ctrl+C to stop.")

    try:
        while True:
            try:
                transcript = transcript_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            label = "MIC" if transcript.source == "mic" else "SYSTEM"
            lag = transcript.recognized_at - transcript.ended_at
            print(f"[{time.strftime('%H:%M:%S')}] [{label}] (+{lag:.1f}s) {transcript.text}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for thread in capture_threads:
            thread.stop()
        asr.stop()
        for thread in capture_threads:
            thread.join(timeout=2.0)
        asr.join(timeout=2.0)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "devices":
            return cmd_devices()
        if args.command == "record-test":
            return cmd_record_test(args)
        if args.command == "transcribe-file":
            return cmd_transcribe_file(args)
        if args.command == "listen":
            return cmd_listen(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2
