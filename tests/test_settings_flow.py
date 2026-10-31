"""Exercise real widgets -> controller -> capture -> disk, not just signal payloads."""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton

from snip.app import Application
from snip.config import Settings
from snip.geometry import Rect
from snip.native import Window


class FakeHotkeys(QObject):
    triggered = Signal()
    registered = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.reject = False
        self.calls = []
        self.defer = False

    def configure(self, *args):
        self.calls.append(args)
        if not self.defer:
            self.registered.emit(not self.reject, "测试：快捷键冲突" if self.reject else "")

    def stop(self):
        pass


class FakeDetector(QObject):
    ready = Signal(int, int, object)

    def request(self, *args):
        pass


class FakeStartup:
    def __init__(self, path):
        self.enabled = False
        self.fail = False
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.calls.append(enabled)
        if self.fail:
            raise PermissionError("test permission denied")
        self.enabled = enabled


@pytest.fixture
def controller(qt_app, tmp_path, monkeypatch):
    import snip.app as appmod
    monkeypatch.setattr(appmod, "Hotkeys", FakeHotkeys)
    monkeypatch.setattr(appmod, "Detector", FakeDetector)
    monkeypatch.setattr(appmod, "StartupRegistration", FakeStartup)
    result = Application(qt_app, tmp_path / "settings.json")
    yield result
    result.shutdown()
    qt_app.aboutToQuit.disconnect(result.shutdown)
    result.dialog.deleteLater()
    result.menu.deleteLater()
    result.deleteLater()
    qt_app.processEvents()


@pytest.mark.parametrize("widget,field", [("smart", "smart_detect"), ("right_click_cancel", "right_click_cancel"),
                                           ("hide_on_capture", "hide_on_capture"), ("auto_save", "auto_save")])
def test_checkboxes_apply_without_save_or_registering_hotkeys(controller, widget, field):
    checkbox = getattr(controller.dialog, widget)
    original_calls = len(controller.hotkeys.calls)
    for checked in (False, True, False):
        checkbox.setChecked(checked)
        assert getattr(controller.settings, field) is checked
        assert getattr(Settings.load(controller.config_path), field) is checked
        assert len(controller.hotkeys.calls) == original_calls


def test_failed_hotkey_does_not_block_other_settings(controller):
    controller.hotkeys.reject = True
    controller.configure(replace(controller.settings, keyboard="Ctrl+Alt+F10"))
    assert controller.settings.keyboard == "Ctrl+Shift+A"
    controller.dialog.smart.setChecked(False)
    controller.dialog.auto_save.setChecked(True)
    persisted = Settings.load(controller.config_path)
    assert not persisted.smart_detect
    assert persisted.auto_save
    assert persisted.keyboard == "Ctrl+Shift+A"


def test_preferences_changed_during_registration_are_not_overwritten(controller):
    controller.hotkeys.defer = True
    controller.configure(replace(controller.settings, keyboard="Ctrl+Alt+F10"))
    controller.dialog.right_click_cancel.setChecked(False)
    controller.dialog.hide_on_capture.setChecked(False)
    controller.hotkeys.registered.emit(True, "")
    assert not controller.settings.right_click_cancel
    assert not controller.settings.hide_on_capture
    assert controller.settings.keyboard == "Ctrl+Alt+F10"
    assert Settings.load(controller.config_path) == controller.settings


def prepare_capture(controller, monkeypatch):
    import snip.app as appmod
    image = QImage(180, 160, QImage.Format.Format_RGB32)
    image.fill(QColor("#36e7b4"))
    window = Window(123, Rect(0, 0, 180, 160))
    monkeypatch.setattr(appmod, "capture_desktop", lambda: (image, window.rect))
    monkeypatch.setattr(appmod, "list_windows", lambda **kwargs: [window])
    controller.busy = True
    controller._capture()
    assert controller.session is not None
    return controller.session


def test_smart_checkbox_reaches_capture_session(controller, monkeypatch):
    import snip.overlay as overlay
    controller.dialog.smart.setChecked(False)
    session = prepare_capture(controller, monkeypatch)
    monkeypatch.setattr(overlay, "cursor_position", lambda: (80, 80))
    session.refresh_hover()
    assert session.hover is None
    assert not session.settings.smart_detect


@pytest.mark.parametrize("automatic", [False, True])
def test_auto_save_from_checkbox_and_directory_to_actual_png(controller, monkeypatch, tmp_path, automatic):
    folder = tmp_path / "截屏目录"
    controller.dialog.path.setText(str(folder))
    controller.dialog.path.editingFinished.emit()
    controller.dialog.auto_save.setChecked(automatic)
    session = prepare_capture(controller, monkeypatch)
    session.select(Rect(20, 30, 100, 80))
    session.complete()
    assert controller.session is None
    files = list(folder.glob("*.png"))
    assert len(files) == (1 if automatic else 0)
    if files:
        result = QImage(str(files[0]))
        assert result.size().toTuple() == (100, 80)
        assert result.pixelColor(0, 0) == QColor("#36e7b4")
    assert controller.qt.clipboard().image().size().toTuple() == (100, 80)


