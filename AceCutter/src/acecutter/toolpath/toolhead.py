"""Управление рабочим органом (перо/нож/лазер) через GCode.

Плоттер на GRBL: перо — соленоид на шпиндельном пине (D11 CNC Shield).
Включение шпинделя (M3 S...) опускает перо, M5 — поднимает.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ToolHead(Protocol):
    """Всё, что умеет опускаться и подниматься, выражается GCode-строками."""

    def up(self) -> list[str]: ...
    def down(self) -> list[str]: ...


@dataclass(frozen=True)
class SpindlePen:
    """Перо на шпиндельном пине: M3 S<down_s> — вниз, M5 — вверх.

    down_s обязателен: после сброса GRBL текущий S=0, и голый M3 не включит
    ШИМ (соленоид не сработает). $30=1000 по умолчанию у GRBL.
    """

    down_s: int = 1000

    def up(self) -> list[str]:
        return ["M5"]

    def down(self) -> list[str]:
        return [f"M3 S{self.down_s}"]


@dataclass(frozen=True)
class LaserHead:
    """Задел под лазер: M4 с индивидуальной мощностью на сегменте."""

    power: int = 800  # 0..1000

    def up(self) -> list[str]:
        return ["M5"]

    def down(self) -> list[str]:
        return [f"M3 S{self.power}"]
