import json

import pytest
from PySide6.QtGui import QColor, QImage

from snip.annotations import Annotation, render_selection
from snip.app import save_png
from snip.config import Settings
from snip.geometry import Rect, candidates_at
from snip.hotkeys import parse_hotkey, parse_modifiers


def test_selection_on_negative_monitor_is_physical_pixels():
    desktop = Rect(-1920, -200, 4480, 1640)
    selection = Rect.between((-500, 200), (-1500, -100)).intersect(desktop)
    assert selection == Rect(-1500, -100, 1000, 300)
    source = QImage(desktop.w, desktop.h, QImage.Format.Format_RGB32)
    source.fill(QColor("#132435"))
    result = render_selection(source, desktop, selection, [])
    assert (result.width(), result.height()) == (1000, 300)
    assert result.pixelColor(0, 0) == QColor("#132435")


def test_candidates_clip_deduplicate_and_choose_nested_regions():
    bounds = Rect(-100, 0, 1100, 800)
    window = Rect(-120, 0, 1000, 900)
    page = Rect(0, 120, 800, 600)
    tiny = Rect(50, 140, 10, 10)
    assert candidates_at([window, page, page, tiny], 55, 145, bounds) == [page, window.intersect(bounds)]
    assert not page.contains(page.right, 150)


def test_config_rejects_wrong_types_and_roundtrips(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_save": "false", "line_width": 100, "mouse_button": "left", "unknown": 12}))
    settings = Settings.load(path)
    assert settings.auto_save is False
    assert settings.line_width == 20
    assert settings.mouse_button == ""
    settings.auto_save = True
    settings.keyboard = "Ctrl+Shift+F9"
    settings.save(path)
    assert Settings.load(path) == settings
    assert not path.with_suffix(".tmp").exists()
    path.write_text("broken json")
    assert Settings.load(path) == Settings()


@pytest.mark.parametrize("text,expected", [("Ctrl+Alt+A", (3, 65)), ("Print", (0, 44)), ("PrintScreen", (0, 44)), ("Ctrl+Shift+F9", (6, 120)), ("Win+F24", (8, 135))])
def test_supported_hotkeys(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["", "A", "Ctrl+Ctrl", "Ctrl+Something", "Hyper+A", "Ctrl+K, Ctrl+C"])
def test_invalid_hotkeys(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_mouse_modifiers():
    assert parse_modifiers("Ctrl+Alt+Shift") == 7
    assert parse_modifiers("") == 0
    with pytest.raises(ValueError):
        parse_modifiers("Hyper")


def test_annotation_export_clips_and_preserves_source(qt_app):
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    desktop = Rect(-100, 20, 300, 200)
    selection = Rect(-50, 50, 120, 100)
    marks = [Annotation("rect", [(-40, 60), (40, 110)], "#ff0000", 4),
             Annotation("text", [(-30, 115)], "#0000ff", 2, "测试 AI")]
    output = render_selection(image, desktop, selection, marks)
    assert (output.width(), output.height()) == (120, 100)
    assert output.pixelColor(10, 10).red() > 200
    assert output.pixelColor(10, 10).green() < 80
    assert image.pixelColor(60, 40) == QColor("white")
    assert output.pixelColor(60, 5) == QColor("white")


def test_mosaic_reduces_detail_and_png_saves(qt_app, tmp_path):
    image = QImage(80, 80, QImage.Format.Format_RGB32)
    for y in range(80):
        for x in range(80):
            image.setPixelColor(x, y, QColor("white" if (x + y) % 2 else "black"))
    output = render_selection(image, Rect(0, 0, 80, 80), Rect(0, 0, 80, 80), [Annotation("mosaic", [(0, 0), (80, 80)])])
    assert output.pixelColor(1, 0) == output.pixelColor(0, 0)
    path = tmp_path / "中文目录" / "截图.png"
    save_png(output, path)
    assert QImage(str(path)).size() == output.size()
    assert not path.with_name(path.name + ".tmp").exists()


@pytest.mark.parametrize("tool", ["pen", "ellipse", "arrow"])
def test_drawing_tools_produce_changed_pixels(qt_app, tool):
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    output = render_selection(image, Rect(0, 0, 100, 100), Rect(0, 0, 100, 100), [Annotation(tool, [(15, 20), (70, 75)])])
    assert output != image


def test_settings_window_constructs(qt_app):
    from snip.settings_ui import SettingsDialog
    dialog = SettingsDialog(Settings())
    assert dialog.auto_save.isChecked() is False
    assert dialog.keyboard.keySequence().toString() == "Ctrl+Shift+A"
    assert dialog.right_click_cancel.isChecked()
    assert dialog.hide_on_capture.isChecked()
    dialog.deleteLater()


def test_previous_default_migrates_without_changing_custom_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"keyboard": "Ctrl+Shift+F8", "auto_save": True, "mouse_button": "x2"}))
    result = Settings.load(path)
    assert result.keyboard == "Ctrl+Shift+A"
    assert result.auto_save and result.mouse_button == "x2"
    assert result.hide_on_capture and result.right_click_cancel
    path.write_text(json.dumps({"keyboard": "Ctrl+Alt+K"}))
    assert Settings.load(path).keyboard == "Ctrl+Alt+K"
    path.write_text(json.dumps({"keyboard": "Ctrl+Shift+F8", "schema_version": 2}))
    assert Settings.load(path).keyboard == "Ctrl+Shift+F8"


def test_capture_preferences_save_and_restore(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(right_click_cancel=False, hide_on_capture=False)
    settings.save(path)
    assert Settings.load(path) == settings


def test_shortcut_apply_does_not_commit_unrelated_preferences(qt_app):
    from snip.settings_ui import SettingsDialog
    dialog = SettingsDialog(Settings())
    received = []
    dialog.requested.connect(received.append)
    dialog.right_click_cancel.setChecked(False)
    dialog.hide_on_capture.setChecked(False)
    dialog.apply()
    assert len(received) == 1
    assert received[0].right_click_cancel
    assert received[0].hide_on_capture
    dialog.deleteLater()
