# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled 1-WIRE target device.

This is the 1-WIRE analogue of ``SpiFlashTarget`` (the SPI keystone in
``bpv5_handlers.py``). The Bus Pirate's 1-WIRE mode is **PIO bit-banged**,
so — per ``INTERFACES.md`` — we do **high-level
emulation at the software leaf helpers** rather than emulating the PIO
state machine:

* ``onewire_reset() -> uint8_t``   — bus reset + presence detect. The
  firmware reads ``uxtb r0``: **0 = device present** (presence pulse seen),
  non-zero = no device. ``hw1wire_start`` (the CLI ``[``) calls this; so do
  the ``ds18b20``/``scan`` demos. We answer 0 (present) and reset our
  per-transaction protocol state.
* ``onewire_tx_byte(uint8_t b)``   — clock one byte onto the wire (MOSI).
  ``b`` arrives in r0. We feed it to the DS18B20 state machine. Used by
  ``hw1wire_write`` (the CLI bare-value byte write) and the demos.
* ``onewire_rx_byte() -> uint8_t``  — clock one byte off the wire (reading
  a 0xFF time-slot). We return the next byte the modeled DS18B20 drives.
  Used by ``hw1wire_read`` (the CLI ``r``/``r:N``) and the demos.

The ABI was RE'd from ``bus_pirate5_rev10.bin`` (Thumb, base 0x10000000):
``hw1wire_write`` loads the byte from ``[ctx+0x14]`` and calls
``onewire_tx_byte``; ``hw1wire_read`` calls ``onewire_rx_byte`` and stores
r0 to ``[ctx+0x18]``; ``hw1wire_start`` calls ``onewire_reset`` and treats
``uxtb r0 == 0`` as success.

Modeled device: a **DS18B20** digital thermometer (Maxim/Dallas 1-Wire).
It answers the standard ROM layer commands and the temperature-conversion
flow the firmware's ``ds18b20`` demo drives end-to-end:

ROM commands (issued after a reset, before a function command):
* 0x33 Read-ROM      — stream the 8-byte ROM id (family 0x28 + serial + CRC).
* 0xCC Skip-ROM      — address all devices (no ROM bytes follow).
* 0x55 Match-ROM     — host streams 8 ROM bytes to select us (consumed).
* 0xF0 Search-ROM    — (acknowledged; full search-triplet bit protocol is
  driven via ``onewire_triplet`` in firmware, out of scope for the byte
  helpers — Read-ROM is the single-drop proof path).

Function commands (after Skip/Match-ROM):
* 0x44 Convert-T        — start a temperature conversion (no data; the
  firmware sleeps, then resets and reads the scratchpad).
* 0xBE Read-Scratchpad  — stream the 9-byte scratchpad: temperature
  LSB-first + config + reserved + a valid Maxim CRC8 over the first 8.
* 0x4E Write-Scratchpad — host streams 3 config bytes (TH, TL, config) —
  consumed; we keep our fixed scratchpad.
* 0x48 Copy / 0xB8 Recall / 0xB4 Read-power — acknowledged.

Temperature: default **+25.0625 °C** → raw 0x0191 (LSB-first 0x91 0x01),
matching the playbook's worked example. The 9-byte scratchpad and its CRC
are computed at construction with the same Maxim CRC8 (poly 0x31, reflected
0x8C) the firmware's ``calc_crc8_buf`` uses, so the demo's CRC check passes.

Override ``temperature_c`` / ``rom_id`` via ``registration_args``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


def _crc8_maxim(data: Sequence[int]) -> int:
    """Dallas/Maxim 1-Wire CRC8 (poly 0x31, reflected form 0x8C).

    Bit-for-bit identical to the firmware's ``calc_crc8_buf`` (which uses the
    reflected ``bics``-with-0x73 / ``lsrs`` lattice). Used so the modeled
    scratchpad and ROM id carry CRCs the firmware accepts.
    """
    crc = 0
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return crc & 0xFF


# DS18B20 family code.
_DS18B20_FAMILY = 0x28


class Ds18b20Target(BPHandler):
    """A modeled DS18B20 1-Wire temperature sensor (single drop)."""

    # Default ROM: family 0x28 + 48-bit serial (CRC byte appended at init).
    DEFAULT_ROM_SERIAL = (0xFF, 0x64, 0x1E, 0x0C, 0x00, 0x00)
    DEFAULT_TEMPERATURE_C = 25.0625

    def __init__(
        self,
        temperature_c: Optional[float] = None,
        rom_id: Optional[Sequence[int]] = None,
    ) -> None:
        super().__init__()

        # --- ROM id (8 bytes: family + 6 serial + CRC8) -----------------
        if rom_id is not None:
            rom = list(int(x) & 0xFF for x in rom_id)
            if len(rom) == 7:  # family + serial, CRC to append
                rom.append(_crc8_maxim(rom))
            assert len(rom) == 8, "rom_id must be 7 (no CRC) or 8 bytes"
        else:
            rom = [_DS18B20_FAMILY, *self.DEFAULT_ROM_SERIAL]
            rom.append(_crc8_maxim(rom))
        self.rom: List[int] = rom

        # --- Scratchpad (9 bytes) for the modeled temperature -----------
        self.temperature_c = (
            self.DEFAULT_TEMPERATURE_C if temperature_c is None else float(temperature_c)
        )
        self.scratchpad: List[int] = self._build_scratchpad(self.temperature_c)

        # --- Per-transaction protocol state -----------------------------
        # _phase: 'rom' (awaiting ROM cmd) -> 'fn' (awaiting function cmd).
        # _resp:  queued bytes the device will drive on the next reads.
        # _consume: count of host bytes to swallow (e.g. Match-ROM/Write-SP).
        self._reset_state()

        print(
            "[Ds18b20Target] modeled DS18B20 attached "
            f"(ROM {' '.join('%02X' % b for b in self.rom)}; "
            f"T={self.temperature_c:+.4f}C; "
            f"scratchpad {' '.join('%02X' % b for b in self.scratchpad)})",
            flush=True,
        )

    # ------------------------------------------------------------------ #
    # device construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_scratchpad(temp_c: float) -> List[int]:
        """9-byte DS18B20 scratchpad encoding ``temp_c`` (12-bit, 1/16 °C).

        Layout: [tempLSB, tempMSB, TH, TL, config, 0xFF, 0x00, 0x10, CRC8].
        """
        raw = int(round(temp_c * 16.0)) & 0xFFFF  # signed 16-bit, 1/16 °C
        sp = [
            raw & 0xFF,          # 0: temperature LSB
            (raw >> 8) & 0xFF,   # 1: temperature MSB
            0x4B,                # 2: TH register (user byte 1) = +75
            0x46,                # 3: TL register (user byte 2) = +70
            0x7F,                # 4: config (12-bit resolution)
            0xFF,                # 5: reserved
            0x00,                # 6: reserved
            0x10,                # 7: reserved
        ]
        sp.append(_crc8_maxim(sp))  # 8: CRC8 over the first 8 bytes
        return sp

    def _reset_state(self) -> None:
        self._phase = "rom"
        self._resp: List[int] = []
        self._consume = 0
        self._romcmd: Optional[int] = None
        self._fncmd: Optional[int] = None

    # ------------------------------------------------------------------ #
    # 1-Wire leaf helpers (the HLE hook surface)
    # ------------------------------------------------------------------ #
    @bp_handler(["reset"])
    def reset(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint8_t onewire_reset(void)`` — bus reset + presence detect.

        Returns NON-ZERO == device present. Both callers agree on this
        polarity: ``hw1wire_start`` takes the error branch on ``r0 == 0``
        (``cmp r0,#0; beq <err>``), and the ds18b20 demo aborts on
        ``r0 == 0`` too — i.e. a 1 is the presence pulse. Every reset
        re-arms the ROM-command phase.
        """
        self._reset_state()
        print("[Ds18b20Target] BUS RESET -> presence pulse (device present)",
              flush=True)
        return True, 1  # non-zero = present

    @bp_handler(["tx_byte"])
    def tx_byte(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void onewire_tx_byte(uint8_t b)`` — host clocks one byte out."""
        b = qemu.get_arg(0) & 0xFF
        self._on_write(b)
        print(f"[Ds18b20Target] TX byte=0x{b:02X} (phase={self._phase})",
              flush=True)
        return True, 0

    @bp_handler(["rx_byte"])
    def rx_byte(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint8_t onewire_rx_byte(void)`` — host clocks one byte in.

        Returns the next byte the modeled DS18B20 drives, or 0xFF (bus idle
        high) when the device has nothing queued.
        """
        rx = self._resp.pop(0) if self._resp else 0xFF
        print(f"[Ds18b20Target] RX byte=0x{rx:02X} (queued left={len(self._resp)})",
              flush=True)
        return True, rx & 0xFF

    # ------------------------------------------------------------------ #
    # DS18B20 protocol state machine
    # ------------------------------------------------------------------ #
    def _on_write(self, b: int) -> None:
        # Swallow bytes a prior command said to consume (Match-ROM id,
        # Write-Scratchpad config), without treating them as commands.
        if self._consume > 0:
            self._consume -= 1
            return

        if self._phase == "rom":
            self._romcmd = b
            if b == 0xCC:        # Skip-ROM: address all, go to function phase
                self._phase = "fn"
            elif b == 0x33:      # Read-ROM: device streams its 8 ROM bytes
                self._resp.extend(self.rom)
                self._phase = "fn"
            elif b == 0x55:      # Match-ROM: host streams 8 ROM bytes
                self._consume = 8
                self._phase = "fn"
            elif b == 0xF0:      # Search-ROM (triplet protocol — ack only)
                self._phase = "fn"
            else:                # Unknown ROM cmd: best-effort advance
                self._phase = "fn"
            return

        # Function-command phase (after the ROM layer selected the device).
        self._fncmd = b
        if b == 0x44:            # Convert-T: start conversion (no data out)
            pass
        elif b == 0xBE:          # Read-Scratchpad: stream the 9 bytes
            self._resp.extend(self.scratchpad)
        elif b == 0x4E:          # Write-Scratchpad: host sends TH, TL, config
            self._consume = 3
        elif b in (0x48, 0xB8, 0xB4):  # Copy / Recall / Read-power: ack
            pass
        # Unknown function command: ignore (device drives idle high).
