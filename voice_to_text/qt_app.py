from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
from .audio_devices import AudioDevice, get_default_microphone, get_default_system_loopback, list_devices, list_loopback_devices


@dataclass
class RuntimeState:
    audio_queue: queue.Queue[AudioChunk]
    transcript_queue: queue.Queue[Transcript]
    asr_worker: AsrWorker
    capture_threads: list[AudioCapture]


class VoiceToTextWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实时语音转文字")
        self.resize(1180, 760)

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

        transcript_grid.addWidget(QLabel("麦克风转写"), 0, 0)
        transcript_grid.addWidget(QLabel("系统音频转写"), 0, 1)
        transcript_grid.addWidget(self.mic_text, 1, 0)
        transcript_grid.addWidget(self.system_text, 1, 1)
        transcript_grid.addWidget(QLabel("合并时间线"), 2, 0, 1, 2)
        transcript_grid.addWidget(self.timeline_text, 3, 0, 1, 2)
        transcript_grid.setColumnStretch(0, 1)
        transcript_grid.setColumnStretch(1, 1)
        transcript_grid.setRowStretch(1, 3)
        transcript_grid.setRowStretch(3, 2)

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
        font = QFont("Microsoft YaHei UI", 10)
        box.setFont(font)
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
            QComboBox, QSpinBox {
                min-height: 28px;
                border: 1px solid #cbd3df;
                border-radius: 4px;
                padding: 3px 8px;
                background: #ffffff;
            }
            QPushButton { min-height: 30px; }
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
        if device == "cpu":
            self.compute_combo.setCurrentText("int8")
        else:
            self.compute_combo.setCurrentText("float16")

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
        self.status.showMessage(f"已加载 {len(self.mic_devices)} 个输入设备和 {len(self.system_devices)} 个系统音频设备")

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
        language = self.language_combo.currentText()
        language_value = None if language == "auto" else language

        asr_worker = AsrWorker(
            input_queue=audio_queue,
            output_queue=transcript_queue,
            model_size=self.model_combo.currentText(),
            device=self.device_combo.currentText(),
            compute_type=self.compute_combo.currentText(),
            language=language_value,
        )

        captures: list[AudioCapture] = []
        chunk_seconds = float(self.chunk_seconds.value())

        if self.listen_mic.isChecked():
            mic = self.mic_combo.currentData()
            if mic:
                captures.append(AudioCapture(source="mic", device=mic, output_queue=audio_queue, chunk_seconds=chunk_seconds))
        if self.listen_system.isChecked():
            system = self.system_combo.currentData()
            if system:
                captures.append(AudioCapture(source="system", device=system, output_queue=audio_queue, chunk_seconds=chunk_seconds))

        self.runtime = RuntimeState(audio_queue, transcript_queue, asr_worker, captures)
        self.started_at = time.time()
        asr_worker.start()
        for capture in captures:
            capture.start()

        self._set_running_ui(True)
        self.status.showMessage("正在监听。首次加载模型可能需要等待。")

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
        ):
            widget.setEnabled(not running)

    def clear_transcripts(self) -> None:
        self.mic_text.clear()
        self.system_text.clear()
        self.timeline_text.clear()

    def poll_runtime(self) -> None:
        runtime = self.runtime
        if not runtime:
            return

        if not runtime.asr_worker.is_alive():
            self.stop_listening()
            QMessageBox.critical(self, "识别已停止", "ASR 线程异常停止。请检查 CUDA 运行库，或切换到 CPU 模式。")
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
        self.status.showMessage(f"监听中... {elapsed}s | 待识别音频块: {runtime.audio_queue.qsize()}")

    def append_transcript(self, transcript: Transcript) -> None:
        label = "MIC" if transcript.source == "mic" else "SYSTEM"
        line = f"[{time.strftime('%H:%M:%S', time.localtime(transcript.ended_at))}] {transcript.text}"
        timeline_line = f"[{time.strftime('%H:%M:%S', time.localtime(transcript.ended_at))}] [{label}] {transcript.text}"

        target = self.mic_text if transcript.source == "mic" else self.system_text
        target.appendPlainText(line)
        self.timeline_text.appendPlainText(timeline_line)

    def closeEvent(self, event) -> None:
        self.stop_listening()
        event.accept()


def main() -> int:
    app = QApplication([])
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
    app.setPalette(palette)

    window = VoiceToTextWindow()
    window.show()
    return app.exec()
