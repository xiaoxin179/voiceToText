from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service_core import (
    MediaDownloadServiceRequest,
    ServiceTranscriptionResult,
    SpeakerServiceRequest,
    VideoServiceRequest,
    download_video_url,
    transcribe_speaker,
    transcribe_video_url,
)


@dataclass
class ServiceSession:
    id: str
    kind: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def to_dict(self, include_result: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "session_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "logs": self.logs[-200:],
        }
        if include_result:
            data["result"] = self.result
        return data


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, ServiceSession] = {}

    def create(self, kind: str) -> ServiceSession:
        session = ServiceSession(id=f"{kind}_{uuid.uuid4().hex[:12]}", kind=kind)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> ServiceSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[ServiceSession]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda item: item.created_at, reverse=True)


STORE = SessionStore()


class VoiceToTextHandler(BaseHTTPRequestHandler):
    server_version = "VoiceToTextService/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/health":
                self._send_json({"ok": True, "service": "voice-to-text"})
                return
            if parsed.path == "/sessions":
                self._send_json({"sessions": [session.to_dict() for session in STORE.list()]})
                return
            if parsed.path.startswith("/sessions/"):
                session_id = parsed.path.removeprefix("/sessions/").strip("/")
                session = STORE.get(session_id)
                if session is None:
                    self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                query = parse_qs(parsed.query)
                include_result = query.get("result", ["0"])[0] in ("1", "true", "yes")
                self._send_json(session.to_dict(include_result=include_result))
                return
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            data = self._read_json()
            if parsed.path == "/transcribe/video":
                result = transcribe_video_url(VideoServiceRequest.from_mapping(data))
                self._send_json({"ok": True, "result": result.to_dict()})
                return
            if parsed.path == "/download/video":
                result = download_video_url(MediaDownloadServiceRequest.from_mapping(data))
                self._send_json({"ok": True, "result": result.to_dict()})
                return
            if parsed.path == "/sessions/download":
                session = STORE.create("download")
                request = MediaDownloadServiceRequest.from_mapping(data)
                _start_session(session, lambda emit: download_video_url(request, progress=emit))
                self._send_json(session.to_dict(), status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/sessions/video":
                session = STORE.create("video")
                request = VideoServiceRequest.from_mapping(data)
                _start_session(session, lambda emit: transcribe_video_url(request, progress=emit))
                self._send_json(session.to_dict(), status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/sessions/speaker":
                session = STORE.create("speaker")
                request = SpeakerServiceRequest.from_mapping(data)
                _start_session(session, lambda emit: transcribe_speaker(request, progress=emit, stop_event=session.stop_event))
                self._send_json(session.to_dict(), status=HTTPStatus.ACCEPTED)
                return
            if parsed.path.startswith("/sessions/") and parsed.path.endswith("/stop"):
                session_id = parsed.path.removeprefix("/sessions/").removesuffix("/stop").strip("/")
                session = STORE.get(session_id)
                if session is None:
                    self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                session.stop_event.set()
                self._send_json(session.to_dict())
                return
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, exc: Exception) -> None:
        self._send_json(
            {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


def _start_session(session: ServiceSession, work: Any) -> None:
    def run() -> None:
        session.status = "running"
        session.started_at = time.time()

        def emit(message: str) -> None:
            session.logs.append(message)

        try:
            result = work(emit)
            session.result = result.to_dict()
            session.status = "completed"
        except Exception as exc:
            session.error = str(exc)
            session.status = "failed"
        finally:
            session.finished_at = time.time()

    thread = threading.Thread(target=run, name=f"service-session-{session.id}", daemon=True)
    session.thread = thread
    thread.start()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), VoiceToTextHandler)
    print(f"Voice-to-text service listening on http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local voice-to-text HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
