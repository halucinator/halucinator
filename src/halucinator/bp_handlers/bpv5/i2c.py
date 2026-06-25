# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) I2C target-device intercepts.

This file is kept *separate* from ``bpv5_handlers.py`` so the I2C bring-up
merges conflict-free with the SPI keystone and the other fan-out interfaces.

Modeling level — HLE at the **PIO software leaf helpers** (not the PIO MMIO).
The Bus Pirate's I2C mode is PIO-bit-banged; every byte/frame ultimately flows
through four leaf functions in flash, which both the interactive CLI mode
(``hwi2c_start``/``hwi2c_write``/``hwi2c_read``/``hwi2c_stop``) *and* the
``i2c_search_addr`` bus-scan call:

* ``pio_i2c_start_timeout(pio)``            — drive a START.  ret 0=ok, 2=timeout.
* ``pio_i2c_write_timeout(byte, pio)``      — clock one byte out (MOSI).
      ``r0`` = the byte (on the first byte after START this is the 7-bit
      address shifted left with the R/W bit in bit0).  Returns the sampled
      ACK bit: **0 = ACK (SDA pulled low by the slave), 1 = NACK**, 2 = timeout.
* ``pio_i2c_read_timeout(uint8_t *dst, ack)`` — clock one byte in (MISO),
      store it to ``*dst``.  ``r0`` = destination pointer, ``r1`` = the ACK the
      *master* will drive after the byte.  Returns 0=ok, 2=timeout.
* ``pio_i2c_stop_timeout(pio)``             — drive a STOP.  ret 0=ok, 2=timeout.

By answering at these four leaves we satisfy BOTH the address scan (which needs
a real ACK at 0x50) AND a read transaction (which needs modeled bytes back).
The PIO MMIO state machine is never touched.

ABI / RE provenance (Thumb, flash base 0x10000000), from ``bpv5_addrs.yaml`` +
capstone disasm:

* ``pio_i2c_write_timeout`` @ 0x100131c8:  ``lsls r0,#1; orrs r0,#1`` builds the
  9-bit word, runs the transaction, then ``r0 = sampled & 1`` → 0=ACK / 1=NACK.
* ``pio_i2c_read_timeout``  @ 0x100131e8:  ``movs r4,r0`` (saves dst ptr),
  transacts, ``strb r3,[r4]`` stores the received byte to ``*dst``.
* ``i2c_search_check_addr`` @ 0x1001399c:  start → ``pio_i2c_write_timeout(addr)``
  → ``beq`` on r0==0 (ACK) marks the device present; on ACK it reads one byte.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

# Leaf-helper return codes (from the firmware).
_ACK = 0      # slave pulled SDA low
_NACK = 1     # no slave / end-of-read
_OK = 0       # start/stop/read success
_TIMEOUT = 2  # bus hung (never returned by the model)


