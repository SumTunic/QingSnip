from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QInputMethodEvent, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QSpinBox

from snip.annotations import Annotation, render_selection, text_handles, text_rect, text_transform
from snip.config import Settings
from snip.geometry import Rect
from snip.overlay import CaptureSession, WidthButton, brush_cursor_colors, paint_brush_cursor


class Detector(QObject):
    ready = Signal(int, int, object)

    def request(self, *args):
        pass


@pytest.fixture
def editor_session(qt_app):
    image = QImage(320, 220, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    detector = Detector()
    session = CaptureSession(image, Rect(0, 0, 320, 220), [], Settings(), detector, 1)
    session.show()
    session.select(Rect(0, 0, 300, 200))
    yield session
    session.dispose()
    qt_app.processEvents()


def input_text(editor, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(editor, event)


def key(widget, value, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, value, modifiers))


def right_mouse(widget, event_type):
    buttons = Qt.MouseButton.RightButton if event_type == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    QApplication.sendEvent(widget, QMouseEvent(event_type, QPointF(8, 8), QPointF(8, 8),
                                              Qt.MouseButton.RightButton, buttons, Qt.KeyboardModifier.NoModifier))


def test_thickness_is_a_group_of_visible_dot_buttons(editor_session):
    toolbar = editor_session.toolbar
    assert not toolbar.findChildren(QSpinBox)
    buttons = toolbar.findChildren(WidthButton)
    assert [button.value for button in buttons] == [1, 3, 6, 10, 16]
    next(button for button in buttons if button.value == 10).click()
    assert editor_session.width == 10
    assert sum(button.isChecked() for button in buttons) == 1


def test_text_tool_accepts_chinese_ime_and_multiline_and_exports(editor_session, monkeypatch):
    import snip.overlay as overlay
    session = editor_session
    session.toolbar.findChild(QPushButton, "tool_text").click()
    monkeypatch.setattr(overlay, "cursor_position", lambda: (25, 30))
    session.canvases[0].mousePressEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton))
    QApplication.processEvents()
    editor = session.text_editor
    assert isinstance(editor, QPlainTextEdit)
    assert editor.parentWidget() is session.canvases[0]
    assert editor.isVisible()
    assert editor.hasFocus()
    input_text(editor, "中文测试 ABC")
    key(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    input_text(editor, "第二行")
    key(editor, Qt.Key.Key_Return)
    assert session.text_editor is None
    assert len(session.annotations) == 1
    assert session.annotations[0].text == "中文测试 ABC\n第二行"
    assert not session.closed
    result = render_selection(session.image, session.bounds, session.selection, session.annotations)
    assert result != session.image.copy(0, 0, 300, 200)
    session.undo()
    assert not session.annotations
    session.redo()
    assert len(session.annotations) == 1


def test_complete_commits_unconfirmed_text_but_escape_discards_it(editor_session):
    session = editor_session
    session.begin_text(session.canvases[0], 15, 15)
    input_text(session.text_editor, "discard")
    key(session.text_editor, Qt.Key.Key_Escape)
    assert not session.annotations
    assert not session.closed
    session.begin_text(session.canvases[0], 15, 15)
    input_text(session.text_editor, "keep")
    results = []
    session.finished.connect(lambda image, save_only: results.append(image))
    session.complete()
    assert [item.text for item in session.annotations] == ["keep"]
    assert len(results) == 1


def test_switching_tool_commits_text_once(editor_session):
    session = editor_session
    session.begin_text(session.canvases[0], 15, 15)
    input_text(session.text_editor, "one annotation")
    session.toolbar.findChild(QPushButton, "tool_select").click()
    assert len(session.annotations) == 1
    assert session.text_editor is None


def test_right_click_retains_overlay_until_release_and_swallows_context_menu(editor_session):
    session = editor_session
    target = session.canvases[0]
    right_mouse(target, QEvent.Type.MouseButtonPress)
    assert session._right_cancel_pending
    assert not session.closed
    assert target.isVisible()
    context = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(8, 8), QPoint(8, 8))
    QApplication.sendEvent(target, context)
    assert not session.closed
    session.cancel()  # Escape/another cancel cannot expose the pending release.
    assert not session.closed
    right_mouse(target, QEvent.Type.MouseButtonRelease)
    assert not session.closed
    QApplication.processEvents()
    assert session.closed


def test_right_click_on_text_editor_cancels_without_committing(editor_session):
    session = editor_session
    session.begin_text(session.canvases[0], 15, 15)
    input_text(session.text_editor, "not committed")
    viewport = session.text_editor.viewport()
    right_mouse(viewport, QEvent.Type.MouseButtonPress)
    right_mouse(viewport, QEvent.Type.MouseButtonRelease)
    QApplication.processEvents()
    assert session.closed
    assert not session.annotations


