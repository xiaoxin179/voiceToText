from __future__ import annotations

import argparse
import json
import queue
import sys
import time
import threading
import math
from dataclasses import asdict
import urllib.request
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
from .audio_devices import select_input_device
from .live_runtime import RuntimeState, compute_default, normalize_language, validate_live_options
from .operation_control import OperationCancelled, cancel_on_interrupt
from .task_cli import configure_task_parser, cmd_task
from .video_transcription import optimize_transcript_to_file
from .service_core import (
    MediaDownloadServiceRequest,
    SpeakerServiceRequest,
    VideoServiceRequest,
    download_video_url,
    transcribe_speaker,
    transcribe_video_url,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice Studio: desktop-equivalent transcription and task tools")
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
    video.add_argument("--model", default="medium")
    video.add_argument("--device", default="cuda")
    video.add_argument("--compute-type", default="float16")
    video.add_argument("--language", default="zh")
    video.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    video.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    video.add_argument("--cookie-browser", choices=["", "chrome", "edge", "firefox"], default="")
    video.add_argument("--optimize-with-deepseek", action="store_true")
    video.add_argument("--deepseek-api-key", default="")
    video.add_argument("--deepseek-model", default="deepseek-v4-flash")
    video.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")

    optimize = subparsers.add_parser("optimize-file", help="Optimize an existing transcript without downloading or transcribing again")
    optimize.add_argument("transcript_file", type=Path)
    optimize.add_argument("--output", type=Path, help="Default: transcript_deepseek.md next to the input")
    optimize.add_argument("--deepseek-api-key", default="")
    optimize.add_argument("--deepseek-model", default="deepseek-v4-flash")
    optimize.add_argument("--json", action="store_true")

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
    speaker.add_argument("--model", default="medium")
    speaker.add_argument("--device", default="cuda")
    speaker.add_argument("--compute-type", default="float16")
    speaker.add_argument("--language", default="zh")
    speaker.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    speaker.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    speaker.add_argument("--chunk-seconds", type=float, default=10.0)
    speaker.add_argument("--min-rms", type=float, default=0.001)
    speaker.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=True)
    speaker.add_argument("--silence-stop-seconds", type=float, default=0.0)
    speaker.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")
    speaker.add_argument("--audio-device-index", type=int)
    speaker.add_argument("--model-load-timeout-seconds", type=float, default=180.0)
    speaker.add_argument("--capture-start-timeout-seconds", type=float, default=15.0)
    speaker.add_argument("--flush-timeout-seconds", type=float, default=300.0)

    session = subparsers.add_parser("session", help="Control asynchronous HTTP transcription sessions")
    session.add_argument("action", choices=["start", "status", "stop", "wait-ready", "wait"])
    session.add_argument("session_id", nargs="?")
    session.add_argument("--endpoint", default="http://127.0.0.1:8765")
    session.add_argument("--config", type=Path, help="JSON file with SpeakerServiceRequest parameters")
    session.add_argument("--timeout", type=float, default=240.0)
    session.add_argument("--poll-seconds", type=float, default=0.25)

    serve = subparsers.add_parser("serve", help="Start the local HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    task = subparsers.add_parser("task", help="Persistent desktop conversion queue (no running server required)")
    configure_task_parser(task)

    listen = subparsers.add_parser("listen", help="Capture audio and transcribe with faster-whisper")
    listen.add_argument("--source", choices=["mic", "system", "both"], default="both")
    listen.add_argument("--chunk-seconds", type=float, default=10.0)
    listen.add_argument("--model", default="medium")
    listen.add_argument("--device", default="cuda")
    listen.add_argument("--compute-type", default="float16")
    listen.add_argument("--language", default="zh")
    listen.add_argument("--text-mode", choices=["simplified", "traditional", "original"], default="simplified")
    listen.add_argument("--min-rms", type=float, default=0.001)
    listen.add_argument("--mic-device-index", type=int)
    listen.add_argument("--system-device-index", type=int)
    listen.add_argument("--model-load-timeout-seconds", type=float, default=180)
    listen.add_argument("--flush-timeout-seconds", type=float, default=300)
    listen.add_argument("--view", choices=["all", "mic", "system"], default="all", help="Display channel; both sources remain captured")
    listen.add_argument("--verbose", action="store_true", help="Show device/model/recognition logs on stderr")
    listen.add_argument("--json", action="store_true", help="Stream JSON lines (transcript, status and final stopped events)")
    listen.add_argument("--output-dir", type=Path, help="Also save per-source and combined transcripts")
    listen.add_argument("--interactive", action="store_true", help="TTY commands: clear, status, view all/mic/system, stop")

    # Match the desktop's CPU/GPU precision defaults while allowing explicit overrides.
    for command in (transcribe, video, speaker, listen):
        command.set_defaults(compute_type=None)

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
        language=normalize_language(args.language),
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
    with cancel_on_interrupt() as stop_event:
        result = transcribe_video_url(request, progress=_cli_progress(args.json), stop_event=stop_event)
    _print_service_result(result.to_dict(), args.json)
    return 0


def cmd_optimize_file(args: argparse.Namespace) -> int:
    source = args.transcript_file.expanduser().resolve()
    output = (args.output or source.with_name("transcript_deepseek.md")).expanduser().resolve()
    if source == output:
        raise ValueError("Output must differ from the input transcript")
    text = source.read_text(encoding="utf-8-sig")
    with cancel_on_interrupt() as stop_event:
        result = optimize_transcript_to_file(text, output, api_key=args.deepseek_api_key,
                                            model=args.deepseek_model, progress=_cli_progress(args.json),
                                            stop_event=stop_event)
    if args.json:
        print(json.dumps({"optimized_text": result.text, "optimized_transcript_path": str(output),
                          "optimized_token_usage": asdict(result.usage)}, ensure_ascii=False, indent=2))
    else:
        print(f"Optimized transcript: {output}")
        print(result.usage.to_display_text())
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
        audio_device_index=args.audio_device_index,
        model_load_timeout_seconds=args.model_load_timeout_seconds,
        capture_start_timeout_seconds=args.capture_start_timeout_seconds,
        flush_timeout_seconds=args.flush_timeout_seconds,
    )
    result = transcribe_speaker(request, progress=_cli_progress(args.json))
    _print_service_result(result.to_dict(), args.json)
    return 0 if (result.integrity.get("complete", True)
                 and result.audio_diagnostics.get("status", "ok") == "ok") else 3


