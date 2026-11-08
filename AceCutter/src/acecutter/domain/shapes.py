"""Векторные примитивы документа."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Point, Transform


@dataclass
class Polyline:
    points: list[Point]
    closed: bool = False

    @property
    def start(self) -> Point:
        return self.points[0]

    @property
    def end(self) -> Point:
        return self.points[-1]

    def bounds(self) -> tuple[float, float, float, float] | None:
        if not self.points:
            return None
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def transformed(self, t: Transform) -> "Polyline":
        return Polyline([t.apply(p) for p in self.points], self.closed)

    def length(self) -> float:
        total = 0.0
        for i in range(1, len(self.points)):
            total += math.dist(self.points[i - 1], self.points[i])
        if self.closed and len(self.points) > 2:
            total += math.dist(self.points[-1], self.points[0])
        return total
