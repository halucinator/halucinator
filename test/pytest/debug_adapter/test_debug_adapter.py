import io
from unittest import mock
from typing import Dict

import pytest

from halucinator.debug_adapter import debug_adapter
from halucinator.bp_handlers.intercepts import BPHandlerInfo
from halucinator.bp_handlers.debugger import CallbackState, DebugState

LINE_OFFSETS = [
    (20, 0x8000000),
    (21, 0x8000004),
    (22, 0x8000008),
    (23, 0x800000C),
    (30, 0x8000010),
    (31, 0x8000014),
]

# Helper class for resetting one or several mocks in a "with" block
class MockCase:
    def __init__(self, *args):
        self.mocks = args
        for m in self.mocks:
            assert isinstance(m, mock.Mock)

    def __enter__(self):
        pass

    def __exit__(self, *args, **kwargs):
        for m in self.mocks:
            m.reset_mock()
        return False


# Used for mocking a function that returns a different integer each time
# it's called.
class Accumulator:
    def __init__(self, startAt: int = 1) -> None:
        self.nextResult = startAt

    def __call__(self, *args, **kwargs) -> int:
        result = self.nextResult
        self.nextResult += 1
        return result

    def reset(self, startAt: int = 1) -> None:
        self.nextResult = startAt


class Mock_Debugger(mock.Mock):
    DEFAULT_REGISTERS = {
        "r0": 0,
        "r1": 0x11,
        "r2": 0x22,
        "r3": 0x33,
        "r4": 0x44,
        "sp": 0xDD,
        "lr": 0xEE,
        "pc": 0xFF,
    }

    def __init__(self, *args, **kwargs):
        mock.Mock.__init__(self, *args, **kwargs)
        self.registers = dict(self.DEFAULT_REGISTERS)
        self.debug_breakpoints = {}
        self.hal_breakpoints: Dict[int, BPHandlerInfo] = {}
        self.accumulator = Accumulator()
        self.set_debug_breakpoint = mock.Mock(side_effect=self.accumulator)
        self.set_hal_breakpoint = mock.Mock(side_effect=self._set_halbp_effect)
        self.write_register = mock.Mock(
            side_effect=self._write_register_effect
        )
        self.get_state = mock.Mock(return_value=DebugState.STOPPED)

    def list_all_regs_names(self):
        return list(self.registers)

    def list_all_regs_values(self, hex_mode=True):
        assert hex_mode is False
        return self.registers

    def read_register(self, reg, hex_mode=True):
        assert hex_mode is False
        assert reg in self.registers
        return self.registers[reg]

    def _write_register_effect(self, reg, value):
        self.registers[reg] = value

    def _set_halbp_effect(self, addr, pyClass, func, once, clArgs, regArgs):
        id = self.accumulator()
        self.hal_breakpoints[id] = BPHandlerInfo(
            addr, (pyClass, clArgs), "", (func, regArgs), once,
        )

    def list_debug_breakpoints(self):
        return self.debug_breakpoints

    def list_watchpoints(self):
        return {}

    def list_hal_breakpoints(self):
        return self.hal_breakpoints

    def populate_existing_breakpoints(self, breakpoints):
        self.debug_breakpoints = breakpoints
        self.accumulator.reset(max([k for k in breakpoints] + [0]) + 1)


class Mock_DAPConnection(mock.Mock):
    def get_response(self, successExpected=True):
        self.send_response.assert_called_once()
        args = self.send_response.call_args.args
        success = len(args) <= 1 or args[1]
        assert success == successExpected
        if len(args) == 0:
            return None
        elif success:
            return args[0]
        elif len(args) < 3:
            return None
        else:
            return args[2]

    def get_event(self, event):
        event_list = self.get_event_list(event)
        assert len(event_list) == 1
        return event_list[0]

    def get_event_list(self, event):
        result = []
        for call in self.send_event.call_args_list:
            if call.args[0] == event:
                if len(call.args) < 2:
                    result.append(None)
                else:
                    result.append(call.args[1])
        return result


class Test_LineTranslator:
    def test_find_instruction_exact(self):
        lt = debug_adapter.LineTranslator(LINE_OFFSETS)
        assert lt.find_next_instruction(20) == (20, 0x8000000)
        assert lt.find_next_instruction(22) == (22, 0x8000008)
        assert lt.find_next_instruction(31) == (31, 0x8000014)

    def test_find_instruction_gap(self):
        lt = debug_adapter.LineTranslator(LINE_OFFSETS)
        assert lt.find_next_instruction(1) == (20, 0x8000000)
        assert lt.find_next_instruction(24) == (30, 0x8000010)
        assert lt.find_next_instruction(29) == (30, 0x8000010)

    def test_find_instruction_eof(self):
        lt = debug_adapter.LineTranslator(LINE_OFFSETS)
        with pytest.raises(LookupError):
            lt.find_next_instruction(32)

    def test_find_line_exact(self):
        lt = debug_adapter.LineTranslator(LINE_OFFSETS)
        assert lt.find_line_number(0x8000000) == 20
        assert lt.find_line_number(0x8000014) == 31

    def test_find_line_oob(self):
        lt = debug_adapter.LineTranslator(LINE_OFFSETS)
        assert lt.find_line_number(0) is None
        assert lt.find_line_number(0x7FFFFFC) is None


