from __future__ import annotations

from acecutter.domain.geometry import Point
from acecutter.domain.shapes import Polyline
from acecutter.toolpath.gcode import CutParams, compile_gcode, order_polylines
from acecutter.toolpath.toolhead import SpindlePen

SQUARE = Polyline([Point(10, 0), Point(10, 10), Point(0, 10), Point(0, 0)], True)


def test_pen_up_down_around_every_contour():
    lines = compile_gcode([SQUARE], CutParams(), SpindlePen(down_s=1000))
    assert "M5" in lines and "M3 S1000" in lines
    # перо опускается после G0 к началу контура, до первого G1
    i_g0 = lines.index("G0 X10.000 Y0.000")
    i_down = lines.index("M3 S1000")
    i_cut = lines.index("G1 F1500")
    assert i_g0 < i_down < i_cut
    # и поднято в конце
    assert lines.index("M5", i_cut) > i_cut


def test_closed_contour_ends_at_start_point():
    lines = compile_gcode([SQUARE], CutParams(), SpindlePen())
    cuts = [l for l in lines if l.startswith("G1 X")]
    assert cuts[-1] == "G1 X10.000 Y0.000"


def test_passes_repeat_the_job():
    lines = compile_gcode([SQUARE], CutParams(passes=3), SpindlePen())
    assert lines.count("M3 S1000") == 3
    travel = sum(1 for l in lines if l.startswith("G0 X") and l != "G0 X0.000 Y0.000")
    assert travel == 3


def test_nearest_order():
    near = Polyline([Point(1, 1), Point(2, 1)], False)
    far = Polyline([Point(50, 50), Point(60, 50)], False)
    assert order_polylines([far, near]) == [near, far]


def test_no_travel_cuts_between_contours():
    """Между контурами перо обязано быть поднято (M5 после последнего G1)."""
    second = Polyline([Point(30, 0), Point(30, 30)], False)
    lines = compile_gcode([SQUARE, second], CutParams(), SpindlePen())
    cut_moves = [i for i, l in enumerate(lines) if l.startswith("G1 X")]
    for i in cut_moves:
        # до движения реза в этом контуре был M3 после последнего M5
        window = lines[: i + 1]
        assert window.count("M3 S1000") > window.count("M5") - 1
