"""Integration: the ARM ExceptionDeliverer is actually wired into the
live UnicornBackend dispatch path, and main._wire_irq attaches it.

Two levels:
  * _wire_irq: given a config, attaches controller + plan + deliverer
    (and attaches NO deliverer when none is needed).
  * UnicornBackend._apply_pending_irq(arm): with a FRAME plan + the
    ArmExceptionDeliverer attached, a real unicorn ARM core enters
    IRQ mode and vectors to vbar+0x18 — i.e. the deliverer ran.
"""
from __future__ import annotations

import pytest

from halucinator.backends.irq.delivery import (
    ArmExceptionDeliverer,
    DeliveryModel,
    DeliveryPlan,
    build_exception_deliverer,
)


# ---------------------------------------------------------------------------
# main._wire_irq attaches the right pieces
# ---------------------------------------------------------------------------

class _RecordingBackend:
    def __init__(self):
        self.controller = self.plan = self.deliverer = "UNSET"

    def set_irq_controller(self, c):
        self.controller = c

    def set_delivery_plan(self, p):
        self.plan = p

    def set_exception_deliverer(self, d):
        self.deliverer = d


class _Cfg:
    """Minimal stand-in for the config.machine surface _wire_irq touches."""
    def __init__(self, arch, controller, plan):
        self.arch = arch
        self._controller = controller
        self._plan = plan

    class _Machine:
        pass

    @property
    def machine(self):
        m = _Cfg._Machine()
        m.arch = self.arch
        m.build_irq_controller = lambda: self._controller
        m.build_delivery_plan = lambda: self._plan
        return m


class TestWireIrq:
    def test_attaches_deliverer_for_arm_with_plan(self):
        from halucinator.main import _wire_irq
        b = _RecordingBackend()
        plan = DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0)
        _wire_irq(b, _Cfg(arch="arm", controller="CTRL", plan=plan))
        assert b.controller == "CTRL"
        assert b.plan is plan
        assert isinstance(b.deliverer, ArmExceptionDeliverer)

    def test_no_plan_no_deliverer(self):
        from halucinator.main import _wire_irq
        b = _RecordingBackend()
        _wire_irq(b, _Cfg(arch="cortex-m3", controller="CTRL", plan=None))
        assert b.controller == "CTRL"
        assert b.plan == "UNSET"        # set_delivery_plan never called
        assert b.deliverer == "UNSET"

    def test_mips_shadow_plan_gets_shadow_deliverer(self):
        from halucinator.main import _wire_irq
        from halucinator.backends.irq.delivery import ShadowExceptionDeliverer
        b = _RecordingBackend()
        plan = DeliveryPlan(model=DeliveryModel.SHADOW)
        _wire_irq(b, _Cfg(arch="mips", controller="CTRL", plan=plan))
        assert b.plan is plan
        assert isinstance(b.deliverer, ShadowExceptionDeliverer)

    def test_natively_delivering_arch_gets_no_deliverer(self):
        # cortex-m3 (NVIC fast-path) takes exceptions natively -> even with a
        # plan present, build_exception_deliverer returns None.
        from halucinator.main import _wire_irq
        b = _RecordingBackend()
        plan = DeliveryPlan(model=DeliveryModel.FRAME)
        _wire_irq(b, _Cfg(arch="cortex-m3", controller="CTRL", plan=plan))
        assert b.plan is plan           # plan still attached
        assert b.deliverer == "UNSET"   # no in-process deliverer needed


# ---------------------------------------------------------------------------
# Live UnicornBackend ARM dispatch routes through the deliverer
# ---------------------------------------------------------------------------

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn-engine not installed")
class TestUnicornArmDispatch:
    def _arm_backend(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.hal_backend import MemoryRegion
        b = UnicornBackend(arch="arm")
        # Low vectors at 0x0 + room for code/stack.
        b.add_memory_region(MemoryRegion("ram", 0x0, 0x10000, "rwx"))
        b.init()
        return b

    def test_apply_pending_irq_uses_deliverer_frame(self):
        b = self._arm_backend()
        # Install a real IRQ vector word so the FRAME path picks vbar+0x18
        # (non-zero => "vectors installed").
        b.write_memory(0x18, 4, 0xEA000000)
        # SVC mode, IRQs enabled, running somewhere in RAM.
        b.write_register("cpsr", 0x60000013)
        b.write_register("pc", 0x00001000)

        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(DeliveryPlan(model=DeliveryModel.FRAME,
                                         vector_base=0x0))

        # _apply_pending_irq is the dispatch-thread delivery entry point.
        b._apply_pending_irq(7)

        cpsr = b.read_register("cpsr")
        assert cpsr & 0x1F == 0x12          # IRQ mode
        assert cpsr & 0x80                  # IRQs masked on entry
        assert b.read_register("pc") == 0x18      # vectored to IRQ vector
        assert b.read_register("lr") == 0x1004    # interrupted pc + 4
        assert getattr(b, "_last_delivered_irq") == 7

    def test_apply_pending_irq_trampoline(self):
        b = self._arm_backend()
        b.write_register("cpsr", 0x60000013)
        b.write_register("pc", 0x00002000)
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(DeliveryPlan(model=DeliveryModel.TRAMPOLINE,
                                         trampoline=0x00004000))
        b._apply_pending_irq(3)
        assert b.read_register("pc") == 0x4000    # jumped to trampoline
        assert b.read_register("lr") == 0x2004