@pytest.mark.parametrize("mode", ["mosaic", "blur", "solid"])
def test_effect_buttons_drive_drawn_annotation_mode(editor_session, monkeypatch, mode):
    import snip.overlay as overlay
    session = editor_session
    session.toolbar.findChild(QPushButton, "tool_mosaic").click()
    assert session.toolbar.effect_options.isVisible()
    session.toolbar.findChild(QPushButton, f"effect_{mode}").click()
    monkeypatch.setattr(overlay, "cursor_position", lambda: (25, 30))
    session.canvases[0].mousePressEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton))
    monkeypatch.setattr(overlay, "cursor_position", lambda: (100, 90))
    session.canvases[0].mouseMoveEvent(None)
    session.canvases[0].mouseReleaseEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton))
    assert session.annotations[-1].tool == mode


def checkerboard():
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            image.setPixelColor(x, y, QColor("white" if (x + y) % 2 else "black"))
    return image


def test_pixelation_averages_fine_detail_instead_of_sampling_white(qt_app):
    image = checkerboard()
    result = render_selection(image, Rect(0, 0, 100, 80), Rect(0, 0, 100, 80),
                              [Annotation("mosaic", [(0, 0), (100, 80)], width=3)])
    assert 70 < result.pixelColor(8, 8).red() < 190
    assert result.pixelColor(5, 5) == result.pixelColor(6, 5)


def test_mosaic_samples_prior_annotations_and_handles_negative_origin(qt_app):
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    desktop = Rect(-100, -80, 100, 80)
    marks = [Annotation("solid", [(-100, -80), (0, 0)], color="#206090"),
             Annotation("mosaic", [(-90, -70), (-10, -10)])]
    result = render_selection(image, desktop, desktop, marks)
    assert result.pixelColor(50, 40) == QColor("#206090")


@pytest.mark.parametrize("mode", ["mosaic", "blur", "solid"])
def test_effects_preserve_surrounding_pixels(qt_app, mode):
    image = checkerboard()
    result = render_selection(image, Rect(0, 0, 100, 80), Rect(0, 0, 100, 80),
                              [Annotation(mode, [(20, 20), (80, 60)], color="#132435")])
    assert result.pixelColor(5, 5) == image.pixelColor(5, 5)
    assert result.pixelColor(40, 40) != image.pixelColor(40, 40)
    if mode == "solid":
        assert result.pixelColor(40, 40) == QColor("#132435")
    else:
        assert result.pixelColor(40, 40).alpha() == 255


def test_preview_and_export_use_the_same_composited_image(editor_session):
    session = editor_session
    session.annotations.extend([Annotation("solid", [(0, 0), (100, 100)], color="#206090"),
                                Annotation("blur", [(10, 10), (80, 80)])])
    session.update()
    preview = session.preview()
    assert preview == render_selection(session.image, session.bounds, session.selection, session.annotations)


def draw_points(session, monkeypatch, points):
    import snip.overlay as overlay
    canvas = session.canvases[0]
    event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton)
    monkeypatch.setattr(overlay, "cursor_position", lambda: points[0])
    canvas.mousePressEvent(event)
    for point in points[1:]:
        monkeypatch.setattr(overlay, "cursor_position", lambda point=point: point)
        canvas.mouseMoveEvent(None)
    canvas.mouseReleaseEvent(event)


def patterned_source(session):
    painter = QPainter(session.image)
    for x in range(0, session.image.width(), 8):
        painter.fillRect(x, 0, 4, session.image.height(), QColor("black"))
    painter.end()
    session.update()


def create_text(session, text="Movable text", x=30, y=80):
    session.toolbar.findChild(QPushButton, "tool_text").click()
    session.begin_text(session.canvases[0], x, y)
    input_text(session.text_editor, text)
    key(session.text_editor, Qt.Key.Key_Return)
    return session.current_text()


def action(session, name):
    button = session.toolbar.findChild(QPushButton, f"action_{name}")
    assert button.isEnabled()
    button.click()


