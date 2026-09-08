"""Reference content: RCONF field docs, working-mode guide, SMS/downlink
formats, LED guide and known documentation discrepancies.

Compiled from Mictrack_MT710_Commands_List.pdf (Config Info Description
tables), MT710_User_Manual_V1.0.pdf and the config.mictrack.com web tool
embedded help (RCONF_HELP / RCONF_LABELS).
"""

from __future__ import annotations

from .commands import MODE_EXPLAIN, MODE_NAMES, NWM_VALUES

# ── RCONF field reference ─────────────────────────────────────────
# key -> (friendly label, explanation)

RCONF_FIELDS: dict[str, tuple[str, str]] = {
    "MDL": ("Model", "Device model, e.g. MT710."),
    "MODEL": ("Model", "Device model (alternate key on some firmware)."),
    "ID": ("IMEI", "Device IMEI number. May be empty right after power-on "
                   "while the cellular module boots."),
    "SV": ("Firmware", "Device firmware version, e.g. V2.1.6."),
    "HV": ("Hardware version", "Hardware revision."),
    "MV": ("Module version", "Cellular module firmware (e.g. Quectel BG96)."),
    "ISD": ("Firmware date", "Firmware release date (DD/MM/YY)."),
    "NET": ("Protocol", "TCP or UDP — transport used towards the server "
                        "(see command 800)."),
    "GU": ("GPRS credentials", "GPRS username/password baked into reports; "
                               "ignorable when parsing on the server."),
    "UP": ("SMS password", "Password protecting SMS commands (default 0000; "
                           "change with 777)."),
    "SRV": ("Server", "Tracking server: 'DM,host,port' for a domain or "
                      "'IP,ip,port'. 'NC,,0' = not configured."),
    "PORT": ("Port", "Server port (when reported separately)."),
    "APN": ("APN", "Access Point Name (see command 803)."),
    "NWM": ("Network mode", "Modem technology selection (see NWM). "
                            "3,0,2 = Cat-M1 only · 3,1,3 = NB-IoT only · "
                            "1,2,1 = GSM only · 0,0,2 = LTE-M + 2G fallback."),
    "M1": ("LTE-M band", "Locked Cat-M1 band; ANY = automatic."),
    "NB1": ("NB-IoT band", "Locked NB-IoT band; ANY = automatic. Factory "
                           "default shows 8."),
    "GSM": ("GSM band", "Locked GSM band; ANY = automatic quad-band."),
    "EDRX": ("eDRX", "Extended idle-mode DRX for ultra-low-power modes; "
                     "carrier-dependent (default 0,5,0010)."),
    "MODE": ("Working mode", "Current mode + parameters, e.g. "
                             "'8,10s,1,0' (see the mode guide)."),
    "HBC": ("Heartbeat", "Heartbeat interval (5–60 min); effective in "
                         "modes 2 & 5 with always-on TCP."),
    "DUR": ("GPS search time", "Minutes GPS keeps searching after wake "
                               "(1–10)."),
    "RWT": ("TCP keep-alive", "Keep-alive interval in seconds (60–600)."),
    "LEP": ("Last-known position", "ON = re-report last known position "
                                   "when GPS is unavailable."),
    "LBS": ("Cell positioning", "0 off; 1/2/3 progressively more cell-tower "
                                "fallback (see LBS command)."),
    "AGPS": ("Assisted positioning", "ON = use WiFi scan position when "
                                     "GPS unavailable."),
    "XTRA": ("A-GPS cache", "Qualcomm XTRA assistance data for faster "
                            "fixes."),
    "MSW": ("Power button", "ON = power/SOS button enabled."),
    "LTP": ("Light tamper", "Light-sensor alert (MT700 hardware only; "
                            "ignored by MT710)."),
    "TZ": ("Timezone", "Device timezone ×60 min (MODE 6 only)."),
    "GSEN": ("Vibration sensor", "wake/vibrate thresholds (mg), "
            "sensitivity count, vibrate time — see GSEN command."),
    "ANG": ("Angle sensor", "Reserved (0,0,0)."),
    "PRIOR": ("Positioning priority", "GNSS (GPS first) or WIFI — "
                                      "MODE 3 only."),
    "CCID": ("ICCID", "SIM card integrated circuit card ID."),
    "IMSI": ("IMSI", "SIM international mobile subscriber identity."),
    "AP": ("Home WiFi MACs", "Home-zone WiFi MAC addresses (first two "
                             "fields are fixed 1057,300)."),
    "GEO": ("Home geofence", "Home-zone centre + radius R in metres "
                             "(30–300)."),
    "SAVE": ("Save info", "Reserved (1,0,10m)."),
    "SOC": ("Battery", "Battery/charging state, e.g. NORMAL."),
    "BAT": ("Battery", "Battery voltage/level, when reported."),
    "CSQ": ("Signal", "Cellular signal strength, when reported."),
    "LIC": ("Licence", "Internal licence check (OK)."),
    "AU": ("Author", "Internal — firmware author."),
    "IN": ("End marker", "Internal — marks the end of the RCONF dump."),
}

