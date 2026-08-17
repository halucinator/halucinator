"""Parsing of the x86 ``vectors:`` mapping keys.

These keys come straight from user YAML, so they arrive as ints or as any
string a human might type. The parser runs inside interrupt delivery, where an
uncaught exception takes the whole emulation down -- a config typo must degrade
to "that entry is ignored", never to a traceback.

The specific trap: ``int(s, 0)`` applies Python's integer-*literal* rules, which
forbid a leading zero, so ``"04"`` raised ValueError even though it is an
obvious way to write IRQ 4 in a table aligned with ``"14"``.
"""
from __future__ import annotations

import pytest

from halucinator.backends.irq.delivery import _parse_vector_key


@pytest.mark.parametrize("key,expected", [
    (4, 4),               # YAML int key
    ("4", 4),             # quoted decimal
    ("0x4", 4),           # hex, the natural way to write a vector
    ("0x1f", 31),
    (" 4 ", 4),           # stray whitespace from a quoted key
    ("04", 4),            # THE REGRESSION: base-0 rejects the leading zero
    ("007", 7),
    ("0", 0),             # IRQ 0 is the PC clock — must not be falsy-dropped
])
def test_usable_keys_parse(key, expected):
    assert _parse_vector_key(key) == expected


@pytest.mark.parametrize("key", ["clock", "", "   ", "4.5", None, "0x", True,
                                 False])
def test_unusable_keys_are_ignored_not_raised(key):
    """A bad key yields None so the caller skips it. Booleans are excluded on
    purpose: bool subclasses int, so True would otherwise read as IRQ 1."""
    assert _parse_vector_key(key) is None


def test_a_bad_key_does_not_hide_a_good_one():
    """A junk entry earlier in the mapping must not stop the real vector from
    being found -- dict order is insertion order, so this is the realistic
    case of a commented-out or mistyped row above the one that matters."""
    vectors = {"clock": 0xDEAD, "04": 0xB000, 4: 0xB000}
    resolved = [addr for key, addr in vectors.items()
                if _parse_vector_key(key) == 4]
    assert resolved == [0xB000, 0xB000]
