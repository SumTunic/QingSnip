from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    keyboard: str = "Ctrl+Shift+A"
    mouse_button: str = ""
    mouse_modifiers: str = "Ctrl+Alt"
    auto_save: bool = False
    save_dir: str = str(Path.home() / "Pictures" / "QingSnip")
    smart_detect: bool = True
    right_click_cancel: bool = True
    hide_on_capture: bool = True
    line_width: int = 3
    color: str = "#ff4d64"
    schema_version: int = 2

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            defaults = cls()
            values = {}
            for field in fields(cls):
                value = data.get(field.name, getattr(defaults, field.name))
                if type(value) is type(getattr(defaults, field.name)):
                    values[field.name] = value
            result = cls(**values)
            version = data.get("schema_version", 0)
            if (not isinstance(version, int) or version < 2) and result.keyboard == "Ctrl+Shift+F8":
                result.keyboard = defaults.keyboard
            result.schema_version = defaults.schema_version
            result.line_width = max(1, min(20, result.line_width))
            if result.mouse_button not in ("", "middle", "x1", "x2"):
                result.mouse_button = ""
            if not result.save_dir.strip():
                result.save_dir = defaults.save_dir
            return result
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def settings_path() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "QingSnip" / "settings.json"
