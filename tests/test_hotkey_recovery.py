import ctypes as C
import queue
import time

from PySide6.QtTest import QTest

from snip.hotkeys import Hotkeys, user32


def test_hotkey_message_loop_survives_registration_failure(qt_app, monkeypatch):
    """Feed the real worker a failed registration then a successful retry."""
    messages = queue.Queue()
    attempts, received = [], []

    def get_message(pointer, *args):
        message = messages.get(timeout=3)
        pointer._obj.message = message
        return 0 if message == 0x12 else 1

    def post_message(thread, message, *args):
        messages.put(message)
        return 1

    def register(*args):
        attempts.append(args)
        if len(attempts) == 1:
            C.set_last_error(1409)
            return 0
        return 1

    monkeypatch.setattr(user32, "PeekMessageW", lambda *args: 0)
    monkeypatch.setattr(user32, "GetMessageW", get_message)
    monkeypatch.setattr(user32, "PostThreadMessageW", post_message)
    monkeypatch.setattr(user32, "RegisterHotKey", register)
    monkeypatch.setattr(user32, "UnregisterHotKey", lambda *args: 1)
    monkeypatch.setattr(user32, "SetWindowsHookExW", lambda *args: None)
    monkeypatch.setattr(user32, "UnhookWindowsHookEx", lambda *args: 1)
    worker = Hotkeys()
    worker.registered.connect(lambda ok, message: received.append((ok, message)))

    def wait_result(count):
        deadline = time.monotonic() + 2
        while len(received) < count and time.monotonic() < deadline:
            QTest.qWait(10)
        assert len(received) == count

    try:
        worker.configure("Ctrl+Shift+A", "", "")
        wait_result(1)
        assert received[0][0] is False
        assert "占用" in received[0][1]
        assert worker._thread.is_alive()
        worker.configure("Ctrl+Shift+F10", "", "")
        wait_result(2)
        assert received[1] == (True, "")
        assert worker._thread.is_alive()
    finally:
        worker.stop()
