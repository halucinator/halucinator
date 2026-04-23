from typing import List
from unittest import mock

import pytest
from avatar2 import TargetStates

import halucinator.bp_handlers.intercepts as intercepts
from halucinator import hal_stats
from halucinator.bp_handlers import ReturnZero, debugger
from halucinator.bp_handlers.debugger import CallbackState, DebugState
from halucinator.bp_handlers.intercepts import (
    BPHandlerInfo,
    HalInterceptConfig,
)
# GTIRB-based stack trace removed. Using a minimal stub for tests.
from dataclasses import dataclass


@dataclass
class StackFrame:
    """Minimal stack frame stub (GTIRB StackFrame replacement)."""
    address: int
    name: str


class MockMemory:
    def __init__(self) -> None:
        self.register_names = [
            "r0",
            "r1",
            "r2",
            "r3",
            "r4",
            "r5",
            "r6",
            "r7",
            "r8",
            "sp",
            "lr",
            "pc",
            "",
            "xpsr",
            "",
            "sp_usr",
            "lr_usr",
            "r8_fiq",
            "r9_fiq",
            "r10_fiq",
            "r11_fiq",
            "r12_fiq",
            "sp_fiq",
        ]

    def get_register_names(self):
        """
        Returns a subset of a list of registers from a real call to
        get_register_names. The empty string value is a commonly found
        register name returned by Avatar, and is often repeated. For this
        reason the value "" is included twice in the following return, since
        a correct implementation will need to handler repeated empty strings.
        """
        return self.register_names


class MockProtocols:
    def __init__(self) -> None:
        self.memory = MockMemory()


class MockTarget:
    valid_registers = [
        "r0",
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
        "r8",
        "sp",
        "lr",
        "pc",
        "xpsr",
    ]

    return_value = 2

    def __init__(self):
        self.protocols = MockProtocols()
        self.state = TargetStates.STOPPED
        self.steps = 0
        self.pc = 0

    def reset(self):
        """Resets the Mock Target's Counters."""
        self.__init__()

    def get_status(self):
        """Returns the status of the Mock Target."""
        return {"state": self.state}

    def step_func(self):
        """
        Stepping does not affect the Mock Target by default.
        This behavior is intended to be overwritten in testing.
        """
        return

    def step(self):
        """Calls the instance step_func and increments the number of steps."""
        self.step_func()
        self.steps += 1
        return True


class MockArch:

    capstone_arch = 1
    capstone_mode = 1


class MockConfig:

    intercepts: List[HalInterceptConfig] = []


class MockAvatar:
    def __init__(self) -> None:
        self.arch = MockArch()
        self.config = MockConfig()


class MockStackTraceParser:
    def __init__(self) -> None:
        self.stack_record = []

    def refresh(self, pc: int):
        self.stack_record = [StackFrame(pc, "mock")]


class MockDisasmRet:
    def __init__(self, addr, mnem, op):
        self.address = addr
        self.mnemonic = mnem
        self.op_str = op


class MockDebugger(debugger.Debugger):
    def reset(self):
        self.__init__(self.target, self.avatar, self.stack_trace)
        self.md = MockCs()


class MockCs:

    disasm_instr = MockDisasmRet(0x80011AC, "mov", "r3, r0")
    return_instr = [0x80011AC, "mov", "r3, r0"]
    return_hex_instr = ["0x80011ac", "mov", "r3, r0"]

    def __init__(self, arch=None, mode=None):
        self.calls = []

    def reset(self):
        self.calls = []

    def disasm(self, code, addr, num_instr=1):
        self.calls = [code, addr, num_instr]
        for i in range(num_instr):
            yield MockCs.disasm_instr


@mock.patch("halucinator.bp_handlers.debugger.capstone")
def test_init_sets_correct_values(capstone_mock):
    target = MockTarget()
    avatar = MockAvatar()

    Cs = mock.Mock()
    Cs.return_value = 1

    capstone_mock.CS_ARCH_ARM = 1
    capstone_mock.CS_MODE_THUMB = 1

    capstone_mock.Cs = Cs

    debug = debugger.Debugger(target, avatar, None)

    assert debug.target is target
    assert debug.avatar is avatar
    Cs.assert_called_once()


mock_debug = MockDebugger(MockTarget(), MockAvatar(), MockStackTraceParser())
mock_debug.md = MockCs()


INVALID_REGISTER = "sp_usr"


def test_monitor_emulating_continues_when_passing_breakpoints():
    mock_debug.reset()
    mock_debug.state = DebugState.EMULATING
    intercepts.emulation_complete = True
    intercepts.pass_breakpoint = True
    mock_debug.target.cont = mock.Mock()

    mock_debug.monitor_emulating()

    assert intercepts.emulation_complete == False
    assert intercepts.pass_breakpoint == True
    mock_debug.target.cont.assert_called_once()
    assert mock_debug.state == DebugState.RUNNING


