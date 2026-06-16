# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled INFRARED transmitter/receiver target.

This is the INFRARED counterpart to ``SpiFlashTarget`` (the SPI keystone) and
the ``UartPeerTarget`` peer. The Bus Pirate's IR mode (menu #11) is **PIO
bit-banged** through the ``irio_pio_*`` carrier program, so — per
``INTERFACES.md`` — we do **high-level emulation at the
software protocol helpers**, NOT the PIO MMIO state machine.

IR is polymorphic over a 3-entry mode vtable (RAW/aIR, NEC, RC5). The mode
dispatcher ``infrared_write`` advances ``r0 += 0x14`` and tail-calls the active
protocol's TX writer at vtable slot ``+0x08``; ``infrared_periodic`` /
``infrared_macro`` call the RX frame reader at slot ``+0x18`` with a stack
output pointer in ``r0``. For NEC those slots are ``nec_write`` and
``nec_get_frame`` — the clean leaf hook points (one per direction).

ABI (RE'd from ``bus_pirate5_rev10.bin``, Thumb, flash base 0x10000000):

* ``void nec_write(uint32_t *cmd)``  — TX.  ``r0`` points at a 32-bit command
  word.  ``nec_write`` does ``ldr r1,[r0]`` then expands the low two bytes into
  the wire NEC frame ``(~cmd<<24)|(cmd<<16)|(~addr<<8)|addr`` and stores it to
  the PIO TX FIFO.  Crucially, the **input** the firmware encodes is:
      byte0 (``cmd & 0xFF``)        = NEC **address**
      byte1 (``(cmd >> 8) & 0xFF``) = NEC **command**
  We read ``[r0]``, capture address+command, log the wire frame the firmware
  *would* shift out, and return ``(True, 0)`` so the PIO TXF store is skipped.

* ``int nec_get_frame(uint32_t *out)``  — RX (non-blocking), called from the
  IR mode ``infrared_periodic`` loop while at the ``INFRARED-(NEC)>`` prompt.
  ``r0`` points at a 32-bit output slot.  The real routine:
      ldm  ctx,{pio_base, sm}            ; ctx @ 0x2003ac14 (set by nec_rx_init)
      mask = 1 << (sm + 8)
      if (FSTAT[pio_base+4] & mask) return 0     ; RX FIFO empty -> no frame
      word = RXF[pio_base + (sm+8)*4]            ; pop the 32-bit frame
      *out = word;  status = 2
      if (addr == ~addr_inv && cmd == ~cmd_inv)  ; validate NEC frame
          printf("(0x%08x) Address: %d (0x%02x) Command: %d (0x%02x)", ...)
  The NEC wire frame word layout (LSB-first, as the firmware decodes it):
      byte0 = address,  byte1 = ~address,  byte2 = command,  byte3 = ~command

  We want the **firmware's own** decode+print to render, so rather than
  skipping the function we *seed the PIO RX FIFO it reads*.  ``nec_rx_init`` is
  SkipFunc'd (it would spin on real PIO config), so the runtime RX ctx is
  blank; we therefore also fix up the ctx pointer.  On entry our hook:
    1. points ctx @ 0x2003ac14 at a scratch "PIO" block in the halucinator
       RAM region (0x30000000), with sm = 0;
    2. for the first ``rx_repeats`` calls: writes FSTAT = 0 (RX-not-empty bit
       clear) and the RXF slot = the modeled NEC wire frame, then returns
       ``False`` so the REAL ``nec_get_frame`` pops it, decodes, validates, and
       prints the Address/Command line on the CLI;
    3. once exhausted: writes FSTAT with the RX-empty bit SET so the real
       function returns 0 (no frame) and the listen loop stays responsive and
       the test terminates cleanly.

Default modeled code: NEC **address 0x04, command 0x08** (the distinctive code
the playbook calls for).  Override via ``registration_args`` (``address`` /
``command`` / ``rx_repeats``).

Annotations target ``HalBackend`` (the abstract base) so the handler works on
unicorn *and* avatar2 — only ``get_arg`` / ``read_memory`` / ``write_memory``
are used.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# NEC RX context pointer (RAM global, normally populated by nec_rx_init which we
# SkipFunc): holds {pio_base, sm}. nec_get_frame reads FSTAT at [pio_base+4] and
# pops the frame from RXF at [pio_base + (sm+8)*4].
_NEC_RX_CTX = 0x2003AC14
# A scratch "PIO" block in the rwx halucinator RAM region (0x30000000) we point
# pio_base at, so we can present a faithful FSTAT + RXF to the real function.
_FAKE_PIO_BASE = 0x30001000
_FAKE_SM = 0  # mask bit = 1 << (sm + 8) = bit 8
_FSTAT_OFF = 0x04                      # nec_get_frame reads [pio_base + 4]
_RXF_OFF = (_FAKE_SM + 8) * 4          # [pio_base + (sm+8)*4] = +0x20
# FSTAT RX-empty bit for this SM: bit (sm + 8).
_RX_EMPTY_BIT = 1 << (_FAKE_SM + 8)


def _nec_wire_frame(address: int, command: int) -> int:
    """Build the 32-bit NEC frame word the firmware shifts/decodes (LSB-first):

    ``byte0=address, byte1=~address, byte2=command, byte3=~command``.
    Matches both ``nec_write``'s encoder and ``nec_get_frame``'s decoder.
    """
    a = address & 0xFF
    c = command & 0xFF
    return (
        a
        | ((a ^ 0xFF) << 8)
        | (c << 16)
        | ((c ^ 0xFF) << 24)
    ) & 0xFFFFFFFF


class InfraredNecTarget(BPHandler):
    """A modeled NEC IR transmitter sink + receiver source (Bus Pirate IR mode)."""

    DEFAULT_ADDRESS = 0x04
    DEFAULT_COMMAND = 0x08

    def __init__(
        self,
        address: int | None = None,
        command: int | None = None,
        rx_repeats: int = 1,
    ) -> None:
        super().__init__()
        self.address = self.DEFAULT_ADDRESS if address is None else (int(address) & 0xFF)
        self.command = self.DEFAULT_COMMAND if command is None else (int(command) & 0xFF)
        # How many times the RX source injects the modeled frame before it
        # reports an empty FIFO (so the firmware's listen loop terminates).
        self.rx_repeats = max(1, int(rx_repeats))
        self._rx_left = self.rx_repeats
        print(
            "[InfraredNecTarget] modeled NEC IR device attached "
            f"(address=0x{self.address:02X}, command=0x{self.command:02X}; "
            f"RX frame=0x{_nec_wire_frame(self.address, self.command):08X})",
            flush=True,
        )

    # ------------------------------------------------------------------ #
    # TX: firmware -> IR.  Hook nec_write (vtable +0x08 for NEC).
    # ------------------------------------------------------------------ #
    @bp_handler(["tx_debug"])
    def tx_debug(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """DEBUG: trace infrared_write entry to confirm the bytecode path runs."""
        ptr = qemu.get_arg(0)
        try:
            word = qemu.read_memory(ptr + 0x14, 4, 1) & 0xFFFFFFFF
        except Exception:  # noqa: BLE001
            word = -1
        print(f"[InfraredNecTarget] DEBUG infrared_write entered: r0=0x{ptr:08x} "
              f"[r0+0x14]=0x{word:08x}", flush=True)
        return False, 0

    @bp_handler(["tx_frame"])
    def tx_frame(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``nec_write(uint32_t *cmd)`` — capture the NEC frame the firmware emits.

        ``r0`` -> 32-bit command word: byte0=address, byte1=command. We log the
        captured code and the full wire frame, then skip the real PIO TXF store.
        """
        ptr = qemu.get_arg(0)
        word = qemu.read_memory(ptr, 4, 1) & 0xFFFFFFFF
        address = word & 0xFF
        command = (word >> 8) & 0xFF
        frame = _nec_wire_frame(address, command)
        print(
            f"[InfraredNecTarget] TX NEC frame: address=0x{address:02X} "
            f"command=0x{command:02X} -> wire=0x{frame:08X} "
            f"(addr,~addr,cmd,~cmd = "
            f"0x{address:02X} 0x{address ^ 0xFF:02X} "
            f"0x{command:02X} 0x{command ^ 0xFF:02X})",
            flush=True,
        )
        return True, 0

    # ------------------------------------------------------------------ #
    # RX: IR -> firmware.  Hook nec_get_frame (vtable +0x18 for NEC).
    # ------------------------------------------------------------------ #
    @bp_handler(["rx_frame"])
    def rx_frame(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``int nec_get_frame(uint32_t *out)`` — seed a modeled NEC frame.

        Seeds the (faked) PIO RX FIFO the REAL ``nec_get_frame`` reads, then
        returns ``False`` so the firmware pops/decodes/validates/prints the
        frame itself. After ``rx_repeats`` injections it presents an empty FIFO
        so the real function returns 0 and the listen loop stays responsive.
        """
        # Repoint the RX ctx at our scratch PIO block (nec_rx_init was skipped).
        qemu.write_memory(_NEC_RX_CTX, 4, _FAKE_PIO_BASE)
        qemu.write_memory(_NEC_RX_CTX + 4, 4, _FAKE_SM)

        if self._rx_left <= 0:
            # Present an empty RX FIFO: real nec_get_frame returns 0 (no frame).
            qemu.write_memory(_FAKE_PIO_BASE + _FSTAT_OFF, 4, _RX_EMPTY_BIT)
            return False, 0

        self._rx_left -= 1
        frame = _nec_wire_frame(self.address, self.command)
        # FSTAT: RX-not-empty (clear the empty bit); RXF: the modeled frame.
        qemu.write_memory(_FAKE_PIO_BASE + _FSTAT_OFF, 4, 0)
        qemu.write_memory(_FAKE_PIO_BASE + _RXF_OFF, 4, frame)
        print(
            f"[InfraredNecTarget] RX NEC frame seeded: wire=0x{frame:08X} "
            f"-> address=0x{self.address:02X} command=0x{self.command:02X} "
            f"(repeat {self.rx_repeats - self._rx_left}/{self.rx_repeats}); "
            f"firmware nec_get_frame will decode+print",
            flush=True,
        )
        return False, 0  # let the real nec_get_frame run (pops + decodes + prints)
