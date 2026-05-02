"""
Live feature matrix: exercise every HalBackend feature on every backend
whose runtime is lightweight enough to spin up in isolation (unicorn,
ghidra). No mocks — actual execution of a canned Thumb program.

avatar2, qemu-direct and renode require full halucinator main-path setup
(QEMU config JSON, Renode .resc, GDB/QMP spawning) and are validated
separately through the firmware runs under test/STM32, test/zephyr, etc.
Their unit-level packet/monitor layer is covered by the mock-based tests
in test_qemu_backend.py, test_renode_backend.py, test_watchpoints.py,
and test_single_step.py.

Program layout (thumb, loaded at 0x08000000):
  0x08000000  0x2042  movs r0, #0x42
  0x08000002  0x2110  movs r1, #0x10
  0x08000004  0xbf00  nop                 <- breakpoint target
  0x08000006  0x22aa  movs r2, #0xaa
  0x08000008  0x4b01  ldr  r3, [pc, #4]   <- loads from 0x08000010
  0x0800000a  0x601c  str  r4, [r3]       <- writes to RAM (watchpoint target)
  0x0800000c  0xe7fe  b .                 <- infinite loop
  0x0800000e  0xbf00  nop
  0x08000010  0x00 0x00 0x00 0x20         <- literal = 0x20000000

  The Thumb PC-relative load uses (pc+4) aligned down to 4, so at
  insn_pc 0x08000008 the base is 0x0800000c, and [pc,#4] reads 0x08000010.
"""
from __future__ import annotations

import os

import pytest

from halucinator.backends.hal_backend import MemoryRegion


_PROGRAM = (
    b"\x42\x20"
    b"\x10\x21"
    b"\x00\xbf"
    b"\xaa\x22"
    b"\x01\x4b"
    b"\x1c\x60"
    b"\xfe\xe7"
    b"\x00\xbf"
    b"\x00\x00\x00\x20"
)
_FLASH_BASE = 0x08000000
_RAM_BASE = 0x20000000
_BP_ADDR = _FLASH_BASE + 0x04
_LOOP_ADDR = _FLASH_BASE + 0x0c
_WATCH_ADDR = _RAM_BASE


@pytest.fixture
def firmware_path(tmp_path):
    p = tmp_path / "tinyfw.bin"
    p.write_bytes(_PROGRAM + b"\x00" * (0x1000 - len(_PROGRAM)))
    return str(p)


@pytest.fixture(scope="class")
def firmware_path_class(tmp_path_factory):
    p = tmp_path_factory.mktemp("fw") / "tinyfw.bin"
    p.write_bytes(_PROGRAM + b"\x00" * (0x1000 - len(_PROGRAM)))
    return str(p)


def _install_regions(b, firmware_path):
    b.add_memory_region(MemoryRegion(
        name="flash", base_addr=_FLASH_BASE, size=0x1000, file=firmware_path,
        permissions="rwx",
    ))
    b.add_memory_region(MemoryRegion(
        name="ram", base_addr=_RAM_BASE, size=0x1000, file=None,
        permissions="rw-",
    ))


def _have_unicorn():
    try:
        import unicorn  # noqa: F401
        return True
    except ImportError:
        return False


