"""Транспорт: байтовый I/O с транспортом станка (serial или мок)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Transport(ABC):
    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def readline(self) -> bytes | None:
        """Неблокирующе вернуть строку с \\n или None, если данных нет."""

    def close(self) -> None: ...


class SerialTransport(Transport):
    """pyserial с внутренним буфером: readline собирает строки по \\n.

    timeout=0: read() возвращает что есть, без блокировок — поток UI дёргает
    pump() по таймеру. write_timeout страхует от драйверов, которые не
    вычитают (COM-мыши, модемы): вместо вечной блокировки UI получаем ошибку
    строкой в поток чтения.
    """

    def __init__(self, port: str, baud: int = 115200) -> None:
        import serial  # лениво: тестам pyserial не нужен

        self._ser = serial.Serial(port, baud, timeout=0, write_timeout=0.5)
        self._buf = bytearray()
        self._dead = False

    def write(self, data: bytes) -> None:
        if self._dead:
            return
        try:
            self._ser.write(data)
        except Exception as e:  # noqa: BLE001 — SerialTimeoutException и др.
            self._dead = True
            self._buf += f"[ERR: {e}]\n".encode("ascii", "ignore")

    def readline(self) -> bytes | None:
        while (i := self._buf.find(b"\n")) < 0:
            chunk = self._ser.read(256)
            if not chunk:
                return None
            self._buf.extend(chunk)
        line = bytes(self._buf[: i + 1])
        del self._buf[: i + 1]
        return line

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass
