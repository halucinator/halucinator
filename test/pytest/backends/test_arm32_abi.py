"""ARM32/AAPCS calling-convention tests for ARM32HalMixin.

ARM32 is the default ABI — `_bind_abi` falls back to this mixin for any arch
missing from `ABI_MIXINS` — so a defect here is the widest-reach ABI defect in
the tree.

The bug these pin down: `set_args` pushed stack arguments one word at a time,
placing the FIFTH argument at the highest address and the last at the lowest,
while `get_arg` reads ascending from sp. A round-trip therefore came back
reversed, silently, and only on a call with more than four arguments.
"""
from __future__ import annotations

import pytest

from halucinator.backends.hal_backend import ARM32HalMixin

_SP0 = 0x20008000


class _Fake(ARM32HalMixin):
    """Register/memory model just big enough to exercise the ABI methods."""

    def __init__(self, sp: int = _SP0):
        self.regs = {f"r{i}": 0 for i in range(13)}
        self.regs.update({"sp": sp, "lr": 0, "pc": 0, "cpsr": 0})
        self.mem: dict[int, int] = {}

    def read_register(self, name):
        return self.regs[name]

    def write_register(self, name, value):
        self.regs[name] = value

    def read_memory(self, addr, size, num):
        return self.mem.get(addr, 0)

    def write_memory(self, addr, size, value, num=1, raw=False):
        self.mem[addr] = value


def test_register_args_use_r0_r3():
    f = _Fake()
    f.set_args([1, 2, 3, 4])
    assert [f.regs[f"r{i}"] for i in range(4)] == [1, 2, 3, 4]
    assert [f.get_arg(i) for i in range(4)] == [1, 2, 3, 4]
    assert f.regs["sp"] == _SP0, "no stack args — sp must not move"


@pytest.mark.parametrize("n_extra", [1, 2, 3, 5])
def test_stack_args_round_trip_in_order(n_extra):
    """THE REGRESSION: set_args then get_arg must return what was set, in
    order. The old push-one-word-at-a-time loop returned them reversed."""
    f = _Fake()
    args = [0xA0 + i for i in range(4 + n_extra)]
    f.set_args(args)
    assert [f.get_arg(i) for i in range(len(args))] == args


def test_fifth_argument_is_at_the_final_sp():
    """AAPCS puts the fifth argument AT sp (no home space), ascending from
    there — assert the absolute placement, since a reader and writer that are
    both wrong in the same direction would still agree with each other."""
    f = _Fake()
    f.set_args([1, 2, 3, 4, 0x55, 0x66])
    sp = f.regs["sp"]
    assert f.mem[sp] == 0x55
    assert f.mem[sp + 4] == 0x66


def test_stack_stays_eight_byte_aligned():
    """AAPCS requires an 8-byte-aligned SP at a public interface. An odd number
    of stack words must round the allocation up, with the padding ABOVE the
    arguments so the fifth stays at sp+0."""
    for n_extra in (1, 2, 3, 4, 5):
        f = _Fake()
        f.set_args(list(range(4 + n_extra)))
        assert f.regs["sp"] % 8 == 0, f"sp misaligned with {n_extra} stack args"
        assert f.regs["sp"] <= _SP0 - 4 * n_extra, "allocated too little space"


def test_get_arg_rejects_a_negative_index():
    with pytest.raises(ValueError):
        _Fake().get_arg(-1)
