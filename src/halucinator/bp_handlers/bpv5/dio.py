# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled raw digital-I/O (DIO) pin target.

This is the DIO analogue of the SPI keystone (``SpiFlashTarget``) and the
other fan-out interfaces.  The Bus Pirate's **DIO** mode (``m`` menu #9) is
*raw per-pin GPIO*: the eight BIO pins are driven/sampled directly through
the RP2040 **SIO** block (``0xd0000000``) plus a 74-series shift register,
via the software leaf helpers ``bio_put`` / ``bio_get`` / ``bio_output`` /
``bio_input``.  Per ``INTERFACES.md`` (§2 row 9, §3 BIO row) and the
playbook, the clean HALucinator approach is **high-level emulation at those
leaf helpers**, modeling a per-pin level array in Python rather than
emulating SIO atomic-alias register semantics (the RAM-backed SIO region
does not honour the OUT_SET/OUT_CLR/OE_SET alias writes the firmware uses).

ABI / RE provenance (Thumb, flash base ``0x10000000``), from
``bpv5_addrs.yaml`` + capstone disasm of ``bus_pirate5_rev10.bin``:

* ``bio_put(pin, level)``  @ 0x10005a40 — drive a pin.  ``r0`` = BIO pin
  (0..7), ``r1`` = level (0/1).  Maps pin→GPIO via the table at 0x100db1f8
  (BIO ``n`` → GPIO ``n+8``), then writes SIO ``GPIO_OUT_SET`` (+0x14) when
  level!=0 else ``GPIO_OUT_CLR`` (+0x18).  No return value.
* ``bio_get(pin) -> level``  @ 0x10005a5c — sample a pin.  ``r0`` = BIO pin;
  reads SIO ``GPIO_IN`` (+0x04), returns that pin's bit as 0/1 (``uxtb``).
* ``bio_output(pin)`` @ 0x100059f8 / ``bio_input(pin)`` @ 0x10005a1c —
  set pin direction (SIO ``GPIO_OE_SET`` +0x24 / ``GPIO_OE_CLR`` +0x28).

The DIO mode's three single-char CLI commands (verified live — see
``run_dio_test.bash``) all funnel through these leaf helpers, which is why
hooking the leaves is sufficient and clean:

* ``A <IOx>`` (output HIGH) → ``bio_output(x)`` then ``bio_put(x, 1)``;
  the firmware renders ``IO<x> set to OUTPUT: 1``.
* ``a <IOx>`` (output LOW)  → ``bio_output(x)`` then ``bio_put(x, 0)``;
  the firmware renders ``IO<x> set to OUTPUT: 0``.
* ``@ <IOx>`` (input/read)  → ``bio_input(x)`` then ``bio_get(x)``;
  the firmware renders ``IO<x> set to INPUT: <level>``.

(The ``dio_write``/``dio_read`` mode-vtable symbols are an alternate,
unused-on-this-path slot — the interactive ``A``/``a``/``@`` commands go
straight to the bio leaves, so the leaves are the real seam.)

Modeled behaviour (the proof):

* **Modeled externally-driven input pin** — by default **BIO5 reads HIGH**
  (a distinctive, non-trivial value, matching the firmware help example
  ``Pin 5 input, read value: @ 5``).  So ``@ 5`` returns 1 even though
  nothing was written → the firmware prints ``IO5 set to INPUT: 1``.
* **Write-then-read-back** — ``A 4`` drives BIO4 HIGH (``IO4 set to
  OUTPUT: 1``); a subsequent ``@ 4`` reads it back HIGH (``IO4 set to
  INPUT: 1``).  ``a 4`` drives it LOW (``OUTPUT: 0``); ``@ 4`` then reads
  back LOW (``IO4 set to INPUT: 0``).

Every pin operation is logged (``[DioPinTarget] ...``) so the run captures
the real pin-level traffic for verification.

Override the externally-driven-HIGH pin set via ``registration_args``
(``input_high_pins``: list of BIO pin numbers).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

# BIO pin -> GPIO number mapping (table @ 0x100db1f8: BIO n -> GPIO n+8).
_BIO_GPIO_BASE = 8
_NUM_PINS = 8


class DioPinTarget(BPHandler):
    """Modeled per-pin digital I/O attached to the Bus Pirate v5 DIO mode.

    Holds an 8-entry level array (one bit per BIO pin).  Output writes (via
    ``dio_write`` / ``bio_put``) update it; reads (``bio_get``, driven by
    ``dio_read``) answer from it.  Externally-driven input pins (default
    BIO5) always read HIGH regardless of what was written, modeling a real
    pin pulled high by an off-board source.
    """

    DEFAULT_INPUT_HIGH = (5,)

    def __init__(self, input_high_pins: Optional[Sequence[int]] = None) -> None:
        super().__init__()
        if input_high_pins is None:
            input_high_pins = self.DEFAULT_INPUT_HIGH
        self.input_high = {int(p) & 0x7 for p in input_high_pins}
        # Per-pin driven level (what was last written via A/a or bio_put).
        self.level: List[int] = [0] * _NUM_PINS
        # Per-pin direction: True = output (driven by us / firmware).
        self.is_output: List[bool] = [False] * _NUM_PINS
        print(
            "[DioPinTarget] modeled DIO pins attached "
            f"(externally-driven HIGH input pins: "
            f"{sorted(self.input_high) if self.input_high else 'none'})",
            flush=True,
        )

    # --- read seam: bio_get(pin) -> level -------------------------------
    @bp_handler(["get"])
    def get(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``bio_get(pin) -> level`` — sample a pin; answer from the model.

        Externally-driven input pins read HIGH; otherwise return the level
        last driven onto the pin (read-back of an output, or 0 for a
        floating/undriven pin).  Drives the ``@`` read and ``dio_read``.
        """
        pin = qemu.get_arg(0) & 0x7
        if pin in self.input_high:
            val = 1
            src = "ext-HIGH"
        else:
            val = self.level[pin] & 1
            src = "out" if self.is_output[pin] else "level"
        print(f"[DioPinTarget] bio_get(BIO{pin}) -> {val} "
              f"(GPIO{_BIO_GPIO_BASE + pin}, {src})", flush=True)
        return True, val

    # --- write seam: bio_put(pin, level) --------------------------------
    @bp_handler(["put"])
    def put(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``bio_put(pin, level)`` — drive a pin; record it in the model."""
        pin = qemu.get_arg(0) & 0x7
        level = 1 if (qemu.get_arg(1) & 0xFF) else 0
        self.level[pin] = level
        self.is_output[pin] = True
        print(f"[DioPinTarget] bio_put(BIO{pin}, {level}) "
              f"(GPIO{_BIO_GPIO_BASE + pin})", flush=True)
        return True, 0

    # --- direction seams ------------------------------------------------
    @bp_handler(["output"])
    def output(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``bio_output(pin)`` — mark a pin as a driven output."""
        pin = qemu.get_arg(0) & 0x7
        self.is_output[pin] = True
        print(f"[DioPinTarget] bio_output(BIO{pin})", flush=True)
        return True, 0

    @bp_handler(["input"])
    def input_(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``bio_input(pin)`` — release a pin to high-Z input."""
        pin = qemu.get_arg(0) & 0x7
        self.is_output[pin] = False
        print(f"[DioPinTarget] bio_input(BIO{pin})", flush=True)
        return True, 0
