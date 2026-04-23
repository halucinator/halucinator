"""
Unit test for hal config
"""

import pytest

from halucinator import hal_config


class OtherMem:
    def __init__(self, base_addr=0, size=1024):
        self.base_addr = base_addr
        self.size = size


class TestHalMemConfig:
    def test_constructor_set_fields_correctly(self):
        memconf = hal_config.HalMemConfig(
            "memconf", "/tmp/cfg.txt", 4096, 8192, "r", "file.txt", True
        )
        assert memconf.name == "memconf"
        assert memconf.config_file == "/tmp/cfg.txt"
        assert memconf.file == "/tmp/file.txt"
        assert memconf.size == 8192
        assert memconf.permissions == "r"
        assert memconf.emulate
        assert memconf.base_addr == 4096

    def test_constructor_set_default_values_correctly(self):
        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 0, 8192)
        assert memconf.file is None
        assert memconf.permissions == "rwx"
        assert not memconf.emulate

    def test_configs_with_size_zero_are_valid(self):
        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 0, size=0)
        assert memconf.is_valid()

    @pytest.mark.parametrize("num_pages", [i for i in range(1, 10)])
    def test_configs_with_sizes_a_multiple_of_4K_are_valid(self, num_pages):
        memconf = hal_config.HalMemConfig(
            "memconf", "cfg.txt", 0, size=4096 * num_pages
        )
        assert memconf.is_valid()

    @pytest.mark.parametrize("num_pages", [1024, 2048, 12125])
    def test_configs_with_sizes_not_a_multiple_of_4K_are_not_valid(
        self, num_pages
    ):
        memconf = hal_config.HalMemConfig(
            "memconf", "cfg.txt", 0, size=num_pages
        )
        assert not memconf.is_valid()

    def test_configs_that_need_emulation_that_we_cannot_are_not_valid(self):
        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 0, 8192)
        memconf.emulate_required = True
        assert not memconf.is_valid()

    def test_range_fully_below_does_not_overlap(self):
        # memconf                  [----------]
        # othermem    [----------]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(2048, 2048)
        assert not memconf.overlaps(othermem)

    def test_range_fully_above_does_not_overlap(self):
        # memconf   [----------]
        # othermem               [----------]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(10240, 2048)
        assert not memconf.overlaps(othermem)

    def test_range_starting_below_overlaps(self):
        # memconf              [----------]
        # othermem    [----------]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(4000, 2048)
        assert memconf.overlaps(othermem)

    def test_range_finishing_above_overlaps(self):
        # memconf   [----------]
        # othermem           [----------]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(6000, 4096)
        assert memconf.overlaps(othermem)

    def test_range_fully_inside_overlaps(self):
        # memconf   [----------]
        # othermem      [---]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(6000, 1)
        assert memconf.overlaps(othermem)

    def test_range_starting_below_and_ending_above_overlaps(self):
        # memconf      [----]
        # othermem  [----------]

        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        othermem = OtherMem(2048, 10240)
        assert memconf.overlaps(othermem)

    def test_string_representation(self):
        EXPECTED_REPR = "(cfg.txt){name:memconf, base_addr:0x1000, size:0x1000, emulate:None}"
        memconf = hal_config.HalMemConfig("memconf", "cfg.txt", 4096, 4096)
        assert EXPECTED_REPR == repr(memconf)