@pytest.mark.parametrize("mode", ["mosaic", "blur", "solid"])
def test_effect_brush_follows_bent_path_and_one_stroke_is_one_undo(editor_session, monkeypatch, mode):
    session = editor_session
    patterned_source(session)
    session.toolbar.findChild(QPushButton, "tool_mosaic").click()
    session.toolbar.findChild(QPushButton, f"effect_{mode}").click()
    draw_points(session, monkeypatch, [(40, 40), (140, 40), (140, 140)])
    item = session.annotations[-1]
    assert item.points == [(40, 40), (140, 40), (140, 140)]
    assert item.brush_size == 24
    result = session.preview().copy()
    assert result.pixelColor(80, 40) != session.image.pixelColor(80, 40)
    assert result.pixelColor(140, 100) != session.image.pixelColor(140, 100)
    assert result.pixelColor(80, 100) == session.image.pixelColor(80, 100)
    assert len(session.undo_stack) == 1
    action(session, "undo")
    assert not session.annotations
    assert session.preview() == session.image.copy(0, 0, 300, 200)
    action(session, "redo")
    assert session.preview() == result


def test_mosaic_click_makes_a_round_stamp_even_with_duplicate_mouse_points(editor_session, monkeypatch):
    session = editor_session
    patterned_source(session)
    session.toolbar.findChild(QPushButton, "tool_mosaic").click()
    draw_points(session, monkeypatch, [(80, 80), (80, 80)])
    result = session.preview()
    assert result.pixelColor(80, 80) != session.image.pixelColor(80, 80)
    assert result.pixelColor(91, 91) == session.image.pixelColor(91, 91)


def test_toolbar_stays_owned_and_usable_while_typing_and_can_undo_text(editor_session):
    session = editor_session
    session.begin_text(session.canvases[0], 20, 30)
    input_text(session.text_editor, "toolbar remains usable")
    QApplication.processEvents()
    assert session.text_editor.hasFocus()
    assert session.toolbar.isVisible()
    assert session.toolbar.windowHandle().transientParent() == session.canvases[0].windowHandle()
    assert session.toolbar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    action(session, "undo")
    assert session.text_editor is None
    assert not session.annotations
    assert session.toolbar.isVisible()
    action(session, "redo")
    assert session.annotations[0].text == "toolbar remains usable"


def test_text_can_move_resize_rotate_and_each_operation_can_be_undone(editor_session, monkeypatch):
    import math
    session = editor_session
    canvas = session.canvases[0]
    canvas.scale_x = canvas.scale_y = 1
    item = create_text(session)
    original_position = item.points[0]
    center = text_transform(item).map(text_rect(item).center())
    start = (round(center.x()), round(center.y()))
    draw_points(session, monkeypatch, [start, (start[0] + 12, start[1] + 10)])
    assert session.current_text().points[0] == (original_position[0] + 12, original_position[1] + 10)
    action(session, "undo")
    assert session.current_text().points[0] == original_position
    action(session, "redo")
    item = session.current_text()
    corner = text_handles(item, 28)[2]
    original_scale = item.scale
    draw_points(session, monkeypatch, [(round(corner.x()), round(corner.y())), (round(corner.x() + 30), round(corner.y() + 15))])
    assert session.current_text().scale > original_scale
    resized_scale = session.current_text().scale
    action(session, "undo")
    assert session.current_text().scale == original_scale
    action(session, "redo")
    assert session.current_text().scale == resized_scale
    item = session.current_text()
    center = text_transform(item).map(text_rect(item).center())
    handle = text_handles(item, 28)[4]
    target = (round(center.x() - (handle.y() - center.y())), round(center.y() + (handle.x() - center.x())))
    draw_points(session, monkeypatch, [(round(handle.x()), round(handle.y())), target])
    assert math.isclose(session.current_text().rotation, 90, abs_tol=2)
    action(session, "undo")
    assert session.current_text().rotation == 0
    action(session, "redo")
    assert math.isclose(session.current_text().rotation, 90, abs_tol=2)


def test_text_content_size_and_deletion_are_reversible(editor_session):
    session = editor_session
    create_text(session, "original")
    session.edit_selected_text()
    session.text_editor.setPlainText("changed")
    key(session.text_editor, Qt.Key.Key_Return)
    assert len(session.annotations) == 1
    assert session.current_text().text == "changed"
    action(session, "undo")
    assert session.current_text().text == "original"
    action(session, "redo")
    session.toolbar.font_size.setCurrentText("48")
    assert session.current_text().font_size == 48
    action(session, "undo")
    assert session.current_text().font_size == 25
    action(session, "redo")
    session.toolbar.findChild(QPushButton, "delete_text").click()
    assert not session.annotations
    action(session, "undo")
    assert session.current_text().font_size == 48
    assert session.current_text().text == "changed"


