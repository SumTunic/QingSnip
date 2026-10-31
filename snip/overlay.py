from __future__ import annotations

import time
import math
from copy import deepcopy

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                               QComboBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .annotations import Annotation, render_selection, text_font, text_handles, text_rect, text_transform
from .config import Settings
from .detection import Detector
from .geometry import Rect, candidates_at
from .native import Window, cursor_position, physical_monitors

ACCENT = QColor("#36e7b4")


def brush_cursor_colors(image: QImage, center: QPointF, radius: float, origin: tuple[int, int]):
    """Choose contrast against the visible circle, with an opposite outline."""
    if image.isNull():
        return QColor("#ffffff"), QColor("#111111")
    luminance = 0.0
    for index in range(16):
        angle = index * math.tau / 16
        x = min(image.width() - 1, max(0, round(center.x() + radius * math.cos(angle) - origin[0])))
        y = min(image.height() - 1, max(0, round(center.y() + radius * math.sin(angle) - origin[1])))
        color = image.pixelColor(x, y)
        luminance += color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722
    dark, light = QColor("#111111"), QColor("#ffffff")
    return (dark, light) if luminance / 16 >= 140 else (light, dark)


def paint_brush_cursor(painter: QPainter, center: QPointF, radius: float, color: QColor, outline: QColor, scale: float):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for pen_color, width in ((outline, 3 * scale), (color, scale)):
        painter.setPen(QPen(pen_color, width))
        painter.drawEllipse(center, radius, radius)
        arm = 3 * scale
        painter.drawLine(QPointF(center.x() - arm, center.y()), QPointF(center.x() + arm, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - arm), QPointF(center.x(), center.y() + arm))
    painter.restore()


