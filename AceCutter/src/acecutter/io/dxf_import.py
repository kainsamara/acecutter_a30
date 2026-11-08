"""Импорт DXF через ezdxf: миллиметры как есть, кривые — в полилинии."""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import path as dxf_path

from ..domain.geometry import Point
from ..domain.shapes import Polyline
from ..domain.simplify import simplify as simplify_polylines

SUPPORTED = {"LINE", "CIRCLE", "ARC", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}


def import_dxf(path: str | Path, *, tolerance_mm: float = 0.05,
               simplify_mm: float = 0.08) -> list[Polyline]:
    doc = ezdxf.readfile(str(path))
    result: list[Polyline] = []
    for e in doc.modelspace():
        if e.dxftype() not in SUPPORTED:
            continue
        try:
            bp = dxf_path.make_path(e)
        except Exception:
            continue
        # в этой версии ezdxf flattening() отдаёт плоский поток вершин Vec3:
        # один entity -> один контур
        pts = [Point(v.x, v.y) for v in bp.flattening(tolerance_mm)]
        if len(pts) < 2:
            continue
        closed = math.dist(pts[0], pts[-1]) < 1e-9
        result.append(Polyline(pts, closed))
    return simplify_polylines(result, simplify_mm)
