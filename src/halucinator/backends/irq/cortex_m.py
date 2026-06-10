"""Cortex-M NVIC controller.

ARMv7-M / ARMv8-M architectures embed the NVIC (Nested Vector
Interrupt Controller) into the CPU at a fixed address. To make
external interrupt N pending, write `1 << (N % 32)` to
`NVIC_ISPR{N // 32}` (Interrupt Set-Pending Register).

  base + 0x100..0x13C   NVIC_ISER0..15  (Interrupt Set-Enable)
  base + 0x180..0x1BC   NVIC_ICER0..15  (Interrupt Clear-Enable)
  base + 0x200..0x23C   NVIC_ISPR0..15  (Interrupt Set-Pending)  ← here
  base + 0x280..0x2BC   NVIC_ICPR0..15  (Interrupt Clear-Pending)

Architectural NVIC base is 0xE000E000; ISPR0 lives at 0xE000E200.
ARMv7-M supports IRQs 0..495 (16 registers × 32 bits). ARMv6-M caps
at 32. Halucinator targets v7-M so we accept up to 495.

Backends that have a faster path (avatar-armv7m-inject-irq QMP, or
the in-process synthetic exception frame on unicorn/ghidra) override
`Backend.inject_irq` directly and never reach this module. This
module is the fallback for any backend that didn't override.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import IrqConfigError, IrqController

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


_NVIC_ISPR_BASE = 0xE000E200
_NVIC_MAX_IRQ = 495   # ARMv7-M: 16 ISPR words × 32 bits − 1


class CortexMController(IrqController):
    name = "cortex_m"

    def trigger(self, backend: "HalBackend", num: int) -> None:
        if num < 0 or num > _NVIC_MAX_IRQ:
            raise IrqConfigError(
                f"Cortex-M NVIC supports IRQ 0..{_NVIC_MAX_IRQ}, got {num}"
            )
        word = num // 32
        bit = num % 32
        addr = _NVIC_ISPR_BASE + word * 4
        backend.write_memory(addr, 4, 1 << bit)
