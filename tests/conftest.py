import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    # The offscreen platform does not enumerate Windows system fonts. Load
    # real fonts so text geometry and rasterization use the same metrics.
    fonts = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "segoeui.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))
    return app
