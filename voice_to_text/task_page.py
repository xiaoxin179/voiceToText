"""Desktop conversion queue; execution is explicitly delegated to a Codex worker."""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QTextCursor, QTextBlockFormat, QTextCharFormat
from PyQt6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QCheckBox, QSpinBox, QPushButton,
    QDialogButtonBox, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QTabWidget, QComboBox, QSplitter, QFrame, QTextBrowser, QToolButton, QSizePolicy)

from .task_store import TaskStore, STATUS_LABELS, PHASE_LABELS, TERMINAL, ACTIVE, extract_urls, read_task_artifact, default_task_output_dir


from .ui_theme import APP_STYLE, QUEUE_STYLE


def source_name(url):
    host = (urlsplit(url).hostname or "").lower()
    for domain, name in (("douyin.com", "抖音"), ("bilibili.com", "哔哩哔哩"),
                         ("b23.tv", "哔哩哔哩"), ("youtube.com", "YouTube"), ("youtu.be", "YouTube")):
        if host == domain or host.endswith("." + domain): return name
    return host or "视频"


def display_title(task):
    return task["title"] or f"{source_name(task['url'])}视频"


def source_reference(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    reference = (query.get("modal_id") or query.get("v") or [parsed.path.rstrip("/").split("/")[-1]])[0]
    return f"视频 …{reference[-6:]}" if reference else ""


class ElidedLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))


