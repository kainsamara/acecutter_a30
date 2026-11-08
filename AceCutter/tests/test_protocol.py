from __future__ import annotations

from acecutter.driver.protocol import parse_line


def test_ok():
    assert parse_line("ok") == ("ok", None)


def test_error():
    assert parse_line("error:5") == ("error", 5)


def test_alarm():
    assert parse_line("ALARM:1") == ("alarm", 1)


def test_status_run():
    kind, st = parse_line("<Run|MPos:1.500,2.000,0.000|FS:800,0>")
    assert kind == "status"
    assert st.state == "Run"
    assert st.mpos == (1.5, 2.0, 0.0)
    assert st.fs == (800.0, 0.0)


def test_message_and_other():
    assert parse_line("[MSG:Caution: Unlocked]") == ("message", "[MSG:Caution: Unlocked]")
    assert parse_line("Grbl 1.1h ['$' for help]") == ("other", "Grbl 1.1h ['$' for help]")
    assert parse_line("") is None
