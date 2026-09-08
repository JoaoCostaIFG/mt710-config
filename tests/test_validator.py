import pytest

from mt710.commands import (COMMANDS, MODE_ARG_COUNT, build_batch_commands,
                            suspicious_char_reason, validate,
                            validate_batch)


@pytest.mark.parametrize("line,valid", [
    # session / no-arg
    ("ETS", True), ("QTS", True), ("REBOOT", True), ("RESET", True),
    ("WHERE", True), ("SCAN", True), ("SEARCH", True),
    ("ETS,x", False), ("WHERE,1", False), ("ets", False), ("Ets", False),
    # APN
    ("803,cmnbiot,,", True), ("803,cmnet,internet,internet", True),
    ("803,cmnbiot", False),          # needs all 3 fields
    ("803,,,", False),               # empty apn
    ("803,my apn,,", False),         # space in apn
    # server
    ("804,e.trackits.com,7700", True), ("804,1.2.3.4,5030", True),
    ("804,host,0", False), ("804,host,65536", False),
    ("804,host", False), ("804,host,70000", False),
    # protocol
    ("800,TCP", True), ("800,UDP", True), ("800,tcp", False),
    # NWM (values joined by commas)
    ("NWM,0,0,2", True), ("NWM,3,0,2", True), ("NWM,3,1,3", True),
    ("NWM,1,2,1", True), ("NWM,0,0,0", True), ("NWM,0,1,0", True),
    ("NWM,9,9,9", False), ("NWM", False),
    # BAND
    ("BAND,0,0,f", True), ("BAND,28,0,f", True), ("BAND,12,8,f", True),
    ("BAND,28,0,g", False), ("BAND,28,0", False), ("BAND,x,0,f", False),
    # MODE matrix
    ("MODE,0,60,1", True), ("MODE,0,5,1", False), ("MODE,0,60,25", False),
    ("MODE,1,60", True), ("MODE,1,5", False), ("MODE,1,605", False),
    ("MODE,1", False), ("MODE,1,60,5", False),
    ("MODE,2,10,1,1", True), ("MODE,2,10,1,2", False),
    ("MODE,3,1", True), ("MODE,3,25", False),
    ("MODE,4,60", True), ("MODE,5,5,0,1", True), ("MODE,5,5,1,1", False),
    ("MODE,6", True), ("MODE,6,1", False),
    ("MODE,7,10,1", True), ("MODE,7,5,1", False), ("MODE,7,1441,1", False),
    ("MODE,8,10", True), ("MODE,8,61", False), ("MODE,8,5", False),
    ("MODE,9,10,1", True),
    ("MODE,10,1,01:00", True), ("MODE,10,0,00:00", True),
    ("MODE,10,24,01:00", False), ("MODE,10,1,24:00", False),
    ("MODE,10,1,1:00", False),
    ("MODE,12,1", False), ("MODE", False),
    # LOCK / heartbeat / misc ranges
    ("LOCK,10,1", True), ("LOCK,5,1", False), ("LOCK,10,61", False),
    ("HBC,5", True), ("HBC,3", False), ("HBC,61", False),
    ("DUR,1", True), ("DUR,0", False), ("DUR,11", False),
    ("LEP,0", True), ("LEP,1", True), ("LEP,2", False),
    ("LBS,0", True), ("LBS,3", True), ("LBS,4", False),
    ("AGPS,0", True), ("XTRA,1", True), ("PRIOR,0", True),
    ("MSW,1", True), ("DBG,1", True),
    ("RWT,60", True), ("RWT,59", False), ("RWT,600", True),
    # timezone: signed, tz*60, range -720..+780
    ("896,0", True), ("896,+480", True), ("896,-180", True),
    ("896,-720", True), ("896,+780", True), ("896,-721", False),
    ("896,781", False), ("896,+08:00", False),
    # password
    ("777,1234", True), ("777,ab12", True), ("777,12345", False),
    ("777,a!2", False), ("777,", False),
    # home zone
    ("AP,,,6877248FA31A,,", True), ("AP,,,,,", True),
    ("AP,1,,mac,,", False), ("AP,,,XYZ,,", False),
    ("GEO,22.64823,114.03440,30", True), ("GEO,22.6,114.0,29", False),
    ("GEO,22.6,114.0,301", False), ("GEO,a,b,30", False),
    # GSEN
    ("GSEN,70,70,2,1", True), ("GSEN,0,70,2,1", False),
    ("GSEN,70,5,2,1", False), ("GSEN,70,70,0,1", False),
    # KEY unchecked shape, RCONF paging
    ("KEY,984504615adacba7167bed1c16678fe5", True),
    ("RCONF", True), ("RCONF,1", True), ("RCONF,4", True),
    ("RCONF,5", False),
    # unknown / suspicious
    ("XYZ,1", False), ("DEF,R", False),
    ("803，apn,,", False), ("803,ａpn,,", False),
    ("803,apn,\u200b", False), ("803,ａpn,,", False),
    ("803,apn,１,", False),
    # leading zeros rejected (web tool parity)
    ("DUR,02", False), ("MODE,1,060", False),
])
def test_validate(line, valid):
    ok, reason = validate(line)
    assert ok is valid, f"{line!r}: {reason}"


