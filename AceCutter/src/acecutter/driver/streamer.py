"""Стример GRBL: окно по RX-буферу 128 байт, character counting.

Модель: UI дёргает pump() по таймеру. pump() сначала вычитывает все
готовые ответы (ok/error освобождают место), потом дозаливает окно.
Реалтайм-команды (?, ~, !, 0x18) идут мимо очереди — их GRBL
исполняет немедленно и не отвечает на них 'ok'.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Callable, Iterable

from .protocol import GrblStatus, parse_line
from .transport import Transport

RX_LIMIT = 128       # аппаратный RX-буфер GRBL 1.1
REALTIME_RESERVE = 4  # запас под реалтайм-байты

ErrorCallback = Callable[[str, int], None]
StatusCallback = Callable[[GrblStatus], None]
SettingCallback = Callable[[int, float], None]  # $130=245.000 → (130, 245.0)


_SETTING_RE = re.compile(r"^\$(\d+)=(-?[\d.]+)\s*$")


class GrblStreamer:
    def __init__(self, transport: Transport, *,
                 on_ok: Callable[[str], None] | None = None,
                 on_error: ErrorCallback | None = None,
                 on_alarm: Callable[[int], None] | None = None,
                 on_status: StatusCallback | None = None,
                 on_message: Callable[[str], None] | None = None,
                 on_setting: SettingCallback | None = None) -> None:
        self._transport = transport
        self._on_ok = on_ok
        self._on_error = on_error
        self._on_alarm = on_alarm
        self._on_status = on_status
        self._on_message = on_message
        self._on_setting = on_setting
        self._queue: deque[str] = deque()
        self._pending: deque[tuple[str, int]] = deque()  # (line, size) ждём ok/error
        self._outstanding = 0
        self.ok_count = 0  # получил ли вообще хоть один ok (живой ли GRBL)

    # -- публичное API ---------------------------------------------------

    def send(self, line: str) -> bool:
        """Поставить строку в очередь. Комментарии/пустые не шлются.
        Возвращает: поставлена ли строка (для честного счётчика прогресса)."""
        line = line.strip()
        if not line or line.startswith(";"):
            return False
        self._queue.append(line)
        return True

    def send_many(self, lines: Iterable[str]) -> int:
        """Сколько строк реально ушло в очередь (без комментариев)."""
        sent = 0
        for ln in lines:
            if self.send(ln):
                sent += 1
        return sent

    def send_realtime(self, data: bytes) -> None:
        """'?', '~', '!', 0x18 — пишутся сразу, ok не ждём."""
        self._transport.write(data)

    def cancel_queue(self) -> None:
        self._queue.clear()

    @property
    def busy(self) -> bool:
        return bool(self._queue or self._pending)

    def pump(self) -> None:
        self._drain()
        self._fill()

    # -- внутреннее -------------------------------------------------------

    def _drain(self) -> None:
        while (raw := self._transport.readline()) is not None:
            parsed = parse_line(raw.decode("ascii", "ignore"))
            if parsed is None:
                continue
            kind, val = parsed
            if kind in ("ok", "error"):
                if not self._pending:
                    continue  # чужой ok (например, после soft reset)
                sent, size = self._pending.popleft()
                self._outstanding = max(0, self._outstanding - size)
                if kind == "ok":
                    self.ok_count += 1
                    if self._on_ok:
                        self._on_ok(sent)
                elif self._on_error:
                    self._on_error(sent, int(val))
            elif kind == "alarm" and self._on_alarm:
                self._on_alarm(int(val))
            elif kind == "status" and self._on_status:
                self._on_status(val)  # type: ignore[arg-type]
            elif kind == "message" and self._on_message:
                self._on_message(str(val))
            elif kind == "other":
                s = raw.decode("ascii", "ignore").strip()
                m = _SETTING_RE.match(s)
                if m and self._on_setting:
                    self._on_setting(int(m.group(1)), float(m.group(2)))

    def _fill(self) -> None:
        budget = RX_LIMIT - REALTIME_RESERVE
        while self._queue:
            line = self._queue[0]
            size = len(line.encode("ascii", "ignore")) + 1  # + \n
            if self._outstanding + size > budget:
                break
            self._queue.popleft()
            self._transport.write(line.encode("ascii") + b"\n")
            self._pending.append((line, size))
            self._outstanding += size
