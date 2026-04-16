from __future__ import annotations

import base64
import bisect
import json
import logging
import socket
import sys
import threading
import traceback

log = logging.getLogger(__name__)
from io import StringIO
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from halucinator.bp_handlers.debugger import (
    CallbackState,
    Debugger,
    DebugState,
)
from halucinator.debug_adapter.variables import Variables

# When True, all DAP messages are printed to stdout, making it easy to see
# which requests and events are being received in a real DAP session and what
# arguments VSCode actually provides with those requests.
dump_messages = False

PC_REGISTER = "pc"
SP_REGISTER = "sp"
DEFAULT_PORT = 34157


class LineTranslator(object):
    """ Translation handler between line numbers and instruction offsets

    This class handles translating between the line numbers, as they appear in
    the disassembly view file (e.g., a gview file from Ghidra) that displays
    the disassembled contents to the user, and the actual addresses of
    instructions in the running program.
    Every instruction occupies exactly one line, in order, but many lines contain
    other things (section labels, comments, etc) instead of instructions.
    """

    def __init__(self, lineOffsetList: List[Tuple[int, int]]):
        """Constructor

        Parameters:
            lineOffsetList: A list of (line_number, offset) pairs. Will be
                sorted by line number for bisect lookups. Duplicate line
                numbers (overlapping/aliased addresses) are dropped — first
                occurrence wins.
        """
        # Sort by line number, deduplicate by line (same line can't have
        # two different addresses). Mask Thumb bit so lookups match.
        sorted_pairs = sorted(lineOffsetList, key=lambda p: (p[0], p[1]))
        seen_lines: set = set()
        deduped: List[Tuple[int, int]] = []
        for line, off in sorted_pairs:
            if line in seen_lines:
                continue
            seen_lines.add(line)
            deduped.append((line, off & 0xFFFFFFFE))

        # Lookup structures for the two directions:
        #
        # Line → addr: lineList + offsetList (parallel arrays sorted by
        # line number). find_next_instruction uses bisect_left on lineList.
        #
        # Addr → line: _addr_to_line dict (O(1) exact match) +
        # _sorted_addrs/_sorted_lines (sorted by address for bisect
        # fallback when PC is between known instructions).
        # lineList/offsetList: sorted by LINE number — used by
        # find_next_instruction (bisect_left on lineList).
        self.lineList = [r[0] for r in deduped]
        self.offsetList = [r[1] for r in deduped]

        # _addr_to_line: exact address → line lookup (O(1)).
        self._addr_to_line: Dict[int, int] = {}
        for line, off in deduped:
            if off not in self._addr_to_line:
                self._addr_to_line[off] = line

        # _sorted_addrs / _sorted_lines: sorted by ADDRESS for bisect
        # fallback in find_line_number when the exact address isn't in
        # the dict (PC between two known instructions).
        by_addr = sorted(
            [(off, line) for line, off in deduped],
            key=lambda p: p[0],
        )
        self._sorted_addrs = [p[0] for p in by_addr]
        self._sorted_lines = [p[1] for p in by_addr]

        global line_translator
        line_translator = self

    def find_next_instruction(self, line: int) -> Tuple[int, int]:
        """
        Find the next instruction that is at, or after, the specified line
        number.

        Returns: a (line_number, offset) pair

        Raises LookupError if the requested line number is after the last
        instruction.
        """
        # lineList is sorted (line numbers are always monotonic in a file)
        i = bisect.bisect_left(self.lineList, line)
        if i != len(self.lineList):
            return (self.lineList[i], self.offsetList[i])
        raise LookupError

    def find_line_number(self, addr: int) -> Optional[int]:
        """ Finds the line number associated with the specified address.
        Masks the Thumb bit (bit 0) so 0x8000809 maps the same as 0x8000808.
        Tries exact dict lookup first; falls back to bisect on the
        address-sorted list for "closest instruction at or before addr".
        """
        addr = addr & 0xFFFFFFFE  # Clear Thumb bit
        # Exact match — fast path
        exact = self._addr_to_line.get(addr)
        if exact is not None:
            return exact
        # Fallback: closest instruction at or before this address
        i = bisect.bisect_right(self._sorted_addrs, addr)
        return self._sorted_lines[i - 1] if i > 0 else None


line_translator = None

EventCallback = Callable[[str, Optional[Dict[str, Any]]], None]


