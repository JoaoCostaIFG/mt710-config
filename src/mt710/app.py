"""MT710 Config Tool (Textual TUI).

Layout:
  · status bar       port, connection, device identity, ETS session state
  · left column      tabs: Setup / Device / Reference / Guide / Batch
  · right column     live serial console + raw command bar
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import time
from typing import Optional

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header,
                             Input, Label, ListItem, ListView, RichLog,
                             Select, Static, Switch, TabbedContent, TabPane,
                             TextArea)

from . import __version__
from .commands import (COMMANDS, Category, MODE_EXPLAIN, MODE_NAMES,
                       MODE_RANGES, REGION_PRESETS, Save,
                       build_batch_commands, validate, validate_batch)
from .protocol import REBOOT_WATCHDOG_S, MT710Session
from .reference import (GUIDE_SECTIONS, PRESETS, RCONF_FIELDS, RCONF_HIDDEN,
                        RCONF_ORDER)
from .transport import scan_ports

TS = _dt.datetime.now


# ── modal screens ─────────────────────────────────────────────────

class ConfirmModal(ModalScreen[bool]):
    """Yes/No dialog; dismisses with True (confirm) or False."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, confirm_label: str = "Confirm",
                 danger: bool = False) -> None:
        super().__init__()
        self.m_title = title
        self.m_body = body
        self.m_confirm = confirm_label
        self.danger = danger

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.m_title, classes="modal-title"),
            Static(self.m_body, classes="modal-body"),
            Horizontal(
                Button("Cancel", id="m_cancel", variant="default"),
                Button(self.m_confirm, id="m_ok",
                       variant="error" if self.danger else "primary"),
                classes="modal-buttons",
            ),
            classes="modal-card",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "m_ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class HistoryInput(Input):
    """Raw-command input with ↑/↓ history navigation."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.history: list[str] = []
        self._hist_idx: Optional[int] = None
        self._draft = ""

    def push_history(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            del self.history[:-50]
        self._hist_idx = None

    async def _on_key(self, event) -> None:  # noqa: ANN001
        if event.key == "up" and self.history:
            if self._hist_idx is None:
                self._draft = self.value
                self._hist_idx = len(self.history) - 1
            elif self._hist_idx > 0:
                self._hist_idx -= 1
            self.value = self.history[self._hist_idx]
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
            return
        if event.key == "down":
            if self._hist_idx is not None:
                self._hist_idx += 1
                if self._hist_idx >= len(self.history):
                    self._hist_idx = None
                    self.value = self._draft
                else:
                    self.value = self.history[self._hist_idx]
                self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)


# ── helpers ───────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(\d+)\s*([smh]?)", re.I)


def split_duration(value: str) -> tuple[str, str]:
    """"2m" → ("2", "m");  "10s" → ("10", "s")."""
    m = _DURATION_RE.match(value.strip())
    if not m:
        return value.strip(), ""
    return m.group(1), m.group(2).lower()


def autosendable(keyword: str, value: str) -> Optional[str]:
    """Build a command from a simple single-value setting, or None to skip."""
    v = value.strip()
    if not v:
        return None
    return f"{keyword},{v}"


# ── app ───────────────────────────────────────────────────────────

class MT710App(App[None]):

    TITLE = f"MT710 Config Tool v{__version__}"
    CSS = """
    #statusbar { height: auto; dock: top; background: $surface; padding: 0 1; }
    #statusbar Horizontal { height: 1; }
    .st { width: auto; color: $text-muted; margin-right: 2; }
    /* port/device text: flexible, crops itself instead of pushing the
       connection pill off-screen */
    #st_port { width: 1fr; overflow: hidden; margin-right: 0; }
    #st_device { width: 1fr; overflow: hidden; }
    #st_ets { width: auto; max-width: 30; margin-right: 2; }
    #st_ets.on { color: $success; }
    #st_ets.off { color: $warning; }

    /* connection status indicator */
    #st_conn { width: auto; margin-right: 2; text-style: bold; }
    #st_conn.disconnected { color: $text-muted; }
    #st_conn.connecting { color: $warning; }
    #st_conn.connected { color: $success; }
    #btn_conn { min-width: 14; height: 1; border: none; margin: 0 2 0 0;
                background: $boost; }

    #main { height: 1fr; }
    #leftcol { width: 58%; min-width: 64; border-right: solid $primary-darken-2; }
    #rightcol { width: 1fr; }

    TabbedContent { height: 100%; }
    VerticalScroll { padding: 0 1; }

    .section { border: round $panel; padding: 0 1 1 1; margin: 0 0 1 0; height: auto; }
    .section.danger { border: round $error; }
    .section-title { text-style: bold; color: $accent; margin: 0 0 1 0; }
    .row { height: auto; margin-bottom: 1; }
    .row Label { width: 24; padding: 1 1 0 0; color: $text-muted; }
    .row Input { width: 1fr; }
    .row Select { width: 1fr; }
    .row Switch { width: 6; }
    .hint { color: $text-muted; margin: 0 0 1 0; }
    .err { color: $error; }
    .ok { color: $success; }

    #modehelp { border: round $panel; padding: 1; color: $text-muted; height: auto; margin-bottom: 1; }

    #console-card { height: 1fr; border: round $primary; display: block; }
    #console-head { height: 2; background: $panel; dock: top; }
    #console-head Button { margin: 0 1 0 0; min-width: 6; height: 1; border: none; background: $boost; }
    #console-head Button.-on { background: $success; color: $text; }
    #ver_badge { width: auto; margin-left: 1; color: $text-muted; }
    #rx_counter { width: auto; margin-left: 1; color: $text-muted; }
    #rx_counter.stale { color: $warning; }
    #ets_badge { width: auto; padding: 0 1; color: $text-muted; }
    #ets_badge.on { color: $success; }
    #console { height: 1fr; }

    #rawbar { height: auto; dock: bottom; padding: 0 1; }
    /* Textual containers default to 1fr height; the input/button row must
       shrink to its content or it starves the console above it */
    #rawbar Horizontal { height: auto; }
    #rawbar HistoryInput { width: 1fr; }
    #rawbar Button { min-width: 8; }
    #rawvalid { color: $text-muted; height: 1; }
    #rawvalid.err { color: $error; }
    #rawvalid.warn { color: $warning; }
    #tab_batch Horizontal { height: auto; }
    #tab_device Horizontal { height: auto; }
    .section Horizontal { height: auto; }

    #devtable { height: 1fr; }
    #rawdump { height: 12; border: round $panel; margin-top: 1; }
    #batch_input { height: 1fr; }

    #refbody { height: 1fr; border: round $panel; padding: 1; }
    #reflist { width: 34; border-right: solid $panel; }
    #refdetail { width: 1fr; padding: 0 0 0 1; }

    .modal-card { width: 72; max-height: 80%; margin: 1 0 0 0; border: round $accent; background: $surface; padding: 1 2; }
    .modal-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .modal-body { margin-bottom: 1; }
    .modal-buttons { height: auto; align: right middle; }
    .modal-buttons Button { margin-left: 1; }
    """

    BINDINGS = [
        ("ctrl+t", "toggle_ts", "Timestamps"),
        ("ctrl+l", "clear_log", "Clear log"),
        ("ctrl+s", "save_log", "Save log"),
        ("ctrl+q", "quit", "Quit"),
        ("c", "cycle_port", "Cycle port"),
        ("f2", "connect", "Connect"),
        ("f5", "rconf", "Read config"),
        ("f9", "apply", "Apply"),
    ]

    def __init__(self, port: Optional[str] = None) -> None:
        super().__init__()
        self.initial_port = port
        self.session: Optional[MT710Session] = None
        self.show_ts = False
        self.log_lines: list[str] = []
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._ports: dict = {}
        self._active_port: Optional[str] = None
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._dbg_on: Optional[bool] = None  # None = unknown

    # ── layout ────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="statusbar"):
            with Horizontal():
                yield Button("Connect (F2)", id="btn_conn", variant="success")
                yield Static("", id="st_conn")
                yield Static("", id="st_ets")
                yield Static("", id="st_port")
            with Horizontal():
                yield Static("", id="st_device")
        with Horizontal(id="main"):
            with Vertical(id="leftcol"):
                with TabbedContent():
                    with TabPane("Setup", id="tab_setup"):
                        with VerticalScroll(id="setup_scroll"):
                            yield from self._compose_setup()
                    with TabPane("Device", id="tab_device"):
                        yield Vertical(
                            Horizontal(
                                Button("Refresh (RCONF)", id="btn_rconf",
                                       variant="primary"),
                                Static("", id="dev_note", classes="hint"),
                            ),
                            DataTable(id="devtable"),
                            Static("raw configuration", classes="hint"),
                            Static("", id="rawdump"),
                        )
                    with TabPane("Reference", id="tab_ref"):
                        with Horizontal():
                            yield ListView(id="reflist")
                            yield VerticalScroll(Static("", id="refdetail"))
                    with TabPane("Guide", id="tab_guide"):
                        with Horizontal():
                            yield ListView(id="guidelist")
                            yield VerticalScroll(Static("", id="guidedetail"))
                    with TabPane("Batch", id="tab_batch"):
                        yield Vertical(
                            Static("One command per line (# comments and "
                                   "blank lines ignored). MODE lines run "
                                   "last; ETS is added automatically.",
                                   classes="hint"),
                            TextArea("", id="batch_input"),
                            Horizontal(
                                Button("Validate", id="btn_val_batch"),
                                Button("Run…", id="btn_run_batch",
                                       variant="primary"),
                                Button("Import", id="btn_import"),
                                Button("Export", id="btn_export"),
                            ),
                            Static("", id="batch_result", classes="hint"),
                            Input(placeholder="import/export file path",
                                  id="io_path",
                                  value="config.txt"),
                        )
            with Vertical(id="rightcol"):
                with Vertical(id="console-card"):
                    with Horizontal(id="console-head"):
                        yield Button("TS", id="btn_ts")
                        yield Button("Clear", id="btn_clear")
                        yield Button("Save", id="btn_save")
                        yield Button("Pause", id="btn_pause")
                        yield Button("DBG: ?", id="btn_dbg")
                        yield Static(f"v{__version__}", id="ver_badge")
                        yield Static("", id="ets_badge")
                        yield Static("", id="rx_counter")
                    yield RichLog(id="console", markup=True, wrap=True,
                                  max_lines=4000, auto_scroll=True)
                with Vertical(id="rawbar"):
                    with Horizontal():
                        yield HistoryInput(
                            placeholder="raw command, e.g. RCONF  (↑/↓ history, Enter sends)",
                            id="raw_input")
                        yield Button("Send", id="btn_raw_send",
                                     variant="primary")
                        yield Button("Force", id="btn_raw_force",
                                     variant="warning")
                    yield Static("", id="rawvalid")
                    yield Select(
                        [(f"{label}   {cmd}", label)
                         for label, cmd in PRESETS],
                        prompt="Presets…", id="preset_select", allow_blank=True)
        yield Footer()

    def _compose_setup(self):
        yield from self._section_network()
        yield from self._section_mode()
        yield from self._section_options()
        yield Vertical(
            Static("Apply", classes="section-title"),
            Static("Sends the checked Setup sections top-to-bottom after "
                   "an ETS wake-up. MODE (if included) runs last and "
                   "saves + reboots; otherwise REBOOT is appended.",
                   classes="hint"),
            Button("Review & Apply  (F9)", id="btn_apply", variant="success"),
            classes="section",
        )
        yield from self._section_danger()

    def _section_network(self):
        yield Vertical(
            Static("Network", classes="section-title"),
            Checkbox("include in Apply", True, id="inc_net"),
            Horizontal(Label("APN"), Input(placeholder="e.g. cmnbiot",
                                           id="f_apn"), classes="row"),
            Horizontal(Label("  user / pass"),
                       Input(placeholder="APN username (mostly empty)",
                             id="f_apn_user"),
                       Input(placeholder="APN password", id="f_apn_pass"),
                       classes="row"),
            Horizontal(Label("Server"), Input(
                placeholder="domain or IP (no http://)", id="f_srv"),
                classes="row"),
            Horizontal(Label("Port"), Input(placeholder="e.g. 5030 / 7700",
                                            id="f_port"),
                Switch(value=True, id="f_tcp"), classes="row"),
            Static("switch = TCP (on) / UDP (off)", classes="hint"),
            Horizontal(Label("Region"),
                       Select([(v["label"], k)
                               for k, v in REGION_PRESETS.items()]
                              + [("Custom…", "custom")],
                              prompt="Region", id="f_region",
                              allow_blank=True), classes="row"),
            Horizontal(Label("NWM"), Input(placeholder="auto from region "
                                       "e.g. 0,0,2", id="f_nwm"),
                       classes="row"),
            Horizontal(Label("BAND"), Input(placeholder="auto from region "
                                       "e.g. 0,0,f", id="f_band"),
                       classes="row"),
            classes="section", id="sec_net",
        )

    def _section_mode(self):
        modes = [("(keep current)", "keep")] + [
            (f"MODE,{n}: {MODE_NAMES[n]}", n)
            for n in sorted(MODE_NAMES, key=int)]
        yield Vertical(
            Static("Working Mode", classes="section-title"),
            Checkbox("include in Apply (MODE saves & reboots!)", False,
                     id="inc_mode"),
            Horizontal(Label("Mode"), Select(modes, prompt="mode",
                                             id="f_mode",
                                             allow_blank=True),
                       classes="row"),
            Static("", id="modehelp"),
            Horizontal(Label("T / T1"), Input(placeholder="interval",
                                              id="f_mt1"), classes="row"),
            Horizontal(Label("T2 / time"), Input(placeholder="interval or "
                                                 "HH:MM", id="f_mt2"),
                       classes="row"),
            Horizontal(Label("X"), Input(placeholder="0/1", id="f_mx"),
                       classes="row"),
            Horizontal(Label("Y"), Input(placeholder="0/1", id="f_my"),
                       classes="row"),
            classes="section", id="sec_mode",
        )

    def _section_options(self):
        yield Vertical(
            Static("Device Options", classes="section-title"),
            Checkbox("include in Apply", False, id="inc_opt"),
            Horizontal(Label("Protocol 800"),
                       Input(placeholder="TCP or UDP", id="f_proto"),
                       classes="row"),
            Horizontal(Label("GPS search DUR"),
                       Input(placeholder="1-10 min", id="f_dur"),
                       classes="row"),
            Horizontal(Label("Keep-alive RWT"),
                       Input(placeholder="60-600 s", id="f_rwt"),
                       classes="row"),
            Horizontal(Label("Heartbeat HBC"),
                       Input(placeholder="5-60 min (modes 2/5)",
                             id="f_hbc"), classes="row"),
            Horizontal(Label("Last known LEP"),
                       Select([("0: report invalid", "0"),
                               ("1: report last position", "1")],
                              prompt="LEP", id="f_lep", allow_blank=True),
                       classes="row"),
            Horizontal(Label("LBS"),
                       Input(placeholder="0-3", id="f_lbs"), classes="row"),
            Horizontal(Label("AGPS"),
                       Select([("0: off", "0"), ("1: on", "1")],
                               prompt="AGPS", id="f_agps",
                               allow_blank=True), classes="row"),
             Horizontal(Label("XTRA"),
                        Select([("0: off", "0"), ("1: on", "1")],
                              prompt="XTRA", id="f_xtra",
                              allow_blank=True), classes="row"),
            Horizontal(Label("Priority PRIOR"),
                       Select([("0: GPS first", "0"),
                               ("1: WiFi first", "1")],
                              prompt="PRIOR", id="f_prior",
                              allow_blank=True), classes="row"),
            Horizontal(Label("Power btn MSW"),
                       Select([("0: disabled", "0"), ("1: enabled", "1")],
                              prompt="MSW", id="f_msw", allow_blank=True),
                       classes="row"),
            Horizontal(Label("Timezone 896"),
                       Input(placeholder="tz×60, e.g. +0 → 0, +1 → +60",
                             id="f_tz"), classes="row"),
            Horizontal(Label("GSEN"),
                       Input(placeholder="wake mg (1-125)", id="f_g1"),
                       Input(placeholder="vib mg (10-2000)", id="f_g2"),
                       Input(placeholder="count (1-32)", id="f_g3"),
                       Input(placeholder="time min (1-10)", id="f_g4"),
                       classes="row"),
            classes="section", id="sec_opt",
        )

    def _section_danger(self):
        yield Vertical(
            Static("Danger Zone", classes="section-title"),
            Static("QTS saves without reboot · REBOOT saves & restarts · "
                   "RESET wipes everything (no reboot).", classes="hint"),
            Horizontal(
                Button("Save & Exit (QTS)", id="btn_qts",
                       variant="default"),
                Button("Save & Reboot", id="btn_reboot",
                       variant="warning"),
                Button("FACTORY RESET", id="btn_reset", variant="error"),
            ),
            classes="section danger",
        )

    # ── lifecycle ─────────────────────────────────────────────────

    def on_mount(self) -> None:
        table = self.query_one("#devtable", DataTable)
        table.add_columns("Setting", "Value", "Meaning")
        self._populate_reference()
        self._populate_guide()
        self._render_mode_help()
        self._refresh_ports(initial=True)
        self._set_ets(False)
        self._set_conn("disconnected")
        self._update_rx_counter()
        self._refresh_dbg_button()
        # keep the "last RX" age ticker fresh even when the device is quiet
        self.set_interval(1.0, self._update_rx_counter)
        self.query_one("#raw_input", HistoryInput).focus()

    def _refresh_ports(self, initial: bool = False) -> None:
        ports = scan_ports()
        st = self.query_one("#st_port", Static)
        if not ports:
            st.update("[b]port:[/b] none found; plug the USB config cable")
            return
        self._ports = {p.device: p for p in ports}
        default = (self.initial_port if self.initial_port in self._ports
                   else ports[0].device)
        p = self._ports[default]
        self._active_port = default
        self._render_port(p)

    def _render_port(self, p=None) -> None:
        if p is None:
            p = self._ports[self._active_port]
        extra = f" (+{len(self._ports) - 1})" if len(self._ports) > 1 else ""
        self.query_one("#st_port", Static).update(
            f"[b]port:[/b] {self._active_port} · "
            f"{escape(p.chip if p.chip != '?' else p.description)}"
            f"{extra} [dim]· 'c' cycles[/]")

    def action_cycle_port(self) -> None:
        if not getattr(self, "_ports", None):
            self.notify("No ports available", severity="warning")
            return
        keys = list(self._ports)
        idx = (keys.index(self._active_port) + 1) % len(keys) \
            if self._active_port in keys else 0
        self._active_port = keys[idx]
        self._render_port()

    # ── session plumbing ──────────────────────────────────────────

    def _session_event(self, event: str, data: dict) -> None:
        """Session callback; may be invoked from the reader thread, a
        worker thread, OR the UI thread itself (e.g. raw-bar sends call
        send_raw() directly). call_from_thread() is invalid on the app's
        own thread, so dispatch accordingly."""
        try:
            if threading.get_ident() == getattr(self, "_thread_id", None):
                self._handle_session_event(event, data)
            else:
                self.call_from_thread(self._handle_session_event, event, data)
        except Exception as exc:  # noqa: BLE001
            # surface instead of dying silently; unless the app is closing
            try:
                if self.is_running:
                    self.call_from_thread(
                        self._console_warn, f"event handler error: {exc}")
            except Exception:
                pass

    def _handle_session_event(self, event: str, data: dict) -> None:
        if event == "line":
            self._console_line(data["line"], data["kind"])
        elif event == "sent":
            self._console_sent(data["command"])
        elif event == "ets":
            self._set_ets(data["active"])
        elif event == "config":
            self._on_config(data["dump"])
        elif event == "imei":
            st = self.query_one("#st_device", Static)
            imei = data["imei"]
            st.update(f"[b]IMEI:[/b] {escape(imei)}")
        elif event == "disconnected":
            self._set_conn("disconnected")
            self.notify("Device disconnected", severity="error")

    # ── console ───────────────────────────────────────────────────

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        return f"{n/1000:.1f}kB" if n >= 1000 else str(n)

    def _update_rx_counter(self) -> None:
        st = self.query_one("#rx_counter", Static)
        st.update(f"↓{self._fmt_bytes(self._rx_bytes)} "
                  f"↑{self._fmt_bytes(self._tx_bytes)} · last RX "
                  f"{self._rx_age_text()}")
        age = self._rx_age_s()
        st.set_class(age is not None and age > 30, "stale")

    def _rx_age_s(self) -> Optional[float]:
        session = self.session
        if session is None or not session.transport.is_open:
            return None
        rx_at = session.transport.last_rx_at
        if rx_at == 0.0:
            return None
        return time.monotonic() - rx_at

    def _rx_age_text(self) -> str:
        age = self._rx_age_s()
        if age is None:
            return "-"
        if age < 60:
            return f"{age:.0f}s ago"
        return f"{age/60:.0f}m ago"

    def _ts(self) -> str:
        return (_dt.datetime.now().strftime("%H:%M:%S") + " "
                if self.show_ts else "")

    def _console_line(self, line: str, kind: str) -> None:
        log = self.query_one("#console", RichLog)
        stamp = self._ts()
        plain = line
        if kind == "trace":
            log.write(f"{stamp}[dim]{escape(line)}[/]")
        else:
            log.write(f"{stamp}{escape(line)}")
        self.log_lines.append(plain)
        self._rx_bytes += len(line) + 2  # +2 for the \r\n separator
        self._update_rx_counter()
        # track debug state from reply echoes: <CFG>:DBG,ON / DBG,OFF
        body = line[6:] if line.startswith("<CFG>:") else line
        if body.startswith("DBG,"):
            self._dbg_on = body.strip() == "DBG,ON"
            self._refresh_dbg_button()

    def _console_sent(self, cmd: str) -> None:
        log = self.query_one("#console", RichLog)
        log.write(f"{self._ts()}[bold cyan]→ {escape(cmd)}[/]")
        self.log_lines.append(f"→ {cmd}")
        self._tx_bytes += len(cmd)
        self._update_rx_counter()

    def _console_warn(self, text: str) -> None:
        log = self.query_one("#console", RichLog)
        log.write(f"{self._ts()}[bold yellow]{escape(text)}[/]")
        self.log_lines.append(text)

    def _console_ok(self, text: str) -> None:
        log = self.query_one("#console", RichLog)
        log.write(f"{self._ts()}[bold green]{escape(text)}[/]")
        self.log_lines.append(text)

    def _refresh_dbg_button(self) -> None:
        btn = self.query_one("#btn_dbg", Button)
        if self._dbg_on is None:
            btn.label = "DBG: ?"
            btn.remove_class("-on")
        else:
            btn.label = "DBG: on" if self._dbg_on else "DBG: off"
            btn.set_class(self._dbg_on, "-on")

    def _toggle_dbg(self) -> None:
        if not self.session:
            self.notify("Not connected (F2)", severity="warning")
            return
        if self._dbg_on is None:
            self.notify("DBG state unknown; press F5 to read config first",
                        severity="warning")
            return
        target = 0 if self._dbg_on else 1
        if target == 1:
            self.notify("Enabling debug output (increases power use; "
                        "toggle off when done monitoring")
        self.run_worker(lambda: self._dbg_worker(target), thread=True,
                        exclusive=True)

    def _dbg_worker(self, target: int) -> None:
        assert self.session
        # ETS wake (device may have slept) → set DBG → QTS saves without
        # rebooting (same sequence as the official web tool)
        if not self.session.ensure_ts():
            self.call_from_thread(self._console_warn,
                                  "could not wake device (ETS); DBG not set")
            return
        cmds = [f"DBG,{target}", "QTS"]
        results = self.session.run_batch(cmds)
        for r in results:
            if r.ok:
                self.call_from_thread(self._console_ok,
                                      f"{r.command} → {r.ack or 'ok'}")
            else:
                self.call_from_thread(self._console_warn,
                                      f"{r.command} → NO ACK")
        if target == 1:
            self.call_from_thread(
                self._console_ok,
                "debug output ON; <Trace> lines will stream in the console "
                "(GPS/voltage/network state). Battery drains faster; toggle "
                "off when done.")

    def action_toggle_ts(self) -> None:
        self.show_ts = not self.show_ts
        self.notify(f"timestamps {'on' if self.show_ts else 'off'}")

    def action_clear_log(self) -> None:
        self.query_one("#console", RichLog).clear()
        self.log_lines.clear()

    def action_save_log(self) -> None:
        self._save_log()

    def _save_log(self) -> None:
        os.makedirs("logs", exist_ok=True)
        path = os.path.join(
            "logs", f"session-{_dt.datetime.now():%Y%m%d-%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.log_lines))
        self.notify(f"Log saved to {path}")

    # ── status widgets ────────────────────────────────────────────

    def _set_ets(self, active: bool) -> None:
        st = self.query_one("#st_ets", Static)
        badge = self.query_one("#ets_badge", Static)
        if active:
            st.update("[b]● ETS active[/b]")
            st.add_class("on")
            st.remove_class("off")
            badge.update("● ETS")
            badge.add_class("on")
        else:
            st.update("○ ETS none")
            st.add_class("off")
            st.remove_class("on")
            badge.update("○ no session")
            badge.remove_class("on")

    _CONN_STATES = {
        "disconnected": ("◌ DISCONNECTED", "disconnected"),
        "connecting": ("… CONNECTING", "connecting"),
        "connected": ("● CONNECTED", "connected"),
    }

    def _set_conn(self, state: str) -> None:
        text, css_class = self._CONN_STATES.get(
            state, (state.upper(), "disconnected"))
        st = self.query_one("#st_conn", Static)
        st.update(text)
        st.remove_class("disconnected", "connecting", "connected")
        st.add_class(css_class)
        btn = self.query_one("#btn_conn", Button)
        btn.label = "Connect (F2)" if state != "connected" \
            else "Disconnect (F2)"

    @staticmethod
    def _friendly_value(key: str, value: str) -> str:
        """Humanise MT710-specific fields for the Device grid."""
        if key == "GEO":
            m = re.match(r"Lat=([-\d.]+),lng=([-\d.]+),R=(\d+)", value)
            if m:
                lat, lng, r = m.groups()
                if float(lat) == 0.0 and float(lng) == 0.0 and int(r) == 0:
                    return "no home geofence set (see MODE,8 / GEO)"
                return f"{lat}, {lng} · radius {r} m"
        if key == "AP":
            parts = value.split(",")
            macs = [p for p in parts[2:6] if p]
            if macs:
                return f"{len(macs)} home MAC(s): {' '.join(macs)}"
            return "no home MACs set (see MODE,8 / AP)"
        return value

    def _on_config(self, dump) -> None:
        st = self.query_one("#st_device", Static)
        st.update(f"[b]device:[/b] {escape(dump.model)}  "
                  f"[b]IMEI:[/b] {escape(dump.imei)}  "
                  f"[b]FW:[/b] {escape(dump.firmware)}")
        table = self.query_one("#devtable", DataTable)
        table.clear()
        shown: list[tuple[str, str]] = []
        seen = set()
        for key in RCONF_ORDER:
            value = dump.get(key)
            if value is None:
                continue
            label, help_ = RCONF_FIELDS.get(key, (key, ""))
            shown.append((f"{label} ({key})", self._friendly_value(key, value),
                          help_))
            seen.add(key)
        for key, value in dump.fields:
            if key not in seen and key not in RCONF_HIDDEN:
                label, help_ = RCONF_FIELDS.get(key, (key, ""))
                shown.append((f"{label} ({key})", self._friendly_value(key, value),
                              help_))
        for row in shown:
            table.add_row(*(escape(c) for c in row))
        self.query_one("#rawdump", Static).update(
            escape("\n".join(f"{k}:{v}" for k, v in dump.fields)))
        dbg = dump.get("DBG")
        if dbg is not None:
            self._dbg_on = dbg.strip().upper() == "ON"
            self._refresh_dbg_button()
        self._populate_forms(dump)

    # ── form population ───────────────────────────────────────────

    def _populate_forms(self, dump) -> None:
        def set_input(sid: str, value: str) -> None:
            self.query_one(sid, Input).value = value

        apn = dump.get("APN", "")
        if apn:
            parts = apn.split(",")
            set_input("#f_apn", parts[0])
            set_input("#f_apn_user", parts[1] if len(parts) > 1 else "")
            set_input("#f_apn_pass", parts[2] if len(parts) > 2 else "")
        srv = dump.get("SRV", "")
        m = re.match(r"^(?:DM|IP),([^,]*),(\d+)$", srv or "")
        if m:
            set_input("#f_srv", m.group(1))
            set_input("#f_port", m.group(2))
        proto = dump.get("NET", "TCP")
        self.query_one("#f_tcp", Switch).value = proto.upper() != "UDP"
        nwm = dump.get("NWM")
        if nwm:
            set_input("#f_nwm", nwm)
        mode = dump.get("MODE")
        if mode:
            mn = mode.split(",")[0]
            try:
                self.query_one("#f_mode", Select).value = mn
                params = mode.split(",")[1:]
                if params:
                    v, _u = split_duration(params[0])
                    set_input("#f_mt1", v)
                if len(params) > 1:
                    set_input("#f_mt2", params[1])
                if len(params) > 2:
                    set_input("#f_mx", params[2].rstrip("s"))
                if len(params) > 3:
                    set_input("#f_my", params[3])
            except Exception:
                pass
        for key, sid in [("DUR", "#f_dur"), ("RWT", "#f_rwt"),
                         ("HBC", "#f_hbc")]:
            raw = dump.get(key)
            if raw:
                v, _u = split_duration(raw)
                set_input(sid, v)
        lbs = dump.get("LBS")
        if lbs is not None:
            set_input("#f_lbs", lbs)
        for key, sid, zero_on in [("LEP", "#f_lep", "OFF"),
                                  ("AGPS", "#f_agps", "OFF"),
                                  ("XTRA", "#f_xtra", "OFF"),
                                  ("MSW", "#f_msw", "OFF")]:
            raw = dump.get(key)
            if raw is not None:
                self.query_one(sid, Select).value = "0" if raw == zero_on \
                    else "1"
        prior = dump.get("PRIOR")
        if prior:
            self.query_one("#f_prior", Select).value = \
                "1" if "WIFI" in prior.upper() else "0"
        gsen = dump.get("GSEN")
        if gsen:
            parts = gsen.split(",")
            for i, sid in enumerate(("#f_g1", "#f_g2", "#f_g3", "#f_g4")):
                if i < len(parts):
                    set_input(sid, parts[i])
        tz = dump.get("TZ")
        if tz:
            set_input("#f_tz", tz)

    # ── reference / guide tabs ────────────────────────────────────

    def _populate_reference(self) -> None:
        lv = self.query_one("#reflist", ListView)
        items = []
        for cat in Category:
            header = ListItem(Label(f"[b]{cat.value}[/b]"))
            header.payload = None
            items.append(header)
            for kw in sorted(COMMANDS):
                cmd = COMMANDS[kw]
                if cmd.category is cat:
                    title = f"{kw}: {cmd.title}"
                    if cmd.mt710_only:
                        title += "  (MT710)"
                    if cmd.ignored_on_mt710:
                        title += "  (ignored!)"
                    li = ListItem(Label(title))
                    li.payload = kw
                    items.append(li)
        lv.clear()
        lv.extend(items)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if getattr(item, "payload", None) is None:
            return
        if event.list_view.id == "reflist":
            self._show_reference(item.payload)
        else:
            self._show_guide(item.payload)

    def _show_reference(self, kw: str) -> None:
        cmd = COMMANDS[kw]
        lines = [f"[b]{kw}[/b]: {cmd.title}",
                f"[dim]category: {cmd.category.value} · "
                f"save: {self._save_label(cmd)}[/]", ""]
        lines.append(f"[b]format[/b]:  {escape(cmd.format)}")
        lines.append(f"[b]example[/b]: {escape(cmd.example)}")
        lines.append(f"[b]reply[/b]:    {escape(cmd.reply)}")
        lines.append(f"[b]default[/b]:  {escape(cmd.default)}")
        lines.append("")
        lines.append(cmd.description)
        if cmd.args:
            lines.append("")
            lines.append("[b]parameters[/b]")
            for a in cmd.args:
                rng = ""
                if a.minimum is not None:
                    rng = f" ({a.minimum:g}–{a.maximum:g}" \
                          f"{f' {a.unit}' if a.unit else ''})"
                elif a.unit:
                    rng = f" ({a.unit})"
                opt = " · optional" if a.optional else ""
                emp = " · may be empty" if a.allow_empty else ""
                lines.append(f"· [b]{a.name}[/b]{rng}{opt}{emp}")
                if a.help:
                    lines.append(f"   {a.help}")
        if cmd.note:
            lines.append("")
            lines.append(f"[yellow]⚠ {escape(cmd.note)}[/]")
        self.query_one("#refdetail", Static).update("\n".join(lines))

    @staticmethod
    def _save_label(cmd) -> str:
        return {
            Save.EXPLICIT: "needs QTS/REBOOT to persist",
            Save.SAVES_REBOOT: "saves & reboots",
            Save.SAVES: "saves (no reboot)",
            Save.QUERY: "query only",
        }[cmd.save]

    def _populate_guide(self) -> None:
        lv = self.query_one("#guidelist", ListView)
        lv.clear()
        for title, _body in GUIDE_SECTIONS:
            li = ListItem(Label(title))
            li.payload = title
            lv.append(li)

    def _show_guide(self, title: str) -> None:
        for t, body in GUIDE_SECTIONS:
            if t == title:
                self.query_one("#guidedetail", Static).update(
                    f"[b]{t}[/b]\n\n{escape(body)}")
                return

    def _render_mode_help(self) -> None:
        sel = self.query_one("#f_mode", Select)
        mode = sel.value
        if mode in (None, "keep", "") or str(mode) not in MODE_EXPLAIN:
            self.query_one("#modehelp", Static).update(
                "Pick a mode to see its parameters and power profile.")
            return
        n = str(mode)
        text = f"MODE,{n}: {MODE_NAMES[n]}\n{MODE_EXPLAIN[n]}"
        self.query_one("#modehelp", Static).update(escape(text))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "f_mode":
            self._render_mode_help()
        if event.select.id == "f_region" and event.value == "custom":
            self.notify("Enter NWM/BAND manually below", severity="information")
        if event.select.id == "preset_select":
            label = event.value
            if label:
                for lbl, cmd in PRESETS:
                    if lbl == label:
                        inp = self.query_one("#raw_input", HistoryInput)
                        inp.value = cmd
                        self._validate_raw(cmd)
                        inp.focus()
                        break
                event.select.clear()  # back to prompt for next pick

    # ── raw bar ───────────────────────────────────────────────────

    def _validate_raw(self, value: str) -> tuple[bool, Optional[str]]:
        label = self.query_one("#rawvalid", Static)
        if not value.strip():
            label.update("")
            label.set_classes("")
            return True, None
        ok, reason = validate(value)
        if ok:
            kw = value.split(",")[0]
            cmd = COMMANDS.get(kw)
            extra = ""
            if cmd:
                if cmd.ignored_on_mt710:
                    extra = " (ignored by MT710 firmware)"
                    label.set_classes("warn")
                else:
                    label.set_classes("ok")
            label.update(f"✓ {kw}: {cmd.title if cmd else ''}{extra}")
            return True, None
        label.update(f"✗ {reason}")
        label.set_classes("err")
        return False, reason

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "raw_input":
            # auto-uppercase the keyword; the firmware ignores lowercase
            v = event.input.value
            kw, sep, rest = v.partition(",")
            if kw and kw != kw.upper():
                event.input.value = kw.upper() + (sep + rest if sep else "")
                event.input.cursor_position = len(event.input.value)
            self._validate_raw(event.input.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "raw_input":
            self._send_raw(force=False)

    def _send_raw(self, force: bool) -> None:
        inp = self.query_one("#raw_input", HistoryInput)
        cmd = inp.value.strip()
        if not cmd:
            return
        ok, reason = self._validate_raw(cmd)
        if not ok and not force:
            self._console_warn(f"✗ not sent: {cmd}; {reason}")
            self.notify(f"Not sent; {reason}", severity="warning")
            return
        if cmd == "RESET":
            def _cb(confirmed: bool | None) -> None:
                if confirmed:
                    self._dispatch_raw(cmd)
            self.push_screen(ConfirmModal(
                "Factory reset",
                "This erases ALL settings and restores factory defaults.\n"
                "It cannot be undone. The device stays connected.",
                "RESET device", danger=True), _cb)
            return
        self._dispatch_raw(cmd)

    def _dispatch_raw(self, cmd: str) -> None:
        if not self.session:
            self._console_warn(
                f"✗ not sent: {cmd}; NOT CONNECTED (press F2 to connect; "
                "the command was kept in the input box)")
            self.notify("Not connected (F2)", severity="error")
            return
        inp = self.query_one("#raw_input", HistoryInput)
        inp.push_history(cmd)
        inp.value = ""
        self.query_one("#rawvalid", Static).update("")
        if not self.session.ets_active:
            self._console_warn(
                "no ETS session; the device may ignore this command "
                "(pick 'START session' from the presets, or F2/F5 to wake "
                "it first)")
        try:
            self.session.send_raw(cmd)
        except Exception as exc:  # noqa: BLE001
            self._console_warn(f"✗ send failed: {cmd}; {exc}")

    # ── connect / disconnect ──────────────────────────────────────

    def action_connect(self) -> None:
        if self.session:
            self._disconnect()
            return
        port = getattr(self, "_active_port", None) or self.initial_port
        if not port:
            self.notify("No serial port found", severity="error")
            return
        self._set_conn("connecting")
        self.run_worker(self._connect_worker, thread=True,
                        exclusive=True, description="connect")

    def _disconnect(self) -> None:
        self._watchdog_stop.set()
        if self.session:
            self.session.close()
            self.session = None
        self._set_conn("disconnected")
        self._set_ets(False)

    def _connect_worker(self) -> None:
        port = self._active_port
        session = MT710Session(port, on_event=self._session_event)
        try:
            session.open()
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._connect_failed, str(exc))
            return
        self.session = session
        self.call_from_thread(self._set_conn, "connected")
        self.call_from_thread(self._console_ok,
                              f"connected to {port} @ 921600 8N1; "
                              "waking device (ETS)…")
        ok = session.ets_handshake()
        if not ok:
            self.call_from_thread(
                self._console_warn,
                "no ETS reply; device may be asleep or off "
                "(blue LED should be on). You can still watch the console; "
                "press F2 twice to retry.")
            return
        self.call_from_thread(self._console_ok, "ETS session active")
        dump = session.read_config()
        if dump:
            self.call_from_thread(self._console_ok,
                                  f"RCONF ok; {dump.model} "
                                  f"FW {dump.firmware}")
        else:
            self.call_from_thread(self._console_warn,
                                  "RCONF gave no data; try F5")

    def _connect_failed(self, reason: str) -> None:
        self._set_conn("disconnected")
        self.notify(f"Connect failed: {reason}", severity="error")
        self._console_warn(f"connect failed: {reason}")

    def action_rconf(self) -> None:
        if not self.session:
            self.notify("Not connected (F2)", severity="warning")
            return
        self.run_worker(self._rconf_worker, thread=True, exclusive=True)

    def _rconf_worker(self) -> None:
        assert self.session
        dump = self.session.read_config()
        if not dump:
            self.call_from_thread(self._console_warn,
                                  "RCONF gave no data; device asleep? "
                                  "ETS retry happens automatically.")

    # ── apply flow ────────────────────────────────────────────────

    def _build_apply_commands(self) -> tuple[list[str], list[str]]:
        """Returns (commands, problems)."""
        cmds: list[str] = []
        problems: list[str] = []

        if self.query_one("#inc_net", Checkbox).value:
            apn = self.query_one("#f_apn", Input).value.strip()
            user = self.query_one("#f_apn_user", Input).value.strip()
            pwd = self.query_one("#f_apn_pass", Input).value.strip()
            if apn:
                cmds.append(f"803,{apn},{user},{pwd}")
            else:
                problems.append("APN empty; network section incomplete")
            srv = self.query_one("#f_srv", Input).value.strip()
            port = self.query_one("#f_port", Input).value.strip()
            if srv and port:
                cmds.append(f"804,{srv},{port}")
            else:
                problems.append("server/port incomplete; 804 skipped")
            proto = self.query_one("#f_proto", Input).value.strip().upper()
            if proto in ("TCP", "UDP"):
                cmds.append(f"800,{proto}")
            region = self.query_one("#f_region", Select).value
            nwm = self.query_one("#f_nwm", Input).value.strip()
            band = self.query_one("#f_band", Input).value.strip()
            if region == "custom":
                if nwm:
                    cmds.append(f"NWM,{nwm}")
                if band:
                    cmds.append(f"BAND,{band}")
            elif region and region != "keep":
                preset = REGION_PRESETS.get(region)
                if preset:
                    cmds.append(preset["nwm"])
                    cmds.append(preset["band"])

        if self.query_one("#inc_opt", Checkbox).value:
            for kw, sid in (("DUR", "#f_dur"), ("RWT", "#f_rwt"),
                            ("HBC", "#f_hbc"), ("LBS", "#f_lbs")):
                c = autosendable(kw, self.query_one(sid, Input).value)
                if c and validate(c)[0]:
                    cmds.append(c)
            for kw, sid in (("LEP", "#f_lep"), ("AGPS", "#f_agps"),
                            ("XTRA", "#f_xtra"), ("PRIOR", "#f_prior"),
                            ("MSW", "#f_msw")):
                v = self.query_one(sid, Select).value
                if v not in (None, ""):
                    cmds.append(f"{kw},{v}")
            tz = self.query_one("#f_tz", Input).value.strip()
            if tz:
                c = f"896,{tz}"
                if validate(c)[0]:
                    cmds.append(c)
            g = [self.query_one(s, Input).value.strip()
                 for s in ("#f_g1", "#f_g2", "#f_g3", "#f_g4")]
            if all(g):
                c = f"GSEN,{','.join(g)}"
                if validate(c)[0]:
                    cmds.append(c)

        mode = self.query_one("#f_mode", Select).value
        if self.query_one("#inc_mode", Checkbox).value and \
                mode not in (None, "", "keep"):
            n = str(mode)
            t1 = self.query_one("#f_mt1", Input).value.strip()
            t2 = self.query_one("#f_mt2", Input).value.strip()
            x = self.query_one("#f_mx", Input).value.strip()
            y = self.query_one("#f_my", Input).value.strip()
            if n == "6":
                cmds.append("MODE,6")
            elif n == "10":
                c = f"MODE,10,{t1},{t2}"
                if validate(c)[0]:
                    cmds.append(c)
                else:
                    problems.append(f"invalid MODE,10 parameters ({c})")
            elif n in ("2", "5"):
                c = f"MODE,{n},{t1},{x or '0'},{y or '0'}"
                if validate(c)[0]:
                    cmds.append(c)
                else:
                    problems.append(f"invalid MODE,{n} parameters ({c})")
            else:
                count = {"0": 2, "1": 1, "3": 1, "4": 1, "7": 2,
                         "8": 1, "9": 2}[n]
                params = [t1, t2][:count]
                c = f"MODE,{n},{','.join(params)}"
                if validate(c)[0]:
                    cmds.append(c)
                else:
                    problems.append(f"invalid MODE parameters ({c})")
        return cmds, problems

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn_ts":
            self.action_toggle_ts()
        elif bid == "btn_clear":
            self.action_clear_log()
        elif bid == "btn_save":
            self._save_log()
        elif bid == "btn_conn":
            self.action_connect()
        elif bid == "btn_pause":
            log = self.query_one("#console", RichLog)
            log.auto_scroll = not log.auto_scroll
            event.button.label = "Pause" if log.auto_scroll else "Follow"
        elif bid == "btn_dbg":
            self._toggle_dbg()
        elif bid == "btn_raw_send":
            self._send_raw(force=False)
        elif bid == "btn_raw_force":
            self._send_raw(force=True)
        elif bid == "btn_rconf":
            self.action_rconf()
        elif bid == "btn_reboot":
            self._confirm_then_send("Reboot",
                                    "Save settings and reboot the device?",
                                    ["REBOOT"])
        elif bid == "btn_qts":
            self._confirm_then_send("Save & exit",
                                    "Save settings without rebooting?",
                                    ["QTS"])
        elif bid == "btn_reset":
            self._confirm_then_send(
                "Factory reset",
                "Erase ALL settings? This cannot be undone.",
                ["RESET"], danger=True)
        elif bid == "btn_val_batch":
            self._validate_batch_ui()
        elif bid == "btn_run_batch":
            self._run_batch_ui()
        elif bid == "btn_import":
            self._import_file()
        elif bid == "btn_export":
            self._export_file()
        elif bid == "btn_apply":
            self._apply_ui()

    def _confirm_then_send(self, title: str, body: str, cmds: list[str],
                           danger: bool = False) -> None:
        if not self.session:
            self.notify("Not connected (F2)", severity="warning")
            return

        def _cb(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(lambda: self._batch_worker(list(cmds)),
                                thread=True, exclusive=True)
        self.push_screen(ConfirmModal(title, body, title, danger=danger),
                         _cb)

    def _batch_worker(self, cmds: list[str]) -> None:
        assert self.session
        reboot = cmds and cmds[-1].split(",")[0] in ("MODE", "REBOOT")
        results = self.session.run_batch(cmds, reboot_expected=reboot)
        for r in results:
            if r.ok:
                self.call_from_thread(self._console_ok,
                                      f"{r.command} → {r.ack or 'ok'}")
            else:
                self.call_from_thread(
                    self._console_warn,
                    f"{r.command} → NO ACK (timeout or unknown command); "
                    "batch stopped")
        failed = [r for r in results if not r.ok]
        if failed:
            self.call_from_thread(self.notify,
                                  "Batch finished with failures",
                                  severity="warning")
        elif reboot:
            self.call_from_thread(self._console_ok,
                                  "device is rebooting; boot log below; "
                                  "watchdog active")
            self._start_watchdog()
        else:
            self.call_from_thread(self._console_ok,
                                  "batch complete; remember settings only "
                                  "persist after MODE/REBOOT/QTS")

    def _start_watchdog(self) -> None:
        self._watchdog_stop.set()
        self._watchdog_stop = threading.Event()
        stop = self._watchdog_stop

        def _watch() -> None:
            time.sleep(5)
            while not stop.is_set():
                time.sleep(1)
                session = self.session
                if session is None:
                    return
                if time.monotonic() - session.transport.last_rx_at \
                        > REBOOT_WATCHDOG_S:
                    self.call_from_thread(
                        self._console_warn,
                        f"no output for {REBOOT_WATCHDOG_S:.0f}s; the "
                        "device may have gone to sleep (press its power "
                        "button once) or finished booting silently")
                    return
        self._watchdog_thread = threading.Thread(target=_watch, daemon=True)
        self._watchdog_thread.start()

    # ── batch tab ─────────────────────────────────────────────────

    def _batch_lines(self) -> list[str]:
        return self.query_one("#batch_input", TextArea).text.splitlines()

    def _validate_batch_ui(self) -> None:
        results = validate_batch(self._batch_lines())
        label = self.query_one("#batch_result", Static)
        if not results:
            label.update("nothing to validate")
            return
        bad = [(line, reason) for (line, ok, reason) in results if not ok]
        if bad:
            body = "\n".join(f"✗ {l}: {r}" for l, r in bad)
            label.update(f"[b]invalid lines[/b]\n{escape(body)}")
            label.set_classes("err")
        else:
            cmds, reboot = build_batch_commands([l for l, _, _ in results])
            label.update(
                f"all {len(results)} line(s) valid · runner will send: "
                + " ".join(escape(c) for c in cmds))
            label.set_classes("ok")

    def _run_batch_ui(self) -> None:
        if not self.session:
            self.notify("Not connected (F2)", severity="warning")
            return
        results = validate_batch(self._batch_lines())
        bad = [(line, reason) for (line, ok, reason) in results if not ok]
        if bad:
            self.notify(f"{len(bad)} invalid line(s); validate first",
                        severity="error")
            return
        if not results:
            self.notify("batch is empty", severity="warning")
            return
        cmds, reboot = build_batch_commands([l for l, _, _ in results])

        def _cb(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(lambda: self._batch_worker(list(cmds)),
                                thread=True, exclusive=True)
        self.push_screen(ConfirmModal(
            "Run batch",
            "Will send (after ETS wake-up):\n\n"
            + "\n".join(cmds), "Run"), _cb)

    def _import_file(self) -> None:
        path = self.query_one("#io_path", Input).value.strip()
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            self.notify(f"import failed: {exc}", severity="error")
            return
        self.query_one("#batch_input", TextArea).text = text
        self._validate_batch_ui()
        self.notify(f"imported {path}")

    def _export_file(self) -> None:
        path = self.query_one("#io_path", Input).value.strip() or \
            "config.txt"
        lines = self._batch_lines()
        body = ["# MT710 Config Tool: command list",
                f"# Generated {_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
                "# Sent top-to-bottom; MODE is applied last (auto-saves "
                "& reboots)."]
        body += [l for l in lines if l.strip() and not l.startswith("#")]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\r\n".join(body) + "\r\n")
        except OSError as exc:
            self.notify(f"export failed: {exc}", severity="error")
            return
        self.notify(f"exported {len(body) - 3} command(s) → {path}")

    # ── apply ─────────────────────────────────────────────────────

    def _apply_ui(self) -> None:
        if not self.session:
            self.notify("Not connected (F2)", severity="warning")
            return
        cmds, problems = self._build_apply_commands()
        if not cmds:
            self.notify("nothing to send; fill the Setup form",
                        severity="warning")
            return
        body = "\n".join(cmds)
        if problems:
            body += "\n\n[yellow]warnings:[/]\n" + "\n".join(problems)
        body += "\n\n[dim]MODE/REBOOT is appended automatically if missing.[/]"

        def _cb(confirmed: bool | None) -> None:
            if not confirmed:
                return
            final, reboot = build_batch_commands(cmds)
            self.run_worker(lambda: self._batch_worker(final),
                            thread=True, exclusive=True)
        self.push_screen(ConfirmModal("Review & Apply", body,
                                      "Apply & Send"), _cb)

    def action_apply(self) -> None:
        self._apply_ui()


def probe(port: str | None) -> int:
    """Quick read-only device check: ETS + RCONF, prints the dump."""
    import time as _time

    from .protocol import MT710Session
    from .transport import scan_ports

    ports = scan_ports()
    target = port or (ports[0].device if ports else None)
    if not target:
        print("no serial port found")
        return 1

    def on_event(event: str, data: dict) -> None:
        if event == "line":
            print(f"  RX| {data['line']}")
        elif event == "sent":
            print(f"  TX| {data['command']}")

    session = MT710Session(target, on_event=on_event)
    session.open()
    _time.sleep(0.5)
    if not session.ets_handshake():
        print("ETS: no reply; device asleep or off?")
        session.close()
        return 2
    dump = session.read_config(force_ets=False)
    session.close()
    if not dump:
        print("RCONF: no data")
        return 3
    print(f"\n{dump.model} · IMEI {dump.imei} · FW {dump.firmware}")
    for key, value in dump.fields:
        print(f"  {key:6} {value}")
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="mt710",
        description="Mictrack MT710 USB config tool & serial console")
    parser.add_argument("port", nargs="?", default=None,
                        help="serial port (default: first USB-serial port)")
    parser.add_argument("--list-ports", action="store_true",
                        help="list candidate serial ports and exit")
    parser.add_argument("--version", action="store_true",
                        help="print version + resolved module path and exit")
    parser.add_argument("--probe", action="store_true",
                        help="read-only check: wake with ETS, print RCONF, "
                             "exit")
    args = parser.parse_args()
    if args.version:
        import mt710
        print(f"mt710-config {mt710.__version__}")
        print(f"module: {mt710.__file__}")
        return
    if args.list_ports:
        for p in scan_ports():
            print(f"{p.device}\t{p.description}")
        return
    if args.probe:
        raise SystemExit(probe(args.port))
    MT710App(port=args.port).run()


if __name__ == "__main__":
    main()
