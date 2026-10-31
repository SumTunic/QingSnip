from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from .geometry import Rect

EFFECT_TOOLS = {"mosaic", "blur", "solid"}


def blur_image(image: QImage, radius: int) -> QImage:
    """Qt's native blur, with opaque replicated edges to avoid a pale fringe."""
    padding = radius * 2 + 2
    width, height = image.width(), image.height()
    padded = QImage(width + padding * 2, height + padding * 2, QImage.Format.Format_RGB32)
    painter = QPainter(padded)
    painter.drawImage(padding, padding, image)
    painter.drawImage(QRectF(padding, 0, width, padding), image, QRectF(0, 0, width, 1))
    painter.drawImage(QRectF(padding, padding + height, width, padding), image, QRectF(0, height - 1, width, 1))
    painter.drawImage(QRectF(0, padding, padding, height), image, QRectF(0, 0, 1, height))
    painter.drawImage(QRectF(padding + width, padding, padding, height), image, QRectF(width - 1, 0, 1, height))
    for x, y, sx, sy in ((0, 0, 0, 0), (padding + width, 0, width - 1, 0),
                          (0, padding + height, 0, height - 1), (padding + width, padding + height, width - 1, height - 1)):
        painter.fillRect(x, y, padding, padding, image.pixelColor(sx, sy))
    painter.end()
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(padded))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    bounds = QRectF(0, 0, padded.width(), padded.height())
    result = QImage(padded.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    scene.render(painter, bounds, bounds)
    painter.end()
    return result.copy(padding, padding, width, height).convertToFormat(QImage.Format.Format_RGB32)


@dataclass
class Annotation:
    tool: str
    points: list[tuple[int, int]] = field(default_factory=list)
    color: str = "#ff4d64"
    width: int = 3
    text: str = ""
    brush_size: float = 0
    font_size: float = 0
    scale: float = 1
    rotation: float = 0

    def translate(self, dx: int, dy: int):
        self.points = [(x + dx, y + dy) for x, y in self.points]


def text_font(item: Annotation) -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(max(8, round(item.font_size or (16 + item.width * 3))))
    font.setWeight(QFont.Weight.DemiBold)
    return font


def text_rect(item: Annotation) -> QRectF:
    metrics = QFontMetricsF(text_font(item))
    lines = item.text.splitlines() or [""]
    return QRectF(0, 0, max(24, max(metrics.horizontalAdvance(line) for line in lines)) + 8,
                  metrics.lineSpacing() * len(lines) + 8)


def text_transform(item: Annotation) -> QTransform:
    rect = text_rect(item)
    x, y = item.points[0]
    transform = QTransform()
    transform.translate(x + rect.width() * item.scale / 2, y + rect.height() * item.scale / 2)
    transform.rotate(item.rotation)
    transform.scale(item.scale, item.scale)
    transform.translate(-rect.width() / 2, -rect.height() / 2)
    return transform


def text_handles(item: Annotation, gap=28) -> list[QPointF]:
    rect, transform = text_rect(item), text_transform(item)
    return [transform.map(point) for point in (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft(),
                                               QPointF(rect.width() / 2, -gap / item.scale))]


def brush_path(item: Annotation) -> QPainterPath:
    points = []
    for point in item.points:
        if not points or points[-1] != point:
            points.append(point)
    path = QPainterPath(QPointF(*points[0]))
    if len(points) == 1:
        path.addEllipse(QPointF(*points[0]), item.brush_size / 2, item.brush_size / 2)
        return path
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    stroker = QPainterPathStroker()
    stroker.setWidth(item.brush_size)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def paint_annotation(painter: QPainter, item: Annotation, source: QImage, origin: tuple[int, int]):
    if not item.points:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = QColor(item.color)
    painter.setPen(QPen(color, item.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    start, end = item.points[0], item.points[-1]
    rect = QRectF(QPointF(*start), QPointF(*end)).normalized()
    stroke = None
    if item.tool in EFFECT_TOOLS and item.brush_size > 0:
        stroke = brush_path(item)
        painter.setClipPath(stroke, Qt.ClipOperation.IntersectClip)
        rect = stroke.boundingRect()
        padding = max(8, item.width * 6) if item.tool == "blur" else 0
        rect = rect.adjusted(-padding, -padding, padding, padding)
        if item.tool == "mosaic":
            block = max(6, item.width * 5)
            left, top = math.floor(rect.left() / block) * block, math.floor(rect.top() / block) * block
            rect = QRectF(left, top, math.ceil(rect.right() / block) * block - left, math.ceil(rect.bottom() / block) * block - top)
        rect = rect.intersected(QRectF(origin[0], origin[1], source.width(), source.height())).toAlignedRect().toRectF()
    if item.tool == "rect":
        painter.drawRect(rect)
    elif item.tool == "ellipse":
        painter.drawEllipse(rect)
    elif item.tool == "pen":
        path = QPainterPath(QPointF(*start))
        for point in item.points[1:]:
            path.lineTo(QPointF(*point))
        if len(item.points) == 1:
            painter.drawPoint(QPointF(*start))
        else:
            painter.drawPath(path)
    elif item.tool == "arrow":
        painter.drawLine(QPointF(*start), QPointF(*end))
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        length = max(13, item.width * 4)
        points = [QPointF(*end)]
        for delta in (-0.48, 0.48):
            points.append(QPointF(end[0] - length * math.cos(angle + delta), end[1] - length * math.sin(angle + delta)))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF(points))
    elif item.tool == "text":
        font = text_font(item)
        painter.setTransform(text_transform(item), True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for index, line in enumerate(item.text.splitlines()):
            painter.drawText(QPointF(4, 4 + metrics.ascent() + index * metrics.lineSpacing()), line)
    elif item.tool == "solid":
        painter.fillRect(rect, color)
    elif item.tool in ("mosaic", "blur") and rect.width() >= 1 and rect.height() >= 1:
        crop = source.copy(int(rect.x()) - origin[0], int(rect.y()) - origin[1], int(rect.width()), int(rect.height()))
        if not crop.isNull():
            if item.tool == "blur":
                pixelated = blur_image(crop, max(4, item.width * 3))
            else:
                block = max(6, item.width * 5)
                # Area averaging keeps small dark text and edges in each block;
                # nearest-neighbour downsampling could sample only white gaps.
                tiny = crop.scaled(max(1, math.ceil(crop.width() / block)), max(1, math.ceil(crop.height() / block)), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                pixelated = tiny.scaled(crop.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(rect, pixelated)
    painter.restore()


def render_selection(source: QImage, desktop: Rect, selection: Rect, annotations: list[Annotation]) -> QImage:
    selection = selection.intersect(desktop)
    result = source.copy(selection.x - desktop.x, selection.y - desktop.y, selection.w, selection.h)
    if result.isNull():
        raise ValueError("截图区域为空")
    painter = QPainter(result)
    painter.translate(-selection.x, -selection.y)
    for annotation in annotations:
        # Effects sample the current composite, including previous annotations.
        paint_annotation(painter, annotation, result if annotation.tool in EFFECT_TOOLS else source,
                         (selection.x, selection.y) if annotation.tool in EFFECT_TOOLS else (desktop.x, desktop.y))
    painter.end()
    return result
