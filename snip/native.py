"""Small Win32 surface: DPI, GDI capture, visible window enumeration."""
from __future__ import annotations

import ctypes as C
import os
from ctypes import wintypes as W
from dataclasses import dataclass

from PySide6.QtGui import QImage

from .geometry import Rect

user32 = C.WinDLL("user32", use_last_error=True)
gdi32 = C.WinDLL("gdi32", use_last_error=True)
dwmapi = C.WinDLL("dwmapi")

# Explicit signatures prevent 64-bit HANDLE truncation.
user32.GetDC.argtypes = [W.HWND]
user32.GetDC.restype = W.HDC
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
gdi32.CreateCompatibleBitmap.restype = W.HBITMAP
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
gdi32.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.DeleteDC.argtypes = [W.HDC]
user32.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.IsIconic.argtypes = [W.HWND]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
dwmapi.DwmFlush.argtypes = []


def set_window_transitions_disabled(hwnd: int, disabled: bool) -> bool:
    value = W.BOOL(disabled)
    # DWMWA_TRANSITIONS_FORCEDISABLED only affects this window.
    return dwmapi.DwmSetWindowAttribute(hwnd, 3, C.byref(value), C.sizeof(value)) == 0


def flush_desktop() -> None:
    """Wait until queued desktop composition changes have been presented."""
    dwmapi.DwmFlush()


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
        user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


def cursor_position() -> tuple[int, int]:
    point = W.POINT()
    user32.GetCursorPos(C.byref(point))
    return point.x, point.y


def virtual_bounds() -> Rect:
    return Rect(*(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)))


class BitmapInfoHeader(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bit_count", W.WORD), ("compression", W.DWORD),
                ("size_image", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("clr_used", W.DWORD), ("clr_important", W.DWORD)]


def capture_desktop() -> tuple[QImage, Rect]:
    bounds = virtual_bounds()
    if bounds.w <= 0 or bounds.h <= 0:
        raise OSError("无法读取桌面尺寸")
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, bounds.w, bounds.h)
    previous = None
    try:
        if not screen or not memory or not bitmap:
            raise C.WinError(C.get_last_error())
        previous = gdi32.SelectObject(memory, bitmap)
        if not gdi32.BitBlt(memory, 0, 0, bounds.w, bounds.h, screen, bounds.x, bounds.y, 0x40CC0020):
            raise C.WinError(C.get_last_error())
        # GetDIBits requires the bitmap to be deselected first.
        gdi32.SelectObject(memory, previous)
        previous = None
        header = BitmapInfoHeader(C.sizeof(BitmapInfoHeader), bounds.w, -bounds.h, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = C.create_string_buffer(bounds.w * bounds.h * 4)
        if gdi32.GetDIBits(memory, bitmap, 0, bounds.h, buffer, C.byref(header), 0) != bounds.h:
            raise C.WinError(C.get_last_error())
        image = QImage(buffer.raw, bounds.w, bounds.h, bounds.w * 4, QImage.Format.Format_RGB32).copy()
        return image, bounds
    finally:
        if previous:
            gdi32.SelectObject(memory, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        if screen:
            user32.ReleaseDC(None, screen)


@dataclass(frozen=True)
class Window:
    handle: int
    rect: Rect


ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ENUMPROC, W.LPARAM]


def window_rect(hwnd: int, extended: bool = True) -> Rect | None:
    raw = W.RECT()
    if not (extended and dwmapi.DwmGetWindowAttribute(hwnd, 9, C.byref(raw), C.sizeof(raw)) == 0):
        if not user32.GetWindowRect(hwnd, C.byref(raw)):
            return None
    result = Rect(raw.left, raw.top, raw.right - raw.left, raw.bottom - raw.top)
    return result if result.area else None


def list_windows(include_own: bool = False) -> list[Window]:
    windows = []

    @ENUMPROC
    def callback(hwnd, _):
        pid, cloaked = W.DWORD(), W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if (not include_own and pid.value == os.getpid()) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        dwmapi.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked))
        rect = window_rect(hwnd)
        if not cloaked.value and rect and rect.w >= 24 and rect.h >= 20:
            windows.append(Window(hwnd, rect))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def child_rectangles(hwnd: int) -> list[Rect]:
    rectangles = []

    @ENUMPROC
    def callback(child, _):
        if user32.IsWindowVisible(child):
            rect = window_rect(child, extended=False)
            if rect and rect.w >= 60 and rect.h >= 30:
                rectangles.append(rect)
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return rectangles


def physical_monitors() -> dict[str, Rect]:
    class MonitorInfo(C.Structure):
        _fields_ = [("size", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD), ("device", W.WCHAR * 32)]

    result = {}
    proc_type = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
    user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.c_void_p]
    user32.EnumDisplayMonitors.argtypes = [W.HDC, C.c_void_p, proc_type, W.LPARAM]

    @proc_type
    def callback(handle, dc, raw, data):
        info = MonitorInfo()
        info.size = C.sizeof(info)
        if user32.GetMonitorInfoW(handle, C.byref(info)):
            rect = info.monitor
            result[info.device] = Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return True

    user32.EnumDisplayMonitors(None, None, callback, 0)
    return result