class HalRuntime(object):
    """ DAP abstraction of the Halucinator debugging runtime

    HalRuntime provides several methods that control the underlying debugger
    object, registers itself as a callback to receive events from the debugger,
    and sends event messages back to the DAPConnection as needed.
    """

    def __init__(self, callback: EventCallback, debugger: Debugger) -> None:
        self.debugger = debugger
        self._queued_breakpoints: Optional[List[int]] = None
        self._queued_launch = False
        self._event_callback = callback
        self._source_info: Dict[str, Any] = {}
        self._callback_id: Optional[int] = None
        self.breakOnHal = False
        self.linesStartAt1 = True
        self.vars = Variables(self)
        self.line_translator: Optional[LineTranslator] = None

    def __del__(self) -> None:
        try:
            self.stop()
        except AttributeError:
            pass

    def stop(self) -> None:
        """ Cleanup: unregisters this object's debug state callback """
        if self._callback_id is not None:
            self.debugger.remove_callback(self._callback_id)
            self._callback_id = None

    def _state_callback(self, state: CallbackState) -> None:
        if state in [CallbackState.STOP, CallbackState.HAL_STOP]:
            self._event_callback("stopped", {"reason": "pause", "threadId": 1, "allThreadsStopped": True})
        elif state in [
            CallbackState.STEP,
            CallbackState.NEXT,
            CallbackState.FINISH,
        ]:
            self._event_callback("stopped", {"reason": "step", "threadId": 1, "allThreadsStopped": True})
        elif state in [CallbackState.DEBUG_BP, CallbackState.HAL_BP]:
            self._event_callback(
                "stopped", {"reason": "breakpoint", "threadId": 1, "allThreadsStopped": True}
            )
        elif state == CallbackState.EXIT:
            self._event_callback("exited", {"exitCode": 0})
            self._event_callback("terminated", None)
        elif self.debugger.get_state() == DebugState.STOPPED:
            # Fallback: target stopped for an unrecognized reason (e.g. CONT
            # callback that fired because PC didn't match any known bp due to
            # Thumb bit or other quirks). Always send a stopped event so the
            # VSCode debug UI transitions out of "running" state.
            self._event_callback("stopped", {"reason": "breakpoint", "threadId": 1, "allThreadsStopped": True})

    def launch(self, source: Dict[str, Any]) -> None:
        """ "Launches" this HalRuntime by registering it for event updates.

        Parameters:
            source: Details of the source file being debugged.
                This should be in a format matching the "Source" type defined
                in the DAP specification. Among other optional fields, this
                should include a "name" and a "path".

        Handling those event updates requires that this HalRuntime object
        knows the line number to offset mapping. If `addLineTranslator` has
        not yet been called, the launch is automatically delayed until it is.
        """
        self._source_info = source
        if self.line_translator is None:
            self._queued_launch = True
            self._event_callback("needOffsets", {})
        else:
            self._actual_launch()

    def _actual_launch(self) -> None:
        self._callback_id = self.debugger.add_callback(self._state_callback)
        # Note: start_monitoring is now called when the DAP server starts in
        # main.py, so the request_queue is always being serviced.

        # Always send a stopped-at-entry event to the client. The DAP client
        # expects this after sending 'launch' so it can show the "Continue"
        # button (instead of the "Pause" button it shows for running targets).
        self._event_callback("stopped", {"reason": "entry", "threadId": 1})

    def request_continue(self) -> bool:
        """ Resumes target execution, respecting the breakOnHal setting """
        if self.breakOnHal:
            return self.debugger.cont()
        else:
            return self.debugger.cont_through()

    def step(self) -> bool:
        """ Executes a single target instruction """
        return self.debugger.step()

    def next(self) -> bool:
        """ Resumes target execution until the next instruction; step over

        Unlike "step", this should execute past any branch and continue until
        control returns to the current stack frame or a breakpoint is reached.
        """
        return self.debugger.next()

    def stepOut(self) -> bool:
        """ Resumes target execution until the current stack frame returns """
        return self.debugger.finish()

    def pause(self) -> bool:
        """ Suspends target execution """
        rc = self.debugger.stop()
        if not rc and self.debugger.get_state() == DebugState.STOPPED:
            # Even if the stop failed, send a stop event anyway
            # as long as the target is stopped.
            # This helps avoid getting the debug session stuck if some stop
            # event failed to go through for any reason; the user can just
            # press Pause and get back to debugging.
            self._state_callback(CallbackState.STOP)
        return rc

    def addLineTranslator(self, line_translator: LineTranslator) -> None:
        """ Add a LineTranslator object to this HalRuntime instance

        Several important features of this runtime depend on knowing which line
        numbers correspond to which memory addresses; any updates related to
        execution state will not be sent to the client and breakpoints cannot
        be set in the underlying debugger until this method is called.
        This method can be called at any point during the DAP session, and any
        previous attempts to set breakpoints and state events will take effect
        at that time.
        """
        self.line_translator = line_translator
        if self._queued_breakpoints is not None:
            _, bp_events = self.setBreakpoints(
                self._source_info, self._queued_breakpoints
            )
            self._queued_breakpoints = None
            for e in bp_events:
                self._event_callback("breakpoint", e)
        if self._queued_launch:
            self._actual_launch()
            self._queued_launch = False

    def setBreakpoints(
        self, source: Dict[str, Any], breakpoints: List[int]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """ Sets a list of debug breakpoints, overriding any previous ones

        Parameters:
            source: Details of the source file being debugged.
                This should be in a format matching the "Source" type defined
                in the DAP specification. Among other optional fields, this
                should include a "name" and a "path".
            breakpoints: A list of line numbers that should have a breakpoint

        The provided breakpoint list is expected to include all breakpoints
        that should exist. Any breakpoint that currently exists but is not
        present in the list will be removed, while those that already were set
        will retain their existing breakpoint number and new ones will be
        added as needed.

        A two-tuple is returned, containing the list of acknowledged
        breakpoints in the format that DAP expects in the response message
        as the first element, and a list of DAP breakpoint events that
        correspond to breakpoint additions and removals as the second element.

        This method does not send those events to the DAP client itself,
        because it's generally expected that breakpoint events should be sent
        after the response to the setBreakpoints request.

        If this HalRuntime does not yet have a LineTranslator, the breakpoints
        will be unverified until it does.
        """
        self._source_info = source

        # If we don't know the mapping from line numbers to addresses yet,
        # don't actually set the breakpoints yet. Instead, save the list for
        # later; addLineTranslator will call setBreakpoints again when this
        # information is available. However, VSCode requires responding to the
        # setBreakpoints request right away, so we still give it a response
        # with the list of breakpoints it wanted.
        # Unfortunately, this has the effect that any breakpoints set on
        # a non-instruction line (labels, comments, whitespace, etc) won't get
        # adjusted to their true location if they're set too early.
        if self.line_translator is None:
            self._queued_breakpoints = breakpoints
            bpNumList = [k for k in self.debugger.list_debug_breakpoints()] + [
                k for k in self.debugger.list_hal_breakpoints()
            ]
            bpNext = max(bpNumList) + 1 if len(bpNumList) > 0 else 1
            bp_details = [
                {"id": bpNext + bpIdx, "verified": False, "line": line}
                for bpIdx, line in enumerate(breakpoints)
            ]
            return bp_details, []

        # Prepare a map from line number to bpnum of existing debug breakpoints
        existing_breakpoints = {}
        for idx, addr in self.debugger.list_debug_breakpoints().items():
            line = self.line_translator.find_line_number(addr)
            existing_breakpoints[line] = idx

        # Add any new breakpoints that are needed, and determine which of the
        # specified breakpoints already exist and don't need to be added.
        bp_added = []
        bp_kept = []
        bp_added_or_kept = set()
        for bp in breakpoints:
            line, addr = self.line_translator.find_next_instruction(bp)
            if line in bp_added_or_kept:
                continue
            bp_added_or_kept.add(line)
            if line in existing_breakpoints:
                bp_kept.append(
                    {
                        "id": existing_breakpoints[line],
                        "verified": True,
                        "line": line,
                    }
                )
                del existing_breakpoints[line]
                continue
            bpNum = self.debugger.set_debug_breakpoint(addr)
            bp_added.append({"id": bpNum, "verified": True, "line": line})

        # Any breakpoint that already exists but was not provided as an
        # argument to this request should be removed.
        bp_removed = []
        for line, bpNum in existing_breakpoints.items():
            self.debugger.remove_debug_breakpoint(bpNum)
            bp_removed.append({"id": bpNum, "line": line})

        bp_events = [
            {"reason": "changed", "breakpoint": bp} for bp in bp_added
        ] + [{"reason": "removed", "breakpoint": bp} for bp in bp_removed]
        return bp_kept + bp_added, bp_events

    # Last line reported to the DAP client. When the PC is at an address
    # not in the gview (e.g., HAL intercept return stub in halucinator's
    # memory region at 0x30000000), we keep showing the last known line
    # instead of jumping to line 1.
    _last_reported_line: int = 1

    def stackTrace(self) -> List[Dict[str, Any]]:
        """ Returns a minimal "stack trace" that can be sent to the DAP client

        Currently, this method will always provide only a single stack frame,
        using the current value of the PC register to convey execution state.
        This may change in the future.
        """
        addr = self.debugger.read_register(PC_REGISTER, False)
        if isinstance(addr, int):
            addr = addr & 0xFFFFFFFE  # Clear Thumb bit
        line = self._last_reported_line
        if self.line_translator is not None:
            resolved = self.line_translator.find_line_number(addr)
            if resolved is not None:
                line = resolved
                self._last_reported_line = line
        return [
            {
                "id": 1,
                "name": "main",
                "source": self._source_info,
                "line": line,
                "column": 1,
            }
        ]

    def getRegisters(self) -> Dict[str, int]:
        """ Returns the current values of all target registers """
        return self.debugger.list_all_regs_values(False)

    def setRegister(self, name: str, value: int) -> bool:
        """ Modifies the value of a single target register

        Parameters:
            name: A register name. Must be a valid register.
            value: The value to write. Must fit in 32 bits.
        """
        if not self.debugger.write_register(name, value):
            return False
        if name == PC_REGISTER:
            self._event_callback("stopped", {"reason": "goto", "threadId": 1})
        elif name == SP_REGISTER:
            self._event_callback("invalidated", {"areas": ["variables"]})
        return True

    def getHalBreakpoints(self) -> List[Dict[str, Any]]:
        """ Returns a list of all currently-defined Halucinator intercepts """
        return [
            {"id": bpId, "address": bpinfo.address, "runOnce": bpinfo.run_once}
            for bpId, bpinfo in self.debugger.list_hal_breakpoints().items()
        ]


# ----------------------------------------------------------
# Debug Adapter Protocol socket/connection handling
# ----------------------------------------------------------
class DAPConnection(object):
    """ An active connection to a Debug Adapter Protocol (DAP) client.

    DAPConnection objects can be used to send responses and events back to the
    Debugging front-end client (VSCode).
    This class primarily handles network communication and the message protocol.
    Other aspects of the debugging session are managed by a `HalRuntime` object
    which can be accessed through the `runtime` attribute of this class.

    Attributes:
        runtime: A `HalRuntime` instance used for managing the debug session
    """

    def __init__(self, debug: Debugger, sock: socket.socket) -> None:
        self._write_lock = threading.Lock()
        self._sock: socket.socket = sock
        self._reader: Optional[TextIO] = None
        self.runtime = HalRuntime(self.send_event, debug)

    def _read_header(self) -> int:
        # Messages should start with a header like:
        # b"Content-Length: 512\n\n"
        length = -1
        while True:
            header = self._reader.readline() if self._reader else ""
            header_arr = header.split()
            # An empty line denotes the end of the message header
            if len(header_arr) == 0:
                break
            # Use only the Content-Length header line.
            # Ignore any others that may (but shouldn't) exist.
            if header_arr[0] == "Content-Length:":
                length = int(header_arr[1])
        return length

    def _handle_message(self, data: str) -> None:
        jso = json.loads(data)
        mtype = jso["type"]
        seq = jso["seq"]
        if mtype == "request":
            command = jso["command"]
            args = jso.get("arguments")
            self._handle_request(command, seq, args)

    def _handle_request(
        self, command: str, seq: int, args: Dict[str, Any]
    ) -> None:
        self.response = {
            "seq": 0,
            "type": "response",
            "request_seq": seq,
            "command": command,
            "success": True,
        }
        command_func = _getRequestHandler(command)
        self.sent_response = False
        if command_func is None:
            self.send_error("no handler")
            return
        try:
            command_func(self, args)
            if not self.sent_response:
                self.send_error("no response")
        except:
            self.send_error("unhandled exception while processing request")
            traceback.print_exc()

    def _send(self, contents: Dict[str, Any]) -> None:
        content_string = json.dumps(contents).encode("utf8")
        if self._sock is None:
            return
        with self._write_lock:
            self._sock.sendall(
                b"Content-Length: %d\r\n\r\n" % len(content_string)
            )
            self._sock.sendall(content_string)
            if dump_messages:
                print("DAP <<", content_string.decode("utf8"))

    def send_response(
        self,
        body: Optional[Dict[str, Any]] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        """ Sends a response for the most recently received request.

        This method should be called exactly once for each request handled.
        If it is not called, an error response will be sent after the request
        handler returns.

        Parameters:
            body:
                Optional data to include in the response message.
            errorMessage:
                A short description of an error that occurred while handling
                the current request.
        """
        response_message = self.response
        response_message["success"] = errorMessage is None
        if body is not None:
            response_message["body"] = body
        if errorMessage is not None:
            response_message["message"] = errorMessage
        self._send(response_message)
        self.sent_response = True

    def send_error(self, errorMessage: str) -> None:
        """ Sends an error response to the most recently received request.

        If you need to include structured details about the error, use the
        `send_response` method instead.
        """
        self.send_response(None, errorMessage)

    def send_event(self, event: str, body: Dict[str, Any] = None) -> None:
        """ Send an event message to the connected DAP client.

        Parameters:
            event:
                The event name. Most event names are defined by the DAP
                specification, but custom event names are also supported.
            body:
                An optional body providing additional information about this
                event.
        """
        event_message = {"seq": 0, "type": "event", "event": event}
        if body is not None:
            event_message["body"] = body
        self._send(event_message)

    def run(self) -> None:
        """ The main runner for a DAPConnection.

        Waits for any incoming messages from the DAP client, handling each
        one as it arrives. This method does not exit until the connection has
        closed. """
        try:
            with self._sock.makefile() as reader:
                self._reader = reader
                # Message loop
                while True:
                    length = self._read_header()
                    if length < 0:
                        break
                    data = reader.read(length)
                    if len(data) == 0:
                        break
                    if dump_messages:
                        print("DAP >>", data)
                    self._handle_message(data)
        finally:
            self.runtime.stop()


class DAPServer(object):
    """ A network server for the Debug Adapter Protocol (DAP).

    To run the server on the current thread, instantiate this class and call
    it. Only a single client may be connected at a time.
    """

    def __init__(
        self,
        debug: Debugger,
        port: int = DEFAULT_PORT,
        bind_addr: str = "127.0.0.1",
    ) -> None:
        """ Constructor

        Parameters:
            debug: the Debugger used for all connections to this server
            port: which port number this server will listen on
            bind_addr: which interface to bind to. Defaults to loopback
                (127.0.0.1) so the debug server is not exposed on the
                network. Pass "0.0.0.0" (or a specific interface address)
                explicitly to accept remote connections — no auth is
                implemented, so only do this on trusted networks.
        """
        self.debug = debug
        self.port = port
        self.bind_addr = bind_addr

    def __call__(self) -> None:
        """ Runs this server, listening for and handling any connections.

        Each accepted connection is handled on its own thread so probe
        connections (e.g. the VSCode extension's reachability check) don't
        prevent the real debug session from being accepted promptly.
        """
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind_addr, self.port))
            listener.listen()
            try:
                while True:
                    sock, _ = listener.accept()
                    threading.Thread(
                        target=self._handle_connection,
                        args=(sock,),
                        daemon=True,
                    ).start()
            except KeyboardInterrupt:
                pass

    def _handle_connection(self, sock: socket.socket) -> None:
        try:
            DAPConnection(self.debug, sock).run()
        except Exception as e:
            log.error("DAP connection error: %s", e)
        finally:
            try:
                sock.close()
            except OSError:
                pass