def test_suspicious_fullwidth_comma():
    assert suspicious_char_reason("803，apn,,") is not None
    assert suspicious_char_reason("MODE,1,60") is None


def test_unknown_command_has_hint():
    ok, reason = validate("BADCMD,1")
    assert not ok
    assert "BAND" in reason


def test_lowercase_message_mentions_case():
    _, reason = validate("ets")
    assert "UPPERCASE" in reason


def test_batch_skips_comments_and_blanks():
    results = validate_batch(["# hi", "", "803,apn,,", "NOPE,1"])
    assert [r[0] for r in results] == ["803,apn,,", "NOPE,1"]
    assert results[0][1] is True
    assert results[1][1] is False


def test_build_batch_moves_mode_last_and_appends_reboot():
    cmds, reboot = build_batch_commands(
        ["ETS", "803,apn,,", "MODE,1,60", "DBG,0"])
    assert cmds == ["803,apn,,", "DBG,0", "MODE,1,60"]
    assert reboot is True


def test_build_batch_keeps_qts_without_reboot():
    cmds, reboot = build_batch_commands(["803,apn,,", "QTS"])
    assert cmds == ["803,apn,,", "QTS"]
    assert reboot is False


def test_build_batch_appends_reboot_when_no_selfsave():
    cmds, reboot = build_batch_commands(["803,apn,,", "DBG,0"])
    assert cmds[-1] == "REBOOT"
    assert reboot is True


def test_all_modes_have_names_and_explanations():
    from mt710.commands import MODE_EXPLAIN, MODE_NAMES
    from mt710.reference import GUIDE_SECTIONS, RCONF_FIELDS
    assert set(MODE_ARG_COUNT) == set(MODE_NAMES) == set(MODE_EXPLAIN)
    # key RCONF fields documented
    for key in ["MDL", "ID", "SV", "NET", "SRV", "APN", "NWM", "MODE",
                "HBC", "DUR", "RWT", "LEP", "LBS", "AGPS", "GSEN"]:
        assert key in RCONF_FIELDS
    assert len(GUIDE_SECTIONS) >= 9


def test_registry_covers_expected_keywords():
    expected = {"ETS", "QTS", "RCONF", "REBOOT", "RESET", "777", "803",
                "804", "800", "NWM", "BAND", "RWT", "896", "MODE", "LOCK",
                "HBC", "DUR", "LEP", "LBS", "AGPS", "XTRA", "PRIOR",
                "WHERE", "SCAN", "SEARCH", "AP", "GEO", "GSEN", "MSW",
                "DBG", "KEY", "LTP"}
    assert expected <= set(COMMANDS)


def test_ltp_flagged_ignored_on_mt710():
    assert COMMANDS["LTP"].ignored_on_mt710
    assert COMMANDS["WHERE"].mt710_only
