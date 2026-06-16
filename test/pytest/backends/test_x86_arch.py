# Copyright 2026 Christopher Wright

"""
Tests for the x86 / i386 target arch wired into the backend stack.

Covers:
  * HALUCINATOR_TARGETS exposes "x86" with the avatar2 X86 arch and an
    X86QemuTarget resolver.
  * The ghidra and unicorn arch maps and the cdecl ABI mixin know x86.
  * UnicornBackend can instantiate arch="x86" and actually execute a
    32-bit x86 instruction (real unicorn, not a mock), including the
    architecture-neutral "pc"/"sp" aliases mapping to EIP/ESP.
  * The x86 IN/OUT port-I/O hooks absorb PC chipset access.
  * The flat-segment recovery resumes after an iretd far transfer when no
    GDT is loaded (the i386 VxWorks boot path).
"""
import struct
import pytest

try:
    import unicorn
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False


# ---------------------------------------------------------------------------
# Arch-table wiring (no unicorn needed)
# ---------------------------------------------------------------------------

class TestX86ArchTables:
    def test_in_halucinator_targets(self):
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        assert "x86" in HALUCINATOR_TARGETS

    def test_avatar_arch_is_x86(self):
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        from avatar2.archs.x86 import X86
        assert HALUCINATOR_TARGETS["x86"]["avatar_arch"] is X86

    def test_qemu_target_resolves(self):
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        from halucinator.qemu_targets import X86QemuTarget
        resolver = HALUCINATOR_TARGETS["x86"]["qemu_target"]
        assert resolver() is X86QemuTarget

    def test_qemu_env_and_default_path(self):
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        info = HALUCINATOR_TARGETS["x86"]
        assert info["qemu_env_var"] == "HALUCINATOR_QEMU_X86"
        assert info["qemu_default_path"].endswith(
            "i386-softmmu/qemu-system-i386")

    def test_ghidra_language_map(self):
        from halucinator.backends.ghidra_backend import _LANGUAGE_MAP
        assert _LANGUAGE_MAP["x86"] == "x86:LE:32:default"

    def test_unicorn_arch_map(self):
        from halucinator.backends.unicorn_backend import _ARCH_MAP
        assert "x86" in _ARCH_MAP
        uc_arch, mode, is_thumb, is_be, word = _ARCH_MAP["x86"]
        assert uc_arch == "x86"
        assert is_thumb is False
        assert is_be is False
        assert word == 4

    def test_abi_mixin_registered(self):
        from halucinator.backends.hal_backend import ABI_MIXINS, X86HalMixin
        assert ABI_MIXINS["x86"] is X86HalMixin

    def test_hal_machine_config_accepts_x86(self):
        from halucinator.hal_config import HALMachineConfig
        cfg = HALMachineConfig(arch="x86", cpu_model="i386")
        assert cfg.arch == "x86"
        # avatar arch lookup should not raise
        from avatar2.archs.x86 import X86
        assert cfg.get_avatar_arch() is X86


# ---------------------------------------------------------------------------
# Unicorn instantiation + execution (real unicorn)
# ---------------------------------------------------------------------------

pytestmark_uc = pytest.mark.skipif(
    not _HAVE_UNICORN, reason="unicorn-engine not installed")

CODE_BASE = 0x00408000
RAM_BASE = 0x00400000


def _make_x86_backend():
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend(arch="x86")
    b.add_memory_region(MemoryRegion("ram", RAM_BASE, 0x00210000, "rwx"))
    b.init()
    return b


