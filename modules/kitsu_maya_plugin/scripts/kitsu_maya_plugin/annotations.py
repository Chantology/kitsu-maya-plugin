"""Renders Kitsu drawover annotations (Fabric.js shapes) onto a QPixmap.

Kitsu stores drawover markup as a small Fabric.js canvas per preview file
revision: `preview["annotations"]` is a list of entries shaped like
`{"time": 0, "frame": 0, "drawing": {"objects": [...]}}`. For a still
image (not a video), the relevant entry is the one at time == 0 — Kitsu's
own player picks the same entry the same way (see `findAnnotationAtTime`
in Kitsu's frontend). Each object in `drawing.objects` is a Fabric.js
shape dict: a `type` plus position/size/color fields, with a few
type-specific extras (radius for circles, x1/y1/x2/y2 for lines/arrows,
path commands for freehand strokes, text fields for text objects).

IMPORTANT: object coordinates are NOT authored against the image's native
pixel size. Each object carries its own `canvasWidth`/`canvasHeight` —
the on-screen size of Kitsu's drawing canvas *at the moment it was drawn*
(a browser player widget, commonly much smaller than the actual image
resolution). Kitsu's own renderer (`buildReadOnlyShapeInner` /
`getAnnotationContainMapping`) maps that reference frame onto whatever
target it's rendering into with a "contain" fit (like CSS
`object-fit: contain`): scale by `min(target_w/ref_w, target_h/ref_h)`
and center the result. This module replicates exactly that mapping, once
per object (falling back to the preview's own width/height only if an
object doesn't carry its own canvasWidth/canvasHeight, matching Kitsu's
own fallback).

This covers the shapes Kitsu's own drawover toolbar actually offers
today: rectangle, circle, arrow, text, and the freehand pencil ("PSStroke",
a pressure-sensitive brush stroke from the `fabricjs-psbrush` package —
NOT a plain Fabric 'path', which is what the freehand tool actually
serializes as; this module replicates its exact rendering algorithm, a
sequence of quadratic-curve segments through point midpoints with
per-segment width = point.pressure * strokeWidth). Plain line/ellipse/path
are also handled for completeness. Two things are deliberately NOT
replicated, since they're rare for review notes and meaningfully harder
to get right without a live Fabric.js reference to test against:
  - rotation on freehand strokes / lines / arrows (rare in practice —
    these are usually drawn once and not rotated afterwards)
  - the eraser tool's mask and nested groups

A shape that can't be drawn is skipped rather than raising, so one
unsupported/malformed object never blanks out the rest of the drawover.
"""

import math
import re

from .qt import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRectF,
    Qt,
)

_RGBA_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", re.IGNORECASE
)


def picture_annotation_objects(preview):
    """Return the Fabric shape dicts for a still-image preview's drawover."""
    annotations = preview.get("annotations") or []
    if not annotations:
        return []
    entry = next((a for a in annotations if a.get("time") == 0), annotations[0])
    return (entry.get("drawing") or {}).get("objects") or []


def render_annotations(pixmap, objects, fallback_width=None, fallback_height=None):
    """Return a copy of `pixmap` with the given Fabric shapes drawn on top.

    `fallback_width`/`fallback_height` (typically the preview's own
    width/height) are only used for objects that don't carry their own
    `canvasWidth`/`canvasHeight` — matching Kitsu's own fallback.
    """
    if not objects:
        return pixmap

    result = QPixmap(pixmap)
    target_w, target_h = result.width(), result.height()

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing, True)
    for obj in objects:
        try:
            mapping = _contain_mapping(
                target_w,
                target_h,
                obj.get("canvasWidth") or fallback_width,
                obj.get("canvasHeight") or fallback_height,
            )
            _draw_object(painter, obj, mapping)
        except Exception:
            continue
    painter.end()
    return result


def _contain_mapping(target_width, target_height, ref_width, ref_height):
    """Replicate Kitsu's `getAnnotationContainMapping`: fit ref_w x ref_h
    into target_w x target_h, centered, uniformly scaled."""
    if not ref_width or not ref_height:
        return 1.0, 0.0, 0.0
    scale = min(target_width / ref_width, target_height / ref_height)
    offset_x = (target_width - ref_width * scale) / 2
    offset_y = (target_height - ref_height * scale) / 2
    return scale, offset_x, offset_y


def _color(value):
    if not value or value == "transparent":
        return None

    # QColor doesn't understand CSS rgb()/rgba() strings, which Fabric.js
    # (and Kitsu's own defaults, e.g. text backgroundColor) commonly use.
    match = _RGBA_RE.match(value.strip()) if isinstance(value, str) else None
    if match:
        r, g, b, a = match.groups()
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(float(a) if a is not None else 1.0)
        return color

    color = QColor(value)
    return color if color.isValid() else None


