from __future__ import annotations

import math

from acecutter.domain.geometry import Point
from acecutter.domain.shapes import Polyline
from acecutter.toolpath.overcut import apply_overcut, extend_open, overcut_closed

SQUARE = Polyline([Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)], True)
LINE = Polyline([Point(0, 0), Point(10, 0)], False)


def test_zero_overcut_is_noop():
    assert apply_overcut([SQUARE, LINE], 0.0) == [SQUARE, LINE]


def test_open_line_extended_both_ends():
    out = extend_open(LINE, 1.0)
    assert out.points[0].x == -1.0 and out.points[-1].x == 11.0
    assert not out.closed
    assert math.isclose(out.length(), 12.0)


def test_closed_square_overlap():
    """Квадрат 10×10 (периметр 40) с overcut 1: путь 42 мм, старт и конец
    разнесены, стартовая точка — на 1 мм позади угла (0,0) по контуру."""
    out = overcut_closed(SQUARE, 1.0)
    assert not out.closed
    assert math.isclose(out.length(), 42.0, abs_tol=1e-9)
    start, end = out.points[0], out.points[-1]
    # хвост: последний сегмент контура (0,10)->(0,0), за 1 мм до старта => (0,1)
    assert math.isclose(start.x, 0.0, abs_tol=1e-9)
    assert math.isclose(start.y, 1.0, abs_tol=1e-9)
    # продолжение: первый сегмент (0,0)->(10,0), на 1 мм дальше => (1,0)
    assert math.isclose(end.x, 1.0, abs_tol=1e-9)
    assert math.isclose(end.y, 0.0, abs_tol=1e-9)


def test_tiny_contour_not_butchered():
    """Контур меньше 4×overcut не должен превращаться в кашу."""
    tiny = Polyline([Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)], True)
    assert overcut_closed(tiny, 10.0) is tiny


def test_apply_overcut_mixed():
    out = apply_overcut([SQUARE, LINE], 0.5)
    assert len(out) == 2
    assert not out[0].closed and not out[1].closed
