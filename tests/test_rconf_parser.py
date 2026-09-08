from mt710.protocol import ConfigDump, parse_rconf_lines, strip_prefix

# Full RCONF dump exactly as printed in Mictrack_MT710_Commands_List.pdf
PDF_DUMP = [
    "NET:TCP",
    "GU:MT710,0000",
    "UP:0000",
    "SRV:NC,,0",
    "APN:,",
    "NWM:3,1,3",
    "M1:8",
    "NB1:ANY",
    "GSM:ANY",
    "EDRX:0,5,0010",
    "ID:862255061984701",
    "DBG:OFF",
    "HBC:5m",
    "DUR:2m",
    "RWT:120s",
    "LEP:OFF",
    "MSW:ON",
    "LBS:0",
    "AGPS:ON",
    "XTRA:ON",
    "TZ:0",
    "ANG:0,0,0",
    "GSEN:70,70,2,187",
    "MODE:8,10s,1,0",
    "CCID:898604A6102191189881",
    "AP:1057,300,,,",
    "GEO:Lat=0.00000,lng=0.00000,R=0",
    "SAVE:1,0,10m",
    "SOC:NORMAL",
    "PRIOR:GNSS",
    "ISD:26/04/28",
    "MDL:MT710",
    "SV:V2.1.6",
    "HV:V2.0.0",
    "MV:BG96MAR04A05M1G_01.200.01.200",
    "LIC:OK",
    "AU:Darren@Mictrack",
    "IN:linkedin.com/in/imdarren",
]


def test_parse_pdf_dump():
    dump = parse_rconf_lines(PDF_DUMP)
    assert isinstance(dump, ConfigDump)
    assert dump.model == "MT710"
    assert dump.imei == "862255061984701"
    assert dump.firmware == "V2.1.6"
    assert dump.get("NWM") == "3,1,3"
    assert dump.get("GSEN") == "70,70,2,187"
    assert dump.get("MODE") == "8,10s,1,0"
    assert dump.get("DOES_NOT_EXIST") is None
    assert dump.get("DOES_NOT_EXIST", "x") == "x"
    # order preserved
    keys = [k for k, _ in dump.fields]
    assert keys.index("NET") < keys.index("MDL") < keys.index("IN")


def test_parse_tolerates_cfg_prefix_and_blank():
    dump = parse_rconf_lines(
        ["<CFG>:MDL:MT710", "", "<CFG>:SV:V2.1.6", "IN:x", "no-colon-line"])
    assert dump.model == "MT710"
    assert dump.firmware == "V2.1.6"
    assert len(dump.fields) == 3


def test_parse_web_dump_variant():
    """Dump shape emitted by the web tool's mock (fields with spaces)."""
    dump = parse_rconf_lines(
        ["MDL:MT710  ID:862255061984701  SV:V2.1.6  CCID:89860112345678900881"])
    # a single spaced line is one KEY:value entry; key is MDL
    assert dump.get("MDL") == "MT710  ID:862255061984701  SV:V2.1.6  CCID:89860112345678900881"


def test_strip_prefix():
    assert strip_prefix("<CFG>:ETS,OK") == "ETS,OK"
    assert strip_prefix("<Trace>:VBAT=40") == "VBAT=40"
    assert strip_prefix("ETS,OK") == "ETS,OK"
