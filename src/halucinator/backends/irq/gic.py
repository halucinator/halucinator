"""ARM Generic Interrupt Controller (GICv2 / GICv3) — distributor MMIO.

Cortex-A and AArch64 systems use the GIC instead of the Cortex-M
NVIC. The GIC has two parts:

  * Distributor (GICD) — system-wide, MMIO at a platform-specific
    base. Where we set / clear / enable / mask interrupt source
    pending bits.
  * CPU Interface (GICC for v2, system registers ICC_* for v3) —
    per-CPU, where each core acknowledges interrupts.

To make a Shared Peripheral Interrupt (SPI, IRQ id 32..1019) pending,
both v2 and v3 take the same MMIO write:

  GICD + 0x200 + (N // 32) * 4   ← GICD_ISPENDR<n>
  bit (N % 32)                    ← set to 1

Software-Generated Interrupts (SGIs, IRQ 0..15) on GICv2 take a
different write to GICD_SGIR (0xF00). On GICv3 they're sent via the
system register ICC_SGI1R_EL1 — which most of our backends can't
reach (would need write_register() with 64-bit system-register
support). For SGI on GICv3 we raise IrqConfigError pointing the user
at the limitation.

Private Peripheral Interrupts (PPIs, IRQ 16..31) are per-CPU — the
ISPENDR0 write is the right path on v2; on v3 it should be
GICR_ISPENDR0 in the redistributor, but again most backends don't
expose that cleanly. We accept PPIs via the ISPENDR0 path which
works on QEMU's `virt` machine for v2 and on simple v3 setups.

The GICD base is platform-specific (0x08000000 on QEMU's `virt`,
different on real chips), so the user declares it in the YAML
`machine.interrupt_controller.gicd_base` field.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from . import IrqConfigError, IrqController

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


_GICD_ISPENDR0 = 0x200
_GICD_SGIR     = 0xF00
_MAX_SPI       = 1019  # GICv2 + GICv3 max SPI id


class GicController(IrqController):
    """GIC distributor controller (covers GICv2 + GICv3 SPIs)."""

    def __init__(
        self,
        gicd_base: int,
        version: int = 2,
        gicc_base: int | None = None,
        irq_simple_entry: int | None = None,
        irq_fired_addr: int | None = None,
        irq_number_addr: int | None = None,
        options: Dict[str, Any] | None = None,
    ) -> None:
        if version not in (2, 3):
            raise IrqConfigError(
                f"GIC version must be 2 or 3, got {version}"
            )
        self.gicd_base = gicd_base
        # Optional CPU-interface base. Backends that don't model a
        # real GIC (unicorn, ghidra) use this address to stash the
        # acknowledged IRQ ID into GICC_IAR so the firmware's ISR
        # reads the right interrupt number.
        self.gicc_base = gicc_base
        # Optional firmware-provided IRQ entry address. arm64
        # in-process backends can't model VBAR_EL1 + ERET, so the
        # firmware exposes a callable trampoline (LR = interrupted
        # PC, PC = irq_simple_entry, return via plain `ret`).
        self.irq_simple_entry = irq_simple_entry
        # Shadow-state addresses for in-process backends that
        # bypass the firmware ISR entirely and just write the
        # post-ack globals — same convention as MipsController /
        # OpenPicController. The arm/arm64 firmware corpus
        # exposes irq_fired / irq_number at known addresses; the
        # in-process Ghidra path uses these for delivery.
        self.irq_fired_addr = irq_fired_addr
        self.irq_number_addr = irq_number_addr
        self.version = version
        self.options = options or {}
        self.name = f"gicv{version}"

    def trigger(self, backend: "HalBackend", num: int) -> None:
        if num < 0 or num > _MAX_SPI:
            raise IrqConfigError(
                f"GIC supports IRQ 0..{_MAX_SPI}, got {num}"
            )
        if 0 <= num < 16:
            # Software-Generated Interrupt
            if self.version == 2:
                # GICD_SGIR: write {target_list_filter=0, CPUTargetList=1<<0,
                #   NSATT=0, SGIINTID=num} — i.e. send SGI #num to CPU 0.
                value = (0 << 24) | (1 << 16) | num
                backend.write_memory(self.gicd_base + _GICD_SGIR, 4, value)
                return
            # GICv3: SGIs go through the ICC_SGI1R_EL1 system register —
            # not reachable from a generic backend.write_memory. Raise
            # with a clear message so the user knows what's missing.
            raise IrqConfigError(
                "SGI delivery on GICv3 requires a write to the "
                "ICC_SGI1R_EL1 system register, which the generic "
                "backend interface doesn't expose. Use SPI ids "
                "(>= 32) instead, or the cortex-m backend."
            )
        # SPI (32..) and PPI (16..31): set the pending bit in
        # GICD_ISPENDR<N//32>.
        word = num // 32
        bit = num % 32
        addr = self.gicd_base + _GICD_ISPENDR0 + word * 4
        backend.write_memory(addr, 4, 1 << bit)
