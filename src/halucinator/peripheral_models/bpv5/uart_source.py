# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) UART1 RX source peripheral model.

RP2040/bpv5-specific (the PL011 register layout + a default NMEA payload), so
it lives under ``peripheral_models/bpv5/`` rather than the generic module. It is
re-exported from ``peripheral_models.generic`` so the memory-YAML ``emulate:``
name resolution (which looks classes up on the ``generic`` module) still finds
it as ``Rp2040Uart1Source``.
"""
from __future__ import annotations

import logging
from typing import Any

from halucinator.peripheral_models.generic import GenericPeripheral

log = logging.getLogger(__name__)


class Rp2040Uart1Source(GenericPeripheral):
    """A modeled RP2040 UART1 (PL011) RX source for streaming bytes to firmware.

    Some firmware reads the UART RX path via *raw MMIO* on the PL011 data /
    flag registers rather than through a hookable software helper (e.g. the
    Bus Pirate v5 ``gps`` command's ``nmea_decode_handler``, which inlines the
    FR-poll + DR-read loop). For those paths a leaf-function intercept can't
    supply the incoming bytes, so we model the controller registers directly:

    * DR  (data register)  at ``offset 0x00`` — each read pops and returns the
      next queued RX byte (0x00 when the queue is empty).
    * FR  (flag register)  at ``offset 0x18`` — reports RXFE (RX-FIFO-empty,
      bit 4 / 0x10) *clear* while bytes remain so the firmware's poll loop
      proceeds, and *set* (plus TXFE) once the queue drains so it blocks/waits
      cleanly instead of consuming zero bytes. TX-related bits read as
      "ready" so any transmit poll also passes.

    The default payload is a checksum-correct ``$GPGGA,...*hh`` NMEA fix so the
    firmware's minmea decoder reports a position fix. Override with the
    ``sentence`` kwarg (a str/bytes payload) from the memory YAML.

    Registered by adding a ``peripherals:`` region at base ``0x40034000`` in the
    memory YAML; it overlaps the RAM-backed ``io_ram`` region (io_ram maps the
    page first; this peripheral's MMIO hook overlays reads/writes on top).
    """

    # PL011 register offsets.
    _DR = 0x00
    _FR = 0x18
    # FR bits: TXFE=0x80, RXFF=0x40, TXFF=0x20, RXFE=0x10, BUSY=0x08.
    _FR_TX_READY = 0x80          # TX FIFO empty (always ready to send)
    _FR_RX_EMPTY = 0x10          # RXFE set => RX FIFO empty

    # A checksum-correct GPGGA fix (fix quality 1, 8 satellites). This is the
    # canonical minmea test vector, which minmea_parse_gga accepts cleanly.
    _DEFAULT_SENTENCE = (
        b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
    )

    # Class-level singleton so a bp_handler can ``arm`` the source once the
    # firmware actually enters its UART-RX consumer (the ``gps`` command),
    # without serving bytes prematurely during mode setup (whose RX-drain loop
    # would otherwise swallow the sentence). While disarmed the source reports
    # an empty RX FIFO, so setup_exc's drain loop exits immediately.
    instance: "Rp2040Uart1Source | None" = None

    def __init__(self, name: str, address: int, size: int,
                 sentence: Any = None, **kwargs: Any) -> None:
        super().__init__(name, address, size, **kwargs)
        if sentence is None:
            payload = self._DEFAULT_SENTENCE
        elif isinstance(sentence, str):
            payload = sentence.encode("latin-1")
        else:
            payload = bytes(sentence)
        self._payload = payload
        self._rx = bytearray(payload)
        self._pos = 0
        self._armed = False
        Rp2040Uart1Source.instance = self
        log.info("Rp2040Uart1Source: %d-byte NMEA payload queued at 0x%08x",
                 len(self._rx), address)
        print(f"[Rp2040Uart1Source] UART1 RX source attached @ 0x{address:08x} "
              f"({len(self._rx)} bytes: {payload[:16]!r}...)", flush=True)

    @classmethod
    def arm(cls) -> None:
        """Begin serving the queued sentence from the start (called on entry
        to the firmware's UART-RX consumer, e.g. the ``gps`` command)."""
        if cls.instance is not None:
            cls.instance._pos = 0
            cls.instance._armed = True
            print("[Rp2040Uart1Source] armed — streaming NMEA sentence",
                  flush=True)

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD,
                **kwargs: Any) -> int:
        has_data = self._armed and self._pos < len(self._rx)
        if offset == self._FR:
            if has_data:
                # Data available: RXFE clear, TX ready.
                return self._FR_TX_READY
            # Empty/disarmed: RXFE set so the firmware's RX-wait loop blocks
            # (or, in setup, its drain loop exits) cleanly.
            return self._FR_TX_READY | self._FR_RX_EMPTY
        if offset == self._DR:
            if has_data:
                b = self._rx[self._pos]
                self._pos += 1
                printable = chr(b) if 0x20 <= b < 0x7F else "."
                print(f"[Rp2040Uart1Source] DR read -> 0x{b:02X} '{printable}'",
                      flush=True)
                return b
            return 0x00
        # Other registers (control, baud, etc.): benign zero.
        return 0

    def hw_write(self, offset: int, size: int, value: int, pc: int = 0xBAADBAAD,
                 **kwargs: Any) -> bool:
        # TX and config writes are accepted and dropped (RX source only).
        return True