def test_monitor_emulating_stops_when_not_passing_breakpoints():
    mock_debug.reset()
    mock_debug.state = DebugState.EMULATING
    intercepts.emulation_complete = True
    intercepts.pass_breakpoint = False
    mock_debug.target.cont = mock.Mock()

    mock_debug.monitor_emulating()

    assert intercepts.emulation_complete == False
    assert intercepts.pass_breakpoint == False
    mock_debug.target.cont.assert_not_called()
    assert mock_debug.state == DebugState.STOPPED


def test_monitor_emulating_detects_stop_queue():
    mock_debug.reset()
    mock_debug.state = DebugState.EMULATING
    intercepts.emulation_complete = True
    intercepts.pass_breakpoint = True
    mock_debug.target.cont = mock.Mock()
    queue = mock.Mock()
    mock_debug.request_queue.put((debugger.RequestType.STOP, queue))

    mock_debug.monitor_emulating()

    assert intercepts.emulation_complete == False
    assert intercepts.pass_breakpoint == False
    mock_debug.target.cont.assert_not_called()
    assert mock_debug.state == DebugState.STOPPED
    queue.put.assert_called_once_with(True)


def test_monitor_stopped_handles_request_queue():
    mock_debug.reset()
    func = mock.Mock()
    func.return_value = MockTarget.return_value
    kwargs1 = {"test": "test"}
    kwargs2 = {"Test": "Test"}
    resp = mock.Mock()
    next_action = mock.Mock()
    next_action.return_value = True

    mock_debug.request_queue.put(
        (debugger.RequestType.REQUEST, func, kwargs1, resp)
    )
    mock_debug.request_queue.put(
        (debugger.RequestType.ACTION, next_action, CallbackState.CONT, resp)
    )
    mock_debug.request_queue.put(
        (debugger.RequestType.REQUEST, func, kwargs2, resp)
    )

    mock_debug.monitor_stopped()

    func.assert_called_once_with(test="test")
    resp.put.assert_has_calls(
        [mock.call((True, MockTarget.return_value)), mock.call((True, True)),]
    )
    assert resp.put.call_count == 2
    next_action.assert_called_once_with()
    assert mock_debug.state == DebugState.RUNNING
    assert mock_debug.last_action == CallbackState.CONT


def test_monitor_stopped_ignores_stops():
    mock_debug.reset()
    resp = mock.Mock()
    next_action = mock.Mock()
    next_action.return_value = True

    mock_debug.request_queue.put((debugger.RequestType.STOP, resp))
    mock_debug.request_queue.put(
        (debugger.RequestType.ACTION, next_action, CallbackState.NEXT, resp)
    )

    mock_debug.monitor_stopped()

    assert mock_debug.state == DebugState.RUNNING
    resp.put.assert_has_calls([mock.call(False), mock.call((True, True))])
    assert mock_debug.last_action == CallbackState.NEXT


@mock.patch(
    "halucinator.bp_handlers.debugger.WrongStateError",
    return_value=MockTarget.return_value,
)
def test_monitor_running_ignores_requests_and_finds_stops(mockError):
    mock_debug.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.stop = mock.Mock()
    mock_debug.callback.call_callbacks = mock.Mock()
    resp = mock.Mock()
    func = mock.Mock()

    mock_debug.request_queue.put(
        (debugger.RequestType.REQUEST, func, {}, resp)
    )
    mock_debug.request_queue.put((debugger.RequestType.STOP, resp))

    mock_debug.monitor_running()

    assert mock_debug.state == DebugState.STOPPED
    mock_debug.target.stop.assert_called_once_with()
    mock_debug.callback.call_callbacks.assert_called_once_with(
        CallbackState.STOP
    )
    resp.put.assert_has_calls(
        [mock.call((False, MockTarget.return_value)), mock.call(True)]
    )
    func.assert_not_called()
    mockError.assert_called_once_with()


@mock.patch("halucinator.bp_handlers.debugger.check_hal_bp", return_value=True)
@mock.patch(
    "halucinator.bp_handlers.debugger.check_debug_bp", return_value=True
)
def test_monitor_running_identifies_hal_bp_first(check_debug, check_hal):
    mock_debug.reset()
    mock_debug.target.state = TargetStates.STOPPED
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    mock_debug.monitor_running()

    assert mock_debug.state == DebugState.EMULATING
    mock_debug.target.read_register.assert_called_once_with("pc")
    check_hal.assert_called_once_with(MockTarget.return_value)
    check_debug.assert_not_called()


