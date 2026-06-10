"""Proof that ARM exception delivery collapses to ONE implementation.

This test does not check "does ARM IRQ delivery work" (test_controllers.py
already covers ArmVicController). It checks the *refactor claim*: that the
new ``ArmExceptionDeliverer`` reproduces, byte-for-byte, the CPU-state
mutations of BOTH pre-refactor code paths —

  1. ``ArmVicController.deliver``                 (the synth / VIC path)
  2. ``UnicornBackend._apply_pending_irq_armv7a`` (the GIC / built-in path)

— with the only difference between the two old paths expressed as data on
the ``DeliveryPlan`` (``gicc_base``). If these assertions hold, the two
duplicated implementations can be deleted in favour of the single
deliverer.

We compare the *ordered sequence* of register writes and memory writes,
which is the entire observable effect of delivery on a backend.
"""
from __future__ import annotations

from halucinator.backends.irq.arm_vic import ArmVicController
from halucinator.backends.irq.delivery import (
    ArmExceptionDeliverer,
    DeliveryModel,
    DeliveryPlan,
)

_CPSR_I = 0x80


class _StatefulBackend:
    """Records ordered register/memory writes; reflects writes into reads
    so a read-after-write sees the new value (faithful to a real CPU)."""

    def __init__(self, regs=None, mem=None):
        self._regs = dict(regs or {})
        self._mem = dict(mem or {})
        self.reg_writes: list[tuple[str, int]] = []
        self.mem_writes: list[tuple[int, int, int]] = []

    def read_register(self, name):
        return self._regs.get(name, 0)

    def write_register(self, name, value):
        self._regs[name] = value & 0xFFFFFFFF
        self.reg_writes.append((name, value & 0xFFFFFFFF))

    def read_memory(self, addr, size, num_words=1, raw=False):
        return self._mem.get(addr, 0)

    def write_memory(self, addr, size, value, num_words=1, raw=False):
        self._mem[addr] = value & 0xFFFFFFFF
        self.mem_writes.append((addr, size, value & 0xFFFFFFFF))
        return True


def _armv7a_builtin_reference(backend, num, vbar, gicc_base):
    """Faithful transcription of UnicornBackend._apply_pending_irq_armv7a
    (unicorn_backend.py:1597) restricted to the unmasked path, expressed
    against the _StatefulBackend interface. This is the GIC / built-in
    delivery the deliverer must reproduce.
    """
    cpsr = backend.read_register("cpsr")
    assert not (cpsr & _CPSR_I), "reference only models the unmasked path"
    pc = backend.read_register("pc")
    return_pc = pc + 4
    new_cpsr = cpsr & ~(0x1F | 0x20)
    new_cpsr |= 0x12 | _CPSR_I
    backend.write_register("cpsr", new_cpsr)
    backend.write_register("lr", return_pc)
    backend.write_register("spsr", cpsr)
    if gicc_base is not None:
        # real code: self._uc.mem_write(gicc_base+0x0C, num.to_bytes(4,"little"))
        backend.write_memory(gicc_base + 0x0C, 4, num)
    backend.write_register("pc", vbar + 0x18)


# ---------------------------------------------------------------------------
# 1. New deliverer == ArmVicController.deliver  (the synth / VIC path)
# ---------------------------------------------------------------------------

