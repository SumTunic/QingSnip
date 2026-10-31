"""Short native Qt regression; no desktop input is injected."""
import json
import os
import ctypes as C

os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from snip.config import Settings
from snip.geometry import Rect
from snip.native import ENUMPROC, enable_dpi_awareness, user32, virtual_bounds
from snip.overlay import CaptureSession
from snip.settings_ui import SettingsDialog

enable_dpi_awareness()
app = QApplication([])
app.setApplicationName("QingSnip")
app.setApplicationDisplayName("轻截")
app.setQuitOnLastWindowClosed(False)
settings_dialog = SettingsDialog(Settings())
user32.GetWindowTextW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_int]
caption = C.create_unicode_buffer(256)
user32.GetWindowTextW(int(settings_dialog.winId()), caption, len(caption))
bounds = virtual_bounds()
image = QImage(bounds.w, bounds.h, QImage.Format.Format_RGB32)
image.fill(QColor("#e8edf2"))

class Detector(QObject):
    ready = Signal(int, int, object)
    def request(self, *args):
        pass

detector = Detector()
session = CaptureSession(image, bounds, [], Settings(), detector, 1)
report = {"caption_not_duplicated": caption.value.count("轻截") == 1 and caption.value.startswith("设置")}

def finish():
    settings_dialog.deleteLater()
    session.dispose()
    app.quit()

def inspect():
    try:
        canvas = session.canvases[0]
        toolbar_handle, canvas_handle = int(session.toolbar.winId()), int(canvas.winId())
        ordering = []

        @ENUMPROC
        def enumerate_own(hwnd, _):
            if hwnd in (toolbar_handle, canvas_handle):
                ordering.append(hwnd)
            return True

        user32.EnumWindows(enumerate_own, 0)
        report["toolbar_above_canvas"] = ordering.index(toolbar_handle) < ordering.index(canvas_handle)
        report["toolbar_visible"] = session.toolbar.isVisible()
        report["text_has_focus"] = session.text_editor.hasFocus()
        report["undo_enabled_while_typing"] = session.toolbar.undo_button.isEnabled()
        session.toolbar.undo_button.click()
        report["undo_removed_text"] = not session.annotations and session.text_editor is None
        session.toolbar.redo_button.click()
        report["redo_restored_text"] = len(session.annotations) == 1 and session.annotations[0].text == "Text object test"
    except Exception as error:
        report["error"] = str(error)
    finally:
        print(json.dumps(report), flush=True)
        finish()

def begin():
    canvas = session.canvases[0]
    session.begin_text(canvas, bounds.x + 250, bounds.y + 250)
    session.text_editor.insertPlainText("Text object test")
    canvas.activateWindow()
    canvas.raise_()
    QTimer.singleShot(200, inspect)

session.show()
session.select(Rect(bounds.x + 200, bounds.y + 150, min(800, bounds.w - 250), min(500, bounds.h - 200)))
QTimer.singleShot(150, begin)
QTimer.singleShot(3000, finish)
app.exec()
raise SystemExit(0 if len(report) == 7 and all(report.values()) else 1)
