"""Тесты SVG-импорта. Ключевой кейс — Inkscape-пути с относительными
командами (m/c/l/z), на которых ломался самописный парсер HocoControl.
"""
from __future__ import annotations

from pathlib import Path

from acecutter.io.svg_import import import_svg

INKSCAPE_STYLE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">
  <rect x="10" y="10" width="20" height="15"/>
  <path d="m 5,40 c 0,-2 3,-5 5,-5 l 10,0 v 5 z"/>
  <path d="M 50 10 L 60 10 M 70 20 L 80 20"/>
  <g transform="translate(30,30)">
    <path d="M0 0 L5 0 L5 5 Z"/>
  </g>
</svg>
"""


def _write(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "t.svg"
    f.write_text(text, encoding="utf-8")
    return f


def test_relative_commands_not_lost(tmp_path):
    """Относительные m/c/l/v/z обязаны превращаться в полилинии, а не в пустоту."""
    pls = import_svg(_write(tmp_path, INKSCAPE_STYLE))
    assert len(pls) >= 4  # rect + кривой путь + 2 разрывных subpath + группа


def test_units_are_mm(tmp_path):
    """width=100mm при viewBox 0 0 100 60 → 1 юнит = 1 мм."""
    pls = import_svg(_write(tmp_path, INKSCAPE_STYLE))
    all_x = [p.x for pl in pls for p in pl.points]
    assert max(all_x) <= 100.0 + 1e-6


def test_rect_closed_with_exact_corners(tmp_path):
    pls = import_svg(_write(tmp_path, INKSCAPE_STYLE))
    # SVG Y вниз → флип: rect y 10..25 юнитов → −25..−10 мм
    rects = [pl for pl in pls if pl.bounds()
             and all(abs(a - b) < 0.01 for a, b in
                     zip(pl.bounds(), (10.0, -25.0, 30.0, -10.0)))]
    assert len(rects) == 1 and rects[0].closed


def test_gap_between_subpaths_starts_new_polyline(tmp_path):
    """Два M в одном <path> — два контура, без соединительной линии через весь лист."""
    pls = import_svg(_write(tmp_path, INKSCAPE_STYLE))
    # subpath 50..60 по X и 70..80 по X — отдельные контуры
    segs = [pl for pl in pls if pl.bounds() and pl.bounds()[2] <= 60.5 and not pl.closed]
    assert len(segs) == 1


def test_group_transform_applied(tmp_path):
    pls = import_svg(_write(tmp_path, INKSCAPE_STYLE))
    # группа translate(30,30), путь 0..5 → флип: (30, −35, 35, −30)
    moved = [pl for pl in pls if pl.bounds()
             and all(abs(a - b) < 0.01 for a, b in
                     zip(pl.bounds(), (30.0, -35.0, 35.0, -30.0)))]
    assert len(moved) == 1 and moved[0].closed
