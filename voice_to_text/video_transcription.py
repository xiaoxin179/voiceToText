from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cuda_runtime import add_cuda_dll_directories
from .text_converter import TextConverter, TextMode

ProgressCallback = Callable[[str], None]

DASHSCOPE_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class VideoTranscriptionOptions:
    url: str
    model_size: str = "medium"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = "zh"
    text_mode: TextMode = "simplified"
    output_dir: Path = Path("transcripts")
    cookie_browser: str = ""
    optimize_with_deepseek: bool = False
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"


@dataclass(frozen=True)
class VideoTranscriptionResult:
    raw_text: str
    timestamped_text: str
    optimized_text: str | None
    audio_path: Path
    raw_transcript_path: Path
    timestamped_transcript_path: Path
    optimized_transcript_path: Path | None


def transcribe_platform_video(
    options: VideoTranscriptionOptions,
    progress: ProgressCallback | None = None,
) -> VideoTranscriptionResult:
    progress = progress or (lambda _message: None)
    work_dir = _create_work_dir(options.output_dir)

    progress("准备下载平台视频音频")
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = _download_audio(
            options.url,
            Path(tmpdir),
            cookie_browser=options.cookie_browser,
            progress=progress,
        )
        saved_audio = work_dir / audio_path.name
        saved_audio.write_bytes(audio_path.read_bytes())

    progress(f"音频已保存: {saved_audio}")
    raw_text, timestamped_text = _transcribe_audio(saved_audio, options, progress)

    raw_path = work_dir / "transcript_raw.md"
    timestamped_path = work_dir / "transcript_timestamped.md"
    raw_path.write_text(raw_text, encoding="utf-8")
    timestamped_path.write_text(timestamped_text, encoding="utf-8")
    progress(f"原始文字稿已保存: {raw_path}")

    optimized_text: str | None = None
    optimized_path: Path | None = None
    if options.optimize_with_deepseek:
        progress("正在调用 DeepSeek 优化文字稿")
        optimized_text = optimize_transcript_with_deepseek(
            raw_text,
            api_key=options.deepseek_api_key,
            model=options.deepseek_model,
            progress=progress,
        )
        optimized_path = work_dir / "transcript_deepseek.md"
        optimized_path.write_text(optimized_text, encoding="utf-8")
        progress(f"DeepSeek 优化稿已保存: {optimized_path}")

    progress("平台视频转写完成")
    return VideoTranscriptionResult(
        raw_text=raw_text,
        timestamped_text=timestamped_text,
        optimized_text=optimized_text,
        audio_path=saved_audio,
        raw_transcript_path=raw_path,
        timestamped_transcript_path=timestamped_path,
        optimized_transcript_path=optimized_path,
    )


def _create_work_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = output_dir / f"video-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"video-{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _download_audio(
    url: str,
    tmpdir: Path,
    *,
    cookie_browser: str = "",
    progress: ProgressCallback,
) -> Path:
    output_template = tmpdir / "audio.%(ext)s"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "-o",
        str(output_template),
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    if cookie_browser:
        cmd[3:3] = ["--cookies-from-browser", cookie_browser]
        progress(f"使用 {cookie_browser} 浏览器 Cookie 下载")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=_subprocess_creation_flags(),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        if _bilibili_bvid(url):
            progress("yt-dlp 下载失败，尝试使用 Bilibili 公开接口下载第 1 P 音频")
            try:
                return _download_bilibili_first_page_audio(url, tmpdir, progress=progress)
            except Exception as fallback_exc:
                raise RuntimeError(f"视频音频下载失败: {stderr}\nBilibili fallback 也失败: {fallback_exc}") from fallback_exc
        if "No module named yt_dlp" in stderr:
            raise RuntimeError("缺少 yt-dlp。请先运行: pip install yt-dlp")
        if "ffprobe and ffmpeg not found" in stderr or "ffmpeg" in stderr.lower():
            raise RuntimeError("音频转换需要 ffmpeg。请先安装 ffmpeg 并确认它在 PATH 中。")
        raise RuntimeError(f"视频音频下载失败: {stderr}")

    for extension in (".mp3", ".m4a", ".webm", ".opus", ".ogg", ".wav"):
        candidate = tmpdir / f"audio{extension}"
        if candidate.exists():
            size_mb = candidate.stat().st_size / 1024 / 1024
            progress(f"音频下载完成: {size_mb:.1f} MB")
            return candidate

    raise RuntimeError("yt-dlp 已结束，但没有找到下载后的音频文件。")


def _bilibili_bvid(url: str) -> str:
    match = re.search(r"BV[0-9A-Za-z]+", url)
    return match.group(0) if match else ""


def _download_bilibili_first_page_audio(
    url: str,
    tmpdir: Path,
    *,
    progress: ProgressCallback,
) -> Path:
    bvid = _bilibili_bvid(url)
    if not bvid:
        raise RuntimeError("没有从链接中识别出 Bilibili BV 号。")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    }
    view = _read_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers)
    if view.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: {view.get('message') or view}")

    data = view["data"]
    pages = data.get("pages") or []
    if not pages:
        raise RuntimeError("Bilibili API 没有返回分 P 信息。")
    first_page = pages[0]
    progress(
        f"Bilibili fallback: {data.get('title', bvid)} | "
        f"第 1/{len(pages)} P: {first_page.get('part', '')}"
    )

    play_url = (
        "https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={first_page['cid']}&fnval=16&fourk=1"
    )
    play = _read_json(play_url, headers)
    if play.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {play.get('message') or play}")

    dash = (play.get("data") or {}).get("dash") or {}
    audio_items = dash.get("audio") or []
    if not audio_items:
        raise RuntimeError("Bilibili API 没有返回音频流。")
    best_audio = max(audio_items, key=lambda item: int(item.get("bandwidth") or 0))
    audio_url = best_audio.get("baseUrl") or best_audio.get("base_url")
    if not audio_url:
        raise RuntimeError("音频流缺少下载地址。")

    mp3_audio = tmpdir / "audio.mp3"
    _download_bilibili_audio_with_ffmpeg(audio_url, mp3_audio, headers, progress=progress)
    progress("Bilibili fallback 音频转换完成")
    return mp3_audio


