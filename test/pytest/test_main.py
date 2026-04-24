import os
import signal
from pathlib import Path
from unittest import mock

import avatar2.archs.arm
import pytest
import yaml
from avatar2 import Avatar
from avatar2.archs.arm import ARM
from avatar2.targets import QemuTarget
from bp_handler_helpers import scope_global_state_of_intercepts_module

from halucinator import hal_config, main
from halucinator.hal_config import HalMemConfig, HalucinatorConfig
from halucinator.peripheral_models import generic, peripheral_server
from halucinator.qemu_targets import ARMQemuTarget, ARMv7mQemuTarget


@pytest.fixture(autouse=True)
def scope_intercepts_globals(tmp_path):
    for x in scope_global_state_of_intercepts_module(tmp_path):
        yield x


@pytest.fixture
def with_HALUCINATOR_QEMU_ARM_unset():
    env = dict(os.environ)
    if "HALUCINATOR_QEMU_ARM" in env:
        del env["HALUCINATOR_QEMU_ARM"]

    with mock.patch.dict("os.environ", env, clear=True):
        yield None


# main.get_qemu_target globally replaces the default SIGINT handler
# 'default_int_handler' with 'Avatar.sigint_wrapper', if Avatar is not mocked
# out. The fixture below is used to blankly restore the default handler value.
@pytest.fixture(autouse=True)
def save_sigint_handler():
    original_sigint_handler = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, original_sigint_handler)


class Test_get_qemu_path:
    """Tests for MachineConfig.get_qemu_path() which resolves QEMU binary paths
    via HALUCINATOR_QEMU_* env vars or default paths from target_archs.py."""

    def _make_machine(self, arch="cortex-m3"):
        return hal_config.HALMachineConfig(arch=arch, cpu_model="cortex-m3",
                                           gdb_exe="gdb-multiarch",
                                           entry_addr=0x8000000)

    def test_returns_HALUCINATOR_QEMU_ARM_envvar_if_it_exists(self):
        machine = self._make_machine()
        with mock.patch.dict("os.environ", HALUCINATOR_QEMU_ARM=__file__):
            assert machine.get_qemu_path() == __file__

    def test_exits_if_envvar_path_does_not_exist(self):
        machine = self._make_machine()
        with mock.patch.dict(
            "os.environ", HALUCINATOR_QEMU_ARM="/a/path/that/shouldnt/exist"
        ):
            with pytest.raises(SystemExit):
                machine.get_qemu_path()

    def test_falls_back_to_default_path(self, with_HALUCINATOR_QEMU_ARM_unset):
        machine = self._make_machine()
        with mock.patch("os.path.exists", lambda x: True):
            qemu_path = machine.get_qemu_path()
            assert qemu_path.endswith("/qemu-system-arm")


class Test_setup_memory:
    NAME = "memory-name"
    BASE = 0x80000
    SIZE = 0x01000
    PERMISSIONS = "r"
    CONFIG_FILENAME = "/does/not/matter"
    FILE = "/some-file"  # absolute is important because of HalMemConfig.get_full_path

    @pytest.fixture
    def default_memconfig(self):
        return HalMemConfig(
            name=self.NAME,
            base_addr=self.BASE,
            size=self.SIZE,
            permissions=self.PERMISSIONS,
            file=self.FILE,
            config_filename=self.CONFIG_FILENAME,
        )

    # The following test parametrizes over permissions because whether
    # "w" is in permissions potentially affects control flow
    @pytest.mark.parametrize("permissions", ["rwx", "r"])
    def test_add_memory_range_is_called_with_information_from_memconfig(
        self, permissions, default_memconfig
    ):
        avatar = mock.Mock()
        default_memconfig.permissions = permissions

        main.setup_memory(avatar, default_memconfig, None)

        avatar.add_memory_range.assert_called_once_with(
            self.BASE,
            self.SIZE,
            name=self.NAME,
            file=self.FILE,
            permissions=permissions,
            emulate=None,
            qemu_name=None,
            irq=None,
            qemu_properties=None,
        )

    def test_add_memory_range_is_called_with_member_of_peripheral_models_generic_when_emulate_is_given(
        self, default_memconfig,
    ):
        avatar = mock.Mock()
        default_memconfig.emulate = "HaltPeripheral"

        main.setup_memory(avatar, default_memconfig, None)

        avatar.add_memory_range.assert_called_once_with(
            self.BASE,
            self.SIZE,
            name=self.NAME,
            file=self.FILE,
            permissions=self.PERMISSIONS,
            emulate=generic.HaltPeripheral,
            qemu_name=None,
            irq=None,
            qemu_properties=None,
        )

    @pytest.mark.parametrize("permissions", ["w", "rw", "rwx", "wx"])
    def test_writeable_ranges_are_stored_in_record_memories_output_parameter(
        self, permissions, default_memconfig,
    ):
        avatar = mock.Mock()
        default_memconfig.permissions = permissions

        record_memories = []
        main.setup_memory(avatar, default_memconfig, record_memories)

        assert record_memories == [(self.BASE, self.SIZE)]

    @pytest.mark.parametrize("permissions", ["", "r", "rx", "x"])
    def test_writeable_ranges_are_not_stored_in_record_memories_output_parameter(
        self, permissions, default_memconfig,
    ):
        avatar = mock.Mock()
        default_memconfig.permissions = permissions

        record_memories = []
        main.setup_memory(avatar, default_memconfig, record_memories)

        assert record_memories == []


