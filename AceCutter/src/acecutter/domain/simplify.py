"""Упрощение полилиний (Ramer–Douglas–Peucker, итеративный, без рекурсии).

Гарантия: каждая выброшенная точка отстоит от упрощённой линии не дальше
tolerance_mm. Дискретизация кривых даёт десятки точек на миллиметр дуги —
для плоттера это пустой молот: RDP сжимает «овалы» в разы, не трогая форму
за пределами допуска.
"""
from __future__ import annotations

import math

from .geometry import Point
from .shapes import Polyline


def rdp(points: list[Point], tol: float) -> list[Point]:
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best_d, best_k = -1.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            if seg2 <= 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > best_d:
                best_d, best_k = d, k
        if best_d > tol:
            keep[best_k] = True
            stack.append((i, best_k))
            stack.append((best_k, j))
    return [p for p, k in zip(points, keep) if k]


def simplify_polyline(pl: Polyline, tol_mm: float) -> Polyline:
    if len(pl.points) < 3 or tol_mm <= 0:
        return pl
    if pl.closed:
        ring = [*pl.points, pl.points[0]]
        out = rdp(ring, tol_mm)
        if len(out) > 1 and math.dist(out[0], out[-1]) < 1e-9:
            out = out[:-1]
        if len(out) < 3:  # мелкое кольцо вырождается — оставляем как было
            return pl
        return Polyline(out, closed=True)
    return Polyline(rdp(pl.points, tol_mm), closed=False)


def simplify(polylines: list[Polyline], tol_mm: float) -> list[Polyline]:
    return [simplify_polyline(pl, tol_mm) for pl in polylines]
