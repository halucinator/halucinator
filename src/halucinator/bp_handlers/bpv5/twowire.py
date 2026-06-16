# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled 2WIRE and 3WIRE target devices.

These are the 2WIRE/3WIRE analogues of ``SpiFlashTarget`` (the SPI keystone in
``bpv5_handlers.py``) and ``Ds18b20Target`` (the 1-WIRE target). Both Bus
Pirate modes are **PIO bit-banged**, so — per ``INTERFACES.md`` — we do
**high-level emulation at the software leaf
helpers** rather than emulating the PIO state machine. The PIO MMIO
(``0x50200000``) is never touched; the per-byte leaf helpers busy-poll the PIO
FIFO and would otherwise spin forever, so hooking them is both the model seam
*and* a hang fix.

ABI / RE provenance (Thumb, flash base ``0x10000000``), from
``bpv5_addrs.yaml`` + capstone disasm of ``bus_pirate5_rev10.bin``:

2WIRE (menu #7) — separate TX/RX leaf helpers, full byte each:

* ``hw2wire_write(ctx)``  loads the byte from ``[ctx+0x14]``, bit-order
  formats it, ``uxth r0`` and calls ``pio_hw2wire_put16(word)``.  So
  ``pio_hw2wire_put16(uint16_t word)`` — the **outgoing byte is arg0** (masked
  ``& 0xFF`` inside). Returns void.
* ``hw2wire_read(ctx)``   calls ``pio_hw2wire_get16(uint8_t *dst)`` then stores
  ``*dst`` to ``[ctx+0x18]``.  So ``pio_hw2wire_get16(uint8_t *dst)`` — the
  **received byte is written to ``*r0``** (``strb r3,[r0]``). Returns void.
* ``hw2wire_start(ctx)``  -> ``pio_hw2wire_start``  (the CLI ``[``).
* ``hw2wire_stop(ctx)``   -> ``pio_hw2wire_stop``   (the CLI ``]``).
  ``pio_hw2wire_start``/``stop`` push command words into the PIO FIFO and
  busy-poll status — they must be hooked too (they would hang), and they are
  our START/STOP transaction-framing seam.

3WIRE (menu #8) — single full-duplex leaf helper on one data line:

* ``hw3wire_write(ctx)``  formats the byte into a stack buffer, calls
  ``pio_hw3wire_get16(uint8_t *buf)`` and stores ``*buf`` to ``[ctx+0x18]``.
* ``hw3wire_read(ctx)``   stores ``0xFF`` into the buffer, calls
  ``pio_hw3wire_get16(uint8_t *buf)`` and stores ``*buf`` to ``[ctx+0x18]``.
  So ``pio_hw3wire_get16(uint8_t *buf)`` is **full-duplex**: it reads the TX
  byte from ``*r0`` (``ldrb r7,[r0]``), clocks it out, and writes the RX byte
  back to ``*r0`` (``strb r3,[r0]``). A read is just a write of ``0xFF``.
  ``hw3wire_start``/``stop`` toggle a chip-select via ``bio_put`` (raw GPIO,
  no PIO poll) so they do not hang; but they are still our useful CS framing
  seam, so we hook them to reset transaction state.

Both ``hw2wire_setup_exc``/``hw3wire_setup_exc`` call ``pio_hw{2,3}wire_init``
(loads a PIO program via ``pio_add_program`` + configures the SM through PIO
MMIO) — skipped in the config (mirrors ``skip_onewire_init``). 2WIRE's
setup also calls ``pio_hw2wire_reset`` (more PIO MMIO) — also skipped.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


class TwoWireTarget(BPHandler):
    """A modeled generic 2WIRE device (SLE4442-smartcard-style memory).

    The Bus Pirate's 2WIRE mode clocks a full byte out (``pio_hw2wire_put16``)
    and a full byte in (``pio_hw2wire_get16``), framed by START/STOP
    (``pio_hw2wire_start``/``stop`` — the CLI ``[`` / ``]``).  This models a
    simple command/response memory device: the host writes a 1-byte command
    (and optional address), then clocks reads that stream a known response.

    Default behaviour models an **SLE4442-flavoured** smartcard memory:

    * After a START, the first written byte is the command.
        - ``0x30`` READ-MAIN-MEMORY: optional address byte follows, then reads
          stream the 256-byte main memory starting at that address.
        - any other command: subsequent reads stream a fixed ATR-like
          response so a bare ``[0x.. r:N]`` always yields obvious real bytes.
    * A bare ``[r:N]`` with no command first (or after the command/address)
      streams ``self.response`` — a recognizable known sequence.

    The default backing memory is a ramp (byte n == n & 0xFF) so a captured
    read is obviously real data, and the default no-command response is the
    classic SLE4442 ATR ``A2 13 10 91`` — a crisp, recognizable proof.

    Every byte exchanged is logged (``[TwoWireTarget] ...``) so the run
    captures the real bus traffic for verification.  Override ``atr``,
    ``content`` / ``size`` via ``registration_args``.
    """

    # Classic SLE4442 answer-to-reset (4 bytes) — a recognizable known value.
    DEFAULT_ATR = (0xA2, 0x13, 0x10, 0x91)
    CMD_READ_MAIN = 0x30

    def __init__(
        self,
        atr: Optional[Sequence[int]] = None,
        content: Optional[Sequence[int]] = None,
        size: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.atr: List[int] = (
            list(self.DEFAULT_ATR) if atr is None else [int(x) & 0xFF for x in atr]
        )
        size = 256 if size is None else int(size)
        if content is None:
            content = bytes((i & 0xFF) for i in range(size))
        self.mem = bytearray(bytes(content)[:size].ljust(size, b"\x00"))
        self.size = size
        self._reset_state()
        print(
            "[TwoWireTarget] modeled 2WIRE smartcard/memory attached "
            f"(ATR {' '.join('%02X' % b for b in self.atr)}; "
            f"{self.size}-byte ramp memory)",
            flush=True,
        )

    # --- transaction state ---------------------------------------------- #
    def _reset_state(self) -> None:
        self._cmd: Optional[int] = None
        self._got_addr = False
        self._ptr = 0
        # Bytes the device will drive on the next reads.
        self._resp: List[int] = list(self.atr)

    # --- START / STOP framing ------------------------------------------- #
    @bp_handler(["start"])
    def start(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_hw2wire_start(...)`` — the CLI ``[``: begin a transaction."""
        self._reset_state()
        print("[TwoWireTarget] START", flush=True)
        return True, 0

    @bp_handler(["stop"])
    def stop(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``pio_hw2wire_stop(...)`` — the CLI ``]``: end the transaction."""
        print("[TwoWireTarget] STOP", flush=True)
        self._reset_state()
        return True, 0

    # --- byte write (MOSI) ---------------------------------------------- #
    @bp_handler(["put16"])
    def put16(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void pio_hw2wire_put16(uint16_t word)`` — clock one byte out.

        The outgoing byte is ``arg0 & 0xFF``.  We feed it to the device state
        machine (command / address / data) and queue any response.
        """
        b = qemu.get_arg(0) & 0xFF
        if self._cmd is None:
            self._cmd = b
            if b == self.CMD_READ_MAIN:
                # READ MAIN MEMORY: next written byte (if any) is the address;
                # reads stream from there. If reads come before an address,
                # stream from offset 0.
                self._got_addr = False
                self._ptr = 0
                self._resp = list(self.mem)  # default: whole memory from 0
                note = "READ-MAIN"
            else:
                # Unknown/other command: answer with the ATR sequence.
                self._resp = list(self.atr)
                note = "cmd(ATR resp)"
            print(f"[TwoWireTarget] MOSI cmd=0x{b:02X} ({note})", flush=True)
        elif self._cmd == self.CMD_READ_MAIN and not self._got_addr:
            self._got_addr = True
            self._ptr = b % self.size
            self._resp = [self.mem[(self._ptr + i) % self.size]
                          for i in range(self.size)]
            print(f"[TwoWireTarget] MOSI addr=0x{b:02X} "
                  f"(ptr=0x{self._ptr:02X})", flush=True)
        else:
            # Data write into memory at the current pointer.
            self.mem[self._ptr % self.size] = b
            print(f"[TwoWireTarget] MOSI data=0x{b:02X} "
                  f"@0x{self._ptr % self.size:02X}", flush=True)
            self._ptr = (self._ptr + 1) % self.size
        return True, 0

    # --- byte read (MISO) ----------------------------------------------- #
    @bp_handler(["get16"])
    def get16(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void pio_hw2wire_get16(uint8_t *dst)`` — clock one byte in.

        Writes the next modeled device byte to ``*r0``.  When the device has
        nothing queued the bus floats high (``0xFF``).
        """
        dst = qemu.get_arg(0)
        rx = self._resp.pop(0) if self._resp else 0xFF
        qemu.write_memory(dst, 1, rx & 0xFF)
        print(f"[TwoWireTarget] MISO=0x{rx:02X} "
              f"(queued left={len(self._resp)})", flush=True)
        return True, 0


class ThreeWireTarget(BPHandler):
    """A modeled generic 3WIRE (Microwire) device — 93Cxx-style EEPROM.

    The Bus Pirate's 3WIRE mode is half-duplex on a single data line; every
    byte flows through ONE full-duplex leaf helper:

        ``void pio_hw3wire_get16(uint8_t *buf)``
            in:  ``*buf`` = the byte to clock OUT (MOSI)
            out: ``*buf`` = the byte clocked IN  (MISO)

    ``hw3wire_write`` puts the data byte in ``*buf`` before the call; a
    ``hw3wire_read`` puts ``0xFF`` (a dummy/read time-slot) in ``*buf``.  So we
    treat a written ``0xFF`` as "the host is reading" and return the next queued
    device byte; any other written value is a command/address byte fed to the
    Microwire state machine.

    Modeled device: a **93C46-style Microwire EEPROM** (128 bytes).  After CS
    goes high (``hw3wire_start`` -> our ``cs``), the host clocks a command:

        start-bit(1) + opcode(2) + address — Microwire packs these MSB-first
        across the first written bytes.  Rather than reproduce the exact bit
        packing (which the firmware would drive bit-by-bit), we recognize the
        common interactive pattern: the first non-0xFF byte is the command;
        a READ command (high bit set, opcode 10b) arms a streaming read of the
        EEPROM ramp; subsequent ``r``/``0xFF`` reads return the ramp bytes.

    The backing store is a ramp (byte n == n & 0xFF) so a captured read is
    obviously real data, and the device also keeps a fixed recognizable
    "signature" response (``self.response``) it streams for any read after a
    command, guaranteeing a crisp real-byte proof for a bare ``[0x.. r:N]``.

    Every byte exchanged is logged (``[ThreeWireTarget] ...``).  Override
    ``response`` / ``content`` / ``size`` via ``registration_args``.
    """

    # A recognizable known signature a read streams back (distinct from 2WIRE).
    DEFAULT_RESPONSE = (0x93, 0xC4, 0x6E, 0x5A)

    def __init__(
        self,
        response: Optional[Sequence[int]] = None,
        content: Optional[Sequence[int]] = None,
        size: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.signature: List[int] = (
            list(self.DEFAULT_RESPONSE)
            if response is None
            else [int(x) & 0xFF for x in response]
        )
        size = 128 if size is None else int(size)
        if content is None:
            content = bytes((i & 0xFF) for i in range(size))
        self.mem = bytearray(bytes(content)[:size].ljust(size, b"\x00"))
        self.size = size
        self._reset_state()
        print(
            "[ThreeWireTarget] modeled 3WIRE Microwire EEPROM attached "
            f"(signature {' '.join('%02X' % b for b in self.signature)}; "
            f"{self.size}-byte ramp memory)",
            flush=True,
        )

    # --- transaction state ---------------------------------------------- #
    def _reset_state(self) -> None:
        self._cmd: Optional[int] = None
        self._ptr = 0
        self._resp: List[int] = []

    # --- CS framing (hw3wire_start / hw3wire_stop) ---------------------- #
    @bp_handler(["cs"])
    def cs(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hw3wire_start(ctx)`` — CS asserted (the CLI ``[``).

        Re-arms the device for a new command.  (We let the firmware's own
        ``bio_put`` chip-select run; we just reset our protocol state.)  Return
        ``(False, ...)`` would run the original — but we hook to reset state,
        so emulate the side effects: ``hw3wire_start`` only toggles GPIO via
        ``bio_put`` (already spoofed-safe) and stores a timestamp, so skipping
        it is harmless.  Return ``(True, 0)``.
        """
        self._reset_state()
        print("[ThreeWireTarget] CS asserted (START)", flush=True)
        return True, 0

    @bp_handler(["cs_off"])
    def cs_off(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hw3wire_stop(ctx)`` — CS de-asserted (the CLI ``]``)."""
        print("[ThreeWireTarget] CS de-asserted (STOP)", flush=True)
        self._reset_state()
        return True, 0

    # --- full-duplex byte exchange (pio_hw3wire_get16) ------------------ #
    @bp_handler(["xfer"])
    def xfer(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void pio_hw3wire_get16(uint8_t *buf)`` — full-duplex byte.

        ``*buf`` on entry is the MOSI byte (``0xFF`` == the host is reading);
        we overwrite ``*buf`` with the MISO byte the device drives.
        """
        ptr = qemu.get_arg(0)
        tx = qemu.read_memory(ptr, 1) & 0xFF

        if tx != 0xFF:
            # Command / address byte from the host.
            if self._cmd is None:
                self._cmd = tx
                # Microwire READ: start bit + opcode 10b -> high nibble 0b110.
                # Accept the common case (high bit set) as "arm a read"; queue
                # the recognizable signature followed by the ramp memory so a
                # bare `[0x.. r:N]` yields obvious real bytes.
                self._resp = list(self.signature) + list(self.mem)
                self._ptr = 0
                print(f"[ThreeWireTarget] MOSI cmd=0x{tx:02X} "
                      f"(arm read)", flush=True)
            else:
                # Additional command/address byte (e.g. Microwire address).
                self._ptr = tx % self.size
                self._resp = list(self.signature) + [
                    self.mem[(self._ptr + i) % self.size] for i in range(self.size)
                ]
                print(f"[ThreeWireTarget] MOSI addr=0x{tx:02X} "
                      f"(ptr=0x{self._ptr:02X})", flush=True)
            rx = 0xFF  # write phase: bus idle high
        else:
            # Read time-slot: return the next queued device byte.
            rx = self._resp.pop(0) if self._resp else 0xFF
            print(f"[ThreeWireTarget] MISO=0x{rx:02X} "
                  f"(queued left={len(self._resp)})", flush=True)

        qemu.write_memory(ptr, 1, rx & 0xFF)
        return True, 0
