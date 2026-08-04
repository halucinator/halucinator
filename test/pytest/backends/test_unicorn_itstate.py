"""Regression test for the Thumb-2 IT-block corruption caused by MMIO hooks.

Unicorn 2.x leaks the Thumb `ITSTATE` when one of our peripheral hooks fires on
a load or store INSIDE an `it` block: dispatching the hook restores the CPU
state mid-block, which writes the block's ENTRY ITSTATE into the environment,
and nothing advances or clears it afterwards. ITSTATE is a translation-block
flag, so the NEXT block QEMU translates -- usually in the caller, once the
peripheral driver has returned -- is generated as though its first four
instructions were that `it` block, and the ones whose condition now fails are
silently skipped. No fault, no warning, and the missing instruction is in a
different function from the peripheral access.

`UnicornBackend._repair_itstate` clears the stale bits from inside the hook.
The test below is the mechanism in miniature: an `iteet` block whose middle
instruction loads from a modelled peripheral, followed by an `add sp, #N` that
must still execute. Without the repair the `add` is skipped.
"""
from __future__ import annotations

import pytest

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

pytestmark = pytest.mark.skipif(not _HAVE_UNICORN,
                                reason="unicorn-engine not installed")

_FLASH = 0x08000000
_RAM = 0x20000000
_MMIO = 0x40000000
_CODE = 0x08000100
_SP0 = _RAM + 0x1000

# Thumb-2. The leak only becomes visible in the NEXT translation block, so the
# `it` block sits in a leaf function and the instruction that must survive is in
# its caller -- which is exactly the firmware shape this was found in (ChibiOS'
# palReadLineMode reads GPIO registers inside an `iteet pl`; its caller's
# `add sp, #36` was the casualty).
#
# caller:  bl    leaf
#          add   sp, #16      <-- must execute; not in any it block
#          movs  r7, #0x5a
#          b     .
# leaf:    cmp   r1, #0
#          iteet eq
#          ldreq r2, [r0]     <-- inside the it block, reads the modelled register
#          movne r2, #1
#          movne r3, #2
#          moveq r3, #3
#          lsls  r2, r2, #7    <-- straight-line tail: the stale ITSTATE has to
#          and   r2, r2, #0x780    survive to the end of the block to leak out
#          orrs  r2, r3
#          bx    lr
# (assembled with arm-none-eabi-as -mthumb -mcpu=cortex-m4)
_PROGRAM = bytes.fromhex("00f003f804b05a27fee700290dbf0268012202230323"
                         "d20102f4f0621a437047")
_SPIN = _CODE + 8                                # the `b .` the program ends on


def _run(with_repair: bool) -> int:
    """Execute the program with a modelled MMIO read; return the final SP."""
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend

    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", _FLASH, 0x10000, "rwx"))
    b.add_memory_region(MemoryRegion("ram", _RAM, 0x10000, "rw"))
    region = MemoryRegion("periph", _MMIO, 0x1000, "rw")
    region.read_hook = lambda offset, size: 0x5A
    b.add_memory_region(region)
    b.init()
    if not with_repair:
        # Reinstall the same hook without the repair, to show what unicorn does
        # on its own. This is the pre-fix behaviour the test pins down.
        for h in list(b._mmio_hooks.values()):
            b._uc.hook_del(h)
        b._mmio_hooks.clear()

        def _raw(uc, access, addr, size, value, user_data):
            uc.mem_write(addr, (0x5A).to_bytes(size, "little"))
        b._uc.hook_add(unicorn.UC_HOOK_MEM_READ, _raw,
                       begin=_MMIO, end=_MMIO + 0xFFF)

    # HALucinator always has a per-instruction code hook installed (breakpoint
    # detection). It splits translation blocks, which is what lets a stale
    # ITSTATE reach the next block's translation flags -- so the repro needs it.
    b._uc.hook_add(unicorn.UC_HOOK_CODE, lambda uc, a, sz, ud: None)
    b._uc.mem_write(_CODE, _PROGRAM)
    b.write_register("sp", _SP0)
    b.write_register("r0", _MMIO)      # the it-block load targets the peripheral
    b.write_register("r1", 0)          # eq -> the `ldreq` runs
    b.write_register("lr", _CODE | 1)
    b._uc.emu_start(_CODE | 1, _SPIN, count=20)
    return b.read_register("sp")


def test_it_block_mmio_read_does_not_skip_following_instructions():
    """With the repair, `add sp, #16` after the it block still executes."""
    assert _run(with_repair=True) == _SP0 + 16


def test_unrepaired_hook_reproduces_the_skip():
    """Documents the unicorn behaviour being worked around: the same program,
    same hook, no repair -- the `add sp, #16` is silently skipped."""
    assert _run(with_repair=False) == _SP0, (
        "unicorn no longer leaks ITSTATE across an MMIO hook; "
        "_repair_itstate may be removable -- re-verify against the ArduPilot "
        "device before dropping it")