def _pen(obj, width_override=None):
    stroke = _color(obj.get("stroke"))
    if stroke is None:
        return QPen(Qt.NoPen)
    pen = QPen(stroke)
    width = width_override if width_override is not None else (obj.get("strokeWidth") or 1)
    pen.setWidthF(max(width, 0.1))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _brush(obj):
    fill = _color(obj.get("fill"))
    return QBrush(fill) if fill is not None else QBrush(Qt.NoBrush)


def _draw_object(painter, obj, mapping):
    obj_type = obj.get("type")

    # Note: PSStroke is deliberately NOT lowercased/normalized by Kitsu
    # (see normalizeType) — match the exact mixed-case string.
    if obj_type == "PSStroke":
        _draw_ps_stroke(painter, obj, mapping)
        return
    if obj_type in ("line", "arrow"):
        _draw_line(painter, obj, mapping, arrow=(obj_type == "arrow"))
        return
    if obj_type in ("i-text", "text", "textbox"):
        _draw_text(painter, obj, mapping)
        return
    if obj_type == "path":
        _draw_path(painter, obj, mapping)
        return
    if obj_type in ("rect", "circle", "ellipse"):
        _draw_basic_shape(painter, obj, mapping)
        return
    # 'group', 'eraser', and anything else: not supported yet, skip.


def _basic_shape_size(obj):
    obj_type = obj.get("type")
    if obj_type == "circle":
        radius = obj.get("radius") or 0
        return radius * 2, radius * 2
    if obj_type == "ellipse":
        return (obj.get("rx") or 0) * 2, (obj.get("ry") or 0) * 2
    return obj.get("width") or 0, obj.get("height") or 0


def _enter_object_space(painter, obj, mapping, width, height):
    """Move the painter into the object's local, unrotated (0,0)-(w,h) box.

    Fabric positions/rotates objects around their center regardless of
    origin, with `left`/`top` (default origin) marking the unrotated
    bounding box's top-left corner. Position and scale are mapped through
    the object's own contain-fit `mapping` (scale, offset_x, offset_y);
    since the painter is scaled here, stroke widths drawn afterwards need
    no separate scale correction — the transform already covers them.
    """
    scale, offset_x, offset_y = mapping
    scale_x = (obj.get("scaleX") or 1) * scale
    scale_y = (obj.get("scaleY") or 1) * scale
    left = (obj.get("left") or 0) * scale + offset_x
    top = (obj.get("top") or 0) * scale + offset_y
    angle = obj.get("angle") or 0

    painter.save()
    painter.translate(left + (width * scale_x) / 2, top + (height * scale_y) / 2)
    if angle:
        painter.rotate(angle)
    painter.scale(scale_x or 1, scale_y or 1)
    painter.translate(-width / 2, -height / 2)


def _draw_basic_shape(painter, obj, mapping):
    width, height = _basic_shape_size(obj)
    _enter_object_space(painter, obj, mapping, width, height)
    painter.setPen(_pen(obj))
    painter.setBrush(_brush(obj))
    if obj.get("type") in ("circle", "ellipse"):
        painter.drawEllipse(QRectF(0, 0, width, height))
    else:
        painter.drawRect(QRectF(0, 0, width, height))
    painter.restore()


def _text_width(metrics, text):
    if hasattr(metrics, "horizontalAdvance"):
        return metrics.horizontalAdvance(text)
    return metrics.width(text)  # pragma: no cover - older Qt fallback


def _draw_text(painter, obj, mapping):
    text = obj.get("text") or ""

    font = QFont(obj.get("fontFamily") or "Arial")
    font.setPointSizeF(max((obj.get("fontSize") or 16), 1))
    if obj.get("fontWeight") in ("bold", 700):
        font.setBold(True)

    # Only 'textbox' is a fixed-width, word-wrapping box in Fabric.
    # 'i-text'/'text' grow to fit their content and never wrap — wrapping
    # those cuts text off, since Kitsu's own (browser) font metrics for
    # the recorded width rarely match Qt's exactly.
    wrap = obj.get("type") == "textbox"
    width, height = _basic_shape_size(obj)
    if not wrap:
        metrics = QFontMetricsF(font)
        width = max(width, _text_width(metrics, text))
        height = max(height, metrics.height())

    _enter_object_space(painter, obj, mapping, width, height)

    background = _color(obj.get("backgroundColor"))
    rect = QRectF(0, 0, width, height)
    if background is not None:
        painter.fillRect(rect, background)

    painter.setFont(font)
    text_color = _color(obj.get("fill")) or QColor("black")
    painter.setPen(QPen(text_color))
    flags = Qt.TextWordWrap if wrap else Qt.TextSingleLine
    painter.drawText(rect, flags, text)
    painter.restore()


