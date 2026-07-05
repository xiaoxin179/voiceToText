from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
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
class DeepSeekTokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def has_values(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens or self.total_tokens)

    def __add__(self, other: "DeepSeekTokenUsage") -> "DeepSeekTokenUsage":
        return DeepSeekTokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    def to_display_text(self) -> str:
        if not self.has_values:
            return "Token 用量未返回"

        parts: list[str] = []
        if self.prompt_tokens:
            parts.append(f"输入 {self.prompt_tokens:,}")
        if self.completion_tokens:
            parts.append(f"输出 {self.completion_tokens:,}")

        total_text = f"总计 {self.total_tokens:,} tokens" if self.total_tokens else "Token 用量已返回"
        return f"{total_text}（{' / '.join(parts)}）" if parts else total_text


@dataclass(frozen=True)
class DeepSeekOptimizationResult:
    text: str
    usage: DeepSeekTokenUsage


@dataclass(frozen=True)
class VideoTranscriptionResult:
    raw_text: str
    timestamped_text: str
    optimized_text: str | None
    audio_path: Path
    raw_transcript_path: Path
    timestamped_transcript_path: Path
    optimized_transcript_path: Path | None
    optimized_token_usage: DeepSeekTokenUsage | None = None


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
    optimized_usage: DeepSeekTokenUsage | None = None
    if options.optimize_with_deepseek:
        progress("正在调用 DeepSeek 优化文字稿")
        optimization = optimize_transcript_with_deepseek(
            raw_text,
            api_key=options.deepseek_api_key,
            model=options.deepseek_model,
            progress=progress,
        )
        optimized_text = optimization.text
        optimized_usage = optimization.usage
        optimized_path = work_dir / "transcript_deepseek.md"
        optimized_path.write_text(optimized_text, encoding="utf-8")
        progress(f"DeepSeek 优化稿已保存: {optimized_path}")
        progress(f"DeepSeek Token 用量: {optimized_usage.to_display_text()}")

    progress("平台视频转写完成")
    return VideoTranscriptionResult(
        raw_text=raw_text,
        timestamped_text=timestamped_text,
        optimized_text=optimized_text,
        audio_path=saved_audio,
        raw_transcript_path=raw_path,
        timestamped_transcript_path=timestamped_path,
        optimized_transcript_path=optimized_path,
        optimized_token_usage=optimized_usage,
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
    url = _normalize_video_url(url, progress=progress)
    if _is_douyin_media_url(url):
        progress("检测到 Douyin 媒体流链接，直接下载音频流")
        return _download_direct_media_audio(url, tmpdir, progress=progress)

    output_template = tmpdir / "audio.%(ext)s"
    cmd = _yt_dlp_command_prefix()
    if cookie_browser:
        cmd.extend(["--cookies-from-browser", cookie_browser])
        progress(f"使用 {cookie_browser} 浏览器 Cookie 下载")

    cmd.extend(
        [
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
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=_subprocess_creation_flags(),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        if _bilibili_bvid(url):
            page_number = _bilibili_page_number(url)
            progress(f"yt-dlp 下载失败，尝试使用 Bilibili 公开接口下载第 {page_number} P 音频")
            try:
                return _download_bilibili_page_audio(url, tmpdir, progress=progress)
            except Exception as fallback_exc:
                raise RuntimeError(f"视频音频下载失败: {stderr}\nBilibili fallback 也失败: {fallback_exc}") from fallback_exc
        if "No module named yt_dlp" in stderr:
            raise RuntimeError("缺少 yt-dlp。请先运行: pip install yt-dlp，或把 yt-dlp.exe 放到 PATH 中。")
        if "ffprobe and ffmpeg not found" in stderr or "ffmpeg" in stderr.lower():
            raise RuntimeError("音频转换需要 ffmpeg。请先安装 ffmpeg 并确认它在 PATH 中。")
        raise RuntimeError(_format_download_error(url, stderr))

    for extension in (".mp3", ".m4a", ".webm", ".opus", ".ogg", ".wav"):
        candidate = tmpdir / f"audio{extension}"
        if candidate.exists():
            size_mb = candidate.stat().st_size / 1024 / 1024
            progress(f"音频下载完成: {size_mb:.1f} MB")
            return candidate

    raise RuntimeError("yt-dlp 已结束，但没有找到下载后的音频文件。")


def _yt_dlp_command_prefix() -> list[str]:
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]

    executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if executable:
        return [executable]

    return [sys.executable, "-m", "yt_dlp"]


def _normalize_video_url(url: str, *, progress: ProgressCallback) -> str:
    original = url
    url = _extract_first_url(url)
    if url != original:
        progress(f"已从分享文案中提取链接: {url}")

    normalized = _normalize_douyin_url(url)
    if normalized != url:
        progress(f"Douyin 链接已规范化为视频页: {normalized}")
    return normalized


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s，。！？、]+", text)
    if not match:
        return text.strip()
    return match.group(0).rstrip(".,;:!?，。；：！？）)]】")


def _normalize_douyin_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "douyin.com" not in host and "iesdouyin.com" not in host:
        return url

    if host == "v.douyin.com":
        url = _resolve_douyin_short_url(url)
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if "douyin.com" not in host and "iesdouyin.com" not in host:
            return url

    video_id = _douyin_video_id(parsed)
    if not video_id:
        return url

    return f"https://www.douyin.com/video/{video_id}"


def _resolve_douyin_short_url(url: str) -> str:
    request = urllib.request.Request(url, headers=_browser_headers())
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.geturl()
    except (urllib.error.URLError, TimeoutError):
        return url


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }


