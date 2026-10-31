from __future__ import annotations

import ctypes as C
import queue
import threading
from ctypes import wintypes as W

from PySide6.QtCore import QObject, Signal

from .native import user32

MODIFIERS = {"ctrl": 2, "alt": 1, "shift": 4, "win": 8, "meta": 8}
KEYS = {"print": 0x2C, "printscreen": 0x2C, "prtsc": 0x2C,
        "space": 0x20, "tab": 9, "pause": 0x13, "insert": 0x2D,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        **{f"f{i}": 0x6F + i for i in range(1, 25)}}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("请设置键盘快捷键")
    mask = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"不支持的修饰键：{part}")
        mask |= MODIFIERS[part]
    key = parts[-1]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        vk = ord(key.upper())
        if not mask:
            raise ValueError("字母或数字需搭配 Ctrl、Alt、Shift 或 Win")
    elif key in KEYS:
        vk = KEYS[key]
    else:
        raise ValueError("支持字母、数字、F1–F24、PrintScreen 等按键")
    return mask, vk


def parse_modifiers(text: str) -> int:
    mask = 0
    for part in text.lower().split("+"):
        part = part.strip()
        if part:
            if part not in MODIFIERS:
                raise ValueError(f"不支持的鼠标修饰键：{part}")
            mask |= MODIFIERS[part]
    return mask


class Hotkeys(QObject):
    triggered = Signal()
    registered = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._commands = queue.Queue()
        self._ready = threading.Event()
        self._thread_id = 0
        self._mouse = ("", 0)
        self._thread = threading.Thread(target=self._run, daemon=True, name="GlobalHotkeys")
        self._thread.start()

    def configure(self, keyboard: str, mouse_button: str, mouse_modifiers: str):
        try:
            keyboard_value = parse_hotkey(keyboard)
            mouse_value = parse_modifiers(mouse_modifiers)
        except ValueError as error:
            self.registered.emit(False, str(error))
            return
        if not self._ready.wait(2):
            self.registered.emit(False, "Windows 热键线程未能启动")
            return
        self._commands.put((keyboard_value, mouse_button, mouse_value))
        user32.PostThreadMessageW(self._thread_id, 0x8001, 0, 0)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        self._thread.join(timeout=2)

    def _run(self):
        kernel32 = C.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = W.DWORD
        kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
        kernel32.GetModuleHandleW.restype = W.HMODULE
        hook_proc = C.WINFUNCTYPE(C.c_ssize_t, C.c_int, W.WPARAM, W.LPARAM)
        user32.SetWindowsHookExW.argtypes = [C.c_int, hook_proc, W.HINSTANCE, W.DWORD]
        user32.SetWindowsHookExW.restype = W.HHOOK
        user32.CallNextHookEx.argtypes = [W.HHOOK, C.c_int, W.WPARAM, W.LPARAM]
        user32.CallNextHookEx.restype = C.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
        user32.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
        user32.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
        user32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]

        class MouseData(C.Structure):
            _fields_ = [("point", W.POINT), ("data", W.DWORD), ("flags", W.DWORD),
                        ("time", W.DWORD), ("extra", C.c_size_t)]

        # Consume the release as well as the press so side-button Back/Forward
        # does not reach the application under the pointer.
        swallowed = set()

        @hook_proc
        def callback(code, message, pointer):
            if code >= 0:
                data = C.cast(pointer, C.POINTER(MouseData)).contents
                button = ""
                down = message in (0x207, 0x20B)
                if message in (0x207, 0x208):
                    button = "middle"
                elif message in (0x20B, 0x20C):
                    button = "x1" if (data.data >> 16) == 1 else "x2"
                if button and not (data.flags & 1):
                    if not down and button in swallowed:
                        swallowed.discard(button)
                        return 1
                    current = 0
                    for vk, modifier in ((0x11, 2), (0x12, 1), (0x10, 4), (0x5B, 8), (0x5C, 8)):
                        if user32.GetAsyncKeyState(vk) & 0x8000:
                            current |= modifier
                    if down and button == self._mouse[0] and current == self._mouse[1]:
                        swallowed.add(button)
                        self.triggered.emit()
                        return 1
            return user32.CallNextHookEx(None, code, message, pointer)

        self._thread_id = kernel32.GetCurrentThreadId()
        message = W.MSG()
        user32.PeekMessageW(C.byref(message), None, 0, 0, 0)
        hook = user32.SetWindowsHookExW(14, callback, kernel32.GetModuleHandleW(None), 0)
        active = None
        active_id = 1
        self._ready.set()
        try:
            while user32.GetMessageW(C.byref(message), None, 0, 0) > 0:
                if message.message == 0x312:
                    self.triggered.emit()
                elif message.message == 0x8001:
                    while not self._commands.empty():
                        keyboard, button, modifiers = self._commands.get_nowait()
                        if button and not hook:
                            self.registered.emit(False, "Windows 未能安装鼠标快捷键监听")
                            continue
                        if keyboard != active:
                            next_id = 2 if active_id == 1 else 1
                            if not user32.RegisterHotKey(None, next_id, keyboard[0] | 0x4000, keyboard[1]):
                                error = C.get_last_error()
                                error_message = "键盘快捷键已被其他程序占用，请换一个组合。" if error == 1409 else f"Windows 未能注册热键（错误 {error}）：{C.FormatError(error).strip()}"
                                self.registered.emit(False, error_message)
                                continue
                            if active is not None:
                                user32.UnregisterHotKey(None, active_id)
                            active, active_id = keyboard, next_id
                        self._mouse = (button, modifiers)
                        self.registered.emit(True, "")
        finally:
            if active is not None:
                user32.UnregisterHotKey(None, active_id)
            if hook:
                user32.UnhookWindowsHookEx(hook)