def _draw_path(painter, obj, mapping):
    commands = obj.get("path") or []
    xs, ys = [], []
    for command in commands:
        for i in range(1, len(command), 2):
            xs.append(command[i])
            ys.append(command[i + 1])
    if not xs:
        return
    origin_x, origin_y = min(xs), min(ys)

    path = QPainterPath()
    for command in commands:
        op = command[0]
        coords = [
            (command[i] - origin_x, command[i + 1] - origin_y)
            for i in range(1, len(command), 2)
        ]
        if op == "M" and coords:
            path.moveTo(*coords[0])
        elif op == "L" and coords:
            path.lineTo(*coords[0])
        elif op == "Q" and len(coords) >= 2:
            path.quadTo(coords[0][0], coords[0][1], coords[1][0], coords[1][1])
        elif op == "C" and len(coords) >= 3:
            path.cubicTo(
                coords[0][0], coords[0][1],
                coords[1][0], coords[1][1],
                coords[2][0], coords[2][1],
            )
        elif op == "Z":
            path.closeSubpath()

    scale, offset_x, offset_y = mapping
    scale_x = (obj.get("scaleX") or 1) * scale
    scale_y = (obj.get("scaleY") or 1) * scale
    left = (obj.get("left") or 0) * scale + offset_x
    top = (obj.get("top") or 0) * scale + offset_y

    painter.save()
    painter.translate(left, top)
    painter.scale(scale_x or 1, scale_y or 1)
    painter.setPen(_pen(obj))
    painter.setBrush(_brush(obj))
    painter.drawPath(path)
    painter.restore()


def _draw_ps_stroke(painter, obj, mapping):
    """Replicate PSStroke._renderStroke from fabricjs-psbrush: a chain of
    quadratic-curve segments through consecutive point midpoints, each
    segment's width set by that point's pressure * strokeWidth. Points are
    in the same absolute (pre-mapping) coordinate space as x1/y1 on
    line/arrow objects, so the same contain-fit mapping applies directly
    — no local object-space transform needed (rotation isn't replicated
    here, consistent with the plain-path/line simplification).
    """
    points = obj.get("strokePoints") or []
    if len(points) < 2:
        return

    scale, offset_x, offset_y = mapping
    stroke_width = obj.get("strokeWidth") or 1
    stroke_color = _color(obj.get("stroke")) or QColor("black")

    def mapped(point):
        x = (point.get("x") or 0) * scale + offset_x
        y = (point.get("y") or 0) * scale + offset_y
        pressure = point.get("pressure")
        return x, y, (pressure if pressure is not None else 1)

    mapped_points = [mapped(p) for p in points]

    pen = QPen(stroke_color)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)

    count = len(mapped_points)
    p1 = mapped_points[0]
    p2 = mapped_points[1]
    mid = p1
    for i in range(1, count):
        next_mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)

        path = QPainterPath()
        path.moveTo(mid[0], mid[1])
        path.quadTo(p1[0], p1[1], next_mid[0], next_mid[1])

        pen.setWidthF(max(p1[2] * stroke_width * scale, 0.5))
        painter.setPen(pen)
        painter.drawPath(path)

        mid = next_mid
        p1 = mapped_points[i]
        p2 = mapped_points[i + 1] if i + 1 < count else p1


def _draw_line(painter, obj, mapping, arrow):
    scale, offset_x, offset_y = mapping
    x1 = (obj.get("x1") or 0) * scale + offset_x
    y1 = (obj.get("y1") or 0) * scale + offset_y
    x2 = (obj.get("x2") or 0) * scale + offset_x
    y2 = (obj.get("y2") or 0) * scale + offset_y

    stroke_width = (obj.get("strokeWidth") or 1) * scale
    pen = _pen(obj, width_override=stroke_width)
    if pen.style() == Qt.NoPen:
        pen = QPen(QColor(obj.get("stroke") or "black"))
        pen.setWidthF(max(stroke_width, 0.1))
    painter.setPen(pen)
    painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    if not arrow:
        return

    head_size = (obj.get("arrowHeadSize") or 15) * scale
    head_width = (obj.get("arrowHeadWidth") or 12) * scale
    angle = math.atan2(y2 - y1, x2 - x1)
    spread = math.atan2(head_width / 2, head_size)

    left_x = x2 - head_size * math.cos(angle - spread)
    left_y = y2 - head_size * math.sin(angle - spread)
    right_x = x2 - head_size * math.cos(angle + spread)
    right_y = y2 - head_size * math.sin(angle + spread)

    head = QPainterPath()
    head.moveTo(x2, y2)
    head.lineTo(left_x, left_y)
    head.lineTo(right_x, right_y)
    head.closeSubpath()
    painter.setBrush(QBrush(pen.color()))
    painter.drawPath(head)
