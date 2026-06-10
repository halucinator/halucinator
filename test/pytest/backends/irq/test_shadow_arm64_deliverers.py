"""Unit coverage for the Shadow (mips/ppc) and AArch64 deliverers that
replaced UnicornBackend._apply_pending_irq_{mips,ppc,arm64}.

Asserts the observable effect (the ordered register/memory writes) matches
the behaviour the old per-arch methods produced.
"""
from __future__ import annotations

from halucinator.backends.irq.delivery import (
    Arm64ExceptionDeliverer,
    DeliveryModel,
    DeliveryPlan,
    ShadowExceptionDeliverer,
    build_exception_deliverer,
)


class _StatefulBackend:
    def __init__(self, regs=None):
        self._regs = dict(regs or {})
        self.reg_writes = []
        self.mem_writes = []

    def read_register(self, name):
        return self._regs.get(name, 0)

    def write_register(self, name, value):
        self._regs[name] = value & 0xFFFFFFFF
        self.reg_writes.append((name, value & 0xFFFFFFFF))

    def write_memory(self, addr, size, value, num_words=1, raw=False):
        self.mem_writes.append((addr, size, value & 0xFFFFFFFF))
        return True


class TestShadowDeliverer:
    def test_writes_number_then_fired(self):
        b = _StatefulBackend()
        plan = DeliveryPlan(model=DeliveryModel.SHADOW,
                            irq_number_addr=0x40000008,
                            irq_fired_addr=0x40000004)
        ok = ShadowExceptionDeliverer().deliver(b, 33, plan)
        assert ok is True
        assert b.mem_writes == [(0x40000008, 4, 33), (0x40000004, 4, 1)]
        assert getattr(b, "_last_delivered_irq") == 33

    def test_missing_addrs_returns_false(self):
        b = _StatefulBackend()
        plan = DeliveryPlan(model=DeliveryModel.SHADOW)   # no addrs
        assert ShadowExceptionDeliverer().deliver(b, 1, plan) is False
        assert b.mem_writes == []


class TestArm64Deliverer:
    def test_trampoline_with_iar_shadow(self):
        b = _StatefulBackend(regs={"pc": 0x80001000})
        plan = DeliveryPlan(model=DeliveryModel.TRAMPOLINE,
                            trampoline=0x80004000, gicc_base=0x08010000)
        assert Arm64ExceptionDeliverer().deliver(b, 42, plan) is True
        assert b.mem_writes == [(0x08010000 + 0x0C, 4, 42)]   # GICC_IAR
        assert ("lr", 0x80001000) in b.reg_writes             # return PC
        assert b.reg_writes[-1] == ("pc", 0x80004000)         # jumped to entry

    def test_frame_fallback_uses_vbar_offset(self):
        b = _StatefulBackend(regs={"pc": 0x80002000})
        plan = DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0)
        assert Arm64ExceptionDeliverer().deliver(b, 5, plan) is True
        # vbar_el1 read fails on the fake -> falls back to plan.vector_base
        assert b.reg_writes[-1] == ("pc", 0x280)


class TestFactory:
    def test_factory_returns_right_deliverer(self):
        assert build_exception_deliverer("mips").arch == "shadow"
        assert build_exception_deliverer("powerpc").arch == "shadow"
        assert build_exception_deliverer("ppc64").arch == "shadow"
        assert build_exception_deliverer("arm64").arch == "arm64"
        assert build_exception_deliverer("arm").arch == "arm"
        assert build_exception_deliverer("cortex-m3") is None
        assert build_exception_deliverer("x86") is None
