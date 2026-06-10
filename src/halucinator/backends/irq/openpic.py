"""OpenPIC (embedded PowerPC interrupt controller).

Embedded PowerPC SoCs (e500, MPC8xx-derivatives) use OpenPIC — a
distributor-and-cpu-interface model in the same family as the GIC,
but predating it. Server-class POWER chips use XICS or XIVE which
are out of scope.

To make external interrupt source N pending, we write 1 to the
source's Interrupt Pending Register (IPIDR), located at:

  base + 0x10000 + N * 0x20

(The IPIDR for source N lives in a 32-byte slot; offset 0 within
the slot is the IPI Dispatch Register, which on a write makes the
source assert its line into the connected CPU.)

IPIDR is in the "global" space; sources are typically 0..255 on
e500-class chips. Halucinator's bp_handlers replace the real
peripherals, so as long as the firmware's IRQ unmask + the OpenPIC
priority/destination defaults are reasonable, the CPU sees the
exception.

The OpenPIC base is platform-specific (e.g. 0x40040000 on the QEMU
ppce500 machine), declared in YAML.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from . import IrqConfigError, IrqController

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


_GLOBAL_IPIDR_OFFSET = 0x10000
_SOURCE_STRIDE = 0x20
_MAX_SOURCE = 255


class OpenPicController(IrqController):
    name = "openpic"

    def __init__(
        self,
        openpic_base: int,
        irq_fired_addr: int | None = None,
        irq_number_addr: int | None = None,
        options: Dict[str, Any] | None = None,
    ) -> None:
        self.openpic_base = openpic_base
        # Shadow-state addresses for in-process backends that
        # bypass the firmware ISR entirely; same convention as the
        # MIPS controller. Not used by avatar2/qemu/renode (real
        # OpenPIC peripheral handles delivery there).
        self.irq_fired_addr = irq_fired_addr
        self.irq_number_addr = irq_number_addr
        self.options = options or {}

    def trigger(self, backend: "HalBackend", num: int) -> None:
        if num < 0 or num > _MAX_SOURCE:
            raise IrqConfigError(
                f"OpenPIC supports source 0..{_MAX_SOURCE}, got {num}"
            )
        addr = self.openpic_base + _GLOBAL_IPIDR_OFFSET + num * _SOURCE_STRIDE
        backend.write_memory(addr, 4, 1)
