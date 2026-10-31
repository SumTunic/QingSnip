from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton

from snip.annotations import Annotation
from snip.app import Application
from snip.config import Settings
from snip.geometry import Rect
from snip.overlay import CaptureSession


class FakeDetector(QObject):
    ready = Signal(int, int, object)

    def request(self, *args):
        pass


@pytest.fixture
def session(qt_app):
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    detector = FakeDetector()
    result = CaptureSession(image, Rect(0, 0, 300, 200), [], Settings(), detector, 1)
    yield result
    result.dispose()
    qt_app.processEvents()


def left_event():
    return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton)


def test_reverse_drag_and_copy_share_final_pixels(session, monkeypatch):
    import snip.overlay as overlay
    canvas = session.canvases[0]
    monkeypatch.setattr(overlay, "cursor_position", lambda: (200, 160))
    canvas.mousePressEvent(left_event())
    monkeypatch.setattr(overlay, "cursor_position", lambda: (40, 30))
    canvas.mouseMoveEvent(left_event())
    canvas.mouseReleaseEvent(left_event())
    assert session.selection == Rect(40, 30, 160, 130)
    session.annotations.append(Annotation("arrow", [(50, 40), (150, 120)]))
    images = []
    session.finished.connect(lambda image, save_only: images.append((image, save_only)))
    session.complete()
    assert len(images) == 1
    assert images[0][0].size().toTuple() == (160, 130)
    assert images[0][1] is False
    assert session.closed


def test_move_keeps_annotations_relative_and_clamps(session, monkeypatch):
    import snip.overlay as overlay
    canvas = session.canvases[0]
    session.select(Rect(30, 30, 100, 80))
    mark = Annotation("rect", [(40, 40), (80, 70)])
    session.annotations.append(mark)
    monkeypatch.setattr(overlay, "cursor_position", lambda: (80, 70))
    canvas.mousePressEvent(left_event())
    monkeypatch.setattr(overlay, "cursor_position", lambda: (500, 500))
    canvas.mouseMoveEvent(left_event())
    canvas.mouseReleaseEvent(left_event())
    assert session.selection == Rect(200, 120, 100, 80)
    assert mark.points == [(210, 130), (250, 160)]


def test_manual_save_leaves_capture_open_and_undo_redo(session):
    session.select(Rect(20, 20, 100, 100))
    mark = Annotation("ellipse", [(30, 30), (80, 80)])
    session.add_annotation(mark)
    session.undo()
    assert session.annotations == []
    session.redo()
    assert session.annotations == [mark]
    results = []
    session.finished.connect(lambda image, save_only: results.append(save_only))
    session.complete(True)
    assert results == [True]
    assert not session.closed
    session.hide()
    session.hide()  # cleanup after complete/cancel must be idempotent


@pytest.mark.parametrize("automatic", [False, True])
def test_completion_copies_and_only_saves_when_enabled(qt_app, tmp_path, automatic):
    image = QImage(35, 25, QImage.Format.Format_RGB32)
    image.fill(QColor("#36e7b4"))
    notices, cleanups = [], []
    path = tmp_path / "output.png"
    app = SimpleNamespace(qt=qt_app, settings=Settings(auto_save=automatic), next_filename=lambda: path,
                          dialog=SimpleNamespace(set_status=lambda text: None),
                          cleanup=lambda: cleanups.append(True), notify=lambda *args: notices.append(args))
    Application.on_finished(app, image, False)
    assert qt_app.clipboard().image().size() == image.size()
    assert qt_app.clipboard().image().pixelColor(0, 0) == QColor("#36e7b4")
    assert path.exists() is automatic
    assert cleanups == [True]
    assert notices == []


def test_save_failure_preserves_clipboard(qt_app, tmp_path):
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    notices = []
    app = SimpleNamespace(qt=qt_app, settings=Settings(auto_save=True), next_filename=lambda: blocker / "output.png",
                          dialog=SimpleNamespace(set_status=lambda text: None),
                          cleanup=lambda: None, notify=lambda *args: notices.append(args))
    Application.on_finished(app, image, False)
    assert qt_app.clipboard().image().pixelColor(0, 0) == QColor("red")
    assert len(notices) == 1
    assert "已复制" in notices[0][0]


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("surface", ["canvas", "toolbar", "button"])
def test_right_click_respects_setting_on_every_capture_surface(session, enabled, surface):
    session.settings.right_click_cancel = enabled
    session.select(Rect(20, 20, 100, 100))
    target = {"canvas": session.canvases[0], "toolbar": session.toolbar,
              "button": session.toolbar.findChildren(QPushButton)[0]}[surface]
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)
    assert not session.closed
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                          Qt.MouseButton.RightButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, release)
    assert not session.closed
    QApplication.processEvents()
    assert session.closed is enabled


def test_escape_cancels_with_right_click_disabled(session):
    session.settings.right_click_cancel = False
    session.key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert session.closed


@pytest.mark.parametrize("hide,visible,transition_ok,delay", [
    (True, True, True, 100), (True, True, False, 300),
    (False, True, True, 35), (True, False, True, 35),
])
def test_capture_hides_and_flushes_before_reading_pixels(monkeypatch, hide, visible, transition_ok, delay):
    import snip.app as appmod
    calls, scheduled = [], []
    dialog = SimpleNamespace(isVisible=lambda: visible, winId=lambda: 42,
                             set_status=lambda text: None,
                             hide=lambda: calls.append("hide"), show=lambda: calls.append("restore"),
                             setAttribute=lambda *args: None)
    app = SimpleNamespace(session=None, busy=False, last_capture=0, settings=Settings(hide_on_capture=hide),
                          dialog=dialog, menu=SimpleNamespace(hide=lambda: None), _hidden_window=None,
                          _restore_dialog=False, generation=0, detector=None, on_finished=lambda *args: None,
                          notify=lambda *args: calls.append("error"))
    app._capture = lambda: Application._capture(app)
    app.restore_dialog = lambda: Application.restore_dialog(app)
    app.cleanup = lambda: Application.cleanup(app)
    app.on_cancelled = lambda: Application.on_cancelled(app)
    signal = SimpleNamespace(connect=lambda callback: None)

    def new_session(*args):
        return SimpleNamespace(finished=signal, cancelled=signal, show=lambda: calls.append("overlay"),
                               dispose=lambda: calls.append("dispose"))

    def transitions(hwnd, disabled):
        calls.append(("transitions", disabled))
        return transition_ok

    monkeypatch.setattr(appmod.QTimer, "singleShot", lambda ms, cb: scheduled.append((ms, cb)))
    monkeypatch.setattr(appmod, "set_window_transitions_disabled", transitions)
    monkeypatch.setattr(appmod, "flush_desktop", lambda: calls.append("flush"))
    monkeypatch.setattr(appmod, "list_windows", lambda include_own=False: calls.append(("windows", include_own)) or [])
    monkeypatch.setattr(appmod, "capture_desktop", lambda: (calls.append("pixels") or None, Rect(0, 0, 100, 100)))
    monkeypatch.setattr(appmod, "CaptureSession", new_session)
    Application.capture(app)
    assert scheduled[0][0] == delay
    assert ("hide" in calls) is (hide and visible)
    assert "pixels" not in calls
    scheduled[0][1]()
    assert "error" not in calls
    assert ("flush" in calls) is (hide and visible)
    if hide and visible:
        assert calls.index("hide") < calls.index("flush") < calls.index("pixels")
        assert calls.index("pixels") < calls.index(("transitions", False))
    assert ("windows", not hide) in calls
    assert not app.busy
    Application.cleanup(app)
    assert ("restore" in calls) is (hide and visible)
