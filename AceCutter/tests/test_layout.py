from __future__ import annotations

from acecutter.domain.geometry import Point
from acecutter.domain.layout import (
    center_x_transform, fit_width_transform, handle_scale, polylines_bounds,
    scale_about_transform, to_origin_transform,
)
from acecutter.domain.shapes import Polyline

WIDE = Polyline([Point(0, 0), Point(300, 0), Point(300, 50), Point(0, 50)], True)
NARROW = Polyline([Point(10, 10), Point(110, 10), Point(110, 60), Point(10, 60)], True)


def test_bounds_union():
    assert polylines_bounds([NARROW]) == (10.0, 10.0, 110.0, 60.0)
    assert polylines_bounds([]) is None


def test_fit_width_scales_down_only():
    # 300 мм в поле 245 → ужаться до 241 (padding 2 с каждой стороны)
    t = fit_width_transform([WIDE], 245.0)
    b = polylines_bounds([WIDE.transformed(t)]) or (0, 0, 0, 0)
    assert abs((b[2] - b[0]) - 241.0) < 1e-9
    assert abs(b[0] - 2.0) < 1e-9  # прижато к левому краю с отступом


def test_fit_width_never_scales_up():
    t = fit_width_transform([NARROW], 245.0)
    b = polylines_bounds([NARROW.transformed(t)]) or (0, 0, 0, 0)
    assert (b[2] - b[0]) == 100.0  # 1:1, деталь уже влезает


def test_center_x():
    t = center_x_transform([NARROW], 245.0)
    b = polylines_bounds([NARROW.transformed(t)]) or (0, 0, 0, 0)
    assert abs((b[0] + b[2]) / 2 - 122.5) < 1e-9


def test_to_origin():
    t = to_origin_transform([NARROW])
    b = polylines_bounds([NARROW.transformed(t)]) or (0, 0, 0, 0)
    assert b[0] == 0.0 and b[1] == 0.0


def test_scale_about_center():
    sq = Polyline([Point(10, 10), Point(30, 10), Point(30, 30), Point(10, 30)], True)
    # пропорциональный ×2 вокруг центра (20,20): 0..40
    t = scale_about_transform([sq], 2.0)
    assert polylines_bounds([sq.transformed(t)]) == (0.0, 0.0, 40.0, 40.0)
    # только X ×2: 0..40 по X, Y прежний 10..30
    t = scale_about_transform([sq], 2.0, 1.0)
    assert polylines_bounds([sq.transformed(t)]) == (0.0, 10.0, 40.0, 30.0)


def test_handle_scale_edge():
    # тянем правое ребро: пивот левый край (x0=0), мышь на 30 → sx=1.5
    sx, sy, px, py = handle_scale((0, 0, 20, 10), "r", 30, 5)
    assert sx == 1.5 and sy == 1.0 and px == 0.0


def test_handle_scale_corner_and_shift():
    # угол br: пивот (0,10); мышь (30, -2): w→30, h→12
    sx, sy, px, py = handle_scale((0, 0, 20, 10), "br", 30, -2)
    assert sx == 1.5 and abs(sy - 1.2) < 1e-9 and px == 0.0 and py == 10.0
    # Shift: пропорция по большей стороне
    sx, sy, _, _ = handle_scale((0, 0, 20, 10), "br", 30, -2, proportional=True)
    assert sx == 1.5 and sy == 1.5


def test_handle_scale_min_size():
    # мышь за пивотом: не даём вывернуть в ноль/минус
    sx, sy, _, _ = handle_scale((0, 0, 20, 10), "r", -100, 0)
    assert sx == 0.5 / 20  # min_size 0.5 мм
