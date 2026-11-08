from __future__ import annotations

import ezdxf

from acecutter.io.dxf_import import import_dxf


def _doc_with_entities():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True)
    msp.add_line((20, 0), (30, 5))
    msp.add_circle((40, 10), radius=3)
    return doc


def test_dxf_import_line_and_polyline(tmp_path):
    doc = _doc_with_entities()
    f = tmp_path / "t.dxf"
    doc.saveas(str(f))
    pls = import_dxf(f)
    assert len(pls) == 3
    closed = [pl for pl in pls if pl.closed]
    assert len(closed) == 2  # полилиния (close=True) и круг


def test_dxf_circle_rounded(tmp_path):
    doc = _doc_with_entities()
    f = tmp_path / "t.dxf"
    doc.saveas(str(f))
    pls = import_dxf(f)
    circle = [pl for pl in pls if pl.bounds()[0] > 35]
    assert circle and circle[0].closed
