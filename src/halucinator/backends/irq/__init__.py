"""halucinator.backends.irq — multi-arch interrupt controller abstraction.

Each ISA HALucinator supports has a different way to make an external
interrupt pending so the CPU takes the exception:

  cortex-m3   NVIC at 0xE000E000 — write the Set-Pending Register
  arm / arm64 GIC distributor (v2 or v3) — write GICD_ISPENDR
  mips        CP0 Cause.IP[N] read-modify-write
  powerpc /   OpenPIC source IPIDR write (embedded), or set CPU's
  ppc64       external-IRQ pin via QMP qom-set / Renode OnGPIO

A single backend cannot know all five; the plumbing depends on what
the platform wires the controller into. Halucinator's config declares
the controller (and any platform-specific addresses) once, and every
backend then routes its `inject_irq(N)` through the same
`IrqController.trigger(backend, N)` call.

Backends keep their existing fast-path overrides where one exists
(notably avatar-armv7m-inject-irq for QEMU + Cortex-M3 — it integrates
with avatar-qemu's ignore-irq-return watchman semantics, which the
generic NVIC ISPR write doesn't). Override `Backend.inject_irq` to
short-circuit the controller; otherwise the base class delegates here.

Public surface:

  IrqController            — abstract base
  build_irq_controller()   — factory: HALMachineConfig -> IrqController
  default_for_arch()       — per-arch default controller spec
  IrqConfigError           — raised on invalid / missing controller config
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


class IrqConfigError(ValueError):
    """Raised when the YAML interrupt_controller block is missing or
    inconsistent (e.g. type=gicv2 with no gicd_base on an arm64 target)."""


@dataclass
class IrqControllerSpec:
    """Parsed form of the YAML `machine.interrupt_controller` block.

    `type` selects the controller class; the remaining fields are
    type-specific (gicd_base for gicv2/v3; openpic_base for openpic;
    ignored for cortex_m + mips).
    """
    type: str
    gicd_base: Optional[int] = None
    openpic_base: Optional[int] = None
    options: Dict[str, Any] = field(default_factory=dict)


class IrqController(ABC):
    """One-method interface every controller implements."""

    name: str = "abstract"

    @abstractmethod
    def trigger(self, backend: "HalBackend", num: int) -> None:
        """Make IRQ `num` pending on `backend`.

        Implementations MUST be idempotent in the sense that the
        write itself doesn't depend on whether the line was already
        pending — the CPU's controller hardware handles edge/level
        semantics. Implementations SHOULD raise IrqConfigError on
        out-of-range `num` for the controller (e.g. NVIC supports
        0..495; GICv2 0..1019; etc.).
        """


# ---------------------------------------------------------------------------
# Per-arch default controller (used when YAML omits the block)
# ---------------------------------------------------------------------------
#
# Cortex-M's NVIC base is fixed by the architecture. MIPS's CP0
# mechanism needs no MMIO. The other arches have no canonical default
# — the user must declare gicd_base / openpic_base for the platform.

_DEFAULTS: Dict[str, Optional[IrqControllerSpec]] = {
    "cortex-m3": IrqControllerSpec(type="cortex_m"),
    "mips":      IrqControllerSpec(type="mips"),
    # arm / arm64 / powerpc / ppc64 — no default; user must specify.
    "arm":       None,
    "arm64":     None,
    "powerpc":   None,
    "ppc64":     None,
    "powerpc:MPC8XX": None,
}


def default_for_arch(arch: str) -> Optional[IrqControllerSpec]:
    """Return the default controller spec for `arch`, or None if the
    user must declare one in YAML."""
    return _DEFAULTS.get(arch)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _instantiate(spec: IrqControllerSpec) -> IrqController:
    """Construct the IrqController instance from a parsed spec."""
    # Imports are local so a HalBackend that never calls inject_irq
    # doesn't pay the import cost.
    if spec.type == "cortex_m":
        from .cortex_m import CortexMController
        return CortexMController()
    if spec.type in ("gicv2", "gicv3"):
        from .gic import GicController
        if spec.gicd_base is None:
            raise IrqConfigError(
                f"interrupt_controller: type={spec.type!r} requires "
                f"`gicd_base` (the GIC distributor base address). "
                f"On QEMU's `virt` machine that's 0x08000000."
            )
        opts = spec.options or {}
        gicc_base = opts.pop("gicc_base", None)
        irq_simple_entry = opts.pop("irq_simple_entry", None)
        irq_fired_addr = opts.pop("irq_fired_addr", None)
        irq_number_addr = opts.pop("irq_number_addr", None)
        return GicController(gicd_base=spec.gicd_base,
                             version=2 if spec.type == "gicv2" else 3,
                             gicc_base=gicc_base,
                             irq_simple_entry=irq_simple_entry,
                             irq_fired_addr=irq_fired_addr,
                             irq_number_addr=irq_number_addr,
                             options=opts)
    if spec.type == "mips":
        from .mips import MipsController
        opts = spec.options or {}
        irq_simple_entry = opts.pop("irq_simple_entry", None)
        irq_fired_addr = opts.pop("irq_fired_addr", None)
        irq_number_addr = opts.pop("irq_number_addr", None)
        irq_fired_phys_addr = opts.pop("irq_fired_phys_addr", None)
        irq_number_phys_addr = opts.pop("irq_number_phys_addr", None)
        return MipsController(
            irq_simple_entry=irq_simple_entry,
            irq_fired_addr=irq_fired_addr,
            irq_number_addr=irq_number_addr,
            irq_fired_phys_addr=irq_fired_phys_addr,
            irq_number_phys_addr=irq_number_phys_addr,
            options=opts,
        )
    if spec.type == "openpic":
        from .openpic import OpenPicController
        if spec.openpic_base is None:
            raise IrqConfigError(
                "interrupt_controller: type='openpic' requires "
                "`openpic_base` (the OpenPIC base address)."
            )
        opts = spec.options or {}
        irq_fired_addr = opts.pop("irq_fired_addr", None)
        irq_number_addr = opts.pop("irq_number_addr", None)
        return OpenPicController(
            openpic_base=spec.openpic_base,
            irq_fired_addr=irq_fired_addr,
            irq_number_addr=irq_number_addr,
            options=opts,
        )
    if spec.type in ("arm_vic", "vic"):
        from .arm_vic import ArmVicController
        opts = dict(spec.options or {})
        return ArmVicController(
            vector_base=opts.pop("vector_base", 0x0),
            isr_addr=opts.pop("isr_addr", None),
            irq_simple_entry=opts.pop("irq_simple_entry", None),
            options=opts,
        )
    if spec.type in ("x86_pic", "x86", "i8259"):
        from .x86_pic import X86PicController
        opts = dict(spec.options or {})
        return X86PicController(
            isr_addr=opts.pop("isr_addr", None),
            int_ent=opts.pop("int_ent", None),
            int_exit=opts.pop("int_exit", None),
            stub_addr=opts.pop("stub_addr", 0x7000),
            isr_arg=opts.pop("isr_arg", 0),
            vector=opts.pop("vector", 0x20),
            options=opts,
        )
    raise IrqConfigError(
        f"Unknown interrupt_controller type: {spec.type!r}. "
        f"Supported: cortex_m, gicv2, gicv3, arm_vic, mips, openpic, x86_pic."
    )


def build_irq_controller(
    arch: str,
    spec: Optional[IrqControllerSpec] = None,
) -> Optional[IrqController]:
    """Build the controller for the given arch.

    If `spec` is None, fall back to the per-arch default. If the arch
    has no default and no spec is given, return None — callers that
    actually invoke `inject_irq` will raise IrqConfigError there with
    a helpful message; backends that never inject can run without
    declaring one.
    """
    effective = spec if spec is not None else default_for_arch(arch)
    if effective is None:
        return None
    return _instantiate(effective)


__all__ = [
    "IrqConfigError",
    "IrqController",
    "IrqControllerSpec",
    "build_irq_controller",
    "default_for_arch",
]
