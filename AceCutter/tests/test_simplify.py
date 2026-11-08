from __future__ import annotations

import math

from acecutter.domain.geometry import Point
from acecutter.domain.shapes import Polyline
from acecutter.domain.simplify import rdp, simplify, simplify_polyline


def _line_with_jitter(n=200):
    return [Point(i * 0.1, 0.001 * (-1) ** i) for i in range(n)]


def _quarter_circle(r=10.0, n=400):
    return [Point(r * math.cos(t), r * math.sin(t))
            for t in (i * (math.pi / 2) / (n - 1) for i in range(n))]


def test_straight_line_collapses_to_two_points():
    out = rdp(_line_with_jitter(), 0.05)
    assert len(out) == 2
    assert out[0].x == 0.0 and abs(out[-1].x - 19.9) < 1e-9


def test_circle_keeps_shape_within_tolerance():
    pts = _quarter_circle()
    out = rdp(pts, 0.05)
    assert 8 <= len(out) < len(pts) // 4, "овал должен сжаться в разы"
    # концы сохранены
    assert out[0] == pts[0] and out[-1] == pts[-1]
    # каждая исходная точка — в пределах допуска от ломаной out
    for k, p in enumerate(pts):
        if any(p == q for q in out):
            continue
        best = min(
            _dist_to_seg(p, out[i], out[i + 1])
            for i in range(len(out) - 1)
        )
        assert best <= 0.05 + 1e-9, (k, best)


def _dist_to_seg(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / seg2))
    return math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)


def test_closed_square_drops_midpoints():
    sq = Polyline([Point(0, 0), Point(5, 0), Point(10, 0), Point(10, 5),
                   Point(10, 10), Point(5, 10), Point(0, 10), Point(0, 5)], True)
    out = simplify_polyline(sq, 0.05)
    assert out.closed and len(out.points) == 4, out.points
    assert (Point(0, 0) in out.points) and (Point(10, 10) in out.points)


def test_tiny_and_open():
    assert simplify_polyline(Polyline([Point(0, 0), Point(1, 1)], False), 0.1).points \
        == [Point(0, 0), Point(1, 1)]
    tri = Polyline([Point(0, 0), Point(5, 0.001), Point(10, 0)], False)
    assert len(simplify_polyline(tri, 0.05).points) == 2


def test_simplify_list():
    out = simplify([Polyline([Point(0, 0), Point(5, 0.001), Point(10, 0)], False)], 0.05)
    assert len(out) == 1 and len(out[0].points) == 2
