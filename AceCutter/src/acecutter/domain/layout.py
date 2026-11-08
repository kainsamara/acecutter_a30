"""Раскладка контуров на рабочем поле: вписать в ширину, центровка, к нулю.

Поле плоттера — рулон: ширина жёсткая (X, у станка $130=245 мм),
длина — валик подачи, практически не ограничена. Поэтому все функции
оперируют только шириной; по длине ограничений не накладываем.
"""
from __future__ import annotations

from ..domain.geometry import Point, Transform
from ..domain.shapes import Polyline


def polylines_bounds(polylines: list[Polyline]) -> tuple[float, float, float, float] | None:
    rects = [b for b in (pl.bounds() for pl in polylines) if b is not None]
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def fit_width_transform(polylines: list[Polyline], width_mm: float,
                        padding_mm: float = 2.0) -> Transform:
    """Масштаб «вписать в ширину поля» + прижать к левому краю с отступом.

    Узкие детали не увеличивает (масштаб 1:1 важен для резки), только
    ужимает то, что шире поля.
    """
    b = polylines_bounds(polylines)
    if b is None:
        return Transform()
    min_x, _, max_x, _ = b
    w = max_x - min_x
    available = max(width_mm - 2 * padding_mm, 1.0)
    s = 1.0 if w <= available else available / w
    t = Transform.scale(s)
    b2 = polylines_bounds([pl.transformed(t) for pl in polylines]) or (0, 0, 0, 0)
    return Transform.translate(padding_mm - b2[0], 0.0) @ t


def center_x_transform(polylines: list[Polyline], width_mm: float) -> Transform:
    b = polylines_bounds(polylines)
    if b is None:
        return Transform()
    return Transform.translate(width_mm / 2.0 - (b[0] + b[2]) / 2.0, 0.0)


def to_origin_transform(polylines: list[Polyline], margin_mm: float = 0.0) -> Transform:
    """Прижать к началу поля: min_x/min_y → margin (удобно начинать рез от края)."""
    b = polylines_bounds(polylines)
    if b is None:
        return Transform()
    return Transform.translate(margin_mm - b[0], margin_mm - b[1])


def scale_about_transform(polylines: list[Polyline],
                          sx: float, sy: float | None = None) -> Transform:
    """Масштаб вокруг центра bounds (полилиния не уползает при увеличении)."""
    if sy is None:
        sy = sx
    b = polylines_bounds(polylines)
    if b is None:
        return Transform()
    cx = (b[0] + b[2]) / 2.0
    cy = (b[1] + b[3]) / 2.0
    return (Transform.translate(cx, cy)
            @ Transform.scale(sx, sy)
            @ Transform.translate(-cx, -cy))


# --- интерактивный масштаб за хэндлы -------------------------------------

HANDLES = ("tl", "tr", "bl", "br", "l", "r", "t", "b")


def handle_scale(bounds: tuple[float, float, float, float],
                 handle: str, mx: float, my: float, *,
                 proportional: bool = False,
                 min_size_mm: float = 0.5) -> tuple[float, float, float, float]:
    """Масштаб за хэндл рамки выделения.

    handle: tl/tr/bl/br (углы) или l/r/t/b (рёбра). mx/my — курсор в мм.
    Пивот — противоположная сторона/угол (тянем правый край — левый стоит).
    proportional=True (Shift) — соотношение сторон лочится (масштаб = max).
    Возвращает (sx, sy, pivot_x, pivot_y).
    """
    x0, y0, x1, y1 = bounds
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    px = x1 if "l" in handle else x0   # тянем лево — пивот правый край
    py = y1 if "b" in handle else y0   # тянем низ — пивот верх (мм: Y вверх)
    sx = sy = 1.0
    if "r" in handle:
        sx = max(mx - px, min_size_mm) / w
    elif "l" in handle:
        sx = max(px - mx, min_size_mm) / w
    if "t" in handle:
        sy = max(my - py, min_size_mm) / h
    elif "b" in handle:
        sy = max(py - my, min_size_mm) / h
    if proportional:
        s = max(sx, sy)
        sx = sy = s
    return sx, sy, px, py


def apply(polylines: list[Polyline], t: Transform) -> list[Polyline]:
    return [pl.transformed(t) for pl in polylines]


def clamp_point(p: Point, width_mm: float) -> Point:  # pragma: no cover - хелпер
    return Point(min(max(p.x, 0.0), width_mm), p.y)
