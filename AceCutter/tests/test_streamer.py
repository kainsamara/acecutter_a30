from __future__ import annotations

from acecutter.driver.mock import MockGrblTransport
from acecutter.driver.streamer import RX_LIMIT, REALTIME_RESERVE, GrblStreamer


def test_roundtrip_ok():
    m = MockGrblTransport()
    oks: list[str] = []
    s = GrblStreamer(m, on_ok=oks.append)
    s.send("G21")
    s.pump()  # отправка
    s.pump()  # вычитка ok
    assert m.written == [b"G21\n"]
    assert oks == ["G21"]
    assert not s.busy


def test_never_exceeds_rx_window():
    """Строки не лезут в порт, если окно 128 байт (минус резерв) исчерпано."""
    m = MockGrblTransport(hold_oks=True)
    s = GrblStreamer(m)
    line = "G1 X" + "9" * 110  # ~114 байт с \n
    s.send(line)
    s.send(line)
    s.pump()
    sent = [w for w in m.written if w.endswith(b"\n")]
    assert len(sent) == 1  # вторая не влезла
    m.release(1)
    s.pump()
    sent = [w for w in m.written if w.endswith(b"\n")]
    assert len(sent) == 2
    budget = RX_LIMIT - REALTIME_RESERVE
    assert budget >= 124


def test_comments_skipped():
    m = MockGrblTransport()
    s = GrblStreamer(m)
    s.send("; просто комментарий")
    s.send("G90")
    s.pump()
    assert m.lines_sent == ["G90"]


def test_error_reported_and_stream_continues():
    m = MockGrblTransport()
    m.fail_next = 1
    errs: list[tuple[str, int]] = []
    s = GrblStreamer(m, on_error=lambda ln, code: errs.append((ln, code)))
    s.send("G99 X1")  # невалидная — получит error:5
    s.send("G21")
    s.pump()
    s.pump()
    assert errs == [("G99 X1", 5)]
    assert "G21" in m.lines_sent


def test_send_many_counts_only_real_lines():
    m = MockGrblTransport()
    s = GrblStreamer(m)
    sent = s.send_many(["; комментарий", "G21", "   ", "M5", "; end"])
    assert sent == 2
    assert s.send("  ; тоже мусор  ") is False
    assert s.send("G90") is True


def test_settings_reported():
    """$$ → $130=245.000/$131=2000.000 доходят колбэком on_setting."""
    m = MockGrblTransport()
    got: list[tuple[int, float]] = []
    s = GrblStreamer(m, on_setting=lambda n, v: got.append((n, v)))
    s.send("$$")
    s.pump()
    s.pump()
    assert (130, 245.0) in got and (131, 2000.0) in got
    assert "$$" in m.lines_sent


def test_ok_count_grows():
    m = MockGrblTransport()
    s = GrblStreamer(m)
    assert s.ok_count == 0
    s.send("G21")
    s.pump()
    s.pump()
    assert s.ok_count == 1


def test_realtime_bypasses_queue():
    m = MockGrblTransport()
    statuses: list = []
    s = GrblStreamer(m, on_status=statuses.append)
    s.send_realtime(b"?")
    assert m.written[-1] == b"?"
    assert not s.busy
    s.pump()  # мок отвечает <Idle|...>, стример его парсит
    assert len(statuses) == 1 and statuses[0].state == "Idle"
