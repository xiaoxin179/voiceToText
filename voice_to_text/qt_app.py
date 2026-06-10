from __future__ import annotations

import queue
import threading
import time
import os
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
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


@dataclass
class RuntimeState:
    audio_queue: queue.Queue[AudioChunk]
    transcript_queue: queue.Queue[Transcript]
    debug_queue: queue.Queue[object]
    asr_worker: AsrWorker
    capture_threads: list[AudioCapture]
    capture_started: bool = False
    last_loading_notice_at: float = 0.0


class VoiceToTextWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("")
        self._set_blank_window_icon()
        self.resize(1360, 900)
        self.setMinimumSize(1100, 740)

        self.mic_devices: list[AudioDevice] = []
        self.system_devices: list[AudioDevice] = []
        self.runtime: RuntimeState | None = None
        self.started_at = 0.0

        self._build_ui()
        self._apply_style()
        self._connect_events()
        self.refresh_devices()

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll_runtime)
        self.timer.start()

    def _set_blank_window_icon(self) -> None:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        self.setWindowIcon(QIcon(pixmap))

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(22, 18, 22, 16)
        root_layout.setSpacing(16)
        self.setCentralWidget(root)

        header = QFrame()
        header.setObjectName("headerPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(16)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        title = QLabel("实时语音转文字")
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

        self.start_button = QPushButton("开始监听")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.clear_button = QPushButton("清空文本")
        self.refresh_button = QPushButton("刷新设备")
        for button in (self.start_button, self.stop_button, self.clear_button, self.refresh_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.setEnabled(False)
        header_layout.addWidget(self.start_button)
        header_layout.addWidget(self.stop_button)
        header_layout.addWidget(self.clear_button)
        header_layout.addWidget(self.refresh_button)
        root_layout.addWidget(header)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(16)
        root_layout.addLayout(body_layout, stretch=1)

        sidebar = QFrame()
        sidebar.setObjectName("sidePanel")
        sidebar.setFixedWidth(390)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(18)
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
        asr_form.addRow("切片", self.chunk_seconds)
        asr_form.addRow("静音阈值", self.min_rms)
        sidebar_layout.addLayout(asr_form)
        sidebar_layout.addStretch()

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(10)
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

        self.status = QStatusBar(self)
        self.status.setObjectName("bottomStatus")
        self.setStatusBar(self.status)
        self._set_status("就绪", "idle")

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
        layout.setContentsMargins(16, 14, 16, 16)
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
                background: #eef2f5;
                color: #17212b;
            }
            QLabel {
                color: #17212b;
                font-family: "Microsoft YaHei UI";
            }
            QFrame#headerPanel, QFrame#sidePanel, QFrame#micPanel, QFrame#systemPanel,
            QFrame#timelinePanel, QFrame#debugPanel {
                background: #ffffff;
                border: 1px solid #d9e1ea;
                border-radius: 8px;
            }
            QFrame#statusPanel {
                background: #f7fafc;
                border: 1px solid #d9e1ea;
                border-radius: 8px;
            }
            QLabel#appTitle {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#appSubtitle, QLabel#sectionSubtitle, QLabel#panelSubtitle, QLabel#metricLabel {
                color: #65758a;
                font-size: 12px;
            }
            QLabel#sectionTitle, QLabel#panelTitle {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#statusBadge {
                min-width: 64px;
                min-height: 28px;
                padding: 2px 12px;
                border-radius: 14px;
                font-weight: 700;
            }
            QPlainTextEdit {
                background: #fbfcfd;
                border: 1px solid #d7e0ea;
                border-radius: 6px;
                padding: 10px;
                selection-background-color: #15616d;
                selection-color: #ffffff;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                min-height: 34px;
                border: 1px solid #c8d3df;
                border-radius: 6px;
                padding: 4px 10px;
                background: #ffffff;
            }
            QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {
                border-color: #9db3c7;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
                border-color: #15616d;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #9db3c7;
                border-radius: 5px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #15616d;
                border-color: #15616d;
            }
            QPushButton {
                min-height: 34px;
                padding: 0 15px;
                border: 1px solid #c8d3df;
                border-radius: 6px;
                background: #ffffff;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f4f7fa;
                border-color: #9db3c7;
            }
            QPushButton:disabled {
                color: #9aa7b4;
                background: #edf1f5;
                border-color: #d8e0e8;
            }
            QPushButton#primaryButton {
                color: #ffffff;
                background: #15616d;
                border-color: #15616d;
            }
            QPushButton#primaryButton:hover {
                background: #0f5360;
            }
            QPushButton#dangerButton {
                color: #7a2e24;
                background: #fff4ef;
                border-color: #e9b8a8;
            }
            QPushButton#dangerButton:hover {
                background: #ffe8df;
            }
            QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled {
                color: #9aa7b4;
                background: #edf1f5;
                border-color: #d8e0e8;
            }
            QFrame#divider {
                color: #d9e1ea;
                background: #d9e1ea;
                max-height: 1px;
            }
            QFrame#metric {
                background: #ffffff;
                border: 1px solid #d9e1ea;
                border-radius: 6px;
            }
            QLabel#metricValue {
                color: #15616d;
                font-size: 18px;
                font-weight: 700;
            }
            QSplitter::handle {
                background: #eef2f5;
            }
            QStatusBar#bottomStatus {
                background: #eef2f5;
                border-top: 1px solid #d9e1ea;
                color: #526173;
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
        }
        colors = {
            "idle": ("#eff6ff", "#1d4f7a", "#c9dff5"),
            "loading": ("#fff7e6", "#7a4b00", "#f0d18a"),
            "running": ("#eaf7f4", "#0f5f57", "#a7d7cf"),
            "stopped": ("#f1f5f9", "#475569", "#d4dde7"),
            "error": ("#fff0f0", "#8a1f1f", "#efb5b5"),
        }
        background, foreground, border = colors.get(tone, colors["idle"])
        self.status_badge.setText(labels.get(tone, "就绪"))
        self.status_badge.setStyleSheet(
            f"background: {background}; color: {foreground}; border: 1px solid {border};"
        )

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
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)
        self.clear_button.clicked.connect(self.clear_transcripts)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.device_combo.currentTextChanged.connect(self._sync_compute_default)

    def _sync_compute_default(self, device: str) -> None:
        self.compute_combo.setCurrentText("int8" if device == "cpu" else "float16")

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
            f"compute={self.compute_combo.currentText()}, chunk={chunk_seconds}s, min_rms={self.min_rms.value():.5f}"
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

    def poll_runtime(self) -> None:
        runtime = self.runtime
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

    def closeEvent(self, event) -> None:
        self.stop_listening()
        event.accept()


def main() -> int:
    app = QApplication([])
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
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
