from __future__ import annotations

import logging
import queue
import threading
import time

from PySide6.QtCore import QObject, Signal

from .geometry import Rect
from .native import child_rectangles

log = logging.getLogger(__name__)


class Detector(QObject):
    ready = Signal(int, int, object)

    def __init__(self):
        super().__init__()
        self._queue = queue.Queue(maxsize=1)
        threading.Thread(target=self._run, daemon=True, name="UIAutomation").start()

    def request(self, generation: int, hwnd: int, x: int, y: int):
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait((generation, hwnd, x, y))
        except queue.Full:
            pass

    def _run(self):
        try:
            import uiautomation as auto
            initializer = auto.UIAutomationInitializerInThread()
            initializer.__enter__()
        except Exception:
            auto = None
            log.info("UI Automation unavailable; native window detection remains active", exc_info=True)
        while True:
            generation, hwnd, x, y = self._queue.get()
            rectangles = []
            try:
                rectangles.extend(child_rectangles(hwnd))
                if auto is not None:
                    root = auto.ControlFromHandle(hwnd)
                    raw = root.BoundingRectangle
                    outer = Rect(raw.left, raw.top, raw.right - raw.left, raw.bottom - raw.top)
                    deadline = time.monotonic() + 0.18
                    seen = 0

                    def visit(control, depth):
                        nonlocal seen
                        if depth > 14 or time.monotonic() > deadline or seen >= 220:
                            return
                        for child in control.GetChildren():
                            seen += 1
                            if time.monotonic() > deadline or seen > 220:
                                break
                            raw = child.BoundingRectangle
                            rect = Rect(raw.left, raw.top, raw.right - raw.left, raw.bottom - raw.top).intersect(outer)
                            if rect.w < 60 or rect.h < 30:
                                continue
                            control_type = child.ControlTypeName
                            # Ignore individual text labels, menu items, and buttons.
                            if control_type in ("DocumentControl", "PaneControl", "GroupControl", "ToolBarControl", "TabControl", "WindowControl", "ListControl", "TreeControl", "DataGridControl"):
                                rectangles.append(rect)
                            if control_type == "DocumentControl" and rect.w >= outer.w * 0.65 and rect.y > outer.y + 35:
                                rectangles.append(Rect(outer.x, outer.y, outer.w, rect.y - outer.y))
                            # Descend only along the pointer path. Siblings such as
                            # a browser Document still contribute a top-strip candidate.
                            if rect.contains(x, y):
                                visit(child, depth + 1)

                    visit(root, 0)
            except Exception:
                log.debug("Detection provider failed for hwnd=%s", hwnd, exc_info=True)
            self.ready.emit(generation, hwnd, rectangles)
