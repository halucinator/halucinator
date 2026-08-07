# Copyright 2026 Christopher Wright
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recording and self-modelling catch-all peripherals.

``RecordingPeripheral`` observes every MMIO access at a catch-all region and
logs ``(seq, pc, addr, size, value, rw)`` — to memory always, and to a SQLite
``mmio_trace`` table when a db path is configured. The trace is a general-purpose
record of the firmware's MMIO behaviour, useful for analysis and for building a
precise hand-written model later.

``AutoPeripheral`` adds a runtime policy that lets unmodelled firmware boot
without hand-written intercepts:

  * Busy-wait breaker (uEmu-lite). Firmware routinely spins on a status bit —
    ``while(!(REG & READY));`` or ``while(REG & BUSY);``. With a return-0
    catch-all those loops never exit. We detect a stall (the same instruction
    reading the same address many times in a row) and escalate the returned
    value: first all-ones (breaks "wait for a bit to SET", the common case:
    HSERDY/PLLRDY/TXE/RXNE), then zero (breaks "wait while BUSY"); whichever
    ends the stall is cached for that ``(pc, addr)``.

  * Free-running counters, 32- and 64-bit (opt-in; see the ``HAL_AUTO_COUNTER*``
    knobs documented below).

  * Output capture. A register that receives a stream of byte writes whose low
    byte is printable ASCII is almost certainly a UART/data TX register; we
    accumulate and log it, surfacing the firmware's console (e.g. a GRBL banner)
    without knowing the driver function.

This is intentionally heuristic: it gets firmware booting and records a trace of
its MMIO behaviour for later analysis. It is a base class — concrete device
peripheral models subclass ``AutoPeripheral`` to add specific register behaviour
while keeping the recording and busy-wait-breaker machinery.

Environment knobs (all opt-in, default off):

  ``HAL_AUTO_COUNTER_ADDRS=0xA[,0xB...]``       each address becomes a free-running
                                                up-counter (every read returns a
                                                strictly larger value).
  ``HAL_AUTO_COUNTER64_ADDRS=0xLO:0xHI[,...]``  a 64-bit up-counter split across
                                                two 32-bit registers; a bare
                                                ``0xLO`` implies ``HI = LO + 4``.
  ``HAL_AUTO_COUNTER_STEP=0x1000``              increment applied per counter read.
  ``HAL_MMIO_LOG=1``                            log the first read of every
                                                ``(pc, addr)`` and the value
                                                handed back (deduped).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple)

from halucinator import hal_log
from halucinator.peripheral_models.generic import GenericPeripheral

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()

# --- environment knobs ------------------------------------------------------
COUNTER_ADDRS_ENV = "HAL_AUTO_COUNTER_ADDRS"
COUNTER64_ADDRS_ENV = "HAL_AUTO_COUNTER64_ADDRS"
COUNTER_STEP_ENV = "HAL_AUTO_COUNTER_STEP"
MMIO_LOG_ENV = "HAL_MMIO_LOG"
NO_MMIO_TRACE_ENV = "HAL_NO_MMIO_TRACE"
STALL_WINDOW_ENV = "HAL_AUTO_STALL_WINDOW"
STALL_DIV_ENV = "HAL_AUTO_STALL_DIV"
DEFAULT_COUNTER_STEP = 0x1000
DEFAULT_STALL_WINDOW = 8192
DEFAULT_STALL_DIV = 4


def _tokens(raw: str) -> Iterable[str]:
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            yield tok


def parse_counter_addrs(raw: Optional[str]) -> Tuple[Set[int], List[str]]:
    """``"0x10,0x20"`` -> ``({0x10, 0x20}, [])``. Bad tokens are reported."""
    addrs: Set[int] = set()
    bad: List[str] = []
    for tok in _tokens(raw or ""):
        try:
            addrs.add(int(tok, 0))
        except ValueError:
            bad.append(tok)
    return addrs, bad


