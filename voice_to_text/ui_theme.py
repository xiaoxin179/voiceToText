"""Shared desktop palette and widget styles for Voice Studio."""

from pathlib import Path

APP_STYLE = """
QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #24324B; }
QMainWindow, QWidget#appRoot { background: #F3F5F9; }
QDialog { background: #F8FAFD; }
QLabel { background: transparent; border: none; }
QFrame#headerPanel { background: transparent; border: none; }
QLabel#appTitle { font-size: 26px; font-weight: 700; color: #19263E; }
QLabel#appSubtitle { font-size: 12px; color: #8390A5; padding-top: 5px; }
QFrame#navPanel { background: #152137; border: none; }
QLabel#navBrand { color: #F3F7FF; font-size: 16px; font-weight: 700; letter-spacing: 1px; }
QLabel#navCaption { color: #8292AE; font-size: 11px; padding-top: 4px; }
QLabel#navLabel { color: #788AA7; font-size: 11px; padding: 0 12px 8px; }
QLabel#navHint { color: #8292AE; font-size: 11px; padding: 16px 8px 0; border-top: 1px solid #2A364C; }
QPushButton { background: white; border: 1px solid #DEE4EF; border-radius: 8px;
    min-height: 36px; padding: 0 13px; font-weight: 500; }
QPushButton:hover { background: #F0F4FF; border-color: #B5C7F5; color: #386CF4; }
QPushButton:pressed { background: #E5ECFC; }
QPushButton:disabled { color: #A0AABE; border-color: #E8ECF3; background: #F5F7FA; }
QPushButton#primaryButton, QPushButton#queuePrimary { background: #386CF4; color: white; border-color: #386CF4; font-weight: 600; }
QPushButton#primaryButton:hover, QPushButton#queuePrimary:hover { background: #2859DC; border-color: #2859DC; }
QPushButton#primaryButton:disabled { background: #E8ECF3; border-color: #E8ECF3; color: #A0AABE; }
QPushButton#dangerButton { color: #B96269; background: #FFF7F7; border-color: #F2DFE1; }
QPushButton#dangerButton:disabled { color: #A0AABE; background: #F5F7FA; border-color: #E8ECF3; }
QPushButton#settingsButton { min-width: 36px; padding: 0; }
QPushButton#navButton { background: transparent; color: #AAB9D4; border: none; border-radius: 8px;
    min-height: 44px; padding: 0 12px; text-align: left; font-weight: 500; }
QPushButton#navButton:hover { color: #FFFFFF; background: #202F49; }
QPushButton#navButton:checked { color: #FFFFFF; background: #2D4674; font-weight: 600; }
QFrame#sidePanel, QFrame#videoSidePanel { background: #FFFFFF; border: 1px solid #E4E9F2; border-radius: 12px; }
QScrollArea#settingsScroll { background: transparent; border: none; }
QFrame#editorWorkspace, QFrame#queuePanel { background: #FFFFFF; border: 1px solid #E4E9F2; border-radius: 12px; }
QLabel#workspaceTitle { font-size: 16px; font-weight: 600; color: #23314B; }
QLabel#workspaceCaption { font-size: 10px; letter-spacing: 2px; color: #9AA6B9; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; color: #25324A; }
QLabel#sectionSubtitle, QLabel#panelSubtitle, QLabel#metricLabel { color: #8B97AA; font-size: 11px; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #F8FAFD; border: 1px solid #E3E8F1;
    border-radius: 7px; min-height: 30px; padding: 3px 10px; selection-background-color: #386CF4; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: #B9C7DF; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #7C9CF4; background: #FFFFFF; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(CHEVRON_PATH); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #DEE4EF; selection-background-color: #EAF0FF; selection-color: #386CF4; padding: 4px; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #CED7E6; border-radius: 5px; background: #FFFFFF; }
QCheckBox::indicator:checked { background: #386CF4; border-color: #386CF4; image: url(CHECK_ICON_PATH); }
QTabWidget::pane { border: none; background: #FFFFFF; }
QTabBar::tab { background: transparent; color: #8995A8; padding: 11px 16px; margin-right: 8px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #386CF4; border-bottom: 2px solid #386CF4; font-weight: 600; }
QTabBar::tab:hover { color: #386CF4; background: #F8FAFF; }
QPlainTextEdit, QTextBrowser { background: #FFFFFF; color: #33425C; border: none; border-radius: 8px;
    padding: 18px 8px; selection-background-color: #DFE8FF; selection-color: #253655; font-size: 14px; }
QToolButton#disclosure, QToolButton#queueLogToggle { background: transparent; border: none; text-align: left;
    padding: 8px 0; color: #7C89A0; font-size: 12px; }
QToolButton#disclosure:hover, QToolButton#queueLogToggle:hover { color: #386CF4; }
QFrame#divider, QFrame#queueRule { background: #EBEFF5; border: none; max-height: 1px; min-height: 1px; }
QFrame#statusPanel { background: #F7F9FD; border: none; border-radius: 9px; }
QFrame#metric { background: transparent; border: none; }
QLabel#metricValue { color: #344B73; font-size: 19px; font-weight: 600; }
QLabel#statusBadge { font-size: 11px; min-height: 28px; padding: 0 11px; border-radius: 14px; }
QStatusBar#bottomStatus { background: #F3F5F9; border: none; color: #8D99AC; font-size: 11px; }
QStatusBar::item { border: none; }
QSplitter::handle { background: transparent; width: 12px; height: 10px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #D5DCE8; border-radius: 3px; min-height: 35px; }
QScrollBar::handle:vertical:hover { background: #A9B8D0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 7px; background: transparent; }
QScrollBar::handle:horizontal { background: #D5DCE8; min-width: 30px; border-radius: 3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLabel#usageToast { color: #FFFFFF; background: #243550; padding: 14px 20px; border-radius: 10px; }
"""

QUEUE_STYLE = """
QWidget#conversionQueue, QDialog#taskEditor { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #24324B; }
QLabel#queueTitle { font-size: 19px; font-weight: 600; }
QLabel#queueDetailTitle { font-size: 18px; font-weight: 600; }
QLabel#queueMuted { color: #8995A8; font-size: 11px; }
QLabel#queueRowTitle { font-size: 13px; font-weight: 600; }
QTableWidget#queueList { background: #FFFFFF; border: none; gridline-color: #FFFFFF; outline: none; }
QTableWidget#queueList::item { border: none; border-bottom: 1px solid #EEF1F6; padding: 0; }
QTableWidget#queueList::item:selected { background: #EDF2FF; color: #386CF4; }
QWidget#queueRow { background: transparent; border: none; }
QHeaderView::section { background: #F9FAFD; color: #8995A8; border: none; padding: 12px; font-size: 11px; }
QTextBrowser#queueReader { font-size: 14px; padding: 10px; }
QPlainTextEdit#queueLogs { background: #F7F9FD; color: #8290A6; font-size: 11px; padding: 10px; }
"""

APP_STYLE = APP_STYLE.replace("CHECK_ICON_PATH", (Path(__file__).parent / "assets" / "check.svg").as_posix())

APP_STYLE = APP_STYLE.replace("CHEVRON_PATH", (Path(__file__).parent / "assets" / "chevron.svg").as_posix())