def _douyin_video_id(parsed: urllib.parse.ParseResult) -> str:
    path_match = re.search(r"/(?:share/)?video/(\d+)", parsed.path)
    if path_match:
        return path_match.group(1)

    query = urllib.parse.parse_qs(parsed.query)
    for key in ("modal_id", "vid", "aweme_id", "item_id"):
        for value in query.get(key, []):
            value_match = re.search(r"\d{8,}", value)
            if value_match:
                return value_match.group(0)
    return ""


def _format_download_error(url: str, stderr: str) -> str:
    if _is_douyin_url(url):
        if "Fresh cookies" in stderr:
            return (
                "视频音频下载失败: 抖音要求使用新鲜浏览器 Cookie。"
                "请在 Cookie 下拉框选择已登录抖音的 Edge/Chrome/Firefox 后重试；"
                "如果提示无法复制浏览器 Cookie 数据库，请关闭对应浏览器窗口后再试，或换一个浏览器。"
                f"\n原始错误: {stderr}"
            )
        if "Unsupported URL" in stderr:
            return (
                "视频音频下载失败: 当前抖音链接不是 yt-dlp 可直接识别的视频页，"
                "请使用包含 /video/ 的抖音链接，或包含 modal_id/vid 的分享链接。"
                f"\n原始错误: {stderr}"
            )

    if "Could not copy" in stderr and "cookie database" in stderr.lower():
        return (
            "视频音频下载失败: 无法复制浏览器 Cookie 数据库。"
            "这通常是 Chrome/Edge 正在运行并锁住 Network\\Cookies 文件。"
            "请关闭对应浏览器窗口后重试，或改选 Edge/Firefox；"
            "如果浏览器页面已能播放该抖音视频，也可以在开发者工具 Network 中复制 media-audio/douyinvod 音频流链接后粘贴到这里。"
            f"\n原始错误: {stderr}"
        )

    if "Failed to decrypt with DPAPI" in stderr:
        return (
            "视频音频下载失败: 浏览器 Cookie 数据库可以读取，但 yt-dlp 无法解密其中的 Cookie。"
            "这通常是新版 Chrome/Edge 在 Windows 上启用了 v20/App-Bound Cookie 加密导致的；"
            "关闭浏览器只能解除文件锁，不能绕过这种加密。"
            "请改用 Firefox Cookie、浏览器扩展导出的 Netscape cookies.txt，"
            "或在已登录的浏览器开发者工具 Network 中复制 media-audio/douyinvod 音频流链接后粘贴到这里。"
            f"\n原始错误: {stderr}"
        )

    return f"视频音频下载失败: {stderr}"


def _is_douyin_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "douyin.com" in host or "iesdouyin.com" in host


def _is_douyin_media_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (
        "douyinvod.com" in host
        or "bytecdn" in host and "media-audio" in path
        or "media-audio" in path and urllib.parse.parse_qs(parsed.query).get("mime_type") == ["video_mp4"]
    )


def _download_direct_media_audio(
    url: str,
    tmpdir: Path,
    *,
    progress: ProgressCallback,
) -> Path:
    output_path = tmpdir / "audio.mp3"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
    }
    _download_media_with_ffmpeg(url, output_path, headers, progress=progress)
    progress("Douyin 媒体流音频转换完成")
    return output_path


def _bilibili_bvid(url: str) -> str:
    match = re.search(r"BV[0-9A-Za-z]+", url)
    return match.group(0) if match else ""


def _bilibili_page_number(url: str, page_count: int | None = None) -> int:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    raw_page = (query.get("p") or query.get("page") or ["1"])[0]
    try:
        page_number = int(raw_page)
    except (TypeError, ValueError):
        page_number = 1

    page_number = max(1, page_number)
    if page_count is not None and page_number > page_count:
        raise RuntimeError(f"Bilibili 链接指定第 {page_number} P，但该视频只有 {page_count} P。")
    return page_number


def _download_bilibili_page_audio(
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
    page_number = _bilibili_page_number(url, page_count=len(pages))
    selected_page = pages[page_number - 1]
    progress(
        f"Bilibili fallback: {data.get('title', bvid)} | "
        f"第 {page_number}/{len(pages)} P: {selected_page.get('part', '')}"
    )

    play_url = (
        "https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={selected_page['cid']}&fnval=16&fourk=1"
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
    _download_media_with_ffmpeg(audio_url, mp3_audio, headers, progress=progress)
    progress("Bilibili fallback 音频转换完成")
    return mp3_audio


def _read_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_media_with_ffmpeg(
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
        raise RuntimeError(f"ffmpeg 下载/转换音频失败: {result.stderr.strip()}")
    size_mb = output_path.stat().st_size / 1024 / 1024
    progress(f"音频下载完成: {size_mb:.1f} MB")


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
) -> DeepSeekOptimizationResult:
    api_key, base_url = _resolve_deepseek_provider(api_key)
    if not api_key:
        raise RuntimeError(
            "未配置 DeepSeek API Key。请填写界面里的 Key，或设置 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY 环境变量。"
        )

    chunks = _split_text(transcript, max_chars=12000)
    optimized_chunks: list[str] = []
    total_usage = DeepSeekTokenUsage()
    for index, chunk in enumerate(chunks, start=1):
        if progress:
            progress(f"DeepSeek 正在优化第 {index}/{len(chunks)} 段")
        optimized_chunk, usage = _call_deepseek(chunk, api_key=api_key, model=model, base_url=base_url)
        optimized_chunks.append(optimized_chunk)
        total_usage = total_usage + usage
    return DeepSeekOptimizationResult("\n\n".join(optimized_chunks).strip(), total_usage)


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


def _call_deepseek(
    transcript_chunk: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> tuple[str, DeepSeekTokenUsage]:
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
        optimized_text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"DeepSeek API 返回格式异常: {body[:500]}") from exc
    return optimized_text, _parse_token_usage(data.get("usage"))


def _parse_token_usage(usage: object) -> DeepSeekTokenUsage:
    if not isinstance(usage, dict):
        return DeepSeekTokenUsage()

    prompt_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_int(usage, "total_tokens")
    if not total_tokens and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens

    return DeepSeekTokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _usage_int(usage: dict, *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _subprocess_creation_flags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0