def parse_counter64_addrs(raw: Optional[str]) -> Tuple[Dict[int, int], List[str]]:
    """``"0x200:0x204,0x300"`` -> ``({0x204: 0x200, 0x304: 0x300}, [])``.

    The mapping is ``hi_addr -> lo_addr``; the low address is the pair id.
    """
    pairs: Dict[int, int] = {}
    bad: List[str] = []
    for tok in _tokens(raw or ""):
        try:
            if ":" in tok:
                lo_s, hi_s = tok.split(":", 1)
                lo, hi = int(lo_s, 0), int(hi_s, 0)
            else:
                lo = int(tok, 0)
                hi = lo + 4
        except ValueError:
            bad.append(tok)
            continue
        pairs[hi] = lo
    return pairs, bad


def parse_counter_step(raw: Optional[str]) -> int:
    if not raw:
        return DEFAULT_COUNTER_STEP
    try:
        return int(raw, 0)
    except ValueError:
        return DEFAULT_COUNTER_STEP


def parse_int(raw: Optional[str], default: int) -> int:
    """``int(raw, 0)`` with a fallback for missing/garbage input."""
    if not raw:
        return default
    try:
        return int(raw, 0)
    except ValueError:
        return default


def mmio_log_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get(MMIO_LOG_ENV) == "1"


def mmio_trace_disabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when the host should pass ``db_path=None`` (throughput runs)."""
    environ = os.environ if environ is None else environ
    return environ.get(NO_MMIO_TRACE_ENV) == "1"


def trace_db_path(db_path: Optional[str],
                  environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The db_path a recording peripheral should actually be given: the
    configured path unless ``HAL_NO_MMIO_TRACE=1``."""
    return None if mmio_trace_disabled(environ) else db_path


