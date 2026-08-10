# Copyright 2026 Christopher Wright

"""
Tests for the Infineon TriCore (AURIX) target arch wired into the backend stack.

Covers:
  * HALUCINATOR_TARGETS exposes "tricore" so hal_config accepts it.
  * The unicorn arch map and the TriCore EABI mixin know the arch.
  * The register map exposes both banks (d0-d15 / a0-a15) plus the sp/ra/lr
    aliases onto a10/a11, which TriCore does not have as real registers.
  * UnicornBackend can instantiate arch="tricore" and actually execute TriCore
    instructions (real unicorn, not a mock) at the authentic AURIX TC27x
    PFLASH0 base.
  * The 16 KB page-size constraint is real and is what a too-small region
    trips over -- documented here so it is not re-diagnosed as a bad address.

No avatar2 dependency: TriCore is in-process (unicorn) only, so its
HALUCINATOR_TARGETS entry deliberately carries avatar_arch=None.
"""
import pytest

try:
    import unicorn
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

# Authentic AURIX TC27xD map (QEMU hw/tricore/tc27x_soc.c).
PFLASH0 = 0x80000000
DSPR0 = 0x70000000
# Unicorn's TriCore target page size. NOT 4 KB.
TRICORE_PAGE = 0x4000

# mov d1,#1 ; mov d3,#2 ; mov d7,#3  (SRC format; capstone-cross-checked)
CODE = bytes([0x82, 0x11, 0x82, 0x23, 0x82, 0x37])


# ---------------------------------------------------------------------------
# Arch-table wiring (no unicorn needed)
# ---------------------------------------------------------------------------

class TestTriCoreArchTables:
    def test_in_halucinator_targets(self):
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        assert "tricore" in HALUCINATOR_TARGETS

    def test_is_in_process_only(self):
        """TriCore has no avatar2 arch and no tricore-softmmu QEMU, so the
        entry exists purely so hal_config accepts `arch: tricore`."""
        from halucinator.config.target_archs import HALUCINATOR_TARGETS
        assert HALUCINATOR_TARGETS["tricore"]["avatar_arch"] is None
        assert HALUCINATOR_TARGETS["tricore"]["qemu_target"] is None

    def test_hal_machine_config_accepts_tricore(self):
        from halucinator.hal_config import HALMachineConfig
        cfg = HALMachineConfig(arch="tricore", cpu_model="tc27x",
                               entry_addr=PFLASH0, init_sp=DSPR0 + 0x3F00)
        assert cfg.arch == "tricore"

    def test_unicorn_arch_map_row(self):
        from halucinator.backends.unicorn_backend import _ARCH_MAP
        uc_arch, mode, thumb, big_endian, ptr = _ARCH_MAP["tricore"]
        assert uc_arch == "tricore"
        assert thumb is False
        assert big_endian is False        # TriCore is little-endian
        assert ptr == 4                   # 32-bit

    def test_abi_mixin_is_tricore(self):
        from halucinator.backends.hal_backend import ABI_MIXINS, TriCoreHalMixin
        assert ABI_MIXINS["tricore"] is TriCoreHalMixin


class TestTriCoreRegisterMap:
    def test_both_register_banks_present(self):
        """TriCore has 16 DATA (d0-d15) and 16 ADDRESS (a0-a15) registers."""
        from halucinator.backends.unicorn_backend import _reg_map_for_arch
        m = _reg_map_for_arch("tricore")
        for i in range(16):
            assert f"d{i}" in m, f"data register d{i} missing"
            assert f"a{i}" in m, f"address register a{i} missing"
        assert "pc" in m

    def test_sp_ra_lr_are_aliases_onto_a10_a11(self):
        """TriCore has no sp/lr register: a10 IS the stack pointer and a11 IS
        the return address. The aliases let arch-generic core code resolve."""
        from halucinator.backends.unicorn_backend import _reg_map_for_arch
        m = _reg_map_for_arch("tricore")
        assert m["sp"] == m["a10"]
        assert m["ra"] == m["a11"]
        assert m["lr"] == m["a11"]