def test_new_edit_after_undo_clears_redo(editor_session, monkeypatch):
    session = editor_session
    session.toolbar.findChild(QPushButton, "tool_rect").click()
    draw_points(session, monkeypatch, [(20, 20), (70, 70)])
    draw_points(session, monkeypatch, [(90, 30), (130, 80)])
    action(session, "undo")
    assert len(session.annotations) == 1
    session.toolbar.findChild(QPushButton, "tool_arrow").click()
    draw_points(session, monkeypatch, [(120, 100), (170, 150)])
    assert not session.redo_stack
    assert not session.toolbar.redo_button.isEnabled()
    assert [item.tool for item in session.annotations] == ["rect", "arrow"]


def test_rotated_text_export_matches_object_geometry_on_negative_monitor(qt_app):
    from copy import deepcopy
    source = QImage(240, 200, QImage.Format.Format_RGB32)
    source.fill(QColor("white"))
    desktop = Rect(-100, -100, 240, 200)
    item = Annotation("text", [(-50, -40)], "#ff0000", text="Text", font_size=20, scale=1.4, rotation=37)
    result = render_selection(source, desktop, desktop, [item])
    bounds = text_transform(item).mapRect(text_rect(item)).adjusted(-2, -2, 2, 2)
    changed = []
    for y in range(result.height()):
        for x in range(result.width()):
            if result.pixelColor(x, y) != QColor("white"):
                changed.append((x + desktop.x, y + desktop.y))
    assert changed
    assert all(bounds.contains(QPointF(x, y)) for x, y in changed)
    flat = deepcopy(item)
    flat.rotation = 0
    assert result != render_selection(source, desktop, desktop, [flat])


def test_double_click_reopens_rotated_text_without_creating_duplicate(editor_session, monkeypatch):
    import snip.overlay as overlay
    session = editor_session
    item = create_text(session, "edit again")
    session.rotate_text(30)
    center = text_transform(item).map(text_rect(item).center())
    monkeypatch.setattr(overlay, "cursor_position", lambda: (round(center.x()), round(center.y())))
    session.canvases[0].mouseDoubleClickEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton))
    QApplication.processEvents()
    assert session.text_editor is not None
    assert session.text_editor.hasFocus()
    assert session.toolbar.isVisible()
    session.text_editor.setPlainText("edited")
    key(session.text_editor, Qt.Key.Key_Return)
    assert len(session.annotations) == 1
    assert session.current_text().text == "edited"
    assert session.current_text().rotation == 30
    action(session, "undo")
    assert session.current_text().text == "edit again"


def test_crop_move_and_resize_can_be_undone_from_toolbar(editor_session, monkeypatch):
    session = editor_session
    before = session.selection
    draw_points(session, monkeypatch, [(80, 80), (90, 88)])
    assert session.selection == before.translated(10, 8)
    action(session, "undo")
    assert session.selection == before
    draw_points(session, monkeypatch, [(300, 200), (280, 180)])
    assert session.selection == Rect(0, 0, 280, 180)
    action(session, "undo")
    assert session.selection == before


def test_mosaic_hover_does_not_toggle_cross_and_blank_on_every_move(editor_session, monkeypatch):
    import snip.overlay as overlay
    session = editor_session
    canvas = session.canvases[0]
    session.toolbar.findChild(QPushButton, "tool_mosaic").click()
    original = canvas.setCursor
    changes = []

    def record_cursor(shape):
        changes.append(shape)
        original(shape)

    monkeypatch.setattr(canvas, "setCursor", record_cursor)
    for x in range(40, 100):
        monkeypatch.setattr(overlay, "cursor_position", lambda x=x: (x, 60))
        canvas.mouseMoveEvent(None)
    assert changes == [Qt.CursorShape.BlankCursor]
    monkeypatch.setattr(overlay, "cursor_position", lambda: (310, 210))
    canvas.mouseMoveEvent(None)
    canvas.mouseMoveEvent(None)
    assert changes == [Qt.CursorShape.BlankCursor, Qt.CursorShape.CrossCursor]


@pytest.mark.parametrize("background", ["white", "black"])
def test_brush_cursor_is_visible_on_light_and_dark_backgrounds(qt_app, background):
    image = QImage(80, 80, QImage.Format.Format_RGB32)
    base = QColor(background)
    image.fill(base)
    center = QPointF(40, 40)
    foreground, outline = brush_cursor_colors(image, center, 16, (0, 0))
    assert abs(foreground.lightness() - base.lightness()) > 200
    painter = QPainter(image)
    paint_brush_cursor(painter, center, 16, foreground, outline, 1)
    painter.end()
    assert abs(image.pixelColor(40, 40).lightness() - base.lightness()) > 100
    assert image.pixelColor(0, 0) == base