# ----------------------------------------------------------
# Managing the list of request handlers
# ----------------------------------------------------------
DapHandlerFunc = Callable[[DAPConnection, Dict[str, Any]], None]

_request_handlers: Dict[str, DapHandlerFunc] = {}


def _getRequestHandler(command: str) -> Optional[DapHandlerFunc]:
    return _request_handlers.get(command)


# Decorator for DAP request handler functions
def dapHandler(name: str) -> Callable[[DapHandlerFunc], DapHandlerFunc]:
    def dapHandler2(func: DapHandlerFunc) -> DapHandlerFunc:
        _request_handlers[name] = func
        return func

    return dapHandler2


# ----------------------------------------------------------
# Request handlers
#
# For documentation on the format of all these requests, see:
# https://microsoft.github.io/debug-adapter-protocol/specification
# ----------------------------------------------------------


@dapHandler("initialize")
def initialize(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "initialize". """
    dap.send_response(
        {
            "supportsConfigurationDoneRequest": False,
            "supportsSetVariable": True,
            "supportsLoadedSourcesRequest": False,
            "supportsDataBreakpoints": True,
            "supportsReadMemoryRequest": True,
            "supportsBreakpointLocationsRequest": True,
        }
    )
    dap.runtime.linesStartAt1 = args.get("linesStartAt1", True)
    dap.send_event("initialized")
    dap.send_event("halBreakMode", {"breakOnHal": dap.runtime.breakOnHal})


@dapHandler("launch")
def launch(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "launch". """
    program = args.get("program", "")
    basename = program.replace("\\", "/").split("/")[-1]
    source = {"name": basename, "path": program}
    dap.runtime.launch(source)
    dap.send_response()

    # Send stopped-at-entry event immediately, before the client times out
    # waiting for it. This ensures VSCode shows Continue/Step buttons.
    # The runtime's _actual_launch() may also send this event later when
    # line offsets arrive — duplicate stopped events are harmless.
    dap.send_event("stopped", {"reason": "entry", "threadId": 1, "allThreadsStopped": True})


@dapHandler("attach")
def attach(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "attach". """
    dap.send_response()


@dapHandler("disconnect")
def disconnect(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "disconnect".

    Always terminate halucinator on disconnect — there's no use case for
    keeping a halucinator session alive after the debug client goes away.
    """
    dap.send_response()
    # Schedule exit after sending response (give socket time to flush)
    def _exit() -> None:
        import time as _t
        _t.sleep(0.3)
        import os as _os
        _os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()


@dapHandler("breakpointLocations")
def breakpointLocations(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "breakpointLocations". """
    line = args["line"]
    if dap.runtime.line_translator is not None:
        line, _ = dap.runtime.line_translator.find_next_instruction(line)
    dap.send_response({"breakpoints": [{"line": line}]})


@dapHandler("setBreakpoints")
def setBreakpoints(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "setBreakpoints". """
    bpoints = args.get("lines", [])
    source = args.get("source", {})
    res_bpoints, bp_events = dap.runtime.setBreakpoints(source, bpoints)
    dap.send_response({"breakpoints": res_bpoints})
    for e in bp_events:
        dap.send_event("breakpoint", e)


@dapHandler("setExceptionBreakpoints")
def setExceptionBreakpoints(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "setExceptionBreakpoints". """
    # Exception breakpoints are unsupported and ignored
    dap.send_response({"breakpoints": []})


@dapHandler("dataBreakpointInfo")
def dataBreakpointInfo(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "dataBreakpointInfo" """

    name = args.get("name", "")
    vref = args.get("variablesReference", 0)

    addr = dap.runtime.vars.get_address(vref, name)
    if addr is not None:
        dap.send_response(
            {
                "dataId": hex(addr),
                "description": "Watchpoint on address " + hex(addr),
            }
        )
    else:
        dap.send_response(
            {
                "dataId": None,
                "description": "Watchpoints may only be set on memory",
            }
        )


@dapHandler("setDataBreakpoints")
def setDataBreakpoints(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "setDataBreakpoints" """

    result = []
    for bp in args.get("breakpoints", []):
        addr = int(bp.get("dataId"), 0)
        bpnum = dap.runtime.debugger.set_watchpoint(addr)
        result.append({"id": bpnum, "verified": True})

    dap.send_response({"breakpoints": result})


@dapHandler("continue")
def request_continue(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "continue". """
    dap.runtime.request_continue()
    dap.send_response({})


@dapHandler("next")
def request_next(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "next" (Step Over). """
    dap.runtime.next()
    dap.send_response()


@dapHandler("stepIn")
def stepIn(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "stepIn". """
    dap.runtime.step()
    dap.send_response()


@dapHandler("stepOut")
def stepOut(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "stepOut" (aka Step Return/Finish). """
    dap.runtime.stepOut()
    dap.send_response()


@dapHandler("pause")
def pause(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "pause". """
    dap.runtime.pause()
    dap.send_response()


@dapHandler("stackTrace")
def stackTrace(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "stackTrace". """
    frames_out = dap.runtime.stackTrace()
    dap.send_response(
        {"stackFrames": frames_out, "totalFrames": len(frames_out)}
    )


@dapHandler("scopes")
def scopes(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "scopes". """
    dap.send_response({"scopes": dap.runtime.vars.get_scopes()})


@dapHandler("variables")
def variables(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "variables". """
    vref = args.get("variablesReference", 0)
    dap.send_response({"variables": dap.runtime.vars.read_variables(vref)})


@dapHandler("setVariable")
def setVariable(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "setVariable". """
    ref = args["variablesReference"]
    name = args["name"]
    val = args["value"]
    try:
        response, invalidate = dap.runtime.vars.set_variable(ref, name, val)
        dap.send_response(response)
        if invalidate:
            dap.send_event("invalidated", {"areas": ["variables"]})
    except ValueError as e:
        dap.send_error(e.args[0])


@dapHandler("source")
def source(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "source". """
    dap.send_response({"content": ""})


@dapHandler("threads")
def threads(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "threads". """
    dap.send_response({"threads": [{"id": 1, "name": "thread1"}]})


@dapHandler("evaluate")
def evaluate(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "evaluate". """
    expr = args["expression"]
    context = args["context"]

    if context == "repl":
        evaluate_repl(dap, expr)
    elif context == "watch":
        evaluate_watch(dap, expr)
    else:
        dap.send_error("Unsupported evaluate request")


def evaluate_repl(dap: DAPConnection, expr: str) -> None:
    """ Evaluates text provided to the Debug Console """
    # This debug variable exists for use from `eval`
    debug = dap.runtime.debugger  # noqa: F841
    old_stdout = sys.stdout
    sys.stdout = mystdout = StringIO()
    ev_result = None
    try:
        ev_result = eval(expr)
    except:
        pass
    sys.stdout = old_stdout
    result = mystdout.getvalue()
    if ev_result is not None:
        result = result + str(ev_result)

    dap.send_response({"result": result, "variablesReference": 0})


def evaluate_watch(dap: DAPConnection, expr: str) -> None:
    """ Evaluates a watch expression """
    try:
        address = int(expr, 0)
    except ValueError:
        dap.send_error("Invalid watch expression: %s" % expr)
        return
    mem = dap.runtime.debugger.read_memory(address, 4)
    if len(mem) == 0:
        dap.send_error("Memory read error")
        return
    result = hex(mem[0])
    dap.send_response(
        {
            "result": result,
            "variablesReference": 0,
            "memoryReference": hex(address),
        }
    )


@dapHandler("readMemory")
def readMemory(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP request handler for "readMemory". """
    expr = args["memoryReference"]
    offset = args.get("offset", 0)
    count = args["count"]
    try:
        address = int(expr, 0) + offset
    except ValueError:
        dap.send_error("Invalid memory reference: " + expr)
        return
    try:
        mem = b""
        if count >= 1:
            mem = dap.runtime.debugger.read_memory(address, 1, count, True)
        mem_b64 = ""
        if type(mem) == bytes:
            mem_b64 = base64.b64encode(mem).decode()
        dap.send_response({"address": hex(address), "data": mem_b64})
    except:
        dap.send_error("Unable to read memory: " + hex(address))


# -------------------------------------------
# Custom requests
# -------------------------------------------


@dapHandler("variableFormat")
def variableFormat(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "variableFormat".

    This request can be used to change the formatting returned by the
    "variables" request for the specified variable.

    All variables are by default displayed as 4 byte integers in hex.
    When a variable is formatted as a pointer, the value is used as a memory
    address and the pointed-to value is displayed instead of that address.

    The "size" and "arrayLength" fields determine how much memory is read for
    this variable: from the pointed-to address if isPointer=true, or from the
    variable location itself otherwise. Both fields are ignored for non-pointer
    register variables.

    If both the format and reset arguments are omitted, the existing variable
    format is unchanged but still returned.

    DAP message arguments:
        variablesReference: int
            Identifies which variable group (from the variables request)
            the formatting should be applied to.
        reset: bool (default False)
            When true, resets all existing variable format changes for the
            specified variable, or for the variable group of name is omitted.
        name: str
            A register name, or a memory address.
        format: dict (optional)
            The desired formatting options for the specified variable. If
            omitted, the variable is unchanged.
            May include one or more of the following keys:

            isPointer: bool
            size: int
                Number of bytes per word; must be one of 1, 2, 4, or 8
            arrayLength: int
                Number of words in array; must be 1 or greater
            baseType: str
                Must be one of "hex, "signed", "unsigned", "char"
            codec: str
                Must be one of "UTF-8", "UTF-16LE", "UTF-16BE",
                    "UTF-32LE", "UTF-32BE"

    DAP response:
        variablesReference: int
            The variableReference that was provided as a request argument
        name: str
            The variable name that was provided as a request argument
        format: dict
            The current format details of the variable after any requested
            changes have been applied. Includes every modifiable field and
            several read only ones:

            section: str
                One of "reg", "stack", "global", "array".
            totalSize: int
                The number of bytes that this variable occupies in memory.
                Returns the size of the pointer (not the pointed-to data) for
                pointer types, otherwise is equal to the word size multiplied
                by the array length.
            isFixedSize: bool
                Returns true for register variables; denotes that the totalSize
                of this variable is fixed such that changing either the size or
                the array length automatically adjusts the other.
    """
    vref = args.get("variablesReference", Variables.VREF_REGISTERS)
    set_format = args.get("format")
    name = args.get("name")
    reset = args.get("reset", False)

    if reset is True:
        dap.runtime.vars.reset_format(vref, name)

    format_dict = None
    write = False
    if name is not None:
        write = set_format is not None
        format = dap.runtime.vars.get_format(vref, name)
        if set_format is not None:
            format.set(set_format)
        format_dict = format.to_dict()

    dap.send_response(
        {"variablesReference": vref, "name": name, "format": format_dict}
    )
    if write or reset:
        dap.send_event("invalidated", {"areas": ["variables"]})


@dapHandler("variableGlobals")
def variableGlobals(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "variableGlobals".

    This request can be used to add or remove items from the Globals section
    of the variables listing.

    DAP message arguments:
        add: list[str]
            List of addresses to add to the globals list.
        remove: list[str]
            List of addresses to remove from the globals list.
            Any associated formatting or watch points will also be cleared.

    DAP response: empty
    """
    dap.runtime.vars.remove_globals(args.get("remove", []))
    dap.runtime.vars.add_globals(args.get("add", []))
    dap.send_response()
    dap.send_event("invalidated", {"areas": ["variables"]})


@dapHandler("setLineOffsets")
def setLineOffsets(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "setLineOffsets".

    This request *must* be sent by the client at any point during the DAP
    initialization process. Until this request is received, the server will not
    send status events (like "stopped") to the client, and cannot verify
    breakpoints.

    This is necessary because line numbers must be known for those features to
    work, but the line numbers we use are those of the disassembly output that
    the client has.

    DAP message arguments:
        lineOffsetList: list[list[int]]
            A list of [line, offset] pairs that should represent every
            instruction in the target binary.
        linesStartAt1: bool
            When true, the line numbers provided in "lineOffsetList" start
            from 1 for the first line. When false or omitted, lines start at 0.

    DAP response: empty
    """
    in_list = args.get("lineOffsetList", [])
    startsAt1 = args.get("linesStartAt1", False)
    addend = dap.runtime.linesStartAt1 - startsAt1
    if addend != 0:
        in_list = [(l[0] + addend, l[1]) for l in in_list]

    # Respond to VSCode IMMEDIATELY, then do the (potentially slow) line
    # translator setup and queued-breakpoint resolution in a background thread.
    # This avoids blocking VSCode's debug session setup if avatar2/GDB calls
    # to install breakpoints take a long time (or hang) while QEMU is in an
    # unexpected state.
    dap.send_response()

    def _setup() -> None:
        try:
            dap.runtime.addLineTranslator(LineTranslator(in_list))
        except Exception as e:
            log.error("Failed to set line translator: %s", e)

    threading.Thread(target=_setup, daemon=True).start()


@dapHandler("listHalBreakpoints")
def listHalBreakpoints(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "listHalBreakpoints".

    Clients may send this request to obtain a list of HAL intercepts that are
    currently defined.

    DAP message arguments: none

    DAP response body:
        halBreakpoints: list[dict]
            A list with one element for each breakpoint.
            Fields in each element: id (int), address (int), runOnce (bool)
    """
    dap.send_response({"halBreakpoints": dap.runtime.getHalBreakpoints()})


@dapHandler("setHalBreakpoints")
def setHalBreakpoints(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "setHalBreakpoints".

    Clients may send this request to add new HAL breakpoints at runtime.

    DAP message arguments:
        halBreakpoints: list[dict]; each element contains the following items
            address: int
            className: str
            function: str
            runOnce: bool (default = False)
            classArgs: dict (default = {})
            registrationArgs: dict (default = {})

    DAP response: empty
    """
    for bp in args["halBreakpoints"]:
        addr = bp["address"]
        pyClass = bp["className"]
        func = bp["function"]
        once = bp.get("runOnce", False)
        clArgs = bp.get("classArgs", {})
        regArgs = bp.get("registrationArgs", {})
        dap.runtime.debugger.set_hal_breakpoint(
            addr, pyClass, func, once, clArgs, regArgs
        )
    dap.send_response()


@dapHandler("setBreakMode")
def setBreakMode(dap: DAPConnection, args: Dict[str, Any]) -> None:
    """ DAP custom request handler for "setBreakMode".

    Clients may send this request to toggle the behavior of the "continue"
    request or to check the current break mode.
    When breakOnHal is disabled (the default), the execution of the
    "continue" request will not stop when a HAL intercept is encountered.
    When breakOnHal is enabled, the target stops executing when a HAL
    intercept is encountered.
    In either case, "continue" will always stop execution when a debug
    breakpoint is reached if nothing causes execution to stop before then.

    This setting does not currently affect the behavior of the "next" and
    "stepOut" requests, but this may change in the future. Those requests
    always stop at HAL intercepts if any are encountered.

    DAP message arguments:
        breakOnHal: bool (optional)
            If omitted, the break mode remains unchanged.

    DAP response:
        breakOnHal: bool
    """
    breakOnHal = args.get("breakOnHal")
    if breakOnHal is not None:
        dap.runtime.breakOnHal = breakOnHal
    dap.send_response({"breakOnHal": dap.runtime.breakOnHal})
    dap.send_event("halBreakMode", {"breakOnHal": dap.runtime.breakOnHal})
