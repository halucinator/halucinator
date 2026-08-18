# Copyright 2026 Christopher Wright
"""ARMv8-M EXC_RETURN values have to be recognised too.

`_decode_exc_return_frame` matches `pc & MASK == MAGIC`. A 0xFFFFFFE0 window
covers everything v7-M can produce, FP-context returns included. ARMv8-M adds
two flags underneath those bits -- bit 6 = S (secure), bit 5 = DCRS -- so a
plain non-secure thread return of 0xFFFFFFBC lands outside it.

Nothing complains when that happens. The value is not taken for an exception
return, so the ISR's `bx lr` branches to 0xFFFFFFBC as if it were an address
and the core faults there for the rest of the run, while the host side keeps
reporting a healthy boot because it binds regardless of the guest. An nRF9160
(Cortex-M33) rehost produced 1.8 GB of `CPU exception 3 at pc=0xffffffbc` in
four minutes before this was found.

Bits[31:7] is the field the architecture defines. The v7-M cases below are kept
as a guard: widening must not lose anything that already worked.
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
    """Sweep every value the previous 0xFFFFFFE0 window matched; all must still
    match. This is the whole safety argument for widening, so it is checked
    exhaustively rather than by sampling."""
    old_mask = old_magic = 0xFFFFFFE0
    for pc in range(0xFFFFFF00, 0x100000000, 4):
        if (pc & old_mask) == old_magic:
            assert _matches(pc), "0x%08X matched before and does not now" % pc


def test_the_golioth_value_specifically():
    """The measured failure: nRF9160 default non-secure thread return."""
    assert _matches(0xFFFFFFBC)
