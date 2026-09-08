# MT710 Config Tool

A terminal UI for configuring and monitoring the **Mictrack MT710** GPS
tracker over its USB config cable — a local, more complete alternative to
https://config.mictrack.com.

```
┌─ MT710 Config Tool ──────────────────────────────┬─ Device log ────────┐
│ [Setup] [Device] [Reference] [Guide] [Batch]     │ <CFG>:ETS,OK        │
│                                                  │ → RCONF             │
│ Network · Working Mode (all 11) · Options        │ MDL:MT710           │
│ Home zone (AP/GEO) · Danger zone                 │ SV:V2.1.8           │
├──────────────────────────────────────────────────┴─────────────────────┤
│ raw command: RCONF ▂                                                  │
└───────────────────────────────────────────────────────────────────────┘
```

## Features

- **Live serial console** — verbatim device output (`<Trace>:` debug
  chatter dimmed), timestamps, autoscroll pause, save-to-file, and a
  live activity ticker (`↓bytes ↑bytes · last RX 4s ago`) so a quiet
  device is never mistaken for a broken console. Note: the MT710 is
  silent on USB except when answering commands (reports go over the
  cellular link) — that ticker is how you tell.
- **Connection status pill** — `◌ DISCONNECTED` / `… CONNECTING` /
  `● CONNECTED` plus a clickable Connect (F2) button; a separate
  `● ETS` badge tracks the config session (it ends on reboot or `QTS`).
- **Debug toggle** — one-click `DBG: on/off` button in the console
  header (sends `ETS → DBG,x → QTS`, state auto-detected from RCONF and
  reply echoes). Debug increases power use — toggle off when done.
- **Raw command bar** — hardware-accurate validation (exact rules from
  Mictrack's own web tool), ↑/↓ history, 30+ presets, auto-uppercase of
  the keyword, confirm gate on `RESET`, "force send" escape hatch.
  Every send attempt leaves a trace in the feed — including rejections
  (invalid, not connected, no ETS session).
- **Full command reference** — every MT710 USB command with format,
  parameter ranges, defaults, expected replies, save semantics and
  per-command explanations merged from the official PDFs *and* the
  hardware-validated web tool — including every known documentation
  discrepancy, flagged inline.
- **All 11 working modes** (0–10 + LOCK) with contextual parameter
  inputs and power-behaviour notes — the web tool only exposes four.
- **Setup forms** (scrollable) populated automatically from the device's
  RCONF, with per-section include checkboxes and a review-before-send
  apply flow (MODE last, auto-REBOOT if needed, reboot watchdog).
- **Batch runner** — multi-command textarea, per-line validation preview,
  import/export of web-tool-compatible `#`-commented command files.
- **Device info** — friendly RCONF grid with explanations + raw dump.

## Install

```bash
# with pip
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# or with uv (what this repo currently uses — venv has no pip)
uv venv .venv
uv pip install -e '.[dev]' --python .venv/bin/python

.venv/bin/mt710              # TUI (version chip sits in the console header)
.venv/bin/mt710 --version    # print version + resolved module path
.venv/bin/mt710 --probe      # scripted read-only check (ETS + RCONF)
.venv/bin/mt710 --list-ports
```

Requires Linux/macOS (or Windows) + a USB config cable
(Prolific/CH340/CP210x/FTDI are auto-detected). Your user needs access to
the serial port (`uucp`/`dialout` group on Linux).

## Keys

| key | action |
|---|---|
| `F2` | connect / disconnect (ETS wake + RCONF read) |
| `F5` | re-read config (RCONF) |
| `F9` | review & apply the Setup form |
| `c` | cycle serial port |
| `Enter` (raw bar) | validate + send raw command |
| `↑/↓` (raw bar) | command history |
| `Ctrl+T/L/S` | timestamps / clear log / save log |

Console-header buttons: `TS` timestamps · `Clear` · `Save` ·
`Pause`/`Follow` autoscroll · `DBG` toggle debug output. All buttons are
clickable; everything also works keyboard-only.

## Protocol notes (implemented in `src/mt710/`)

- 921600 baud 8N1, DTR+RTS asserted on open.
- Commands are sent **verbatim — no CR/LF terminator**; replies are
  `\r\n`-separated, prefixed `<CFG>:`, with no trailing newline (250 ms
  idle-flush reassembles them).
- `ETS` opens the config session (3 tries × 1.8 s); the session dies on
  reboot/boot-banner and is tracked passively.
- Unknown or lowercase commands get **no reply at all** — hence the
  validator.
- `MODE`/`REBOOT` save + reboot, `QTS` saves without rebooting, `RESET`
  wipes to factory defaults without rebooting; everything else needs an
  explicit save.

## Documentation discrepancies (all verified / catalogued)

| topic | PDF says | tool uses |
|---|---|---|
| `MODE,1` interval | 60–600 s (one table) vs 10–600 s (others) | 10–600 s |
| `NWM,0,0,2` | not documented | supported (confirmed on real MT710) |
| `LTP` | listed in web tool | flagged *ignored* — MT710 has no light sensor |
| `XTRA`,`SCAN`,`SEARCH`,`AP`,`GEO` | missing from PDF USB table | supported |
| `GSEN` | PDF-only (SMS `#999#`) | supported, T4=187 quirk noted |
| `RCONF,1..4` | paged dumps (downlink only) | USB `RCONF` returns all |

## Tests

```bash
.venv/bin/python -m pytest
```

156 cases: command validator matrix, RCONF parser (real PDF dump),
transport chunk reassembly / idle-flush / blank-line burst rule,
passive ETS/QTS session tracking. UI layout is additionally verified via
Textual pilot runs that check the *painted* output at several terminal
sizes.

## Sources

- `mictrack_mt710_docs/Mictrack_MT710_Commands_List.pdf` (SMS/USB/downlink
  command tables, config-info descriptions)
- `mictrack_mt710_docs/MT710_User_Manual_V1.0.pdf` (behaviour, LEDs, modes)
- https://config.mictrack.com v1.2.32 (validator rules + timings,
  hardware-validated)
- Your actual device (FW V2.1.8): ETS/RCONF verified live during
  development.
