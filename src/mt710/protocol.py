"""Command/reply session layer for the MT710 (ETS, RCONF, batch runner).

Timing constants mirror the official web config tool (v1.2.32), which was
calibrated against real hardware:

===========================  =========  =========  ==========
command class                match      idle (ms)  timeout ms
===========================  =========  =========  ==========
ETS handshake                ETS,OK     -          1800
RCONF dump                   IN:        700        6000
sequence step                <CFG>:     600        4000
RESET                        -          800        5000
DBG / QTS                    <CFG>:|OK  400        2500
===========================  =========  =========  ==========
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .transport import LineEvent, LineKind, SerialTransport

ETS_TRIES = 3
ETS_TIMEOUT_MS = 1800
RCONF_IDLE_MS = 700
RCONF_TIMEOUT_MS = 6000
STEP_IDLE_MS = 600
STEP_TIMEOUT_MS = 4000
RESET_IDLE_MS = 800
RESET_TIMEOUT_MS = 5000
DBG_QTS_IDLE_MS = 400
DBG_QTS_TIMEOUT_MS = 2500
REBOOT_WATCHDOG_S = 40.0
IMEI_RETRY_MS = 4000
IMEI_MAX_TRIES = 5

_PREFIX_RE = re.compile(r"^<(?:CFG|Trace)>:")
_BOOT_BANNER_RE = re.compile(r"^<Trace>:(WAIT CHECK VBATT|LOARD USER CONFIG)")
_ETS_OK_RE = re.compile(r"^<CFG>:ETS,OK")
_IMEI_RE = re.compile(r"^ID:(\d{10,})")


def strip_prefix(line: str) -> str:
    """'\\r\\n<CFG>:ETS,OK' → 'ETS,OK' etc."""
    return _PREFIX_RE.sub("", line).strip()


@dataclass
class Reply:
    ok: bool
    lines: list[str] = field(default_factory=list)
    timed_out: bool = False

    def cfg_lines(self) -> list[str]:
        return [l for l in self.lines if l.startswith("<CFG>:")]

    def cfg_field(self, key: str) -> Optional[str]:
        """First 'KEY:value' field from any reply line (prefix-tolerant)."""
        for raw in self.lines:
            body = strip_prefix(raw)
            if body.startswith(key + ":"):
                return body[len(key) + 1:].strip()
        return None


# ── RCONF dump ────────────────────────────────────────────────────


@dataclass
class ConfigDump:
    """Parsed RCONF output.  ``fields`` keeps the device's order."""

    fields: list[tuple[str, str]] = field(default_factory=list)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        for k, v in self.fields:
            if k == key:
                return v
        return default

    @property
    def model(self) -> str:
        return self.get("MDL") or self.get("MODEL") or "?"

    @property
    def imei(self) -> str:
        return self.get("ID") or "?"

    @property
    def firmware(self) -> str:
        return self.get("SV") or "?"


@dataclass
class StepResult:
    command: str
    ok: bool
    ack: Optional[str] = None
    all_lines: list[str] = field(default_factory=list)


class _Collector:
    """One outstanding command; resolves on match / idle / timeout."""

    def __init__(self, match: Optional[Callable[[str], bool]],
                 idle_ms: int, timeout_ms: int) -> None:
        self.match = match
        self.idle_ms = idle_ms
        self.timeout_ms = timeout_ms
        self.lines: list[str] = []
        self.timed_out = False
        self.done = threading.Event()
        self._deadline = time.monotonic() + timeout_ms / 1000
        self._idle_timer: Optional[threading.Timer] = None

    def feed(self, line: str) -> None:
        self.lines.append(line)
        if self.match and self.match(line):
            self.finish(True)
            return
        if self.idle_ms:
            if self._idle_timer:
                self._idle_timer.cancel()
            self._idle_timer = threading.Timer(
                self.idle_ms / 1000, self.finish, args=(True,)
            )
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def finish(self, ok: bool) -> None:
        if self.done.is_set():
            return
        if self._idle_timer:
            self._idle_timer.cancel()
        if not ok:
            self.timed_out = True
        self.done.set()

    def wait(self) -> Reply:
        remaining = self._deadline - time.monotonic()
        self.done.wait(max(0.0, remaining))
        if not self.done.is_set():
            self.finish(False)
            self.lines  # keep collected lines
        return Reply(ok=self.done.is_set() and not self.timed_out,
                     lines=self.lines, timed_out=self.timed_out)


def parse_rconf_lines(lines: list[str]) -> ConfigDump:
    """Parse raw reply lines into a ConfigDump (prefix-tolerant)."""
    dump = ConfigDump()
    for raw in lines:
        body = strip_prefix(raw)
        if not body or ":" not in body:
            continue
        key, _, value = body.partition(":")
        dump.fields.append((key.strip(), value.strip()))
    return dump