class CaptureSession(QObject):
    finished = Signal(object, bool)  # image, manual-save-only
    cancelled = Signal()

    def __init__(self, image: QImage, bounds: Rect, windows: list[Window], settings: Settings, detector: Detector, generation: int):
        super().__init__()
        self.image, self.bounds, self.windows = image, bounds, windows
        self.settings, self.detector, self.generation = settings, detector, generation
        self.selection: Rect | None = None
        self.hover: Rect | None = None
        self.annotations: list[Annotation] = []
        self.undo_stack = []
        self.redo_stack = []
        self._operation_before = None
        self.selected_text = None
        self.text_size = 25
        self.pointer = None
        self.pending: Annotation | None = None
        self.tool = "select"
        self.effect_mode = "mosaic"
        self.text_editor = None
        self._text_anchor = None
        self._text_template = None
        self._text_edit_index = None
        self._text_before = None
        self._preview = None
        self._right_cancel_pending = False
        self._right_grabber = None
        self.color, self.width = settings.color, settings.line_width
        self.cache: dict[int, list[Rect]] = {}
        self.candidate_index = 0
        self.last_hwnd = None
        self.last_request = 0.0
        self.last_probe = None
        self.drag = None
        self.closed = False
        self.canvases: list[Canvas] = []
        self.toolbar = Toolbar(self)
        monitors = physical_monitors()
        for screen in QApplication.screens():
            geo = screen.geometry()
            physical = monitors.get(screen.name())
            if physical is None:
                # Qt can report a friendly EDID name ("Mi Monitor") rather
                # than \\.\DISPLAY1. On Windows Qt keeps native origins.
                physical = next((r for r in monitors.values() if r.x == geo.x() and r.y == geo.y()), None)
            if physical is None:
                scale = screen.devicePixelRatio()
                physical = Rect(geo.x(), geo.y(), round(geo.width() * scale), round(geo.height() * scale))
            self.canvases.append(Canvas(self, screen, physical))
        for widget in [*self.canvases, self.toolbar, *self.toolbar.findChildren(QWidget)]:
            widget.installEventFilter(self)
        detector.ready.connect(self.detected)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.refresh_hover)
        self.update_history_buttons()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            self.begin_right_cancel(watched)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            self.end_right_cancel()
            return True
        if event.type() == QEvent.Type.ContextMenu:
            return True
        return super().eventFilter(watched, event)

    def begin_right_cancel(self, widget):
        if self.settings.right_click_cancel and not self._right_cancel_pending:
            self._right_cancel_pending = True
            self._right_grabber = widget.window()
            self._right_grabber.grabMouse()

    def end_right_cancel(self):
        if self._right_cancel_pending:
            self._right_cancel_pending = False
            if self._right_grabber:
                self._right_grabber.releaseMouse()
                self._right_grabber = None
            # Finish handling the release/context-menu events before hiding.
            QTimer.singleShot(0, self.cancel)

    def show(self):
        for canvas in self.canvases:
            canvas.show()
            canvas.raise_()
        x, y = cursor_position()
        active = next((c for c in self.canvases if c.physical.contains(x, y)), self.canvases[0])
        active.activateWindow()
        active.setFocus()
        self.refresh_hover()
        self.timer.start()

    def update(self, invalidate=True):
        if invalidate:
            self._preview = None
        for canvas in self.canvases:
            canvas.update()
        self.update_history_buttons()

    def preview(self):
        if self._preview is None and self.selection and self.selection.w > 0 and self.selection.h > 0:
            marks = [item for index, item in enumerate(self.annotations) if not (self.text_editor is not None and index == self._text_edit_index)]
            marks += [self.pending] if self.pending else []
            self._preview = render_selection(self.image, self.bounds, self.selection, marks)
        return self._preview

    def snapshot(self):
        return (self.selection, deepcopy(self.annotations), self.selected_text)

    def record(self, before):
        if before is not None and (before[0] != self.selection or before[1] != self.annotations):
            self.undo_stack.append(before)
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
        self.update()

    def restore(self, state):
        self.discard_text()
        self.selection, self.annotations, self.selected_text = deepcopy(state)
        self.pending = None
        self.drag = None
        self._operation_before = None
        self.toolbar.sync_text_options()
        if self.selection:
            self.keep_toolbar()
        else:
            self.toolbar.hide()
        self.update()

    def add_annotation(self, item):
        before = self.snapshot()
        self.annotations.append(item)
        self.selected_text = len(self.annotations) - 1 if item.tool == "text" else None
        self.record(before)

    def current_text(self):
        index = self.selected_text
        return self.annotations[index] if index is not None and index < len(self.annotations) and self.annotations[index].tool == "text" else None

    def text_at(self, x, y):
        for index in range(len(self.annotations) - 1, -1, -1):
            item = self.annotations[index]
            if item.tool == "text":
                inverse, valid = text_transform(item).inverted()
                if valid and text_rect(item).contains(inverse.map(QPointF(x, y))):
                    return index
        return None

    def keep_toolbar(self, canvas=None):
        if self.closed or not self.selection:
            return
        if canvas is None:
            x, y = self.selection.x + self.selection.w // 2, self.selection.bottom - 1
            canvas = next((c for c in self.canvases if c.physical.contains(x, y)), self.canvases[0])
        canvas.winId()
        self.toolbar.winId()
        if self.toolbar.windowHandle().transientParent() != canvas.windowHandle():
            self.toolbar.windowHandle().setTransientParent(canvas.windowHandle())
        self.position_toolbar()
        self.toolbar.show()
        self.toolbar.raise_()

    def update_history_buttons(self):
        if not hasattr(self, "toolbar"):
            return
        editing = self.text_editor is not None and bool(self.text_editor.toPlainText().strip())
        self.toolbar.undo_button.setEnabled(bool(self.undo_stack) or editing)
        self.toolbar.redo_button.setEnabled(bool(self.redo_stack))

    def begin_text(self, canvas, x, y, index=None):
        self.commit_text()
        self._text_before = self.snapshot()
        self._text_edit_index = index
        self.selected_text = index
        template = deepcopy(self.annotations[index]) if index is not None else Annotation("text", [(x, y)], self.color, self.width, font_size=self.text_size)
        self._text_template = template
        if index is not None:
            box = text_transform(template).mapRect(text_rect(template))
            x, y = max(canvas.physical.x + 8, round(box.x())), max(canvas.physical.y + 8, round(box.y()))
        editor = TextEditor(self, canvas)
        self.text_editor = editor
        self._text_anchor = (x, y, template.color, template.width)
        position = canvas.from_physical(x, y)
        right = canvas.from_physical(self.selection.right, y).x()
        bottom = canvas.from_physical(x, self.selection.bottom).y()
        editor.setGeometry(position.x(), position.y(), max(100, min(360, right - position.x())), max(48, min(110, bottom - position.y())))
        font = text_font(template)
        font.setPixelSize(max(10, round(font.pixelSize() * template.scale / canvas.scale_y)))
        editor.setFont(font)
        editor.setStyleSheet(f"QPlainTextEdit {{ color: {template.color}; background: #142131; border: 1px dashed #36e7b4; padding: 2px; }}")
        editor.setPlainText(template.text)
        editor.textChanged.connect(self.update_history_buttons)
        editor.installEventFilter(self)
        editor.viewport().installEventFilter(self)
        editor.show()
        editor.raise_()
        canvas.activateWindow()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.keep_toolbar(canvas)
        self.toolbar.sync_text_options()
        self.update()
        QTimer.singleShot(0, lambda: self.focus_text(editor))

    def focus_text(self, editor):
        if self.text_editor is editor and not self.closed:
            editor.window().windowHandle().requestActivate()
            editor.setFocus(Qt.FocusReason.MouseFocusReason)
            self.keep_toolbar(editor.window())

    def commit_text(self):
        editor = self.text_editor
        if editor is None:
            return
        text = editor.toPlainText()
        template, before, index = self._text_template, self._text_before, self._text_edit_index
        self.text_editor = None
        self._text_anchor = None
        self._text_edit_index = None
        self._text_template = None
        self._text_before = None
        editor.hide()
        editor.deleteLater()
        if not self.closed:
            if text.strip():
                template.text = text
                if index is None:
                    self.annotations.append(template)
                    index = len(self.annotations) - 1
                else:
                    self.annotations[index] = template
                self.selected_text = index
            elif index is not None:
                self.annotations.pop(index)
                self.selected_text = None
            self.record(before)
            self.toolbar.sync_text_options()
            self.keep_toolbar()

    def discard_text(self):
        editor, self.text_editor = self.text_editor, None
        self._text_anchor = None
        self._text_template = None
        self._text_before = None
        self._text_edit_index = None
        if editor:
            editor.hide()
            editor.deleteLater()
        self.update()

    def edit_selected_text(self):
        item = self.current_text()
        if item:
            x, y = item.points[0]
            canvas = next((c for c in self.canvases if c.physical.contains(x, y)), self.canvases[0])
            self.begin_text(canvas, x, y, self.selected_text)

    def delete_selected_text(self):
        self.commit_text()
        if self.current_text():
            before = self.snapshot()
            self.annotations.pop(self.selected_text)
            self.selected_text = None
            self.record(before)
            self.toolbar.sync_text_options()

    def set_color(self, color):
        self.color = color
        if self.text_editor is not None:
            self._text_template.color = color
            self.text_editor.setStyleSheet(f"color: {color}; background: #142131; border: 1px dashed #36e7b4; padding: 2px;")
        elif self.current_text() and self.tool in ("select", "text"):
            before = self.snapshot()
            self.current_text().color = color
            self.record(before)

    def set_text_size(self, size):
        self.text_size = int(size)
        if self.text_editor is not None:
            self._text_template.font_size = self.text_size
            self._text_template.scale = 1
            font = text_font(self._text_template)
            font.setPixelSize(max(10, round(self.text_size / self.text_editor.window().scale_y)))
            self.text_editor.setFont(font)
        elif self.current_text():
            before = self.snapshot()
            self.current_text().font_size = self.text_size
            self.current_text().scale = 1
            self.record(before)

    def rotate_text(self, delta=None):
        self.commit_text()
        if self.current_text():
            before = self.snapshot()
            self.current_text().rotation = 0 if delta is None else self.current_text().rotation + delta
            self.record(before)

    def refresh_hover(self):
        if self.selection or self.drag:
            return
        x, y = cursor_position()
        window = next((w for w in self.windows if w.rect.contains(x, y)), None)
        if not self.settings.smart_detect or window is None:
            self.hover = None
            self.update()
            return
        if self.last_hwnd != window.handle:
            self.candidate_index = 0
            self.last_hwnd = window.handle
            self.last_probe = None
        now = time.monotonic()
        if now - self.last_request > 0.18 and (self.last_probe is None or abs(x - self.last_probe[0]) + abs(y - self.last_probe[1]) > 12):
            self.last_request = now
            self.last_probe = (x, y)
            self.detector.request(self.generation, window.handle, x, y)
        rectangles = [window.rect, *self.cache.get(window.handle, [])]
        candidates = candidates_at(rectangles, x, y, self.bounds)
        border = min(x - window.rect.x, window.rect.right - x, y - window.rect.y, window.rect.bottom - y) < 10
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier or border:
            self.hover = window.rect.intersect(self.bounds)
        else:
            self.hover = candidates[self.candidate_index % len(candidates)] if candidates else None
        self.update()

    def detected(self, generation, hwnd, rectangles):
        if generation == self.generation and not self.closed:
            # Keep previously discovered sibling regions when moving the pointer.
            self.cache[hwnd] = list(set(self.cache.get(hwnd, []) + rectangles))[:500]
            self.refresh_hover()

    def cycle(self, direction: int):
        if not self.selection:
            self.candidate_index += direction
            self.refresh_hover()

    def select(self, rect: Rect):
        rect = rect.intersect(self.bounds)
        if rect.w < 2 or rect.h < 2:
            return
        self.selection = rect
        self.hover = None
        self.keep_toolbar()
        self.update()

    def position_toolbar(self):
        if not self.selection:
            return
        rect = self.selection
        x = (rect.x + rect.right) // 2
        y = min(rect.bottom - 1, self.bounds.bottom - 1)
        canvas = next((c for c in self.canvases if c.physical.contains(x, y)), self.canvases[0])
        screen_geo = canvas.screen().availableGeometry()
        local = canvas.from_physical(x, rect.bottom)
        point = canvas.mapToGlobal(local)
        self.toolbar.adjustSize()
        width, height = self.toolbar.width(), self.toolbar.height()
        left = max(screen_geo.left() + 8, min(point.x() - width // 2, screen_geo.right() - width - 8))
        top = point.y() + 12
        if top + height > screen_geo.bottom() - 8:
            upper = canvas.mapToGlobal(canvas.from_physical(x, rect.y)).y() - height - 12
            top = upper if upper >= screen_geo.top() + 8 else screen_geo.bottom() - height - 8
        self.toolbar.move(left, max(screen_geo.top() + 8, top))

    def undo(self):
        self.commit_text()
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        self.commit_text()
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def complete(self, save_only=False):
        if self._right_cancel_pending:
            return
        self.commit_text()
        if self.selection and not self.closed:
            result = render_selection(self.image, self.bounds, self.selection, self.annotations)
            if save_only:
                self.finished.emit(result, True)
            else:
                self.hide()
                self.finished.emit(result, False)

    def hide(self):
        if self.closed:
            return
        self.closed = True
        self.discard_text()
        if self._right_grabber:
            self._right_grabber.releaseMouse()
            self._right_grabber = None
        self._right_cancel_pending = False
        self.timer.stop()
        try:
            self.detector.ready.disconnect(self.detected)
        except RuntimeError:
            pass
        self.toolbar.hide()
        for canvas in self.canvases:
            canvas.hide()

    def dispose(self):
        self.hide()
        self.toolbar.deleteLater()
        for canvas in self.canvases:
            canvas.deleteLater()
        self.deleteLater()

    def cancel(self):
        if self._right_cancel_pending:
            return
        if not self.closed:
            self.hide()
            self.cancelled.emit()

    def key(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.complete()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_text()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self.redo() if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self.undo()
            elif event.key() == Qt.Key.Key_Y:
                self.redo()
            elif event.key() == Qt.Key.Key_S:
                self.complete(True)


class Canvas(QWidget):
    def __init__(self, session: CaptureSession, screen, physical: Rect):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.session, self.physical = session, physical
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.scale_x = physical.w / screen.geometry().width()
        self.scale_y = physical.h / screen.geometry().height()

    def from_physical(self, x: int, y: int) -> QPoint:
        return QPoint(round((x - self.physical.x) / self.scale_x), round((y - self.physical.y) / self.scale_y))

    def handles(self):
        rect = self.session.selection
        if rect is None:
            return []
        cx, cy = (rect.x + rect.right) // 2, (rect.y + rect.bottom) // 2
        return [("nw", rect.x, rect.y), ("n", cx, rect.y), ("ne", rect.right, rect.y),
                ("e", rect.right, cy), ("se", rect.right, rect.bottom),
                ("s", cx, rect.bottom), ("sw", rect.x, rect.bottom), ("w", rect.x, cy)]

    def handle_at(self, x, y):
        for name, hx, hy in self.handles():
            if abs(hx - x) < 8 * self.scale_x and abs(hy - y) < 8 * self.scale_y:
                return name
        return None

    def set_cursor_shape(self, shape):
        if self.cursor().shape() != shape:
            self.setCursor(shape)

    def text_handle_at(self, x, y):
        item = self.session.current_text()
        if item is not None:
            for index, point in enumerate(text_handles(item, 28 * self.scale_y)):
                if abs(point.x() - x) < 9 * self.scale_x and abs(point.y() - y) < 9 * self.scale_y:
                    return index
        return None

    def paintEvent(self, event):
        session = self.session
        painter = QPainter(self)
        painter.scale(1 / self.scale_x, 1 / self.scale_y)
        painter.translate(-self.physical.x, -self.physical.y)
        painter.drawImage(QPoint(session.bounds.x, session.bounds.y), session.image)
        selected = session.selection or session.hover
        outside = QPainterPath()
        outside.addRect(QRectF(self.physical.x, self.physical.y, self.physical.w, self.physical.h))
        if selected:
            hole = QPainterPath()
            hole.addRect(QRectF(selected.x, selected.y, selected.w, selected.h))
            outside = outside.subtracted(hole)
        painter.fillPath(outside, QColor(5, 10, 19, 132))
        if session.selection:
            painter.save()
            painter.setClipRect(QRectF(selected.x, selected.y, selected.w, selected.h))
            preview = session.preview()
            if preview is not None:
                painter.drawImage(QPoint(selected.x, selected.y), preview)
            painter.restore()
        if selected:
            painter.setPen(QPen(ACCENT, max(1, 1.5 * self.scale_x)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(selected.x, selected.y, selected.w, selected.h))
            if session.selection:
                painter.setBrush(ACCENT)
                radius = 3.2 * self.scale_x
                for _, hx, hy in self.handles():
                    painter.drawEllipse(QPointF(hx, hy), radius, radius)
        item = session.current_text()
        if item is not None and session.text_editor is None:
            points = text_handles(item, 28 * self.scale_y)
            painter.setPen(QPen(ACCENT, 1.3 * self.scale_x))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF(points[:4]))
            top = text_transform(item).map(QPointF(text_rect(item).width() / 2, 0))
            painter.drawLine(top, points[4])
            painter.setBrush(QColor("#142131"))
            for index, point in enumerate(points):
                radius = (5 if index == 4 else 4) * self.scale_x
                painter.drawEllipse(point, radius, radius)
        if session.selection and session.tool == "mosaic" and session.pointer is not None:
            x, y = session.pointer
            if session.selection.contains(x, y):
                radius = max(12, session.width * 8) / 2
                color, outline = brush_cursor_colors(session.preview(), QPointF(x, y), radius,
                                                     (session.selection.x, session.selection.y))
                paint_brush_cursor(painter, QPointF(x, y), radius, color, outline, self.scale_x)
        # Draw labels in logical coordinates for crisp, DPI-correct text.
        painter.resetTransform()
        if selected:
            pos = self.from_physical(selected.x, selected.y)
            label = f"{selected.w} × {selected.h} px"
            font = painter.font()
            font.setPixelSize(12)
            painter.setFont(font)
            label_rect = QRectF(max(8, min(pos.x(), self.width() - 160)), max(8, pos.y() - 32), 156, 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#101b29"))
            painter.drawRoundedRect(label_rect, 5, 5)
            painter.setPen(QColor("#e7fff6"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
        if not session.selection:
            text = "单击选择区域 · 拖动自由框选 · 滚轮切换区域 · Alt 选整窗 · Esc 取消"
            font = painter.font()
            font.setPixelSize(13)
            painter.setFont(font)
            width = min(self.width() - 24, painter.fontMetrics().horizontalAdvance(text) + 40)
            box = QRectF((self.width() - width) / 2, 24, width, 42)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(16, 27, 41, 240))
            painter.drawRoundedRect(box, 10, 10)
            painter.setPen(QColor("#e7fff6"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def mousePressEvent(self, event):
        session = self.session
        if event.button() == Qt.MouseButton.RightButton:
            session.begin_right_cancel(self)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = cursor_position()
        if session.tool == "mosaic" and session.selection and session.selection.contains(x, y):
            self.set_cursor_shape(Qt.CursorShape.BlankCursor)
        if session.text_editor is not None:
            session.commit_text()
        self.setFocus()
        session._operation_before = session.snapshot()
        if session.selection and session.tool in ("select", "text"):
            text_handle = self.text_handle_at(x, y)
            hit = session.text_at(x, y)
            if text_handle is not None or hit is not None:
                if text_handle is None:
                    session.selected_text = hit
                item = session.current_text()
                mode = "text_rotate" if text_handle == 4 else "text_resize" if text_handle is not None else "text_move"
                session.drag = (mode, (x, y), deepcopy(item), text_handle)
                session.toolbar.sync_text_options()
                session.keep_toolbar(self)
                session.update()
                return
            session.selected_text = None
            session.toolbar.sync_text_options()
        if session.selection and session.tool != "select":
            if not session.selection.contains(x, y):
                return
            if session.tool == "text":
                session._operation_before = None
                self.releaseMouse()
                session.begin_text(self, x, y)
                return
            tool = session.effect_mode if session.tool == "mosaic" else session.tool
            session.pending = Annotation(tool, [(x, y)], session.color, session.width)
            if session.tool == "mosaic":
                session.pending.brush_size = max(12, session.width * 8)
            session.drag = ("draw", (x, y), session.selection, None)
        else:
            handle = self.handle_at(x, y) if session.selection else None
            if handle:
                session.drag = ("resize", (x, y), session.selection, handle)
            elif session.selection and session.selection.contains(x, y):
                session.drag = ("move", (x, y), session.selection, None)
            else:
                candidate = session.hover
                session.selection = None
                session.annotations.clear()
                session.selected_text = None
                session.toolbar.hide()
                session.drag = ("new", (x, y), candidate, None)
        session.update()

    def mouseMoveEvent(self, event):
        session = self.session
        x, y = cursor_position()
        session.pointer = (x, y)
        if not session.drag:
            if session.tool in ("select", "text") and (self.text_handle_at(x, y) is not None or session.text_at(x, y) is not None):
                handle = self.text_handle_at(x, y)
                self.set_cursor_shape(Qt.CursorShape.CrossCursor if handle == 4 else Qt.CursorShape.SizeFDiagCursor if handle is not None else Qt.CursorShape.SizeAllCursor)
                return
            handle = self.handle_at(x, y) if session.tool == "select" else None
            shape = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                     "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                     "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                     "w": Qt.CursorShape.SizeHorCursor, "e": Qt.CursorShape.SizeHorCursor}.get(handle)
            if shape is None:
                shape = Qt.CursorShape.SizeAllCursor if session.selection and session.tool == "select" and session.selection.contains(x, y) else Qt.CursorShape.CrossCursor
            if session.tool == "mosaic" and session.selection and session.selection.contains(x, y):
                shape = Qt.CursorShape.BlankCursor
                session.update(False)
            # Resolve the final cursor once; never flash Cross -> Blank on
            # every move while painting with the mosaic brush.
            self.set_cursor_shape(shape)
            return
        mode, start, original, handle = session.drag
        x = min(max(x, session.bounds.x), session.bounds.right)
        y = min(max(y, session.bounds.y), session.bounds.bottom)
        if mode == "text_move":
            session.current_text().points = [(original.points[0][0] + x - start[0], original.points[0][1] + y - start[1])]
        elif mode == "text_rotate":
            center = text_transform(original).map(text_rect(original).center())
            first = math.atan2(start[1] - center.y(), start[0] - center.x())
            current = math.atan2(y - center.y(), x - center.x())
            session.current_text().rotation = original.rotation + math.degrees(current - first)
        elif mode == "text_resize":
            corners = text_handles(original)
            anchor = corners[(handle + 2) % 4]
            distance = math.hypot(start[0] - anchor.x(), start[1] - anchor.y())
            item = session.current_text()
            item.scale = max(0.2, min(10, original.scale * math.hypot(x - anchor.x(), y - anchor.y()) / max(1, distance)))
            item.points = list(original.points)
            moved = text_handles(item)[(handle + 2) % 4]
            item.translate(anchor.x() - moved.x(), anchor.y() - moved.y())
        elif mode == "new":
            if abs(x - start[0]) + abs(y - start[1]) >= 4 * self.scale_x:
                session.selection = Rect.between(start, (x, y)).intersect(session.bounds)
        elif mode == "move":
            dx = max(session.bounds.x - original.x, min(x - start[0], session.bounds.right - original.right))
            dy = max(session.bounds.y - original.y, min(y - start[1], session.bounds.bottom - original.bottom))
            result = original.translated(dx, dy)
            for annotation in session.annotations:
                annotation.translate(result.x - session.selection.x, result.y - session.selection.y)
            session.selection = result
        elif mode == "resize":
            left = x if "w" in handle else original.x
            right = x if "e" in handle else original.right
            top = y if "n" in handle else original.y
            bottom = y if "s" in handle else original.bottom
            session.selection = Rect.between((left, top), (right, bottom))
        elif mode == "draw":
            rect = session.selection
            point = (min(max(x, rect.x), rect.right - 1), min(max(y, rect.y), rect.bottom - 1))
            if session.pending.tool == "pen" or session.pending.brush_size > 0:
                session.pending.points.append(point)
            else:
                session.pending.points = [start, point]
        session.update()

    def mouseReleaseEvent(self, event):
        session = self.session
        if event.button() == Qt.MouseButton.RightButton:
            session.end_right_cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton or not session.drag:
            return
        mode, start, original, handle = session.drag
        session.drag = None
        if mode == "draw" and session.pending:
            if len(session.pending.points) > 1 or session.pending.tool == "pen" or session.pending.brush_size > 0:
                session.annotations.append(session.pending)
            session.pending = None
        if mode == "new" and session.selection is None:
            if original:
                session.select(original)
        elif session.selection:
            if session.selection.w >= 2 and session.selection.h >= 2:
                session.select(session.selection)
            else:
                session.selection = original if mode == "resize" else None
                session.update()
        before, session._operation_before = session._operation_before, None
        session.record(before)
        session.toolbar.sync_text_options()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.session.tool in ("select", "text"):
            self.session.commit_text()
            x, y = cursor_position()
            index = self.session.text_at(x, y)
            if index is not None:
                self.session.drag = None
                self.session._operation_before = None
                self.releaseMouse()
                self.session.begin_text(self, x, y, index)

    def wheelEvent(self, event):
        self.session.cycle(1 if event.angleDelta().y() < 0 else -1)

    def keyPressEvent(self, event):
        self.session.key(event)

    def closeEvent(self, event):
        self.session.cancel()
        event.accept()


class Toolbar(QFrame):
    def __init__(self, session: CaptureSession):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.session = session
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("captureToolbar")
        self.setStyleSheet("""
            QFrame#captureToolbar { background: #142131; border: 1px solid #34495c; border-radius: 10px; }
            QPushButton { color: #e6edf5; background: transparent; border: 0; padding: 7px 9px; border-radius: 5px; }
            QPushButton:hover { background: #304255; }
            QPushButton:checked { color: #36e7b4; background: #25483f; }
            QPushButton:disabled { color: #53687a; }
            QComboBox { color: #e6edf5; background: #233448; border: 1px solid #34495c; border-radius: 4px; padding: 3px 7px; }
            QComboBox QAbstractItemView { color: #e6edf5; background: #142131; selection-background-color: #25483f; }
            QLabel { color: #9eb0c4; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.group = QButtonGroup(self)
        for tool, label in (("select", "选区"), ("rect", "矩形"), ("ellipse", "椭圆"), ("arrow", "箭头"), ("pen", "画笔"), ("mosaic", "马赛克"), ("text", "文字")):
            button = QPushButton(label)
            button.setObjectName(f"tool_{tool}")
            button.setCheckable(True)
            button.setChecked(tool == "select")
            button.clicked.connect(lambda checked=False, t=tool: self.choose(t))
            self.group.addButton(button)
            row.addWidget(button)
        row.addSpacing(10)
        for label, callback, tip in (("撤销", session.undo, "Ctrl+Z"), ("重做", session.redo, "Ctrl+Y"),
                                     ("保存…", lambda: session.complete(True), "Ctrl+S · 另存为 PNG"),
                                     ("取消", session.cancel, "Esc"), ("✓ 完成", session.complete, "Enter · 完成并自动复制")):
            button = QPushButton(label)
            button.setToolTip(tip)
            if label == "撤销":
                self.undo_button = button
                button.setObjectName("action_undo")
                button.setEnabled(False)
            elif label == "重做":
                self.redo_button = button
                button.setObjectName("action_redo")
                button.setEnabled(False)
            # clicked(bool) must not become complete(save_only=True).
            button.clicked.connect(lambda checked=False, callback=callback: callback())
            if label == "✓ 完成":
                button.setStyleSheet("background: #36e7b4; color: #082a21; font-weight: bold;")
            row.addWidget(button)
        outer.addLayout(row)
        options = QHBoxLayout()
        options.setSpacing(7)
        options.addWidget(QLabel("标注颜色"))
        for color in ("#ff4d64", "#ffc857", "#36e7b4", "#4aa3ff", "#ffffff", "#111827"):
            button = QPushButton()
            button.setFixedSize(20, 20)
            button.setStyleSheet(f"background: {color}; border: 1px solid #728095; border-radius: 10px;")
            button.clicked.connect(lambda checked=False, c=color: session.set_color(c))
            options.addWidget(button)
        options.addSpacing(8)
        self.width_label = QLabel("粗细")
        options.addWidget(self.width_label)
        self.width_group = QButtonGroup(self)
        widths = (1, 3, 6, 10, 16)
        closest = min(widths, key=lambda value: abs(value - session.width))
        for value in widths:
            button = WidthButton(value)
            button.setChecked(value == closest)
            button.clicked.connect(lambda checked=False, value=value: setattr(session, "width", value))
            self.width_group.addButton(button, value)
            options.addWidget(button)
        options.addStretch()
        outer.addLayout(options)
        self.effect_options = QWidget()
        modes = QHBoxLayout(self.effect_options)
        modes.setContentsMargins(0, 2, 0, 0)
        modes.addWidget(QLabel("马赛克效果"))
        self.effect_group = QButtonGroup(self)
        for tool, label in (("mosaic", "像素块"), ("blur", "模糊"), ("solid", "实色遮盖")):
            button = QPushButton(label)
            button.setObjectName(f"effect_{tool}")
            button.setCheckable(True)
            button.setChecked(tool == session.effect_mode)
            button.clicked.connect(lambda checked=False, mode=tool: setattr(session, "effect_mode", mode))
            self.effect_group.addButton(button)
            modes.addWidget(button)
        modes.addStretch()
        outer.addWidget(self.effect_options)
        self.effect_options.hide()
        self.text_options = QWidget()
        text_row = QHBoxLayout(self.text_options)
        text_row.setContentsMargins(0, 2, 0, 0)
        text_row.addWidget(QLabel("字号"))
        self.font_size = QComboBox()
        self.font_size.setObjectName("textFontSize")
        self.font_size.addItems([str(value) for value in (12, 16, 20, 25, 32, 40, 48, 64, 96, 128)])
        self.font_size.setCurrentText(str(session.text_size))
        self.font_size.currentTextChanged.connect(lambda value: session.set_text_size(int(value)))
        self.font_size.setMinimumWidth(65)
        text_row.addWidget(self.font_size)
        for name, label, callback in (("edit_text", "编辑文字", session.edit_selected_text),
                                       ("delete_text", "删除文字", session.delete_selected_text),
                                       ("rotate_left", "左转15°", lambda: session.rotate_text(-15)),
                                       ("rotate_right", "右转15°", lambda: session.rotate_text(15)),
                                       ("rotate_reset", "归正", session.rotate_text)):
            button = QPushButton(label)
            button.setObjectName(name)
            button.clicked.connect(lambda checked=False, callback=callback: callback())
            text_row.addWidget(button)
        text_row.addStretch()
        outer.addWidget(self.text_options)
        self.text_options.hide()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def choose(self, tool):
        self.session.commit_text()
        self.session.tool = tool
        if tool not in ("select", "text"):
            self.session.selected_text = None
        self.effect_options.setVisible(tool == "mosaic")
        self.width_label.setText("笔径" if tool == "mosaic" else "粗细")
        self.width_label.setVisible(tool != "text")
        for button in self.width_group.buttons():
            button.setVisible(tool != "text")
            button.setToolTip(f"{max(12, button.value * 8) if tool == 'mosaic' else button.value} px")
        self.sync_text_options()
        self.session.position_toolbar()
        self.session.update(False)

    def sync_text_options(self):
        item = self.session._text_template if self.session.text_editor is not None else self.session.current_text()
        text_context = self.session.tool == "text" or item is not None
        self.text_options.setVisible(text_context)
        self.width_label.setVisible(not text_context)
        for button in self.width_group.buttons():
            button.setVisible(not text_context)
        size = round((item.font_size or 16 + item.width * 3) * item.scale) if item else self.session.text_size
        previous = self.font_size.blockSignals(True)
        if self.font_size.findText(str(size)) < 0:
            self.font_size.addItem(str(size))
        self.font_size.setCurrentText(str(size))
        self.font_size.blockSignals(previous)

    def keyPressEvent(self, event):
        self.session.key(event)


class WidthButton(QPushButton):
    def __init__(self, value):
        super().__init__()
        self.value = value
        self.setObjectName(f"width_{value}")
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setToolTip(f"{value} px")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isChecked() or self.underMouse():
            painter.setBrush(QColor("#25483f") if self.isChecked() else QColor("#304255"))
            painter.drawRoundedRect(QRectF(1, 1, 26, 26), 5, 5)
        painter.setBrush(ACCENT if self.isChecked() else QColor("#e6edf5"))
        radius = max(1.5, self.value / 2 + 1)
        painter.drawEllipse(QPointF(14, 14), radius, radius)
        painter.end()


class TextEditor(QPlainTextEdit):
    def __init__(self, session, canvas):
        super().__init__(canvas)
        self.session = session
        self._composing = False
        self.setObjectName("annotationTextEditor")
        self.setPlaceholderText("输入文字，Enter 确认")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.document().setDocumentMargin(0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.session.discard_text()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self._composing and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.session.commit_text()
        else:
            super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)
