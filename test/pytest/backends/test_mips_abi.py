"""MIPS32 O32 calling-convention tests for MIPSHalMixin (mips and mipsel).

O32 reserves a 16-byte home area at $sp+0..$sp+12 for a0-a3, so the fifth
argument lives at $sp+16. ``set_args`` always wrote there; ``get_arg`` read at
$sp+0 instead, i.e. the a0 home slot -- so an intercept on any function with
more than four arguments silently got a stale word (usually 0) in place of
argument 5. The tests below pin the ABSOLUTE address as well as the round-trip,
because a reader and a writer that are wrong in the same direction still agree
with each other.
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

_RAM = 0x40000000
_SIZE = 0x10000
_SP = _RAM + 0x8000

# O32 home space for a0-a3; the fifth argument starts immediately above it.
_O32_HOME_BYTES = 16


def _backend(arch: str):
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend

    b = UnicornBackend(arch=arch)
    b.add_memory_region(MemoryRegion("ram", _RAM, _SIZE, "rwx"))
    b.init()
    b.write_register("sp", _SP)
    return b


@pytest.mark.parametrize("arch", ["mips", "mipsel"])
def test_binds_the_mips_abi(arch):
    from halucinator.backends.hal_backend import ABI_MIXINS, MIPSHalMixin

    assert ABI_MIXINS.get(arch) is MIPSHalMixin
    assert _backend(arch)._abi is MIPSHalMixin


@pytest.mark.parametrize("arch", ["mips", "mipsel"])
def test_register_args_come_from_a0_a3(arch):
    b = _backend(arch)
    for i in range(4):
        b.write_register(f"a{i}", 0xC0000000 + i)
    assert [b.get_arg(i) for i in range(4)] == [0xC0000000 + i
                                                for i in range(4)]


@pytest.mark.parametrize("arch", ["mips", "mipsel"])
def test_fifth_argument_is_read_from_above_the_home_space(arch):
    """The regression itself: argument 5 is at $sp+16, not $sp+0."""
    b = _backend(arch)
    sentinel_home, wanted = 0xDEAD0000, 0xC0FFEE05
    # Poison the a0 home slot, which is what the buggy read returned.
    b.write_memory(_SP, 4, sentinel_home)
    b.write_memory(_SP + _O32_HOME_BYTES, 4, wanted)
    assert b.get_arg(4) == wanted, "get_arg(4) read the a0 home slot"


@pytest.mark.parametrize("arch", ["mips", "mipsel"])
def test_stack_args_round_trip_and_land_at_the_o32_offsets(arch):
    b = _backend(arch)
    args = [0xB0000000 + i for i in range(7)]
    b.set_args(args)
    assert [b.get_arg(i) for i in range(7)] == args
    # Absolute placement, so reader and writer cannot drift together.
    for i, v in enumerate(args[4:]):
        assert b.read_memory(_SP + _O32_HOME_BYTES + i * 4, 4, 1) == v


@pytest.mark.parametrize("arch", ["mips", "mipsel"])
def test_get_arg_rejects_a_negative_index(arch):
    with pytest.raises(ValueError):
        _backend(arch).get_arg(-1)