class I2cEepromTarget(BPHandler):
    """A modeled 24Cxx I2C EEPROM wired to the Bus Pirate's HW-I2C mode.

    Replaces the I2C *spoof* (PIO MMIO / timeout helpers) with a real **target
    device** answering at the four PIO I2C leaf helpers.  A Python EEPROM model
    supplies the MISO data and the ACK/NACK handshake.

    Default identity: a **24C02** (256 bytes, single-byte word address) at
    7-bit address **0x50** (so the 8-bit control bytes are 0xA0 write / 0xA1
    read — the classic ``[0xA0 0x00 [0xA1 r:2]`` transaction).  Override the
    7-bit address, size, or backing content via ``registration_args``.

    Transaction state machine (reset on START and STOP):

    * On START the next write byte is interpreted as ``addr<<1 | rw``.
        - If the 7-bit address matches us we ACK (return 0); else NACK (1) so an
          ``i2c_search_addr`` scan reports only our address as present.
        - ``rw`` selects the phase: 0 → we expect a word-address byte next
          (write phase); 1 → subsequent reads stream from the current pointer.
    * In the write phase the first data byte after the control byte sets the
      internal word-address pointer; further writes store into the array.
    * In the read phase each ``pio_i2c_read_timeout`` returns
      ``content[pointer++]`` (auto-incrementing, wrapping the page like real
      24Cxx parts).

    Every byte exchanged is logged (``[I2cEepromTarget] ...``) so the run
    captures the real bus traffic for verification.
    """

    DEFAULT_ADDR7 = 0x50  # 24C02 default A2:A0 = 000

    def __init__(self, addr7=None, size=None, content=None) -> None:
        super().__init__()
        self.addr7 = self.DEFAULT_ADDR7 if addr7 is None else int(addr7)
        size = 256 if size is None else int(size)
        if content is None:
            # Recognizable ramp so a captured read is obviously real data, not
            # zeros: byte n == n & 0xFF.
            content = bytes((i & 0xFF) for i in range(size))
        self.mem = bytearray(content[:size].ljust(size, b"\x00"))
        self.size = size
        self._reset()
        print(
            f"[I2cEepromTarget] modeled 24Cxx EEPROM attached "
            f"(7-bit addr {self.addr7:#04x}; control {self.addr7 << 1:#04x} W / "
            f"{(self.addr7 << 1) | 1:#04x} R; {self.size} bytes)",
            flush=True,
        )

    # --- transaction state ----------------------------------------------
    def _reset(self) -> None:
        # Are we the addressed slave for the current (sub)transaction?
        self._selected = False
        # Phase: None until a control byte arrives; 'W' or 'R' after.
        self._phase = None
        # Have we consumed the word-address byte yet in the write phase?
        self._got_wordaddr = False
        # Auto-incrementing internal byte pointer.
        self._ptr = 0

    # --- START / STOP framing -------------------------------------------
    @bp_handler(["start"])
    def start(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_i2c_start_timeout(pio)`` — a (repeated) START.

        Per the I2C protocol a repeated-START re-opens the control-byte phase
        but does NOT clear the word-address pointer (that's what makes
        ``[0xA0 0x00 [0xA1 r:2]`` read from offset 0).  So we re-arm for a new
        control byte while preserving ``self._ptr``.
        """
        self._selected = False
        self._phase = None
        self._got_wordaddr = False
        print("[I2cEepromTarget] START", flush=True)
        return True, _OK

    @bp_handler(["restart"])
    def restart(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_i2c_restart_timeout(pio)`` — a repeated START.

        Emitted by ``hwi2c_start`` when a transaction is already open (the
        nested ``[`` in ``[0xA0 0x00 [0xA1 r:2]``). Like a fresh START it
        re-opens the control-byte phase but preserves the word-address pointer
        so the read streams from where the write phase left it. Must be hooked:
        the firmware's real ``pio_i2c_restart_timeout`` polls PIO MMIO (the
        logger catch-all) and would hang waiting on a FIFO bit that never sets.
        """
        self._selected = False
        self._phase = None
        self._got_wordaddr = False
        print("[I2cEepromTarget] RESTART", flush=True)
        return True, _OK

    @bp_handler(["stop"])
    def stop(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_i2c_stop_timeout(pio)`` — STOP: end the transaction fully."""
        print("[I2cEepromTarget] STOP", flush=True)
        self._reset()
        return True, _OK

    # --- byte write (MOSI) ----------------------------------------------
    @bp_handler(["write"])
    def write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_i2c_write_timeout(byte, pio) -> ack`` — master clocks a byte out.

        Returns 0 (ACK) / 1 (NACK) exactly as the real slave would, so both the
        address scan and the write phase behave correctly.
        """
        byte = qemu.get_arg(0) & 0xFF

        if self._phase is None:
            # This is the control byte: addr<<1 | rw.
            target7 = (byte >> 1) & 0x7F
            rw = byte & 1
            if target7 == self.addr7:
                self._selected = True
                self._phase = "R" if rw else "W"
                self._got_wordaddr = False
                ack = _ACK
            else:
                self._selected = False
                ack = _NACK
            print(
                f"[I2cEepromTarget] MOSI control=0x{byte:02X} "
                f"(addr=0x{target7:02X} {'R' if rw else 'W'}) -> "
                f"{'ACK' if ack == _ACK else 'NACK'}",
                flush=True,
            )
            return True, ack

        if not self._selected:
            # Not for us — NACK any stray bytes.
            print(f"[I2cEepromTarget] MOSI=0x{byte:02X} (not selected) -> NACK",
                  flush=True)
            return True, _NACK

        # Write phase data byte.
        if not self._got_wordaddr:
            self._ptr = byte % self.size
            self._got_wordaddr = True
            print(f"[I2cEepromTarget] MOSI word-addr=0x{byte:02X} "
                  f"(ptr=0x{self._ptr:02X}) -> ACK", flush=True)
        else:
            self.mem[self._ptr] = byte
            print(f"[I2cEepromTarget] MOSI data=0x{byte:02X} "
                  f"@0x{self._ptr:02X} -> ACK", flush=True)
            self._ptr = (self._ptr + 1) % self.size
        return True, _ACK

    # --- byte read (MISO) -----------------------------------------------
    @bp_handler(["read"])
    def read(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_i2c_read_timeout(uint8_t *dst, ack)`` — master clocks a byte in.

        Stores the next modeled EEPROM byte to ``*dst`` and returns 0 (ok).
        """
        dst = qemu.get_arg(0)
        if self._selected and self._phase == "R":
            val = self.mem[self._ptr]
            self._ptr = (self._ptr + 1) % self.size
        else:
            # No device addressed for read — bus floats high (0xFF).
            val = 0xFF
        qemu.write_memory(dst, 1, val)
        print(f"[I2cEepromTarget] MISO=0x{val:02X} @0x{(self._ptr - 1) & 0xFF:02X}",
              flush=True)
        return True, _OK
