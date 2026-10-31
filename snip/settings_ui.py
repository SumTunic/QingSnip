from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QKeySequenceEdit, QLineEdit,
                               QPushButton, QVBoxLayout)

from .config import Settings

STYLE = """
QDialog { background: #0e1724; color: #e7eef6; }
QLabel { color: #e7eef6; font-size: 13px; }
QLabel#muted { color: #9baec3; font-size: 12px; }
QLabel#title { font-size: 28px; font-weight: bold; }
QLabel#eyebrow { color: #36e7b4; font-size: 12px; font-weight: bold; }
QFrame#card { background: #162335; border: 1px solid #293b50; border-radius: 10px; }
QCheckBox { color: #e7eef6; spacing: 9px; font-size: 13px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QLineEdit, QComboBox, QKeySequenceEdit { background: #0d1928; color: #f1f5f9; padding: 9px; border: 1px solid #34485f; border-radius: 6px; min-height: 20px; }
QComboBox QAbstractItemView { background: #162335; color: white; selection-background-color: #315468; }
QPushButton { color: #dde7f1; background: #263a50; border: none; border-radius: 6px; padding: 10px 18px; }
QPushButton:hover { background: #35536c; }
QPushButton#primary { background: #36e7b4; color: #072d22; font-weight: bold; }
QPushButton#primary:hover { background: #74f0cd; }
QPushButton:disabled { background: #283341; color: #718193; }
"""


class SingleKeySequenceEdit(QKeySequenceEdit):
    def keyPressEvent(self, event):
        # PrintScreen is emitted on release on some keyboards. Native registration
        # still uses VK_SNAPSHOT; this branch lets users set it by pressing it.
        if event.key() == Qt.Key.Key_Print:
            self.setKeySequence(QKeySequence(Qt.Key.Key_Print))
            event.accept()
        else:
            super().keyPressEvent(event)


