"""Геометрия: точки и аффинные преобразования. Единицы — миллиметры."""
from __future__ import annotations

import math
from typing import NamedTuple


class Point(NamedTuple):
    x: float
    y: float


class Transform:
    """2D-аффинное преобразование, матрица в порядке SVG: [[a, c, e], [b, d, f]]."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a: float = 1.0, b: float = 0.0, c: float = 0.0,
                 d: float = 1.0, e: float = 0.0, f: float = 0.0) -> None:
        self.a, self.b, self.c, self.d = a, b, c, d
        self.e, self.f = e, f

    def apply(self, p: Point) -> Point:
        return Point(self.a * p.x + self.c * p.y + self.e,
                     self.b * p.x + self.d * p.y + self.f)

    def __matmul__(self, o: "Transform") -> "Transform":
        """self @ o: к точке сначала применяется o, потом self."""
        return Transform(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    @classmethod
    def translate(cls, dx: float, dy: float) -> "Transform":
        return cls(e=dx, f=dy)

    @classmethod
    def scale(cls, sx: float, sy: float | None = None) -> "Transform":
        return cls(a=sx, d=sx if sy is None else sy)

    @classmethod
    def rotate(cls, deg: float) -> "Transform":
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        return cls(c, s, -s, c, 0.0, 0.0)
