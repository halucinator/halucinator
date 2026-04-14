"""Tests for halucinator.bp_handlers.generic.function_callers module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.function_callers import (
    ARMFunctionCaller,
    FunctionCaller,
    FunctionCallerIntercept,
)


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    m.avatar = mock.Mock()
    m.avatar.arch = mock.Mock()
    m.avatar.arch.registers = {"r0": 0, "r1": 0, "r2": 0, "sp": 0, "lr": 0, "pc": 0}
    return m


# ---------------------------------------------------------------------------
# FunctionCaller base class
# ---------------------------------------------------------------------------


class TestFunctionCaller:
    def test_reg_size(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [1, 2])
        assert caller.reg_size() == 4

    def test_get_return_addr(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [1, 2])
        caller.return_addr = 0x3000
        assert caller.get_return_addr() == 0x3000

    def test_save_state(self, qemu):
        qemu.read_register.return_value = 42
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [])
        caller.save_state()
        assert len(caller.regs) == len(qemu.avatar.arch.registers)
        for reg in qemu.avatar.arch.registers:
            assert caller.regs[reg] == 42

    def test_restore_state(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [])
        caller.regs = {"lr": 10, "pc": 20, "r0": 30, "r1": 40, "r2": 50, "sp": 60}
        caller.restore_state()
        assert qemu.write_register.call_count == 6

    def test_restore_state_before_save_raises(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [])
        # regs is empty, so keys won't be found
        qemu.write_register.side_effect = KeyError("lr")
        with pytest.raises(KeyError):
            caller.restore_state()

    def test_call_raises_not_implemented(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [])
        qemu.read_register.return_value = 0
        with pytest.raises(NotImplementedError):
            caller.call()

    def test_setup_stack_and_args_raises(self, qemu):
        caller = FunctionCaller(qemu, 0x1000, 0x1000, 0x2000, [])
        with pytest.raises(NotImplementedError):
            caller.setup_stack_and_args()


# ---------------------------------------------------------------------------
# ARMFunctionCaller
# ---------------------------------------------------------------------------


class TestARMFunctionCaller:
    def test_init_sets_initial_sp_and_return_addr(self, qemu):
        start = 0x10000
        size = 0x1000
        caller = ARMFunctionCaller(qemu, start, size, 0x20000, [])
        assert caller.initial_sp == start + size - 4  # reg_size=4
        assert caller.return_addr == (start + size) & 0xFFFFFFFE

    def test_setup_stack_and_args_with_4_args(self, qemu):
        caller = ARMFunctionCaller(qemu, 0x10000, 0x1000, 0x20000, [1, 2, 3, 4])
        caller.setup_stack_and_args()
        qemu.write_register.assert_any_call("r0", 1)
        qemu.write_register.assert_any_call("r1", 2)
        qemu.write_register.assert_any_call("r2", 3)
        qemu.write_register.assert_any_call("r3", 4)

    def test_setup_stack_and_args_more_than_4_raises(self, qemu):
        caller = ARMFunctionCaller(qemu, 0x10000, 0x1000, 0x20000, [1, 2, 3, 4, 5])
        with pytest.raises(NotImplementedError, match="Stack parameters"):
            caller.setup_stack_and_args()

    def test_call_sets_lr_and_pc(self, qemu):
        caller = ARMFunctionCaller(qemu, 0x10000, 0x1000, 0x20000, [10])
        caller._call()
        assert qemu.regs.lr == caller.return_addr
        assert qemu.regs.pc == 0x20000

    def test_call_full_flow(self, qemu):
        """Test the full call() method which saves state, sets up args, and calls."""
        qemu.read_register.return_value = 0
        caller = ARMFunctionCaller(qemu, 0x10000, 0x1000, 0x20000, [42])
        caller.call()
        # save_state was called (read_register for each register)
        assert qemu.read_register.call_count > 0
        # setup_stack_and_args was called
        qemu.write_register.assert_any_call("r0", 42)
        # _call was called
        assert qemu.regs.pc == 0x20000

    def test_function_return_restores_state(self, qemu):
        qemu.read_register.return_value = 99
        caller = ARMFunctionCaller(qemu, 0x10000, 0x1000, 0x20000, [])
        caller.save_state()
        caller.function_return()
        # All registers should be restored
        for reg in sorted(qemu.avatar.arch.registers.keys()):
            qemu.write_register.assert_any_call(reg, 99)


# ---------------------------------------------------------------------------
# FunctionCallerIntercept
# ---------------------------------------------------------------------------


class TestFunctionCallerIntercept:
    def test_find_memory_region_success(self, qemu):
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rwx"
        mem.address = 0x60000000
        mem.size = 0x10000

        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]

        addr, size = fci.find_memory_region()
        assert addr == 0x60000000
        assert size == 0x10000

    def test_find_memory_region_wrong_permissions(self, qemu):
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rw-"
        mem.address = 0x60000000
        mem.size = 0x10000

        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]

        with pytest.raises(ValueError, match="must be 'rwx'"):
            fci.find_memory_region()

    def test_find_memory_region_not_found(self, qemu):
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "other_region"

        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]

        with pytest.raises(ValueError, match="Memory Region named"):
            fci.find_memory_region()

    def test_get_stack_addr(self):
        fci = FunctionCallerIntercept()
        fci.memory_addr = 0x60000000
        fci.memory_size = 0x100000
        fci.next_stack_addr = 0x60000000

        addr = fci.get_stack_addr(0x1000)
        assert addr == 0x60000000
        assert fci.next_stack_addr == 0x60001000

    def test_get_stack_addr_exhausted(self):
        fci = FunctionCallerIntercept()
        fci.next_stack_addr = None
        fci.memory_addr = 0x60000000
        fci.memory_size = 0x1000

        with pytest.raises(ValueError, match="Insufficient Memory"):
            fci.get_stack_addr(0x1000)

    def test_get_stack_addr_sets_none_when_full(self):
        fci = FunctionCallerIntercept()
        fci.memory_addr = 0x60000000
        fci.memory_size = 0x2000
        fci.next_stack_addr = 0x60000000

        fci.get_stack_addr(0x2000)
        assert fci.next_stack_addr is None

    def test_register_handler_is_return(self, qemu):
        fci = FunctionCallerIntercept()
        result = fci.register_handler(
            qemu, 0x1000, "func", callee=0x2000, is_return=True
        )
        assert result is FunctionCallerIntercept.return_handler

    def test_initiate_call_handler(self, qemu):
        fci = FunctionCallerIntercept()
        caller = mock.Mock()
        fci.function_caller = {0x1000: caller}
        fci.interactive = {0x1000: False}

        intercept, ret = fci.initiate_call_handler(qemu, 0x1000)
        caller.call.assert_called_once()
        assert intercept is False
        assert ret is None

    def test_return_handler(self, qemu):
        fci = FunctionCallerIntercept()
        caller = mock.Mock()
        fci.function_caller = {0x1000: caller}
        fci.interactive = {0x1000: False}

        intercept, ret = fci.return_handler(qemu, 0x1000)
        caller.restore_state.assert_called_once()
        assert intercept is False
        assert ret is None

    def test_initiate_call_handler_interactive(self, qemu):
        fci = FunctionCallerIntercept()
        caller = mock.Mock()
        caller.callee_fname = "test_func"
        fci.function_caller = {0x1000: caller}
        fci.interactive = {0x1000: True}

        with mock.patch("halucinator.bp_handlers.generic.function_callers.IPython") as mock_ip:
            intercept, ret = fci.initiate_call_handler(qemu, 0x1000)
            mock_ip.embed.assert_called_once()
        caller.call.assert_called_once()

    def test_return_handler_interactive(self, qemu):
        fci = FunctionCallerIntercept()
        caller = mock.Mock()
        caller.callee_fname = "test_func"
        fci.function_caller = {0x1000: caller}
        fci.interactive = {0x1000: True}

        with mock.patch("halucinator.bp_handlers.generic.function_callers.IPython") as mock_ip:
            intercept, ret = fci.return_handler(qemu, 0x1000)
            mock_ip.embed.assert_called_once()
        caller.restore_state.assert_called_once()

    def test_register_handler_full_flow_arm(self, qemu):
        """Test register_handler with ARM architecture and int callee."""
        import avatar2
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        # Setup memory region
        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rwx"
        mem.address = 0x60000000
        mem.size = 0x100000

        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]
        qemu.avatar.arch = avatar2.archs.arm.ARM

        with mock.patch.object(fci, "setup_return_bp"):
            result = fci.register_handler(
                qemu, 0x1000, "my_func",
                callee=0x2000, args=[1, 2],
            )
        assert result is FunctionCallerIntercept.initiate_call_handler
        assert 0x1000 in fci.function_caller

    def test_register_handler_with_string_callee(self, qemu):
        """Test register_handler resolving callee by name."""
        import avatar2
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rwx"
        mem.address = 0x60000000
        mem.size = 0x100000
        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]
        qemu.avatar.arch = avatar2.archs.arm.ARM
        qemu.avatar.callables = {"target_func": 0x3000}

        with mock.patch.object(fci, "setup_return_bp"):
            result = fci.register_handler(
                qemu, 0x1000, "my_func",
                callee="target_func", args=[],
            )
        assert result is FunctionCallerIntercept.initiate_call_handler

    def test_register_handler_string_callee_not_found(self, qemu):
        """Test register_handler with invalid callee name exits."""
        import avatar2
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rwx"
        mem.address = 0x60000000
        mem.size = 0x100000
        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]
        qemu.avatar.arch = avatar2.archs.arm.ARM
        qemu.avatar.callables = {}

        with pytest.raises(SystemExit):
            fci.register_handler(
                qemu, 0x1000, "my_func",
                callee="nonexistent_func", args=[],
            )

    def test_setup_return_bp_regular(self, qemu):
        """Test setup_return_bp creates a BP intercept config."""
        fci = FunctionCallerIntercept()
        with mock.patch("halucinator.bp_handlers.generic.function_callers.hal_config") as mock_hc, \
             mock.patch("halucinator.bp_handlers.generic.function_callers.register_bp_handler") as mock_reg:
            fci.qemu = qemu
            fci.setup_return_bp("myfunc", 0x2000, 0x3000)
            mock_reg.assert_called_once()

    def test_setup_return_bp_watchpoint(self, qemu):
        """Test setup_return_bp with WP break_type."""
        fci = FunctionCallerIntercept()
        with mock.patch("halucinator.bp_handlers.generic.function_callers.hal_config") as mock_hc, \
             mock.patch("halucinator.bp_handlers.generic.function_callers.register_bp_handler") as mock_reg:
            fci.qemu = qemu
            fci.setup_return_bp("myfunc", 0x2000, 0x3000, break_type="WP", rw="r")
            mock_reg.assert_called_once()

    def test_register_handler_with_watchpoint(self, qemu):
        """Test register_handler with watchpoint parameter."""
        import avatar2
        fci = FunctionCallerIntercept()
        fci.qemu = qemu

        mem = mock.Mock()
        mem.name = "halucinator"
        mem.permissions = "rwx"
        mem.address = 0x60000000
        mem.size = 0x100000
        interval = mock.Mock()
        interval.data = mem
        qemu.avatar.memory_ranges = [interval]
        qemu.avatar.arch = avatar2.archs.arm.ARM

        with mock.patch.object(fci, "setup_return_bp") as mock_setup:
            result = fci.register_handler(
                qemu, 0x1000, "my_func",
                callee=0x2000, args=[], watchpoint="r",
            )
            mock_setup.assert_called_once_with(
                "my_func", 0x2000, mock.ANY, break_type="WP", rw="r"
            )
