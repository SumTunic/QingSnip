from __future__ import annotations

import ctypes as C
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from .config import Settings, settings_path
from .detection import Detector
from .hotkeys import Hotkeys
from .native import capture_desktop, enable_dpi_awareness, flush_desktop, list_windows, set_window_transitions_disabled
from .overlay import CaptureSession
from .settings_ui import SettingsDialog
from .startup import StartupRegistration

log = logging.getLogger(__name__)


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#132b2b"))
    painter.drawRoundedRect(QRectF(2, 2, 60, 60), 15, 15)
    painter.setPen(QPen(QColor("#36e7b4"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for a, b, c in (((17, 29), (17, 17), (29, 17)), ((35, 17), (47, 17), (47, 29)),
                     ((17, 35), (17, 47), (29, 47)), ((35, 47), (47, 47), (47, 35))):
        painter.drawLine(*a, *b)
        painter.drawLine(*b, *c)
    painter.end()
    return QIcon(pixmap)


def save_png(image: QImage, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Encode before replacing so partial files never look like valid screenshots.
    temporary = path.with_name(path.name + ".tmp")
    if not image.save(str(temporary), "PNG"):
        raise OSError(f"无法写入图片：{path}")
    temporary.replace(path)


class Application(QObject):
    def __init__(self, qt: QApplication, config_path: Path, background=False):
        super().__init__()
        self.qt, self.config_path = qt, config_path
        self.background = background
        self.startup = StartupRegistration(config_path)
        self.settings = Settings.load(config_path)
        self.pending_settings = None
        self._registration_feedback = False
        self.session = None
        self.busy = False
        self.generation = 0
        self.last_capture = 0
        self._hidden_window = None
        self._restore_dialog = False
        self.detector = Detector()
        self.hotkeys = Hotkeys()
        self.hotkeys.triggered.connect(self.capture)
        self.hotkeys.registered.connect(self.registration_result)
        self.dialog = SettingsDialog(self.settings)
        self.dialog.setWindowIcon(app_icon())
        self.dialog.requested.connect(self.configure)
        self.dialog.preferences_changed.connect(self.update_preferences)
        self.dialog.capture_requested.connect(self.capture)
        self.dialog.folder_requested.connect(self.open_folder)
        self.dialog.startup_requested.connect(self.set_startup)
        try:
            self._startup_enabled = self.startup.is_enabled()
            self.dialog.set_startup_state(self._startup_enabled)
        except OSError as error:
            self._startup_enabled = False
            self.dialog.set_startup_state(False, f"无法读取开机启动设置：{error}")
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"轻截 · {self.settings.keyboard}")
        menu = QMenu()
        for text, callback in (("立即截图", self.capture), ("设置…", self.show_settings), ("打开保存文件夹", self.open_folder)):
            action = QAction(text, menu)
            action.triggered.connect(lambda checked=False, callback=callback: callback())
            menu.addAction(action)
        menu.addSeparator()
        action = QAction("退出轻截", menu)
        action.triggered.connect(qt.quit)
        menu.addAction(action)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray_click_timer = QTimer(self)
        self.tray_click_timer.setSingleShot(True)
        self.tray_click_timer.timeout.connect(self.capture)
        self.tray.show()
        qt.aboutToQuit.connect(self.shutdown)
        self.configure(self.settings, feedback=False)

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # A short delay lets Windows dismiss its tray flyout before capture.
            self.tray_click_timer.start(QApplication.doubleClickInterval())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.tray_click_timer.stop()
            self.show_settings()

    def notify(self, title, message):
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 2800)

    def show_settings(self):
        if self.session:
            self.session.cancel()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def update_preferences(self, candidate):
        # Ordinary preferences do not depend on global shortcut registration.
        self.settings = replace(self.settings, smart_detect=candidate.smart_detect,
                                right_click_cancel=candidate.right_click_cancel,
                                hide_on_capture=candidate.hide_on_capture,
                                auto_save=candidate.auto_save, save_dir=candidate.save_dir)
        self.dialog.settings = self.settings
        try:
            self.settings.save(self.config_path)
            self.dialog.set_status("")
        except OSError as error:
            self.dialog.set_status(f"设置已生效，但无法写入配置：{error}")

    def set_startup(self, enabled):
        try:
            self.startup.set_enabled(enabled)
            if self.startup.is_enabled() != enabled:
                raise OSError("Windows 未能确认开机启动设置")
        except OSError as error:
            self.dialog.set_startup_state(self._startup_enabled, f"开机启动设置失败：{error}")
        else:
            self._startup_enabled = enabled
            self.dialog.set_startup_state(enabled)

    def configure(self, candidate, feedback=True):
        if self.pending_settings is not None:
            self.dialog.result(False, "正在注册快捷键，请稍后重试。")
            return
        self.update_preferences(candidate)
        self.pending_settings = (candidate.keyboard, candidate.mouse_button, candidate.mouse_modifiers)
        self._registration_feedback = feedback
        self.hotkeys.configure(*self.pending_settings)

    def registration_result(self, ok, message):
        if self.pending_settings is None:
            return
        shortcuts, self.pending_settings = self.pending_settings, None
        if ok:
            # A checkbox can change while the background registration is pending.
            candidate = replace(self.settings, keyboard=shortcuts[0], mouse_button=shortcuts[1], mouse_modifiers=shortcuts[2])
            self.settings = candidate
            try:
                candidate.save(self.config_path)
            except OSError as error:
                self.dialog.result(False, f"快捷键已生效，但设置无法写入：{error}")
                self.dialog.settings = candidate
                if self.background and not self._registration_feedback:
                    self.notify("设置无法保存", str(error))
                else:
                    self.show_settings()
                return
            self.tray.setToolTip(f"轻截 · {candidate.keyboard}")
            self.dialog.settings = candidate
            self.dialog.result(True, "", candidate)
        else:
            self.dialog.result(False, message)
            if self.background and not self._registration_feedback:
                self.notify("快捷键未生效", message)
            else:
                self.show_settings()

    def capture(self):
        if self.session or self.busy or time.monotonic() - self.last_capture < 0.35:
            return
        self.busy = True
        self.last_capture = time.monotonic()
        self.dialog.set_status("")
        self._restore_dialog = self.settings.hide_on_capture and self.dialog.isVisible()
        delay = 35
        if self._restore_dialog:
            self._hidden_window = int(self.dialog.winId())
            disabled = set_window_transitions_disabled(self._hidden_window, True)
            self.dialog.hide()
            # The event loop can finish hiding before DwmFlush at capture time.
            # Use a longer fallback if DWM cannot disable the transition.
            delay = 100 if disabled else 300
        self.menu.hide()
        QTimer.singleShot(delay, self._capture)

    def _capture(self):
        try:
            started = time.perf_counter()
            if self._hidden_window:
                flush_desktop()
            windows = list_windows(include_own=not self.settings.hide_on_capture)
            image, bounds = capture_desktop()
            self.generation += 1
            self.session = CaptureSession(image, bounds, windows, self.settings, self.detector, self.generation)
            self.session.finished.connect(self.on_finished)
            self.session.cancelled.connect(self.on_cancelled)
            self.session.show()
            log.info("Overlay ready in %.1f ms; desktop %sx%s", (time.perf_counter() - started) * 1000, bounds.w, bounds.h)
        except Exception as error:
            log.exception("Capture failed")
            if self.session:
                self.cleanup()
            else:
                self.restore_dialog()
            self.notify("截图未能启动", str(error))
        finally:
            if self._hidden_window:
                set_window_transitions_disabled(self._hidden_window, False)
                self._hidden_window = None
            self.busy = False

    def on_finished(self, image: QImage, save_only: bool):
        if save_only:
            # The frozen capture remains editable after an explicit save.
            self.busy = True
            try:
                filename = self.next_filename()
                path, _ = QFileDialog.getSaveFileName(self.session.toolbar, "保存截图", str(filename), "PNG 图片 (*.png)")
                if path:
                    target = Path(path)
                    if target.suffix.lower() != ".png":
                        target = target.with_suffix(".png")
                    save_png(image, target)
                    self.dialog.set_status("图片已保存")
            except OSError as error:
                QMessageBox.warning(self.session.toolbar, "保存失败", str(error))
            finally:
                self.busy = False
            return
        try:
            self.qt.clipboard().setImage(image)
            if self.qt.clipboard().image().isNull():
                raise OSError("Windows 剪贴板暂时不可用，请重新截图后重试")
            self.dialog.set_status("截图已复制")
            if self.settings.auto_save:
                try:
                    save_png(image, self.next_filename())
                    self.dialog.set_status("截图已复制并保存 PNG")
                except OSError as error:
                    self.dialog.set_status("截图已复制，PNG 保存失败")
                    self.notify("图片已复制，但本地保存失败", str(error))
        except Exception as error:
            log.exception("Clipboard failed")
            self.dialog.set_status("截图复制失败")
            self.notify("复制失败", str(error))
        finally:
            self.cleanup()

    def next_filename(self) -> Path:
        return Path(self.settings.save_dir).expanduser() / f"轻截_{datetime.now():%Y%m%d_%H%M%S_%f}.png"

    def on_cancelled(self):
        self.dialog.set_status("截图已取消")
        self.cleanup()

    def cleanup(self):
        session, self.session = self.session, None
        if session:
            session.dispose()
        self.restore_dialog()

    def restore_dialog(self):
        if self._restore_dialog:
            self._restore_dialog = False
            self.dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.dialog.show()
            self.dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

    def open_folder(self, directory=None):
        from PySide6.QtCore import QUrl
        try:
            value = self.settings.save_dir if directory is None else directory
            if not value.strip():
                raise ValueError("请先选择保存文件夹")
            path = Path(value).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("Windows 未能打开文件夹")
        except (OSError, ValueError) as error:
            self.dialog.set_status(f"无法打开文件夹：{error}")
            self.notify("无法打开保存文件夹", str(error))

    def shutdown(self):
        self._restore_dialog = False
        self.cleanup()
        self.hotkeys.stop()
        self.tray.hide()


def run() -> int:
    if sys.platform != "win32":
        raise SystemExit("轻截目前仅支持 Windows 10/11")
    enable_dpi_awareness()
    qt = QApplication(sys.argv)
    qt.setApplicationName("QingSnip")
    qt.setApplicationDisplayName("轻截")
    qt.setOrganizationName("QingSnip")
    qt.setFont(QFont("Microsoft YaHei UI", 10))
    qt.setQuitOnLastWindowClosed(False)
    qt.setWindowIcon(app_icon())
    # A named mutex works for both Python and packaged launches.
    kernel32 = C.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
    kernel32.CreateMutexW.restype = C.c_void_p
    kernel32.CloseHandle.argtypes = [C.c_void_p]
    mutex = kernel32.CreateMutexW(None, False, "Local\\QingSnip.SingleInstance.v1")
    if not mutex:
        QMessageBox.critical(None, "轻截", "无法创建单实例锁。")
        return 1
    if C.get_last_error() == 183:
        QMessageBox.information(None, "轻截已在运行", "请在右下角系统托盘中找到绿色“轻截”图标，或按已设置的截图快捷键。")
        kernel32.CloseHandle(mutex)
        return 0
    import argparse
    parser = argparse.ArgumentParser(description="轻截 · 本地截图与剪贴板")
    parser.add_argument("--settings", action="store_true", help="启动时打开设置")
    parser.add_argument("--config", type=Path, help="使用指定设置文件（便携模式）")
    parser.add_argument("--background", action="store_true", help="启动后仅常驻托盘")
    args = parser.parse_args()
    path = args.config or settings_path()
    first_run = not path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(path.parent / "qingsnip.log", maxBytes=512_000, backupCount=1, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.WARNING)

    def exception_hook(kind, value, traceback):
        log.error("Unhandled exception", exc_info=(kind, value, traceback))

    sys.excepthook = exception_hook
    controller = Application(qt, path, background=args.background)
    if not args.background and (first_run or args.settings):
        controller.show_settings()
    try:
        return qt.exec()
    finally:
        kernel32.CloseHandle(mutex)
