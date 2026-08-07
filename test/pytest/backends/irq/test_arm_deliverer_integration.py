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


# ---------------------------------------------------------------------------
# Modelled GICv2 CPU interface (GICC_IAR / GICC_EOIR)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn-engine not installed")
class TestUnicornGicIarModel:
    """Regression for the modelled GICv2 CPU interface.

    An A-profile firmware's low-level IRQ handler reads GICC_IAR to learn which
    interrupt just fired. The in-process backend has no real GIC, and that
    address is typically shadowed by an AutoPeripheral catch-all whose read
    default is NOT the acknowledged IRQ id — so without a model the handler
    only ever sees the GICv2 spurious id 0x3FF and never dispatches the
    delivered ISR. This is exactly what wedged the ION7400 (VxWorks/Cortex-A9)
    boot: its GIC drain loop polling GICC_IAR (0xec80010c) always read 0x3FF,
    so the delivered system tick (IRQ 27) never entered the scheduler.

    set_delivery_plan models GICC_IAR (acked id once, then 0x3FF) and GICC_EOIR
    only when the plan carries a gicc_base — i.e. only for GICv2 configs, never
    for cortex-m / x86 / arm_vic ARM configs (no gicc_base).
    """

    GICC = 0x8000
    IAR = GICC + 0x0C
    EOIR = GICC + 0x10
    SPURIOUS = 0x3FF

    def _arm_backend(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.hal_backend import MemoryRegion
        b = UnicornBackend(arch="arm")
        b.add_memory_region(MemoryRegion("ram", 0x0, 0x10000, "rwx"))
        b.init()
        return b

    def _gic_plan(self):
        return DeliveryPlan(model=DeliveryModel.FRAME, vector_base=0x0,
                            gicc_base=self.GICC)

    def test_gicc_plan_installs_model(self):
        b = self._arm_backend()
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(self._gic_plan())
        assert b._gicc_iface_base == self.GICC

    def test_vic_plan_leaves_model_uninstalled(self):
        # arm_vic ARM configs (m340, bmxnoe) carry no gicc_base: the model must
        # stay off so their IRQ path is byte-for-byte unchanged.
        b = self._arm_backend()
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(DeliveryPlan(model=DeliveryModel.FRAME,
                                         vector_base=0x0, gicc_base=None))
        assert b._gicc_iface_base is None

    def test_deliverer_stashes_acked_irq(self):
        b = self._arm_backend()
        b.write_memory(0x18, 4, 0xEA000000)      # vectors installed
        b.write_register("cpsr", 0x60000013)     # SVC, IRQs enabled
        b.write_register("pc", 0x00001000)
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(self._gic_plan())
        b._apply_pending_irq(27)
        # The deliverer stashed the acked id for the modelled IAR read.
        assert b._gicc_iar_pending == 27

    def _read_iar_via_guest(self, b):
        """Execute one `ldr r0,[r1]` (r1=IAR) so the modelled MEM_READ hook
        fires (a host-side uc.mem_read would bypass it), and return r0."""
        import unicorn
        uc = b._uc
        b.write_memory(0x1000, 4, 0xE5910000)    # ldr r0, [r1]
        uc.reg_write(unicorn.arm_const.UC_ARM_REG_R1, self.IAR)
        uc.emu_start(0x1000, 0x1004, count=1)
        return uc.reg_read(unicorn.arm_const.UC_ARM_REG_R0)

    def test_iar_read_returns_acked_id_once_then_spurious(self):
        b = self._arm_backend()
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(self._gic_plan())
        # Nothing pending yet -> the drain loop must see spurious, not a phantom.
        assert self._read_iar_via_guest(b) == self.SPURIOUS
        # After an acknowledge, the ISR reads the real id exactly once...
        b._gicc_iar_pending = 27
        assert self._read_iar_via_guest(b) == 27
        # ...and every subsequent read is spurious again.
        assert self._read_iar_via_guest(b) == self.SPURIOUS

    def test_eoir_write_clears_active_irq(self):
        import unicorn
        b = self._arm_backend()
        b.set_exception_deliverer(ArmExceptionDeliverer())
        b.set_delivery_plan(self._gic_plan())
        b._gicc_iar_pending = 27
        assert self._read_iar_via_guest(b) == 27     # -> _gicc_active_irq = 27
        assert b._gicc_active_irq == 27
        # `str r0,[r1]` (r1=EOIR) writes end-of-interrupt -> active id cleared.
        uc = b._uc
        b.write_memory(0x1000, 4, 0xE5810000)        # str r0, [r1]
        uc.reg_write(unicorn.arm_const.UC_ARM_REG_R0, 27)
        uc.reg_write(unicorn.arm_const.UC_ARM_REG_R1, self.EOIR)
        uc.emu_start(0x1000, 0x1004, count=1)
        assert b._gicc_active_irq is None