def _read_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_bilibili_audio_with_ffmpeg(
    url: str,
    output_path: Path,
    headers: dict[str, str],
    *,
    progress: ProgressCallback,
) -> None:
    header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    result = subprocess.run(
        [
            "ffmpeg",
            "-headers",
            header_text,
            "-i",
            url,
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "5",
            str(output_path),
            "-y",
            "-loglevel",
            "error",
        ],
        capture_output=True,
        text=True,
        creationflags=_subprocess_creation_flags(),
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"ffmpeg 下载/转换 Bilibili 音频失败: {result.stderr.strip()}")
    size_mb = output_path.stat().st_size / 1024 / 1024
    progress(f"Bilibili fallback 音频下载完成: {size_mb:.1f} MB")


def _convert_audio_to_mp3(source: Path, target: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-i", str(source), "-codec:a", "libmp3lame", "-q:a", "5", str(target), "-y", "-loglevel", "error"],
        capture_output=True,
        text=True,
        creationflags=_subprocess_creation_flags(),
    )
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(f"ffmpeg 音频转换失败: {result.stderr.strip()}")


def _transcribe_audio(
    audio_path: Path,
    options: VideoTranscriptionOptions,
    progress: ProgressCallback,
) -> tuple[str, str]:
    if options.device == "cuda":
        add_cuda_dll_directories()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("缺少 faster-whisper。请先运行: pip install faster-whisper") from exc

    progress(
        f"加载本地 faster-whisper 模型: model={options.model_size}, "
        f"device={options.device}, compute={options.compute_type}"
    )
    model = WhisperModel(
        options.model_size,
        device=options.device,
        compute_type=options.compute_type,
        local_files_only=True,
    )

    kwargs: dict = {
        "vad_filter": True,
        "beam_size": 1,
        "condition_on_previous_text": False,
    }
    if options.language:
        kwargs["language"] = options.language

    progress("开始本地识别音频")
    segments, info = model.transcribe(str(audio_path), **kwargs)
    converter = TextConverter(options.text_mode)

    raw_lines: list[str] = []
    timestamped_lines: list[str] = []
    for segment in segments:
        text = converter.convert(segment.text.strip())
        if not text:
            continue
        raw_lines.append(text)
        timestamped_lines.append(f"[{_format_seconds(segment.start)} -> {_format_seconds(segment.end)}] {text}")

    if not raw_lines:
        raise RuntimeError("本地 Whisper 没有识别出文字。请确认视频有可听见的人声。")

    progress(f"识别完成，检测语言: {getattr(info, 'language', 'unknown')}")
    return "\n".join(raw_lines), "\n".join(timestamped_lines)


def _format_seconds(value: float) -> str:
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def optimize_transcript_with_deepseek(
    transcript: str,
    *,
    api_key: str = "",
    model: str = "deepseek-v4-flash",
    progress: ProgressCallback | None = None,
) -> str:
    api_key, base_url = _resolve_deepseek_provider(api_key)
    if not api_key:
        raise RuntimeError(
            "未配置 DeepSeek API Key。请填写界面里的 Key，或设置 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY 环境变量。"
        )

    chunks = _split_text(transcript, max_chars=12000)
    optimized_chunks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if progress:
            progress(f"DeepSeek 正在优化第 {index}/{len(chunks)} 段")
        optimized_chunks.append(_call_deepseek(chunk, api_key=api_key, model=model, base_url=base_url))
    return "\n\n".join(optimized_chunks).strip()


def _resolve_deepseek_provider(api_key: str) -> tuple[str, str]:
    explicit_key = api_key.strip()
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    base_url = (
        os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        or os.environ.get("DASHSCOPE_BASE_URL", "").strip()
    )
    if explicit_key:
        return explicit_key, (base_url or DASHSCOPE_COMPATIBLE_BASE_URL).rstrip("/")
    if dashscope_key:
        return dashscope_key, (base_url or DASHSCOPE_COMPATIBLE_BASE_URL).rstrip("/")
    return deepseek_key, (base_url or DEEPSEEK_OFFICIAL_BASE_URL).rstrip("/")


def _split_text(text: str, max_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        if current and current_len + paragraph_len + 1 > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if paragraph_len > max_chars:
            for start in range(0, paragraph_len, max_chars):
                chunks.append(paragraph[start : start + max_chars])
            continue
        current.append(paragraph)
        current_len += paragraph_len + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


def _call_deepseek(transcript_chunk: str, *, api_key: str, model: str, base_url: str) -> str:
    endpoint = f"{base_url}/chat/completions"
    payload = {
        "model": model or "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个中文视频文字稿校对 agent。你的任务是修正语音识别错误、补全标点、"
                    "优化分段和可读性。必须保持原意，不要扩写事实，不要加入原文没有的信息。"
                    "如果某句话无法确定，请保守保留原词。只输出优化后的文字稿。"
                ),
            },
            {
                "role": "user",
                "content": f"请优化下面这段 ASR 文字稿：\n\n{transcript_chunk}",
            },
        ],
        "temperature": 0.1,
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API 请求失败: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek API 连接失败: {exc}") from exc

    data = json.loads(body)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"DeepSeek API 返回格式异常: {body[:500]}") from exc


def _subprocess_creation_flags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0