class Test_HalRuntime:
    # These correspond to lines 20, 31, 22
    BREAKPOINTS = {1: 0x8000000, 2: 0x8000014, 3: 0x8000008}

    def setup_setBreakpoint(self):
        send_event = mock.Mock()
        debugger = Mock_Debugger()
        debugger.set_debug_breakpoint.assert_not_called()
        debugger.populate_existing_breakpoints(self.BREAKPOINTS)
        debugger.set_debug_breakpoint.assert_not_called()
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime.line_translator = debug_adapter.LineTranslator(LINE_OFFSETS)
        return runtime, debugger, send_event

    def assert_breakpoint_list(self, bpResult, lines, ids, verified=True):
        assert set(lines) == set([bp["line"] for bp in bpResult])
        assert set(ids) == set([bp["id"] for bp in bpResult])
        for bp in bpResult:
            assert bp["verified"] is verified

    def test_setBreakpoint_add(self):
        bpLines = [20, 31, 22, 30]
        bpIds = [1, 2, 3, 4]
        runtime, debugger, send_event = self.setup_setBreakpoint()
        bpList, events = runtime.setBreakpoints(None, bpLines)

        assert len(events) == 1
        assert events[0]["reason"] == "changed"
        assert events[0]["breakpoint"] == {
            "id": 4,
            "line": 30,
            "verified": True,
        }
        self.assert_breakpoint_list(bpList, bpLines, bpIds)
        debugger.set_debug_breakpoint.assert_called_once_with(0x8000010)
        debugger.remove_debug_breakpoints.assert_not_called()
        send_event.assert_not_called()

    def test_setBreakpoint_add_many(self):
        # Line 31 is duplicated to verify deduplication
        bpLines = [20, 31, 22, 30, 21, 23, 31]
        bpIds = [1, 2, 3, 4, 5, 6]
        runtime, debugger, send_event = self.setup_setBreakpoint()
        bpList, events = runtime.setBreakpoints(None, bpLines)

        self.assert_breakpoint_list(bpList, bpLines, bpIds)
        assert len(events) == 3
        for ev in events:
            assert ev["reason"] == "changed"
            assert ev["breakpoint"]["verified"] is True
        debugger.set_debug_breakpoint.assert_called()
        debugger.remove_debug_breakpoints.assert_not_called()
        send_event.assert_not_called()

    def test_setBreakpoint_remove(self):
        bpLines = [20, 22]
        bpIds = [1, 3]
        runtime, debugger, send_event = self.setup_setBreakpoint()
        assert debugger.set_debug_breakpoint.call_args == None
        debugger.set_debug_breakpoint = mock.Mock(side_effect=KeyError)
        bpList, events = runtime.setBreakpoints(None, bpLines)

        self.assert_breakpoint_list(bpList, bpLines, bpIds)
        assert len(events) == 1
        assert events[0]["reason"] == "removed"
        assert events[0]["breakpoint"] == {"id": 2, "line": 31}
        debugger.set_debug_breakpoint.assert_not_called()
        debugger.remove_debug_breakpoint.assert_called_once_with(2)
        send_event.assert_not_called()

    def test_setBreakpoint_clear(self):
        bpLines = []
        bpIds = []
        runtime, debugger, send_event = self.setup_setBreakpoint()
        bpList, events = runtime.setBreakpoints(None, bpLines)

        self.assert_breakpoint_list(bpList, bpLines, bpIds)
        assert len(events) == 3
        debugger.set_debug_breakpoint.assert_not_called()
        debugger.remove_debug_breakpoint.assert_called()
        send_event.assert_not_called()

    def test_setBreakpoint_noLines(self):
        bpLines = [20, 31, 22, 30]
        bpIds = [1, 2, 3, 4]
        runtime, debugger, send_event = self.setup_setBreakpoint()
        debugger.populate_existing_breakpoints({})
        runtime.line_translator = None
        bpList, events = runtime.setBreakpoints(None, bpLines)

        self.assert_breakpoint_list(bpList, bpLines, bpIds, False)
        assert len(events) == 0
        assert runtime._queued_breakpoints is not None
        debugger.set_debug_breakpoint.assert_not_called()
        debugger.remove_debug_breakpoints.assert_not_called()
        send_event.assert_not_called()

        runtime.addLineTranslator(debug_adapter.LineTranslator(LINE_OFFSETS))
        debugger.set_debug_breakpoint.assert_called()
        send_event.assert_called()

    def test_setRegister_r0(self):
        send_event = mock.Mock()
        debugger = Mock_Debugger()
        debugger.write_register = mock.Mock(return_value=True)
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime.setRegister("r0", mock.sentinel.r0)

        debugger.write_register.assert_called_with("r0", mock.sentinel.r0)
        send_event.assert_not_called()

    def test_setRegister_pc(self):
        send_event = mock.Mock()
        debugger = Mock_Debugger()
        debugger.write_register = mock.Mock(return_value=True)
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime.setRegister("pc", mock.sentinel.pc)

        debugger.write_register.assert_called_with("pc", mock.sentinel.pc)
        send_event.assert_called()
        assert send_event.call_args.args[0] == "stopped"
        assert send_event.call_args.args[1]["threadId"] == 1

    def test_state_events(self):
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        conn.runtime = runtime

        def assert_stop_reason(event, reason):
            assert event["threadId"] == 1
            assert event["reason"] == reason

        with MockCase(conn):
            runtime._state_callback(CallbackState.STOP)
            assert_stop_reason(conn.get_event("stopped"), "pause")

        with MockCase(conn):
            runtime._state_callback(CallbackState.STEP)
            assert_stop_reason(conn.get_event("stopped"), "step")

        with MockCase(conn):
            runtime._state_callback(CallbackState.NEXT)
            assert_stop_reason(conn.get_event("stopped"), "step")

        with MockCase(conn):
            runtime._state_callback(CallbackState.FINISH)
            assert_stop_reason(conn.get_event("stopped"), "step")

        with MockCase(conn):
            runtime._state_callback(CallbackState.DEBUG_BP)
            assert_stop_reason(conn.get_event("stopped"), "breakpoint")

        with MockCase(conn):
            runtime._state_callback(CallbackState.EXIT)
            assert type(conn.get_event("exited")["exitCode"]) == int
            conn.get_event("terminated")


