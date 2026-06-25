# Copyright 2026 Christopher Wright

"""AT91RM9200 peripheral models: At91SysCtrl (System Controller / ST-PIT
status), At91Emac (EMAC + MII/PHY link-up), and At91Dbgu (Debug UART).
These model the AT91 silicon and are OS-agnostic (used by the ARM/VxWorks
re-host, but applicable to any OS on the AT91RM9200).

--- At91Dbgu (below) ---
Models the AT91 debug UART register window so firmware that writes to
THR (transmit holding register) gets its bytes captured and forwarded
to the host. Used by ARM/AT91-family VxWorks rehosts.

Register layout (relative to base, typically 0xFFFFF200):
    0x00  CR    Control Register  (RESET / ENABLE bits)
    0x04  MR    Mode Register
    0x08  IER   Interrupt Enable Register
    0x0C  IDR   Interrupt Disable Register
    0x10  IMR   Interrupt Mask Register
    0x14  CSR   Channel Status Register
                 bit 0 RXRDY  (RX data available in RHR)
                 bit 1 TXRDY  (TX register empty, ready for new byte)
                 bit 9 TXEMPTY (TX shift register empty)
    0x18  RHR   Receiver Holding Register
    0x1C  THR   Transmitter Holding Register
    0x20  BRGR  Baud Rate Generator Register
    0x40  C1R   Chip ID 1
    0x44  C2R   Chip ID 2
    0x48  FNTR  Force NTRST
    0x100+      PDC (peripheral DMA controller; unhandled here)

Bridging modes:
- "log" (default): captures TX bytes to a log file (one byte per write,
  newline-separated lines for readability)
- "stderr": prints captured bytes directly to halucinator stderr/log
- "pty": creates a host pty pair; reads from the slave end push into RX,
  TX bytes are written to the slave. (Future work; stub raises.)
- "tcp": creates a TCP server on a configured port; one client at a
  time. (Future work; stub raises.)

YAML usage:
    peripherals:
      at91_dbgu:
        base_addr: 0xFFFFF200
        size: 0x200
        emulate: At91Dbgu
        permissions: rw-
        # Optional class_args via the future bridge_mode field; for now
        # the default 'log' mode writes to /tmp/<name>_uart.log.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional

from .generic import GenericPeripheral
from .. import hal_log

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()


# Register offsets
REG_CR   = 0x00
REG_MR   = 0x04
REG_IER  = 0x08
REG_IDR  = 0x0c
REG_IMR  = 0x10
REG_CSR  = 0x14
REG_RHR  = 0x18
REG_THR  = 0x1c
REG_BRGR = 0x20

# CSR status bits
CSR_RXRDY   = 1 << 0
CSR_TXRDY   = 1 << 1
CSR_TXEMPTY = 1 << 9


class At91Dbgu(GenericPeripheral):
    """AT91 Debug UART peripheral model with host bridging.

    Default: captures TX bytes to /tmp/<name>_uart.log. Reads from CSR
    return TXRDY|TXEMPTY always (firmware sees the UART as immediately
    ready to send). RX is empty unless a future bridge mode pushes
    bytes.
    """

    def __init__(self, name: str, address: int, size: int,
                 bridge_mode: str = "log",
                 log_path: Optional[str] = None,
                 reg_offset: int = 0,
                 pty_link_path: Optional[str] = None,
                 **kwargs: Any) -> None:
        super().__init__(name, address, size, **kwargs)
        self.bridge_mode = bridge_mode
        self.log_path = log_path or f"/tmp/{name}_uart.log"
        # reg_offset: where the DBGU register block actually starts
        # within this mapped peripheral region. The AT91 DBGU is at
        # absolute 0xFFFFF200 but page-alignment forces the mapped
        # region to start at 0xFFFFF000, so reg_offset=0x200.
        self.reg_offset = reg_offset
        # pty mode state
        self._pty_master_fd: Optional[int] = None
        self._pty_slave_path: Optional[str] = None
        self._pty_link_path = pty_link_path or f"/tmp/{name}_pty"
        self._pty_reader_thread = None
        self._pty_stop = False
        self._mode_reg = 0
        self._ier = 0
        self._imr = 0
        self._brgr = 0
        # RX buffer for future bidirectional bridging
        self._rx_buf: bytearray = bytearray()
        # TX byte accumulator (for ASCII-line capture in log mode)
        self._tx_line: bytearray = bytearray()
        # Total bytes captured (logged)
        self.tx_count = 0
        # Open log file in "w" so each run starts fresh
        try:
            self._log_fp = open(self.log_path, "wb")
            hlog.info("At91Dbgu(%s): base=0x%08x size=0x%x bridge=%s "
                      "log=%s", name, address, size, bridge_mode,
                      self.log_path)
        except Exception as _e:  # noqa: BLE001
            hlog.error("At91Dbgu: failed to open log %s: %s", self.log_path, _e)
            self._log_fp = None

        # If pty bridge mode requested, open a pty pair and spawn a
        # reader thread that feeds incoming host bytes into the RX
        # buffer. The slave-side path is symlinked to pty_link_path so
        # host tools can talk to a stable path.
        if bridge_mode == "pty":
            self._open_pty()

    # ---------- pty bridge ----------

    def _open_pty(self) -> None:
        """Open a host pty pair and start a reader thread. The slave
        side is symlinked to `pty_link_path` for stable host access."""
        import pty as _pty
        import threading

        try:
            master_fd, slave_fd = _pty.openpty()
            slave_path = os.ttyname(slave_fd)
            self._pty_master_fd = master_fd
            self._pty_slave_path = slave_path
            # Symlink the slave to a stable path
            try:
                if os.path.islink(self._pty_link_path) or os.path.exists(self._pty_link_path):
                    os.unlink(self._pty_link_path)
                os.symlink(slave_path, self._pty_link_path)
            except Exception as _e:  # noqa: BLE001
                hlog.error("At91Dbgu: pty symlink failed: %s", _e)
            os.close(slave_fd)  # keep only master; clients open via the symlink

            def _reader() -> None:
                while not self._pty_stop:
                    try:
                        chunk = os.read(master_fd, 64)
                        if chunk:
                            self.feed_rx(chunk)
                            hlog.info("At91Dbgu(%s) pty<-host: %d bytes",
                                      self.name, len(chunk))
                    except OSError:
                        break
                    except Exception as _e:  # noqa: BLE001
                        hlog.error("At91Dbgu pty reader: %s", _e)
                        break

            t = threading.Thread(target=_reader, daemon=True,
                                 name=f"{self.name}-pty-reader")
            t.start()
            self._pty_reader_thread = t
            hlog.info("At91Dbgu(%s): pty bridge open. Slave=%s "
                      "(symlinked %s).  Use:  python3 -c 'open(\"%s\",\"wb\").write(b\"abc\")'  to send bytes.",
                      self.name, slave_path, self._pty_link_path,
                      self._pty_link_path)
        except Exception as _e:  # noqa: BLE001
            hlog.error("At91Dbgu: pty open failed: %s", _e)

    # ---------- helpers ----------

    def _emit_tx(self, byte: int) -> None:
        """Forward a TX byte to the configured bridge sink."""
        b = byte & 0xff
        self.tx_count += 1
        if self.bridge_mode == "stderr":
            try:
                sys.stderr.write(chr(b))
                sys.stderr.flush()
            except Exception:
                pass
        elif self.bridge_mode == "pty" and self._pty_master_fd is not None:
            try:
                os.write(self._pty_master_fd, bytes([b]))
            except Exception:
                pass
        # Always also accumulate to log_path for offline review
        if self._log_fp is not None:
            try:
                self._log_fp.write(bytes([b]))
                # Flush on newline or every 64 bytes for visibility
                if b == 0x0a or self.tx_count % 64 == 0:
                    self._log_fp.flush()
            except Exception:
                pass
        # Also accumulate ASCII line and log when newline appears
        self._tx_line.append(b)
        if b == 0x0a or len(self._tx_line) >= 256:
            try:
                line = bytes(self._tx_line).rstrip(b"\r\n").decode(
                    "ascii", errors="replace")
                if line:
                    hlog.info("At91Dbgu TX: %s", line)
            except Exception:
                pass
            self._tx_line.clear()

    def _pop_rx(self) -> int:
        """Pop one byte from the RX buffer, or 0 if empty."""
        if self._rx_buf:
            b = self._rx_buf[0]
            del self._rx_buf[0]
            return b
        return 0

    def feed_rx(self, data: bytes) -> None:
        """Public API: push bytes into the RX queue. Future bridge code
        (pty reader / TCP server) calls this to feed firmware input."""
        self._rx_buf.extend(data)

    # ---------- peripheral interface ----------

    def hw_read(self, offset: int, size: int,
                pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        abs_addr = self.address + offset
        # Translate the offset from the mapped region into the actual
        # DBGU register offset.
        offset = offset - self.reg_offset
        if offset not in (REG_CSR, REG_RHR, REG_MR, REG_IER, REG_IMR,
                          REG_BRGR) and os.environ.get("HAL_MMIO_LOG") == "1":
            # Non-DBGU register in this page (AIC at +0x000, PIO/PMC/ST/RTC
            # higher up, chip-ID, etc.) -- all return 0 here. Prime suspect
            # for "firmware read a status/presence bit, got 0, wrong branch".
            if not hasattr(self, "_nondbgu_seen"):
                self._nondbgu_seen = set()
            if abs_addr not in self._nondbgu_seen:
                self._nondbgu_seen.add(abs_addr)
                hlog.info("DBGU-PAGE non-DBGU read pc=0x%08x addr=0x%08x "
                          "size=%d -> 0x0 (unmodeled)", pc, abs_addr, size)
        if offset < 0:
            # Read from a page-aligned address BEFORE the DBGU register
            # block; absorbed silently.
            return 0
        if offset == REG_CSR:
            # Status: always ready to TX; RXRDY if buffer non-empty.
            val = CSR_TXRDY | CSR_TXEMPTY
            if self._rx_buf:
                val |= CSR_RXRDY
            return val
        if offset == REG_RHR:
            return self._pop_rx()
        if offset == REG_MR:
            return self._mode_reg
        if offset == REG_IER:
            return self._ier
        if offset == REG_IMR:
            return self._imr
        if offset == REG_BRGR:
            return self._brgr
        # Other registers (chip ID, etc.) - return 0
        return 0

    def hw_write(self, offset: int, size: int, value: int,
                 pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        offset = offset - self.reg_offset
        if offset < 0:
            return True
        if offset == REG_THR:
            self._emit_tx(value)
            return True
        if offset == REG_MR:
            self._mode_reg = value & 0xffffffff
            return True
        if offset == REG_IER:
            self._ier |= value & 0xffffffff
            self._imr |= value & 0xffffffff
            return True
        if offset == REG_IDR:
            self._imr &= ~(value & 0xffffffff)
            return True
        if offset == REG_BRGR:
            self._brgr = value & 0xffffffff
            return True
        if offset == REG_CR:
            # CR writes (reset / enable). Just absorb -- no real
            # hardware state to manipulate. Could log if useful.
            return True
        # Other writes (chip ID, PDC, etc.) absorbed silently
        return True

    def __del__(self) -> None:
        try:
            if getattr(self, "_log_fp", None) is not None:
                self._log_fp.flush()
                self._log_fp.close()
        except Exception:
            pass
        try:
            self._pty_stop = True
            if getattr(self, "_pty_master_fd", None) is not None:
                os.close(self._pty_master_fd)
            if getattr(self, "_pty_link_path", None) and \
               os.path.islink(self._pty_link_path):
                os.unlink(self._pty_link_path)
        except Exception:
            pass


class At91SysCtrl(GenericPeripheral):
    '''AT91RM9200 system-controller window (0xffff0000) that reports the
    System Timer (ST) periodic-interval as always-pending, so the System-IRQ
    dispatcher (mr9200Int1Dispatcher) actually invokes the registered clock
    handler each time the synthesised clock tick is injected.

    The dispatcher gates the clock call on (ST_SR & ST_IMR) & PITS: it reads
    ST_SR (0xfffffd10, status) and ST_IMR (0xfffffd1c, interrupt mask) and only
    calls the PIT/clock handler when bit0 is set in both. With a plain
    GenericPeripheral those read 0, so tickAnnounce never runs and the scheduler
    idle-spins. Returning bit0=1 for those two registers (and 0 elsewhere, like
    GenericPeripheral) makes each injected System IRQ drive one clock tick.
    Read-to-clear is irrelevant here -- we re-assert every tick by construction.'''
    _PITS_REGS = (0xfffffd10, 0xfffffd1c)  # ST_SR, ST_IMR

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        if (self.address + offset) in self._PITS_REGS:
            return 1
        return super().hw_read(offset, size, pc=pc, **kwargs)


class At91Emac(GenericPeripheral):
    '''AT91RM9200 peripheral window (0xfffb0000) that models just enough of the
    EMAC + its MII/PHY management so the firmware's Ethernet bring-up
    (ipAttach -> mr920End -> ARM920_SetupPhy) sees an LXT971 PHY with the link
    UP, instead of spinning on an unmodeled PHY / a link that never comes up.

    EMAC is at base 0xfffbc000. readPhyRegister(): writes EMAC_MAN (0xfffbc034)
    with the PHY-reg # in bits 18..22, polls EMAC_ISR (0xfffbc024) bit0 for
    "management done", then reads EMAC_MAN[15:0] for the result. We:
      * EMAC_ISR (0xfffbc024): report bit0=1 (management frame done) so the
        poll completes immediately.
      * EMAC_MAN (0xfffbc034): return the requested PHY register's value
        (decoded from the last EMAC_MAN write): MII status (reg1) = link-up +
        autoneg-complete; PHY id (regs 2/3) = LXT971A (0x0013 / 0x78e0).
      * EMAC_SR (0xfffbc008): link + MDIO-idle bits set.
    Everything else falls through to GenericPeripheral (returns 0).'''
    _EMAC_BASE = 0xfffbc000
    _ISR = _EMAC_BASE + 0x024   # EMAC_ISR  (management-done in bit0)
    _MAN = _EMAC_BASE + 0x034   # EMAC_MAN  (PHY maintenance: write cmd / read result)
    _SR = _EMAC_BASE + 0x008    # EMAC_SR   (link / idle status)
    # PHY register values to report (LXT971A, link up, autoneg complete).
    _PHY = {0: 0x1000, 1: 0x786d, 2: 0x0013, 3: 0x78e0}

    def __init__(self, name: str, address: int, size: int, **kwargs: Any) -> None:
        super().__init__(name, address, size, **kwargs)
        self._last_man = 0

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        addr = self.address + offset
        if addr == self._ISR:
            return 1                       # management frame done
        if addr == self._SR:
            return 0x06                    # link up (bit1) + MDIO idle (bit2)
        if addr == self._MAN:
            phy_reg = (self._last_man >> 18) & 0x1f
            return (self._last_man & 0xffff0000) | self._PHY.get(phy_reg, 0)
        return super().hw_read(offset, size, pc=pc, **kwargs)

    def hw_write(self, offset: int, size: int, value: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        if (self.address + offset) == self._MAN:
            self._last_man = value & 0xffffffff
        return super().hw_write(offset, size, value, pc=pc, **kwargs)
