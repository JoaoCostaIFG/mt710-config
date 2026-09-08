"""Serial transport for the Mictrack MT710 (USB config cable).

Protocol constants calibrated from the official web config tool
(config.mictrack.com v1.2.32, validated on real hardware) and the
Mictrack_MT710_Commands_List.pdf:

* 921600 baud, 8N1, DTR + RTS asserted after open (device stays silent
  without them).
* Commands are written VERBATIM — no CR/LF terminator, no wrapper.
* Replies are ``\\r\\n`` separated but a single line may be split across
  reads, and ``\\r`` / ``\\n`` may arrive in separate chunks.
* Command replies have NO trailing newline (e.g. ``\\r\\n<CFG>:ETS,OK``),
  so the tail of the buffer must be flushed as a complete line after a
  short idle gap (REPLY_IDLE_MS = 250 ms).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from serial import Serial, SerialException, serial_for_url
from serial.tools import list_ports

BAUD_RATE = 921600
REPLY_IDLE_MS = 250
READ_TIMEOUT_S = 0.05
READ_LOOP_RETRIES = 6
READ_RETRY_BACKOFF_S = 0.4

# Known USB-serial chips used by Mictrack config cables (plus the generic
# names the web tool uses).
KNOWN_CHIPS = {
    0x067B: "Prolific PL2303",
    0x1A86: "CH340",
    0x10C4: "CP210x",
    0x0403: "FTDI",
}


@dataclass
class PortInfo:
    device: str
    description: str
    chip: str
    vid: Optional[int] = None
    pid: Optional[int] = None


def scan_ports() -> list[PortInfo]:
    """List candidate ports. USB-serial devices (config cables) only —
    legacy 8250 ttyS* ports are noise; fall back to everything if no
    USB port exists."""
    ports: list[PortInfo] = []
    for p in list_ports.comports():
        if p.vid is None:
            continue  # not a USB device (built-in ttyS* etc.)
        chip = KNOWN_CHIPS.get(p.vid)
        label = f"{chip} ({p.description})" if chip else p.description
        ports.append(PortInfo(p.device, label, chip or "?", p.vid, p.pid))
    if not ports:  # last resort: show everything
        for p in list_ports.comports():
            if p.device is None:
                continue
            chip = KNOWN_CHIPS.get(p.vid) if p.vid else None
            label = f"{chip} ({p.description})" if chip else p.description
            ports.append(PortInfo(p.device, label, chip or "?", p.vid, p.pid))
    ports.sort(key=lambda pi: (pi.vid not in KNOWN_CHIPS, pi.device))
    return ports


class LineKind(Enum):
    """Classifies a received line for rendering / parsing."""

    CFG = "cfg"        # <CFG>: command acknowledgement / config dump
    TRACE = "trace"    # <Trace>: device debug chatter
    PLAIN = "plain"    # boot banners, misc output


def classify_line(line: str) -> LineKind:
    if line.startswith("<CFG>:"):
        return LineKind.CFG
    if line.startswith("<Trace>"):
        return LineKind.TRACE
    return LineKind.PLAIN


@dataclass
class LineEvent:
    line: str
    kind: LineKind
    at: float = field(default_factory=time.monotonic)


class SerialTransport:
    """Threaded serial reader + verbatim writer.

    Callbacks (``on_line``, ``on_rx``, ``on_disconnect``) are invoked from
    the reader thread — GUIs must marshal them onto their own loop.
    """

    def __init__(
        self,
        port: str,
        baud: int = BAUD_RATE,
        on_line: Optional[Callable[[LineEvent], None]] = None,
        on_rx: Optional[Callable[[bytes], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.on_line = on_line
        self.on_rx = on_rx
        self.on_disconnect = on_disconnect

        self._serial: Optional[Serial] = None
        self._rx_buffer = ""
        self._last_rx_at = 0.0            # monotonic time of last byte (watchdog)
        self._last_line_at = 0.0          # for blank-line burst rule
        self._flush_deadline: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def last_rx_at(self) -> float:
        return self._last_rx_at

    def open(self) -> None:
        if self.is_open:
            return
        ser = serial_for_url(self.port, baudrate=self.baud, timeout=READ_TIMEOUT_S)
        try:
            ser.dtr = True   # device stays silent until DTR/RTS are raised
            ser.rts = True
        except (SerialException, OSError, ValueError):
            pass
        self._serial = ser
        self._stop.clear()
        self._rx_buffer = ""
        self._thread = threading.Thread(
            target=self._read_loop, name=f"mt710-rx:{self.port}", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        ser, self._serial = self._serial, None
        if ser is not None and ser.is_open:
            try:
                ser.close()
            except (SerialException, OSError):
                pass

    # ── writing ────────────────────────────────────────────────────

    def write(self, command: str) -> None:
        """Send a command VERBATIM — deliberately no CR/LF (device protocol)."""
        if not self.is_open:
            raise SerialException("port not open")
        with self._write_lock:
            assert self._serial is not None
            self._serial.write(command.encode("utf-8"))
            self._serial.flush()

    # ── read loop ──────────────────────────────────────────────────

    def _read_loop(self) -> None:
        retries = 0
        while not self._stop.is_set():
            ser = self._serial
            if ser is None:
                return
            try:
                chunk = ser.read(4096)
            except (SerialException, OSError, TermError) as exc:
                retries += 1
                if retries > READ_LOOP_RETRIES:
                    if self.on_disconnect:
                        self.on_disconnect(str(exc))
                    return
                self._sleep(READ_RETRY_BACKOFF_S)
                continue
            if chunk:
                retries = 0
                self._feed(chunk.decode("utf-8", errors="replace"))
            self._maybe_flush_idle()

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def _feed(self, text: str) -> None:
        self._last_rx_at = time.monotonic()
        if self.on_rx:
            self.on_rx(text.encode("utf-8"))
        self._rx_buffer += text
        while "\n" in self._rx_buffer:
            raw, self._rx_buffer = self._rx_buffer.split("\n", 1)
            self._emit(raw.rstrip("\r"))
        if self._rx_buffer:
            self._flush_deadline = self._last_rx_at + REPLY_IDLE_MS / 1000
        else:
            self._flush_deadline = None

    def _maybe_flush_idle(self) -> None:
        """Replies have no trailing newline: after REPLY_IDLE_MS without a
        newline, emit whatever is buffered as a complete line."""
        if self._flush_deadline is None or not self._rx_buffer:
            return
        if time.monotonic() >= self._flush_deadline:
            partial, self._rx_buffer = self._rx_buffer, ""
            self._flush_deadline = None
            self._emit(partial.rstrip("\r"))

    def _emit(self, line: str) -> None:
        now = time.monotonic()
        # The device prefixes each print batch with \r\n, which yields a
        # phantom blank line after every quiet period.  Keep a blank line
        # only when it arrived in the same burst as the previous line.
        if line == "":
            if self._last_line_at and (now - self._last_line_at) < REPLY_IDLE_MS / 1000:
                self._emit_now(line, now)
            return
        self._emit_now(line, now)

    def _emit_now(self, line: str, now: float) -> None:
        self._last_line_at = now
        if self.on_line:
            self.on_line(LineEvent(line, classify_line(line), now))


# pyserial may raise either SerialException or plain OSError depending on
# backend; alias for the except-clause above.
TermError = OSError
