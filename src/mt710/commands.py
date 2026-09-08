"""MT710 command database and validator.

Sources, in order of authority:

1. ``config.mictrack.com`` v1.2.32 JS — validator rules validated on real
   hardware by Mictrack (whitelist, arg ranges, protocol quirks).
2. ``Mictrack_MT710_Commands_List.pdf`` — official command tables (USB,
   SMS, downlink) with ranges, replies and defaults.
3. ``MT710_User_Manual_V1.0.pdf`` — behavioural explanations.

Where the sources disagree the conflict is recorded in ``Command.note``
and surfaced in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Category(Enum):
    SESSION = "Session"
    NETWORK = "Network"
    MODE = "Working Mode"
    POSITIONING = "Positioning"
    SENSORS = "Sensors & Power"
    HOME = "Home Zone (MT710)"
    SYSTEM = "System"


class Save(Enum):
    EXPLICIT = "explicit"        # needs a trailing QTS / REBOOT / MODE to persist
    SAVES_REBOOT = "reboot"      # saves settings and reboots by itself
    SAVES = "saves"              # saves settings, no reboot (QTS)
    QUERY = "query"              # read-only


@dataclass
class ArgSpec:
    """One comma-separated argument of a command."""

    name: str
    kind: str                    # int | sint | bit | enum | str | mac | hhmm | float
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: str = ""
    options: list[str] = field(default_factory=list)   # for enum
    example: str = ""
    help: str = ""
    optional: bool = False       # may be omitted entirely (shorter arg list)
    allow_empty: bool = False    # must be present but may be "" (placeholder)
    must_be_empty: bool = False  # reserved field — must stay blank
    pattern: Optional[str] = None  # regex the whole field must match


@dataclass
class Command:
    keyword: str
    title: str
    category: Category
    args: list[ArgSpec]
    format: str                  # canonical wire format
    example: str
    reply: str
    default: str
    save: Save
    description: str
    note: str = ""
    mt710_only: bool = False
    ignored_on_mt710: bool = False
    needs_ets: bool = True
    source: str = "web+pdf"

    @property
    def min_args(self) -> int:
        return sum(1 for a in self.args if not a.optional)

    @property
    def max_args(self) -> int:
        return len(self.args)


# ── argument primitives (mirror the web tool's validator) ─────────

_INT_RE = re.compile(r"^(0|[1-9]\d*)$")          # unsigned, no leading zeros
_SIGNED_RE = re.compile(r"^[+-]?(0|[1-9]\d{0,2})$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")

FULLWIDTH_MAP = {
    "，": ",", "：": ":", "；": ";", "。": ".",
    "（": "(", "）": ")", "　": " ",
}
_FULLWIDTH_DIGITS = re.compile(r"[０-９]")
_FULLWIDTH_LETTERS = re.compile(r"[ａ-ｚＡ-Ｚ]")
_INVISIBLE = re.compile(r"[\u200B\u200C\u200D\uFEFF]")


def suspicious_char_reason(line: str) -> Optional[str]:
    """Characters that paste from Excel/PDFs/IMEs silently inject."""
    for ch, repl in FULLWIDTH_MAP.items():
        if ch in line:
            return (f"contains a full-width '{ch}' (should be '{repl}') — "
                    "common when pasting from Excel or a Chinese input method")
    if _FULLWIDTH_DIGITS.search(line):
        return "contains full-width digits (０-９)"
    if _FULLWIDTH_LETTERS.search(line):
        return "contains full-width letters (ａ-ｚ/Ａ-Ｚ)"
    if _INVISIBLE.search(line):
        return "contains an invisible character (zero-width / BOM)"
    return None


def _is_int(v: str) -> bool:
    return bool(_INT_RE.match(v))


def _is_signed_int(v: str) -> bool:
    return bool(_SIGNED_RE.match(v))


def _in_range(v: str, lo: float, hi: float) -> bool:
    try:
        return lo <= float(v) <= hi
    except ValueError:
        return False


# ── MODE validation ───────────────────────────────────────────────
# arg-count map from the web validator; ranges cross-checked with the PDF
# (the PDF contradicts itself once for MODE,1 — "60-600" in the USB table
# vs "10-600" everywhere else; 10-600 is used, matching the web tool).

MODE_ARG_COUNT = {
    "0": 2, "1": 1, "2": 3, "3": 1, "4": 1, "5": 3,
    "6": 0, "7": 2, "8": 1, "9": 2, "10": 2,
}

MODE_RANGES = {
    "0": [("T1", 10, 600, "seconds"), ("T2", 1, 24, "hours")],
    "1": [("T", 10, 600, "seconds")],
    "2": [("T", 10, 60, "minutes"), ("X", 0, 1, ""), ("Y", 0, 1, "")],
    "3": [("T", 1, 24, "hours")],
    "4": [("T", 10, 600, "seconds")],
    "5": [("T", 1, 60, "minutes"), ("X", 0, 0, "fixed to 0"), ("Y", 0, 1, "")],
    "7": [("T1", 10, 1440, "minutes"), ("T2", 1, 24, "hours")],
    "8": [("T", 10, 60, "seconds")],
    "9": [("T1", 10, 1440, "minutes"), ("T2", 1, 24, "hours")],
    "10": [("T1", 0, 23, "hours"), ("T2", None, None, "HH:MM UTC")],
}

MODE_NAMES = {
    "0": "Mix Mode — fast reporting when moving, slow when still",
    "1": "Real-time Mode — fixed interval, GPS+TCP always on",
    "2": "GPS Auto Mode — configurable GPS/TCP always-on behaviour",
    "3": "Deep Sleep Mode — one report every 1–24 h (max battery)",
    "4": "Vibrate Mode — wakes and reports on motion",
    "5": "WiFi-only Mode — WiFi positioning, no GPS",
    "6": "SMS-only Mode — only responds to SMS commands",
    "7": "Smart Mode — low-power mix (GPS priority)",
    "8": "Home Mode — sleeps indoors, tracks outdoors (MT710)",
    "9": "Smart Mode, WiFi positioning priority (MT710)",
    "10": "Clock Mode — scheduled reports at fixed UTC times",
}

MODE_EXPLAIN = {
    "0": "Mix of Mode 3 and Mode 4: reports every T1 seconds while "
         "vibrating, every T2 hours while still. T1 10–600 s, T2 1–24 h.",
    "1": "Always-on real-time tracking. T 10–600 s. Best for testing; "
         "highest power draw. GPS and TCP stay connected.",
    "2": "T 10–60 min report interval. X=1 keeps GPS always on, X=0 wakes "
         "GPS per report. Y=1 keeps TCP always connected, Y=0 wakes TCP "
         "per report. Heartbeat (HBC) applies here.",
    "3": "Wakes once per interval, reports, sleeps again. T 1–24 h. "
         "Recommended for ~12 months of battery. PRIOR applies (GPS vs "
         "WiFi priority).",
    "4": "Sleeps until vibration. On wake: connects, reports immediately, "
         "then every T seconds while vibrating. Sleeps after ~7 min idle "
         "(3 min if data was sent). T 10–600 s.",
    "5": "WiFi positioning only (no GPS). T 1–60 min, X fixed to 0, "
         "Y controls always-on TCP. Heartbeat (HBC) applies.",
    "6": "Radio (modem) stays off; device answers SMS commands only. Send "
         "SMS 'WHERE0000' for a Google-Maps link. Timezone (896) applies.",
    "7": "Optimised Mode 0 with lower power: T1 10–1440 min moving, "
         "T2 1–24 h still. GPS positioning priority.",
    "8": "Home Mode: sleeps when indoors (near stored Home WiFi/geo), "
         "tracks outdoors at T 10–60 s while moving. Pair with AP/GEO "
         "commands. Factory default mode of the MT710.",
    "9": "Same as Mode 7 but prioritizes WiFi positioning. T1 10–1440 "
         "min moving, T2 1–24 h still.",
    "10": "Scheduled reporting: T1 0–23 h spacing, T2 start time HH:MM "
          "(UTC). Device generates up to 24 sub-alarms; T1=0 → one report "
          "per day.",
}

LOCK_EXPLAIN = (
    "Temporarily switches to real-time tracking at X seconds interval, "
    "then returns to the previous working mode after Y minutes. Useful to "
    "recover/observe a sleeping device without changing its mode "
    "permanently."
)


def _validate_mode(args: list[str]) -> Optional[str]:
    if not args:
        return "MODE needs a mode number (e.g. MODE,1,60)"
    mode = args[0]
    if mode not in MODE_ARG_COUNT:
        return (f"unknown working mode '{mode}' "
                f"(supported: {', '.join(MODE_ARG_COUNT)})")
    want = MODE_ARG_COUNT[mode]
    if len(args) - 1 != want:
        return (f"MODE,{mode} takes {want} parameter(s) after the mode "
                f"number (got {len(args) - 1})")
    ranges = MODE_RANGES.get(mode, [])
    for (name, lo, hi, unit), raw in zip(ranges, args[1:]):
        if lo is None:  # HH:MM field (MODE,10 T2)
            m = _HHMM_RE.match(raw)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                return "MODE,10 T2 must be HH:MM with hh≤23, mm≤59 (UTC)"
            continue
        if not _is_int(raw):
            return f"{name} must be a plain integer, got '{raw}'"
        if not _in_range(raw, lo, hi):
            return f"{name} must be {lo}–{hi} {unit}".strip()
    return None


# ── NWM / BAND reference values ───────────────────────────────────

NWM_VALUES = {
    "0,0,2": "LTE-M (Cat M1) with 2G fallback",
    "0,0,0": "Cat M1 with GSM fallback (PDF downlink list)",
    "0,1,0": "NB-IoT with GSM fallback (PDF downlink list)",
    "3,0,2": "Cat M1 (LTE-M) only",
    "3,1,3": "NB-IoT only (factory default)",
    "1,2,1": "GSM (2G) only",
}

NWM_NOTE = (
    "NWM,0,0,2 (LTE-M + 2G fallback) is missing from the official PDF but "
    "was confirmed on real hardware by Mictrack's web tool. The MT710 "
    "hardware supports Cat-M1/NB-IoT bands B1/B2/B3/B4/B5/B8/B12/B13/B18/"
    "B19/B20/B26/B28 and GSM 850/900/1800/1900."
)

# MT710 hardware bands (user manual): Cat-M1/NB-IoT B1/B2/B3/B4/B5/B8/B12/
# B13/B18/B19/B20/B26/B28; GSM 850/900/1800/1900 quad band.

REGION_PRESETS = {
    "global": {"nwm": "NWM,0,0,2", "band": "BAND,0,0,f",
               "label": "Global — LTE-M + 2G fallback"},
    "usa":    {"nwm": "NWM,3,0,2", "band": "BAND,0,0,f",
               "label": "USA / Canada — LTE-M only"},
    "au":     {"nwm": "NWM,3,0,2", "band": "BAND,28,0,f",
               "label": "Australia / NZ — LTE-M, lock Band 28"},
}


def _validate_nwm(args: list[str]) -> Optional[str]:
    joined = ",".join(args)
    if joined not in NWM_VALUES:
        return ("NWM must be one of: " +
                "; ".join(f"{k} ({v})" for k, v in NWM_VALUES.items()))
    return None


# ── command registry ──────────────────────────────────────────────

COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    COMMANDS[cmd.keyword] = cmd
    return cmd


register(Command(
    "ETS", "Enter config session", Category.SESSION, [], "ETS", "ETS",
    "ETS,OK", "—", Save.QUERY,
    "Wake the device and open the USB config session. Must be sent first — "
    "most other commands are silently dropped without it.",
    note="Device replies 'WAIT CONFIG CMD......' while still booting; retry. "
         "The session ends on reboot, power cycle or MODE change.",
    needs_ets=False,
))

register(Command(
    "QTS", "Save & exit session", Category.SESSION, [], "QTS", "QTS",
    "QTS,OK", "—", Save.SAVES,
    "Save all pending settings and exit config mode WITHOUT rebooting.",
))

register(Command(
    "RCONF", "Read full configuration", Category.SESSION,
    [ArgSpec("page", "int", 1, 4, unit="page",
             help="PDF documents RCONF,1..RCONF,4 as paged dumps over the "
                  "downlink channel; plain RCONF over USB returns "
                  "everything.", optional=True)],
    "RCONF", "RCONF", "multi-line KEY:value dump, ends with IN:", "—",
    Save.QUERY,
    "Read the device's entire configuration as KEY:value lines (model, "
    "IMEI, firmware, server, mode, sensors…).",
))

register(Command(
    "803", "Set APN", Category.NETWORK,
    [ArgSpec("apn", "str", example="cmnbiot",
             help="Access Point Name from your SIM provider. Max 36 chars, "
                  "no spaces or commas."),
     ArgSpec("user", "str", example="", allow_empty=True,
             help="APN username — most IoT SIMs need none; leave empty but "
                  "keep the comma."),
     ArgSpec("pass", "str", example="", allow_empty=True,
             help="APN password — most IoT SIMs need none.")],
    "803,<apn>,<user>,<pass>", "803,cmnbiot,,", "803,OK", "empty",
    Save.EXPLICIT,
    "Set the cellular Access Point Name (and optional username/password) "
    "used for the data connection.",
))

register(Command(
    "804", "Set server IP/port", Category.NETWORK,
    [ArgSpec("server", "str", example="e.trackits.com",
             help="Domain or IP — no http:// prefix, no path, no spaces."),
     ArgSpec("port", "int", 1, 65535, unit="port", example="7700")],
    "804,<server>,<port>", "804,e.trackits.com,7700", "804,OK", "empty",
    Save.EXPLICIT,
    "Set the tracking-server address (IP or domain) and TCP/UDP port.",
))

register(Command(
    "800", "Set protocol TCP/UDP", Category.NETWORK,
    [ArgSpec("x", "enum", options=["TCP", "UDP"])],
    "800,<TCP|UDP>",     "800,TCP", "800,OK", "TCP", Save.EXPLICIT,
    "Choose the transport protocol used towards the tracking server. "
    "TCP is connection-oriented (recommended); UDP is fire-and-forget "
    "with lower overhead.",
))

register(Command(
    "NWM", "Set network mode", Category.NETWORK,
    [ArgSpec("mode", "enum", options=list(NWM_VALUES), example="0,0,2")],
    "NWM,<a>,<b>,<c>", "NWM,0,0,2", "NWM,OK", "NWM,3,1,3", Save.EXPLICIT,
    "Select which cellular technology the modem uses.",
    note=NWM_NOTE,
))

register(Command(
    "BAND", "Lock modem bands", Category.NETWORK,
    [ArgSpec("catm1", "int", 0, 99, unit="LTE-M band", example="0",
             help="0 = automatic. e.g. 12 → Band 12, 28 → Band 28 (AU)."),
     ArgSpec("nbiot", "int", 0, 99, unit="NB-IoT band", example="0",
             help="0 = automatic. e.g. 8 → Band 8 (EU, factory default)."),
     ArgSpec("f", "enum", options=["f"],
             help="literal 'f' — GSM stays on automatic quad-band")],
    "BAND,<catM1>,<NB-IoT>,f", "BAND,28,0,f", "BAND,OK",
    "ANY (M1), 8 (NB1)", Save.EXPLICIT,
    "Lock specific LTE-M / NB-IoT bands. Use 0,0,f for automatic "
    "selection on both.",
    note="Third field is literally the letter 'f' (fixed).",
))

register(Command(
    "RWT", "TCP keep-alive", Category.NETWORK,
    [ArgSpec("x", "int", 60, 600, unit="sec", example="180")],
    "RWT,<sec>", "RWT,60", "RWT,OK", "120s", Save.EXPLICIT,
    "Interval of TCP keep-alive packets while a connection is held open.",
))

register(Command(
    "896", "Set timezone", Category.NETWORK,
    [ArgSpec("x", "sint", -720, 780, unit="min", example="+480",
             help="Timezone × 60 minutes: +8:00 → +480, -3:00 → -180. "
                  "Range covers UTC-12:00 … UTC+13:00.")],
    "896,<tz*60>", "896,+480", "896,OK", "0", Save.EXPLICIT,
    "Device-local timezone (only used by MODE 6). Does not change the "
    "timezone on your server.",
))

register(Command(
    "777", "Set password", Category.SYSTEM,
    [ArgSpec("password", "str", pattern=r"^[A-Za-z0-9]{4}$", example="1234",
             help="Exactly 4 letters/digits. Protects SMS/downlink "
                  "commands; default is 0000.")],
    "777,<password>", "777,1234", "777,OK", "0000", Save.EXPLICIT,
    "Change the command password used by SMS and platform downlink "
    "commands.",
    note="USB form takes just the new password. The SMS form is "
         "777<new><old> concatenated; over USB the old password is not "
         "required.",
))

register(Command(
    "MODE", "Set working mode", Category.MODE,
    [ArgSpec("mode", "enum", options=sorted(MODE_ARG_COUNT, key=int),
             example="1"),
     ArgSpec("params", "str", example="60",
             help="Mode-specific interval parameters — see the mode table "
                  "in the reference browser.")],
    "MODE,<n>[,params…]", "MODE,1,60", "MODE,OK", "MODE,8,10s,1,0",
    Save.SAVES_REBOOT,
    "Set the working mode (power/reporting behaviour). SAVES SETTINGS AND "
    "REBOOTS — always send last in a batch.",
    note="PDF lists MODE,1 as '60-600 s' in its USB table but '10-600 s' "
         "everywhere else; the hardware-validated range 10-600 is used.",
))

register(Command(
    "LOCK", "Temporary lock mode", Category.MODE,
    [ArgSpec("x", "int", 10, 60, unit="sec",
             help="Real-time tracking interval while locked."),
     ArgSpec("y", "int", 1, 60, unit="min",
             help="After this long, revert to the previous working mode.")],
    "LOCK,<sec>,<min>", "LOCK,10,1", "LOCK,OK", "—", Save.EXPLICIT,
    LOCK_EXPLAIN,
))

register(Command(
    "HBC", "Heartbeat interval", Category.NETWORK,
    [ArgSpec("t", "int", 5, 60, unit="min", example="5")],
    "HBC,<min>", "HBC,5", "HBC,OK", "5m", Save.EXPLICIT,
    "Heartbeat report interval. Only effective in MODE 2 and MODE 5 with "
    "TCP always-on.",
))

register(Command(
    "DUR", "GPS search duration", Category.POSITIONING,
    [ArgSpec("x", "int", 1, 10, unit="min", example="2")],
    "DUR,<min>", "DUR,5", "DUR,OK", "2m", Save.EXPLICIT,
    "How long GPS keeps searching for a fix after the device wakes up. "
    "Longer = better fix odds, worse battery.",
))

register(Command(
    "LEP", "Last-known position", Category.POSITIONING,
    [ArgSpec("x", "bit", help="0 = report invalid data when no fix; "
                              "1 = re-report last known position.")],
    "LEP,<0|1>", "LEP,1", "LEP,ON", "OFF", Save.EXPLICIT,
    "When GPS is unavailable, re-report the last known position instead "
    "of invalid coordinates.",
))

register(Command(
    "LBS", "Cell-tower positioning", Category.POSITIONING,
    [ArgSpec("x", "int", 0, 3, example="2",
             help="0 off; 1 when GPS+WiFi unavailable; 2 also when fewer "
                  "than 4 WiFi MACs seen; 3 whenever GPS is unavailable.")],
    "LBS,<0-3>", "LBS,2", "LBS,OK", "0", Save.EXPLICIT,
    "Include LBS (cell tower) positioning data in reports as a fallback.",
))

register(Command(
    "AGPS", "WiFi/assisted positioning", Category.POSITIONING,
    [ArgSpec("x", "bit", help="1 = report the WiFi-scan location when GPS "
                              "is unavailable.")],
    "AGPS,<0|1>", "AGPS,1", "AGPS,ON", "ON", Save.EXPLICIT,
    "Assisted positioning: uses WiFi scan results to compute a position "
    "when GPS has no fix.",
))

register(Command(
    "XTRA", "Qualcomm A-GPS cache", Category.POSITIONING,
    [ArgSpec("x", "bit")],
    "XTRA,<0|1>", "XTRA,1", "XTRA,OK", "ON", Save.EXPLICIT,
    "Use Qualcomm XTRA assistance data (downloaded satellite ephemeris) "
    "for faster GPS fixes.",
    note="Absent from the official PDF command tables; present in RCONF "
         "output and the web tool.",
))

register(Command(
    "PRIOR", "Positioning priority", Category.POSITIONING,
    [ArgSpec("x", "bit", help="0 = GPS priority; 1 = WiFi priority.")],
    "PRIOR,<0|1>", "PRIOR,0", "PRIOR,GPS", "GNSS", Save.EXPLICIT,
    "Choose GPS-first or WiFi-first positioning. Only effective in "
    "MODE 3.",
))

register(Command(
    "WHERE", "Query position now", Category.POSITIONING, [],
    "WHERE", "WHERE", "current coordinates", "—", Save.QUERY,
    "Ask the device for its current GPS latitude/longitude.",
    mt710_only=True,
))

register(Command(
    "SCAN", "Scan WiFi APs", Category.HOME, [], "SCAN", "SCAN",
    "list of MAC addresses", "—", Save.QUERY,
    "Trigger a WiFi scan (find candidate Home MAC addresses).",
    mt710_only=True,
))

register(Command(
    "SEARCH", "Search current position", Category.HOME, [], "SEARCH",
    "SEARCH", "current lat/lng", "—", Save.QUERY,
    "Search the current position (used together with the Home geofence "
    "feature).",
    mt710_only=True,
))

register(Command(
    "AP", "Set Home WiFi MACs", Category.HOME,
    [ArgSpec("r1", "str", must_be_empty=True,
             help="reserved — must be empty"),
     ArgSpec("r2", "str", must_be_empty=True,
             help="reserved — must be empty"),
     ArgSpec("mac1", "mac", example="6877248FA31A", allow_empty=True,
             help="12 hex chars, or empty. RCONF shows the first fields "
                  "fixed to 1057 and 300."),
     ArgSpec("mac2", "mac", example="", allow_empty=True),
     ArgSpec("mac3", "mac", example="", allow_empty=True)],
    "AP,,,<mac1>,<mac2>,<mac3>", "AP,,,6877248FA31A,7CB59B3B8777,",
    "AP,OK", "AP:1057,300,,,", Save.EXPLICIT,
    "Store up to 3 WiFi MAC addresses as the 'Home' zone. Combined with "
    "MODE 8 (Home Mode): sleep indoors, track outdoors.",
    mt710_only=True,
))

register(Command(
    "GEO", "Set Home geofence", Category.HOME,
    [ArgSpec("lat", "float", example="22.64823"),
     ArgSpec("lng", "float", example="114.03440"),
     ArgSpec("radius", "int", 30, 300, unit="m", example="30")],
    "GEO,<lat>,<lng>,<radius>", "GEO,22.64823,114.03440,30", "GEO,OK",
    "GEO:Lat=0.00000,lng=0.00000,R=0", Save.EXPLICIT,
    "Set the Home geofence centre and radius (30–300 m) used by Home "
    "Mode (MODE 8).",
    mt710_only=True,
))

register(Command(
    "GSEN", "Vibration sensitivity", Category.SENSORS,
    [ArgSpec("t1", "int", 1, 125, unit="mg", example="70",
             help="Wake-up threshold: static → vibration-detection "
                  "transition. Default 70 mg."),
     ArgSpec("t2", "int", 10, 2000, unit="mg", example="70",
             help="Vibrate threshold: magnitude of an effective "
                  "vibration. Default 70 mg."),
     ArgSpec("t3", "int", 1, 32, unit="count", example="2",
             help="Vibrate sensitivity: number of effective vibrations "
                  "needed. Default 2."),
     ArgSpec("t4", "int", 1, 10, unit="min", example="1",
             help="Vibrate time: period of a single detection window. "
                  "Default 1 min.")],
    "GSEN,<t1>,<t2>,<t3>,<t4>", "GSEN,70,70,2,1", "GSEN,OK",
    "GSEN:70,70,2,187", Save.EXPLICIT,
    "Tune the 3-axis accelerometer for motion wake-up (lower = more "
    "sensitive = more wake-ups = less battery).",
    note="Documented in the PDF downlink list (#999# SMS form and RCONF "
         "GSEN field) but NOT whitelisted in Mictrack's own web tool. "
         "RCONF default shows T4=187, outside the documented [1,10] "
         "range — treated as an internal encoding.",
    source="pdf",
))

register(Command(
    "MSW", "Power button enable", Category.SENSORS,
    [ArgSpec("x", "bit", help="0 = power button disabled (no power-off, "
                              "no SOS); 1 = enabled.")],
    "MSW,<0|1>", "MSW,1", "MSW,ON", "ON", Save.EXPLICIT,
    "Enable/disable the SOS/power button. Disabling prevents the device "
    "being switched off in the field.",
    mt710_only=True,
))

register(Command(
    "DBG", "Debug output", Category.SYSTEM,
    [ArgSpec("x", "bit", help="1 = stream <Trace> diagnostics (more power); "
                              "0 = quiet.")],
    "DBG,<0|1>", "DBG,1", "DBG,ON", "OFF", Save.EXPLICIT,
    "Verbose diagnostics on the USB port (<Trace> lines: VBAT, network "
    "state…). Increases power consumption — disable after testing.",
))

register(Command(
    "KEY", "Vendor/licence key", Category.SYSTEM,
    [ArgSpec("code", "str", example="984504615adacba7167bed1c16678fe5",
             help="Vendor-supplied key. Not validated; forwarded "
                  "verbatim.")],
    "KEY,<code>", "KEY,984504615adacba7167bed1c16678fe5", "KEY,OK", "—",
    Save.EXPLICIT,
    "Apply a vendor licence/configuration key. Only useful with a key "
    "supplied by Mictrack or a reseller.",
    source="web",
))

register(Command(
    "LTP", "Light tamper alert", Category.SENSORS,
    [ArgSpec("x", "bit")],
    "LTP,<0|1>", "LTP,1", "LTP,ON", "—", Save.EXPLICIT,
    "Light-sensor tamper alert (wake + alert when the device is opened "
    "or removed from its mount).",
    ignored_on_mt710=True,
    note="MT700 feature — the MT710 has no light sensor; the command is "
         "accepted over USB but SILENTLY IGNORED by the firmware.",
    source="web",
))

register(Command(
    "REBOOT", "Save & reboot", Category.SYSTEM, [], "REBOOT", "REBOOT",
    "REBOOT,OK", "—", Save.SAVES_REBOOT,
    "Save all settings and reboot. The USB COM port lives in the cable, "
    "so the connection survives and the boot log streams in.",
))

register(Command(
    "RESET", "Factory reset", Category.SYSTEM, [], "RESET", "RESET",
    "RESET,OK (reply may be empty)", "factory defaults", Save.SAVES,
    "Erase ALL settings and restore factory defaults. Irreversible. "
    "Does NOT reboot — the device stays connected.",
))


# ── validator ─────────────────────────────────────────────────────

_SPECIAL_VALIDATORS = {
    "MODE": _validate_mode,
    "NWM": _validate_nwm,
}


def _validate_generic(cmd: Command, args: list[str]) -> Optional[str]:
    if not (cmd.min_args <= len(args) <= cmd.max_args):
        return (f"{cmd.keyword} needs "
                f"{cmd.min_args if cmd.min_args == cmd.max_args else f'{cmd.min_args}–{cmd.max_args}'} "
                f"field(s) — format: {cmd.format}")
    for spec, raw in zip(cmd.args, args):
        if spec.must_be_empty and raw != "":
            return f"{cmd.keyword} field '{spec.name}' is reserved and must be empty"
        if raw == "" and (spec.optional or spec.allow_empty or spec.must_be_empty):
            continue
        if raw == "":
            return f"{cmd.keyword} field '{spec.name}' cannot be empty"
        if spec.pattern and not re.match(spec.pattern, raw):
            return (f"{cmd.keyword} {spec.name} has an invalid format "
                    f"(expected e.g. '{spec.example}')")
        if " " in raw:
            return f"{cmd.keyword} field '{spec.name}' must not contain spaces"
        if spec.kind == "bit" and raw not in ("0", "1"):
            return f"{cmd.keyword} {spec.name} must be 0 or 1 (got '{raw}')"
        if spec.kind == "enum" and spec.options and raw not in spec.options:
            return (f"{cmd.keyword} {spec.name} must be one of "
                    f"{'/'.join(spec.options)} (got '{raw}')")
        if spec.kind in ("int", "sint"):
            ok_int = _is_int(raw) if spec.kind == "int" else _is_signed_int(raw)
            if not ok_int:
                return (f"{cmd.keyword} {spec.name} must be a plain integer "
                        f"(got '{raw}')")
            if spec.minimum is not None and not _in_range(raw, spec.minimum, spec.maximum):
                return (f"{cmd.keyword} {spec.name} must be "
                        f"{spec.minimum:g}…{spec.maximum:g}"
                        f"{f' {spec.unit}' if spec.unit else ''} (got '{raw}')")
        if spec.kind == "float" and not _FLOAT_RE.match(raw):
            return f"{cmd.keyword} {spec.name} must be a decimal number (got '{raw}')"
        if spec.kind == "mac" and raw and not _MAC_RE.match(raw):
            return f"{cmd.keyword} {spec.name} must be 12 hex chars (got '{raw}')"
    return None


def validate(line: str) -> tuple[bool, Optional[str]]:
    """Validate one raw command line. Returns (ok, reason).

    Rules mirror the web tool: suspicious characters → hard error;
    keyword must be exactly uppercase (the firmware ignores anything
    else); unknown keywords are rejected — the device gives NO reply at
    all to unknown commands; argument shapes are enforced per command.
    """
    line = line.strip()
    if not line:
        return False, "empty command"
    reason = suspicious_char_reason(line)
    if reason:
        return False, f"command {reason}"
    keyword, _, rest = line.partition(",")
    args = rest.split(",") if rest else []
    if keyword != keyword.upper():
        return False, (f"keyword '{keyword}' must be UPPERCASE "
                       "(the device ignores lowercase commands)")
    if keyword not in COMMANDS:
        prefix = keyword[:2].upper()
        close = [k for k in COMMANDS if k.startswith(prefix)]
        hint = f" — did you mean {', '.join(close[:3])}?" if close else ""
        return False, f"unknown command '{keyword}'{hint}"
    cmd = COMMANDS[keyword]
    validator = _SPECIAL_VALIDATORS.get(keyword)
    if validator:
        err = validator(args)
    elif cmd.args:
        err = _validate_generic(cmd, args)
    else:
        if rest.strip():
            return False, f"{keyword} takes no arguments"
        err = None
    if err:
        return False, err
    return True, None


def validate_batch(lines: list[str]) -> list[tuple[str, bool, Optional[str]]]:
    """Validate a batch (import file / advanced box): per-line results."""
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ok, reason = validate(line)
        out.append((line, ok, reason))
    return out


def build_batch_commands(lines: list[str]) -> tuple[list[str], bool]:
    """Normalise a batch for running, web-tool style.

    * MODE lines are moved to the end (order preserved among them)
    * ETS is dropped (the runner always handshakes first)
    * if nothing self-saving is present, REBOOT is appended so settings
      persist
    Returns (commands, reboot_expected).
    """
    cmds = []
    modes = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kw = line.split(",", 1)[0]
        if kw == "ETS":
            continue
        if kw == "MODE":
            modes.append(line)
        else:
            cmds.append(line)
    cmds.extend(modes)
    last_kw = cmds[-1].split(",", 1)[0] if cmds else ""
    reboot_expected = last_kw in ("MODE", "REBOOT")
    if last_kw not in ("MODE", "REBOOT", "QTS"):
        cmds.append("REBOOT")
        reboot_expected = True
    return cmds, reboot_expected