# fields hidden from the friendly grid (internal / noise)
RCONF_HIDDEN = {"EDRX", "CSN", "ANG", "SAVE", "AU", "IN", "GU", "SOC",
                "TZ", "LIC", "UP", "ISSUED", "AP", "GEO", "LTP"}

# display order for the device-info grid
RCONF_ORDER = ["MDL", "MODEL", "ID", "SV", "MV", "HV", "ISD", "CCID",
               "IMSI", "NET", "SRV", "PORT", "APN", "NWM", "BAND", "M1",
               "NB1", "GSM", "MODE", "HBC", "DUR", "RWT", "LEP", "LBS",
               "AGPS", "XTRA", "MSW", "LTP", "GSEN", "PRIOR", "ANG",
               "EDRX", "TZ"]


def mode_table() -> str:
    rows = []
    for n in sorted(MODE_EXPLAIN, key=int):
        rows.append(f"MODE,{n} — {MODE_NAMES[n]}\n    {MODE_EXPLAIN[n]}")
    return "\n\n".join(rows)


# ── general guide sections ────────────────────────────────────────

GUIDE_SECTIONS: list[tuple[str, str]] = [
    ("Getting started", (
        "1. Connect the USB Config Cable (Prolific/CH340/CP210x/FTDI) "
        "to the tracker's charging contacts.\n"
        "2. Power the device on with the SOS button — the blue LED must "
        "be lit.\n"
        "3. Pick the serial port and press Connect. The tool opens the "
        "port at 921600 8N1, raises DTR/RTS, wakes the device with ETS "
        "and reads the configuration with RCONF."
    )),
    ("The ETS session", (
        "ETS (Enter To Set) wakes the tracker and opens the config "
        "session. Without it most commands are silently dropped.\n"
        "· While booting the device answers 'WAIT CONFIG CMD......' — "
        "just retry (the tool tries 3×).\n"
        "· The session ends when the device reboots, power-cycles or "
        "changes MODE. The tool tracks this passively and re-handshakes "
        "before the next batch.\n"
        "· A sleeping device may need the power button pressed once to "
        "wake it."
    )),
    ("Saving settings", (
        "· Most commands only stage a change; nothing is stored until a "
        "save happens.\n"
        "· MODE saves AND reboots — that's why it is always sent last.\n"
        "· REBOOT saves and reboots without changing the mode.\n"
        "· QTS saves and exits config mode WITHOUT rebooting.\n"
        "· RESET wipes everything to factory defaults (no reboot).\n"
        "The USB COM port lives in the cable, so the connection survives "
        "reboots and you can watch the boot log."
    )),
    ("LED indicators", (
        "RED (charge): off = no charge/full · solid = charging.\n"
        "BLUE (SYS):   off = power off or sleeping · solid = running.\n"
        "GREEN (GPS):  off = no fix or sleeping · solid = GPS fix.\n"
        "Power: press SOS to power on · hold 8 s to power off · hold 3 s "
        "for an SOS alert."
    )),
    ("Battery & working modes", (
        "The 650 mAh battery lasts up to ~12 months at one report/day "
        "(MODE 3). Ranking from most to least power-hungry:\n"
        "MODE 1 (always on) > MODE 2/5 (always-on options) > MODE 0/4 "
        "(motion-driven) > MODE 7/9 (smart low power) > MODE 8 (home) > "
        "MODE 3 (deep sleep).\n"
        "GPS search time (DUR), heartbeat (HBC) and debug output (DBG) "
        "all trade battery for behaviour."
    )),
    ("SMS command formats", (
        "Away from USB the same settings work over SMS using the "
        "password (default 0000):\n"
        "· APN:      #803#0000#apn##\n"
        "· Server:   #804#0000#host#port##\n"
        "· Mode:     MODE,1,0000,60\n"
        "· Duration: *DUR#0000#2##\n"
        "· Timezone: 8960000+08:00\n"
        "· Read:     *RCONF#1##  (pages 1-4)\n"
        "· Reset:    *RESET#0000##\n"
        "Change the password first: SMS '777' + new + old, e.g. "
        "77712340000."
    )),
    ("Platform downlink format", (
        "A tracking platform can push the same commands to the device "
        "over TCP/UDP wrapped as:\n"
        "  #IMEI#REPLY#COMMAND##  →  #IMEI#REPLY#803,OK##\n"
        "RCONF over downlink is paged: RCONF,1…RCONF,4."
    )),
    ("Network modes & bands", (
        "The MT710 modem (Quectel BG96 class) supports:\n"
        "· LTE-M (Cat M1)  bands B1/B2/B3/B4/B5/B8/B12/B13/B18/B19/B20/"
        "B26/B28\n"
        "· NB-IoT          same band list\n"
        "· GSM             850/900/1800/1900 MHz quad-band\n\n"
        + "\n".join(f"· NWM,{k} = {v}" for k, v in NWM_VALUES.items()) +
        "\n\nLock bands with BAND,<catM1>,<nbiot>,f — 0 means automatic "
        "and the third field is literally 'f' (GSM fixed quad-band). "
        "Australia/NZ: lock LTE-M band 28 (BAND,28,0,f)."
    )),
    ("Home zone (MODE 8)", (
        "Home Mode sleeps while the device is indoors near its Home "
        "zone and tracks at 10–60 s intervals once outdoors and moving.\n"
        "· AP,,,mac1,mac2,mac3 — up to 3 WiFi MAC addresses (12 hex "
        "chars each) that identify 'home'.\n"
        "· GEO,lat,lng,radius — a geofence circle (30–300 m) as home.\n"
        "· SCAN lists surrounding MAC addresses; SEARCH reports the "
        "current position to use as the centre."
    )),
    ("Known documentation discrepancies", (
        "The official PDFs are internally inconsistent in places. This "
        "tool follows the hardware-validated web tool where they "
        "disagree:\n"
        "· MODE,1 range: PDF USB table says 60–600 s once, but 10–600 s "
        "everywhere else → 10–600 used.\n"
        "· NWM,0,0,2 (LTE-M + 2G fallback): not in the PDF, confirmed "
        "working on real hardware by Mictrack's web tool → supported.\n"
        "· LTP: listed in the web tool, but the MT710 has no light "
        "sensor — the firmware silently ignores it (MT700 feature).\n"
        "· XTRA / SCAN / SEARCH / AP / GEO: missing from the PDF USB "
        "table but work over USB/ETS.\n"
        "· GSEN: documented in the PDF (SMS #999#) but NOT whitelisted "
        "in the web tool; RCONF default shows T4=187 outside the "
        "documented 1–10 range.\n"
        "· RCONF,1..4 paged dumps exist only over the platform "
        "downlink; USB RCONF returns everything."
    )),
    ("Troubleshooting", (
        "· No port appears → install the USB-serial driver for your "
        "cable chip (Prolific/CH340/CP210x/FTDI).\n"
        "· Connects but no reply → device asleep or off (blue LED out); "
        "press the power button once, retry ETS.\n"
        "· Command times out → unknown/misspelled commands get NO reply "
        "at all; the validator catches most cases.\n"
        "· IMEI shows empty right after boot → the cellular module is "
        "still initialising; re-read RCONF in a few seconds.\n"
        "· After REBOOT the port stays valid (it lives in the cable) — "
        "watch the boot log, then re-run ETS."
    )),
]