class TaskEditDialog(QDialog):
    def __init__(self, parent=None, task=None, batch=False):
        super().__init__(parent)
        self.setObjectName("taskEditor")
        self.setStyleSheet(APP_STYLE + QUEUE_STYLE)
        self.task = task
        self.batch = batch
        self.setWindowTitle("编辑转换任务" if task else "批量添加任务" if batch else "新增转换任务")
        self.resize(620, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("粘贴视频链接；批量添加时每行一个链接，也支持分享文本")
        self.urls.setMaximumHeight(110)
        self.title = QLineEdit()
        self.title.setPlaceholderText("可留空，由 Codex 从视频页面读取")
        self.output = QLineEdit(str(default_task_output_dir()))
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText("例如：保留专业术语；总结为问题、观点和行动项")
        self.instructions.setMaximumHeight(90)
        self.chunk = QSpinBox()
        self.chunk.setRange(1, 60)
        self.chunk.setSuffix(" s")
        self.chunk.setValue(10)
        self.keep = QCheckBox("保留录音（默认不保留）")
        self.optimize = QCheckBox("生成校对稿")
        self.optimize.setChecked(True)
        self.summarize = QCheckBox("生成总结")
        self.summarize.setChecked(True)
        self.duplicate = QCheckBox("允许添加重复链接")
        for name, widget in (("视频链接", self.urls), ("标题", self.title), ("输出目录", self.output),
                             ("切片时长", self.chunk), ("处理要求", self.instructions)):
            form.addRow(name, widget)
        layout.addLayout(form)
        for box in (self.keep, self.optimize, self.summarize, self.duplicate): layout.addWidget(box)
        layout.addWidget(QLabel("默认使用 medium / cuda / float16 / 中文；任务不会自行启动浏览器。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存任务")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("queuePrimary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if task:
            p = task["payload"]
            self.urls.setPlainText(task["url"])
            self.title.setText(task["title"])
            self.output.setText(p["output_dir"])
            self.instructions.setPlainText(p["instructions"])
            self.chunk.setValue(int(p["config"]["chunk_seconds"]))
            self.keep.setChecked(p["keep_audio"])
            self.optimize.setChecked(p["optimize"])
            self.summarize.setChecked(p["summarize"])
            self.duplicate.hide()

    def validate(self):
        urls = extract_urls(self.urls.toPlainText())
        if not urls or (not self.batch and len(urls) != 1):
            QMessageBox.warning(self, "检查链接", "请填写视频链接；多条链接请使用批量添加。")
            return
        if not self.output.text().strip():
            QMessageBox.warning(self, "检查目录", "请填写输出目录。")
            return
        self.accept()

    def data(self):
        config = dict(self.task["payload"]["config"]) if self.task else {}
        config["chunk_seconds"] = self.chunk.value()
        return dict(title=self.title.text().strip(), output_dir=self.output.text().strip(),
                    instructions=self.instructions.toPlainText(), config=config,
                    keep_audio=self.keep.isChecked(), optimize=self.optimize.isChecked(),
                    summarize=self.summarize.isChecked())


class TaskQueuePage(QWidget):
    def __init__(self, parent=None, store=None):
        super().__init__(parent)
        self.setObjectName("conversionQueue")
        self.setStyleSheet(APP_STYLE + QUEUE_STYLE)
        self.store = store or TaskStore()
        self.signature = None
        self.page_number = 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        titlebar = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        heading = QLabel("转换任务")
        heading.setObjectName("queueTitle")
        titles.addWidget(heading)
        subtitle = QLabel("保存视频链接，由 Codex 依次完成转写、校对与总结。")
        subtitle.setObjectName("queueMuted")
        titles.addWidget(subtitle)
        titlebar.addLayout(titles)
        titlebar.addStretch()
        for label, callback in (("批量添加", lambda: self.add_tasks(True)), ("新增任务", lambda: self.add_tasks())):
            button = QPushButton(label)
            if label == "新增任务": button.setObjectName("queuePrimary")
            button.clicked.connect(callback)
            titlebar.addWidget(button)
        layout.addLayout(titlebar)
        self.count = QLabel()
        self.count.setObjectName("queueMuted")
        layout.addWidget(self.count)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(18)
        list_panel = QFrame()
        list_panel.setObjectName("queuePanel")
        list_panel.setMinimumWidth(280)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        filters = QHBoxLayout()
        filters.setContentsMargins(14, 14, 14, 14)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题或链接")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reset_page)
        filters.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItem("全部状态", "")
        for key, label in STATUS_LABELS.items(): self.filter.addItem(label, key)
        self.filter.setFixedWidth(104)
        self.filter.currentIndexChanged.connect(self.reset_page)
        filters.addWidget(self.filter)
        list_layout.addLayout(filters)
        self.table = QTableWidget(0, 2)
        self.table.setObjectName("queueList")
        self.table.setHorizontalHeaderLabels(["视频任务", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 86)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(82)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.show_details)
        list_layout.addWidget(self.table, 1)
        self.list_empty = QLabel("还没有转换任务\n添加视频链接后，任务会保存在这里。")
        self.list_empty.setObjectName("queueMuted")
        self.list_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_empty.setMinimumHeight(100)
        list_layout.addWidget(self.list_empty, 1)
        pager = QHBoxLayout()
        pager.setContentsMargins(12, 10, 12, 0)
        pager.setSpacing(8)
        self.page_summary = QLabel()
        self.page_summary.setObjectName("queueMuted")
        pager.addWidget(self.page_summary)
        pager.addStretch()
        self.page_size = QComboBox()
        for size in (10, 20, 50): self.page_size.addItem(f"{size} 条 / 页", size)
        self.page_size.setFixedWidth(104)
        self.page_size.currentIndexChanged.connect(self.reset_page)
        pager.addWidget(self.page_size)
        list_layout.addLayout(pager)
        navigation = QHBoxLayout()
        navigation.setContentsMargins(12, 8, 12, 0)
        navigation.setSpacing(8)
        self.previous_page = QPushButton("上一页")
        self.next_page = QPushButton("下一页")
        self.previous_page.clicked.connect(lambda: self.change_page(-1))
        self.next_page.clicked.connect(lambda: self.change_page(1))
        self.page_label = QLabel()
        self.page_label.setObjectName("queueMuted")
        navigation.addWidget(self.previous_page)
        navigation.addStretch()
        navigation.addWidget(self.page_label)
        navigation.addStretch()
        navigation.addWidget(self.next_page)
        list_layout.addLayout(navigation)
        list_footer = QLabel("任务保存在本地，关闭窗口不会丢失。")
        list_footer.setObjectName("queueMuted")
        list_footer.setContentsMargins(14, 12, 14, 12)
        list_layout.addWidget(list_footer)
        splitter.addWidget(list_panel)
        detail_panel = QFrame()
        detail_panel.setObjectName("queuePanel")
        detail_panel.setMinimumWidth(390)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(22, 20, 22, 14)
        detail_layout.setSpacing(12)
        detail_heading = QHBoxLayout()
        self.detail_title = QLabel("选择一个任务")
        self.detail_title.setObjectName("queueDetailTitle")
        self.detail_title.setWordWrap(True)
        detail_heading.addWidget(self.detail_title, 1)
        self.badge = QLabel()
        detail_heading.addWidget(self.badge)
        detail_layout.addLayout(detail_heading)
        self.metadata = QLabel()
        self.metadata.setObjectName("queueMuted")
        self.metadata.setWordWrap(True)
        detail_layout.addWidget(self.metadata)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background:#F0F5FF; padding:14px; border-radius:8px; color:#617599;")
        detail_layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.details = QPlainTextEdit()
        self.details.setObjectName("queueLogs")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        self.details.hide()
        self.result_views = {}
        for key, name in (("summary_path", "总结"), ("optimized_transcript_path", "校对稿"),
                          ("raw_transcript_path", "原始稿"), ("timestamped_transcript_path", "时间戳稿")):
            view = QTextBrowser()
            view.setObjectName("queueReader")
            view.setReadOnly(True)
            view.setOpenLinks(False)
            view.document().setDefaultFont(QFont("Microsoft YaHei UI", 10))
            view.document().setDocumentMargin(16)
            view.document().setDefaultStyleSheet("""
                body { font-family: 'Microsoft YaHei UI'; color: #33425C; }
                p { margin-top: 10px; margin-bottom: 12px; line-height: 150%; }
                h1 { font-size: 21px; margin-top: 8px; margin-bottom: 18px; color: #24324B; }
                h2 { font-size: 18px; margin-top: 22px; margin-bottom: 12px; }
                h3 { font-size: 15px; margin-top: 18px; margin-bottom: 10px; }
                li { margin-bottom: 8px; }
                a { color: #386CF4; }
                code { background-color: #F0F4FB; font-family: Consolas; }
            """)
            self.result_views[key] = view
            self.tabs.addTab(view, name)
        detail_layout.addWidget(self.tabs, 1)
        self.log_toggle = QToolButton()
        self.log_toggle.setObjectName("queueLogToggle")
        self.log_toggle.setText("技术信息与执行日志")
        self.log_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.log_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.toggle_logs)
        detail_layout.addWidget(self.log_toggle)
        detail_layout.addWidget(self.details)
        rule = QFrame()
        rule.setObjectName("queueRule")
        detail_layout.addWidget(rule)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.buttons = {}
        for key, label in (("edit", "编辑任务"), ("retry", "重新处理"), ("stop", "取消任务"), ("folder", "打开结果目录")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=key: self.act(action))
            actions.addWidget(button)
            self.buttons[key] = button
        actions.insertStretch(3, 1)
        detail_layout.addLayout(actions)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([410, 640])
        layout.addWidget(splitter, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh(True)

    def toggle_logs(self, checked):
        self.details.setVisible(checked)
        self.log_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def reset_page(self, *_):
        self.page_number = 1
        self.refresh(True)

    def change_page(self, delta):
        self.page_number = max(1, self.page_number + delta)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.refresh(True)

    def selected_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, force=False):
        try:
            status = self.filter.currentData()
            result = self.store.page(self.page_number, self.page_size.currentData(), status, self.search.text())
            self.page_number = result["page"]
            tasks = result["items"]
            signature = json.dumps(result, ensure_ascii=False)
            if signature == self.signature and not force: return
            self.signature = signature
            selected = self.selected_id()
            self.table.blockSignals(True)
            self.table.setRowCount(len(tasks))
            for row, task in enumerate(tasks):
                values = ["", STATUS_LABELS[task["status"]]]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(task["url"] if col == 0 else value)
                    item.setData(Qt.ItemDataRole.UserRole, task["id"])
                    if col == 1:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, col, item)
                card = QWidget()
                card.setObjectName("queueRow")
                card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(14, 14, 8, 14)
                card_layout.setSpacing(5)
                title = ElidedLabel(display_title(task))
                title.setObjectName("queueRowTitle")
                card_layout.addWidget(title)
                added = time.strftime("%m-%d %H:%M", time.localtime(task["created"]))
                reference = source_reference(task["url"])
                subtitle = ElidedLabel(f"{reference + ' · ' if reference else ''}{added}")
                subtitle.setObjectName("queueMuted")
                subtitle.setMinimumWidth(0)
                card_layout.addWidget(subtitle)
                self.table.setCellWidget(row, 0, card)
                if task["id"] == selected: self.table.selectRow(row)
            self.table.blockSignals(False)
            if tasks and self.table.currentRow() < 0: self.table.selectRow(0)
            counts = result["counts"]
            queued = counts.get("queued", 0)
            active = sum(counts.get(status, 0) for status in ACTIVE)
            complete = counts.get("completed", 0)
            self.count.setText(f"{queued} 待处理    /    {active} 执行中    /    {complete} 已完成")
            self.list_empty.setVisible(not tasks)
            self.table.setVisible(bool(tasks))
            self.list_empty.setText("没有匹配的任务\n试试其他关键词或状态。" if sum(counts.values()) else "还没有转换任务\n点击右上角“新增任务”添加视频链接。")
            total = result["total"]
            first = (self.page_number - 1) * result["page_size"] + 1 if total else 0
            last = first + len(tasks) - 1 if total else 0
            self.page_summary.setText(f"共 {total} 条 · {first}–{last} 条")
            self.page_label.setText(f"{self.page_number} / {result['pages']} 页")
            self.previous_page.setEnabled(self.page_number > 1)
            self.next_page.setEnabled(self.page_number < result["pages"])
            self.show_details()
        except (OSError, ValueError) as exc:
            self.count.setText(str(exc))

    def show_details(self):
        task_id = self.selected_id()
        for button in self.buttons.values():
            button.setEnabled(bool(task_id))
            button.setVisible(bool(task_id))
        self.details.setVisible(bool(task_id) and self.log_toggle.isChecked())
        if not task_id:
            self.detail_title.setText("选择一个任务")
            self.metadata.setText("任务详情和转换结果会显示在这里。")
            self.badge.hide()
            self.notice.setText("添加链接后，在 Codex 中说“处理转换队列”即可开始。")
            self.details.clear()
            self.log_toggle.hide()
            for view in self.result_views.values():
                view.setProperty("contentKey", None)
                view.setPlainText("选择任务后查看结果。")
            return
        task = self.store.get(task_id)
        p = task["payload"]
        self.log_toggle.show()
        self.badge.show()
        self.badge.setText(STATUS_LABELS[task["status"]])
        emphasis = task["status"] in ACTIVE or task["status"] == "completed"
        self.badge.setStyleSheet(f"background:{'#EAF0FF' if emphasis else '#F3F5F9'};color:{'#386CF4' if emphasis else '#8290A6'};padding:6px 10px;border-radius:10px;font-size:11px;")
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setText(display_title(task))
        self.metadata.setText(f"{source_name(task['url'])}  ·  {p['config']['chunk_seconds']} 秒切片  ·  {'保留录音' if p['keep_audio'] else '不保留录音'}")
        self.metadata.setToolTip(task["url"])
        notices = {"queued": "等待 Codex 领取。准备好后，在 Codex 中说“处理转换队列”。",
                   "completed": "转换已完成，可以阅读下方文字稿与总结。",
                   "cancelled": "任务已取消，点击“重新处理”可再次加入队列。",
                   "stopping": "已请求停止，正在等待执行器确认并保存已有内容。",
                   "interrupted": "执行器连接已中断。检查采集会话后，可以重新处理。",
                   "needs_review": "任务需要检查，请结合执行日志确认转写是否完整。"}
        self.notice.setText(str(task["error"] or task["progress"].get("message") or notices.get(task["status"], PHASE_LABELS[task["phase"]])))
        self.buttons["edit"].setEnabled(task["status"] == "queued")
        self.buttons["edit"].setVisible(task["status"] == "queued")
        self.buttons["retry"].setEnabled(task["status"] in TERMINAL)
        self.buttons["retry"].setVisible(task["status"] in TERMINAL)
        self.buttons["stop"].setEnabled(task["status"] == "queued" or task["status"] in ACTIVE)
        self.buttons["stop"].setVisible(task["status"] == "queued" or task["status"] in ACTIVE)
        self.buttons["stop"].setText("取消任务" if task["status"] == "queued" else "请求停止")
        self.buttons["folder"].setEnabled(Path(p["output_dir"]).is_dir())
        lines = [task["id"], task["url"], f"输出目录：{p['output_dir']}",
                 f"录音：{'保留' if p['keep_audio'] else '不保留'}；切片：{p['config']['chunk_seconds']} 秒",
                 f"处理要求：{p['instructions'] or '无'}", f"错误：{task['error'] or '无'}", "", "执行记录："]
        lines += [time.strftime("%H:%M:%S", time.localtime(e["at"])) + "  " + e["message"] for e in task["events"]]
        self.details.setPlainText("\n".join(lines))
        for key, view in self.result_views.items():
            try:
                artifact = read_task_artifact(task, key)
                content = artifact["text"] if artifact else ""
            except (OSError, ValueError) as exc: content = str(exc)
            if not content:
                disabled = (key == "summary_path" and not p["summarize"]) or (key == "optimized_transcript_path" and not p["optimize"])
                content = "此任务未启用这项输出。" if disabled else "结果尚未生成。\n\n任务处理完成后，内容会显示在这里。"
            cache_key = (task_id, content)
            if view.property("contentKey") != repr(cache_key):
                view.setProperty("contentKey", repr(cache_key))
                view.setMarkdown(content)
                document_cursor = QTextCursor(view.document())
                document_cursor.select(QTextCursor.SelectionType.Document)
                body_format = QTextCharFormat()
                body_format.setFontFamilies(["Microsoft YaHei UI"])
                body_format.setFontPointSize(10)
                document_cursor.mergeCharFormat(body_format)
                block = view.document().begin()
                while block.isValid():
                    cursor = QTextCursor(block)
                    block_format = block.blockFormat()
                    block_format.setLineHeight(145, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                    block_format.setTopMargin(6)
                    block_format.setBottomMargin(10)
                    cursor.setBlockFormat(block_format)
                    if block_format.headingLevel():
                        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                        heading_format = QTextCharFormat()
                        heading_format.setFontPointSize(14 if block_format.headingLevel() == 1 else 12)
                        cursor.mergeCharFormat(heading_format)
                    block = block.next()

    def add_tasks(self, batch=False):
        dialog = TaskEditDialog(self, batch=batch)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        errors = []
        for url in extract_urls(dialog.urls.toPlainText()):
            try: self.store.add({**dialog.data(), "url": url}, dialog.duplicate.isChecked())
            except ValueError as exc: errors.append(f"{url}\n{exc}")
        self.refresh(True)
        if errors: QMessageBox.warning(self, "部分链接未添加", "\n\n".join(errors))

    def act(self, action):
        task_id = self.selected_id()
        if not task_id: return
        try:
            task = self.store.get(task_id)
            if action == "edit":
                dialog = TaskEditDialog(self, task)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.store.edit(task_id, {**dialog.data(), "url": dialog.urls.toPlainText()})
            elif action == "retry": self.store.retry(task_id)
            elif action == "stop": self.store.stop(task_id)
            elif action == "folder":
                path = Path(task["payload"]["output_dir"])
                if not path.is_dir(): raise ValueError("任务尚未开始，输出目录还未创建。")
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            self.refresh(True)
        except (ValueError, OSError, KeyError) as exc: QMessageBox.warning(self, "任务操作", str(exc))
