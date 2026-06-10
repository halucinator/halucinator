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
        """When no ISR is installed at the vector table slot, applying
        a queued IRQ should refuse to jump into zero rather than
        crash."""
        import logging
        b = _make_backend()
        # No vector table installed → slot holds 0. inject_irq queues;
        # _apply_pending_irq is what the dispatch loop runs.
        b.inject_irq(5)
        with caplog.at_level(logging.WARNING):
            b._apply_pending_irq(b._pending_irqs.pop(0))
        assert "vector table slot" in caplog.text

    def test_inject_irq_non_cortex_m_routes_through_controller(self):
        """On non-cortex-m archs, inject_irq falls through to
        HalBackend.inject_irq, which uses the configured IrqController.
        Without a controller attached, it raises IrqConfigError so the
        caller sees a clear "no controller configured" message instead
        of a silent no-op."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq import IrqConfigError
        b = UnicornBackend(arch="arm64")
        b.add_memory_region(MemoryRegion("ram", 0x40000000, 0x1000, "rw"))
        b.init()
        with pytest.raises(IrqConfigError, match="no interrupt controller"):
            b.inject_irq(5)

    def test_inject_irq_non_cortex_m_with_controller_attached(self):
        """When the user configured a GIC controller, inject_irq routes
        through it and the GICD MMIO write happens on the unicorn
        engine (visible via read_memory)."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq.gic import GicController
        b = UnicornBackend(arch="arm64")
        # Map space the GICD will write into.
        b.add_memory_region(MemoryRegion("gicd", 0x08000000, 0x10000, "rw"))
        b.add_memory_region(MemoryRegion("ram",  0x40000000, 0x1000,  "rw"))
        b.init()
        b.set_irq_controller(GicController(gicd_base=0x08000000, version=2))
        b.inject_irq(33)  # SPI 33 → ISPENDR1 bit 1
        # GICD_ISPENDR1 at gicd_base + 0x200 + 4 = 0x08000204
        assert b.read_memory(0x08000204, 4, 1) == (1 << 1)

    def test_inject_irq_enters_isr_on_cortex_m(self):
        """With a vector table installed, inject_irq queues the IRQ;
        applying it pushes an exception frame, sets PC to the ISR,
        and puts the EXC_RETURN magic in LR. Apply happens
        automatically inside cont() between emu_start chunks; the
        test calls _apply_pending_irq directly to avoid spinning a
        real CPU."""
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
        assert b._pending_irqs == [2]
        # Drain the pending IRQ as cont() would on the dispatch thread.
        b._apply_pending_irq(b._pending_irqs.pop(0))

        assert b.read_register("pc") == isr_addr  # Thumb bit handled by unicorn
        # EXC_RETURN thread/MSP value
        assert b.read_register("lr") == 0xFFFFFFF9

        # Frame should be 8 words starting at new SP.
        sp = b.read_register("sp")
        frame = struct.unpack("<8I", bytes(b.read_memory(sp, 1, 32, raw=True)))
        # saved lr (index 5) and saved pc (index 6) match what we set
        assert frame[5] == 0x08000ABF
        assert frame[6] == 0x08000ABC

    def test_arm_vic_inject_irq_queues_via_trigger(self):
        """A-profile ARM with an ArmVicController: inject_irq routes
        through the controller's trigger() (queue-only) — the IRQ lands
        in _pending_irqs exactly once (no double-append) and no CPU
        state is mutated by the queue step."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq.arm_vic import ArmVicController
        b = UnicornBackend(arch="arm")
        b.add_memory_region(MemoryRegion("ram", 0x20000000, 0x10000, "rwx"))
        b.add_memory_region(MemoryRegion("low", 0x00000000, 0x1000, "rwx"))
        b.init()
        b.set_irq_controller(ArmVicController(vector_base=0x0))
        b.inject_irq(7)
        assert b._pending_irqs == [7]   # queued once, not twice

    def test_arm_vic_apply_pending_irq_vectors(self):
        """Applying a pended IRQ on the ARM ArmVic path performs the
        architectural IRQ-mode vector entry: SPSR_irq=CPSR, LR_irq=PC+4,
        CPSR=IRQ mode (I set), PC=vector_base+0x18. This is what cont()
        runs on the dispatch thread between emu_start chunks."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq.arm_vic import ArmVicController
        b = UnicornBackend(arch="arm")
        b.add_memory_region(MemoryRegion("ram", 0x20000000, 0x10000, "rwx"))
        b.add_memory_region(MemoryRegion("low", 0x00000000, 0x1000, "rwx"))
        b.init()
        b.set_irq_controller(ArmVicController(vector_base=0x0))
        b.write_register("cpsr", 0x13)            # SVC mode, IRQs enabled
        b.write_register("pc", 0x20001000)
        b.inject_irq(4)
        assert b._pending_irqs == [4]
        b._apply_pending_irq(b._pending_irqs.pop(0))
        assert b.read_register("pc") == 0x18      # IRQ vector
        assert (b.read_register("cpsr") & 0x1F) == 0x12   # IRQ mode
        assert (b.read_register("cpsr") & 0x80) != 0      # I masked
        # LR is banked in IRQ mode now and holds the return address.
        assert b.read_register("lr") == 0x20001004

    def test_arm_vic_apply_pending_irq_masked_drops(self):
        """When CPSR.I masks IRQs, applying the pended IRQ is a no-op
        (the tick is dropped, not re-queued, so cont() doesn't spin)."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq.arm_vic import ArmVicController
        b = UnicornBackend(arch="arm")
        b.add_memory_region(MemoryRegion("ram", 0x20000000, 0x10000, "rwx"))
        b.add_memory_region(MemoryRegion("low", 0x00000000, 0x1000, "rwx"))
        b.init()
        b.set_irq_controller(ArmVicController(vector_base=0x0))
        b.write_register("cpsr", 0x13 | 0x80)     # I bit set: masked
        b.write_register("pc", 0x20001000)
        b.inject_irq(4)
        b._apply_pending_irq(b._pending_irqs.pop(0))
        # PC unchanged; not re-queued.
        assert b.read_register("pc") == 0x20001000
        assert b._pending_irqs == []

    def test_shutdown(self):
        b = _make_backend()
        b.shutdown()
        assert b._uc is None

    def test_irq_set_clear_enable_bp_are_noops(self):
        """The avatar2/QEMU path drives irq_{set,clear,enable}_bp by
        writing to the halucinator-irq controller's MMIO region. Unicorn
        doesn't model a NVIC/GIC — IRQ delivery here goes through
        inject_irq() / IrqController.trigger() instead. Peripheral models
        call these defensively to deassert lines that were never asserted
        via MMIO (UTTYModel after rx-char delivery is the most common
        trigger), so they must exist as no-ops rather than AttributeError."""
        b = _make_backend()
        # Should be callable without raising; return value is irrelevant.
        assert b.irq_set_bp(1) is None
        assert b.irq_clear_bp(1) is None
        assert b.irq_enable_bp(1) is None
        # And without arguments, default to irq_num=1:
        assert b.irq_set_bp() is None
        assert b.irq_clear_bp() is None
        assert b.irq_enable_bp() is None


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