@mock.patch(
    "halucinator.bp_handlers.debugger.check_hal_bp", return_value=False
)
@mock.patch(
    "halucinator.bp_handlers.debugger.check_debug_bp", return_value=True
)
def test_monitor_running_identifies_debug_bp(check_debug, check_hal):
    mock_debug.reset()
    mock_debug.target.state = TargetStates.STOPPED
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.callback.call_callbacks = mock.Mock()

    mock_debug.monitor_running()

    assert mock_debug.state == DebugState.STOPPED
    mock_debug.target.read_register.assert_called_once_with("pc")
    check_hal.assert_called_once_with(MockTarget.return_value)
    check_debug.assert_called_once_with(MockTarget.return_value)
    mock_debug.callback.call_callbacks.assert_called_once_with(
        CallbackState.DEBUG_BP
    )


@mock.patch(
    "halucinator.bp_handlers.debugger.check_hal_bp", return_value=False
)
@mock.patch(
    "halucinator.bp_handlers.debugger.check_debug_bp", return_value=False
)
def test_monitor_running_identifies_other_stop(check_debug, check_hal):
    mock_debug.reset()
    mock_debug.target.state = TargetStates.STOPPED
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.callback.call_callbacks = mock.Mock()
    mock_debug.last_action = CallbackState.FINISH

    mock_debug.monitor_running()

    assert mock_debug.state == DebugState.STOPPED
    mock_debug.target.read_register.assert_called_once_with("pc")
    check_hal.assert_called_once_with(MockTarget.return_value)
    check_debug.assert_called_once_with(MockTarget.return_value)
    mock_debug.callback.call_callbacks.assert_called_once_with(
        CallbackState.FINISH
    )


def test_call_callbacks_adds_to_queue_and_calls_callbacks():
    mock_debug.reset()
    mock_debug.callback.callback_queue.put = mock.Mock()
    CB_STATE = CallbackState.FINISH
    cb1 = mock.Mock()
    cb2 = mock.Mock()

    v1 = mock_debug.add_callback(cb1)
    v2 = mock_debug.add_callback(cb2)

    mock_debug._call_callbacks(CB_STATE)
    mock_debug.callback._run_callbacks(CB_STATE)

    assert v1 != v2
    cb1.assert_called_once_with(CB_STATE)
    cb2.assert_called_once_with(CB_STATE)
    mock_debug.callback.callback_queue.put.assert_called_once_with(CB_STATE)


def test_add_and_remove_callbacks_correctly_modifies_state():
    mock_debug.reset()
    mock_debug.callback.callback_queue.put = mock.Mock()
    CB_STATE = CallbackState.FINISH
    cb1 = mock.Mock()
    cb2 = mock.Mock()

    v1 = mock_debug.add_callback(cb1)
    v2 = mock_debug.add_callback(cb2)

    v3 = mock_debug.remove_callback(v1)
    v4 = mock_debug.remove_callback(v1 + v2)

    mock_debug._call_callbacks(CB_STATE)
    mock_debug.callback._run_callbacks(CB_STATE)

    assert v3
    assert not v4
    assert v1 != v2
    cb1.assert_not_called()
    cb2.assert_called_once_with(CB_STATE)
    mock_debug.callback.callback_queue.put.assert_called_once_with(CB_STATE)


@pytest.mark.parametrize("reg", MockTarget.valid_registers)
def test_read_register_returns_int_when_not_hex_mode(reg):
    mock_debug.target.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    val = mock_debug._read_register(reg, False)

    mock_debug.target.read_register.assert_called_once_with(reg)
    assert val == MockTarget.return_value


@pytest.mark.parametrize("reg", MockTarget.valid_registers)
def test_read_register_returns_hex_when_hex_mode(reg):
    mock_debug.target.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    val = mock_debug._read_register(reg, True)

    mock_debug.target.read_register.assert_called_once_with(reg)
    assert val == hex(MockTarget.return_value)


