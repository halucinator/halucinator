"""MIPS interrupt-pending bits (CP0 Cause register).

MIPS doesn't have an architecture-mandated interrupt controller;
instead every MIPS core has 8 interrupt-pending bits in the CP0
`Cause` register:

  Cause.IP[1:0]   Software interrupts (set by mtc0 from kernel code)
  Cause.IP[7:2]   Hardware interrupts (asserted by external pins)

To deliver IRQ N (0 ≤ N ≤ 7) we read Cause, set bit (8 + N), and
write it back. If `Status.IE` is set and `Status.IM[N]` allows it,
the CPU takes Interrupt exception (Cause.ExcCode = 0) on the next
instruction boundary.

The IP bits sit at bits 8..15 of Cause:

  bit 31 30 ..  16   15  14 13 12 11 10  9  8   7  ..  0
      BD ─── ── ──── IP7 IP6 IP5 IP4 IP3 IP2 IP1 IP0  ExcCode

So IP[N] is at bit (8 + N).

Caveats noted in the plan:

* The QEMU MIPS emulator reads the IRQ pin via cpu_mips_irq line
  objects; setting Cause.IP from outside via the GDB stub does
  *not* trigger the interrupt logic for QEMU MIPS. The QEMU /
  avatar2 backends override `inject_irq` to also call qmp_qom_set
  on the CPU's irq[N] property (added in qemu_backend.py).
* unicorn and ghidra read Cause directly, so this RMW path works
  without help.

Most embedded MIPS cores wire one or more of the 6 hardware IP bits
into an external INTC. Halucinator's bp_handlers replace real
peripherals, so the firmware typically just polls a memory location
the ISR sets — it doesn't read its INTC's status registers — and
the simple Cause.IP write is enough.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from . import IrqConfigError, IrqController

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


_CAUSE_IP_SHIFT = 8
_MAX_IP = 7


class MipsController(IrqController):
    name = "mips"

    def __init__(
        self,
        irq_simple_entry: int | None = None,
        irq_fired_addr: int | None = None,
        irq_number_addr: int | None = None,
        irq_fired_phys_addr: int | None = None,
        irq_number_phys_addr: int | None = None,
        options: Dict[str, Any] | None = None,
    ) -> None:
        # Trampoline address for in-process backends without a real
        # CP0 exception entry model. Same convention as
        # GicController.irq_simple_entry: receives the IRQ number in
        # the first argument register and returns via the link
        # register.
        self.irq_simple_entry = irq_simple_entry
        # Shadow-state addresses for in-process backends that
        # bypass the firmware ISR entirely and just write the
        # post-ack globals. *_addr is the kseg-virtual view the
        # firmware uses (so unicorn / ghidra, which don't model
        # MIPS MMU, read/write it directly); *_phys_addr is the
        # physical translation for avatar-qemu's avatar-shadow-irq
        # QMP path (which calls cpu_physical_memory_write). MIPS
        # kseg0/kseg1 are hardware-mapped to the low 512 MB of
        # physical address space, and our YAML also lists `alias_at`
        # mirrors of the ram region at those physical addresses, so
        # the two views address the same bytes.
        self.irq_fired_addr = irq_fired_addr
        self.irq_number_addr = irq_number_addr
        self.irq_fired_phys_addr = irq_fired_phys_addr
        self.irq_number_phys_addr = irq_number_phys_addr
        self.options = options or {}

    def trigger(self, backend: "HalBackend", num: int) -> None:
        if num < 0 or num > _MAX_IP:
            raise IrqConfigError(
                f"MIPS Cause.IP supports IRQ 0..{_MAX_IP}, got {num}"
            )
        try:
            cause = backend.read_register("cause")
        except Exception as exc:  # noqa: BLE001
            raise IrqConfigError(
                f"MIPS controller: backend doesn't expose CP0 'cause' "
                f"register: {exc}"
            ) from None
        backend.write_register("cause", cause | (1 << (_CAUSE_IP_SHIFT + num)))
