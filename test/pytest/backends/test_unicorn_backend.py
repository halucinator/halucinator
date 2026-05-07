"""
Unit tests for UnicornBackend.

These tests use real unicorn-engine to emulate simple ARM Thumb code snippets
so they exercise the actual emulation path, not just mocks.
"""
import struct
import pytest

try:
    import unicorn
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

from halucinator.backends.hal_backend import HalBackend
from halucinator.backends.unicorn_backend import UnicornBackend

pytestmark = pytest.mark.skipif(
    not _HAVE_UNICORN,
    reason="unicorn-engine not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLASH_BASE = 0x08000000
RAM_BASE   = 0x20000000
FLASH_SIZE = 0x10000
RAM_SIZE   = 0x8000


def _make_backend():
    """Create an initialised UnicornBackend with blank flash+RAM."""
    from halucinator.backends.hal_backend import MemoryRegion
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", FLASH_BASE, FLASH_SIZE, "rwx"))
    b.add_memory_region(MemoryRegion("ram",   RAM_BASE,   RAM_SIZE,   "rw"))
    b.init()
    return b


# ---------------------------------------------------------------------------
# Basic API tests (no real execution)
# ---------------------------------------------------------------------------

class TestUnicornBackendInterface:
    def test_is_hal_backend(self):
        assert issubclass(UnicornBackend, HalBackend)

    def test_read_write_register(self):
        b = _make_backend()
        b.write_register("r0", 0xDEADBEEF)
        assert b.read_register("r0") == 0xDEADBEEF

    def test_read_write_memory_word(self):
        b = _make_backend()
        b.write_memory(RAM_BASE, 4, 0x12345678)
        val = b.read_memory(RAM_BASE, 4, 1)
        assert val == 0x12345678

    def test_read_write_memory_bytes(self):
        b = _make_backend()
        data = b"\xDE\xAD\xBE\xEF"
        b.write_memory(RAM_BASE, 1, data, len(data), raw=True)
        out = b.read_memory(RAM_BASE, 1, 4, raw=True)
        assert out == data

    def test_set_remove_breakpoint(self):
        b = _make_backend()
        bp_id = b.set_breakpoint(0x08001000)
        assert isinstance(bp_id, int)
        assert (0x08001000 & ~1) in b._breakpoints
        b.remove_breakpoint(bp_id)
        assert (0x08001000 & ~1) not in b._breakpoints

    def test_unknown_register_raises(self):
        b = _make_backend()
        with pytest.raises(ValueError):
            b.read_register("xyz_unknown")

    def test_inject_irq_warns_without_vector(self, caplog):
        """When no ISR is installed at the vector table slot, inject_irq
        should refuse to jump into zero rather than crash."""
        import logging
        b = _make_backend()
        # No vector table installed → slot holds 0.
        with caplog.at_level(logging.WARNING):
            b.inject_irq(5)
        assert "vector table slot" in caplog.text

    def test_inject_irq_non_cortex_m_is_noop(self, caplog):
        """On non-cortex-m archs, inject_irq just warns — we don't have
        an in-process interrupt model for ARMv7A / ARM64 / MIPS / PPC."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        import logging
        b = UnicornBackend(arch="arm64")
        b.add_memory_region(MemoryRegion("ram", 0x40000000, 0x1000, "rw"))
        b.init()
        with caplog.at_level(logging.WARNING):
            b.inject_irq(5)
        assert "only cortex-m3" in caplog.text

    def test_inject_irq_enters_isr_on_cortex_m(self):
        """With a vector table installed, inject_irq pushes an exception
        frame, sets PC to the ISR, and puts the EXC_RETURN magic in LR."""
        import struct
        b = _make_backend()
        # Pretend vector table starts at flash base.
        b.set_vtor(FLASH_BASE)
        # Put a dummy ISR address at external IRQ #2 (vector 18).
        isr_addr = FLASH_BASE + 0x400
        b.write_memory(FLASH_BASE + (16 + 2) * 4, 1,
                       isr_addr.to_bytes(4, "little"), 4, raw=True)
        # Give the CPU a valid SP and a pre-IRQ PC/LR to save.
        b.write_register("sp", RAM_BASE + 0x1000)
        b.write_register("pc", 0x08000ABC)
        b.write_register("lr", 0x08000ABF)

        b.inject_irq(2)
        assert b.read_register("pc") == isr_addr  # Thumb bit handled by unicorn
        # EXC_RETURN thread/MSP value
        assert b.read_register("lr") == 0xFFFFFFF9

        # Frame should be 8 words starting at new SP.
        sp = b.read_register("sp")
        frame = struct.unpack("<8I", bytes(b.read_memory(sp, 1, 32, raw=True)))
        # saved lr (index 5) and saved pc (index 6) match what we set
        assert frame[5] == 0x08000ABF
        assert frame[6] == 0x08000ABC

    def test_shutdown(self):
        b = _make_backend()
        b.shutdown()
        assert b._uc is None


# ---------------------------------------------------------------------------
# Execution tests — run real ARM Thumb instructions
# ---------------------------------------------------------------------------

class TestUnicornExecution:
    """
    Encode minimal Thumb-2 instructions and verify execution.

    Thumb instruction encoding used:
        MOV r0, #imm8   → 0x20xx  (T1: MOV Rd, #imm8)
        BX  LR          → 0x4770
    """

    def _load_thumb(self, backend, addr, thumb_bytes):
        """Write Thumb code at addr and set PC to addr|1 (Thumb mode)."""
        backend.write_memory(addr, 1, thumb_bytes, len(thumb_bytes), raw=True)
        backend.write_register("pc", addr)
        backend.write_register("lr", addr + len(thumb_bytes))  # fake return addr

    def test_mov_r0_immediate(self):
        """MOV r0, #42 should leave r0 == 42."""
        b = _make_backend()
        # Thumb T1: MOV r0, #42  → 0x202A
        # BX LR → 0x4770
        insns = struct.pack("<HH", 0x202A, 0x4770)
        self._load_thumb(b, FLASH_BASE, insns)

        # Set a breakpoint after MOV (at BX LR) to stop execution
        bp_id = b.set_breakpoint(FLASH_BASE + 2)
        b.cont()

        assert b.read_register("r0") == 42

    def test_mov_r1_and_r2(self):
        """MOV r1, #10; MOV r2, #20."""
        b = _make_backend()
        # MOV r1, #10 → 0x210A   MOV r2, #20 → 0x2214   BX LR → 0x4770
        insns = struct.pack("<HHH", 0x210A, 0x2214, 0x4770)
        self._load_thumb(b, FLASH_BASE, insns)
        b.set_breakpoint(FLASH_BASE + 4)
        b.cont()

        assert b.read_register("r1") == 10
        assert b.read_register("r2") == 20

    def test_breakpoint_stops_execution(self):
        """Execution halts at a breakpoint, not at the end of memory."""
        b = _make_backend()
        # Three MOV instructions; break after the first
        insns = struct.pack("<HHH", 0x2001, 0x2102, 0x2203)  # r0=1,r1=2,r2=3
        self._load_thumb(b, FLASH_BASE, insns)
        b.set_breakpoint(FLASH_BASE + 2)   # after first MOV
        b.cont()

        # r0 should be set (MOV r0,#1 executed), r1/r2 should not
        assert b.read_register("r0") == 1
        assert b.read_register("r1") == 0   # not yet executed

    def test_single_step(self):
        """step() advances exactly one instruction."""
        b = _make_backend()
        # MOV r0, #7 → 0x2007; MOV r1, #8 → 0x2108
        insns = struct.pack("<HH", 0x2007, 0x2108)
        self._load_thumb(b, FLASH_BASE, insns)

        b.write_register("pc", FLASH_BASE)
        b.step()

        assert b.read_register("r0") == 7
        assert b.read_register("r1") == 0  # not executed yet

    def test_arm_mixin_get_arg(self):
        """get_arg reads from r0–r3 via ARMHalMixin."""
        b = _make_backend()
        b.write_register("r2", 0xABCD)
        assert b.get_arg(2) == 0xABCD

    def test_arm_mixin_read_string(self):
        """read_string reads null-terminated ASCII from memory."""
        b = _make_backend()
        text = b"hello\x00"
        b.write_memory(RAM_BASE, 1, text, len(text), raw=True)
        assert b.read_string(RAM_BASE) == "hello"