class Test_DAPConnection:
    def test_read_header(self):
        inString = "Content-Length: 64\n\nBody"
        debugger = Mock_Debugger()
        sock = mock.Mock()
        conn = debug_adapter.DAPConnection(debugger, sock)
        conn._reader = io.StringIO(inString)
        assert conn._read_header() == 64
        assert conn._reader.read() == "Body"

    def test_read_header_error(self):
        inString = "Cantent-Langth: 64\n\nBody"
        debugger = Mock_Debugger()
        sock = mock.Mock()
        conn = debug_adapter.DAPConnection(debugger, sock)

        conn._reader = io.StringIO(inString)
        assert conn._read_header() == -1

    @mock.patch(
        "halucinator.debug_adapter.debug_adapter.DAPConnection._handle_request"
    )
    def test_handle_message(self, handle_request):
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())

        # Request message with args
        data = '{"command": "sample1", "arguments": {"arg1": 12}, "type": "request", "seq": 1}'
        with MockCase(handle_request):
            conn._handle_message(data)
            handle_request.assert_called_once_with("sample1", 1, {"arg1": 12})

        # Request message without args
        data = '{"command": "sample2", "type": "request", "seq": 2}'
        with MockCase(handle_request):
            conn._handle_message(data)
            handle_request.assert_called_once_with("sample2", 2, None)

        # Response message; should have no effect
        data = '{"request_seq": 3, "success": true, "command": "sample3", "type": "response", "seq": 0}'
        with MockCase(handle_request):
            conn._handle_message(data)
            handle_request.assert_not_called()

        # Event message; should have no effect
        data = '{"event": "testevent1", "type": "event", "seq": 0}'
        with MockCase(handle_request):
            conn._handle_message(data)
            handle_request.assert_not_called()

    @mock.patch("traceback.print_exc")
    @mock.patch(
        "halucinator.debug_adapter.debug_adapter.DAPConnection.send_error"
    )
    @mock.patch("halucinator.debug_adapter.debug_adapter.DAPConnection._send")
    def test_handle_request(self, send, send_error, print_exc):
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())

        mockCommand = mock.Mock()
        send_error.side_effect = lambda a: setattr(conn, "sent_response", True)
        debug_adapter._request_handlers["mockCommand"] = mockCommand

        with MockCase(mockCommand, send_error, send):
            seq = mock.sentinel.seq1
            args = mock.sentinel.mockCommandArgs1
            conn._handle_request("mockCommand", seq, args)
            assert conn.response["request_seq"] == seq
            mockCommand.assert_called_once_with(conn, args)
            send.assert_not_called()
            send_error.assert_called_once_with("no response")

        with MockCase(mockCommand, send_error, send):
            seq = mock.sentinel.seq2
            mockCommand.side_effect = lambda a, b: conn.send_response()
            conn._handle_request("mockCommand", seq, None)
            assert conn.response["request_seq"] == seq
            mockCommand.assert_called_once_with(conn, None)
            send.assert_called_once()
            send_error.assert_not_called()

        with MockCase(mockCommand, send_error, send):
            seq = mock.sentinel.seq3
            mockCommand.side_effect = lambda a, b: ValueError()
            conn._handle_request("mockCommand", seq, None)
            assert conn.response["request_seq"] == seq
            mockCommand.assert_called_once_with(conn, None)
            send.assert_not_called()
            send_error.assert_called_once()

        with MockCase(mockCommand, send_error, send):
            seq = mock.sentinel.seq4
            conn._handle_request("invalidCommand", seq, None)
            assert conn.response["request_seq"] == seq
            send.assert_not_called()
            send_error.assert_called_once_with("no handler")

        del debug_adapter._request_handlers["mockCommand"]

    def test_send(self):
        class Mock_Sock(io.BytesIO):
            def sendall(self, data):
                self.write(data)

        sock = Mock_Sock()
        conn = debug_adapter.DAPConnection(Mock_Debugger(), sock)
        jso = {"seq": 12, "command": "foo", "success": True}
        encoded = (
            "Content-Length: 46\r\n\r\n"
            '{"seq": 12, "command": "foo", "success": true}'
        )
        conn._send(jso)
        assert sock.getvalue().decode() == encoded

    @mock.patch("halucinator.debug_adapter.debug_adapter.DAPConnection._send")
    def test_send_response(self, send):
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())
        conn.response = {}
        body = mock.sentinel.body

        conn.send_response(body)
        send.assert_called_once()
        assert send.call_args.args[0]["success"] == True
        assert send.call_args.args[0]["body"] == body

    @mock.patch("halucinator.debug_adapter.debug_adapter.DAPConnection._send")
    def test_send_error(self, send):
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())
        conn.response = {}
        errorMessage = mock.sentinel.errorMessage

        conn.send_error(errorMessage)
        send.assert_called_once()
        assert send.call_args.args[0]["success"] == False
        assert send.call_args.args[0]["message"] == errorMessage

    @mock.patch("halucinator.debug_adapter.debug_adapter.DAPConnection._send")
    def test_send_event(self, send):
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())
        eventName = mock.sentinel.eventName
        eventBody = mock.sentinel.eventBody

        with MockCase(send):
            conn.send_event(eventName, eventBody)
            send.assert_called_once()
            assert send.call_args.args[0]["seq"] == 0
            assert send.call_args.args[0]["type"] == "event"
            assert send.call_args.args[0]["event"] == eventName
            assert send.call_args.args[0]["body"] == eventBody

        with MockCase(send):
            conn.send_event(eventName)
            send.assert_called_once()
            assert send.call_args.args[0]["event"] == eventName
            assert "body" not in send.call_args.args[0]


