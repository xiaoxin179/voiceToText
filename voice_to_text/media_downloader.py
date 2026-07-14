from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class MediaDownloadOptions:
    """Options for a link-only media download; no transcription is involved."""

    url: str
    output_dir: Path = Path("downloads")
    cookie_browser: str = ""
    cookies_file: Path | None = None
    referer: str = ""
    headers: tuple[str, ...] = ()
    format_selector: str = "bv*+ba/b"


@dataclass(frozen=True)
class MediaDownloadResult:
    media_path: Path
    output_dir: Path
    title: str = ""
    media_id: str = ""
    extractor: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "media_path": str(self.media_path),
            "output_dir": str(self.output_dir),
            "title": self.title,
            "media_id": self.media_id,
            "extractor": self.extractor,
            "source_url": self.source_url,
        }


def download_media(options: MediaDownloadOptions, progress: ProgressCallback | None = None) -> MediaDownloadResult:
    """Download the best available video for a URL through yt-dlp.

    The caller may supply exported cookies or request headers captured from a
    browser. This avoids reading Chromium's encrypted cookie database.
    """

    progress = progress or (lambda _message: None)
    source_url = options.url.strip()
    if not source_url:
        raise ValueError("url must not be empty")
    if options.cookie_browser and options.cookies_file:
        raise ValueError("cookie_browser and cookies_file cannot be used together")
    if options.cookies_file and not options.cookies_file.is_file():
        raise ValueError(f"cookies_file does not exist: {options.cookies_file}")

    output_dir = _create_output_dir(options.output_dir)
    output_template = output_dir / "%(title).160B [%(id)s].%(ext)s"
    metadata_path = output_dir / "metadata.json"
    command = _yt_dlp_command_prefix()
    command.extend(
        [
            "--no-playlist",
            "--windows-filenames",
            "--merge-output-format",
            "mp4",
            "-f",
            options.format_selector,
            "-o",
            str(output_template),
            "--write-info-json",
            "--no-warnings",
            "--print",
            "after_move:filepath",
        ]
    )
    if options.cookie_browser:
        command.extend(["--cookies-from-browser", options.cookie_browser])
        progress(f"使用 {options.cookie_browser} 浏览器 Cookie 下载")
    if options.cookies_file:
        command.extend(["--cookies", str(options.cookies_file)])
        progress("使用导出的 cookies.txt 下载")
    if options.referer.strip():
        command.extend(["--referer", options.referer.strip()])
    for header in options.headers:
        header = header.strip()
        if header:
            command.extend(["--add-header", header])

    command.append(source_url)
    progress("开始下载媒体文件")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_subprocess_creation_flags(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(_format_download_error(detail))

    media_path = _find_downloaded_media(result.stdout, output_dir)
    if media_path is None:
        raise RuntimeError("下载命令已完成，但没有找到媒体文件。")
    metadata = _read_metadata(media_path, metadata_path)
    size_mb = media_path.stat().st_size / 1024 / 1024
    progress(f"下载完成: {media_path.name} ({size_mb:.1f} MB)")
    return MediaDownloadResult(
        media_path=media_path,
        output_dir=output_dir,
        title=str(metadata.get("title") or ""),
        media_id=str(metadata.get("id") or ""),
        extractor=str(metadata.get("extractor_key") or metadata.get("extractor") or ""),
        source_url=str(metadata.get("webpage_url") or source_url),
    )


def _create_output_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = base_dir / f"media-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = base_dir / f"media-{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _yt_dlp_command_prefix() -> list[str]:
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if executable:
        return [executable]
    raise RuntimeError("缺少 yt-dlp。请先运行: pip install -r requirements.txt")


def _find_downloaded_media(stdout: str, output_dir: Path) -> Path | None:
    for line in reversed(stdout.splitlines()):
        path = Path(line.strip())
        if path.is_file() and path.suffix.lower() not in {".json", ".part", ".ytdl"}:
            return path
    files = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() not in {".json", ".part", ".ytdl"}
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _read_metadata(media_path: Path, fallback_path: Path) -> dict[str, object]:
    candidates = [media_path.with_suffix(media_path.suffix + ".info.json"), fallback_path]
    candidates.extend(media_path.parent.glob("*.info.json"))
    for candidate in candidates:
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _format_download_error(detail: str) -> str:
    lowered = detail.lower()
    if "failed to decrypt with dpapi" in lowered or "could not copy chrome cookie database" in lowered:
        return (
            "视频下载失败: Chrome/Edge 的 Cookie 无法被读取或解密。"
            "请改用 Firefox，或从浏览器导出 Netscape 格式 cookies.txt 后通过 --cookies-file 传入。"
            f"\n原始错误: {detail}"
        )
    return f"视频下载失败: {detail}"


def _subprocess_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