class MT710Session:
    """Owns the transport; tracks the ETS session state passively."""

    def __init__(self, port: str,
                 on_event: Optional[Callable[[str, dict], None]] = None) -> None:
        self.on_event = on_event
        self.ets_active = False
        self.passive_imei: Optional[str] = None
        self.last_config: Optional[ConfigDump] = None
        self._collector: Optional[_Collector] = None
        self._lock = threading.Lock()
        self.transport = SerialTransport(
            port, on_line=self._on_line, on_disconnect=self._on_disconnect
        )

    # ── lifecycle ──────────────────────────────────────────────────

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()
        self.ets_active = False

    def _on_disconnect(self, reason: str) -> None:
        self._emit("disconnected", {"reason": reason})

    def _emit(self, event: str, data: dict) -> None:
        if self.on_event:
            self.on_event(event, data)

    # ── line routing ───────────────────────────────────────────────

    def _on_line(self, ev: LineEvent) -> None:
        self._passive_scan(ev.line)
        with self._lock:
            collector = self._collector
        if collector:
            collector.feed(ev.line)
        self._emit("line", {"line": ev.line, "kind": ev.kind.value, "at": ev.at})

    def _passive_scan(self, line: str) -> None:
        if _BOOT_BANNER_RE.match(line):
            self.ets_active = False
            self._emit("ets", {"active": False, "why": "boot banner"})
        elif _ETS_OK_RE.match(line):
            self.ets_active = True
            self._emit("ets", {"active": True})
        elif line.startswith("<CFG>:QTS,OK"):
            # QTS saves and exits config mode; the session is over
            self.ets_active = False
            self._emit("ets", {"active": False, "why": "QTS exited config"})
        body = strip_prefix(line)
        m = _IMEI_RE.match(body)
        if m:
            self.passive_imei = m.group(1)
            self._emit("imei", {"imei": m.group(1)})

    # ── sending ────────────────────────────────────────────────────

    def send_raw(self, command: str) -> None:
        """Write verbatim, no collector (raw console bar)."""
        self.transport.write(command)
        self._emit("sent", {"command": command})

    def _collect(self, command: str, *, match: Optional[Callable[[str], bool]],
                 idle_ms: int = 0, timeout_ms: int = STEP_TIMEOUT_MS) -> Reply:
        with self._lock:
            if self._collector:
                self._collector.finish(False)  # superseded by a new command
        collector = _Collector(match, idle_ms, timeout_ms)
        with self._lock:
            self._collector = collector
        self.transport.write(command)
        self._emit("sent", {"command": command})
        reply = collector.wait()
        with self._lock:
            if self._collector is collector:
                self._collector = None
        return reply

    # ── ETS ────────────────────────────────────────────────────────

    def ensure_ts(self) -> bool:
        if self.ets_active:
            return True
        return self.ets_handshake()

    def ets_handshake(self, tries: int = ETS_TRIES) -> bool:
        for _ in range(tries):
            reply = self._collect(
                "ETS",
                match=lambda l: "ETS,OK" in l,
                timeout_ms=ETS_TIMEOUT_MS,
            )
            if any("ETS,OK" in l for l in reply.lines):
                self.ets_active = True
                self._emit("ets", {"active": True})
                return True
        return False

    # ── RCONF ──────────────────────────────────────────────────────

    def read_config(self, force_ets: bool = True) -> Optional[ConfigDump]:
        if force_ets or not self.ets_active:
            if not self.ensure_ts():
                return None
        reply = self._collect(
            "RCONF",
            match=lambda l: strip_prefix(l).startswith("IN:"),
            idle_ms=RCONF_IDLE_MS,
            timeout_ms=RCONF_TIMEOUT_MS,
        )
        dump = parse_rconf_lines(reply.lines)
        if dump.fields:
            self.last_config = dump
            self._emit("config", {"dump": dump})
            return dump
        return None

    # ── single command / batch runner ──────────────────────────────

    def send_command(self, command: str) -> StepResult:
        """Send one already-validated command, expect a <CFG>: ack."""
        reply = self._collect(
            command,
            match=lambda l: l.startswith("<CFG>:"),
            idle_ms=STEP_IDLE_MS,
            timeout_ms=STEP_TIMEOUT_MS,
        )
        acks = reply.cfg_lines()
        return StepResult(command, bool(acks),
                          acks[0] if acks else None, reply.lines)

    def run_batch(self, commands: list[str],
                  reboot_expected: bool = False) -> list[StepResult]:
        """Run commands top-to-bottom; stop at first failure.

        Tolerance rule from the web tool: if the ack of the LAST step is
        missing and a reboot is expected, treat it as OK; MODE can reboot
        immediately after acking, faster than the reply can be flushed.
        """
        results: list[StepResult] = []
        for i, cmd in enumerate(commands):
            if cmd == "ETS":
                ok = self.ets_handshake()
                results.append(StepResult("ETS", ok, "ETS,OK" if ok else None))
                if not ok:
                    return results
                continue
            if cmd == "RESET":
                reply = self._collect("RESET", match=None,
                                      idle_ms=RESET_IDLE_MS,
                                      timeout_ms=RESET_TIMEOUT_MS)
                results.append(StepResult("RESET", bool(reply.lines),
                                          None, reply.lines))
                if not reply.lines:
                    return results
                continue
            result = self.send_command(cmd)
            last = i == len(commands) - 1
            if not result.ok and last and reboot_expected:
                result.ok = True  # may have rebooted before acking
                results.append(result)
                return results
            results.append(result)
            if not result.ok:
                return results
        return results
