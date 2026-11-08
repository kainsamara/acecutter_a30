"""Overcut — продление реза для замкнутых и открытых контуров.

Смысл: у винильного резака точка входа ножа всегда даёт недорез —
наклейка висит на подложке именно в месте старта. Лечение: начать рез
чуть РАНЬШЕ стартовой точки (пройдя хвост контура назад) и закончить
чуть ПОЗЖЕ (продолжив по началу контура). Для замкнутого контура это
тот же контур, пройденный с перехлёстом на overcut мм в обе стороны.
"""
from __future__ import annotations

import math

from ..domain.geometry import Point
from ..domain.shapes import Polyline


def apply_overcut(polylines: list[Polyline], mm: float) -> list[Polyline]:
    """Карандаш (mm=0) — контуры как есть; резак — с продлением."""
    if mm <= 0:
        return polylines
    return [overcut_closed(pl, mm) if pl.closed else extend_open(pl, mm)
            for pl in polylines]


def extend_open(pl: Polyline, mm: float) -> Polyline:
    """Открытый контур: продлить по касательной (направлению крайних сегментов)."""
    if len(pl.points) < 2:
        return pl
    p0, p1 = pl.points[0], pl.points[1]
    q0, q1 = pl.points[-2], pl.points[-1]
    d0 = _unit(p1.x - p0.x, p1.y - p0.y)
    d1 = _unit(q1.x - q0.x, q1.y - q0.y)
    start = Point(p0.x - d0[0] * mm, p0.y - d0[1] * mm)
    end = Point(q1.x + d1[0] * mm, q1.y + d1[1] * mm)
    return Polyline([start, *pl.points, end], closed=False)


def overcut_closed(pl: Polyline, mm: float) -> Polyline:
    """Замкнутый контур: пройти хвост (start−overcut … start) + полный круг
    + продолжение (start … start+overcut). Результат открытый — он уже
    «пере-замкнут» с перехлёстом.
    """
    if len(pl.points) < 3:
        return pl
    cyc = [*pl.points, pl.points[0]]
    # кумулятивные длины вершин цикла: [(s_i, point_i)], s_0 = 0
    vs: list[tuple[float, Point]] = [(0.0, pl.points[0])]
    s = 0.0
    for i in range(1, len(cyc)):
        s += math.dist(cyc[i - 1], cyc[i])
        vs.append((s, cyc[i]))
    total = s
    if total < 4.0 * mm:  # мелкий контур — перехлёст его просто замкнёт
        return pl

    def p_at(dist: float) -> Point:
        d = dist % total
        for k in range(1, len(vs)):
            if d <= vs[k][0]:
                s0, p0 = vs[k - 1]
                s1, p1 = vs[k]
                t = 0.0 if s1 == s0 else (d - s0) / (s1 - s0)
                return Point(p0.x + (p1.x - p0.x) * t, p0.y + (p1.y - p0.y) * t)
        return vs[-1][1]

    tail_start = total - mm
    pts: list[Point] = [p_at(tail_start)]
    pts += [p for si, p in vs if tail_start < si < total]  # хвост до старта
    # полный круг: старт и все вершины цикла, включая возврат в старт (s=total)
    pts += [p for si, p in vs if si <= total]
    pts += [p for si, p in vs if 0 < si < mm]  # продолжение после старта
    pts.append(p_at(mm))
    return Polyline(pts, closed=False)


def _unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy)
    if n < 1e-12:
        return 0.0, 0.0
    return dx / n, dy / n
