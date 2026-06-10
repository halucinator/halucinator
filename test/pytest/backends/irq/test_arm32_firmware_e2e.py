"""Real-firmware end-to-end proof for the ARM IRQ deliverer.

Loads the actual multi_arch_irq/arm32 `test_irq.bin` (a bare-metal ARMv7-A
image that programs a GICv2, enables IRQs, and spins on a flag its
IRQ_Handler sets), drives it under a live UnicornBackend past gic_init,
injects IRQ 33 through the backend's normal inject path, and asserts the
firmware's OWN handler ran: irq_fired==1 and irq_number==33 in guest RAM.

This exercises the whole chain end to end on real firmware:
  firmware GIC setup -> backend.inject_irq -> ArmExceptionDeliverer (FRAME,
  GICC_IAR shadow) -> vector 0x18 -> _irq_entry -> IRQ_Handler -> globals.

If the deliverer regressed, the firmware would spin forever and the
globals would stay zero.
"""
from __future__ import annotations

import os
import struct

import pytest

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

pytestmark = pytest.mark.skipif(not _HAVE_UNICORN,
                                reason="unicorn-engine not installed")

_FW = os.path.join(
    os.path.dirname(__file__),
    "../../../multi_arch_irq/arm32/firmware/test_irq.bin",
)
# From test_irq_addrs/config + the firmware .bss layout.
_RAM_BASE = 0x40000000
_GICD_BASE = 0x08000000
_GICC_BASE = 0x08010000
_IRQ_FIRED_ADDR = 0x40000004
_IRQ_NUMBER_ADDR = 0x40000008
_TEST_IRQ = 33


@pytest.mark.skipif(not os.path.exists(_FW), reason="arm32 test_irq.bin missing")
def test_arm32_firmware_irq_delivered_end_to_end():
    # The firmware's _reset uses `cps` (ARMv6+); the backend default
    # ARM926 (ARMv5) rejects it. The config models a cortex-a9.
    prev = os.environ.get("HAL_ARM_CPU_MODEL")
    os.environ["HAL_ARM_CPU_MODEL"] = "UC_CPU_ARM_CORTEX_A9"
    try:
        _run_firmware_irq()
    finally:
        if prev is None:
            os.environ.pop("HAL_ARM_CPU_MODEL", None)
        else:
            os.environ["HAL_ARM_CPU_MODEL"] = prev


def _run_firmware_irq():
    from halucinator.backends.unicorn_backend import UnicornBackend
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.irq.gic import GicController

    fw = open(_FW, "rb").read()
    b = UnicornBackend(arch="arm")
    b.add_memory_region(MemoryRegion("flash", 0x0, 0x10000, "rwx"))
    b.add_memory_region(MemoryRegion("ram", _RAM_BASE, 0x10000, "rw"))
    # GIC window mapped as plain RW (unicorn models no GIC); covers both
    # the distributor and the CPU interface at +0x10000.
    b.add_memory_region(MemoryRegion("gic", _GICD_BASE, 0x11000, "rw"))
    b.init()
    b._uc.mem_write(0x0, fw)

    # Wire the controller exactly as a gicv2 config would. No separate
    # delivery plan -> the ARM dispatch falls to _apply_pending_irq_armv7a,
    # which (now) delegates to ArmExceptionDeliverer with the controller's
    # gicc_base for the GICC_IAR shadow. set_vtor gives the vector base.
    b.set_irq_controller(GicController(gicd_base=_GICD_BASE,
                                       gicc_base=_GICC_BASE))
    b.set_vtor(0x0)

    # Boot from the reset vector and run enough to clear gic_init + the
    # `cpsie i` that unmasks IRQs, landing in the `while(!irq_fired)` spin.
    b._uc.emu_start(0x0, 0xFFFFFFFF, count=20000)

    # Pre-condition: handler hasn't run yet.
    assert struct.unpack("<I", b._uc.mem_read(_IRQ_FIRED_ADDR, 4))[0] == 0

    # Inject through the backend's dispatch-thread delivery entry point
    # (safe: emulation is stopped between emu_start calls).
    b._apply_pending_irq(_TEST_IRQ)

    # The deliverer should have vectored us into the IRQ path.
    assert b.read_register("pc") == 0x18
    assert b.read_register("cpsr") & 0x1F == 0x12   # IRQ mode

    # Continue: _irq_entry -> IRQ_Handler runs, ACKs GICC_IAR (shadowed to
    # 33), sets the globals, EOIs, returns to the spin loop.
    pc = b.read_register("pc")
    b._uc.emu_start(pc, 0xFFFFFFFF, count=20000)

    fired = struct.unpack("<I", b._uc.mem_read(_IRQ_FIRED_ADDR, 4))[0]
    number = struct.unpack("<I", b._uc.mem_read(_IRQ_NUMBER_ADDR, 4))[0]
    assert fired == 1, f"firmware IRQ_Handler never set irq_fired (got {fired})"
    assert number == _TEST_IRQ, f"wrong intid: {number} (GICC_IAR shadow?)"
