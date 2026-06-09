from __future__ import annotations

import queue
import threading
import time
import os
from dataclasses import dataclass

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QToolBar,
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
        self.setWindowTitle("实时语音转文字")
        self.resize(1240, 860)

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

    def _build_ui(self) -> None:
        toolbar = QToolBar("控制", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.start_action = QAction("开始监听", self)
        self.stop_action = QAction("停止", self)
        self.clear_action = QAction("清空文本", self)
        self.refresh_action = QAction("刷新设备", self)
        toolbar.addAction(self.start_action)
        toolbar.addAction(self.stop_action)
        toolbar.addSeparator()
        toolbar.addAction(self.clear_action)
        toolbar.addAction(self.refresh_action)
        self.stop_action.setEnabled(False)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 14)
        root_layout.setSpacing(12)
        self.setCentralWidget(root)

        settings = QGroupBox("监听设置")
        settings_layout = QGridLayout(settings)
        settings_layout.setHorizontalSpacing(14)
        settings_layout.setVerticalSpacing(10)
        root_layout.addWidget(settings)

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

        form_left = QFormLayout()
        form_left.addRow("监听来源", self._wrap_layout(source_row))
        form_left.addRow("麦克风设备", self.mic_combo)
        form_left.addRow("系统音频设备", self.system_combo)

        form_right = QFormLayout()
        form_right.addRow("识别模型", self.model_combo)
        form_right.addRow("推理设备", self.device_combo)
        form_right.addRow("计算精度", self.compute_combo)
        form_right.addRow("语言", self.language_combo)
        form_right.addRow("切片长度", self.chunk_seconds)
        form_right.addRow("静音阈值", self.min_rms)

        settings_layout.addLayout(form_left, 0, 0)
        settings_layout.addLayout(form_right, 0, 1)
        settings_layout.setColumnStretch(0, 2)
        settings_layout.setColumnStretch(1, 1)

        transcript_grid = QGridLayout()
        transcript_grid.setSpacing(12)
        root_layout.addLayout(transcript_grid, stretch=1)

        self.mic_text = self._make_text_box()
        self.system_text = self._make_text_box()
        self.timeline_text = self._make_text_box()
        self.debug_text = self._make_text_box()

        transcript_grid.addWidget(QLabel("麦克风转写"), 0, 0)
        transcript_grid.addWidget(QLabel("系统音频转写"), 0, 1)
        transcript_grid.addWidget(self.mic_text, 1, 0)
        transcript_grid.addWidget(self.system_text, 1, 1)
        transcript_grid.addWidget(QLabel("合并时间线"), 2, 0, 1, 2)
        transcript_grid.addWidget(self.timeline_text, 3, 0, 1, 2)
        transcript_grid.addWidget(QLabel("调试日志"), 4, 0, 1, 2)
        transcript_grid.addWidget(self.debug_text, 5, 0, 1, 2)
        transcript_grid.setColumnStretch(0, 1)
        transcript_grid.setColumnStretch(1, 1)
        transcript_grid.setRowStretch(1, 3)
        transcript_grid.setRowStretch(3, 2)
        transcript_grid.setRowStretch(5, 1)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

    def _wrap_layout(self, layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

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
            QMainWindow { background: #f6f7f9; }
            QGroupBox {
                border: 1px solid #d8dee8;
                border-radius: 6px;
                margin-top: 10px;
                padding: 12px;
                background: #ffffff;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 6px;
                padding: 8px;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                min-height: 28px;
                border: 1px solid #cbd3df;
                border-radius: 4px;
                padding: 3px 8px;
                background: #ffffff;
            }
            QToolBar {
                background: #ffffff;
                border-bottom: 1px solid #d8dee8;
                spacing: 8px;
                padding: 6px;
            }
            QToolButton {
                min-width: 72px;
                min-height: 30px;
                border: 1px solid #cbd3df;
                border-radius: 4px;
                background: #ffffff;
            }
            QToolButton:hover { background: #eef3f8; }
            """
        )

    def _connect_events(self) -> None:
        self.start_action.triggered.connect(self.start_listening)
        self.stop_action.triggered.connect(self.stop_listening)
        self.clear_action.triggered.connect(self.clear_transcripts)
        self.refresh_action.triggered.connect(self.refresh_devices)
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
        self.status.showMessage("设备已刷新")

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

        audio_queue: queue.Queue[AudioChunk] = queue.Queue()
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
        self.status.showMessage("正在加载模型。首次使用 medium/large-v3 可能需要下载模型文件。")

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
        self.status.showMessage("已停止")

    def _set_running_ui(self, running: bool) -> None:
        self.start_action.setEnabled(not running)
        self.stop_action.setEnabled(running)
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
            self.status.showMessage(f"模型加载中... {elapsed}s | 音频采集尚未启动")
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
        self.status.showMessage(f"监听中... {elapsed}s | 待识别音频块: {runtime.audio_queue.qsize()} | 采集线程: {alive}")

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
