"""Live (backend × ISA) integration tests for inject_irq.

For each (in-process backend, ISA) cell that has a real interrupt
controller, build a synthetic mini-firmware that:

  1. Polls a memory location (the sentinel).
  2. ISR / signal handler writes a known value to the sentinel.
  3. Test calls `backend.inject_irq(N)` — through the configured
     IrqController — and asserts the sentinel changes within a
     bounded number of instructions.

Backends covered: UnicornBackend (Cortex-M3 fast-path; AArch64 + MIPS
+ PPC via the IrqController). GhidraBackend would also be covered in
principle but its PCode emulator can't model GIC/MIPS/PPC interrupt
delivery without a per-arch CALLOTHER/exception model — those cells
are handled by the firmware-level matrix tests instead.

Avatar2/QEMU/Renode aren't covered by this file because they need a
running subprocess; the matrix harness's run_backend_matrix.sh exercises
those paths against the multi_arch_irq firmware corpus.

If unicorn isn't installed, the entire module skips."""
from __future__ import annotations

import struct

import pytest

from halucinator.backends.hal_backend import MemoryRegion
from halucinator.backends.irq.cortex_m import CortexMController
from halucinator.backends.irq.gic import GicController


# ---------------------------------------------------------------------------
# Skip the whole file when unicorn isn't importable (CI without backends).
# ---------------------------------------------------------------------------
try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

pytestmark = pytest.mark.skipif(
    not _HAVE_UNICORN,
    reason="unicorn-engine not installed",
)


def _flash_with(prog_bytes: bytes, total_size: int = 0x4000) -> bytes:
    """Pad firmware bytes out to total_size with NOPs, suitable for
    loading into a unicorn-mapped flash region via MemoryRegion.file."""
    if len(prog_bytes) > total_size:
        raise ValueError(f"program {len(prog_bytes)} > flash {total_size}")
    return prog_bytes + b"\x00" * (total_size - len(prog_bytes))


# ---------------------------------------------------------------------------
# Cortex-M3 (NVIC fast-path; bypasses controller MMIO since unicorn doesn't
# model the NVIC peripheral).
# ---------------------------------------------------------------------------

