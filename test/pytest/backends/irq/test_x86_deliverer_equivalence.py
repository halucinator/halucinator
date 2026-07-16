"""Proof that x86 PC interrupt delivery collapses to ONE implementation.

Like ``test_arm_deliverer_equivalence.py``, this does not re-test "does x86
IRQ delivery work" (``test_controllers`` covers ``X86PicController``). It
checks the *refactor claim*: that the new ``X86ExceptionDeliverer``
reproduces, byte-for-byte, the CPU-state mutations of the pre-refactor
``X86PicController.deliver`` — so the delivery logic can move out of the
controller and the controller becomes a thin shim.

We compare the *ordered sequence* of register writes and memory writes,
which is the entire observable effect of delivery on a backend.
"""
from __future__ import annotations

from halucinator.backends.irq.x86_pic import X86PicController
from halucinator.backends.irq.delivery import (
    DeliveryModel,
    DeliveryPlan,
    X86ExceptionDeliverer,
)

_EFLAGS_IF = 1 << 9


class _StatefulBackend:
    """Records ordered register/memory writes; reflects writes into reads.
    Unlike the ARM mock, ``write_memory`` values may be int (frame words) or
    bytes (the assembled stub), so nothing is masked — values are stored and
    compared as-is."""

    def __init__(self, regs=None, mem=None):
        self._regs = dict(regs or {})
        self._mem = dict(mem or {})
        self.reg_writes: list = []
        self.mem_writes: list = []

    def read_register(self, name):
        return self._regs.get(name, 0)

    def write_register(self, name, value):
        self._regs[name] = value & 0xFFFFFFFF
        self.reg_writes.append((name, value & 0xFFFFFFFF))

    def read_memory(self, addr, size, num_words=1, raw=False):
        return self._mem.get(addr, 0)

    def write_memory(self, addr, size, value, num_words=1, raw=False):
        self._mem[addr] = value
        self.mem_writes.append((addr, size, value))
        return True


_ISR = 0x00410E50
_INT_ENT = 0x00436240
_INT_EXIT = 0x004362D0
_STUB = 0x7000
_ESP = 0x00500000
_EIP = 0x00408123
_CS = 0x08


def _regs(if_set=True):
    eflags = 0x00000002 | (_EFLAGS_IF if if_set else 0)
    return {"eflags": eflags, "eip": _EIP, "cs": _CS, "esp": _ESP}


def _run_pair(*, ctrl_kwargs, plan, regs):
    """Run the real controller and the new deliverer against two identical
    fresh backends; return both plus their return values."""
    b_old = _StatefulBackend(regs=dict(regs))
    b_new = _StatefulBackend(regs=dict(regs))
    old_ret = X86PicController(**ctrl_kwargs).deliver(b_old)
    new_ret = X86ExceptionDeliverer().deliver(b_new, 0, plan)
    return b_old, b_new, old_ret, new_ret


class TestMatchesX86PicController:
    def test_direct_isr_path(self):
        """No int_ent/int_exit -> both push the iret frame and vector at the
        ISR directly."""
        b_old, b_new, ro, rn = _run_pair(
            ctrl_kwargs=dict(isr_addr=_ISR),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, isr_addr=_ISR),
            regs=_regs(),
        )
        assert ro is True and rn is True
        assert b_old.reg_writes == b_new.reg_writes
        assert b_old.mem_writes == b_new.mem_writes
        # landed at the ISR, IF masked, frame pushed at esp-12
        assert b_new.reg_writes[-1] == ("eip", _ISR)
        assert b_new.mem_writes == [
            (_ESP - 12, 4, _EIP), (_ESP - 8, 4, _CS),
            (_ESP - 4, 4, _regs()["eflags"]),
        ]

    def test_stub_path_assembles_once_and_vectors_at_stub(self):
        """int_ent/int_exit set -> both assemble the intEnt/intExit stub in
        guest RAM (identical bytes) and vector at the stub entry."""
        b_old, b_new, ro, rn = _run_pair(
            ctrl_kwargs=dict(isr_addr=_ISR, int_ent=_INT_ENT,
                             int_exit=_INT_EXIT, stub_addr=_STUB, isr_arg=0),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, isr_addr=_ISR,
                              extra={"int_ent": _INT_ENT, "int_exit": _INT_EXIT,
                                     "stub_addr": _STUB, "isr_arg": 0}),
            regs=_regs(),
        )
        assert ro is True and rn is True
        assert b_old.reg_writes == b_new.reg_writes
        assert b_old.mem_writes == b_new.mem_writes
        assert b_new.reg_writes[-1] == ("eip", _STUB)
        # first mem write is the stub blob (bytes) at the stub base
        assert b_new.mem_writes[0][0] == _STUB
        assert isinstance(b_new.mem_writes[0][2], (bytes, bytearray))

    def test_masked_irqs_suppressed_identically(self):
        """IF=0 -> both suppress with no state mutation."""
        b_old, b_new, ro, rn = _run_pair(
            ctrl_kwargs=dict(isr_addr=_ISR),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, isr_addr=_ISR),
            regs=_regs(if_set=False),
        )
        assert ro is False and rn is False
        assert b_old.reg_writes == b_new.reg_writes == []
        assert b_old.mem_writes == b_new.mem_writes == []

    def test_no_isr_dropped_identically(self):
        """isr_addr unknown -> both drop with no state mutation."""
        b_old, b_new, ro, rn = _run_pair(
            ctrl_kwargs=dict(isr_addr=None),
            plan=DeliveryPlan(model=DeliveryModel.FRAME, isr_addr=None),
            regs=_regs(),
        )
        assert ro is False and rn is False
        assert b_old.reg_writes == b_new.reg_writes == []
        assert b_old.mem_writes == b_new.mem_writes == []
