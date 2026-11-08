"""Компиляция полилиний в GCode для GRBL."""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.geometry import Point
from ..domain.shapes import Polyline
from .toolhead import ToolHead


@dataclass(frozen=True)
class CutParams:
    feed_mm_min: float = 1500.0
    passes: int = 1


def order_polylines(polylines: list[Polyline], start: Point = Point(0, 0)) -> list[Polyline]:
    """Порядок «ближайший сосед» от текущей позиции — режем холостые проезды.

    Жадная эвристика: на плоттере с пером это минуты сэкономленного времени.
    """
    remaining = list(polylines)
    ordered: list[Polyline] = []
    cur = start
    while remaining:
        best = min(remaining, key=lambda pl: (pl.start.x - cur.x) ** 2 + (pl.start.y - cur.y) ** 2)
        remaining.remove(best)
        ordered.append(best)
        cur = best.end
    return ordered


def compile_gcode(polylines: list[Polyline], params: CutParams,
                  tool: ToolHead) -> list[str]:
    """Полилинии (мм) → список GCode-строк.

    Контракт: перо всегда поднято на холостых G0, опущено только на G1.
    Замкнутые контуры замыкаются явным сегментом в первую точку.
    """
    if not polylines:
        return []

    out: list[str] = [
        "; acecutter job",
        "G21 ; mm",
        "G90 ; absolute",
        *tool.up(),
    ]

    ordered = order_polylines(polylines)

    for pass_no in range(1, params.passes + 1):
        if params.passes > 1:
            out.append(f"; pass {pass_no}/{params.passes}")
        for pl in ordered:
            pts = [*pl.points, pl.points[0]] if pl.closed else list(pl.points)
            if len(pts) < 2:
                continue
            out.append(f"G0 X{pts[0].x:.3f} Y{pts[0].y:.3f}")
            out.extend(tool.down())
            out.append(f"G1 F{params.feed_mm_min:.0f}")
            prev = pts[0]
            for p in pts[1:]:
                if abs(p.x - prev.x) < 5e-4 and abs(p.y - prev.y) < 5e-4:
                    continue  # нулевой сегмент (дубль от Close) — нахер
                out.append(f"G1 X{p.x:.3f} Y{p.y:.3f}")
                prev = p
            out.extend(tool.up())

    out += ["G0 X0.000 Y0.000", "; end"]
    return out