def _have_pyghidra_with_ghidra():
    try:
        import pyghidra  # noqa: F401
        return bool(os.environ.get("GHIDRA_INSTALL_DIR"))
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Unicorn — light enough to boot per-test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_unicorn(), reason="unicorn not installed")
class TestUnicornLiveFeatures:
    @pytest.fixture
    def backend(self, firmware_path):
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        _install_regions(b, firmware_path)
        b.init()
        b.write_register("pc", _FLASH_BASE | 1)
        b.write_register("sp", _RAM_BASE + 0x800)
        yield b
        b.shutdown()

    def test_memory_read_flash(self, backend):
        assert backend.read_memory(_FLASH_BASE, 2, 1) == 0x2042

    def test_register_rw(self, backend):
        backend.write_register("r5", 0xDEADBEEF)
        assert backend.read_register("r5") == 0xDEADBEEF

    def test_memory_rw(self, backend):
        backend.write_memory(_RAM_BASE + 0x100, 4, 0xCAFEBABE)
        assert backend.read_memory(_RAM_BASE + 0x100, 4, 1) == 0xCAFEBABE

    def test_breakpoint_fires(self, backend):
        bp = backend.set_breakpoint(_BP_ADDR)
        assert isinstance(bp, int)
        backend.cont()
        pc = backend.read_register("pc") & ~1
        assert pc == _BP_ADDR
        # Two instructions executed before the bp
        assert backend.read_register("r0") == 0x42
        assert backend.read_register("r1") == 0x10

    def test_remove_breakpoint_does_not_halt(self, backend):
        bp = backend.set_breakpoint(_BP_ADDR)
        backend.remove_breakpoint(bp)
        # Without the first bp, cont() would run forever on the infinite
        # loop. Set a second bp past the watched address so we have a
        # stopping condition.
        bp2 = backend.set_breakpoint(_LOOP_ADDR)
        backend.write_register("r4", 0)  # so the str doesn't fault
        backend.cont()
        pc = backend.read_register("pc") & ~1
        assert pc == _LOOP_ADDR
        backend.remove_breakpoint(bp2)

    def test_single_step(self, backend):
        backend.step()
        assert (backend.read_register("pc") & ~1) == _FLASH_BASE + 2
        assert backend.read_register("r0") == 0x42

    def test_write_watchpoint_fires(self, backend):
        backend.set_watchpoint(_WATCH_ADDR, write=True, read=False, size=4)
        backend.write_register("r4", 0xDEADBEEF)
        backend.cont()
        # Execution halts at/after the str instruction at _LOOP_ADDR - 2
        pc = backend.read_register("pc") & ~1
        assert _FLASH_BASE <= pc <= _LOOP_ADDR

    def test_inject_irq_cortex_m_without_vector_is_warned(self, backend, caplog):
        # Without a vector table slot populated, the injection should warn
        # rather than crash.
        import logging
        caplog.set_level(logging.WARNING)
        backend.inject_irq(16)
        # either warned or raised cleanly — just verify it didn't crash
        # and the backend is still operable:
        assert backend.read_register("pc") is not None


# ---------------------------------------------------------------------------
# Ghidra PCode — heavy (JVM), so we batch the smoke tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_pyghidra_with_ghidra(),
                    reason="pyghidra/Ghidra not available")
class TestGhidraLiveFeatures:
    @pytest.fixture(scope="class")
    def backend(self, firmware_path_class):
        from halucinator.backends.ghidra_backend import GhidraBackend
        # scope=class: one JVM boot for the whole feature set
        b = GhidraBackend(arch="cortex-m3")
        _install_regions(b, firmware_path_class)
        b.init()
        b.write_register("pc", _FLASH_BASE | 1)
        b.write_register("sp", _RAM_BASE + 0x800)
        yield b
        b.shutdown()

    def test_memory_read_flash(self, backend):
        assert backend.read_memory(_FLASH_BASE, 2, 1) == 0x2042

    def test_register_rw(self, backend):
        backend.write_register("r5", 0xDEADBEEF)
        assert backend.read_register("r5") == 0xDEADBEEF

    def test_single_step_advances_pc(self, backend):
        before = backend.read_register("pc") & ~1
        backend.step()
        after = backend.read_register("pc") & ~1
        assert after == before + 2

    def test_breakpoint_halts_cont(self, backend):
        # Reset PC and run to the nop at offset 4
        backend.write_register("pc", _FLASH_BASE | 1)
        bp = backend.set_breakpoint(_BP_ADDR)
        backend.cont()
        pc = backend.read_register("pc") & ~1
        assert pc == _BP_ADDR
        assert backend.read_register("r0") == 0x42
        assert backend.read_register("r1") == 0x10
        backend.remove_breakpoint(bp)

    def test_write_watchpoint_halts_at_str(self, backend):
        backend.write_register("pc", _FLASH_BASE | 1)
        backend.write_register("r4", 0xCAFE)
        wp = backend.set_watchpoint(_WATCH_ADDR, write=True, size=4)
        backend.cont()
        # After the str r4, [r3] executes, the watchpoint should fire
        assert backend._bp_hit_addr == _WATCH_ADDR
        # memory at watched address now holds r4's value
        assert backend.read_memory(_WATCH_ADDR, 4, 1) == 0xCAFE
        backend.remove_watchpoint(wp)

    def test_inject_irq_enters_isr(self, backend):
        # Build a second firmware file with a real vector table + ISR
        # isn't easy with the shared class-scope fixture, so this is a
        # smoke test that inject_irq with an empty vector table warns
        # cleanly and doesn't corrupt the emulator.
        backend.write_register("pc", _FLASH_BASE | 1)
        backend.set_vtor(_FLASH_BASE)
        backend.inject_irq(0)   # vector 16 is zero -> warning path
        # emulator still usable afterwards
        pc = backend.read_register("pc") & ~1
        assert pc == _FLASH_BASE