def test_right_cancel_never_saves_image_or_changes_clipboard(controller, monkeypatch, tmp_path):
    folder = tmp_path / "取消后不应写入"
    controller.dialog.path.setText(str(folder))
    controller.dialog.path.editingFinished.emit()
    controller.dialog.auto_save.setChecked(True)
    controller.dialog.result(True, "", controller.settings)
    assert controller.dialog.shortcut_hint.text() == "快捷键已生效"
    controller.qt.clipboard().setText("keep existing clipboard")
    session = prepare_capture(controller, monkeypatch)
    session.select(Rect(20, 30, 100, 80))
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(session.canvases[0], event)
    assert controller.session is session
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                          Qt.MouseButton.RightButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(session.canvases[0], release)
    QApplication.processEvents()
    assert controller.session is None
    assert controller.dialog.message.text() == "截图已取消"
    assert not list(folder.glob("*.png"))
    assert controller.qt.clipboard().text() == "keep existing clipboard"


def test_right_cancel_disabled_from_checkbox_reaches_capture(controller, monkeypatch):
    controller.dialog.right_click_cancel.setChecked(False)
    session = prepare_capture(controller, monkeypatch)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(session.canvases[0], event)
    assert not session.closed
    assert controller.session is session


def test_open_folder_uses_directory_shown_in_settings(controller, monkeypatch, tmp_path):
    import snip.app as appmod
    opened = []
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    folder = tmp_path / "新选择的保存目录"
    controller.dialog.path.setText(str(folder))
    controller.dialog.findChild(QPushButton, "openFolder").click()
    assert [Path(path) for path in opened] == [folder.resolve()]
    assert folder.is_dir()
    assert controller.settings.save_dir == str(folder)


def test_settings_buttons_cannot_be_triggered_as_dialog_defaults(controller):
    assert all(not button.autoDefault() and not button.isDefault() for button in controller.dialog.findChildren(QPushButton))
    original = len(controller.hotkeys.calls)
    QApplication.sendEvent(controller.dialog, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert len(controller.hotkeys.calls) == original


@pytest.mark.parametrize("hide", [True, False])
def test_hide_checkbox_changes_immediate_capture_and_cancel_restores_window(controller, monkeypatch, hide):
    import snip.app as appmod
    image = QImage(180, 160, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    scheduled = []
    monkeypatch.setattr(appmod, "capture_desktop", lambda: (image, Rect(0, 0, 180, 160)))
    monkeypatch.setattr(appmod, "list_windows", lambda **kwargs: [])
    monkeypatch.setattr(appmod, "set_window_transitions_disabled", lambda *args: True)
    monkeypatch.setattr(appmod, "flush_desktop", lambda: None)
    monkeypatch.setattr(appmod.QTimer, "singleShot", lambda delay, cb: scheduled.append(cb))
    controller.dialog.show()
    controller.dialog.hide_on_capture.setChecked(hide)
    controller.dialog.set_status("设置已保存")
    controller.capture()
    assert controller.dialog.message.text() == ""
    assert controller.dialog.isVisible() is not hide
    scheduled[0]()
    assert controller.session.settings.hide_on_capture is hide
    controller.session.cancel()
    assert controller.dialog.isVisible()
    assert controller.dialog.message.text() == "截图已取消"


def test_open_folder_failure_has_actionable_error(controller, monkeypatch, tmp_path):
    import snip.app as appmod
    errors = []
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: False)
    monkeypatch.setattr(controller, "notify", lambda *args: errors.append(args))
    controller.open_folder(str(tmp_path))
    assert len(errors) == 1
    assert "无法打开" in controller.dialog.message.text()


def test_startup_toggle_applies_immediately_without_hotkey_registration(controller):
    before = len(controller.hotkeys.calls)
    controller.dialog.auto_start.setChecked(True)
    assert controller.startup.enabled
    assert controller.dialog.auto_start.isChecked()
    controller.dialog.auto_start.setChecked(False)
    assert not controller.startup.enabled
    assert controller.startup.calls == [True, False]
    assert len(controller.hotkeys.calls) == before


def test_startup_failure_restores_checkbox_and_does_not_block_other_preferences(controller):
    controller.startup.fail = True
    controller.dialog.auto_start.setChecked(True)
    assert not controller.dialog.auto_start.isChecked()
    assert controller.startup.calls == [True]
    assert "失败" in controller.dialog.startup_message.text()
    controller.dialog.smart.setChecked(False)
    assert not controller.settings.smart_detect


def test_apply_button_is_next_to_shortcuts_and_does_not_require_folder(controller):
    from PySide6.QtGui import QKeySequence
    dialog = controller.dialog
    assert dialog.shortcut_card.isAncestorOf(dialog.save_button)
    assert dialog.save_button.text() == "应用快捷键"
    before = len(controller.hotkeys.calls)
    dialog.keyboard.setKeySequence(QKeySequence("Ctrl+Alt+F10"))
    assert "有更改" in dialog.shortcut_hint.text()
    assert len(controller.hotkeys.calls) == before
    dialog.path.setText("")
    dialog.save_button.click()
    assert controller.settings.keyboard == "Ctrl+Alt+F10"
    assert dialog.shortcut_hint.text() == "快捷键已生效"
    assert controller.settings.save_dir


def test_background_shortcut_error_keeps_settings_window_hidden(controller, monkeypatch):
    notices = []
    controller.background = True
    controller.hotkeys.reject = True
    monkeypatch.setattr(controller, "notify", lambda *args: notices.append(args))
    controller.configure(controller.settings, feedback=False)
    assert not controller.dialog.isVisible()
    assert notices and notices[0][0] == "快捷键未生效"