class TestTriCoreABI:
    def test_scalar_args_come_from_the_data_bank(self):
        """The EABI puts scalar arguments in d4-d7 (pointers go in a4-a7), so
        get_arg indexes the DATA bank."""
        from halucinator.backends.hal_backend import TriCoreHalMixin

        class Fake(TriCoreHalMixin):
            def __init__(self):
                self.regs = {f"d{i}": i * 10 for i in range(16)}

            def read_register(self, r):
                return self.regs[r]

        f = Fake()
        assert f.get_arg(0) == f.regs["d4"]
        assert f.get_arg(3) == f.regs["d7"]

    def test_return_uses_a11_and_d2(self):
        from halucinator.backends.hal_backend import TriCoreHalMixin

        class Fake(TriCoreHalMixin):
            def __init__(self):
                self.written = {}
                self.continued = False

            def read_register(self, r):
                return 0xDEADBEEF if r == "a11" else 0

            def write_registers(self, regs):
                self.written.update(regs)

            def cont(self):
                self.continued = True

        f = Fake()
        f.execute_return(0x1234)
        assert f.written["pc"] == 0xDEADBEEF     # returns via a11, not lr
        assert f.written["d2"] == 0x1234         # result in d2
        assert f.continued

    def test_stack_args_are_written_where_they_are_read(self):
        """Arguments past the fourth go on the stack, and set_args must put
        them where get_arg looks.

        TriCore's EABI has NO O32-style home space -- the caller reserves no
        slots for the register-passed arguments -- so the fifth argument is the
        first word at a10. set_args used to write at a10+16, the MIPS offset,
        inherited by copying MIPSHalMixin: a round-trip then disagreed by 16
        bytes and an intercept reading argument 5 silently got an unrelated
        word."""
        from halucinator.backends.hal_backend import TriCoreHalMixin

        SP = 0x70003F00

        class Fake(TriCoreHalMixin):
            def __init__(self):
                self.regs = {f"d{i}": 0 for i in range(16)}
                self.regs["a10"] = SP
                self.mem = {}

            def read_register(self, r):
                return self.regs[r]

            def write_register(self, r, v):
                self.regs[r] = v

            def read_memory(self, addr, size, num):
                return self.mem.get(addr, 0)

            def write_memory(self, addr, size, value, num=1, raw=False):
                self.mem[addr] = value

        f = Fake()
        args = [0xA0 + i for i in range(7)]
        f.set_args(args)
        assert [f.get_arg(i) for i in range(7)] == args
        # Absolute placement: a reader and writer that are both wrong in the
        # same direction would still agree with each other.
        assert f.mem[SP] == args[4]
        assert f.mem[SP + 4] == args[5]
        assert SP + 16 not in f.mem, "wrote at the MIPS O32 home-space offset"


# ---------------------------------------------------------------------------
# Real execution
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn not installed")
class TestTriCoreUnicornExecution:
    def _backend(self):
        from halucinator.backends.unicorn_backend import UnicornBackend
        from halucinator.backends.hal_backend import MemoryRegion
        be = UnicornBackend(arch="tricore")
        be.add_memory_region(MemoryRegion("pflash", PFLASH0, TRICORE_PAGE, "rwx"))
        be.add_memory_region(MemoryRegion("dspr", DSPR0, TRICORE_PAGE, "rw"))
        be.init()
        return be

    def test_executes_tricore_instructions(self):
        be = self._backend()
        be.write_memory(PFLASH0, 1, CODE, num_words=len(CODE), raw=True)
        be.write_register("pc", PFLASH0)
        be._uc.emu_start(PFLASH0, PFLASH0 + len(CODE))
        assert be.read_register("d1") == 1
        assert be.read_register("d3") == 2
        assert be.read_register("d7") == 3
        assert be.read_register("pc") == PFLASH0 + len(CODE)

    def test_sp_alias_writes_a10(self):
        be = self._backend()
        be.write_register("sp", DSPR0 + 0x100)
        assert be.read_register("a10") == DSPR0 + 0x100

    def test_page_size_is_16k_not_4k(self):
        """Unicorn's TriCore target uses a 16 KB page. A 4 KB region is
        rejected by uc_mem_map with UC_ERR_ARG -- which reads like a bad
        ADDRESS rather than a bad size, and is why HALucinator configs that
        declare the usual 4 KB peripheral window fail on this arch."""
        uc = unicorn.Uc(unicorn.UC_ARCH_TRICORE, unicorn.UC_MODE_LITTLE_ENDIAN)
        with pytest.raises(unicorn.UcError):
            uc.mem_map(PFLASH0, 0x1000)
        uc.mem_map(PFLASH0, TRICORE_PAGE)        # 16 KB is accepted

    def test_only_mode_zero_opens(self):
        """Unicorn exposes no UC_MODE_TRICORE* constant; mode 0
        (UC_MODE_LITTLE_ENDIAN) is the only value uc_open accepts."""
        assert unicorn.UC_MODE_LITTLE_ENDIAN == 0
        unicorn.Uc(unicorn.UC_ARCH_TRICORE, unicorn.UC_MODE_LITTLE_ENDIAN)
        with pytest.raises(unicorn.UcError):
            unicorn.Uc(unicorn.UC_ARCH_TRICORE, unicorn.UC_MODE_BIG_ENDIAN)