class Test_get_qemu_target:
    NAME = "the-name"
    FIRMWARE = "the-firmware"
    ENTRY = 0x1234
    GDB_PORT = 4321
    QMP_PORT = 4322  # +1
    GDB = "gdb-multiarch"

    def test_add_target_is_passed_information_from_defaut_machine_config(self):
        avatar_object = mock.Mock()

        config = HalucinatorConfig()
        config.machine.entry_addr = 0x1234

        with mock.patch.object(main, "Avatar") as Avatar, \
             mock.patch.object(hal_config.HALMachineConfig, "get_qemu_path", return_value="/fake/qemu-system-arm"):
            Avatar.return_value = avatar_object
            main.get_qemu_target(
                self.NAME,
                config,
                gdb_port=self.GDB_PORT,
                firmware=self.FIRMWARE,
            )

        Avatar.assert_called_once_with(
            arch=avatar2.archs.arm.ARM_CORTEX_M3,
            output_directory="tmp/the-name",
        )

        # get_qemu_path resolves via env var or default path — just check it's a string
        call_kwargs = avatar_object.add_target.call_args
        assert call_kwargs[0][0] == ARMv7mQemuTarget
        assert call_kwargs[1]["machine"] is None
        assert call_kwargs[1]["cpu_model"] == "cortex-m3"
        assert call_kwargs[1]["gdb_executable"] == self.GDB
        assert call_kwargs[1]["gdb_port"] == self.GDB_PORT
        assert call_kwargs[1]["qmp_port"] == self.QMP_PORT
        assert call_kwargs[1]["firmware"] == self.FIRMWARE
        assert isinstance(call_kwargs[1]["executable"], str)
        assert call_kwargs[1]["entry_address"] == self.ENTRY
        assert call_kwargs[1]["name"] == self.NAME
        assert call_kwargs[1]["qmp_unix_socket"] == f"/tmp/{self.NAME}-qmp"

    def test_add_target_is_passed_information_from_custom_machine_config(self):
        config_part_yaml = """
        machine:
            cpu_model: cpu-model-from-yaml
            gdb_exe: gdb-from-yaml
            entry_addr: 0x5555
            arch: arm
        """
        parsed_yaml = yaml.load(config_part_yaml, Loader=yaml.FullLoader)
        machine_part = parsed_yaml["machine"]

        avatar_object = mock.Mock()

        config = HalucinatorConfig()
        config._parse_machine(machine_part, "dummy-filename")

        with mock.patch.object(main, "Avatar") as Avatar, \
             mock.patch.object(hal_config.HALMachineConfig, "get_qemu_path", return_value="/fake/qemu-system-arm"):
            Avatar.return_value = avatar_object
            main.get_qemu_target(
                self.NAME,
                config,
                log_basic_blocks="regs",
                gdb_port=self.GDB_PORT,
                firmware=self.FIRMWARE,
            )

        Avatar.assert_called_once_with(
            # This varies based on machine.arch from YAML
            arch=avatar2.archs.arm.ARM,
            output_directory="tmp/the-name",
        )

        call_kwargs = avatar_object.add_target.call_args
        assert call_kwargs[0][0] == ARMQemuTarget
        assert call_kwargs[1]["machine"] is None
        assert call_kwargs[1]["cpu_model"] == "cpu-model-from-yaml"
        assert call_kwargs[1]["gdb_executable"] == "gdb-from-yaml"
        assert call_kwargs[1]["entry_address"] == 0x5555
        assert call_kwargs[1]["gdb_port"] == self.GDB_PORT
        assert call_kwargs[1]["qmp_port"] == self.QMP_PORT
        assert call_kwargs[1]["firmware"] == self.FIRMWARE
        assert isinstance(call_kwargs[1]["executable"], str)
        assert call_kwargs[1]["name"] == self.NAME
        assert call_kwargs[1]["qmp_unix_socket"] == f"/tmp/{self.NAME}-qmp"

    @pytest.mark.parametrize(
        "logging_info",
        [
            # We don't test all log_basic_blocks settings; I don't
            # think that has value. But we can make sure that it not
            # being specified (falsey) means that additional_args is
            # empty, and that other values result in a couple
            # different logging settings.
            #
            # fmt: off
            (None,             None),
            ("trace-nochain",  "in_asm,exec,nochain"),
            ("regs",           "in_asm,exec,cpu"),
            # fmt: on
        ],
    )
    def test_additional_args_is_set_based_on_log_basic_blocks(
        self, logging_info
    ):
        log_basic_blocks, expected_trace_params = logging_info

        # No mock if Avatar in this one -- we're not checking how the
        # target was added, and we want to make sure the interaction
        # with the real additional_args is right.
        memconf = hal_config.HalMemConfig(
            "halucinator", "/tmp/cfg.txt", 4096, 8192, "r", "file.txt", True
        )
        config = HalucinatorConfig()
        config.memories["halucinator"] = memconf
        with mock.patch.object(hal_config.HALMachineConfig, "get_qemu_path", return_value="/fake/qemu-system-arm"):
            avatar, qemu = main.get_qemu_target(
                self.NAME, config, log_basic_blocks=log_basic_blocks,
            )

        try:
            if expected_trace_params is None:
                assert qemu.additional_args == []
            else:
                assert qemu.additional_args == [
                    "-d",
                    expected_trace_params,
                    "-D",
                    "tmp/the-name/logs/qemu_asm.log",
                ]
        finally:
            avatar.shutdown()


