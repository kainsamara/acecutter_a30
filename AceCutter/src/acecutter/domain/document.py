"""Документ: слои и полилинии."""
from __future__ import annotations

from dataclasses import dataclass, field

from .shapes import Polyline


@dataclass
class Layer:
    name: str
    color: str = "#1f77b4"
    polylines: list[Polyline] = field(default_factory=list)


@dataclass(frozen=True)
class Workspace:
    """Рабочее поле: ширина жёсткая (ось X), длина — валик подачи (ось Y)."""

    width_mm: float = 245.0   # $130 станка
    length_mm: float = 2000.0  # $131: рулон, условно


@dataclass
class Document:
    layers: list[Layer] = field(default_factory=list)

    def all_polylines(self) -> list[Polyline]:
        return [pl for layer in self.layers for pl in layer.polylines]

    def bounds(self) -> tuple[float, float, float, float] | None:
        pts: list[tuple[float, float, float, float]] = [
            b for b in (pl.bounds() for pl in self.all_polylines()) if b is not None
        ]
        if not pts:
            return None
        return (min(b[0] for b in pts), min(b[1] for b in pts),
                max(b[2] for b in pts), max(b[3] for b in pts))
