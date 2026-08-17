# Copyright 2026 Christopher Wright
"""EXC_RETURN matching must cover FP-context and ARMv8-M values.

``_decode_exc_return_frame`` matched the top nibble
(``pc & 0xFFFFFFF0 == 0xFFFFFFF0``), which misses two whole classes of legal
EXC_RETURN: FP-context returns (bit 4 clear -> 0xFFFFFFE1 / E9 / ED) and every
ARMv8-M value, since bit 6 = S and bit 5 = DCRS sit below the v7-M flag bits
(a default non-secure thread return is 0xFFFFFFBC).

The consequence is silent. The value is not recognised as an exception return,
so the ISR's ``bx lr`` executes as an ordinary branch to 0xFFFFFFBC and the
core faults there forever -- while ``booted`` still reports true, because the
host-side seam binds regardless of what the guest is doing. Measured on
device-golioth-nrf9160 (nRF9160, Cortex-M33): 1.8 GB of
``CPU exception 3 at pc=0xffffffbc`` in four minutes.

Bits[31:7] is the architecturally defined field and is a strict superset of
the v7-M window, so widening cannot affect a v7-M target.
"""
import pytest

from halucinator.backends.irq.in_process import InProcessIrqMixin


# (name, EXC_RETURN value) -- ARMv7-M, ARMv8-M non-secure, ARMv8-M secure.
V7M = [
    ("handler MSP",            0xFFFFFFF1),
    ("thread MSP",             0xFFFFFFF9),
    ("thread PSP",             0xFFFFFFFD),
    ("handler MSP, FP frame",  0xFFFFFFE1),
    ("thread MSP, FP frame",   0xFFFFFFE9),
    ("thread PSP, FP frame",   0xFFFFFFED),
]
V8M = [
    ("v8-M NS thread PSP",     0xFFFFFFBC),
    ("v8-M NS thread MSP",     0xFFFFFFB8),
    ("v8-M NS handler MSP",    0xFFFFFFB0),
    ("v8-M NS thread PSP, FP", 0xFFFFFFAC),
    ("v8-M S thread PSP",      0xFFFFFFFC),
    ("v8-M NS, DCRS=0",        0xFFFFFF9C),
]
NOT_EXC_RETURN = [
    ("ordinary code",          0x00008001),
    ("ram address",            0x20001234),
    ("just below the window",  0xFFFFFF7C),
    ("unmapped high, not ER",  0xF7FFFFFD),
]

MASK = InProcessIrqMixin._EXC_RETURN_MASK
MAGIC = InProcessIrqMixin._EXC_RETURN_MAGIC


def _matches(pc):
    return (pc & MASK) == MAGIC


@pytest.mark.parametrize("name,val", V7M, ids=[n for n, _ in V7M])
def test_v7m_values_still_recognised(name, val):
    assert _matches(val), "%s (0x%08X) is no longer an exception return" % (name, val)


@pytest.mark.parametrize("name,val", V8M, ids=[n for n, _ in V8M])
def test_v8m_values_recognised(name, val):
    assert _matches(val), "%s (0x%08X) not recognised as an exception return" % (name, val)


@pytest.mark.parametrize("name,val", NOT_EXC_RETURN, ids=[n for n, _ in NOT_EXC_RETURN])
def test_ordinary_addresses_are_not_exception_returns(name, val):
    assert not _matches(val), "%s (0x%08X) was taken for an exception return" % (name, val)


def test_window_is_bits_31_to_7():
    assert MASK == 0xFFFFFF80
    assert MAGIC == 0xFFFFFF80


def test_window_is_a_strict_superset_of_the_v7m_window():
    """Widening must not have dropped anything the old window matched."""
    old_mask = old_magic = 0xFFFFFFF0
    for pc in range(0xFFFFFF00, 0x100000000, 4):
        if (pc & old_mask) == old_magic:
            assert _matches(pc), "0x%08X matched before and does not now" % pc


def test_the_golioth_value_specifically():
    """The measured failure: nRF9160 default non-secure thread return."""
    assert _matches(0xFFFFFFBC)