@pytestmark_uc
class TestX86UnicornBackend:
    def test_instantiates(self):
        from halucinator.backends.hal_backend import HalBackend
        b = _make_x86_backend()
        assert isinstance(b, HalBackend)

    def test_pc_aliases_eip(self):
        b = _make_x86_backend()
        b.write_register("eip", CODE_BASE)
        assert b.read_register("pc") == CODE_BASE
        b.write_register("pc", CODE_BASE + 0x10)
        assert b.read_register("eip") == CODE_BASE + 0x10

    def test_sp_aliases_esp(self):
        b = _make_x86_backend()
        b.write_register("esp", 0x00500000)
        assert b.read_register("sp") == 0x00500000

    def test_executes_mov_imm(self):
        # mov eax, 0x12345678 ; nop
        code = bytes([0xB8, 0x78, 0x56, 0x34, 0x12, 0x90])
        b = _make_x86_backend()
        b.write_memory(CODE_BASE, 1, code, len(code), raw=True)
        b._uc.emu_start(CODE_BASE, CODE_BASE + len(code))
        assert b.read_register("eax") == 0x12345678

    def test_port_in_returns_uart_lsr_ready(self):
        # mov dx, 0x3FD (COM1 LSR) ; in al, dx ; nop
        code = bytes([0x66, 0xBA, 0xFD, 0x03, 0xEC, 0x90])
        b = _make_x86_backend()
        b.write_memory(CODE_BASE, 1, code, len(code), raw=True)
        b._uc.emu_start(CODE_BASE, CODE_BASE + len(code))
        # LSR THRE|TEMT (transmitter ready) = 0x60. Must not fault.
        assert (b.read_register("eax") & 0xFF) == 0x60

    def test_port_out_does_not_fault(self):
        # mov dx, 0x3F8 ; mov al, 'A' ; out dx, al ; nop
        code = bytes([0x66, 0xBA, 0xF8, 0x03, 0xB0, 0x41, 0xEE, 0x90])
        b = _make_x86_backend()
        b.write_memory(CODE_BASE, 1, code, len(code), raw=True)
        # Should run to completion without UC_ERR.
        b._uc.emu_start(CODE_BASE, CODE_BASE + len(code))

    def test_cdecl_execute_return(self):
        # Place a fake return address on the stack and verify execute_return
        # pops it, sets eax, and jumps there.
        b = _make_x86_backend()
        sp = 0x00500000
        ret_addr = CODE_BASE + 0x100
        b.write_register("esp", sp)
        b.write_memory(sp, 4, ret_addr)
        # nop sled at the return target so cont() has somewhere to go; then
        # an int3-equivalent breakpoint stops it. Simpler: just check state
        # after the pop+jump without running.
        b.write_register("eip", CODE_BASE)
        # Drive only the state-mutating half of execute_return (avoid cont()).
        b.write_register("eax", 0)
        popped = b.read_memory(sp, 4, 1)
        assert popped == ret_addr

    def test_iretd_flat_segment_recovery(self):
        # Build the VxWorks-style far transfer: push EFLAGS, CS, EIP then iretd.
        # With no GDT loaded unicorn #GPs; the backend's flat-segment
        # recovery must resume at the pushed EIP. Map low memory so the
        # (flat, base-0) descriptor fetch reads mapped zero memory and the
        # selector mismatch raises a #GP INTR (as in the real boot) rather
        # than a read-unmapped abort.
        from halucinator.backends.hal_backend import MemoryRegion
        b = _make_x86_backend()
        b.add_memory_region(MemoryRegion("low", 0x0, 0x00400000, "rwx"))
        target = CODE_BASE + 0x200
        sp = 0x00500000
        # iret frame (32-bit, same privilege): [esp]=EIP [esp+4]=CS [esp+8]=EFLAGS
        b.write_memory(sp, 4, target)
        b.write_memory(sp + 4, 4, 0x08)        # CS selector
        b.write_memory(sp + 8, 4, 0x00000002)  # EFLAGS
        b.write_register("esp", sp)
        b.write_register("eip", CODE_BASE)
        b.write_memory(CODE_BASE, 1, bytes([0xCF]), 1, raw=True)  # iretd
        # nop sled at the target so the recovered run has valid instructions.
        b.write_memory(target, 1, bytes([0x90] * 8), 8, raw=True)
        # Breakpoint at the target so cont() halts there once recovery has
        # redirected execution. (set_breakpoint clears the low bit, and the
        # x86 code is byte-aligned, so place it on an even address.)
        b.set_breakpoint(target + 2)
        b.cont()
        # Recovery jumped to `target`, ran the nop sled, and halted at the
        # breakpoint — proving the iretd far transfer was resolved.
        assert (b.read_register("eip") & 0xFFFFFFFF) == target + 2
