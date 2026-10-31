import ctypes as C
from contextlib import nullcontext
from pathlib import Path

import pytest

from snip import startup


class Registry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {"OtherApp": ("keep", self.REG_SZ)}
        self.writes = []
        self.exists = True

    def OpenKey(self, root, path, reserved, rights):
        assert root == self.HKEY_CURRENT_USER
        assert path == startup.StartupRegistration.key_path
        if not self.exists:
            raise FileNotFoundError()
        return nullcontext("key")

    def CreateKeyEx(self, root, path, reserved, rights):
        assert root == self.HKEY_CURRENT_USER
        assert path == startup.StartupRegistration.key_path
        self.exists = True
        return nullcontext("key")

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError()
        return self.values[name]

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = (value, value_type)
        self.writes.append(name)

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError()
        del self.values[name]


def windows_argv(command):
    # Use Windows' parser to verify quotes and Unicode; do not launch anything.
    shell32, kernel32 = C.WinDLL("shell32"), C.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [C.c_wchar_p, C.POINTER(C.c_int)]
    shell32.CommandLineToArgvW.restype = C.POINTER(C.c_wchar_p)
    kernel32.LocalFree.argtypes = [C.c_void_p]
    kernel32.LocalFree.restype = C.c_void_p
    count = C.c_int()
    pointer = shell32.CommandLineToArgvW(command, C.byref(count))
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(C.cast(pointer, C.c_void_p))


def test_registry_enable_disable_changes_only_our_run_value(monkeypatch, tmp_path):
    registry = Registry()
    monkeypatch.setattr(startup, "winreg", registry)
    manager = startup.StartupRegistration(tmp_path / "settings.json")
    manager.command = r"C:\Apps\QingSnip.exe --background"
    assert not manager.is_enabled()
    manager.set_enabled(True)
    assert manager.is_enabled()
    assert registry.writes == ["QingSnip"]
    manager.set_enabled(False)
    manager.set_enabled(False)
    assert not manager.is_enabled()
    assert registry.values == {"OtherApp": ("keep", registry.REG_SZ)}


def test_disabling_missing_run_key_does_not_create_it(monkeypatch, tmp_path):
    registry = Registry()
    registry.exists = False
    monkeypatch.setattr(startup, "winreg", registry)
    manager = startup.StartupRegistration(tmp_path / "settings.json")
    manager.set_enabled(False)
    assert not registry.exists


def test_overlong_command_is_rejected_before_writing(monkeypatch, tmp_path):
    registry = Registry()
    monkeypatch.setattr(startup, "winreg", registry)
    manager = startup.StartupRegistration(tmp_path / "settings.json")
    manager.command = "x" * 261
    with pytest.raises(OSError, match="路径过长"):
        manager.set_enabled(True)
    assert not registry.writes


def test_frozen_startup_command_handles_spaces_unicode_and_custom_config(monkeypatch, tmp_path):
    executable = tmp_path / "应用 文件夹" / "QingSnip.exe"
    config = tmp_path / "个人 设置" / "settings.json"
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", str(executable))
    assert windows_argv(startup.startup_command(config)) == [str(executable.resolve()), "--background", "--config", str(config.resolve())]


def test_source_startup_uses_windowless_python(monkeypatch, tmp_path):
    executable = tmp_path / "Python Files" / "python.exe"
    executable.parent.mkdir()
    windowless = executable.with_name("pythonw.exe")
    windowless.touch()
    config = tmp_path / "settings.json"
    monkeypatch.setattr(startup.sys, "frozen", False, raising=False)
    monkeypatch.setattr(startup.sys, "executable", str(executable))
    args = windows_argv(startup.startup_command(config))
    assert args[0] == str(windowless.resolve())
    assert Path(args[1]).name == "main.py"
    assert args[2:] == ["--background", "--config", str(config.resolve())]