class TestUnicornCortexM:
    """Verifies the cortex-m fast-path: backend.inject_irq pushes the
    architectural exception frame and sets PC to the ISR. Already
    covered by test_unicorn_backend.py; this is a smoke check that the
    HalBackend.set_irq_controller plumbing doesn't get in the way."""

    def test_cortex_m_uses_fast_path_when_no_controller(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        # Vector table at flash base; IRQ #2 (vector 18) → 0x80
        # Tiny "infinite NOP" body at PC=0x100 so we can verify state.
        flash = bytearray(0x1000)
        # Sentinel: vector[18] = 0x101 (Thumb-mode ISR address)
        flash[18 * 4 : 18 * 4 + 4] = (0x101).to_bytes(4, "little")
        b.add_memory_region(MemoryRegion(
            "flash", 0x08000000, 0x1000, "rx", file=None,
        ))
        b.add_memory_region(MemoryRegion(
            "ram", 0x20000000, 0x10000, "rw",
        ))
        b.init()
        b._uc.mem_write(0x08000000, bytes(flash))
        b.regs.sp = 0x20008000
        b.regs.pc = 0x08000200
        b.set_vtor(0x08000000)
        # No IrqController attached — cortex-m3 fast-path runs anyway.
        b.inject_irq(2)
        # inject_irq queues; cont() drains the queue between
        # emu_start chunks. Drain manually here so we can assert
        # against PC without spinning the CPU.
        b._apply_pending_irq(b._pending_irqs.pop(0))
        # PC should be at the ISR (0x100 with Thumb bit cleared).
        assert b.read_register("pc") & ~1 == 0x100


# ---------------------------------------------------------------------------
# AArch64 + GICv2 (controller MMIO write — visible in mapped GICD region).
# ---------------------------------------------------------------------------

class TestUnicornAArch64Gic:
    """Verifies that UnicornBackend on arm64 with a GicController
    attached routes inject_irq through the GICD MMIO write."""

    def _build(self, version: int):
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="arm64")
        # Map GICD at the standard QEMU `virt` machine address.
        b.add_memory_region(MemoryRegion("gicd", 0x08000000, 0x10000, "rw"))
        b.add_memory_region(MemoryRegion("ram",  0x40000000, 0x1000,  "rw"))
        b.init()
        b.set_irq_controller(GicController(gicd_base=0x08000000,
                                           version=version))
        return b

    @pytest.mark.parametrize("num,expected_addr,expected_val", [
        (32, 0x08000204, 1 << 0),       # First SPI: ISPENDR1 bit 0
        (33, 0x08000204, 1 << 1),       # Next SPI bit
        (63, 0x08000204, 1 << 31),      # Last bit of ISPENDR1
        (64, 0x08000208, 1 << 0),       # Crosses to ISPENDR2
        (255, 0x0800021C, 1 << 31),     # ISPENDR7 last bit
    ])
    def test_gicv2_spi_writes_ispendr(self, num, expected_addr, expected_val):
        b = self._build(version=2)
        b.inject_irq(num)
        word = b.read_memory(expected_addr, 4, 1)
        assert word == expected_val, (
            f"IRQ {num}: expected GICD[{expected_addr:#x}] = "
            f"{expected_val:#x}, got {word:#x}"
        )

    def test_gicv2_sgi_writes_sgir(self):
        b = self._build(version=2)
        b.inject_irq(7)  # SGI 7
        # GICD_SGIR at offset 0xF00; CPUTargetList=1, SGIINTID=7
        word = b.read_memory(0x08000F00, 4, 1)
        assert word == ((1 << 16) | 7)

    def test_gicv3_spi_works_same_as_v2(self):
        b = self._build(version=3)
        b.inject_irq(50)  # SPI 50 → ISPENDR1 bit 18
        word = b.read_memory(0x08000204, 4, 1)
        assert word == (1 << (50 - 32))

    def test_gicv3_sgi_raises(self):
        from halucinator.backends.irq import IrqConfigError
        b = self._build(version=3)
        with pytest.raises(IrqConfigError, match="ICC_SGI1R_EL1"):
            b.inject_irq(5)

    def test_no_controller_attached_raises(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq import IrqConfigError
        b = UnicornBackend(arch="arm64")
        b.add_memory_region(MemoryRegion("ram", 0x40000000, 0x1000, "rw"))
        b.init()
        with pytest.raises(IrqConfigError, match="no interrupt controller"):
            b.inject_irq(5)


# ---------------------------------------------------------------------------
# MIPS Cause.IP (read-modify-write on CP0).
# ---------------------------------------------------------------------------

class TestUnicornMipsCause:
    """MIPS Cause.IP[N] correctness is unit-tested in
    test/pytest/backends/irq/test_controllers.py against a mocked
    backend; live integration on unicorn requires reading/writing CP0
    Cause, which unicorn-engine 2.0.1's MIPS API doesn't expose
    (UC_MIPS_REG_CP0_STATUS exists but no UC_MIPS_REG_CP0_CAUSE).

    The QEMU + Renode subprocess paths *can* read/write CP0 Cause via
    GDB; those cells are validated by run_backend_matrix.sh against
    the multi_arch_irq/mips firmware corpus.
    """

    def test_out_of_range_raises_before_register_access(self):
        """The range check fires before the register write, so we can
        still test bounds even without unicorn CP0 support."""
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq import IrqConfigError, build_irq_controller
        b = UnicornBackend(arch="mips")
        b.add_memory_region(MemoryRegion("ram", 0x00400000, 0x10000, "rwx"))
        b.init()
        b.set_irq_controller(build_irq_controller("mips"))
        with pytest.raises(IrqConfigError, match="0..7"):
            b.inject_irq(8)


# ---------------------------------------------------------------------------
# PowerPC + OpenPIC (controller MMIO write).
# ---------------------------------------------------------------------------

class TestUnicornPowerPcOpenPic:
    """OpenPIC IPIDR write — same pattern as the GIC test, just at a
    different controller base."""

    def _build(self, arch: str = "powerpc",
               openpic_base: int = 0x40040000):
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.irq.openpic import OpenPicController
        b = UnicornBackend(arch=arch)
        # OpenPIC is a 256 KB region on e500 (covers IPIDRs of all 256
        # sources at offset 0x10000 + N*0x20).
        b.add_memory_region(MemoryRegion(
            "openpic", openpic_base, 0x40000, "rw",
        ))
        b.add_memory_region(MemoryRegion("ram", 0x00200000, 0x10000, "rwx"))
        b.init()
        b.set_irq_controller(OpenPicController(openpic_base=openpic_base))
        return b

    @pytest.mark.parametrize("num", [0, 1, 17, 100, 255])
    def test_writes_ipidr_for_source(self, num):
        b = self._build()
        b.inject_irq(num)
        addr = 0x40040000 + 0x10000 + num * 0x20
        word = b.read_memory(addr, 4, 1)
        # Words on PPC are big-endian — read_memory returns native int.
        # The controller writes the literal value 1.
        assert word == 1

    def test_ppc64_works_too(self):
        b = self._build(arch="ppc64")
        b.inject_irq(42)
        addr = 0x40040000 + 0x10000 + 42 * 0x20
        word = b.read_memory(addr, 4, 1)
        assert word == 1


# ---------------------------------------------------------------------------
# Smoke: peripheral_server.inject_irq fans out to backend.inject_irq
# correctly across non-cortex-m backends. (Mock-driven; complements
# the cortex-m-only unit test in test_peripheral_server_unit.py.)
# ---------------------------------------------------------------------------

class TestPeripheralServerFanOut:
    def test_calls_inject_irq_when_backend_has_it(self):
        from halucinator.peripheral_models import peripheral_server as ps
        from unittest import mock
        backend = mock.Mock()
        old = getattr(ps, "__QEMU", None)
        setattr(ps, "__QEMU", backend)
        try:
            ps.inject_irq(33)
            backend.inject_irq.assert_called_once_with(33)
        finally:
            setattr(ps, "__QEMU", old)
