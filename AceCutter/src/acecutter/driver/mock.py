"""Мок GRBL для тестов и разработки без станка."""
from __future__ import annotations

from collections import deque

from .transport import Transport


class MockGrblTransport(Transport):
    """Эмулирует GRBL 1.1: ok на каждую строку, <Idle|...> на '?'.

    hold_oks=True — ok копятся и не отдаются, пока не вызовешь release():
    так тесты проверяют, что стример не переполняет RX-окно.
    fail_next=N — следующие N строк получают error:5 вместо ok.
    """

    def __init__(self, *, hold_oks: bool = False) -> None:
        self.written: list[bytes] = []
        self.lines_sent: list[str] = []
        self.fail_next = 0
        self._hold = hold_oks
        self._responses: deque[bytes] = deque()
        self._held: deque[bytes] = deque()

    def write(self, data: bytes) -> None:
        self.written.append(data)
        if data in (b"?",):
            self._responses.append(b"<Idle|MPos:1.000,2.000,0.000|FS:0,0>\n")
            return
        if data in (b"~", b"!", b"\x18"):
            return
        line = data.decode("ascii", "ignore").strip()
        if not line or line.startswith(";"):
            return
        self.lines_sent.append(line)
        if line == "$$":
            # GRBL отвечает списком настроек и завершает ok
            for s in (b"$100=36.360\n", b"$101=32.000\n",
                      b"$130=245.000\n", b"$131=2000.000\n"):
                self._responses.append(s)
        if self.fail_next > 0:
            self.fail_next -= 1
            resp = b"error:5\n"
        else:
            resp = b"ok\n"
        (self._held if self._hold else self._responses).append(resp)

    def release(self, n: int | None = None) -> None:
        while self._held and (n is None or n > 0):
            self._responses.append(self._held.popleft())
            if n is not None:
                n -= 1

    def readline(self) -> bytes | None:
        return self._responses.popleft() if self._responses else None
