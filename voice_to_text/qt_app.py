from __future__ import annotations

import queue
import threading
import time
import os
import ctypes
from dataclasses import dataclass, replace
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .asr import AsrWorker, Transcript
from .audio_capture import AudioCapture, AudioChunk
from .audio_devices import (
    AudioDevice,
    get_default_microphone,
    get_default_system_loopback,
    list_devices,
    list_loopback_devices,
)
from .model_cache import required_models_error_message
from .media_downloader import MediaDownloadOptions, MediaDownloadResult, download_media
from .video_transcription import (
    DeepSeekOptimizationResult,
    VideoTranscriptionOptions,
    VideoTranscriptionResult,
    optimize_transcript_with_deepseek,
    transcribe_platform_video,
)

APP_TITLE = "实时语音转文字"
APP_USER_MODEL_ID = "xiaoxin.voiceToText.desktop"
SWISS_ACCENT = "#002FA7"
SWISS_BLACK = "#111111"
SWISS_MUTED = "#6B7280"
SWISS_RULE = "#D8DDE6"


def configure_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def create_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SWISS_ACCENT))
        radius = max(2.0, size * 0.08)
        inset = max(1.0, size * 0.04)
        painter.drawRoundedRect(QRectF(inset, inset, size - inset * 2, size - inset * 2), radius, radius)

        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(max(1.2, size * 0.075))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        capsule = QRectF(size * 0.36, size * 0.18, size * 0.28, size * 0.42)
        painter.drawRoundedRect(capsule, size * 0.12, size * 0.12)
        painter.drawLine(int(size * 0.28), int(size * 0.44), int(size * 0.28), int(size * 0.53))
        painter.drawLine(int(size * 0.72), int(size * 0.44), int(size * 0.72), int(size * 0.53))
        painter.drawLine(int(size * 0.5), int(size * 0.64), int(size * 0.5), int(size * 0.78))
        painter.drawLine(int(size * 0.36), int(size * 0.8), int(size * 0.64), int(size * 0.8))

        painter.end()
        icon.addPixmap(pixmap)
    return icon


def create_line_icon(kind: str, color: str) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    icon_color = QColor(color)
    pen = QPen(icon_color)
    pen.setWidthF(1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "play":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_color)
        painter.drawPolygon(QPolygonF([QPointF(9, 7), QPointF(9, 17), QPointF(17, 12)]))
    elif kind == "stop":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_color)
        painter.drawRect(QRectF(8, 8, 8, 8))
    elif kind == "clear":
        painter.drawLine(QPointF(8, 8), QPointF(16, 16))
        painter.drawLine(QPointF(16, 8), QPointF(8, 16))
    elif kind == "refresh":
        painter.drawArc(QRectF(6, 6, 12, 12), 35 * 16, 290 * 16)
        painter.drawLine(QPointF(17.5, 7.0), QPointF(18.2, 11.2))
        painter.drawLine(QPointF(17.5, 7.0), QPointF(13.4, 7.2))
    elif kind == "mic":
        painter.drawRoundedRect(QRectF(9, 4.5, 6, 10), 3, 3)
        painter.drawLine(QPointF(6.5, 11.0), QPointF(6.5, 13.2))
        painter.drawLine(QPointF(17.5, 11.0), QPointF(17.5, 13.2))
        painter.drawLine(QPointF(12, 15.5), QPointF(12, 19))
        painter.drawLine(QPointF(9, 19.2), QPointF(15, 19.2))
    elif kind == "video":
        painter.drawRect(QRectF(5, 6.5, 14, 11))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_color)
        painter.drawPolygon(QPolygonF([QPointF(10.3, 9.2), QPointF(10.3, 14.8), QPointF(15, 12)]))
    elif kind == "settings":
        painter.drawEllipse(QRectF(8.5, 8.5, 7, 7))
        for start, end in (
            (QPointF(12, 3.8), QPointF(12, 6.2)),
            (QPointF(12, 17.8), QPointF(12, 20.2)),
            (QPointF(3.8, 12), QPointF(6.2, 12)),
            (QPointF(17.8, 12), QPointF(20.2, 12)),
            (QPointF(6.2, 6.2), QPointF(7.9, 7.9)),
            (QPointF(16.1, 16.1), QPointF(17.8, 17.8)),
            (QPointF(17.8, 6.2), QPointF(16.1, 7.9)),
            (QPointF(7.9, 16.1), QPointF(6.2, 17.8)),
        ):
            painter.drawLine(start, end)
    painter.end()
    return QIcon(pixmap)


@dataclass
class RuntimeState:
    audio_queue: queue.Queue[AudioChunk]
    transcript_queue: queue.Queue[Transcript]
    debug_queue: queue.Queue[object]
    asr_worker: AsrWorker
    capture_threads: list[AudioCapture]
    capture_started: bool = False
    last_loading_notice_at: float = 0.0


@dataclass
class VideoRuntimeState:
    progress_queue: queue.Queue[str]
    result_queue: queue.Queue[VideoTranscriptionResult | Exception]
    thread: threading.Thread
    started_at: float
    cancel_requested: bool = False


@dataclass
class VideoAgentRuntimeState:
    progress_queue: queue.Queue[str]
    result_queue: queue.Queue[tuple[DeepSeekOptimizationResult, Path] | Exception]
    thread: threading.Thread
    started_at: float


@dataclass
class MediaDownloadRuntimeState:
    progress_queue: queue.Queue[str]
    result_queue: queue.Queue[MediaDownloadResult | Exception]
    thread: threading.Thread
    started_at: float


class VoiceToTextWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.app_icon = create_app_icon()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(self.app_icon)
        self.resize(1440, 920)
        self.setMinimumSize(1180, 760)

        self.mic_devices: list[AudioDevice] = []
        self.system_devices: list[AudioDevice] = []
        self.runtime: RuntimeState | None = None
        self.video_runtime: VideoRuntimeState | None = None
        self.video_agent_runtime: VideoAgentRuntimeState | None = None
        self.media_download_runtime: MediaDownloadRuntimeState | None = None
        self.video_result: VideoTranscriptionResult | None = None
        self.started_at = 0.0

        self._build_ui()
        self._apply_style()
        self._connect_events()
        self.refresh_devices()

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll_runtime)
        self.timer.start()

        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.hide_usage_toast)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 12)
        root_layout.setSpacing(14)
        self.setCentralWidget(root)

        header = QFrame()
        header.setObjectName("headerPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(16)

        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon.setPixmap(self.app_icon.pixmap(40, 40))
        brand_icon.setFixedSize(40, 40)
        header_layout.addWidget(brand_icon)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel(APP_TITLE)
        title.setObjectName("appTitle")
        subtitle = QLabel("麦克风和系统音频分路采集、分路转写，便于排查每一路输入状态。")
        subtitle.setObjectName("appSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header_layout.addLayout(title_block, stretch=1)

        self.status_badge = QLabel("就绪")
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.status_badge)

        self.voice_header_actions = QWidget()
        self.voice_header_actions.setObjectName("headerActions")
        voice_actions_layout = QHBoxLayout(self.voice_header_actions)
        voice_actions_layout.setContentsMargins(0, 0, 0, 0)
        voice_actions_layout.setSpacing(12)

        self.start_button = QPushButton("开始监听")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.clear_button = QPushButton("清空文本")
        self.refresh_button = QPushButton("刷新设备")
        self.start_button.setIcon(create_line_icon("play", "#ffffff"))
        self.stop_button.setIcon(create_line_icon("stop", SWISS_MUTED))
        self.clear_button.setIcon(create_line_icon("clear", SWISS_BLACK))
        self.refresh_button.setIcon(create_line_icon("refresh", SWISS_BLACK))
        for button in (self.start_button, self.stop_button, self.clear_button, self.refresh_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(16, 16))
        self.stop_button.setEnabled(False)
        voice_actions_layout.addWidget(self.start_button)
        voice_actions_layout.addWidget(self.stop_button)
        voice_actions_layout.addWidget(self.clear_button)
        voice_actions_layout.addWidget(self.refresh_button)
        header_layout.addWidget(self.voice_header_actions)

        self.video_header_actions = QWidget()
        self.video_header_actions.setObjectName("headerActions")
        video_actions_layout = QHBoxLayout(self.video_header_actions)
        video_actions_layout.setContentsMargins(0, 0, 0, 0)
        video_actions_layout.setSpacing(12)
        self.video_settings_button = QPushButton("")
        self.video_settings_button.setObjectName("settingsButton")
        self.video_settings_button.setIcon(create_line_icon("settings", SWISS_BLACK))
        self.video_settings_button.setIconSize(QSize(18, 18))
        self.video_settings_button.setFixedSize(46, 40)
        self.video_settings_button.setToolTip("平台视频转写设置")
        self.video_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        video_actions_layout.addWidget(self.video_settings_button)
        header_layout.addWidget(self.video_header_actions)
        self.video_header_actions.hide()
        root_layout.addWidget(header)

        app_body_layout = QHBoxLayout()
        app_body_layout.setSpacing(14)
        root_layout.addLayout(app_body_layout, stretch=1)

        nav = QFrame()
        nav.setObjectName("navPanel")
        nav.setFixedWidth(220)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)

        nav_label = QLabel("工作区")
        nav_label.setObjectName("navLabel")
        nav_layout.addWidget(nav_label)

        self.voice_nav_button = QPushButton("01  语音转写")
        self.video_nav_button = QPushButton("02  平台视频转写")
        self.voice_nav_button.setIcon(create_line_icon("mic", SWISS_BLACK))
        self.video_nav_button.setIcon(create_line_icon("video", SWISS_BLACK))
        for button in (self.voice_nav_button, self.video_nav_button):
            button.setObjectName("navButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(True)
            button.setIconSize(QSize(18, 18))
        self.voice_nav_button.setChecked(True)
        nav_layout.addWidget(self.voice_nav_button)
        nav_layout.addWidget(self.video_nav_button)
        nav_layout.addStretch()

        nav_hint = QLabel("本地 Whisper\nDashScope DeepSeek")
        nav_hint.setObjectName("navHint")
        nav_layout.addWidget(nav_hint)
        app_body_layout.addWidget(nav)

        self.pages = QStackedWidget()
        app_body_layout.addWidget(self.pages, stretch=1)
        self.pages.addWidget(self._build_voice_page())
        self.pages.addWidget(self._build_video_page())

        self.status = QStatusBar(self)
        self.status.setObjectName("bottomStatus")
        self.setStatusBar(self.status)
        self._set_status("就绪", "idle")

    def _build_voice_page(self) -> QWidget:
        page = QWidget()
        body_layout = QHBoxLayout(page)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("sidePanel")
        sidebar.setFixedWidth(400)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(16)
        body_layout.addWidget(sidebar)

        self.listen_mic = QCheckBox("麦克风")
        self.listen_mic.setChecked(True)
        self.listen_system = QCheckBox("系统音频")
        self.listen_system.setChecked(True)
        source_row = QHBoxLayout()
        source_row.addWidget(self.listen_mic)
        source_row.addWidget(self.listen_system)
        source_row.addStretch()

        self.mic_combo = QComboBox()
        self.system_combo = QComboBox()
        self.mic_combo.setMinimumWidth(310)
        self.system_combo.setMinimumWidth(310)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.model_combo.setCurrentText("medium")

        self.device_combo = QComboBox()
        self.device_combo.addItems(["cuda", "cpu"])

        self.compute_combo = QComboBox()
        self.compute_combo.addItems(["float16", "int8_float16", "int8", "float32"])
        self.compute_combo.setCurrentText("float16")

        self.language_combo = QComboBox()
        self.language_combo.addItems(["zh", "en", "auto"])

        self.text_mode_combo = QComboBox()
        self.text_mode_combo.addItem("简体中文", userData="simplified")
        self.text_mode_combo.addItem("原始输出", userData="original")
        self.text_mode_combo.addItem("繁体中文", userData="traditional")

        self.chunk_seconds = QSpinBox()
        self.chunk_seconds.setRange(1, 10)
        self.chunk_seconds.setValue(2)
        self.chunk_seconds.setSuffix(" s")

        self.min_rms = QDoubleSpinBox()
        self.min_rms.setRange(0.0, 0.1)
        self.min_rms.setDecimals(5)
        self.min_rms.setSingleStep(0.001)
        self.min_rms.setValue(0.001)

        sidebar_layout.addWidget(self._section_title("监听来源", "选择要采集的输入通道"))
        source_form = self._make_form()
        source_form.addRow("来源", self._wrap_layout(source_row))
        source_form.addRow("麦克风", self.mic_combo)
        source_form.addRow("系统音频", self.system_combo)
        sidebar_layout.addLayout(source_form)

        sidebar_layout.addWidget(self._divider())
        sidebar_layout.addWidget(self._section_title("识别参数", "模型、GPU 和切片设置"))
        asr_form = self._make_form()
        asr_form.addRow("模型", self.model_combo)
        asr_form.addRow("设备", self.device_combo)
        asr_form.addRow("精度", self.compute_combo)
        asr_form.addRow("语言", self.language_combo)
        asr_form.addRow("文本", self.text_mode_combo)
        asr_form.addRow("切片", self.chunk_seconds)
        asr_form.addRow("静音阈值", self.min_rms)
        sidebar_layout.addLayout(asr_form)
        sidebar_layout.addStretch()

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(8)
        status_layout.addWidget(self._section_title("运行状态", "实时队列和采集线程"))

        status_grid = QHBoxLayout()
        status_grid.setSpacing(10)
        self.elapsed_value = self._make_metric("00:00", "运行时间")
        self.queue_value = self._make_metric("0", "待识别")
        self.threads_value = self._make_metric("-", "采集线程")
        status_grid.addWidget(self.elapsed_value)
        status_grid.addWidget(self.queue_value)
        status_grid.addWidget(self.threads_value)
        status_layout.addLayout(status_grid)
        sidebar_layout.addWidget(status_panel)

        workspace = QSplitter(Qt.Orientation.Vertical)
        workspace.setObjectName("workspace")
        body_layout.addWidget(workspace, stretch=1)

        self.mic_text = self._make_text_box()
        self.system_text = self._make_text_box()
        self.timeline_text = self._make_text_box()
        self.debug_text = self._make_text_box()
        self.mic_text.setPlaceholderText("这里显示麦克风转写结果")
        self.system_text.setPlaceholderText("这里显示系统音频转写结果")
        self.timeline_text.setPlaceholderText("这里按时间顺序合并两路文本")
        self.debug_text.setPlaceholderText("这里显示采集、模型加载和识别调试信息")

        transcript_splitter = QSplitter(Qt.Orientation.Horizontal)
        transcript_splitter.setObjectName("transcriptSplitter")
        transcript_splitter.addWidget(self._text_panel("麦克风转写", "MIC", self.mic_text, "micPanel"))
        transcript_splitter.addWidget(self._text_panel("系统音频转写", "SYSTEM", self.system_text, "systemPanel"))
        transcript_splitter.setSizes([1, 1])

        workspace.addWidget(transcript_splitter)
        workspace.addWidget(self._text_panel("合并时间线", "按识别完成时间汇总", self.timeline_text, "timelinePanel"))
        workspace.addWidget(self._text_panel("调试日志", "采集电平、队列和异常会显示在这里", self.debug_text, "debugPanel"))
        workspace.setSizes([420, 230, 150])

        return page

    def _build_video_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("videoSidePanel")
        sidebar.setFixedWidth(400)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(14)
        layout.addWidget(sidebar)

        self.video_url_input = QLineEdit()
        self.video_url_input.setPlaceholderText("粘贴 Bilibili / YouTube / Douyin 等视频链接")
        self.video_url_input.setMinimumHeight(36)

        self.video_model_combo = QComboBox()
        self.video_model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.video_model_combo.setCurrentText("tiny")

        self.video_device_combo = QComboBox()
        self.video_device_combo.addItems(["cuda", "cpu"])
        self.video_device_combo.setCurrentText("cpu")

        self.video_compute_combo = QComboBox()
        self.video_compute_combo.addItems(["float16", "int8_float16", "int8", "float32"])
        self.video_compute_combo.setCurrentText("int8")

        self.video_language_combo = QComboBox()
        self.video_language_combo.addItems(["zh", "en", "auto"])

        self.video_text_mode_combo = QComboBox()
        self.video_text_mode_combo.addItem("简体中文", userData="simplified")
        self.video_text_mode_combo.addItem("原始输出", userData="original")
        self.video_text_mode_combo.addItem("繁体中文", userData="traditional")

        self.video_cookie_combo = QComboBox()
        self.video_cookie_combo.addItem("不使用浏览器 Cookie", userData="")
        self.video_cookie_combo.addItem("Chrome", userData="chrome")
        self.video_cookie_combo.addItem("Edge", userData="edge")
        self.video_cookie_combo.addItem("Firefox", userData="firefox")

        self.video_deepseek_key_input = QLineEdit()
        self.video_deepseek_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.video_deepseek_key_input.setPlaceholderText("可留空，默认读取 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY")

        self.video_deepseek_model_combo = QComboBox()
        self.video_deepseek_model_combo.addItems(
            ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
        )

        self.video_optimize_button = QPushButton("优化文字稿")
        self.video_optimize_button.setEnabled(False)
        self.video_optimize_button.setIcon(create_line_icon("refresh", SWISS_BLACK))
        self.video_optimize_button.setIconSize(QSize(16, 16))
        self.video_optimize_button.setCursor(Qt.CursorShape.PointingHandCursor)

        self.video_start_button = QPushButton("开始转写")
        self.video_start_button.setObjectName("primaryButton")
        self.video_download_button = QPushButton("下载视频")
        self.video_download_button.setObjectName("primaryButton")
        self.video_stop_button = QPushButton("停止")
        self.video_stop_button.setObjectName("dangerButton")
        self.video_stop_button.setEnabled(False)
        self.video_clear_button = QPushButton("清空结果")
        self.video_start_button.setIcon(create_line_icon("play", "#ffffff"))
        self.video_download_button.setIcon(create_line_icon("video", "#ffffff"))
        self.video_stop_button.setIcon(create_line_icon("stop", SWISS_MUTED))
        self.video_clear_button.setIcon(create_line_icon("clear", SWISS_BLACK))
        for button in (self.video_start_button, self.video_download_button, self.video_stop_button, self.video_clear_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(16, 16))

        sidebar_layout.addWidget(self._section_title("平台视频", "可直接下载视频，或下载音频后本地转写"))
        source_form = self._make_form()
        source_form.addRow("链接", self.video_url_input)
        source_form.addRow("Cookie", self.video_cookie_combo)
        sidebar_layout.addLayout(source_form)

        sidebar_layout.addWidget(self._divider())
        sidebar_layout.addWidget(self._section_title("Agent 优化", "原始文字稿生成后，可再调用 DeepSeek 优化"))
        deepseek_form = self._make_form()
        deepseek_form.addRow("Key", self.video_deepseek_key_input)
        deepseek_form.addRow("模型", self.video_deepseek_model_combo)
        deepseek_form.addRow("操作", self.video_optimize_button)
        sidebar_layout.addLayout(deepseek_form)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addWidget(self.video_download_button)
        button_row.addWidget(self.video_start_button)
        button_row.addWidget(self.video_stop_button)
        button_row.addWidget(self.video_clear_button)
        sidebar_layout.addLayout(button_row)
        sidebar_layout.addStretch()

        workspace = QSplitter(Qt.Orientation.Vertical)
        workspace.setObjectName("videoWorkspace")
        layout.addWidget(workspace, stretch=1)

        self.video_raw_text = self._make_text_box()
        self.video_optimized_text = self._make_text_box()
        self.video_log_text = self._make_text_box()
        self.video_raw_text.setPlaceholderText("这里显示平台视频本地 Whisper 原始文字稿")
        self.video_optimized_text.setPlaceholderText("这里显示 DeepSeek 优化后的文字稿")
        self.video_log_text.setPlaceholderText("这里显示下载、识别和 Agent 优化进度")

        workspace.addWidget(self._text_panel("原始文字稿", "LOCAL WHISPER", self.video_raw_text, "videoRawPanel"))
        workspace.addWidget(self._text_panel("优化文字稿", "DEEPSEEK", self.video_optimized_text, "videoOptimizedPanel"))
        workspace.addWidget(self._text_panel("处理日志", "DOWNLOAD / ASR / AGENT", self.video_log_text, "videoLogPanel"))
        workspace.setSizes([360, 300, 170])
        return page

    def _wrap_layout(self, layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _section_title(self, title: str, subtitle: str) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("sectionSubtitle")
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        return wrapper

    def _divider(self) -> QFrame:
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        return divider

    def _make_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        return form

    def _make_metric(self, value: str, label: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("metric")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(value_label)
        layout.addWidget(caption)
        frame.value_label = value_label  # type: ignore[attr-defined]
        return frame

    def _text_panel(self, title: str, subtitle: str, editor: QPlainTextEdit, object_name: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName(object_name)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("panelTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("panelSubtitle")
        header.addWidget(title_label)
        header.addWidget(subtitle_label)
        header.addStretch()

        layout.addLayout(header)
        layout.addWidget(editor, stretch=1)
        return panel

    def _make_text_box(self) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        box.setFont(QFont("Microsoft YaHei UI", 10))
        return box

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#appRoot {
                background: #F7F7F8;
                color: #111111;
            }
            QLabel {
                color: #111111;
                font-family: "Helvetica Neue", "Microsoft YaHei UI", Arial, sans-serif;
            }
            QFrame#headerPanel, QFrame#navPanel, QFrame#sidePanel, QFrame#videoSidePanel,
            QFrame#micPanel, QFrame#systemPanel, QFrame#timelinePanel, QFrame#debugPanel,
            QFrame#videoRawPanel, QFrame#videoOptimizedPanel, QFrame#videoLogPanel {
                background: #FFFFFF;
                border: 1px solid #D8DDE6;
                border-radius: 10px;
            }
            QFrame#headerPanel {
                background: #FFFFFF;
            }
            QFrame#statusPanel {
                background: #FFFFFF;
                border: 1px solid #D8DDE6;
                border-radius: 10px;
            }
            QLabel#brandIcon {
                background: transparent;
            }
            QLabel#appTitle {
                font-size: 25px;
                font-weight: 700;
                letter-spacing: 0;
            }
            QLabel#appSubtitle, QLabel#sectionSubtitle, QLabel#panelSubtitle, QLabel#metricLabel {
                color: #6B7280;
                font-size: 12px;
            }
            QLabel#navLabel {
                color: #6B7280;
                font-size: 12px;
                font-weight: 700;
                padding: 14px 14px 12px 14px;
                border-bottom: 1px solid #D8DDE6;
            }
            QLabel#navHint {
                color: #6B7280;
                background: #FFFFFF;
                border-top: 1px solid #D8DDE6;
                padding: 12px 14px;
                line-height: 18px;
            }
            QLabel#sectionTitle, QLabel#panelTitle {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#panelSubtitle {
                font-weight: 600;
                padding-top: 2px;
            }
            QLabel#statusBadge {
                min-width: 72px;
                min-height: 36px;
                padding: 0 14px;
                border-radius: 18px;
                font-weight: 700;
            }
            QPlainTextEdit {
                background: #FFFFFF;
                border: 1px solid #D8DDE6;
                border-radius: 8px;
                padding: 12px;
                selection-background-color: #002FA7;
                selection-color: #FFFFFF;
            }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
                min-height: 36px;
                border: 1px solid #D8DDE6;
                border-radius: 8px;
                padding: 4px 11px;
                background: #FFFFFF;
            }
            QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover, QPlainTextEdit:hover {
                border-color: #111111;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #002FA7;
                background: #FFFFFF;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #111111;
                border-radius: 5px;
                background: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                background: #002FA7;
                border-color: #002FA7;
            }
            QPushButton {
                min-height: 36px;
                padding: 0 16px;
                border: 1px solid #111111;
                border-radius: 10px;
                background: #FFFFFF;
                color: #111111;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #F7F7F8;
                border-color: #002FA7;
                color: #002FA7;
            }
            QPushButton:disabled {
                color: #A0A7B2;
                background: #F7F7F8;
                border-color: #D8DDE6;
            }
            QPushButton#primaryButton {
                color: #FFFFFF;
                background: #002FA7;
                border-color: #002FA7;
            }
            QPushButton#primaryButton:hover {
                color: #FFFFFF;
                background: #111111;
                border-color: #111111;
            }
            QPushButton#dangerButton {
                color: #6B7280;
                background: #F7F7F8;
                border-color: #D8DDE6;
            }
            QPushButton#dangerButton:hover {
                color: #111111;
                background: #FFFFFF;
                border-color: #111111;
            }
            QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled {
                color: #A0A7B2;
                background: #F7F7F8;
                border-color: #D8DDE6;
            }
            QPushButton#settingsButton {
                padding: 0;
                min-width: 40px;
                max-width: 46px;
                border-radius: 20px;
            }
            QPushButton#navButton {
                min-height: 52px;
                text-align: left;
                padding-left: 14px;
                padding-right: 14px;
                border-radius: 0;
                font-weight: 700;
                background: #FFFFFF;
                border: 0;
                border-bottom: 1px solid #D8DDE6;
                color: #111111;
            }
            QPushButton#navButton:hover {
                background: #F7F7F8;
                color: #002FA7;
            }
            QPushButton#navButton:checked {
                color: #FFFFFF;
                background: #002FA7;
            }
            QFrame#divider {
                color: #D8DDE6;
                background: #D8DDE6;
                max-height: 1px;
            }
            QFrame#metric {
                background: #FFFFFF;
                border: 1px solid #D8DDE6;
                border-radius: 8px;
            }
            QLabel#metricValue {
                color: #002FA7;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#usageToast {
                background: #111111;
                color: #FFFFFF;
                border: 1px solid #111111;
                border-radius: 12px;
                padding: 12px 16px;
                font-weight: 700;
            }
            QComboBox::drop-down {
                width: 26px;
                border: 0;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 11px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #D8DDE6;
                border-radius: 0;
                min-height: 34px;
            }
            QScrollBar::handle:vertical:hover {
                background: #111111;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 11px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #D8DDE6;
                border-radius: 0;
                min-width: 34px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
            QSplitter::handle {
                background: #F7F7F8;
            }
            QStatusBar#bottomStatus {
                background: #F7F7F8;
                border-top: 1px solid #D8DDE6;
                color: #6B7280;
            }
            """
        )

    def _set_status(self, message: str, tone: str = "idle") -> None:
        self.status.showMessage(message)
        labels = {
            "idle": "就绪",
            "loading": "加载中",
            "running": "监听中",
            "stopped": "已停止",
            "error": "异常",
            "working": "处理中",
        }
        colors = {
            "idle": ("#FFFFFF", "#111111", "#D8DDE6"),
            "loading": ("#F7F7F8", "#002FA7", "#002FA7"),
            "running": ("#002FA7", "#FFFFFF", "#002FA7"),
            "stopped": ("#F7F7F8", "#6B7280", "#D8DDE6"),
            "error": ("#FFFFFF", "#E4002B", "#E4002B"),
            "working": ("#002FA7", "#FFFFFF", "#002FA7"),
        }
        background, foreground, border = colors.get(tone, colors["idle"])
        self.status_badge.setText(labels.get(tone, "就绪"))
        self.status_badge.setStyleSheet(
            f"background: {background}; color: {foreground}; border: 1px solid {border};"
        )

    def _show_usage_toast(self, message: str) -> None:
        if not hasattr(self, "usage_toast"):
            self.usage_toast = QLabel(self)
            self.usage_toast.setObjectName("usageToast")
            self.usage_toast.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.usage_toast.setText(message)
        self.usage_toast.adjustSize()
        self._position_usage_toast()
        self.usage_toast.show()
        self.usage_toast.raise_()
        self.toast_timer.start(6500)

    def _position_usage_toast(self) -> None:
        if not hasattr(self, "usage_toast"):
            return
        margin = 26
        self.usage_toast.move(
            max(margin, self.width() - self.usage_toast.width() - margin),
            margin,
        )

    def hide_usage_toast(self) -> None:
        if hasattr(self, "usage_toast"):
            self.usage_toast.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_usage_toast()

    def _format_elapsed(self, seconds: int) -> str:
        minutes, secs = divmod(max(0, seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _set_metric(self, frame: QFrame, value: str) -> None:
        value_label = getattr(frame, "value_label", None)
        if value_label:
            value_label.setText(value)

    def _update_metrics(self, elapsed: int = 0, queue_size: int = 0, threads: str = "-") -> None:
        self._set_metric(self.elapsed_value, self._format_elapsed(elapsed))
        self._set_metric(self.queue_value, str(queue_size))
        self._set_metric(self.threads_value, threads)

    def _connect_events(self) -> None:
        self.voice_nav_button.clicked.connect(lambda: self.switch_page(0))
        self.video_nav_button.clicked.connect(lambda: self.switch_page(1))
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)
        self.clear_button.clicked.connect(self.clear_transcripts)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.device_combo.currentTextChanged.connect(self._sync_compute_default)
        self.video_device_combo.currentTextChanged.connect(self._sync_video_compute_default)
        self.video_settings_button.clicked.connect(self.show_video_settings)
        self.video_start_button.clicked.connect(self.start_video_transcription)
        self.video_download_button.clicked.connect(self.start_media_download)
        self.video_optimize_button.clicked.connect(self.start_video_agent_optimization)
        self.video_stop_button.clicked.connect(self.stop_video_transcription)
        self.video_clear_button.clicked.connect(self.clear_video_transcripts)

    def show_video_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("平台视频转写设置")
        dialog.setObjectName("settingsDialog")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(self._section_title("识别参数", "复用本地 faster-whisper 模型"))

        form = self._make_form()
        for label, widget in (
            ("模型", self.video_model_combo),
            ("设备", self.video_device_combo),
            ("精度", self.video_compute_combo),
            ("语言", self.video_language_combo),
            ("文本", self.video_text_mode_combo),
        ):
            widget.setParent(dialog)
            form.addRow(label, widget)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("完成")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        dialog.finished.connect(self._restore_video_settings_controls)
        dialog.exec()

    def _restore_video_settings_controls(self) -> None:
        for widget in (
            self.video_model_combo,
            self.video_device_combo,
            self.video_compute_combo,
            self.video_language_combo,
            self.video_text_mode_combo,
        ):
            widget.setParent(self)
        self._set_video_inputs_enabled(self.video_runtime is None and self.video_agent_runtime is None)

    def switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.voice_nav_button.setChecked(index == 0)
        self.video_nav_button.setChecked(index == 1)
        if index == 0:
            self.voice_header_actions.show()
            self.video_header_actions.hide()
            self._set_status("语音转写", "idle" if not self.runtime else "running")
        else:
            self.voice_header_actions.hide()
            self.video_header_actions.show()
            self._set_status("平台视频转写", "idle" if not self.video_runtime else "working")

    def _sync_compute_default(self, device: str) -> None:
        self.compute_combo.setCurrentText("int8" if device == "cpu" else "float16")

    def _sync_video_compute_default(self, device: str) -> None:
        self.video_compute_combo.setCurrentText("int8" if device == "cpu" else "float16")

    def refresh_devices(self) -> None:
        try:
            devices = list_devices()
            self.mic_devices = [device for device in devices if device.max_input_channels > 0 and not device.is_loopback]
            self.system_devices = list_loopback_devices()
            default_mic = get_default_microphone()
            default_system = get_default_system_loopback()
        except Exception as exc:
            QMessageBox.critical(self, "设备错误", str(exc))
            return

        self._fill_device_combo(self.mic_combo, self.mic_devices, default_mic.index)
        self._fill_device_combo(self.system_combo, self.system_devices, default_system.index)
        self.append_debug(
            f"已加载 {len(self.mic_devices)} 个输入设备和 {len(self.system_devices)} 个系统音频设备"
        )
        self._set_status("设备已刷新", "idle")

    def _fill_device_combo(self, combo: QComboBox, devices: list[AudioDevice], default_index: int) -> None:
        combo.clear()
        for row, device in enumerate(devices):
            combo.addItem(f"{device.index}: {device.name}", userData=device)
            if device.index == default_index:
                combo.setCurrentIndex(row)

    def start_listening(self) -> None:
        if self.runtime:
            return
        if not self.listen_mic.isChecked() and not self.listen_system.isChecked():
            QMessageBox.warning(self, "没有监听来源", "请至少选择一个音频来源。")
            return

        audio_queue: queue.Queue[AudioChunk] = queue.Queue(maxsize=6)
        transcript_queue: queue.Queue[Transcript] = queue.Queue()
        debug_queue: queue.Queue[object] = queue.Queue()
        language = self.language_combo.currentText()
        language_value = None if language == "auto" else language

        asr_worker = AsrWorker(
            input_queue=audio_queue,
            output_queue=transcript_queue,
            debug_queue=debug_queue,
            model_size=self.model_combo.currentText(),
            device=self.device_combo.currentText(),
            compute_type=self.compute_combo.currentText(),
            language=language_value,
            min_rms=float(self.min_rms.value()),
            text_mode=self.text_mode_combo.currentData(),
        )

        captures: list[AudioCapture] = []
        chunk_seconds = float(self.chunk_seconds.value())

        if self.listen_mic.isChecked():
            mic = self.mic_combo.currentData()
            if mic:
                captures.append(
                    AudioCapture(
                        source="mic",
                        device=mic,
                        output_queue=audio_queue,
                        debug_queue=debug_queue,
                        chunk_seconds=chunk_seconds,
                    )
                )
        if self.listen_system.isChecked():
            system = self.system_combo.currentData()
            if system:
                captures.append(
                    AudioCapture(
                        source="system",
                        device=system,
                        output_queue=audio_queue,
                        debug_queue=debug_queue,
                        chunk_seconds=chunk_seconds,
                    )
                )

        self.runtime = RuntimeState(audio_queue, transcript_queue, debug_queue, asr_worker, captures)
        self.started_at = time.time()
        self.append_debug(
            f"开始监听: model={self.model_combo.currentText()}, device={self.device_combo.currentText()}, "
            f"compute={self.compute_combo.currentText()}, language={language or 'auto'}, "
            f"text_mode={self.text_mode_combo.currentData()}, chunk={chunk_seconds}s, "
            f"min_rms={self.min_rms.value():.5f}"
        )
        self.append_debug("正在加载模型，模型加载完成后才会开始采集音频")
        asr_worker.start()

        self._set_running_ui(True)
        self._update_metrics(0, 0, "加载")
        self._set_status("正在加载模型。模型就绪后才会开始采集音频。", "loading")

    def stop_listening(self) -> None:
        runtime = self.runtime
        if not runtime:
            return

        for capture in runtime.capture_threads:
            capture.stop()
        runtime.asr_worker.stop()

        def join_threads() -> None:
            for capture_thread in runtime.capture_threads:
                capture_thread.join(timeout=2.0)
            runtime.asr_worker.join(timeout=2.0)

        threading.Thread(target=join_threads, daemon=True).start()
        self.runtime = None
        self._set_running_ui(False)
        self.append_debug("已停止监听")
        self._update_metrics(0, 0, "-")
        self._set_status("已停止", "stopped")

    def _set_running_ui(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        for widget in (
            self.listen_mic,
            self.listen_system,
            self.mic_combo,
            self.system_combo,
            self.model_combo,
            self.device_combo,
            self.compute_combo,
            self.language_combo,
            self.text_mode_combo,
            self.chunk_seconds,
            self.min_rms,
        ):
            widget.setEnabled(not running)

    def clear_transcripts(self) -> None:
        self.mic_text.clear()
        self.system_text.clear()
        self.timeline_text.clear()
        self.debug_text.clear()
        self._set_status("文本已清空", "idle")

    def start_video_transcription(self) -> None:
        if self.video_runtime or self.media_download_runtime:
            return

        url = self.video_url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少视频链接", "请先粘贴 Bilibili 或其他平台的视频链接。")
            return

        language = self.video_language_combo.currentText()
        language_value = None if language == "auto" else language
        options = VideoTranscriptionOptions(
            url=url,
            model_size=self.video_model_combo.currentText(),
            device=self.video_device_combo.currentText(),
            compute_type=self.video_compute_combo.currentText(),
            language=language_value,
            text_mode=self.video_text_mode_combo.currentData(),
            output_dir=Path("transcripts"),
            cookie_browser=self.video_cookie_combo.currentData(),
            optimize_with_deepseek=False,
        )

        progress_queue: queue.Queue[str] = queue.Queue()
        result_queue: queue.Queue[VideoTranscriptionResult | Exception] = queue.Queue()

        def worker() -> None:
            try:
                result = transcribe_platform_video(options, progress=progress_queue.put)
            except Exception as exc:
                result_queue.put(exc)
            else:
                result_queue.put(result)

        thread = threading.Thread(target=worker, name="video-transcription-worker", daemon=True)
        self.video_runtime = VideoRuntimeState(progress_queue, result_queue, thread, time.time())
        self.video_result = None
        self.video_raw_text.clear()
        self.video_optimized_text.clear()
        self.video_log_text.clear()
        self.append_video_log(f"开始平台视频转写: {url}")
        self.append_video_log(
            f"model={options.model_size}, device={options.device}, compute={options.compute_type}, "
            f"language={options.language or 'auto'}"
        )
        self._set_video_running_ui(True)
        self.video_optimize_button.setEnabled(False)
        self._set_status("平台视频转写处理中", "working")
        thread.start()

    def start_media_download(self) -> None:
        if self.video_runtime or self.media_download_runtime:
            return

        url = self.video_url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少视频链接", "请先粘贴要下载的视频链接。")
            return

        options = MediaDownloadOptions(
            url=url,
            output_dir=Path("downloads"),
            cookie_browser=self.video_cookie_combo.currentData(),
        )
        progress_queue: queue.Queue[str] = queue.Queue()
        result_queue: queue.Queue[MediaDownloadResult | Exception] = queue.Queue()

        def worker() -> None:
            try:
                result_queue.put(download_media(options, progress=progress_queue.put))
            except Exception as exc:
                result_queue.put(exc)

        thread = threading.Thread(target=worker, name="media-download-worker", daemon=True)
        self.media_download_runtime = MediaDownloadRuntimeState(progress_queue, result_queue, thread, time.time())
        self.video_log_text.clear()
        self.append_video_log(f"开始下载视频: {url}")
        self._set_video_running_ui(True)
        self._set_status("视频下载处理中", "working")
        thread.start()

    def stop_video_transcription(self) -> None:
        if self.media_download_runtime:
            self.video_stop_button.setEnabled(False)
            self.append_video_log("下载任务正在收尾；当前下载器会在本次请求完成后返回。")
            return
        runtime = self.video_runtime
        if not runtime:
            return
        runtime.cancel_requested = True
        self.video_stop_button.setEnabled(False)
        self.append_video_log("已请求停止。后台任务会在当前下载或识别步骤结束后退出显示。")
        self._set_status("正在停止平台视频转写", "stopped")

    def start_video_agent_optimization(self) -> None:
        if self.video_runtime or self.video_agent_runtime:
            return

        result = self.video_result
        raw_text = self.video_raw_text.toPlainText().strip()
        if not result or not raw_text:
            QMessageBox.warning(self, "没有原始文字稿", "请先完成平台视频转写，再使用 Agent 优化。")
            return

        progress_queue: queue.Queue[str] = queue.Queue()
        result_queue: queue.Queue[tuple[DeepSeekOptimizationResult, Path] | Exception] = queue.Queue()
        api_key = self.video_deepseek_key_input.text().strip()
        model = self.video_deepseek_model_combo.currentText()
        optimized_path = result.raw_transcript_path.with_name("transcript_deepseek.md")

        def worker() -> None:
            try:
                optimization = optimize_transcript_with_deepseek(
                    raw_text,
                    api_key=api_key,
                    model=model,
                    progress=progress_queue.put,
                )
                optimized_path.write_text(optimization.text, encoding="utf-8")
            except Exception as exc:
                result_queue.put(exc)
            else:
                result_queue.put((optimization, optimized_path))

        thread = threading.Thread(target=worker, name="video-agent-worker", daemon=True)
        self.video_agent_runtime = VideoAgentRuntimeState(progress_queue, result_queue, thread, time.time())
        self.video_optimized_text.clear()
        self.video_optimize_button.setEnabled(False)
        self._set_video_inputs_enabled(False)
        self.append_video_log(f"开始 Agent 优化文字稿: model={model}")
        self._set_status("Agent 优化处理中", "working")
        thread.start()

    def clear_video_transcripts(self) -> None:
        self.video_raw_text.clear()
        self.video_optimized_text.clear()
        self.video_log_text.clear()
        self.video_result = None
        self.video_optimize_button.setEnabled(False)
        self._set_status("平台视频结果已清空", "idle")

    def _set_video_running_ui(self, running: bool) -> None:
        self.video_start_button.setEnabled(not running)
        self.video_download_button.setEnabled(not running)
        self.video_stop_button.setEnabled(running)
        self._set_video_inputs_enabled(not running)
        self.video_optimize_button.setEnabled(
            not running and self.video_agent_runtime is None and self.video_result is not None
        )

    def _set_video_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.video_url_input,
            self.video_model_combo,
            self.video_device_combo,
            self.video_compute_combo,
            self.video_language_combo,
            self.video_text_mode_combo,
            self.video_cookie_combo,
            self.video_settings_button,
            self.video_deepseek_key_input,
            self.video_deepseek_model_combo,
        ):
            widget.setEnabled(enabled)

    def poll_runtime(self) -> None:
        runtime = self.runtime
        self.poll_video_runtime()
        self.poll_media_download_runtime()
        self.poll_video_agent_runtime()
        if not runtime:
            return

        self._drain_debug(runtime)

        if not runtime.asr_worker.is_alive():
            error = runtime.asr_worker.last_error
            self.stop_listening()
            if error:
                self.append_debug(f"ASR 已停止: {error}")
            self._set_status("ASR 线程异常停止。请查看调试日志。", "error")
            QMessageBox.critical(
                self,
                "识别已停止",
                "ASR 线程异常停止。请查看调试日志；如果使用 cuda，先切换到 cpu 测试。",
            )
            return

        if runtime.asr_worker.model_ready.is_set() and not runtime.capture_started:
            for capture in runtime.capture_threads:
                capture.start()
            runtime.capture_started = True
            self.append_debug("模型已就绪，开始采集音频")

        if not runtime.capture_started:
            elapsed = int(time.time() - self.started_at)
            if time.time() - runtime.last_loading_notice_at >= 10:
                self.append_debug(f"模型仍在加载中，已等待 {elapsed}s。首次下载 medium 模型可能比较慢。")
                runtime.last_loading_notice_at = time.time()
            self._update_metrics(elapsed, runtime.audio_queue.qsize(), "加载")
            self._set_status(f"模型加载中... {elapsed}s | 音频采集尚未启动", "loading")
            return

        processed = 0
        while processed < 20:
            try:
                transcript = runtime.transcript_queue.get_nowait()
            except queue.Empty:
                break
            self.append_transcript(transcript)
            processed += 1

        elapsed = int(time.time() - self.started_at)
        alive = ", ".join(thread.source for thread in runtime.capture_threads if thread.is_alive()) or "none"
        self._update_metrics(elapsed, runtime.audio_queue.qsize(), alive)
        self._set_status(f"监听中... {elapsed}s | 待识别音频块: {runtime.audio_queue.qsize()} | 采集线程: {alive}", "running")

    def poll_video_runtime(self) -> None:
        runtime = self.video_runtime
        if not runtime:
            return

        while True:
            try:
                message = runtime.progress_queue.get_nowait()
            except queue.Empty:
                break
            self.append_video_log(message)

        try:
            result = runtime.result_queue.get_nowait()
        except queue.Empty:
            elapsed = int(time.time() - runtime.started_at)
            if self.pages.currentIndex() == 1:
                if runtime.cancel_requested:
                    self._set_status(f"平台视频转写正在停止... {elapsed}s", "stopped")
                else:
                    self._set_status(f"平台视频转写处理中... {elapsed}s", "working")
            return

        self.video_runtime = None
        self._set_video_running_ui(False)
        if runtime.cancel_requested:
            self.append_video_log("后台任务已结束，本次结果已丢弃。")
            self._set_status("平台视频转写已停止", "stopped")
            return

        if isinstance(result, Exception):
            self.append_video_log(f"处理失败: {result}")
            self._set_status("平台视频转写失败", "error")
            QMessageBox.critical(self, "平台视频转写失败", str(result))
            return

        self.video_raw_text.setPlainText(result.raw_text)
        self.video_result = result
        self.append_video_log(f"音频文件: {result.audio_path}")
        self.append_video_log(f"原始文字稿: {result.raw_transcript_path}")
        self.append_video_log(f"时间戳文字稿: {result.timestamped_transcript_path}")
        self.append_video_log("原始文字稿已生成，可点击“优化文字稿”调用 Agent。")
        self.video_optimize_button.setEnabled(True)
        self._set_status("平台视频转写完成", "idle")

    def poll_media_download_runtime(self) -> None:
        runtime = self.media_download_runtime
        if not runtime:
            return

        while True:
            try:
                self.append_video_log(runtime.progress_queue.get_nowait())
            except queue.Empty:
                break

        try:
            result = runtime.result_queue.get_nowait()
        except queue.Empty:
            if self.pages.currentIndex() == 1:
                elapsed = int(time.time() - runtime.started_at)
                self._set_status(f"视频下载处理中... {elapsed}s", "working")
            return

        self.media_download_runtime = None
        self._set_video_running_ui(False)
        if isinstance(result, Exception):
            self.append_video_log(f"下载失败: {result}")
            self._set_status("视频下载失败", "error")
            QMessageBox.critical(self, "视频下载失败", str(result))
            return

        if result.status == "queued":
            self.append_video_log("已交给 OmniGet 下载。请在 OmniGet 窗口查看进度和保存位置。")
        else:
            self.append_video_log(f"视频文件: {result.media_path}")
        if result.title:
            self.append_video_log(f"标题: {result.title}")
        self._set_status("已交给 OmniGet 下载" if result.status == "queued" else "视频下载完成", "idle")

    def poll_video_agent_runtime(self) -> None:
        runtime = self.video_agent_runtime
        if not runtime:
            return

        while True:
            try:
                message = runtime.progress_queue.get_nowait()
            except queue.Empty:
                break
            self.append_video_log(message)

        try:
            result = runtime.result_queue.get_nowait()
        except queue.Empty:
            elapsed = int(time.time() - runtime.started_at)
            if self.pages.currentIndex() == 1:
                self._set_status(f"Agent 优化处理中... {elapsed}s", "working")
            return

        self.video_agent_runtime = None
        self._set_video_inputs_enabled(True)
        self.video_optimize_button.setEnabled(self.video_result is not None)

        if isinstance(result, Exception):
            self.append_video_log(f"Agent 优化失败: {result}")
            self._set_status("Agent 优化失败", "error")
            QMessageBox.critical(self, "Agent 优化失败", str(result))
            return

        optimization, optimized_path = result
        usage_text = optimization.usage.to_display_text()
        self.video_optimized_text.setPlainText(optimization.text)
        if self.video_result:
            self.video_result = replace(
                self.video_result,
                optimized_text=optimization.text,
                optimized_transcript_path=optimized_path,
                optimized_token_usage=optimization.usage,
            )
        self.append_video_log(f"优化文字稿: {optimized_path}")
        self.append_video_log(f"DeepSeek Token 用量: {usage_text}")
        self._show_usage_toast(f"Agent 优化完成\n{usage_text}")
        self._set_status("Agent 优化完成", "idle")

    def _drain_debug(self, runtime: RuntimeState) -> None:
        processed = 0
        while processed < 80:
            try:
                event = runtime.debug_queue.get_nowait()
            except queue.Empty:
                break
            created_at = getattr(event, "created_at", time.time())
            source = getattr(event, "source", None)
            prefix = f"[{source.upper()}] " if source else ""
            self.append_debug(f"[{time.strftime('%H:%M:%S', time.localtime(created_at))}] {prefix}{event.message}")
            processed += 1

    def append_transcript(self, transcript: Transcript) -> None:
        label = "MIC" if transcript.source == "mic" else "SYSTEM"
        line = f"[{time.strftime('%H:%M:%S', time.localtime(transcript.ended_at))}] {transcript.text}"
        timeline_line = f"[{time.strftime('%H:%M:%S', time.localtime(transcript.ended_at))}] [{label}] {transcript.text}"

        target = self.mic_text if transcript.source == "mic" else self.system_text
        target.appendPlainText(line)
        self.timeline_text.appendPlainText(timeline_line)

    def append_debug(self, line: str) -> None:
        self.debug_text.appendPlainText(line)

    def append_video_log(self, line: str) -> None:
        self.video_log_text.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {line}")

    def closeEvent(self, event) -> None:
        self.stop_listening()
        self.stop_video_transcription()
        self.video_agent_runtime = None
        self.media_download_runtime = None
        event.accept()


def main() -> int:
    configure_windows_app_id()
    app = QApplication([])
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setWindowIcon(create_app_icon())
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor(SWISS_ACCENT))
    app.setPalette(palette)

    model_error = required_models_error_message()
    if model_error:
        print(model_error)
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            QMessageBox.critical(None, "模型缺失", model_error)
        return 2

    window = VoiceToTextWindow()
    window.show()
    return app.exec()