class SettingsDialog(QDialog):
    requested = Signal(object)
    preferences_changed = Signal(object)
    capture_requested = Signal()
    folder_requested = Signal(str)
    startup_requested = Signal(bool)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        # Qt adds the application display name to the native caption.
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        self.label(layout, "轻截", "title")

        card, form = self.card(layout)
        self.shortcut_card = card
        self.label(form, "快捷键", "eyebrow")
        row = QHBoxLayout()
        row.addWidget(QLabel("键盘快捷键"))
        self.keyboard = SingleKeySequenceEdit(QKeySequence(settings.keyboard))
        self.keyboard.setMaximumSequenceLength(1)
        self.keyboard.setToolTip("点击后按下快捷键，支持 PrintScreen")
        row.addWidget(self.keyboard, 1)
        form.addLayout(row)
        mouse_row = QHBoxLayout()
        mouse_row.addWidget(QLabel("鼠标快捷键"))
        self.mouse_modifiers = QComboBox()
        for label in ("无修饰键", "Ctrl", "Alt", "Shift", "Ctrl+Alt", "Ctrl+Shift", "Alt+Shift", "Ctrl+Alt+Shift", "Win"):
            self.mouse_modifiers.addItem(label, "" if label == "无修饰键" else label)
        index = self.mouse_modifiers.findData(settings.mouse_modifiers)
        self.mouse_modifiers.setCurrentIndex(max(0, index))
        self.mouse_button = QComboBox()
        for label, value in (("关闭", ""), ("鼠标中键", "middle"), ("侧键 X1", "x1"), ("侧键 X2", "x2")):
            self.mouse_button.addItem(label, value)
        self.mouse_button.setCurrentIndex(max(0, self.mouse_button.findData(settings.mouse_button)))
        mouse_row.addWidget(self.mouse_modifiers, 1)
        mouse_row.addWidget(self.mouse_button, 1)
        form.addLayout(mouse_row)
        shortcut_buttons = QHBoxLayout()
        self.shortcut_hint = QLabel("修改后点击应用")
        self.shortcut_hint.setWordWrap(True)
        self.shortcut_hint.setObjectName("muted")
        shortcut_buttons.addWidget(self.shortcut_hint, 1)
        self.save_button = QPushButton("应用快捷键")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.apply)
        shortcut_buttons.addWidget(self.save_button)
        form.addLayout(shortcut_buttons)

        card, form = self.card(layout)
        self.label(form, "常规", "eyebrow")
        options = QGridLayout()
        options.setHorizontalSpacing(22)
        options.setVerticalSpacing(12)
        options.setColumnStretch(0, 1)
        options.setColumnStretch(1, 1)
        form.addLayout(options)
        self.auto_start = QCheckBox("开机自动启动")
        self.auto_start.setToolTip("登录 Windows 后自动运行，常驻托盘")
        options.addWidget(self.auto_start, 0, 0)
        self.startup_message = QLabel("")
        self.startup_message.setWordWrap(True)
        self.startup_message.setStyleSheet("color: #ffc857; font-size: 12px;")
        self.startup_message.hide()
        form.addWidget(self.startup_message)
        self.smart = QCheckBox("智能识别区域")
        self.smart.setToolTip("滚轮切换候选区域，按住 Alt 选择整个窗口")
        self.smart.setChecked(settings.smart_detect)
        options.addWidget(self.smart, 0, 1)
        self.right_click_cancel = QCheckBox("右键取消截图")
        self.right_click_cancel.setChecked(settings.right_click_cancel)
        self.right_click_cancel.setToolTip("修改后立即生效")
        options.addWidget(self.right_click_cancel, 1, 0)
        self.hide_on_capture = QCheckBox("截图时隐藏窗口")
        self.hide_on_capture.setToolTip("关闭后可截取轻截窗口")
        self.hide_on_capture.setChecked(settings.hide_on_capture)
        options.addWidget(self.hide_on_capture, 1, 1)

        card, form = self.card(layout)
        self.label(form, "图片保存", "eyebrow")
        self.auto_save = QCheckBox("自动保存 PNG")
        self.auto_save.setChecked(settings.auto_save)
        form.addWidget(self.auto_save)
        path_row = QHBoxLayout()
        self.path = QLineEdit(settings.save_dir)
        self.path.setPlaceholderText("截图保存文件夹")
        self.path.setToolTip("选择文件夹，或输入路径后按 Enter / 点击其他位置自动生效")
        browse = QPushButton("选择文件夹")
        browse.clicked.connect(self.browse)
        open_folder = QPushButton("打开文件夹")
        open_folder.setObjectName("openFolder")
        open_folder.clicked.connect(self.open_folder)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        form.addLayout(path_row)
        open_row = QHBoxLayout()
        open_row.addStretch()
        open_row.addWidget(open_folder)
        form.addLayout(open_row)
        self.label(layout, "Enter 完成并复制 · Esc 取消 · Ctrl+S 保存", "muted")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #ffc857; font-size: 12px;")
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        capture = QPushButton("立即截图")
        capture.clicked.connect(self.capture_requested.emit)
        buttons.addWidget(capture)
        buttons.addStretch()
        hide = QPushButton("收起到托盘")
        hide.clicked.connect(self.hide)
        buttons.addWidget(hide)
        layout.addLayout(buttons)
        self.auto_start.toggled.connect(self.startup_requested.emit)
        self.keyboard.keySequenceChanged.connect(self.mark_shortcuts_changed)
        self.mouse_modifiers.currentIndexChanged.connect(self.mark_shortcuts_changed)
        self.mouse_button.currentIndexChanged.connect(self.mark_shortcuts_changed)
        for checkbox in (self.smart, self.right_click_cancel, self.hide_on_capture, self.auto_save):
            checkbox.toggled.connect(self.update_preferences)
            checkbox.setToolTip((checkbox.toolTip() + "\n" if checkbox.toolTip() else "") + "修改后立即生效")
        self.path.editingFinished.connect(self.update_preferences)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def label(self, layout, text, name=None):
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def card(self, parent):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        parent.addWidget(card)
        return card, layout

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择截图保存文件夹", self.path.text())
        if path:
            self.path.setText(path)
            self.update_preferences()

    def preferences(self) -> Settings:
        return replace(self.settings, smart_detect=self.smart.isChecked(),
                       right_click_cancel=self.right_click_cancel.isChecked(),
                       hide_on_capture=self.hide_on_capture.isChecked(),
                       auto_save=self.auto_save.isChecked(),
                       save_dir=self.path.text().strip() or self.settings.save_dir)

    def open_folder(self):
        self.update_preferences()
        self.folder_requested.emit(self.path.text().strip())

    def update_preferences(self):
        candidate = self.preferences()
        if candidate != self.settings:
            self.preferences_changed.emit(candidate)

    def set_status(self, text: str):
        self.message.setText(text)

    def set_startup_state(self, enabled, error=""):
        previous = self.auto_start.blockSignals(True)
        self.auto_start.setChecked(enabled)
        self.auto_start.blockSignals(previous)
        self.startup_message.setText(error)
        self.startup_message.setVisible(bool(error))

    def mark_shortcuts_changed(self):
        self.shortcut_hint.setText("有更改，点击应用后生效")

    def apply(self):
        keyboard = self.keyboard.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        # Applying shortcuts must not validate or commit unrelated preferences.
        candidate = replace(self.settings, keyboard=keyboard,
                            mouse_button=self.mouse_button.currentData(),
                            mouse_modifiers=self.mouse_modifiers.currentData())
        self.save_button.setEnabled(False)
        self.shortcut_hint.setText("正在注册快捷键…")
        self.requested.emit(candidate)

    def result(self, ok, message, settings=None):
        self.save_button.setEnabled(True)
        self.shortcut_hint.setText("快捷键已生效" if ok else message)
        self.shortcut_hint.setWordWrap(True)
        if ok and settings is not None:
            self.settings = settings