@mock.patch.object(debugger.log, "error")
def test_read_register_invalid_register_when_not_hex_mode(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    val = mock_debug._read_register(INVALID_REGISTER, False)

    mock_debug.target.read_register.assert_not_called()
    assert val == -1
    mock_log.assert_called_once()


@mock.patch.object(debugger.log, "error")
def test_read_register_invalid_register_when_hex_mode(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    val = mock_debug._read_register(INVALID_REGISTER, True)

    mock_debug.target.read_register.assert_not_called()
    assert val == ""
    mock_log.assert_called_once()


@pytest.mark.parametrize("reg", MockTarget.valid_registers)
@pytest.mark.parametrize("reg_value", list(range(10)))
def test_write_register_writes_to_valid_register(reg, reg_value):
    mock_debug.target.reset()
    mock_debug.target.write_register = mock.Mock()
    mock_debug.target.write_register.return_value = True

    val = mock_debug._write_register(reg, reg_value)

    mock_debug.target.write_register.assert_called_once_with(reg, reg_value)
    assert val == True


@mock.patch.object(debugger.log, "error")
def test_write_register_logs_error_on_invalid_register(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_register = mock.Mock()
    mock_debug.target.write_register.return_value = True

    val = mock_debug._write_register(INVALID_REGISTER, 5)

    mock_debug.target.write_register.assert_not_called()
    assert val == False
    mock_log.assert_called_once()


@mock.patch.object(debugger.log, "error")
def test_read_memory_logs_error_when_raw_and_small_size(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    ADDR = 0x80008

    val = mock_debug._read_memory(ADDR, 0, 1, True)

    assert val == b""
    mock_log.assert_called_once()
    mock_debug.target.read_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_read_memory_logs_error_when_raw_and_small_words(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    ADDR = 0x80008

    val = mock_debug._read_memory(ADDR, 1, 0, True)

    assert val == b""
    mock_log.assert_called_once()
    mock_debug.target.read_memory.assert_not_called()


def test_read_memory_calls_target_and_returns_bytes_when_raw():
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = bytes(MockTarget.return_value)
    ADDR = 0x80008
    SIZE = 3
    WORDS = 2
    RAW = True

    val = mock_debug._read_memory(ADDR, SIZE, WORDS, RAW)

    assert val == bytes(MockTarget.return_value)
    mock_debug.target.read_memory.assert_called_once_with(
        ADDR, SIZE, WORDS, RAW
    )


@mock.patch.object(debugger.log, "error")
def test_read_memory_logs_error_when_not_raw_and_wrong_size(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    ADDR = 0x80008
    SIZE = 3
    WORDS = 1
    RAW = False

    val = mock_debug._read_memory(ADDR, SIZE, WORDS, RAW)

    assert val == []
    mock_log.assert_called_once()
    mock_debug.target.read_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_read_memory_logs_error_when_not_raw_and_small_words(mock_log):
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    ADDR = 0x80008
    SIZE = 1
    WORDS = 0
    RAW = False

    val = mock_debug._read_memory(ADDR, SIZE, WORDS, RAW)

    assert val == []
    mock_log.assert_called_once()
    mock_debug.target.read_memory.assert_not_called()


def test_read_memory_calls_target_and_returns_list_when_not_raw():
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = [MockTarget.return_value]
    ADDR = 0x80008
    SIZE = 2
    WORDS = 2
    RAW = False

    val = mock_debug._read_memory(ADDR, SIZE, WORDS, RAW)

    assert val == [MockTarget.return_value]
    mock_debug.target.read_memory.assert_called_once_with(
        ADDR, SIZE, WORDS, RAW
    )


def test_read_memory_calls_target_and_returns_list_when_not_raw_and_words_is_1():
    mock_debug.target.reset()
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = MockTarget.return_value
    ADDR = 0x80008
    SIZE = 4
    WORDS = 1
    RAW = False

    val = mock_debug._read_memory(ADDR, SIZE, WORDS, RAW)

    assert val == [MockTarget.return_value]
    mock_debug.target.read_memory.assert_called_once_with(
        ADDR, SIZE, num_words=WORDS, raw=RAW
    )


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_raw_and_small_size(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 0
    VAL = bytes(2)
    WORDS = 1
    RAW = True

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_raw_and_small_words(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = bytes(2)
    WORDS = 0
    RAW = True

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_raw_and_non_byte_val(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 1
    VAL = 2
    WORDS = 1
    RAW = True

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


def test_write_memory_calls_target_when_raw():
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 5
    VAL = bytes(27)
    WORDS = 5
    RAW = True

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == True
    mock_debug.target.write_memory.assert_called_once_with(
        ADDR, SIZE, VAL, WORDS, RAW
    )


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_small_size(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 0
    VAL = [5]
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_invalid_size(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 3
    VAL = [5]
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_small_words(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = [5]
    WORDS = 0
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_invalid_val_type(mock_log):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 1
    VAL = bytes(2)
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_val_length_mismatch_words(
    mock_log,
):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = [5, 4, 6]
    WORDS = 4
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_write_memory_logs_error_when_not_raw_and_val_length_2_mismatch_words_1(
    mock_log,
):
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = [5, 4]
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == False
    mock_log.assert_called_once()
    mock_debug.target.write_memory.assert_not_called()


def test_write_memory_calls_target_when_not_raw_val_length_match_words():
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = [5, 4, 6]
    WORDS = 3
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == True
    mock_debug.target.write_memory.assert_called_once_with(
        ADDR, SIZE, VAL, WORDS, RAW
    )


def test_write_memory_calls_target_when_not_raw_val_int_words_1():
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 4
    VAL = 12
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == True
    mock_debug.target.write_memory.assert_called_once_with(
        ADDR, SIZE, VAL, WORDS, RAW
    )


def test_write_memory_calls_target_when_not_raw_val_list_words_1():
    mock_debug.target.reset()
    mock_debug.target.write_memory = mock.Mock()
    mock_debug.target.write_memory.return_value = True
    ADDR = 0x80008
    SIZE = 8
    VAL = [12]
    WORDS = 1
    RAW = False

    val = mock_debug._write_memory(ADDR, SIZE, VAL, WORDS, RAW)

    assert val == True
    mock_debug.target.write_memory.assert_called_once_with(
        ADDR, SIZE, VAL[0], WORDS, RAW
    )


def test_step_calls_target_step_when_stopped():
    mock_debug.target.reset()

    val = mock_debug._step()

    assert val == True
    assert mock_debug.target.steps == 1


@mock.patch.object(debugger.log, "error")
def test_step_not_calls_target_step_and_logs_error_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING

    val = mock_debug._step()

    assert val == False
    assert mock_debug.target.steps == 0
    mock_log.assert_called_once()


def test_get_target_state_gets_target_state():
    mock_debug.target.reset()
    mock_debug.state = TargetStates.STOPPED

    val = mock_debug.get_target_state()

    assert val == TargetStates.STOPPED

    mock_debug.target.state = TargetStates.RUNNING

    val = mock_debug.get_target_state()

    assert val == TargetStates.RUNNING


def test_cont_calls_continue_and_sets_pass_breakpoint_when_stopped():
    mock_debug.target.reset()
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True
    intercepts.pass_breakpoint = True

    val = mock_debug._cont()

    assert intercepts.pass_breakpoint == False
    assert val == True
    mock_debug.target.cont.assert_called_once()


def test_cont_not_call_target_cont_when_not_stopped():
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True

    mock_debug._cont()

    mock_debug.target.cont.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_cont_log_error_and_return_false_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True

    val = mock_debug._cont()

    assert val == False
    mock_log.assert_called_once()


def test_cont_not_change_pass_breakpoint_when_not_stopped():
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True
    intercepts.pass_breakpoint = True

    mock_debug._cont()

    assert intercepts.pass_breakpoint == True


def test_cont_through_calls_continue_and_sets_pass_breakpoint_when_stopped():
    mock_debug.target.reset()
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True
    intercepts.pass_breakpoint = False

    val = mock_debug._cont_through()

    assert intercepts.pass_breakpoint == True
    assert val == True
    mock_debug.target.cont.assert_called_once()


def test_cont_through_not_call_target_cont_when_not_stopped():
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True

    mock_debug._cont_through()

    mock_debug.target.cont.assert_not_called()


@mock.patch.object(debugger.log, "error")
def test_cont_through_log_error_and_return_false_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True

    val = mock_debug._cont_through()

    mock_log.assert_called_once()
    assert val == False


def test_cont_through_not_change_pass_breakpoint_when_not_stopped():
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.cont = mock.Mock()
    mock_debug.target.cont.return_value = True
    intercepts.pass_breakpoint = False

    val = mock_debug._cont_through()

    assert val == False
    assert intercepts.pass_breakpoint == False


@mock.patch.object(debugger.log, "error")
def test_stop_logs_error_when_Halucinator_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.reset()
    mock_debug.state = DebugState.STOPPED

    val = mock_debug.stop()

    mock_log.assert_called_once()
    assert val == False


def test_set_debug_breakpoint_calls_target_and_adds_debug_breakpoint_to_dict():
    mock_debug.target.reset()
    mock_debug.target.set_breakpoint = mock.Mock()
    mock_debug.target.set_breakpoint.return_value = MockTarget.return_value
    intercepts.debugging_bps = {}
    ADDR = 0x800080

    val = mock_debug._set_debug_breakpoint(ADDR)

    assert val == MockTarget.return_value
    assert intercepts.debugging_bps == {MockTarget.return_value: ADDR}
    mock_debug.target.set_breakpoint.assert_called_once_with(ADDR)


def test_remove_debug_breakpoint_removes_valid_breakpoint_and_calls_target():
    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    ADDR = 0x800080
    intercepts.debugging_bps = {MockTarget.return_value: ADDR}

    val = mock_debug._remove_debug_breakpoint(MockTarget.return_value)

    assert val == True
    assert intercepts.debugging_bps == {}
    mock_debug.target.remove_breakpoint.assert_called_once_with(
        MockTarget.return_value
    )


def test_remove_debug_breakpoint_ignores_invalid_breakpoints():
    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    ADDR = 0x800080
    intercepts.debugging_bps = {MockTarget.return_value: ADDR}

    val = mock_debug._remove_debug_breakpoint(MockTarget.return_value + 1)

    assert val == False
    assert intercepts.debugging_bps == {MockTarget.return_value: ADDR}
    mock_debug.target.remove_breakpoint.assert_not_called()


@mock.patch("halucinator.bp_handlers.debugger.get_bp_handler_debug")
def test_set_hal_breakpoint(mock_handler):
    mock_debug.target.reset()
    mock_debug.target.set_breakpoint = mock.Mock()
    mock_debug.target.set_breakpoint.return_value = MockTarget.return_value
    mock_cls = mock.Mock()
    mock_cls.register_handler = mock.Mock()

    def handler(x):
        return x

    mock_cls.register_handler.return_value = handler
    mock_handler.return_value = mock_cls
    intercepts.bp2handler_lut = {}
    hal_stats.stats = {}

    BP_ADDR = 0x8008
    CLS_STR = "test.test"
    FUNC_NAME = "test"
    RUN_ONCE = True
    CLASS_ARGS = {"test": True}
    REGISTRATION_ARGS = {"test": 1}

    val = mock_debug._set_hal_breakpoint(
        BP_ADDR, CLS_STR, FUNC_NAME, RUN_ONCE, CLASS_ARGS, REGISTRATION_ARGS
    )

    assert val == MockTarget.return_value
    mock_handler.assert_called_once_with(CLS_STR, **CLASS_ARGS)
    mock_cls.register_handler.assert_called_once_with(
        mock_debug.target, BP_ADDR, FUNC_NAME, **REGISTRATION_ARGS
    )
    mock_debug.target.set_breakpoint.assert_called_once_with(
        BP_ADDR, temporary=RUN_ONCE
    )
    assert intercepts.bp2handler_lut == {
        MockTarget.return_value: BPHandlerInfo(
            BP_ADDR, mock_cls, "", handler, RUN_ONCE
        )
    }
    assert hal_stats.stats == {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }


def test_remove_hal_breakpoint_fully_removes_valid_breakpoints():
    BP_ADDR = 0x8008
    FUNC_NAME = "test"
    RUN_ONCE = True
    MOCK_CLS = mock.Mock()

    def handler(x):
        return x

    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    intercepts.addr2bp_lut = {BP_ADDR: MockTarget.return_value}
    intercepts.bp2handler_lut = {
        MockTarget.return_value: BPHandlerInfo(
            BP_ADDR, MOCK_CLS, "", handler, RUN_ONCE
        )
    }
    hal_stats.stats = {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }

    val = mock_debug._remove_hal_breakpoint(MockTarget.return_value)

    assert val == True
    assert intercepts.addr2bp_lut == {}
    assert intercepts.bp2handler_lut == {}
    mock_debug.target.remove_breakpoint.assert_called_once_with(
        MockTarget.return_value
    )
    assert hal_stats.stats == {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": False,
            "removed": True,
            "ran_once": False,
        }
    }


def test_remove_hal_breakpoint_ignores_invalid_breakpoint():
    BP_ADDR = 0x8008
    FUNC_NAME = "test"
    RUN_ONCE = True
    MOCK_CLS = mock.Mock()

    def handler(x):
        return x

    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    intercepts.bp2handler_lut = {
        MockTarget.return_value: (BP_ADDR, MOCK_CLS, handler, RUN_ONCE)
    }
    hal_stats.stats = {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }

    val = mock_debug._remove_hal_breakpoint(MockTarget.return_value + 1)

    assert val == False
    assert intercepts.bp2handler_lut == {
        MockTarget.return_value: (BP_ADDR, MOCK_CLS, handler, RUN_ONCE)
    }
    mock_debug.target.remove_breakpoint.assert_not_called()
    assert hal_stats.stats == {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }


def test_reload_hal_config_reloads_all_file_breakpoints():
    BP_ADDR = 0x8008
    FUNC_NAME = "test"
    RUN_ONCE = True
    MOCK_CLS = mock.Mock()
    CFG_FILE = "mock_another_config.yaml"

    def handler(x):
        return x

    HANDLER_INFO = BPHandlerInfo(
        BP_ADDR, MOCK_CLS, CFG_FILE, handler, RUN_ONCE
    )

    # Create a new intercept and handler info to replace the old one
    NEW_CLS = "halucinator.bp_handlers.ReturnZero"
    NEW_INTERCEPT_INFO = HalInterceptConfig(
        CFG_FILE, NEW_CLS, FUNC_NAME, BP_ADDR, run_once=RUN_ONCE,
    )
    NEW_HANDLER_INFO = BPHandlerInfo(
        BP_ADDR,
        intercepts.get_bp_handler(NEW_INTERCEPT_INFO),
        CFG_FILE,
        ReturnZero.return_zero,
        RUN_ONCE,
    )

    mock_debug.avatar.config.intercepts = [NEW_INTERCEPT_INFO]
    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    mock_debug.avatar.config.reload_yaml_intercepts = mock.Mock()
    intercepts.addr2bp_lut = {BP_ADDR: MockTarget.return_value}
    intercepts.bp2handler_lut = {MockTarget.return_value: HANDLER_INFO}
    hal_stats.stats = {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }

    val = mock_debug._reload_hal_config(CFG_FILE)

    assert val == True
    assert intercepts.addr2bp_lut == {BP_ADDR: MockTarget.return_value}
    assert intercepts.bp2handler_lut == {
        MockTarget.return_value: NEW_HANDLER_INFO
    }
    mock_debug.target.remove_breakpoint.assert_called_once_with(
        MockTarget.return_value
    )
    mock_debug.avatar.config.reload_yaml_intercepts.assert_called_once_with(
        CFG_FILE
    )
    assert hal_stats.stats == {
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": f"({CFG_FILE}){{symbol: None, addr: {hex(BP_ADDR)}, class: {NEW_CLS}, function:{FUNC_NAME}}}",
            "count": 0,
            "method": "return_zero",
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }


def test_reload_hal_config_ignores_old_file():
    BP_ADDR = 0x8008
    FUNC_NAME = "test"
    RUN_ONCE = True
    MOCK_CLS = mock.Mock()
    OLD_CFG_FILE = "path/to/old-config.yaml"
    OLD_BPNUM = MockTarget.return_value + 1

    def handler(x):
        return x

    HANDLER_INFO = BPHandlerInfo(
        BP_ADDR, MOCK_CLS, OLD_CFG_FILE, handler, RUN_ONCE
    )

    # Create a new intercept and handler info to replace the old one
    NEW_BP_ADDR = 0x8009
    NEW_CLS = "halucinator.bp_handlers.ReturnZero"
    NEW_CFG_FILE = "path/to/new-config.yaml"
    NEW_INTERCEPT_INFO = HalInterceptConfig(
        NEW_CFG_FILE, NEW_CLS, FUNC_NAME, NEW_BP_ADDR, run_once=RUN_ONCE,
    )
    NEW_HANDLER_INFO = BPHandlerInfo(
        NEW_BP_ADDR,
        intercepts.get_bp_handler(NEW_INTERCEPT_INFO),
        NEW_CFG_FILE,
        ReturnZero.return_zero,
        RUN_ONCE,
    )

    mock_debug.avatar.config.intercepts = [NEW_INTERCEPT_INFO]
    mock_debug.target.reset()
    mock_debug.target.remove_breakpoint = mock.Mock()
    mock_debug.target.remove_breakpoint.return_value = True
    mock_debug.avatar.config.reload_yaml_intercepts = mock.Mock()
    intercepts.addr2bp_lut = {BP_ADDR: OLD_BPNUM}
    intercepts.bp2handler_lut = {OLD_BPNUM: HANDLER_INFO}
    hal_stats.stats = {
        OLD_BPNUM: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
    }

    val = mock_debug._reload_hal_config(NEW_CFG_FILE)

    assert val == True
    assert intercepts.addr2bp_lut == {
        BP_ADDR: OLD_BPNUM,
        NEW_BP_ADDR: MockTarget.return_value,
    }
    assert intercepts.bp2handler_lut == {
        OLD_BPNUM: HANDLER_INFO,
        MockTarget.return_value: NEW_HANDLER_INFO,
    }
    mock_debug.target.remove_breakpoint.assert_not_called()
    mock_debug.avatar.config.reload_yaml_intercepts.assert_called_once_with(
        NEW_CFG_FILE
    )
    assert hal_stats.stats == {
        OLD_BPNUM: {
            "function": FUNC_NAME,
            "desc": "",
            "count": 0,
            "method": "handler",
            "active": True,
            "removed": False,
            "ran_once": False,
        },
        MockTarget.return_value: {
            "function": FUNC_NAME,
            "desc": f"({NEW_CFG_FILE}){{symbol: None, addr: {hex(NEW_BP_ADDR)}, class: {NEW_CLS}, function:{FUNC_NAME}}}",
            "count": 0,
            "method": "return_zero",
            "active": True,
            "removed": False,
            "ran_once": False,
        },
    }


def test_list_all_regs_names_filters_underscores_and_empty_strings():
    assert mock_debug.list_all_regs_names() == MockTarget.valid_registers


def test_list_all_regs_values_filters_underscores_and_empty_strings():
    mock_debug.target.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    expected_result = {}
    for i in MockTarget.valid_registers:
        expected_result[i] = MockTarget.return_value

    val = mock_debug._list_all_regs_values(False)

    mock_debug.target.read_register.assert_has_calls(
        [mock.call(i) for i in MockTarget.valid_registers]
    )
    assert val == expected_result


INSTR_SIZE = 4
INSTR_WORDS = 1
INSTR_RAW = True


@mock.patch.object(debugger.log, "error")
def test_current_instr_logs_error_and_returns_empty_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_memory = mock.Mock()

    val = mock_debug._current_instr()

    assert val == []
    mock_log.assert_called_once()
    mock_debug.target.read_register.assert_not_called()
    mock_debug.target.read_memory.assert_not_called()


def test_current_instr_returns_hex_instr_when_hex_mode():
    mock_debug.target.reset()
    mock_debug.md.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = MockTarget.return_value

    val = mock_debug._current_instr()

    assert val == MockCs.return_hex_instr
    mock_debug.target.read_register.assert_called_once_with("pc")
    mock_debug.target.read_memory.assert_called_once_with(
        MockTarget.return_value, INSTR_SIZE, INSTR_WORDS, INSTR_RAW
    )
    assert mock_debug.md.calls == [
        bytes(MockTarget.return_value),
        MockTarget.return_value,
        1,
    ]


def test_current_instr_returns_int_instr_when_not_hex_mode():
    mock_debug.target.reset()
    mock_debug.md.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = MockTarget.return_value

    val = mock_debug._current_instr(False)

    assert val == MockCs.return_instr
    mock_debug.target.read_register.assert_called_once_with("pc")
    mock_debug.target.read_memory.assert_called_once_with(
        MockTarget.return_value, INSTR_SIZE, INSTR_WORDS, INSTR_RAW
    )
    assert mock_debug.md.calls == [
        bytes(MockTarget.return_value),
        MockTarget.return_value,
        1,
    ]


@mock.patch.object(debugger.log, "error")
def test_read_instructions_logs_error_and_returns_empty_when_not_stopped(
    mock_log,
):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_memory = mock.Mock()

    val = mock_debug._read_instructions(0x80008)

    assert val == []
    mock_log.assert_called_once()
    mock_debug.target.read_register.assert_not_called()
    mock_debug.target.read_memory.assert_not_called()


def test_read_instructions_returns_hex_instr_when_hex_mode():
    mock_debug.target.reset()
    mock_debug.md.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = MockTarget.return_value
    ADDR = 0x80008
    NUM_INSTR = 2

    val = mock_debug._read_instructions(ADDR, NUM_INSTR)

    assert val == [MockCs.return_hex_instr] * 2
    mock_debug.target.read_memory.assert_called_once_with(
        ADDR, INSTR_SIZE * NUM_INSTR, INSTR_WORDS, INSTR_RAW
    )
    assert mock_debug.md.calls == [
        bytes(MockTarget.return_value),
        ADDR,
        NUM_INSTR,
    ]


def test_read_instructions_returns_int_instr_when_not_hex_mode():
    mock_debug.target.reset()
    mock_debug.md.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value
    mock_debug.target.read_memory = mock.Mock()
    mock_debug.target.read_memory.return_value = MockTarget.return_value
    ADDR = 0x80008
    NUM_INSTR = 2

    val = mock_debug._read_instructions(ADDR, NUM_INSTR, False)

    assert val == [MockCs.return_instr] * 2
    mock_debug.target.read_memory.assert_called_once_with(
        ADDR, INSTR_SIZE * NUM_INSTR, INSTR_WORDS, INSTR_RAW
    )
    assert mock_debug.md.calls == [
        bytes(MockTarget.return_value),
        ADDR,
        NUM_INSTR,
    ]


@mock.patch.object(debugger.log, "error")
def test_finish_logs_error_and_returns_result_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    sync_mock = mock.Mock()
    sync_mock.return_value = [True]
    mock_debug.target.protocols.memory._sync_request = sync_mock

    val = mock_debug._finish()

    assert val == False
    mock_log.assert_called_once()
    sync_mock.assert_not_called()


def test_finish_calls_target_finish_when_stopped():
    mock_debug.target.reset()
    sync_mock = mock.Mock()
    sync_mock.return_value = [True]
    mock_debug.target.protocols.memory._sync_request = sync_mock

    val = mock_debug._finish()

    assert val == True
    sync_mock.assert_called_once_with(["finish"], "running")


@mock.patch.object(debugger.log, "error")
def test_next_logs_error_and_returns_result_when_not_stopped(mock_log):
    mock_debug.target.reset()
    mock_debug.target.state = TargetStates.RUNNING
    sync_mock = mock.Mock()
    sync_mock.return_value = [True]
    mock_debug.target.protocols.memory._sync_request = sync_mock

    val = mock_debug._next()

    assert val == False
    mock_log.assert_called_once()
    sync_mock.assert_not_called()


def test_next_calls_target_finish_when_stopped():
    mock_debug.target.reset()
    sync_mock = mock.Mock()
    sync_mock.return_value = [True]
    mock_debug.target.protocols.memory._sync_request = sync_mock

    val = mock_debug._next()

    assert val == True
    sync_mock.assert_called_once_with(["nexti"], "running")


def test_get_stack_trace_includes_pc():
    mock_debug.target.reset()
    mock_debug.md.reset()
    mock_debug.target.read_register = mock.Mock()
    mock_debug.target.read_register.return_value = MockTarget.return_value

    trace = mock_debug._get_stack_trace()

    assert trace == f"\n#0  {hex(MockTarget.return_value)} in mock ()"
    mock_debug.target.read_register.assert_called_once_with("pc")