def cmd_session(args: argparse.Namespace) -> int:
    if args.timeout <= 0 or args.poll_seconds <= 0:
        raise ValueError("timeout and poll-seconds must be positive")
    endpoint = args.endpoint.rstrip("/")
    if args.action == "start":
        payload = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
        request = urllib.request.Request(endpoint + "/sessions/speaker", data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
    else:
        if not args.session_id or not all(c.isalnum() or c == "_" for c in args.session_id):
            raise ValueError("a valid session_id is required")
        path = f"/sessions/{args.session_id}"
        request = urllib.request.Request(endpoint + path + ("/stop" if args.action == "stop" else "?result=1"),
                                         data=b"{}" if args.action == "stop" else None,
                                         headers={"Content-Type": "application/json"})
    deadline = time.monotonic() + args.timeout
    while True:
        with urllib.request.urlopen(request, timeout=min(10, args.timeout)) as response:
            data = json.load(response)
        terminal = data["status"] in ("completed", "incomplete", "failed")
        if args.action not in ("wait-ready", "wait") or terminal or (args.action == "wait-ready" and data["ready"]):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            if data["status"] in ("failed", "incomplete"):
                return 3
            return 0 if args.action != "wait-ready" or data["ready"] else 3
        if time.monotonic() >= deadline:
            raise TimeoutError("session wait timed out; session continues running, use session stop explicitly")
        time.sleep(args.poll_seconds)


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
    if data.get("optimized_transcript_path"):
        print(f"Optimized transcript: {data['optimized_transcript_path']}")
    if data.get("optimized_token_usage") is not None:
        from .video_transcription import DeepSeekTokenUsage
        print(DeepSeekTokenUsage(**data["optimized_token_usage"]).to_display_text())


def _build_capture_threads(args: argparse.Namespace, audio_queue: queue.Queue[AudioChunk], debug_queue) -> list[AudioCapture]:
    threads: list[AudioCapture] = []
    if args.source in ("mic", "both"):
        threads.append(
            AudioCapture(
                source="mic",
                device=select_input_device("mic", args.mic_device_index),
                output_queue=audio_queue,
                debug_queue=debug_queue,
                chunk_seconds=args.chunk_seconds,
            )
        )
    if args.source in ("system", "both"):
        threads.append(
            AudioCapture(
                source="system",
                device=select_input_device("system", args.system_device_index),
                output_queue=audio_queue,
                debug_queue=debug_queue,
                chunk_seconds=args.chunk_seconds,
            )
        )

    return threads


def cmd_listen(args: argparse.Namespace) -> int:
    validate_live_options(args.chunk_seconds, args.min_rms)
    for value in (args.model_load_timeout_seconds, args.flush_timeout_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeouts must be finite and positive")
    if args.interactive and (not sys.stdin.isatty() or args.json):
        raise ValueError("--interactive requires a terminal and cannot be combined with --json")
    audio_queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=6)
    transcript_queue: queue.Queue[Transcript] = queue.Queue()
    debug_queue: queue.Queue = queue.Queue()
    captures = _build_capture_threads(args, audio_queue, debug_queue)
    asr = AsrWorker(
        input_queue=audio_queue,
        output_queue=transcript_queue,
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=normalize_language(args.language),
        min_rms=args.min_rms,
        text_mode=args.text_mode,
        debug_queue=debug_queue,
    )
    runtime = RuntimeState(audio_queue, transcript_queue, debug_queue, asr, captures)
    rows: list[Transcript] = []
    commands: queue.Queue[str] = queue.Queue()
    view = args.view
    start_time = time.monotonic()

    def status(phase, **extra):
        data = {"type": "status", "phase": phase, "elapsed_seconds": round(time.monotonic() - start_time, 1),
                "queued_chunks": audio_queue.qsize(), **extra}
        if args.json:
            print(json.dumps(data, ensure_ascii=False), flush=True)
        else:
            print(f"{phase}: {extra or ''}", file=sys.stderr, flush=True)

    def show_transcript(transcript):
        if view not in ("all", transcript.source):
            return
        if args.json:
            print(json.dumps({"type": "transcript", **asdict(transcript)}, ensure_ascii=False), flush=True)
        else:
            stamp = time.strftime('%H:%M:%S', time.localtime(transcript.ended_at))
            print(f"[{stamp}] [{transcript.source.upper()}] {transcript.text}", flush=True)

    def drain():
        while True:
            try:
                event = debug_queue.get_nowait()
            except queue.Empty:
                break
            if args.verbose:
                print(getattr(event, "message", str(event)), file=sys.stderr, flush=True)
        while True:
            try:
                transcript = transcript_queue.get_nowait()
            except queue.Empty:
                break
            rows.append(transcript)
            show_transcript(transcript)

    def read_commands():
        for line in sys.stdin:
            commands.put(line.strip())

    if args.interactive:
        threading.Thread(target=read_commands, daemon=True).start()
        print("Commands: clear | status | view all/mic/system | stop", file=sys.stderr)
    status("loading_model")
    asr.start()
    try:
        listening = False
        while True:
            runtime.start_capture_if_ready(args.model_load_timeout_seconds)
            if runtime.capture_started:
                runtime.check_capture()
                if not listening and all(c.ready.is_set() for c in captures):
                    status("listening", devices={c.source: asdict(c.device) for c in captures})
                    listening = True
            drain()
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            if command == "clear":
                rows.clear()
                print("\033[2J\033[H", end="", flush=True)
            elif command == "status":
                status("listening" if listening else "loading_model", view=view,
                       threads=[c.source for c in captures if c.is_alive()])
            elif command and command.startswith("view ") and command[5:] in ("all", "mic", "system"):
                view = command[5:]
                print("\033[2J\033[H", end="", flush=True)
                for transcript in rows:
                    show_transcript(transcript)
            elif command:
                print("Unknown command; use clear, status, view all/mic/system, stop", file=sys.stderr)
            time.sleep(.05)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        runtime.shutdown_error = str(exc)
        raise
    finally:
        status("draining")
        runtime.request_stop(args.flush_timeout_seconds)
        while not runtime.stopped.wait(.05):
            drain()
        drain()
        paths = {}
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for channel in ("all", "mic", "system"):
                path = args.output_dir / f"transcript_{channel}.md"
                path.write_text("\n".join(
                    f"[{time.strftime('%H:%M:%S', time.localtime(t.ended_at))}] [{t.source.upper()}] {t.text}"
                    for t in rows if channel in ("all", t.source)), encoding="utf-8")
                paths[channel] = str(path.resolve())
        status("stopped", complete=not bool(runtime.shutdown_error), error=runtime.shutdown_error,
               transcript_paths=paths)
    return 3 if runtime.shutdown_error else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "task" and hasattr(args, "compute_type") and args.compute_type is None:
        args.compute_type = compute_default(args.device)

    try:
        if args.command == "task":
            return cmd_task(args)
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
        if args.command == "optimize-file":
            return cmd_optimize_file(args)
        if args.command == "download-video":
            return cmd_download_video(args)
        if args.command == "transcribe-speaker":
            return cmd_transcribe_speaker(args)
        if args.command == "serve":
            from .api_server import run_server

            run_server(args.host, args.port)
            return 0
        if args.command == "session":
            return cmd_session(args)
        if args.command == "listen":
            return cmd_listen(args)
    except (OperationCancelled, KeyboardInterrupt):
        print("Cancelled; completed output files are retained.", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, KeyError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2
