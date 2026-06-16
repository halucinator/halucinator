# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled JTAG/SWD debug-scan target.

This is the JTAG/SWD counterpart to ``SpiFlashTarget`` in
``bpv5_handlers.py``. Where SPI has a clean per-byte leaf helper
(``hwspi_write_read``), the Bus Pirate's JTAG/SWD mode (a port of the
blueTag / JTAGulator tool) is **bit-banged directly against the RP2040 SIO
GPIO registers** (``0xD0000000``): every TCK edge and every TDO/SWDIO sample
is an inline ``ldr``/``str`` to SIO, with no per-bit software helper to hook.
There is therefore no leaf I/O primitive to intercept the way SPI does.

Modeling level (fall back to the lowest clean routine that yields the
scanned word):

* **JTAG IDCODE scan** (blueTag ``j`` command → ``jtagScan``): this path is
  cleanly HLE-able at the *device-discovery* seam, because the scanned
  IDCODE words are written into the scan **context struct** and the
  firmware's own ``displayDeviceDetails`` renders them. ``jtagScan`` does:

      count = detectDevices(ctx)          ; ctx+0x38 = count
      getDeviceIDs(ctx, count)            ; fills u32 array at ctx+0x3c
      ... validates ctx+0x3c, then displayDeviceDetails() prints each as
          "     [ Device 0 ]  0x%08X "    (fmt @ 0x1006f21c)

  So we intercept the two bit-bang routines and supply the data the
  firmware's own print path consumes:

  - ``detectDevices(ctx) -> count`` — return a positive device count so the
    scan proceeds to read IDCODEs (the real routine would return 0 here
    because GPIO_IN reads back as RAM zeros under emulation).
  - ``getDeviceIDs(ctx, count)`` — write the modeled IDCODE word(s) into the
    ``ctx+0x3c`` array (and re-assert ``ctx+0x38`` = count), then return.
    The firmware reads them straight back and prints them.

* **SWD DPIDR scan** (blueTag ``s`` command → ``swdScan`` → ``swdReadDPIDR``):
  here the 32-bit DPIDR is assembled LSB-first into a *register* and printed
  inline — it is never written to the ctx struct, so there is no field to
  populate from a function-entry hook. We do not model SWD in this handler;
  the JTAG IDCODE path above is the clean, faithful win. (A SWD model would
  require fully reproducing ``swdReadDPIDR``'s multi-printf output, which is
  brittle; documented as the known fallback if JTAG ever becomes intractable.)

Annotations target ``HalBackend`` (the abstract base) so the handler works on
unicorn *and* avatar2 — same ABI discipline (``get_arg`` / ``write_memory``)
as the SPI keystone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# blueTag scan-context struct field offsets (RE'd from jtagScan @ 0x10023500
# and its detectDevices/getDeviceIDs call site @ 0x1002378e):
#   ctx+0x38 : u32  device count (set from detectDevices' return)
#   ctx+0x3c : u32[] IDCODE array (filled by getDeviceIDs, read by jtagScan
#                    and displayDeviceDetails)
_CTX_COUNT_OFF = 0x38
_CTX_IDCODE_OFF = 0x3C


class JtagTarget(BPHandler):
    """A modeled JTAG TAP (scan chain) wired to the Bus Pirate's JTAG mode.

    Answers the firmware's blueTag IDCODE scan with a realistic ARM Cortex-M
    generic JTAG IDCODE so the CLI renders a real device on the scan chain.

    The default identity is the ARM Cortex-M generic TAP IDCODE
    ``0x4BA00477`` (JEDEC: version 0x4, part 0xBA00, manufacturer 0x23/ARM,
    LSB 1 = valid IDCODE per IEEE 1149.1). Override the IDCODE list and the
    reported device count via ``registration_args`` (``idcodes``).
    """

    # ARM Cortex-M generic JTAG-DP TAP IDCODE.
    DEFAULT_IDCODES = (0x4BA00477,)

    def __init__(self, idcodes=None) -> None:
        super().__init__()
        if idcodes is None:
            idcodes = self.DEFAULT_IDCODES
        # Accept ints or hex strings from YAML registration_args.
        norm = []
        for v in idcodes:
            if isinstance(v, str):
                v = int(v, 0)
            norm.append(int(v) & 0xFFFFFFFF)
        self.idcodes = tuple(norm) or self.DEFAULT_IDCODES
        print(
            "[JtagTarget] modeled JTAG scan chain attached ("
            + " ".join(f"IDCODE=0x{c:08X}" for c in self.idcodes)
            + ")",
            flush=True,
        )

    # --- scan-chain plausibility gate ------------------------------------
    @bp_handler(["bypass"])
    def bypass(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint32_t bypassTest(ctx*)`` — BYPASS-register shift-through test.

        ``jtagScan`` brute-forces TCK/TMS/TDI/TDO pin permutations; for each
        candidate it shifts a known pattern through the chain's BYPASS
        register and compares ``bypassTest()`` against the expected pattern in
        ``r4`` (``cmp r0, r4; beq <confirmed>``). Only when they match does it
        commit to the pinout and call ``detectDevices``/``getDeviceIDs``.

        Under emulation the real bit-bang reads GPIO as zero, so the match
        never happens and the scan exhausts every permutation finding nothing.
        We return the *expected* pattern (already in ``r4`` at the compare) so
        the very first candidate pinout is accepted as a valid scan chain and
        the firmware proceeds to read the IDCODE.
        """
        expected = qemu.regs.r4 & 0xFFFFFFFF
        print(f"[JtagTarget] bypassTest -> 0x{expected:08X} (pinout accepted)",
              flush=True)
        return True, expected

    # --- device discovery ------------------------------------------------
    @bp_handler(["detect"])
    def detect(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint32_t detectDevices(ctx*)`` — number of TAPs on the chain.

        The real routine counts devices by bit-banging the scan chain; under
        emulation GPIO_IN reads as zero so it would report 0 (and the scan
        would print "No JTAG devices found"). Return our modeled chain length
        so the firmware proceeds to read the IDCODEs.
        """
        count = len(self.idcodes)
        print(f"[JtagTarget] detectDevices -> {count} device(s)", flush=True)
        return True, count

    @bp_handler(["get_ids"])
    def get_ids(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void getDeviceIDs(ctx*, uint32_t count)`` — fill the IDCODE array.

        r0 = ctx, r1 = device count. The real routine shifts each 32-bit
        IDCODE out of GPIO_IN and stores it into the ``ctx+0x3c`` array; we
        write the modeled IDCODE word(s) there directly (and re-assert the
        count at ``ctx+0x38``), so the firmware's own ``displayDeviceDetails``
        prints ``[ Device 0 ]  0x4BA00477``.
        """
        ctx = qemu.get_arg(0)
        count = len(self.idcodes)
        qemu.write_memory(ctx + _CTX_COUNT_OFF, 4, count)
        for i, code in enumerate(self.idcodes):
            qemu.write_memory(ctx + _CTX_IDCODE_OFF + (i * 4), 4, code)
            print(
                f"[JtagTarget] TAP[{i}] IDCODE -> 0x{code:08X} "
                f"(ctx+0x{_CTX_IDCODE_OFF + i * 4:02x})",
                flush=True,
            )
        return True, 0
