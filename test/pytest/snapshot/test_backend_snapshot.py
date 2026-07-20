"""Layer-1 backend snapshot tests: the generic fallback (HalBackend) and the
native Unicorn override.

The load-bearing assertion is BYTE-IDENTICAL resume: snapshot, mutate everything
reachable (all RAM + every register), restore, and assert the full memory image
and register file are bit-for-bit what they were at snapshot time.
"""
import struct

import pytest

from halucinator.backends.hal_backend import (
    HalBackend,
    MemoryRegion,
    Snapshot,
    SnapshotError,
)

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

FLASH_BASE = 0x08000000
RAM_BASE = 0x20000000
FLASH_SIZE = 0x10000
RAM_SIZE = 0x8000


# ---------------------------------------------------------------------------
# A minimal in-memory backend to exercise the GENERIC fallback save/restore.
# ---------------------------------------------------------------------------

class DictBackend(HalBackend):
    """Backend backed by a python dict of byte arrays — no real CPU. Enough to
    drive HalBackend.save_state/restore_state (the generic path)."""

    def __init__(self):
        self._regions = [MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw")]
        self._mem = bytearray(RAM_SIZE)
        self._regs = {name: 0 for name in self.list_registers()}

    # -- memory --
    def read_memory(self, addr, size, num_words=1, raw=False):
        off = addr - RAM_BASE
        n = size * num_words
        data = bytes(self._mem[off:off + n])
        if raw:
            return data
        return int.from_bytes(data, "little")

    def write_memory(self, addr, size, value, num_words=1, raw=False):
        off = addr - RAM_BASE
        if raw:
            data = bytes(value)
        else:
            data = int(value).to_bytes(size * num_words, "little")
        self._mem[off:off + len(data)] = data
        return True

    def read_register(self, register):
        return self._regs[register]

    def write_register(self, register, value):
        self._regs[register] = value

    # -- unused abstract-ish control methods for this test --
    def set_breakpoint(self, addr, **kw):  # pragma: no cover - unused
        return 0

    def remove_breakpoint(self, bp_id):  # pragma: no cover - unused
        return True

    def cont(self, *a, **k):  # pragma: no cover - unused
        pass

    def step(self, *a, **k):  # pragma: no cover - unused
        pass

    def init(self):  # pragma: no cover - unused
        pass

    def add_memory_region(self, region):  # pragma: no cover - unused
        self._regions.append(region)

    def stop(self, *a, **k):  # pragma: no cover - unused
        pass


class TestGenericFallback:
    def test_round_trip_reverts_memory_and_regs(self):
        b = DictBackend()
        b.write_memory(RAM_BASE, 1, b"snapshot-me", 11, raw=True)
        b.write_register("r0", 0x11111111)

        snap = b.save_state()
        assert isinstance(snap, Snapshot)
        assert snap.backend_type == "DictBackend"
        assert snap.version == HalBackend.SNAPSHOT_VERSION

        # Scribble over both memory and registers.
        b.write_memory(RAM_BASE, 1, b"CLOBBEREDXX", 11, raw=True)
        b.write_register("r0", 0xDEADBEEF)

        assert b.restore_state(snap) is True
        assert b.read_memory(RAM_BASE, 1, 11, raw=True) == b"snapshot-me"
        assert b.read_register("r0") == 0x11111111

    def test_restore_rejects_wrong_backend_type(self):
        b = DictBackend()
        b.write_register("r0", 7)
        alien = Snapshot(backend_type="SomeOtherBackend", version=1,
                         data={"regs": {"r0": 999}, "mem": []})
        assert b.restore_state(alien) is False
        assert b.read_register("r0") == 7  # unchanged — no mutation on mismatch

    def test_restore_rejects_wrong_version(self):
        b = DictBackend()
        b.write_register("r0", 7)
        snap = b.save_state()
        snap.version = 999
        assert b.restore_state(snap) is False
        assert b.read_register("r0") == 7

    def test_save_raises_when_no_regions(self):
        b = DictBackend()
        b._regions = []
        with pytest.raises(SnapshotError):
            b.save_state()

    def test_restore_returns_false_when_memory_write_fails(self):
        """A rejected memory write must make restore_state return False
        (whole-or-nothing) instead of silently reporting success."""
        b = DictBackend()
        b.write_register("r0", 0x1234)
        snap = b.save_state()

        # Make the next write_memory fail.
        b.write_memory = lambda *a, **k: False
        assert b.restore_state(snap) is False


# ---------------------------------------------------------------------------
# Unicorn native path.
# ---------------------------------------------------------------------------

pytestmark_uc = pytest.mark.skipif(
    not _HAVE_UNICORN, reason="unicorn-engine not installed")


def _make_unicorn():
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", FLASH_BASE, FLASH_SIZE, "rwx"))
    b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
    b.init()
    return b


def _full_dump(b):
    """Byte-exact machine image: every mapped region's bytes + every register."""
    mem = {base: bytes(b._uc.mem_read(base, end - base + 1))
           for (base, end, _p) in b._uc.mem_regions()}
    regs = {name: b.read_register(name) for name in b.list_registers()}
    return mem, regs


@pytestmark_uc
class TestUnicornNative:
    def test_capability_probes(self):
        b = _make_unicorn()
        assert b.can_snapshot() is True
        assert b.snapshot_is_fast() is True

    def test_round_trip_byte_identical(self):
        b = _make_unicorn()
        # Seed distinct patterns into flash + ram and set registers.
        b.write_memory(RAM_BASE, 1, bytes(range(256)) * 4, 1024, raw=True)
        b.write_memory(FLASH_BASE, 1, b"\xAB" * 512, 512, raw=True)
        b.write_register("r0", 0xCAFEBABE)
        b.write_register("r5", 0x00C0FFEE)
        b.write_register("sp", RAM_BASE + 0x2000)
        b.write_register("pc", FLASH_BASE + 0x100)

        snap = b.save_state()
        before_mem, before_regs = _full_dump(b)

        # Corrupt everything we can reach.
        b.write_memory(RAM_BASE, 1, b"\x00" * 1024, 1024, raw=True)
        b.write_memory(FLASH_BASE, 1, b"\xFF" * 512, 512, raw=True)
        b.write_register("r0", 0)
        b.write_register("r5", 0)
        b.write_register("sp", RAM_BASE + 0x10)
        b.write_register("pc", FLASH_BASE)

        assert b.restore_state(snap) is True
        after_mem, after_regs = _full_dump(b)

        assert after_mem == before_mem, "RAM/flash not byte-identical after restore"
        assert after_regs == before_regs, "registers not identical after restore"

    def test_deterministic_resume_executes_identically(self):
        """Snapshot at a PC, run a fixed Thumb sequence, capture state; restore
        and run the SAME sequence again — the resulting machine image must be
        byte-identical, proving the snapshot is a faithful resume point."""
        b = _make_unicorn()
        # MOV r0,#1; MOV r1,#2; MOV r2,#3; BX LR
        insns = struct.pack("<HHHH", 0x2001, 0x2102, 0x2203, 0x4770)
        b.write_memory(FLASH_BASE, 1, insns, len(insns), raw=True)
        b.write_register("pc", FLASH_BASE)
        b.write_register("lr", FLASH_BASE + len(insns))
        b.write_register("sp", RAM_BASE + 0x2000)

        snap = b.save_state()

        b.set_breakpoint(FLASH_BASE + 6)  # stop at BX LR
        b.cont()
        run1 = _full_dump(b)

        assert b.restore_state(snap) is True
        b.cont()
        run2 = _full_dump(b)

        assert run1[0] == run2[0]
        assert run1[1] == run2[1]

    def test_restore_rejects_wrong_backend_type(self):
        b = _make_unicorn()
        b.write_register("r0", 0x1234)
        alien = Snapshot(backend_type="NotUnicorn", version=1, data=None)
        assert b.restore_state(alien) is False
        assert b.read_register("r0") == 0x1234

    def test_save_raises_before_init(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        with pytest.raises(SnapshotError):
            b.save_state()

    def test_portable_restore_rejects_mismatched_memory_map(self):
        """A portable snapshot carries a machine fingerprint; restoring it
        onto a backend with a different memory map must be refused BEFORE any
        write, not half-applied."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend

        b = _make_unicorn()
        b.write_memory(RAM_BASE, 1, b"original-ram", 12, raw=True)
        snap = b.save_state(portable=True)

        # A second backend with an EXTRA region -> different fingerprint.
        b2 = UnicornBackend(arch="cortex-m3")
        b2.add_memory_region(MemoryRegion("flash", FLASH_BASE, FLASH_SIZE, "rwx"))
        b2.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
        b2.add_memory_region(MemoryRegion("extra", 0x40000000, 0x1000, "rw"))
        b2.init()
        b2.write_memory(RAM_BASE, 1, b"UNTOUCHED-XX", 12, raw=True)

        assert b2.restore_state(snap) is False
        # Rejected before mutating: RAM is unchanged.
        assert b2.read_memory(RAM_BASE, 1, 12, raw=True) == b"UNTOUCHED-XX"

    def test_portable_restore_accepts_matching_fingerprint(self):
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend

        b = _make_unicorn()
        b.write_memory(RAM_BASE, 1, b"fingerprint-ok", 14, raw=True)
        snap = b.save_state(portable=True)

        b2 = _make_unicorn()  # same config -> same fingerprint
        assert b2.restore_state(snap) is True
        assert b2.read_memory(RAM_BASE, 1, 14, raw=True) == b"fingerprint-ok"
