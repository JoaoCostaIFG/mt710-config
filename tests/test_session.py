"""Passive session-state tracking tests (no I/O)."""

from mt710.protocol import MT710Session, parse_rconf_lines, strip_prefix  # noqa: F401


def make_session() -> MT710Session:
    return MT710Session("/dev/null", on_event=lambda e, d: None)


def test_boot_banner_kills_ets():
    s = make_session()
    s.ets_active = True
    s._passive_scan("<Trace>:WAIT CHECK VBATT")
    assert s.ets_active is False
    s.ets_active = True
    s._passive_scan("<Trace>:LOARD USER CONFIG")
    assert s.ets_active is False


def test_ets_ok_activates_and_qts_deactivates():
    s = make_session()
    assert s.ets_active is False
    s._passive_scan("<CFG>:ETS,OK")
    assert s.ets_active is True
    s._passive_scan("<CFG>:QTS,OK")
    assert s.ets_active is False


def test_passive_imei_scan():
    s = make_session()
    # IMEI from the official command-list PDF example dump
    s._passive_scan("<Trace>:ID:862255061984701")
    assert s.passive_imei == "862255061984701"
    # too-short numbers are not an IMEI
    s._passive_scan("ID:12345")
    assert s.passive_imei == "862255061984701"


def test_cfg_reply_field_extraction():
    from mt710.protocol import Reply
    r = Reply(ok=True, lines=["<CFG>:ETS,OK", "<Trace>:noise", "<CFG>:MDL:MT710"])
    assert r.cfg_field("MDL") == "MT710"
    assert r.cfg_field("NOPE") is None
    assert len(r.cfg_lines()) == 2
