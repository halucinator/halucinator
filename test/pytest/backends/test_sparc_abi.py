"""SPARC V8 (LEON) calling-convention tests for SPARCHalMixin.

The bug these pin down is silent: ``_bind_abi`` resolves the ABI with
``ABI_MIXINS.get(arch, ARM32HalMixin)``, and because the fallback *is*
ARM32HalMixin the rebinding branch is skipped entirely -- so an arch missing
from the table gets the ARM ABI with no warning, and the first intercept to
read an argument dies with ``ValueError: Unknown register: 'r0'`` deep inside
a handler.
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


def _backend():
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend

    b = UnicornBackend(arch="sparc")
    b.add_memory_region(MemoryRegion("ram", _RAM, _SIZE, "rwx"))
    b.init()
    b.write_register("sp", _SP)
    return b


def test_sparc_binds_its_own_abi_not_the_arm_fallback():
    from halucinator.backends.hal_backend import ABI_MIXINS, SPARCHalMixin

    assert ABI_MIXINS.get("sparc") is SPARCHalMixin, (
        "sparc missing from ABI_MIXINS falls back to ARM32HalMixin silently")
    assert _backend()._abi is SPARCHalMixin


def test_register_args_come_from_the_out_registers():
    """%o0-%o5 carry the first six arguments at the callee's entry, before
    its `save` rotates the window."""
    b = _backend()
    for i in range(6):
        b.write_register(f"o{i}", 0xA0000000 + i)
    assert [b.get_arg(i) for i in range(6)] == [0xA0000000 + i
                                                for i in range(6)]


def test_stack_args_round_trip_through_the_92_byte_bias():
    """Seventh argument onward live at %sp+92 -- past the 64-byte register
    window save area, the aggregate-return slot and the %o0-%o5 home space.
    set_args and get_arg must agree on that address."""
    b = _backend()
    args = [0xB0000000 + i for i in range(9)]
    b.set_args(args)
    assert [b.get_arg(i) for i in range(9)] == args
    # And the stack words really are where the ABI says, not at %sp.
    assert b.read_memory(_SP + 92, 4, 1) == args[6]


def test_return_address_carries_the_plus_eight_bias():
    """%o7 holds the address OF THE `call`; control resumes at %o7+8, past
    the call and its delay slot. Losing the bias re-enters the interposed
    function forever."""
    b = _backend()
    target = _RAM + 0x1234
    b.set_ret_addr(target)
    assert b.read_register("o7") == target - 8
    assert b.get_ret_addr() == target


def test_get_arg_rejects_a_negative_index():
    with pytest.raises(ValueError):
        _backend().get_arg(-1)