class TestMatchesArmVicController:
    def _run_pair(self, *, regs, mem, vic_kwargs, plan):
        """Run the real controller and the new deliverer against two
        identical backends; return both for comparison."""
        b_old = _StatefulBackend(regs=regs, mem=mem)
        b_new = _StatefulBackend(regs=regs, mem=mem)
        old_ret = ArmVicController(**vic_kwargs).deliver(b_old, num=7)
        new_ret = ArmExceptionDeliverer().deliver(b_new, 7, plan)
        return b_old, b_new, old_ret, new_ret

    def test_frame_vector_path(self):
        """No isr_addr, vectors installed -> both vector at vector_base+0x18."""
        regs = {"cpsr": 0x60000013, "pc": 0x20001000}   # SVC, IRQs enabled
        mem = {0x18: 0xea000000}                          # vector present
        b_old, b_new, ro, rn = self._run_pair(
            regs=regs, mem=mem,
            vic_kwargs=dict(vector_base=0x0),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0),
        )
        assert ro is True and rn is True
        assert b_old.reg_writes == b_new.reg_writes
        assert b_old.mem_writes == b_new.mem_writes
        # sanity: landed at the IRQ vector
        assert b_new.reg_writes[-1] == ("pc", 0x18)

    def test_isr_fallback_path(self):
        """isr_addr set, vectors NOT installed -> both vector at the ISR."""
        regs = {"cpsr": 0x60000013, "pc": 0x20001000}
        mem = {0x18: 0x0}                                 # vectors not installed
        b_old, b_new, ro, rn = self._run_pair(
            regs=regs, mem=mem,
            vic_kwargs=dict(vector_base=0x0, isr_addr=0x20abcdef),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0,
                              isr_addr=0x20abcdef),
        )
        assert ro is True and rn is True
        assert b_old.reg_writes == b_new.reg_writes
        assert b_old.mem_writes == b_new.mem_writes
        assert b_new.reg_writes[-1] == ("pc", 0x20abcdef)

    def test_trampoline_path(self):
        """irq_simple_entry set -> both vector at the trampoline."""
        regs = {"cpsr": 0x60000013, "pc": 0x20001000}
        b_old, b_new, ro, rn = self._run_pair(
            regs=regs, mem={},
            vic_kwargs=dict(vector_base=0x0, irq_simple_entry=0x20555000),
            plan=DeliveryPlan(model=DeliveryModel.TRAMPOLINE, vector_base=0x0,
                              trampoline=0x20555000),
        )
        assert ro is True and rn is True
        assert b_old.reg_writes == b_new.reg_writes
        assert b_old.mem_writes == b_new.mem_writes
        assert b_new.reg_writes[-1] == ("pc", 0x20555000)

    def test_masked_irqs_suppressed_identically(self):
        """CPSR.I=1 -> both suppress with no state mutation."""
        regs = {"cpsr": 0x600000D3, "pc": 0x20001000}    # I bit set
        b_old, b_new, ro, rn = self._run_pair(
            regs=regs, mem={0x18: 0xea000000},
            vic_kwargs=dict(vector_base=0x0),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0),
        )
        assert ro is False and rn is False
        assert b_old.reg_writes == b_new.reg_writes == []
        assert b_old.mem_writes == b_new.mem_writes == []


# ---------------------------------------------------------------------------
# 2. New deliverer == _apply_pending_irq_armv7a  (the GIC / built-in path)
# ---------------------------------------------------------------------------

class TestMatchesBuiltinArmv7a:
    def test_gic_path_with_iar_shadow(self):
        """The built-in path differs from the VIC path ONLY by the GICC_IAR
        shadow write — now just `plan.gicc_base`."""
        regs = {"cpsr": 0x60000013, "pc": 0x20001000}
        vbar, gicc = 0x0, 0x08010000

        b_ref = _StatefulBackend(regs=regs)
        _armv7a_builtin_reference(b_ref, 7, vbar=vbar, gicc_base=gicc)

        b_new = _StatefulBackend(regs=regs)
        ArmExceptionDeliverer().deliver(
            b_new, 7,
            DeliveryPlan(model=DeliveryModel.FRAME, vector_base=vbar,
                         gicc_base=gicc),
        )
        assert b_ref.reg_writes == b_new.reg_writes
        assert b_ref.mem_writes == b_new.mem_writes
        # the IAR shadow is the one and only memory write
        assert b_new.mem_writes == [(gicc + 0x0C, 4, 7)]

    def test_gic_path_without_iar_equals_vic_path(self):
        """With gicc_base unset, the built-in path == the VIC frame path:
        proving the two old implementations were the same code."""
        regs = {"cpsr": 0x60000013, "pc": 0x20001000}

        b_ref = _StatefulBackend(regs=regs)
        _armv7a_builtin_reference(b_ref, 7, vbar=0x0, gicc_base=None)

        b_vic = _StatefulBackend(regs=regs, mem={0x18: 0xea000000})
        ArmVicController(vector_base=0x0).deliver(b_vic, num=7)

        assert b_ref.reg_writes == b_vic.reg_writes
        assert b_ref.mem_writes == b_vic.mem_writes == []
