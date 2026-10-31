"""Current-user sign-in startup; no administrator rights or shell scripts."""
from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path


def startup_command(config_path: Path) -> str:
    executable = Path(sys.executable).resolve()
    args = [str(executable)]
    if not getattr(sys, "frozen", False):
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            args[0] = str(windowless)
        args.append(str(Path(__file__).resolve().parents[1] / "main.py"))
    args.extend(["--background", "--config", str(config_path.resolve())])
    return subprocess.list2cmdline(args)


class StartupRegistration:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "QingSnip"

    def __init__(self, config_path: Path):
        self.command = startup_command(config_path)

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_QUERY_VALUE) as key:
                value, value_type = winreg.QueryValueEx(key, self.value_name)
                return value_type == winreg.REG_SZ and value == self.command
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled: bool):
        if enabled:
            if len(self.command) > 260:
                raise OSError("启动路径过长，请将程序移到较短的路径后重试")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, self.command)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.value_name)
            except FileNotFoundError:
                pass