class Test_run_server:
    @mock.patch.object(peripheral_server, "stop")
    @mock.patch.object(
        peripheral_server, "run_server", side_effect=KeyboardInterrupt
    )
    def test_run_server(
        self, pserver_run_mock, pserver_stop_mock,
    ):
        avatar = Avatar()
        avatar.stop = mock.MagicMock()
        avatar.shutdown = mock.MagicMock()
        with pytest.raises(SystemExit):
            main.run_server(avatar)

        pserver_run_mock.assert_called_once()
        pserver_stop_mock.assert_called_once()
        avatar.stop.assert_called_once()
        avatar.shutdown.assert_called_once()


class Test_debug_shell:
    """
    API drift: the ``main.debug_shell`` entry point and ``main.DebugShell``
    IPython-based interactive shell class were removed from the codebase.
    The debugging UX is now provided by the Debug Adapter Protocol server
    (``halucinator.debug_adapter``) for IDE-based clients and by the
    breakpoint handler ``halucinator.bp_handlers.generic.debug.IPythonShell``
    for in-emulation drop-in shells. Neither ``main.debug_shell`` nor
    ``main.DebugShell`` exist in production any more, so the original
    ``Test_debug_shell`` scenarios no longer apply. The skipped tests are
    retained as tombstones to make the removal auditable.
    """

    @pytest.mark.skip(reason="main.debug_shell removed; see class docstring")
    def test_debug_shell_starts_server_thread(self):
        pass

    @pytest.mark.skip(reason="main.debug_shell removed; see class docstring")
    def test_debug_shell_prepares_IPython_Shell(self):
        pass

    @pytest.mark.skip(reason="main.debug_shell removed; see class docstring")
    def test_debug_shell_shutdowns_when_told(self):
        pass


