"""Tests for the `machine.irq_delivery` parse + back-compat shim.

Covers the user-facing config contract:
  * the NEW explicit `irq_delivery` block (with and without `model`),
  * the OLD `interrupt_controller`-with-firmware-fields form, which the
    shim must auto-derive into the same DeliveryPlan (zero-migration),
  * purely-hardware controllers, which need NO plan (returns None),
  * lossless carry of arch-specific fields the typed plan doesn't model.

The controller itself is unaffected: build_irq_controller() still builds
from the same block. These two concerns are now independent.
"""
from __future__ import annotations

from halucinator.backends.irq.delivery import DeliveryModel
from halucinator.hal_config import HALMachineConfig


def _machine(**kw) -> HALMachineConfig:
    # config_file left None -> default-machine flag; arch must be real.
    return HALMachineConfig(arch=kw.pop("arch", "arm"), **kw)


# ---------------------------------------------------------------------------
# New explicit irq_delivery block
# ---------------------------------------------------------------------------

class TestExplicitBlock:
    def test_frame_block_explicit_model(self):
        m = _machine(
            interrupt_controller={"type": "arm_vic"},
            irq_delivery={"model": "frame", "vector_base": 0x0,
                          "isr_addr": 0x20abcdef},
        )
        plan = m.build_delivery_plan()
        assert plan.model is DeliveryModel.FRAME
        assert plan.isr_addr == 0x20abcdef
        assert plan.vector_base == 0x0

    def test_model_inferred_when_omitted(self):
        # trampoline present, no explicit model -> TRAMPOLINE
        m = _machine(
            interrupt_controller={"type": "arm_vic"},
            irq_delivery={"trampoline": 0x20555000},
        )
        plan = m.build_delivery_plan()
        assert plan.model is DeliveryModel.TRAMPOLINE
        assert plan.trampoline == 0x20555000

    def test_shadow_block(self):
        m = _machine(
            arch="mips",
            interrupt_controller={"type": "mips"},
            irq_delivery={"model": "shadow",
                          "irq_fired_addr": 0xA0010004,
                          "irq_number_addr": 0xA0010000,
                          "irq_number_phys_addr": 0x00010000},
        )
        plan = m.build_delivery_plan()
        assert plan.model is DeliveryModel.SHADOW
        assert plan.irq_fired_addr == 0xA0010004
        # phys addr isn't a typed field -> preserved in extra, not dropped.
        assert plan.extra["irq_number_phys_addr"] == 0x00010000

    def test_bad_model_raises(self):
        m = _machine(interrupt_controller={"type": "arm_vic"},
                     irq_delivery={"model": "bogus"})
        try:
            m.build_delivery_plan()
        except ValueError as e:
            assert "bogus" in str(e)
        else:
            raise AssertionError("expected ValueError on bad model")


# ---------------------------------------------------------------------------
# Back-compat shim — OLD interrupt_controller blocks keep working
# ---------------------------------------------------------------------------

class TestBackCompatShim:
    def test_arm32_gicv2_shadow_form(self):
        """The committed arm32 test YAML: gicv2 with firmware shadow addrs
        on the controller. Shim derives a SHADOW plan; controller stays a
        real GIC."""
        ctrl = {"type": "gicv2", "gicd_base": 0x08000000,
                "gicc_base": 0x08010000,
                "irq_number_addr": 0x40000008,
                "irq_fired_addr": 0x40000004}
        m = _machine(interrupt_controller=ctrl)
        plan = m.build_delivery_plan()
        assert plan is not None
        assert plan.model is DeliveryModel.SHADOW
        assert plan.irq_fired_addr == 0x40000004
        assert plan.irq_number_addr == 0x40000008
        assert plan.gicc_base == 0x08010000
        # controller still builds from the same block, unchanged.
        assert m.build_irq_controller().gicd_base == 0x08000000

    def test_arm_vic_nested_options_form(self):
        """Old arm_vic put firmware fields under options:. Shim flattens
        and derives a TRAMPOLINE plan (irq_simple_entry -> trampoline)."""
        ctrl = {"type": "arm_vic",
                "options": {"vector_base": 0x0,
                            "isr_addr": 0x20111111,
                            "irq_simple_entry": 0x20222222}}
        m = _machine(interrupt_controller=ctrl)
        plan = m.build_delivery_plan()
        assert plan.model is DeliveryModel.TRAMPOLINE
        assert plan.trampoline == 0x20222222
        assert plan.isr_addr == 0x20111111

    def test_x86_pic_extras_preserved(self):
        """x86-specific fields the ARM-shaped plan doesn't model are carried
        in extra losslessly, pending an X86ExceptionDeliverer."""
        ctrl = {"type": "x86_pic",
                "options": {"isr_addr": 0x410e50,
                            "int_ent": 0x436240,
                            "int_exit": 0x4362d0,
                            "stub_addr": 0x7000,
                            "isr_arg": 0}}
        m = _machine(arch="x86", interrupt_controller=ctrl)
        plan = m.build_delivery_plan()
        assert plan.isr_addr == 0x410e50
        assert plan.extra["int_ent"] == 0x436240
        assert plan.extra["int_exit"] == 0x4362d0
        assert plan.extra["stub_addr"] == 0x7000

    def test_explicit_block_wins_over_legacy(self):
        """If both are present, the explicit irq_delivery block is used."""
        m = _machine(
            interrupt_controller={"type": "arm_vic",
                                  "options": {"isr_addr": 0xdead}},
            irq_delivery={"model": "frame", "isr_addr": 0xbeef},
        )
        assert m.build_delivery_plan().isr_addr == 0xbeef


# ---------------------------------------------------------------------------
# Pure-hardware controllers need NO delivery plan
# ---------------------------------------------------------------------------

class TestNoPlanNeeded:
    def test_cortex_m_no_plan(self):
        m = _machine(arch="cortex-m3",
                     interrupt_controller={"type": "cortex_m"})
        assert m.build_delivery_plan() is None

    def test_plain_gicv2_no_plan(self):
        # gicv2 with only hardware bases (QEMU/avatar case) -> no deliverer.
        m = _machine(interrupt_controller={"type": "gicv2",
                                           "gicd_base": 0x08000000,
                                           "gicc_base": 0x08010000})
        assert m.build_delivery_plan() is None

    def test_no_controller_no_plan(self):
        m = _machine(interrupt_controller=None)
        assert m.build_delivery_plan() is None
