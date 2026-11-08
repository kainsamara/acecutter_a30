"""Разбор строк ответов GRBL 1.1."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GrblStatus:
    state: str  # Idle, Run, Hold, Alarm, Home, Jog, Door, Check
    mpos: tuple[float, float, float] | None = None
    wpos: tuple[float, float, float] | None = None
    fs: tuple[float, float] | None = None  # feed, spindle
    raw: str = ""


def parse_line(line: str) -> tuple[str, object] | None:
    """Классифицирует строку GRBL.

    Возвращает ('ok', None) | ('error', int) | ('alarm', int)
    | ('status', GrblStatus) | ('message', str) | ('other', str) | None.
    """
    s = line.strip()
    if not s:
        return None
    if s == "ok":
        return "ok", None
    if s.startswith("error:"):
        return "error", _to_int(s[6:])
    if s.startswith("ALARM:"):
        return "alarm", _to_int(s[6:])
    if s.startswith("<") and s.endswith(">"):
        return "status", _parse_status(s)
    if s.startswith("["):
        return "message", s
    return "other", s


def _to_int(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        return -1


def _parse_status(s: str) -> GrblStatus:
    fields = s[1:-1].split("|")
    state = fields[0].split(":")[0]
    mpos = wpos = fs = None
    for f in fields[1:]:
        key, _, val = f.partition(":")
        if key == "MPos":
            mpos = _triple(val)
        elif key == "WPos":
            wpos = _triple(val)
        elif key == "FS":
            fs = _pair(val)
    return GrblStatus(state=state, mpos=mpos, wpos=wpos, fs=fs, raw=s)


def _pair(val: str) -> tuple[float, float] | None:
    parts = val.split(",")
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _triple(val: str) -> tuple[float, float, float] | None:
    parts = val.split(",")
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None
