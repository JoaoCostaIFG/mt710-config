"""Transport line-assembly tests — no real serial port needed."""

from mt710.transport import LineKind, SerialTransport, classify_line


def make_transport():
    events = []
    t = SerialTransport(
        "/dev/null",
        on_line=lambda ev: events.append(ev),
        on_rx=lambda b: None,
    )
    return t, events


def feed(t, events, text, flush=True):
    t._feed(text)
    if flush:
        t._maybe_flush_idle()
    return [e.line for e in events]


def test_crlf_split_lines():
    t, events = make_transport()
    lines = feed(t, events, "\r\n<CFG>:ETS,OK\r\n<Trace>:VBAT=40\r\n")
    # leading blank is a phantom batch-prefix and is dropped (burst rule)
    assert lines == ["<CFG>:ETS,OK", "<Trace>:VBAT=40"]


def test_fragmented_line_across_feeds():
    t, events = make_transport()
    feed(t, events, "<CFG>:ETS", flush=False)
    assert events == []
    feed(t, events, ",OK\r\n")
    assert [e.line for e in events] == ["<CFG>:ETS,OK"]


def test_cr_and_n_arrive_separately():
    t, events = make_transport()
    feed(t, events, "ABC", flush=False)
    feed(t, events, "\r", flush=False)
    feed(t, events, "\nDEF", flush=False)
    feed(t, events, "\r\n")
    assert [e.line for e in events] == ["ABC", "DEF"]


def test_idle_flush_partial_reply():
    """Replies have no trailing newline — flush after the idle gap."""
    t, events = make_transport()
    t._feed("\r\n<CFG>:ETS,OK")
    assert events == []  # leading blank dropped, partial still buffered
    # simulate the idle deadline passing
    t._flush_deadline = t._last_rx_at - 1
    t._maybe_flush_idle()
    assert [e.line for e in events] == ["<CFG>:ETS,OK"]


def test_blank_line_burst_rule():
    """A blank line is kept only in the same burst as the previous line."""
    t, events = make_transport()
    t._feed("A\r\n\r\nB\r\n")
    assert [e.line for e in events] == ["A", "", "B"]
    # standalone blank after a quiet period is dropped
    t2, events2 = make_transport()
    t2._last_line_at = 0.0  # long ago
    t2._feed("\r\nTEXT\r\n")
    assert [e.line for e in events2] == ["TEXT"]


def test_classify():
    assert classify_line("<CFG>:803,OK") is LineKind.CFG
    assert classify_line("<Trace>:VBAT=40") is LineKind.TRACE
    assert classify_line("WAIT CONFIG CMD......") is LineKind.PLAIN