# ── raw-send presets (filtered for MT710) ─────────────────────────

PRESETS: list[tuple[str, str]] = [
    ("START session", "ETS"),
    ("Set APN", "803,iot.1nce.net,,"),
    ("Server IP/port", "804,e.trackits.com,7700"),
    ("LTE-M + 2G fallback", "NWM,0,0,2"),
    ("LTE-M only", "NWM,3,0,2"),
    ("NB-IoT only", "NWM,3,1,3"),
    ("Auto band", "BAND,0,0,f"),
    ("Band 28 (AU)", "BAND,28,0,f"),
    ("MODE 0 mix", "MODE,0,60,1"),
    ("MODE 1 real-time", "MODE,1,60"),
    ("MODE 3 deep sleep", "MODE,3,1"),
    ("MODE 7 smart", "MODE,7,10,1"),
    ("MODE 8 home", "MODE,8,10"),
    ("MODE 9 smart WiFi", "MODE,9,10,1"),
    ("Debug ON", "DBG,1"),
    ("Debug OFF", "DBG,0"),
    ("GPS duration", "DUR,2"),
    ("Last known pos ON", "LEP,1"),
    ("LBS mode 2", "LBS,2"),
    ("AGPS ON", "AGPS,1"),
    ("XTRA ON", "XTRA,1"),
    ("Position priority GPS", "PRIOR,0"),
    ("WHERE (position now)", "WHERE"),
    ("SCAN (WiFi MACs)", "SCAN"),
    ("SEARCH (position)", "SEARCH"),
    ("Home MACs", "AP,,,6877248FA31A,,"),
    ("Home geofence", "GEO,22.64823,114.03440,30"),
    ("Vibration sensor", "GSEN,70,70,2,1"),
    ("Read config", "RCONF"),
    ("Save & exit (no reboot)", "QTS"),
    ("Save & reboot", "REBOOT"),
    ("FACTORY RESET", "RESET"),
]
