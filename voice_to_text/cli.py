from __future__ import annotations

import argparse
import json
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
from .text_converter import TextConverter
from .service_core import (
    MediaDownloadServiceRequest,
    SpeakerServiceRequest,
    VideoServiceRequest,
    download_video_url,
    transcribe_speaker,
    transcribe_video_url,
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
    transcribe.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    transcribe.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=True)

    video = subparsers.add_parser("transcribe-video", help="Download/transcribe a platform video URL")
    video.add_argument("url")
    video.add_argument("--model", default="tiny")
    video.add_argument("--device", default="cpu")
    video.add_argument("--compute-type", default="int8")
    video.add_argument("--language", default="zh")
    video.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    video.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    video.add_argument("--cookie-browser", choices=["", "chrome", "edge", "firefox"], default="")
    video.add_argument("--optimize-with-deepseek", action="store_true")
    video.add_argument("--deepseek-api-key", default="")
    video.add_argument("--deepseek-model", default="deepseek-v4-flash")
    video.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")

    download = subparsers.add_parser("download-video", help="Download a platform video URL without transcription")
    download.add_argument("url")
    download.add_argument("--output-dir", type=Path, default=Path("downloads"))
    download.add_argument("--cookie-browser", choices=["", "chrome", "edge", "firefox"], default="")
    download.add_argument("--cookies-file", type=Path)
    download.add_argument("--referer", default="")
    download.add_argument("--header", action="append", default=[], help="Extra request header, for example: Authorization: Bearer ...")
    download.add_argument("--format", default="bv*+ba/b", dest="format_selector")
    download.add_argument("--backend", choices=["auto", "yt-dlp", "omniget"], default="auto")
    download.add_argument("--omniget-endpoint", default="", help="OmniGet local bridge, for example http://127.0.0.1:47720")
    download.add_argument("--omniget-token", default="", help="OmniGet local bridge bearer token")
    download.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")

    speaker = subparsers.add_parser("transcribe-speaker", help="Record system speaker/mic audio and transcribe it")
    speaker.add_argument("--seconds", type=float, default=60.0)
    speaker.add_argument("--source", choices=["system", "mic"], default="system")
    speaker.add_argument("--model", default="tiny")
    speaker.add_argument("--device", default="cpu")
    speaker.add_argument("--compute-type", default="int8")
    speaker.add_argument("--language", default="zh")
    speaker.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    speaker.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    speaker.add_argument("--chunk-seconds", type=float, default=2.0)
    speaker.add_argument("--min-rms", type=float, default=0.006)
    speaker.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=True)
    speaker.add_argument("--silence-stop-seconds", type=float, default=0.0)
    speaker.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")

    serve = subparsers.add_parser("serve", help="Start the local HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    listen = subparsers.add_parser("listen", help="Capture audio and transcribe with faster-whisper")
    listen.add_argument("--source", choices=["mic", "system", "both"], default="both")
    listen.add_argument("--chunk-seconds", type=float, default=2.0)
    listen.add_argument("--model", default="medium")
    listen.add_argument("--device", default="cuda")
    listen.add_argument("--compute-type", default="float16")
    listen.add_argument("--language", default="zh")
    listen.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    listen.add_argument("--min-rms", type=float, default=0.006)

    return parser


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"


def cmd_doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    for package in (
        "pyaudiowpatch",
        "faster-whisper",
        "ctranslate2",
        "numpy",
        "soundfile",
        "opencc-python-reimplemented",
    ):
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
    text_converter = TextConverter(args.text_mode)
    for segment in segments:
        text = text_converter.convert(segment.text.strip())
        print(f"[{segment.start:7.2f}s -> {segment.end:7.2f}s] {text}")
    return 0


def cmd_transcribe_video(args: argparse.Namespace) -> int:
    request = VideoServiceRequest(
        url=args.url,
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=None if args.language == "auto" else args.language,
        text_mode=args.text_mode,
        output_dir=args.output_dir,
        cookie_browser=args.cookie_browser,
        optimize_with_deepseek=args.optimize_with_deepseek,
        deepseek_api_key=args.deepseek_api_key,
        deepseek_model=args.deepseek_model,
    )
    result = transcribe_video_url(request, progress=_cli_progress(args.json))
    _print_service_result(result.to_dict(), args.json)
    return 0


def cmd_download_video(args: argparse.Namespace) -> int:
    request = MediaDownloadServiceRequest(
        url=args.url,
        output_dir=args.output_dir,
        cookie_browser=args.cookie_browser,
        cookies_file=args.cookies_file,
        referer=args.referer,
        headers=tuple(args.header),
        format_selector=args.format_selector,
        backend=args.backend,
        omniget_endpoint=args.omniget_endpoint,
        omniget_token=args.omniget_token,
    )
    result = download_video_url(request, progress=_cli_progress(args.json))
    data = result.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print()
        if data["status"] == "queued":
            print("Queued in OmniGet. Open OmniGet to see progress and the configured download location.")
        else:
            print(f"Downloaded media: {data['media_path']}")
        if data["title"]:
            print(f"Title: {data['title']}")
    return 0


def cmd_transcribe_speaker(args: argparse.Namespace) -> int:
    request = SpeakerServiceRequest(
        seconds=args.seconds,
        source=args.source,
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=None if args.language == "auto" else args.language,
        text_mode=args.text_mode,
        output_dir=args.output_dir,
        chunk_seconds=args.chunk_seconds,
        min_rms=args.min_rms,
        vad_filter=args.vad_filter,
        silence_stop_seconds=args.silence_stop_seconds,
    )
    result = transcribe_speaker(request, progress=_cli_progress(args.json))
    _print_service_result(result.to_dict(), args.json)
    return 0


def _cli_progress(json_mode: bool):
    stream = sys.stderr if json_mode else sys.stdout

    def progress(message: str) -> None:
        print(message, file=stream, flush=True)

    return progress


def _print_service_result(data: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print()
    print(f"Output dir: {data['output_dir']}")
    print(f"Raw transcript: {data['raw_transcript_path']}")
    print(f"Timestamped transcript: {data['timestamped_transcript_path']}")
    if data.get("audio_path"):
        print(f"Audio: {data['audio_path']}")


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
        text_mode=args.text_mode,
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
        if args.command == "transcribe-video":
            return cmd_transcribe_video(args)
        if args.command == "download-video":
            return cmd_download_video(args)
        if args.command == "transcribe-speaker":
            return cmd_transcribe_speaker(args)
        if args.command == "serve":
            from .api_server import run_server

            run_server(args.host, args.port)
            return 0
        if args.command == "listen":
            return cmd_listen(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2
