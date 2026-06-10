"""Unit tests for the per-arch IrqController implementations.

Each test mocks the backend's write_memory / read_register / write_register
and asserts the controller hits the right address with the right value.
No real emulation; deterministic; fast.
"""
from __future__ import annotations

import pytest

from halucinator.backends.irq import (
    IrqConfigError,
    IrqControllerSpec,
    build_irq_controller,
    default_for_arch,
)
from halucinator.backends.irq.arm_vic import ArmVicController
from halucinator.backends.irq.cortex_m import CortexMController
from halucinator.backends.irq.gic import GicController
from halucinator.backends.irq.mips import MipsController
from halucinator.backends.irq.openpic import OpenPicController


class _FakeBackend:
    """Captures memory + register accesses for assertions."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int, int]] = []
        self.reg_reads: dict[str, int] = {}
        self.reg_writes: list[tuple[str, int]] = []
        self.mem: dict[int, int] = {}   # addr -> word (for read_memory)

    def write_memory(self, addr, size, value, num_words=1, raw=False):
        self.writes.append((addr, size, value))
        return True

    def read_memory(self, addr, size, num_words=1, raw=False):
        return self.mem.get(addr, 0)

    def read_register(self, name):
        return self.reg_reads.get(name, 0)

    def write_register(self, name, value):
        self.reg_writes.append((name, value))

    # Helper: last value written to a register.
    def last_reg(self, name):
        for n, v in reversed(self.reg_writes):
            if n == name:
                return v
        return None


# ---------------------------------------------------------------------------
# Factory + defaults
# ---------------------------------------------------------------------------

class TestFactory:
    def test_cortex_m_default(self):
        spec = default_for_arch("cortex-m3")
        assert spec.type == "cortex_m"

    def test_mips_default(self):
        spec = default_for_arch("mips")
        assert spec.type == "mips"

    def test_arm_no_default(self):
        assert default_for_arch("arm") is None

    def test_arm64_no_default(self):
        assert default_for_arch("arm64") is None

    def test_powerpc_no_default(self):
        assert default_for_arch("powerpc") is None

    def test_unknown_arch_returns_none(self):
        assert default_for_arch("notreal") is None

    def test_factory_uses_default_when_no_spec(self):
        c = build_irq_controller("cortex-m3")
        assert isinstance(c, CortexMController)

    def test_factory_with_explicit_spec(self):
        c = build_irq_controller(
            "arm",
            IrqControllerSpec(type="gicv2", gicd_base=0x08000000),
        )
        assert isinstance(c, GicController)
        assert c.version == 2
        assert c.gicd_base == 0x08000000

    def test_factory_arm_no_spec_returns_none(self):
        # No default + no spec → caller will error out at inject time.
        assert build_irq_controller("arm") is None

    def test_factory_unknown_type_raises(self):
        with pytest.raises(IrqConfigError, match="Unknown.*notreal"):
            build_irq_controller("arm", IrqControllerSpec(type="notreal"))

    def test_factory_gicv2_missing_gicd_base_raises(self):
        with pytest.raises(IrqConfigError, match="gicd_base"):
            build_irq_controller("arm", IrqControllerSpec(type="gicv2"))

    def test_factory_openpic_missing_base_raises(self):
        with pytest.raises(IrqConfigError, match="openpic_base"):
            build_irq_controller("powerpc", IrqControllerSpec(type="openpic"))


# ---------------------------------------------------------------------------
# CortexMController
# ---------------------------------------------------------------------------

class TestCortexM:
    @pytest.mark.parametrize("num,expected_addr,expected_val", [
        (0, 0xE000E200, 1),
        (1, 0xE000E200, 1 << 1),
        (15, 0xE000E200, 1 << 15),
        (31, 0xE000E200, 1 << 31),
        (32, 0xE000E204, 1),       # crosses to ISPR1
        (37, 0xE000E204, 1 << 5),
        (63, 0xE000E204, 1 << 31),
        (64, 0xE000E208, 1),       # ISPR2
        (495, 0xE000E23C, 1 << 15),  # 495 // 32 = 15 (word), 495 % 32 = 15
    ])
    def test_writes_correct_ispr(self, num, expected_addr, expected_val):
        fb = _FakeBackend()
        CortexMController().trigger(fb, num)
        assert fb.writes == [(expected_addr, 4, expected_val)]

    def test_negative_irq_raises(self):
        with pytest.raises(IrqConfigError, match="0..495"):
            CortexMController().trigger(_FakeBackend(), -1)

    def test_too_large_raises(self):
        with pytest.raises(IrqConfigError, match="0..495"):
            CortexMController().trigger(_FakeBackend(), 496)


# ---------------------------------------------------------------------------
# GicController
# ---------------------------------------------------------------------------

class TestGic:
    GICD = 0x08000000

    def test_spi_32_writes_ispendr1_bit_0(self):
        fb = _FakeBackend()
        GicController(self.GICD, version=2).trigger(fb, 32)
        assert fb.writes == [(self.GICD + 0x200 + 4, 4, 1)]

    def test_spi_64_writes_ispendr2_bit_0(self):
        fb = _FakeBackend()
        GicController(self.GICD, version=3).trigger(fb, 64)
        assert fb.writes == [(self.GICD + 0x200 + 8, 4, 1)]

    def test_ppi_16_writes_ispendr0(self):
        fb = _FakeBackend()
        GicController(self.GICD, version=2).trigger(fb, 16)
        assert fb.writes == [(self.GICD + 0x200, 4, 1 << 16)]

    def test_sgi_v2_writes_sgir(self):
        fb = _FakeBackend()
        GicController(self.GICD, version=2).trigger(fb, 5)
        # CPUTargetList=1<<0, SGIINTID=5
        assert fb.writes == [(self.GICD + 0xF00, 4, (1 << 16) | 5)]

    def test_sgi_v3_raises(self):
        fb = _FakeBackend()
        with pytest.raises(IrqConfigError, match="ICC_SGI1R_EL1"):
            GicController(self.GICD, version=3).trigger(fb, 5)

    def test_too_large_raises(self):
        with pytest.raises(IrqConfigError, match="0..1019"):
            GicController(self.GICD, version=2).trigger(_FakeBackend(), 1020)

    def test_invalid_version_raises(self):
        with pytest.raises(IrqConfigError, match="version must be 2 or 3"):
            GicController(self.GICD, version=4)


# ---------------------------------------------------------------------------
# MipsController
# ---------------------------------------------------------------------------

class TestMips:
    @pytest.mark.parametrize("num,bit", [
        (0, 8),
        (1, 9),
        (2, 10),
        (7, 15),
    ])
    def test_sets_cause_ip_bit(self, num, bit):
        fb = _FakeBackend()
        fb.reg_reads["cause"] = 0x00000040  # arbitrary non-zero baseline
        MipsController().trigger(fb, num)
        assert fb.reg_writes == [("cause", 0x00000040 | (1 << bit))]

    def test_too_large_raises(self):
        with pytest.raises(IrqConfigError, match="0..7"):
            MipsController().trigger(_FakeBackend(), 8)

    def test_negative_raises(self):
        with pytest.raises(IrqConfigError, match="0..7"):
            MipsController().trigger(_FakeBackend(), -1)

    def test_backend_without_cause_raises(self):
        class NoCauseBackend:
            def read_register(self, name):
                raise AttributeError("no cause")
        with pytest.raises(IrqConfigError, match="cause"):
            MipsController().trigger(NoCauseBackend(), 0)


# ---------------------------------------------------------------------------
# OpenPicController
# ---------------------------------------------------------------------------

class TestOpenPic:
    BASE = 0x40040000

    @pytest.mark.parametrize("num,offset", [
        (0, 0x10000 + 0 * 0x20),
        (1, 0x10000 + 1 * 0x20),
        (16, 0x10000 + 16 * 0x20),
        (255, 0x10000 + 255 * 0x20),
    ])
    def test_writes_ipidr(self, num, offset):
        fb = _FakeBackend()
        OpenPicController(openpic_base=self.BASE).trigger(fb, num)
        assert fb.writes == [(self.BASE + offset, 4, 1)]

    def test_too_large_raises(self):
        with pytest.raises(IrqConfigError, match="0..255"):
            OpenPicController(openpic_base=self.BASE).trigger(
                _FakeBackend(), 256,
            )


# ---------------------------------------------------------------------------
# ArmVicController (synthesised A-profile ARM IRQ delivery)
# ---------------------------------------------------------------------------

class TestArmVic:
    # CPSR in SVC mode (0x13), IRQs enabled (I=0).
    CPSR_SVC = 0x13

    def test_factory_arm_vic(self):
        c = build_irq_controller(
            "arm", IrqControllerSpec(type="arm_vic"),
        )
        assert isinstance(c, ArmVicController)
        assert c.vector_base == 0x0

    def test_factory_vic_alias(self):
        c = build_irq_controller(
            "arm",
            IrqControllerSpec(type="vic",
                              options={"vector_base": 0xFFFF0000}),
        )
        assert isinstance(c, ArmVicController)
        assert c.vector_base == 0xFFFF0000

    def test_factory_passes_options(self):
        c = build_irq_controller(
            "arm",
            IrqControllerSpec(type="arm_vic",
                              options={"isr_addr": 0x20001234,
                                       "irq_simple_entry": 0x20005678}),
        )
        assert c.isr_addr == 0x20001234
        assert c.irq_simple_entry == 0x20005678

    def test_trigger_queues_only(self):
        # trigger must NOT mutate CPU state — it only enqueues.
        class _Q:
            _pending_irqs: list = []
        q = _Q()
        ArmVicController().trigger(q, 7)
        assert q._pending_irqs == [7]

    def test_deliver_vectors_at_0x18_by_default(self):
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC
        fb.reg_reads["pc"] = 0x20010000
        ok = ArmVicController(vector_base=0x0).deliver(fb, 3)
        assert ok is True
        # PC set to the IRQ vector (vector_base + 0x18).
        assert fb.last_reg("pc") == 0x18
        # LR_irq = interrupted PC + 4.
        assert fb.last_reg("lr") == 0x20010004
        # SPSR_irq = pre-exception CPSR.
        assert fb.last_reg("spsr") == self.CPSR_SVC
        # CPSR switched to IRQ mode (0x12) with I bit set, T cleared.
        cpsr = next(v for n, v in fb.reg_writes if n == "cpsr")
        assert (cpsr & 0x1F) == 0x12
        assert (cpsr & 0x80) != 0       # I set
        assert (cpsr & 0x20) == 0       # T clear (ARM state)

    def test_deliver_high_vectors(self):
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC
        fb.reg_reads["pc"] = 0x20010000
        ArmVicController(vector_base=0xFFFF0000).deliver(fb, 0)
        assert fb.last_reg("pc") == 0xFFFF0018

    def test_deliver_dropped_when_masked(self):
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC | 0x80   # I bit set
        fb.reg_reads["pc"] = 0x20010000
        ok = ArmVicController().deliver(fb, 1)
        assert ok is False
        # Nothing mutated.
        assert fb.reg_writes == []

    def test_deliver_direct_isr_when_no_vector_installed(self):
        # vector slot at 0x18 reads 0 -> firmware hasn't installed
        # vectors -> vector straight at the configured isr_addr.
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC
        fb.reg_reads["pc"] = 0x20010000
        fb.mem[0x18] = 0   # no vector installed
        c = ArmVicController(vector_base=0x0, isr_addr=0x20040000)
        c.deliver(fb, 5)
        assert fb.last_reg("pc") == 0x20040000

    def test_deliver_uses_vector_when_installed(self):
        # vector slot non-zero -> use the architectural vector at 0x18.
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC
        fb.reg_reads["pc"] = 0x20010000
        fb.mem[0x18] = 0xE59FF018   # ldr pc,[pc,#0x18] — vector present
        c = ArmVicController(vector_base=0x0, isr_addr=0x20040000)
        c.deliver(fb, 5)
        assert fb.last_reg("pc") == 0x18

    def test_irq_simple_entry_wins(self):
        fb = _FakeBackend()
        fb.reg_reads["cpsr"] = self.CPSR_SVC
        fb.reg_reads["pc"] = 0x20010000
        c = ArmVicController(vector_base=0x0, isr_addr=0x20040000,
                             irq_simple_entry=0x20099000)
        c.deliver(fb, 5)
        assert fb.last_reg("pc") == 0x20099000

    def test_register_clock_isr_only_fills_unset(self):
        c = ArmVicController(isr_addr=0x20001000)
        c.register_clock_isr(0x20009999)  # explicit config wins
        assert c.isr_addr == 0x20001000
        c2 = ArmVicController()
        c2.register_clock_isr(0x20009999)
        assert c2.isr_addr == 0x20009999


# ---------------------------------------------------------------------------
# YAML parsing on HALMachineConfig
# ---------------------------------------------------------------------------

class TestConfigParsing:
    def test_no_block_means_default(self):
        from halucinator.hal_config import HALMachineConfig
        m = HALMachineConfig(arch="cortex-m3")
        assert m.interrupt_controller is None
        c = m.build_irq_controller()
        assert isinstance(c, CortexMController)

    def test_explicit_block_overrides(self):
        from halucinator.hal_config import HALMachineConfig
        m = HALMachineConfig(
            arch="arm",
            interrupt_controller={
                "type": "gicv2",
                "gicd_base": 0x08000000,
            },
        )
        c = m.build_irq_controller()
        assert isinstance(c, GicController)
        assert c.version == 2
        assert c.gicd_base == 0x08000000

    def test_block_must_be_mapping(self):
        from halucinator.hal_config import HALMachineConfig
        with pytest.raises(ValueError, match="must be a mapping"):
            HALMachineConfig(arch="arm", interrupt_controller="gicv2")

    def test_block_requires_type(self):
        from halucinator.hal_config import HALMachineConfig
        with pytest.raises(ValueError, match="missing required `type`"):
            HALMachineConfig(arch="arm",
                             interrupt_controller={"gicd_base": 0x08000000})

    def test_arm_no_block_no_default(self):
        from halucinator.hal_config import HALMachineConfig
        m = HALMachineConfig(arch="arm")
        assert m.build_irq_controller() is None