# --- the peripherals --------------------------------------------------------
class RecordingPeripheral(GenericPeripheral):
    """Catch-all that records every access. Reads still return 0."""

    # Flush to SQLite every this many accesses so the trace survives even a
    # hard kill (firmware that spins forever never lets a clean shutdown /
    # flush run, because the unicorn emu_start C loop doesn't return).
    FLUSH_EVERY = 4096      # accesses
    FLUSH_SECONDS = 2.0     # ...or wall-clock, whichever comes first

    # In-memory trace rows retained before the persisted prefix is dropped.
    TRACE_HIGH_WATER = 200_000

    def __init__(self, name: str, address: int, size: int,
                 db_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(name, address, size, **kwargs)
        self.db_path = db_path
        self._seq = 0
        self._flushed = 0
        self._conn: Optional[sqlite3.Connection] = None
        self._last_flush = time.monotonic()
        self.trace: List[Tuple[int, int, int, int, int, str]] = []

    def _record(self, pc: int, addr: int, size: int, value: int, rw: str) -> None:
        self.trace.append((self._seq, pc, addr, size, value, rw))
        self._seq += 1
        # Flush on either a count threshold (chatty firmware) or a wall-clock
        # interval (low-MMIO firmware that loops forever in compute) — the
        # emu_start C loop never returns so a clean-shutdown flush can't run.
        if self.db_path and (self._seq - self._flushed) >= self.FLUSH_EVERY:
            self.flush()
        elif self.db_path and (time.monotonic() - self._last_flush) >= self.FLUSH_SECONDS:
            self.flush()

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        addr = self.address + offset
        self._record(pc, addr, size, 0, "r")
        return 0

    def hw_write(self, offset: int, size: int, value: int,
                 pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        addr = self.address + offset
        self._record(pc, addr, size, value, "w")
        return True

    def flush(self) -> None:
        """Persist new trace rows to SQLite (incremental). Safe to call
        repeatedly; only rows since the last flush are written."""
        if not self.db_path:
            return
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS mmio_trace ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, region TEXT, seq INTEGER,"
                " pc INTEGER, addr INTEGER, size INTEGER, value INTEGER, rw TEXT)")
        new = self.trace[self._flushed:]
        if new:
            self._conn.executemany(
                "INSERT INTO mmio_trace(region, seq, pc, addr, size, value, rw)"
                " VALUES (?,?,?,?,?,?,?)",
                [(self.name, s, pc, a, sz, v, rw)
                 for (s, pc, a, sz, v, rw) in new])
            self._conn.commit()
            self._flushed = len(self.trace)
        self._last_flush = time.monotonic()
        # Drop the in-memory prefix we've persisted to bound memory on
        # long-running (looping) firmware, keeping seq numbers intact.
        if self._flushed > self.TRACE_HIGH_WATER:
            self.trace = self.trace[self._flushed:]
            self._flushed = 0

    def shutdown(self) -> None:  # noqa: D401
        try:
            self.flush()
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        except Exception:  # noqa: BLE001
            log.exception("RecordingPeripheral flush failed")
        super().shutdown() if hasattr(super(), "shutdown") else None


class AutoPeripheral(RecordingPeripheral):
    """RecordingPeripheral + busy-wait breaker + counters + output capture."""

    def __init__(self, name: str, address: int, size: int,
                 db_path: Optional[str] = None,
                 stall_threshold: int = 16,
                 stall_window: Optional[int] = None,
                 stall_win_div: Optional[int] = None,
                 counter_addrs: Optional[Iterable[int]] = None,
                 counter64_addrs: Optional[Dict[int, int]] = None,
                 counter_step: Optional[int] = None,
                 mmio_log: Optional[bool] = None,
                 **kwargs: Any) -> None:
        super().__init__(name, address, size, db_path=db_path, **kwargs)
        self.stall_threshold = stall_threshold
        # (pc, addr) -> consecutive repeat count
        self._repeat: Dict[Tuple[int, int], int] = {}
        self._last_key: Optional[Tuple[int, int]] = None
        # WINDOWED spin detection (interleaved-poll robust). The consecutive
        # counter above resets whenever a DIFFERENT (pc, addr) is read, so a
        # register polled in a loop that ALSO touches another register each
        # iteration (status + timeout counter), or that is read from two PCs,
        # never reaches ``stall_threshold`` even though it is a genuine
        # never-exiting busy-wait. Track per-(pc,addr) read counts over a
        # tumbling window of the last ``_stall_window`` reads; a key that
        # DOMINATES the window (>= ``_stall_win_trigger`` of them) is a spin
        # regardless of interleaving, and escalates through the same value
        # tiers as the consecutive path via a persistent per-key level. The
        # dominance bar (default 1/4 of an 8192-read window) is high enough
        # that a register merely read often -- not spun on -- never trips it,
        # preserving the conservative behaviour of the consecutive detector.
        self._stall_window = (stall_window if stall_window is not None
                              else parse_int(os.environ.get(STALL_WINDOW_ENV),
                                             DEFAULT_STALL_WINDOW))
        _div = (stall_win_div if stall_win_div is not None
                else parse_int(os.environ.get(STALL_DIV_ENV), DEFAULT_STALL_DIV))
        self._stall_win_trigger = max(self.stall_threshold,
                                      self._stall_window // max(1, _div))
        self._win: Dict[Tuple[int, int], int] = {}
        self._win_total = 0
        self._win_escal: Dict[Tuple[int, int], int] = {}
        # (pc, addr) -> cached value that broke the stall
        self._cached: Dict[Tuple[int, int], int] = {}
        # (pc, addr) -> running value for free-running-counter registers
        self._counter: Dict[Tuple[int, int], int] = {}
        # Addresses explicitly modelled as free-running counters: every read
        # returns a monotonically increasing value (from the FIRST read, so a
        # `start=REG; while(REG-start < N)` calibrated delay sees a real delta
        # and elapses). Declare via HAL_AUTO_COUNTER_ADDRS=0xADDR[,0xADDR...]
        # — the generic spin heuristic can't catch a counter polled in a loop
        # that also reads other registers (the consecutive run keeps resetting).
        self._counter_addrs: Set[int] = set()
        if counter_addrs is None:
            parsed, bad = parse_counter_addrs(os.environ.get(COUNTER_ADDRS_ENV))
            self._counter_addrs |= parsed
            for tok in bad:
                log.warning("bad %s token %r", COUNTER_ADDRS_ENV, tok)
        else:
            self._counter_addrs |= set(counter_addrs)
        self._free_counter: Dict[int, int] = {}   # addr -> running value
        self._counter_step = (
            counter_step if counter_step is not None
            else parse_counter_step(os.environ.get(COUNTER_STEP_ENV)))
        if self._counter_addrs:
            hlog.info("AutoPeripheral: free-running counter regs: %s",
                      ", ".join("0x%08x" % a for a in sorted(self._counter_addrs)))

        # 64-bit free-running counter PAIRS. A single 64-bit up-counter exposed
        # as two 32-bit MMIO registers (low word + high word) -- e.g. the
        # Cortex-A9 MPCore global timer at PERIPHBASE+0x200(lo)/+0x204(hi). A
        # 64-bit reader typically loops `do { hi1=HI; lo=LO; hi2=HI; } while
        # (hi1 != hi2)`, so the HIGH word must be STABLE across consecutive
        # reads (advancing ONLY when the low word overflows) or the loop never
        # converges. Declare via HAL_AUTO_COUNTER64_ADDRS="0xLO:0xHI"
        # (comma-separated; a bare "0xLO" implies HI=LO+4). A LOW read advances
        # the shared 64-bit accumulator by HAL_AUTO_COUNTER_STEP and returns
        # bits[31:0]; a HIGH read returns bits[63:32] WITHOUT advancing.
        self._counter64_lo: Dict[int, int] = {}   # lo addr -> lo addr (pair id)
        self._counter64_hi: Dict[int, int] = {}   # hi addr -> lo addr
        self._counter64_val: Dict[int, int] = {}   # lo addr -> 64-bit accumulator
        if counter64_addrs is None:
            pairs, bad64 = parse_counter64_addrs(
                os.environ.get(COUNTER64_ADDRS_ENV))
            for tok in bad64:
                log.warning("bad %s token %r", COUNTER64_ADDRS_ENV, tok)
        else:
            pairs = dict(counter64_addrs)
        for hi, lo in pairs.items():
            self._counter64_lo[lo] = lo
            self._counter64_hi[hi] = lo
        if self._counter64_hi:
            hlog.info("AutoPeripheral: 64-bit counter pairs (hi/lo): %s",
                      ", ".join("0x%08x/0x%08x" % (hi, lo)
                                for hi, lo in sorted(self._counter64_hi.items())))
        # addr -> accumulated printable output bytes
        self._out: Dict[int, bytearray] = {}
        # HAL_MMIO_LOG=1: log the FIRST read of each (pc,addr) hardware register
        # and the value we hand back. These first-reads return 0 by default --
        # the suspects for "firmware needed a 1 here, got 0, took the wrong
        # branch". Diagnostic-only; deduped so it can't spam.
        self._mmio_log = (mmio_log_enabled() if mmio_log is None
                          else bool(mmio_log))
        self._logged_reads: set = set()

    def _mask(self, size: int) -> int:
        return (1 << (8 * size)) - 1

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        addr = self.address + offset
        self._record(pc, addr, size, 0, "r")
        key = (pc, addr)

        # 64-bit free-running counter pair (see __init__): the LOW word advances
        # the shared accumulator and returns bits[31:0]; the HIGH word returns
        # bits[63:32] WITHOUT advancing, so a 64-bit read-consistency loop
        # (hi1==hi2) converges.
        if addr in self._counter64_lo:
            lo = self._counter64_lo[addr]
            cur = self._counter64_val.get(lo, 0) + self._counter_step
            self._counter64_val[lo] = cur
            return cur & self._mask(size)
        if addr in self._counter64_hi:
            lo = self._counter64_hi[addr]
            return (self._counter64_val.get(lo, 0) >> 32) & self._mask(size)

        # Free-running counter register: monotonically increasing every read.
        if addr in self._counter_addrs:
            cur = self._free_counter.get(addr, 0) + self._counter_step
            self._free_counter[addr] = cur
            return cur & self._mask(size)

        if key in self._cached:
            return self._cached[key]

        if key == self._last_key:
            self._repeat[key] = self._repeat.get(key, 0) + 1
        else:
            self._repeat[key] = 0
            self._last_key = key

        # Windowed spin detection (interleaved-poll robust, see __init__): a
        # key that dominates the recent read window is a busy-wait even when
        # interleaved with other reads, so the strict-consecutive run never
        # reaches the threshold. Escalate through the same all-ones -> zero ->
        # counter tiers, one step per read once dominance is reached, via a
        # persistent per-key level.
        self._win_total += 1
        self._win[key] = self._win.get(key, 0) + 1
        if self._win_total >= self._stall_window:
            self._win = {}
            self._win_total = 0
        if self._win.get(key, 0) >= self._stall_win_trigger:
            lvl = self._win_escal.get(key, 0)
            self._win_escal[key] = lvl + 1
            if lvl == 0:
                wval = self._mask(size)
            elif lvl == 1:
                wval = 0
            else:
                cur = self._counter.get(key, 0) + (lvl - 1) * 0x10000
                self._counter[key] = cur
                wval = cur & self._mask(size)
            hlog.info(
                "AutoPeripheral: WINDOWED busy-wait at pc=0x%08x addr=0x%08x "
                "-> 0x%x (tier %d, interleaved poll)", pc, addr, wval, lvl)
            return wval

        n = self._repeat[key]
        if n >= self.stall_threshold:
            t = self.stall_threshold
            # Escalate through three tiers as the same read keeps spinning:
            #   1. all-ones  — breaks `while(!(REG & READY))` (wait-for-SET,
            #      the common case: HSERDY/PLLRDY/TXE/RXNE).
            #   2. zero      — breaks `while(REG & BUSY)` (wait-while-BUSY).
            #   3. monotonic counter — neither constant broke the stall, so
            #      the firmware is timing against a FREE-RUNNING COUNTER
            #      (`start=REG; while(REG-start < N)` calibrated delay). A
            #      constant can never satisfy it; return an ever-increasing
            #      value so the delay elapses. The step is large and grows
            #      with the spin so any finite threshold is crossed quickly.
            if n < t * 2:
                val = self._mask(size)
            elif n < t * 3:
                val = 0
            else:
                cur = self._counter.get(key, 0) + (n - t * 3 + 1) * 0x10000
                self._counter[key] = cur
                val = cur & self._mask(size)
            hlog.info(
                "AutoPeripheral: busy-wait at pc=0x%08x addr=0x%08x -> 0x%x",
                pc, addr, val)
            return val
        if self._mmio_log and key not in self._logged_reads:
            self._logged_reads.add(key)
            hlog.info("MMIO-READ pc=0x%08x addr=0x%08x size=%d -> 0x0 "
                      "(first read, default)", pc, addr, size)
        return 0

    def hw_write(self, offset: int, size: int, value: int,
                 pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        addr = self.address + offset
        self._record(pc, addr, size, value, "w")
        # Output capture: printable low byte => likely a data/TX register.
        low = value & 0xFF
        if size <= 4 and (low == 0x0A or low == 0x0D or 0x20 <= low < 0x7F):
            buf = self._out.setdefault(addr, bytearray())
            if low == 0x0A:  # newline -> emit the line
                line = buf.decode("latin-1").rstrip("\r")
                hlog.info("AutoPeripheral UART(0x%08x): %s", addr, line)
                buf.clear()
            elif low != 0x0D:
                buf.append(low)
        # A write to a polled status register usually clears the stall.
        self._last_key = None
        return True