class TestHalInterceptConfig:
    def test_constructor_set_fields_correctly(self):
        hic = hal_config.HalInterceptConfig(
            "conf.txt",
            "cls.cls.cls",
            "read",
            0x1000,
            "symbol",
            {"self": 1, "not-self": 2},
            {"self": 3, "reg": 4, "greg": 5},
            True,
            "w",
        )
        assert hic.config_file == "conf.txt"
        assert hic.cls == "cls.cls.cls"
        assert hic.function == "read"
        assert hic.bp_addr == 0x1000
        assert hic.symbol == "symbol"
        assert hic.class_args == {"not-self": 2}
        assert hic.registration_args == {"reg": 4, "greg": 5}
        assert hic.run_once
        assert hic.watchpoint == "w"

    def test_constructor_set_default_values_correctly(self):
        hic = hal_config.HalInterceptConfig("conf.txt", "cls.cls.cls", "read")
        assert hic.bp_addr is None
        assert hic.symbol is None
        assert hic.class_args == {}
        assert hic.registration_args == {}
        assert not hic.run_once
        assert not hic.watchpoint

    def test_check_handler_not_valid_when_cls_wrong(self):
        hic = hal_config.HalInterceptConfig(
            "conf.txt",
            "halucinator.bp_handlers.generic.common.SkipFunc",
            "HAL_Delay",
            0x1000,
            "symbol",
            {"self": 1, "not-self": 2},
            {"self": 3, "reg": 4, "greg": 5},
            True,
            "w",
        )
        assert not hic._check_handler_is_valid()

    def test_string_representation_with_all_symbols(self):
        EXPECTED_REPR = "(conf.txt){symbol: symbol, addr: 0x1000, class: halucinator.bp_handlers.generic.common.SkipFunc, function:HAL_Delay}"
        hic = hal_config.HalInterceptConfig(
            "conf.txt",
            "halucinator.bp_handlers.generic.common.SkipFunc",
            "HAL_Delay",
            0x1000,
            "symbol",
            {"self": 1, "not-self": 2},
            {"self": 3, "reg": 4, "greg": 5},
            True,
            "w",
        )
        assert EXPECTED_REPR == repr(hic)

    def test_string_representation_with_no_address(self):
        EXPECTED_REPR = "(conf.txt){symbol: None, addr: None, class: cls.cls.cls, function:read}"
        hic = hal_config.HalInterceptConfig("conf.txt", "cls.cls.cls", "read")
        assert EXPECTED_REPR == repr(hic)


class TestHalSymbolConfig:
    def test_constructor_set_fields_correctly(self):
        hsc = hal_config.HalSymbolConfig(
            "conf.txt", "halsymbolconfig", 1024, 2048
        )
        assert hsc.config_file == "conf.txt"
        assert hsc.name == "halsymbolconfig"
        assert hsc.addr == 1024
        assert hsc.size == 2048

    def test_constructor_size_default_value_is_correct(self):
        hsc = hal_config.HalSymbolConfig("conf.txt", "halsymbolconfig", 1024)
        assert hsc.size == 0

    def test_symbol_config_always_valid(self):
        hsc = hal_config.HalSymbolConfig(
            "conf.txt", "halsymbolconfig", 1024, 2048
        )
        assert hsc.is_valid()

    def test_string_representation(self):
        EXPECTED_REPR = (
            "SymConfig(conf.txt){halsymbolconfig, 0x400(1024),2048}"
        )
        hsc = hal_config.HalSymbolConfig(
            "conf.txt", "halsymbolconfig", 1024, 2048
        )
        assert EXPECTED_REPR == repr(hsc)


class TestHALMachineConfig:
    def test_constructor_set_fields_correctly(self):
        hmc = hal_config.HALMachineConfig(
            "conf.txt", "cortex", "cortex-m10", 128, 64, "gdb", 0x1000
        )
        assert hmc.arch == "cortex"
        assert hmc.cpu_model == "cortex-m10"
        assert hmc.entry_addr == 128
        assert hmc.init_sp == 64
        assert hmc.gdb_exe == "gdb"
        assert hmc.vector_base == 0x1000
        assert hmc.config_file == "conf.txt"
        assert not hmc._using_default_machine

    def test_constructor_set_default_values_correctly(self):
        hmc = hal_config.HALMachineConfig()
        assert hmc.arch == "cortex-m3"
        assert hmc.cpu_model == "cortex-m3"
        assert hmc.entry_addr is None
        assert hmc.init_sp is None
        assert hmc.gdb_exe == "gdb-multiarch"
        assert hmc.vector_base == 0x08000000
        assert hmc.config_file is None
        assert hmc._using_default_machine


class TestHalucinatorConfig:
    def test_constructor_set_default_values_correctly(self):
        hc = hal_config.HalucinatorConfig()
        assert hc.options == {}
        assert hc.memories == {}
        assert hc.intercepts == []
        assert hc.watchpoints == []
        assert hc.symbols == []
        assert hc.callables == []
