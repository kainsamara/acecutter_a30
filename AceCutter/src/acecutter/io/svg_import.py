"""Импорт SVG через svgelements.

svgelements сам применяет transform-ы групп и viewport (width/viewBox) и
отдаёт координаты в px при 96 ppi — нам остаётся привести px → мм.
Кривые Безье/дуги дискретизируются в полилинии с допуском по хорде,
разрывы subpath внутри одного <path> дают отдельные контуры (никаких
соединительных линий через весь лист). Относительные команды (m/c/l/v/z)
понимает сам парсер — регрессионный тест держит Inkscape-экспорт.
"""
from __future__ import annotations

import math
from pathlib import Path

from svgelements import SVG, Shape as SvgShape

from ..domain.geometry import Point
from ..domain.shapes import Polyline
from ..domain.simplify import simplify as simplify_polylines

PX_TO_MM = 25.4 / 96.0
_EPS = 1e-9
_LINEAR = {"Line", "Close", "Linear"}


def import_svg(path: str | Path, *, tolerance_mm: float = 0.05,
               simplify_mm: float = 0.08) -> list[Polyline]:
    k = PX_TO_MM
    step_px = max(tolerance_mm, 0.01) / k
    svg = SVG.parse(str(path))
    result: list[Polyline] = []
    for el in svg.elements():
        if not isinstance(el, SvgShape):
            continue
        for segs in [_segments(el)]:
            for pl in _polylines_from_segments(segs, step_px):
                # SVG: ось Y вниз; наше поле: Y вверх → флип по вертикали,
                # иначе рисунок зеркален относительно вида в редакторе
                result.append(Polyline(
                    [Point(q.x * k, -q.y * k) for q in pl.points], pl.closed))
    # лёгкое упрощение: дискретизация мельче, чем физика пера — молоть зазря
    return simplify_polylines(result, simplify_mm)


def _segments(el: SvgShape):
    segs = el.segments
    if callable(segs):
        segs = segs()
    return segs or []


def _polylines_from_segments(segs, step: float) -> list[Polyline]:
    polylines: list[Polyline] = []
    pts: list[Point] = []

    def flush() -> None:
        nonlocal pts
        dedup = [q for i, q in enumerate(pts)
                 if i == 0 or math.dist(q, pts[i - 1]) > _EPS]
        if len(dedup) >= 2:
            closed = math.dist(dedup[0], dedup[-1]) < step
            polylines.append(Polyline(dedup, closed))
        pts = []

    for seg in segs:
        if type(seg).__name__ == "Move" or seg.start is None or seg.end is None:
            continue
        seg_len = _seg_len_estimate(seg)
        if seg_len < _EPS:
            continue
        sx, sy = seg.start.real, seg.start.imag
        if pts and math.dist(pts[-1], Point(sx, sy)) > step:
            flush()  # разрыв subpath — новый контур
        if not pts:
            pts.append(Point(sx, sy))
        ex, ey = seg.end.real, seg.end.imag
        if type(seg).__name__ in _LINEAR:
            pts.append(Point(ex, ey))
        else:  # CubicBezier / QuadraticBezier / Arc
            n = max(2, min(4096, math.ceil(seg_len / step)))
            for i in range(1, n + 1):
                q = seg.point(i / n)
                pts.append(Point(q.real, q.imag))
    flush()
    return polylines


def _seg_len_estimate(seg) -> float:
    """Дешёвая верхняя оценка длины сегмента: контрольный полигон Безье
    (или хорда). Точный seg.length() у svgelements на некоторых файлах
    уходит в адаптивную квадратуру по ~0.4 с НА СЕГМЕНТ — импорт вис.
    """
    try:
        pts = [seg.start, seg.end]
        for attr in ("control1", "control2"):
            c = getattr(seg, attr, None)
            if c is not None:
                pts.insert(-1, c)
        poly = sum(abs(b - a) for a, b in zip(pts, pts[1:]))
        chord = abs(pts[-1] - pts[0])
        return max(poly, chord)
    except Exception:
        return 0.0
