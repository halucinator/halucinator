"""A modelled MMIO read must land in the GUEST's byte order.

UnicornBackend serves a modelled read by writing the model's value into the
mapped page and letting the guest's load complete, so the bytes have to be laid
out the way the guest will interpret them. The hook hardcoded ``"little"``,
which byte-swapped every read wider than one byte on a big-endian target: a
model returning 0xFFFFF3F8 was seen by the firmware as 0xF8F3FFFF.

Byte-sized reads were unaffected, which is why this survived -- a driver that
polls a status register one byte at a time never produces a multi-byte modelled
read, so the bug only appears once a model serves a real 32-bit register.

These tests cover BOTH directions, because the fix touches every big-endian
target that already existed (mips, powerpc), not only the SPARC support it
arrived with.
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

_CODE = 0x00400000
_MMIO = 0x1F000000
_MODELLED = 0xFFFFF3F8          # asymmetric: reads differently byte-swapped

# One 32-bit load per arch: (encoding, address register, destination register).
#   mips: lw $t0, 0($t1)  -- opcode 0x23, rs=$t1(9), rt=$t0(8), offset 0
#   arm : ldr r0, [r1]
_LOAD_WORD = {
    "mips": (0x8D280000, "t1", "t0"),
    "arm":  (0xE5910000, "r1", "r0"),
}


def _run_modelled_word_read(arch: str) -> int:
    """Execute one 32-bit load from a modelled region; return what the guest
    got in its destination register."""
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend
    from halucinator.backends.unicorn_backend import _ARCH_MAP

    insn, addr_reg, dest_reg = _LOAD_WORD[arch]
    is_be = _ARCH_MAP[arch][3]

    b = UnicornBackend(arch=arch)
    b.add_memory_region(MemoryRegion("code", _CODE, 0x1000, "rwx"))
    region = MemoryRegion("periph", _MMIO, 0x1000, "rw")
    region.read_hook = lambda offset, size: _MODELLED
    b.add_memory_region(region)
    b.init()

    # The INSTRUCTION is stored in the guest's byte order too, which is a
    # separate concern from the modelled value under test.
    b._uc.mem_write(_CODE, insn.to_bytes(4, "big" if is_be else "little"))
    b.write_register(addr_reg, _MMIO)
    b._uc.emu_start(_CODE, _CODE + 4, count=1)
    return b.read_register(dest_reg)


def test_big_endian_guest_sees_the_value_the_model_returned():
    """mips is big-endian. Before the fix this returned 0xF8F3FFFF."""
    got = _run_modelled_word_read("mips")
    assert got == _MODELLED, (
        f"big-endian guest read 0x{got:08X}, model returned 0x{_MODELLED:08X}"
        " — the modelled value was byte-swapped on the way in")


def test_little_endian_guest_is_unchanged():
    """arm is little-endian and must keep working exactly as before -- the fix
    has to follow the target's endianness, not byte-swap unconditionally."""
    got = _run_modelled_word_read("arm")
    assert got == _MODELLED


@pytest.mark.parametrize("arch,expect_be", [
    ("mips", True), ("powerpc", True), ("ppc64", True), ("sparc", True),
    ("x86", False), ("arm", False), ("cortex-m3", False),
])
def test_arch_table_endianness_flags(arch, expect_be):
    """The hook reads its byte order straight off this flag, so an arch added
    with the wrong one silently corrupts every modelled register it serves."""
    from halucinator.backends.unicorn_backend import _ARCH_MAP

    assert _ARCH_MAP[arch][3] is expect_be