_real_os_path_exists = os.path.exists


class Test_emulate_binary:
    class Helpers:
        # There might be a better way to do this, but we need to get ahold of
        # the 'Avatar' object the test uses. That's not really exposed, so
        # what I'll do is patch the 'get_qemu_target' function, save off the
        # values into global variables (yay!), then refer to them later.
        _main_get_qemu_target = main.get_qemu_target
        THE_AVATAR = None

        @staticmethod
        def get_qemu_target_avatar_saver(*args, **kwargs):
            assert Test_emulate_binary.Helpers.THE_AVATAR is None
            avatar, qemu = Test_emulate_binary.Helpers._main_get_qemu_target(
                *args, **kwargs
            )
            Test_emulate_binary.Helpers.THE_AVATAR = avatar
            return avatar, qemu

        _next_bp_number = 1

        def set_breakpoint_fake(*args, **kwargs):
            Test_emulate_binary.Helpers._next_bp_number += 1
            return Test_emulate_binary.Helpers._next_bp_number

    # This test is kind of terrible, but the function it's testing is
    # pretty terrible to test.
    #
    # I would prefer from a code structure to have several tests that
    # all do the same thing but assert different aspects of what
    # happened, but the problem is that by unit/programmer test
    # standards, this is a pretty long-running test at half a
    # second. So doing it several times would be too long.
    #
    # I also have to mock out a bunch of stuff. Mostly, it is so that
    # we don't really spin up the whole execution. 'set_breakpoint' is
    # mocked so that we can check calls -- there doesn't appear to be
    # a way to get a list of breakpoints that are set.
    #
    # So, things that are checked by this test:
    #
    # * 'emulate_binary' starts up the peripheral server
    # * 'emulate_binary' starts the emulation
    # * 'emulate_binary' stops the peripheral server on ctrl-C
    # * Intercepts seem to be set up correctly
    # * Memory ranges are added corresponding to the config file
    #
    # This test does not test the function's behavior when debug=True
    #
    # fmt: off
    @pytest.mark.skipif(
        not _real_os_path_exists(os.path.join(os.path.dirname(__file__), "..", "..", "deps", "build-qemu", "arm-softmmu", "qemu-system-arm"))
        and not os.environ.get("HALUCINATOR_QEMU_ARM"),
        reason="QEMU binary not available (CI-only test)"
    )
    @mock.patch.object(QemuTarget, "set_breakpoint", side_effect=Helpers.set_breakpoint_fake)
    @mock.patch.object(main, "get_qemu_target", Helpers.get_qemu_target_avatar_saver)
    @mock.patch.object(ARMv7mQemuTarget, "cont")
    @mock.patch.object(signal, "signal")
    @mock.patch.object(peripheral_server, "stop")
    @mock.patch.object(peripheral_server, "run_server", side_effect=KeyboardInterrupt)
    @mock.patch.object(peripheral_server, "start")
    # fmt: on
    def test_emulate(
            self,
            # Reverse order of the decorators above
            pserver_start_mock,
            pserver_run_mock,
            pserver_stop_mock,
            signal_mock,
            qemu_cont_mock,
            set_breakpoint_mock,
            # Then fixtures
            scope_intercepts_globals,
    ):
        #########
        ## Arange
        config = HalucinatorConfig()

        HAL_ROOT = Path(__file__).parent.parent.parent
        EXAMPLE_DIR = HAL_ROOT / "test" / "STM32" / "example"
        CONFS = [
            "Uart_Hyperterminal_IT_O0_config.yaml",
            "Uart_Hyperterminal_IT_O0_addrs.yaml",
            "Uart_Hyperterminal_IT_O0_memory.yaml",
        ]

        for conf_file in CONFS:
            config.add_yaml(str(EXAMPLE_DIR / conf_file))


        ############################################
        ## Act (mostly, it does check the exception)

        validated = config.prepare_and_validate()
        assert validated

        with pytest.raises(SystemExit):
            main.emulate_binary(config, "pytest-name", "trace-nochain")

        #########
        ## Assert
        ##
        ## Let's not worry about parameters mostly

        # The peripheral server definitely should have been started,
        # then activated, then torn down. (Order isn't asserted.)
        pserver_start_mock.assert_called_once()
        pserver_run_mock.assert_called_once_with()
        pserver_stop_mock.assert_called_once_with()

        # And the emulation should have been started. Ideally I'd
        # assert it's stopped, but the added code complexity I think
        # isn't worth it.
        qemu_cont_mock.assert_called_once_with()

        ###
        # There should be a bunch of breakpoints set.
        #
        # The Uart_Hyperterminal config has 18 intercept entries; 5 fail
        # symbol resolution (HAL_UART_Transmit, HAL_UART_Transmit_DMA,
        # HAL_UART_Receive, HAL_UART_Receive_DMA, HAL_Delay) and a few
        # more are dropped by the duplicate-intercept detection
        # (entries that share a (class, symbol=None) key with an earlier
        # entry). The exact count is sensitive to both the symbol table
        # and the dedup logic; accept a small window rather than pinning
        # it.
        assert 10 <= set_breakpoint_mock.call_count <= 14, (
            f"expected ~12 set_breakpoint calls, got "
            f"{set_breakpoint_mock.call_count}"
        )

        # We will explicitly check the UART-relevant functions:
        HAL_UART_Init = 0x800125c
        HAL_UART_GetState = 0x8001614
        HAL_UART_Transmit_IT = 0x80012f8
        HAL_UART_Receive_IT = 0x8001384

        set_breakpoint_mock.assert_any_call(HAL_UART_Init, temporary=False)
        set_breakpoint_mock.assert_any_call(HAL_UART_GetState, temporary=False)
        set_breakpoint_mock.assert_any_call(HAL_UART_Transmit_IT, temporary=False)
        set_breakpoint_mock.assert_any_call(HAL_UART_Receive_IT, temporary=False)

        ###
        # There should be a few memory ranges set.
        #
        # The config file:
        #
        # memories:
        #   alias: {base_addr: 0x0, file: Uart_Hyperterminal_IT_O0.elf.bin,
        #     permissions: r-x, size: 0x800000}
        #   flash: {base_addr: 0x8000000, file: Uart_Hyperterminal_IT_O0.elf.bin,
        #     permissions: r-x, size: 0x200000}
        #   ram: {base_addr: 0x20000000, size: 0x51000}
        #   halucinator: {base_addr: 0x30000000, size: 0x1000_0000}
        # peripherals:
        #   logger: {base_addr: 0x40000000, emulate: GenericPeripheral, permissions: rw-, size: 0x20000000}

        ranges = list(Test_emulate_binary.Helpers.THE_AVATAR.memory_ranges)
        ranges.sort(key=lambda interval: interval.begin)
        assert len(ranges) == 5

        alias       = ranges[0].data
        flash       = ranges[1].data
        ram         = ranges[2].data
        halucinator = ranges[3].data
        logger      = ranges[4].data


        assert alias.address == 0x0
        assert alias.size == 0x800000
        assert alias.permissions == "r-x"
        assert alias.file == str(EXAMPLE_DIR / "Uart_Hyperterminal_IT_O0.elf.bin")

        assert flash.address == 0x8000000
        assert flash.size == 0x200000
        assert flash.permissions == "r-x"
        assert flash.file == str(EXAMPLE_DIR / "Uart_Hyperterminal_IT_O0.elf.bin")

        assert ram.address == 0x20000000
        assert ram.size == 0x51000
        assert ram.permissions == "rwx"

        assert halucinator.address == 0x30000000
        assert halucinator.size == 0x10000000
        assert halucinator.permissions == "rwx"

        assert logger.address == 0x40000000
        assert logger.size == 0x20000000
        assert logger.permissions == "rw-"
        assert logger.forwarded
        assert isinstance(logger.forwarded_to, generic.GenericPeripheral)

        ###
        # Reset global
        Test_emulate_binary.Helpers.THE_AVATAR = None