@mock.patch("halucinator.debug_adapter.debug_adapter.DAPConnection")
@mock.patch("socket.socket")
def test_DapServer(socket, dapConnection):
    s1 = mock.Mock()
    s2 = mock.Mock()
    port = mock.Mock()
    listener = mock.Mock()

    socket.return_value = listener
    listener.__enter__ = mock.Mock(return_value=listener)
    listener.__exit__ = mock.Mock(return_value=False)

    # Simulate disconnect for the first connnection, SIGINT for the second
    listener.accept.side_effect = [(s1, "127.0.0.1"), (s2, "127.0.0.1")]
    s2.side_effect = KeyboardInterrupt()
    dapConnection.side_effect = lambda a, b: b()

    debug_adapter.DAPServer(Mock_Debugger(), port)()
    listener.bind.assert_called_once_with(("0.0.0.0", port))
    s1.close.assert_called_once()
    s2.close.assert_called_once()


# This test reproduces the order of messages that VSCode seems to use when
# initializing a debug session.
def test_debug_session():
    source = {"name": "program.view", "path": "/tmp/program.view"}
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

    # initialize -> events: initialized, halBreakMode
    with MockCase(conn):
        debug_adapter.initialize(conn, {"linesStartAt1": True})
        conn.send_response.assert_called_once()
        assert conn.get_event("initialized") is None
        assert "breakOnHal" in conn.get_event("halBreakMode")

    # launch -> event: needOffsets
    with MockCase(conn):
        debug_adapter.launch(conn, {"program": "/tmp/program.view"})
        conn.send_response.assert_called_once()
        assert conn.get_event("needOffsets") == {}

    # setBreakpoints -> no events
    with MockCase(conn):
        debug_adapter.setBreakpoints(
            conn,
            {
                "source": source,
                "lines": [20, 31, 22],
                "breakpoints": [{"line": 20}, {"line": 31}, {"line": 22}],
                "sourceModified": False,
            },
        )
        conn.send_response.assert_called_once()

    # setLineOffsets -> events: breakpoint (multiple), stopped
    with MockCase(conn):
        debug_adapter.setLineOffsets(
            conn, {"lineOffsetList": LINE_OFFSETS, "linesStartAt1": True}
        )
        conn.send_response.assert_called_once()
        bpEvents = conn.get_event_list("breakpoint")
        for bpEvent in bpEvents:
            assert bpEvent["reason"] == "changed"
            assert bpEvent["breakpoint"]["verified"] is True
        assert [e["breakpoint"]["id"] for e in bpEvents] == [1, 2, 3]
        assert [e["breakpoint"]["line"] for e in bpEvents] == [20, 31, 22]
        assert conn.get_event("stopped") == {"reason": "entry", "threadId": 1}
        debugger.start_monitoring.assert_called_once()
        debugger.add_callback.assert_called_once()

    # setExceptionBreakpoints -> no events
    with MockCase(conn):
        debug_adapter.setExceptionBreakpoints(conn, {"filters": []})
        conn.send_response.assert_called_once_with({"breakpoints": []})

    # breakpointLocations -> no events
    with MockCase(conn):
        debug_adapter.breakpointLocations(conn, {"source": source, "line": 20})
        assert conn.get_response()["breakpoints"] == [{"line": 20}]

    # threads -> no events
    with MockCase(conn):
        debug_adapter.threads(conn, None)
        response_threads = conn.get_response()["threads"]
        assert len(response_threads) == 1
        assert response_threads[0]["id"] == 1
        assert type(response_threads[0]["name"]) == str


