# Copyright 2026 Christopher Wright
"""Breakpoint keys must not drop bit 0 on x86.

``set_breakpoint`` stored ``addr & 0xFFFFFFFE`` unconditionally. On 32-bit ARM
that is right -- bit 0 is the Thumb interworking flag, not part of the address.
On x86 instructions are byte-aligned and odd addresses are real, so the mask
broke breakpoints two ways: a bp asked for at an odd address was installed on
its even neighbour (never firing where asked, firing on an unrelated
instruction instead), and the two instructions at ``a`` and ``a|1`` collapsed
onto one key so the same handler fired on both.
"""
import pytest

from halucinator.backends.unicorn_backend import UnicornBackend


def _backend(arch):
    """A backend object with no unicorn engine -- only the address bookkeeping."""
    be = UnicornBackend.__new__(UnicornBackend)
    be.arch_name = arch
    be._bp_addr_mask = 0xFFFFFFFF if arch == "x86" else 0xFFFFFFFE
    be._breakpoints = {}
    be._per_bp_hooks = {}
    be._next_bp_id = 1
    be._fast_bp_active = False
    be._uc = None
    return be


@pytest.mark.parametrize("arch", ["cortex-m3", "arm", "mips", "sparc",
                                  "riscv32", "tricore", "m68k", "powerpc"])
def test_thumb_bit_is_still_masked_off_everywhere_else(arch):
    be = _backend(arch)
    be.set_breakpoint(0x8001)
    assert 0x8000 in be._breakpoints
    assert 0x8001 not in be._breakpoints


def test_x86_keeps_the_odd_address():
    be = _backend("x86")
    be.set_breakpoint(0x8001)
    assert 0x8001 in be._breakpoints, "x86 bp was moved to its even neighbour"
    assert 0x8000 not in be._breakpoints


def test_x86_two_adjacent_breakpoints_stay_distinct():
    """The collision: 0x8000 and 0x8001 must be two breakpoints, not one."""
    be = _backend("x86")
    a = be.set_breakpoint(0x8000)
    b = be.set_breakpoint(0x8001)
    assert a != b
    assert sorted(be._breakpoints) == [0x8000, 0x8001]
    assert be._breakpoints[0x8000] == a
    assert be._breakpoints[0x8001] == b


def test_arm_two_adjacent_addresses_are_deliberately_one_key():
    """The ARM behaviour this mask exists for, kept intact."""
    be = _backend("cortex-m3")
    a = be.set_breakpoint(0x8000)
    b = be.set_breakpoint(0x8001)
    assert list(be._breakpoints) == [0x8000]
    assert be._breakpoints[0x8000] == b and a != b


def test_mask_is_chosen_by_arch_at_construction():
    assert _backend("x86")._bp_addr_mask == 0xFFFFFFFF
    assert _backend("arm")._bp_addr_mask == 0xFFFFFFFE


def test_x86_removal_uses_the_same_key():
    be = _backend("x86")
    bid = be.set_breakpoint(0x8001)
    be.remove_breakpoint(bid)
    assert be._breakpoints == {}
