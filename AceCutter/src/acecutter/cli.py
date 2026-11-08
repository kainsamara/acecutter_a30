"""CLI: svg/dxf → gcode без GUI.

    python -m acecutter.cli input.svg -o job.nc --feed 1500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .io.dxf_import import import_dxf
from .io.svg_import import import_svg
from .toolpath.gcode import CutParams, compile_gcode
from .toolpath.toolhead import SpindlePen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="acecutter-convert")
    ap.add_argument("input", help="SVG или DXF файл")
    ap.add_argument("-o", "--output", help="выходной .nc (по умолчанию <input>.nc)")
    ap.add_argument("--feed", type=float, default=1500.0, help="подача, мм/мин")
    ap.add_argument("--passes", type=int, default=1)
    ap.add_argument("--pen-s", type=int, default=1000, help="S для M3 (перо вниз)")
    ap.add_argument("--tolerance", type=float, default=0.05, help="допуск кривых, мм")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f"Файл не найден: {src}", file=sys.stderr)
        return 2

    if src.suffix.lower() == ".svg":
        polylines = import_svg(src, tolerance_mm=args.tolerance)
    elif src.suffix.lower() == ".dxf":
        polylines = import_dxf(src, tolerance_mm=args.tolerance)
    else:
        print(f"Не знаю расширение {src.suffix}, жду .svg или .dxf", file=sys.stderr)
        return 2

    if not polylines:
        print("В файле нет векторных контуров.", file=sys.stderr)
        return 1

    gcode = compile_gcode(polylines, CutParams(feed_mm_min=args.feed,
                                               passes=args.passes),
                          SpindlePen(down_s=args.pen_s))
    out = Path(args.output) if args.output else src.with_suffix(".nc")
    out.write_text("\n".join(gcode) + "\n", encoding="ascii")
    print(f"{len(polylines)} контуров → {len(gcode)} строк GCode → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