def test_control_flow_requests():
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

    with MockCase(conn):
        debug_adapter.stepIn(conn, None)
        debugger.step.assert_called_once()
        conn.send_response.assert_called_once()

    with MockCase(conn):
        debug_adapter.request_next(conn, None)
        debugger.next.assert_called_once()
        conn.send_response.assert_called_once()

    with MockCase(conn):
        debug_adapter.stepOut(conn, None)
        debugger.finish.assert_called_once()
        conn.send_response.assert_called_once()

    with MockCase(conn):
        debug_adapter.pause(conn, None)
        debugger.stop.assert_called_once()
        conn.send_response.assert_called_once()

    # Continue without stopping at HAL intercepts
    with MockCase(conn):
        debug_adapter.setBreakMode(conn, {"breakOnHal": False})
        assert conn.get_response()["breakOnHal"] is False
        assert conn.get_event("halBreakMode")["breakOnHal"] is False

    with MockCase(conn, debugger):
        debug_adapter.request_continue(conn, None)
        debugger.cont.assert_not_called()
        debugger.cont_through.assert_called_once()
        conn.send_response.assert_called_once()

    # Continue until hitting a HAL intercept or other breakpoint
    with MockCase(conn):
        debug_adapter.setBreakMode(conn, {"breakOnHal": True})
        assert conn.get_response()["breakOnHal"] is True
        assert conn.get_event("halBreakMode")["breakOnHal"] is True

    with MockCase(conn):
        debug_adapter.request_continue(conn, None)
        debugger.cont.assert_called_once()
        debugger.cont_through.assert_not_called()
        conn.send_response.assert_called_once()


def test_variables():
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

    # scopes
    with MockCase(conn):
        debug_adapter.scopes(conn, None)
        scopes_result = conn.get_response()["scopes"]
        assert len(scopes_result) == 3
        assert scopes_result[0]["variablesReference"] == 1

    # changing the value of a register
    with MockCase(conn):
        debug_adapter.setVariable(
            conn, {"variablesReference": 1, "name": "r1", "value": "12"}
        )
        debugger.write_register.assert_called_once_with("r1", 12)
        conn.send_response.assert_called_once()

    # reading variables
    with MockCase(conn):
        debug_adapter.variables(conn, {"variablesReference": 1})
        reg_list = conn.get_response()["variables"]
        assert len(reg_list) == len(Mock_Debugger.DEFAULT_REGISTERS)
        for reg in reg_list:
            assert reg["name"] in Mock_Debugger.DEFAULT_REGISTERS
            assert reg["variablesReference"] == 0
            if reg["name"] == "r1":
                assert reg["value"] == "0xc"
        conn.send_event.assert_not_called()


def test_requests_send_response():
    conn = Mock_DAPConnection()

    # These requests don't do anything, but they must succeed
    with MockCase(conn):
        debug_adapter.attach(conn, {})
        conn.send_response.assert_called_once()

    with MockCase(conn):
        debug_adapter.disconnect(conn, {})
        conn.send_response.assert_called_once()

    with MockCase(conn):
        debug_adapter.source(conn, {})
        conn.send_response.assert_called_once()


def test_stackTrace():
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
    conn.runtime.line_translator = debug_adapter.LineTranslator(LINE_OFFSETS)

    for line, offset in LINE_OFFSETS:
        debugger.registers["pc"] = offset
        debug_adapter.stackTrace(conn, {})
        response = conn.get_response()
        frames = response["stackFrames"]
        assert response["totalFrames"] == len(frames)
        assert type(frames) == list
        for id, frame in enumerate(frames):
            assert frame["id"] == id + 1
            assert type(frame["name"]) == str
            assert "source" in frame
            assert type(frame["line"]) == int
            assert type(frame["column"]) == int
        assert frames[0]["line"] == line
        conn.reset_mock()


@mock.patch(
    "halucinator.debug_adapter.debug_adapter.HalRuntime.setBreakpoints"
)
def test_setBreakpoints(setBreakpoints):
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
    eventList = [mock.sentinel.event1, mock.sentinel.event2]
    bpList = [mock.sentinel.bp0, mock.sentinel.bp1]
    setBreakpoints.return_value = mock.sentinel.response, eventList
    debug_adapter.setBreakpoints(
        conn,
        {
            "source": mock.sentinel.source,
            "lines": bpList,
            "breakpoints": [{"line": bpList[0]}, {"line": bpList[1]}],
        },
    )
    setBreakpoints.assert_called_once_with(mock.sentinel.source, bpList)
    assert conn.get_event_list("breakpoint") == eventList
    assert conn.get_response()["breakpoints"] == mock.sentinel.response


def test_halBreakpoints():
    args = {
        "halBreakpoints": [
            {
                "address": 0x8000008,
                "className": mock.sentinel.className1,
                "function": mock.sentinel.funcName1,
                "runOnce": True,
                "classArgs": {"arg1": mock.sentinel.carg1},
                "registrationArgs": {"param1": mock.sentinel.farg1},
            },
            {
                "address": 0x8000010,
                "className": mock.sentinel.className2,
                "function": mock.sentinel.funcName2,
                "runOnce": False,
                "classArgs": {"arg1": mock.sentinel.carg2},
                "registrationArgs": {"param1": mock.sentinel.farg2},
            },
        ]
    }
    debugger = Mock_Debugger()
    conn = Mock_DAPConnection()
    conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

    debug_adapter.setHalBreakpoints(conn, args)
    conn.send_response.assert_called_once()
    conn.send_response.reset_mock()

    debug_adapter.listHalBreakpoints(conn, None)
    result = conn.get_response()["halBreakpoints"]
    assert result[0]["id"] == 1
    assert result[0]["address"] == 0x8000008
    assert result[0]["runOnce"] == True
    assert result[1]["id"] == 2
    assert result[1]["address"] == 0x8000010
    assert result[1]["runOnce"] == False


