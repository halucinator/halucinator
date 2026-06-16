# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) HW-UART target-device intercepts.

A modeled **serial peer** wired to the Bus Pirate's hardware UART mode. This
brings the UART interface up end-to-end under HALucinator by doing high-level
emulation (HLE) at the firmware's leaf UART helpers, so the firmware's real
TX/RX byte flow is preserved while a Python peer supplies the incoming bytes —
no PL011 / PIO register state machine to emulate.

ABI (RE'd from ``bus_pirate5_rev10.bin``, Thumb, flash base ``0x10000000``):

The Bus Pirate ``hwuart`` mode write/read vtable entries are *also* the leaf
MMIO helpers (the active path drives the real RP2040 UART1 at ``0x40034000``,
not the ``hwuart_pio_*`` program). Both take a pointer to the firmware's
``bytecode_t`` transaction struct in ``r0`` — the same struct SPI uses:

* ``hwuart_write(bytecode_t *b)`` — TX: loads the outgoing byte from
  ``b->out_data`` at ``[r0 + 0x14]``, then writes it to the UART data register.
  We read ``[r0+0x14]``, hand it to the peer, and return ``(True, 0)`` so the
  real MMIO write/poll is skipped.
* ``hwuart_read(bytecode_t *b)`` — RX: reads the UART data register and stores
  the received byte into ``b->in_data`` at ``[r0 + 0x18]``. This is where the
  firmware blocks waiting for an incoming byte. We write the peer's next byte
  into ``[r0+0x18]`` and return ``(True, 0)`` so the firmware never touches the
  (empty) UART RX FIFO.

The struct offsets were confirmed against ``spi_write`` (``ldrb r0,[r0,#0x14]``
/ ``str r0,[r4,#0x18]``), which uses the identical ``bytecode_t`` layout.

Two peer models, selected via ``registration_args['mode']``:

* ``"loopback"`` (default, the keystone): each byte the firmware transmits is
  echoed straight back on the next read (write ``'A'`` 0x41 -> read 0x41).
* ``"nmea"`` (bonus stretch): the peer streams a fixed ``$GPGGA,...*hh\r\n``
  NMEA sentence into the read path so the firmware's GPS decoder sees a fix.

Annotations target ``HalBackend`` (the abstract base) so the handler works on
unicorn *and* avatar2 — only ``get_arg`` / ``read_memory`` / ``write_memory``
are used.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Deque, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# bytecode_t field offsets (RE'd; shared with the SPI mode).
_OUT_DATA = 0x14   # b->out_data : byte the firmware is transmitting
_IN_DATA = 0x18    # b->in_data  : byte the firmware reads back

# RP2040 UART1 (PL011) — the hwuart mode drives the real controller at this
# base (RAM-backed in the demo memory map). hwuart_setup_exc, hwuart_periodic
# and the open/preflight paths poll the Flag Register (FR) at +0x18 directly,
# bypassing the hwuart_write/read hooks. With blank RAM the FR reads 0, so
# RXFE (RX-FIFO-empty, bit 4) is *clear* — the firmware then thinks the RX
# FIFO is permanently non-empty and spins forever in setup_exc's RX-drain
# loop. Seeding FR with a quiescent "TX empty / RX empty" value breaks that.
_UART1_BASE = 0x40034000
_UART_FR = _UART1_BASE + 0x18   # PL011 flag register
# PL011 FR bits: TXFE=0x80 (TX FIFO empty), RXFF=0x40, TXFF=0x20,
# RXFE=0x10 (RX FIFO empty), BUSY=0x08. Quiescent idle line = TX empty,
# RX empty, not busy/full: 0x90 (TXFE | RXFE).
_UART_FR_IDLE = 0x90


# A recognizable NMEA GPGGA fix, checksum-correct (*59). The firmware's
# nmea_decode_handler/process_gps expect $GPGGA,...*hh\r\n. (The NMEA stretch
# streams this via the Rp2040Uart1Source peripheral, since the gps command
# reads raw PL011 MMIO; this copy is kept for reference/parity.)
_NMEA_SENTENCE = b"$GPGGA,123519,3723.2475,N,12158.3416,W,1,08,0.9,545.4,M,46.9,M,,*59\r\n"


class UartPeerTarget(BPHandler):
    """A modeled UART serial peer attached to the Bus Pirate HW-UART mode."""

    def __init__(self, mode: str = "loopback", sentence: bytes | None = None) -> None:
        super().__init__()
        self.mode = (mode or "loopback").lower()
        # Bytes queued for the firmware to read back.
        self._rx: Deque[int] = deque()
        if self.mode == "nmea":
            data = sentence if sentence is not None else _NMEA_SENTENCE
            if isinstance(data, str):
                data = data.encode("latin-1")
            self._nmea = bytes(data)
            # Pre-load the sentence so the very first read already has data.
            self._rx.extend(self._nmea)
        else:
            self._nmea = b""
        print(
            f"[UartPeerTarget] modeled UART peer attached (mode={self.mode})",
            flush=True,
        )

    # --- setup: keep the PL011 FR in a quiescent state -----------------------
    @bp_handler(["seed_fr"])
    def seed_fr(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Seed UART1 FR = TXFE|RXFE before ``hwuart_setup_exc`` runs.

        ``hwuart_setup_exc`` drains the (real) RX FIFO in a tight loop keyed on
        RXFE. With blank RAM FR=0 so RXFE never sets and the loop spins. We
        write a quiescent FR and return ``False`` so the real setup_exc still
        runs (it configures the bio pins) but its drain loop terminates
        immediately. Re-seeded on every call so periodic/open pollers also see
        an empty-but-ready line.
        """
        qemu.write_memory(_UART_FR, 4, _UART_FR_IDLE)
        print(f"[UartPeerTarget] seeded UART1 FR=0x{_UART_FR_IDLE:02X} "
              f"(setup_exc)", flush=True)
        return False, 0

    # --- TX: firmware -> peer ------------------------------------------------
    @bp_handler(["write"])
    def write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwuart_write(bytecode_t *b)`` — firmware transmits ``b->out_data``."""
        b = qemu.get_arg(0)
        tx = qemu.read_memory(b + _OUT_DATA, 1, 1) & 0xFF
        if self.mode == "nmea":
            # NMEA source: ignore what the firmware sends, keep streaming the
            # sentence (already queued in __init__). Just log the TX byte.
            pass
        else:
            # Loopback/echo peer: queue the byte to be read back.
            self._rx.append(tx)
        printable = chr(tx) if 0x20 <= tx < 0x7F else "."
        print(f"[UartPeerTarget] TX firmware->peer = 0x{tx:02X} '{printable}'",
              flush=True)
        return True, 0

    # --- RX: peer -> firmware ------------------------------------------------
    @bp_handler(["read"])
    def read(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwuart_read(bytecode_t *b)`` — peer drives a byte into ``b->in_data``."""
        b = qemu.get_arg(0)
        rx = self._rx.popleft() if self._rx else 0xFF  # idle line reads as 0xFF
        qemu.write_memory(b + _IN_DATA, 1, rx)
        printable = chr(rx) if 0x20 <= rx < 0x7F else "."
        print(f"[UartPeerTarget] RX peer->firmware = 0x{rx:02X} '{printable}'",
              flush=True)
        return True, 0

    # --- NMEA stretch: arm the raw-MMIO UART1 RX source ----------------------
    @bp_handler(["arm_nmea"])
    def arm_nmea(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Entry hook on ``nmea_decode_handler`` (the ``gps`` command).

        The GPS decoder reads the UART RX path via raw PL011 MMIO, not through
        ``hwuart_read``, so the bytes come from the ``Rp2040Uart1Source``
        peripheral. We arm that source here so the modeled NMEA sentence only
        starts streaming once the firmware actually enters its consume loop
        (it stays empty during mode setup so setup_exc's drain doesn't eat it).
        Returns ``False`` to let ``nmea_decode_handler`` run normally.
        """
        try:
            from halucinator.peripheral_models.bpv5.uart_source import (
                Rp2040Uart1Source)
            Rp2040Uart1Source.arm()
        except Exception as exc:  # noqa: BLE001
            print(f"[UartPeerTarget] arm_nmea failed: {exc}", flush=True)
        return False, 0

    @bp_handler(["div_s32"])
    def div_s32(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """HLE of ``div_s32s32(int32 a, int32 b)`` using the RP2040 SIO HW
        divider (``0xd0000060``). That divider is RAM-backed (unmodeled) here,
        so the real routine returns garbage — which makes minmea's float-field
        scanner produce invalid values and ``minmea_scan`` fail. We compute the
        signed quotient in Python instead.

        ABI: dividend in r0, divisor in r1; quotient returned in r0 (the caller
        also reads the remainder from r1, so we set both).
        """
        def _s32(v: int) -> int:
            v &= 0xFFFFFFFF
            return v - 0x100000000 if v & 0x80000000 else v

        a = _s32(qemu.get_arg(0))
        b = _s32(qemu.get_arg(1))
        if b == 0:
            q, r = 0, a
        else:
            # C truncated (toward-zero) signed division.
            neg = (a < 0) ^ (b < 0)
            q = -(abs(a) // abs(b)) if neg else abs(a) // abs(b)
            r = a - q * b
        try:
            qemu.regs.r1 = r & 0xFFFFFFFF
        except Exception:  # noqa: BLE001
            pass
        return True, q & 0xFFFFFFFF

    @bp_handler(["strtol"])
    def strtol(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """HLE of ``long strtol(const char *nptr, char **endptr, int base)``.

        The firmware's newlib ``strtol`` resolves ``_impure_ptr`` through an
        SRAM table; under emulation that reentrancy state isn't faithful and
        the integer fields (GGA fix-quality, satellite count) come back as
        garbage (INT_MAX). We parse the leading integer in Python instead and
        set ``*endptr`` so minmea's scanner advances correctly.
        """
        nptr = qemu.get_arg(0)
        endptr = qemu.get_arg(1)
        base = qemu.get_arg(2) & 0xFFFFFFFF
        # Read the C string (bounded).
        raw = bytes(qemu.read_memory(nptr, 1, 32, raw=True))
        s = raw.split(b"\x00")[0]
        i = 0
        n = len(s)
        while i < n and s[i] in b" \t\n\r\f\v":
            i += 1
        start = i
        sign = 1
        if i < n and s[i] in b"+-":
            if s[i] == ord("-"):
                sign = -1
            i += 1
        b = base if base else 10
        digits = i
        val = 0
        while i < n:
            c = s[i]
            if ord("0") <= c <= ord("9"):
                d = c - ord("0")
            elif ord("a") <= c <= ord("z"):
                d = c - ord("a") + 10
            elif ord("A") <= c <= ord("Z"):
                d = c - ord("A") + 10
            else:
                break
            if d >= b:
                break
            val = val * b + d
            i += 1
        if i == digits:  # no digits consumed
            i = start
            val = 0
        result = sign * val
        # Clamp to signed 32-bit (strtol returns long; ARM long == 32-bit).
        if result > 0x7FFFFFFF:
            result = 0x7FFFFFFF
        elif result < -0x80000000:
            result = -0x80000000
        if endptr:
            qemu.write_memory(endptr, 4, (nptr + i) & 0xFFFFFFFF)
        return True, result & 0xFFFFFFFF

    @bp_handler(["terminate_gps"])
    def terminate_gps(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Entry hook on ``process_gps(char *buf)`` — NUL-terminate the buffer.

        ``minmea_check`` (and the GGA scanner) require the assembled sentence
        buffer to be NUL-terminated after the trailing CRLF, but
        ``nmea_decode_handler`` stops at ``\\n`` and never writes a NUL, so the
        byte past the sentence is uninitialised stack and the parse fails. The
        buffer pointer is ``process_gps``'s arg0, so we walk to the terminating
        ``\\n`` and drop a ``\\0`` right after it. Returns ``False`` to let
        ``process_gps`` run normally on the now-terminated buffer.
        """
        try:
            buf = qemu.get_arg(0)
            # The sentence starts with '$'; scan for the line-ending '\n', then
            # write a NUL just after it. Cap the scan to the firmware's 79-byte
            # buffer to stay in bounds.
            raw = bytes(qemu.read_memory(buf, 1, 80, raw=True))
            term = False
            for i in range(80):
                if raw[i] == 0x0A:  # '\n'
                    qemu.write_memory(buf + i + 1, 1, 0)
                    term = True
                    break
            if not term:
                qemu.write_memory(buf + 79, 1, 0)
            shown = raw.split(b"\x0a")[0]
            print(f"[UartPeerTarget] process_gps buf=0x{buf:08x} "
                  f"-> {shown!r} (NUL-terminated)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[UartPeerTarget] terminate_gps failed: {exc}", flush=True)
        return False, 0