# ---------------------------------------------------------------------------
# Tests for missing coverage
# ---------------------------------------------------------------------------

class Test_LineTranslator_Validation:
    def test_lines_out_of_order(self):
        """Lines out of order raises ValueError (line 61)."""
        with pytest.raises(ValueError, match="out of order"):
            debug_adapter.LineTranslator([(30, 0x8000000), (20, 0x8000004)])

    def test_offsets_out_of_order(self):
        """Offsets out of order raises ValueError (line 66)."""
        with pytest.raises(ValueError, match="offsets out of order"):
            debug_adapter.LineTranslator([(20, 0x8000010), (21, 0x8000004)])


class Test_HalRuntime_Additional:
    def test_del_cleanup(self):
        """__del__ calls stop (lines 120-121)."""
        debugger = Mock_Debugger()
        send_event = mock.Mock()
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime._callback_id = 42
        runtime.__del__()
        debugger.remove_callback.assert_called_once_with(42)
        assert runtime._callback_id is None

    def test_del_no_callback(self):
        """__del__ with no callback is safe (lines 120-121)."""
        debugger = Mock_Debugger()
        send_event = mock.Mock()
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime.__del__()
        debugger.remove_callback.assert_not_called()

    def test_del_attribute_error(self):
        """__del__ handles AttributeError when partially constructed (lines 120-121)."""
        debugger = Mock_Debugger()
        send_event = mock.Mock()
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        # Simulate partial destruction by removing the attribute
        del runtime._callback_id
        # Should not raise
        runtime.__del__()

    def test_state_callback_hal_stop(self):
        """HAL_STOP sends pause event (line 145-146)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        runtime._state_callback(CallbackState.HAL_STOP)
        event = conn.get_event("stopped")
        assert event["reason"] == "pause"

    def test_state_callback_hal_bp(self):
        """HAL_BP sends breakpoint event."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        runtime._state_callback(CallbackState.HAL_BP)
        event = conn.get_event("stopped")
        assert event["reason"] == "breakpoint"

    def test_state_callback_unknown_stopped(self):
        """Unknown callback state falls through to check debugger state (line 145-146)."""
        debugger = Mock_Debugger()
        debugger.get_state = mock.Mock(return_value=DebugState.STOPPED)
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        # Use a state not in any explicit branch (e.g., create a mock state)
        # Actually CallbackState might not have another value, so let's use an int
        runtime._state_callback(999)
        event = conn.get_event("stopped")
        assert event["reason"] == "step"

    def test_launch_with_translator(self):
        """Launch with existing translator calls _actual_launch (line 166)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        runtime.line_translator = debug_adapter.LineTranslator(LINE_OFFSETS)
        source = {"name": "test.view", "path": "/tmp/test.view"}
        runtime.launch(source)
        debugger.start_monitoring.assert_called_once()
        debugger.add_callback.assert_called_once()

    def test_pause_when_already_stopped(self):
        """Pause sends stop event if debugger reports stopped (line 208)."""
        debugger = Mock_Debugger()
        debugger.stop = mock.Mock(return_value=False)
        debugger.get_state = mock.Mock(return_value=DebugState.STOPPED)
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        runtime.pause()
        event = conn.get_event("stopped")
        assert event["reason"] == "pause"

    def test_setRegister_sp(self):
        """Setting SP sends invalidated event (line 365)."""
        send_event = mock.Mock()
        debugger = Mock_Debugger()
        debugger.write_register = mock.Mock(return_value=True)
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        runtime.setRegister("sp", 0x1000)
        send_event.assert_called_once()
        assert send_event.call_args.args[0] == "invalidated"
        assert "variables" in send_event.call_args.args[1]["areas"]

    def test_setRegister_failure(self):
        """Setting register returns False when write fails."""
        send_event = mock.Mock()
        debugger = Mock_Debugger()
        debugger.write_register = mock.Mock(return_value=False)
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        result = runtime.setRegister("r0", 0)
        assert result is False
        send_event.assert_not_called()

    def test_stackTrace_no_translator(self):
        """Stack trace without line translator defaults to line 1."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        # No line_translator set
        frames = runtime.stackTrace()
        assert frames[0]["line"] == 1

    def test_addLineTranslator_with_queued(self):
        """addLineTranslator processes queued breakpoints and launch."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        # Queue breakpoints by setting them without translator
        runtime.setBreakpoints({}, [20, 22])
        assert runtime._queued_breakpoints == [20, 22]

        # Queue launch
        runtime.launch({"name": "test", "path": "/tmp/test"})
        assert runtime._queued_launch is True

        # Now add translator
        runtime.addLineTranslator(debug_adapter.LineTranslator(LINE_OFFSETS))
        assert runtime._queued_breakpoints is None
        assert runtime._queued_launch is False
        debugger.start_monitoring.assert_called_once()


class Test_DAPConnection_Additional:
    def test_send_null_socket(self):
        """_send with None socket does nothing (lines 448-449)."""
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())
        conn._sock = None
        # Should not raise
        conn._send({"seq": 0})

    def test_handle_request_exception(self):
        """_handle_request catches exceptions (lines 442-444)."""
        conn = debug_adapter.DAPConnection(Mock_Debugger(), mock.Mock())
        conn.response = {"seq": 0, "type": "response", "request_seq": 1, "command": "test", "success": True}

        def bad_handler(dap, args):
            raise RuntimeError("test error")

        debug_adapter._request_handlers["_test_bad"] = bad_handler
        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()

        with mock.patch("traceback.print_exc"):
            conn._handle_request("_test_bad", 1, {})
        conn.send_error.assert_called_once_with("unhandled exception while processing request")

        del debug_adapter._request_handlers["_test_bad"]

    def test_send_with_dump_messages(self, capsys):
        """_send prints message when dump_messages is True (line 456)."""
        import halucinator.debug_adapter.debug_adapter as da
        original = da.dump_messages
        da.dump_messages = True
        try:
            sock = mock.Mock()
            conn = debug_adapter.DAPConnection(Mock_Debugger(), sock)
            conn._send({"seq": 0, "test": True})
            sock.sendall.assert_called()
            captured = capsys.readouterr()
            assert "DAP <<" in captured.out
        finally:
            da.dump_messages = original

    def test_run_reads_messages(self):
        """run method processes messages until connection closes (lines 515-530)."""
        debugger = Mock_Debugger()
        sock = mock.Mock()
        conn = debug_adapter.DAPConnection(debugger, sock)

        # Simulate a stream that has one message then EOF
        msg = '{"type":"request","command":"threads","seq":1}'
        stream_data = "Content-Length: %d\r\n\r\n%s" % (len(msg), msg)
        file_mock = io.StringIO(stream_data)
        sock.makefile.return_value = file_mock
        file_mock.close = mock.Mock()  # prevent actual close

        conn._handle_message = mock.Mock()
        conn.run()
        conn._handle_message.assert_called_once_with(msg)


class Test_DAPHandlers_Additional:
    def test_dataBreakpointInfo_with_address(self):
        """dataBreakpointInfo returns data ID when address exists (lines 667-679)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        conn.runtime.vars = mock.Mock()
        conn.runtime.vars.get_address.return_value = 0x1000

        debug_adapter.dataBreakpointInfo(conn, {"name": "0x1000", "variablesReference": 2})
        response = conn.get_response()
        assert response["dataId"] == "0x1000"
        assert "Watchpoint" in response["description"]

    def test_dataBreakpointInfo_without_address(self):
        """dataBreakpointInfo returns null when no address (lines 679-684)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        conn.runtime.vars = mock.Mock()
        conn.runtime.vars.get_address.return_value = None

        debug_adapter.dataBreakpointInfo(conn, {"name": "r0", "variablesReference": 1})
        response = conn.get_response()
        assert response["dataId"] is None

    def test_setDataBreakpoints(self):
        """setDataBreakpoints sets watchpoints (lines 691-697)."""
        debugger = Mock_Debugger()
        debugger.set_watchpoint = mock.Mock(return_value=1)
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.setDataBreakpoints(conn, {
            "breakpoints": [{"dataId": "0x1000"}]
        })
        response = conn.get_response()
        assert response["breakpoints"][0]["id"] == 1
        assert response["breakpoints"][0]["verified"] is True

    def test_evaluate_repl(self):
        """evaluate with repl context (lines 801-814)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.evaluate(conn, {"expression": "1+1", "context": "repl"})
        response = conn.get_response()
        assert "2" in response["result"]

    def test_evaluate_repl_exception(self):
        """evaluate_repl catches exceptions (line 807-808)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        # An expression that can't be eval'd
        debug_adapter.evaluate(conn, {"expression": "raise ValueError()", "context": "repl"})
        response = conn.get_response()
        assert response["variablesReference"] == 0

    def test_evaluate_watch_valid(self):
        """evaluate with watch context returns memory (lines 819-829)."""
        debugger = Mock_Debugger()
        debugger.read_memory = mock.Mock(return_value=[0x42])
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.evaluate(conn, {"expression": "0x1000", "context": "watch"})
        response = conn.get_response()
        assert response["result"] == "0x42"
        assert response["memoryReference"] == "0x1000"

    def test_evaluate_watch_invalid(self):
        """evaluate_watch with invalid expression sends error (line 822)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        debug_adapter.evaluate_watch(conn, "notanumber")
        conn.send_error.assert_called()

    def test_evaluate_watch_empty_mem(self):
        """evaluate_watch with empty memory read sends error (lines 825-826)."""
        debugger = Mock_Debugger()
        debugger.read_memory = mock.Mock(return_value=[])
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        debug_adapter.evaluate_watch(conn, "0x1000")
        conn.send_error.assert_called()

    def test_evaluate_unsupported_context(self):
        """evaluate with unsupported context sends error (line 795)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        debug_adapter.evaluate(conn, {"expression": "x", "context": "hover"})
        conn.send_error.assert_called()

    def test_readMemory_valid(self):
        """readMemory returns base64 data (lines 841-858)."""
        debugger = Mock_Debugger()
        debugger.read_memory = mock.Mock(return_value=b"\x01\x02\x03\x04")
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.readMemory(conn, {
            "memoryReference": "0x1000", "offset": 0, "count": 4
        })
        response = conn.get_response()
        assert response["address"] == "0x1000"
        import base64
        assert base64.b64decode(response["data"]) == b"\x01\x02\x03\x04"

    def test_readMemory_invalid_ref(self):
        """readMemory with invalid reference sends error (line 847)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        debug_adapter.readMemory(conn, {
            "memoryReference": "not_a_number", "offset": 0, "count": 4
        })
        conn.send_error.assert_called()

    def test_readMemory_exception(self):
        """readMemory exception sends error (line 857-858)."""
        debugger = Mock_Debugger()
        debugger.read_memory = mock.Mock(side_effect=RuntimeError("fail"))
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        debug_adapter.readMemory(conn, {
            "memoryReference": "0x1000", "offset": 0, "count": 4
        })
        conn.send_error.assert_called()

    def test_readMemory_zero_count(self):
        """readMemory with count=0 returns empty data."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.readMemory(conn, {
            "memoryReference": "0x1000", "offset": 0, "count": 0
        })
        response = conn.get_response()
        assert response["data"] == ""

    def test_variableFormat_get_and_set(self):
        """variableFormat request (lines 932-953)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        # Get format without setting
        debug_adapter.variableFormat(conn, {
            "variablesReference": 1, "name": "r0"
        })
        response = conn.get_response()
        assert response["variablesReference"] == 1
        assert response["name"] == "r0"
        assert response["format"] is not None
        conn.send_event.assert_not_called()
        conn.reset_mock()

        # Set format
        debug_adapter.variableFormat(conn, {
            "variablesReference": 1,
            "name": "r0",
            "format": {"baseType": "unsigned"}
        })
        response = conn.get_response()
        assert response["format"]["baseType"] == "unsigned"
        conn.get_event("invalidated")
        conn.reset_mock()

        # Reset format
        debug_adapter.variableFormat(conn, {
            "variablesReference": 1,
            "name": "r0",
            "reset": True
        })
        conn.get_event("invalidated")

    def test_variableFormat_no_name(self):
        """variableFormat without name returns None format."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.variableFormat(conn, {"variablesReference": 1})
        response = conn.get_response()
        assert response["format"] is None

    def test_variableGlobals(self):
        """variableGlobals add and remove (lines 972-975)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.variableGlobals(conn, {"add": ["0x100", "0x200"]})
        conn.send_response.assert_called_once()
        conn.get_event("invalidated")
        assert 0x100 in conn.runtime.vars.globals
        assert 0x200 in conn.runtime.vars.globals
        conn.reset_mock()

        debug_adapter.variableGlobals(conn, {"remove": ["0x100"]})
        conn.send_response.assert_called_once()
        assert 0x100 not in conn.runtime.vars.globals

    def test_setLineOffsets_with_adjustment(self):
        """setLineOffsets with different linesStartAt1 adjusts lines (line 1005)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)
        conn.runtime.linesStartAt1 = True

        # Lines start at 0 in the input, so they need +1 adjustment
        offsets = [(19, 0x8000000), (20, 0x8000004)]
        debug_adapter.setLineOffsets(conn, {
            "lineOffsetList": offsets, "linesStartAt1": False
        })
        conn.send_response.assert_called_once()
        # The line translator should have adjusted lines by +1
        lt = conn.runtime.line_translator
        assert lt.find_line_number(0x8000000) == 20
        assert lt.find_line_number(0x8000004) == 21

    def test_setVariable_error(self):
        """setVariable with ValueError sends error (lines 768-769)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        conn.send_error = mock.Mock(side_effect=lambda a: setattr(conn, "sent_response", True))
        conn._send = mock.Mock()
        # Try setting an invalid register
        debug_adapter.setVariable(conn, {
            "variablesReference": 1, "name": "bad_reg", "value": "0"
        })
        conn.send_error.assert_called()

    def test_breakpointLocations_no_translator(self):
        """breakpointLocations without line translator returns line as-is (line 787)."""
        debugger = Mock_Debugger()
        conn = Mock_DAPConnection()
        conn.runtime = debug_adapter.HalRuntime(conn.send_event, debugger)

        debug_adapter.breakpointLocations(conn, {"line": 42})
        response = conn.get_response()
        assert response["breakpoints"] == [{"line": 42}]

    def test_setBreakpoints_noLines_with_hal_bps(self):
        """setBreakpoints with no translator includes hal breakpoints in numbering."""
        debugger = Mock_Debugger()
        debugger.debug_breakpoints = {1: 0x8000000}
        from halucinator.bp_handlers.intercepts import BPHandlerInfo
        debugger.hal_breakpoints = {
            2: BPHandlerInfo(0x8000004, ("cls", {}), "", ("fn", {}), False)
        }
        send_event = mock.Mock()
        runtime = debug_adapter.HalRuntime(send_event, debugger)
        # No translator set

        bpList, events = runtime.setBreakpoints({}, [20])
        assert bpList[0]["id"] == 3  # max(1,2)+1
        assert bpList[0]["verified"] is False

    def test_run_with_dump_messages(self):
        """run method prints messages when dump_messages is True."""
        import halucinator.debug_adapter.debug_adapter as da
        original = da.dump_messages
        da.dump_messages = True
        try:
            debugger = Mock_Debugger()
            sock = mock.Mock()
            conn = debug_adapter.DAPConnection(debugger, sock)

            msg = '{"type":"request","command":"threads","seq":1}'
            stream_data = "Content-Length: %d\r\n\r\n%s" % (len(msg), msg)
            file_mock = io.StringIO(stream_data)
            sock.makefile.return_value = file_mock
            file_mock.close = mock.Mock()

            conn._handle_message = mock.Mock()
            conn.run()
            conn._handle_message.assert_called_once()
        finally:
            da.dump_messages = original
