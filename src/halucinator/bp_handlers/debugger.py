from __future__ import annotations

import dataclasses
import logging
import threading
import time
from enum import Enum, auto
from queue import Empty, Queue
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
    overload,
)

import capstone
from avatar2 import Avatar, QemuTarget, TargetStates
from IPython.terminal.embed import InteractiveShellEmbed
from IPython.terminal.prompts import Prompts
from pygments.token import Token, _TokenType
from typing_extensions import Literal

import halucinator.bp_handlers.intercepts as intercepts
from halucinator import hal_log as hal_log_conf
from halucinator import hal_stats
from halucinator import hal_config
from halucinator.bp_handlers.intercepts import (
    get_bp_handler_debug,
    BPHandlerInfo,
)
from halucinator.peripheral_models import peripheral_server as periph_server

# GTIRB-based stack trace parser removed. If needed, alternatives are
# GDB backtrace or ELF symbol-based function boundary detection.
StackTraceParser = None  # type: ignore[misc,assignment]

# The monitor functions run an infinite loop in a separate thread witing for an
#  event, so time.sleep is repeatedly called to yield the control to a
#  different thread. The below constant is the value used.
THREAD_SLEEP = 0.00001

log = logging.getLogger(__name__)

hal_logger = hal_log_conf.getHalLogger()


class WrongStateError(Exception):
    pass


class RequestType(Enum):
    REQUEST = auto()
    ACTION = auto()
    STOP = auto()
    KILL = auto()


class DebugState(Enum):
    EMULATING = auto()
    STOPPED = auto()
    RUNNING = auto()
    EXITED = auto()


class CallbackState(Enum):
    STOP = auto()
    "Indicates the execution of a 'stop' operation while the target was running"

    HAL_STOP = auto()
    "Indicates the execution of a 'stop' operation while Halucinator was emulating"

    STEP = auto()
    "Indicates the completion of a 'step' operation."

    NEXT = auto()
    "Indicates the completion of a 'next' operation."

    FINISH = auto()
    "Indicates the completion of a 'finish' operation."

    # If everything is working, the callback functions should never be passed
    #  this state, since execution should only stop after a cont command if
    #  stop is called or a breakpoint is encountered
    CONT = auto()
    "Indicates the completion of a 'cont' or 'cont_through' operation."

    DEBUG_BP = auto()
    "Indicates a Debugging Breakpoint was encountered"

    HAL_BP = auto()
    "Indicates a Halucinator Breakpoint was encountered and execution stopped"

    EXIT = auto()
    "Indicates the Target Firmware execution has exited normally"

    _KILL = auto()
    "Private state used to indicate a shutdown request for the monitor"


@dataclasses.dataclass
class MemoryMatch:
    addr_start: int
    addr_end: int
    name: str
    supports_watch: bool


def check_hal_bp(pc: int) -> bool:
    return intercepts.check_hal_bp(pc)


def check_debug_bp(pc: int) -> bool:
    # Mask off the ARM Thumb mode bit — QEMU's GDB sometimes reports PC with
    # bit 0 set during Thumb execution, but breakpoints are stored without it.
    pc_masked = pc & 0xFFFFFFFE
    addrs = intercepts.debugging_bps.values()
    return pc_masked in addrs or pc in addrs


def shell_cb(state: CallbackState) -> None:
    hal_logger.info(state)


class DebuggerCallback(object):
    def __init__(self) -> None:
        self.callback: Dict[int, Callable[[CallbackState], None]] = {}
        self.callback_lock = threading.Lock()
        self.callback_count = 0
        self.callback_queue: Queue[CallbackState] = Queue()
        self.monitoring = False

    def _run_callbacks(self, cb_state: CallbackState) -> None:
        with self.callback_lock:
            callbacks = list(self.callback.values())
        for callback in callbacks:
            callback(cb_state)

    def _callback_monitor(self) -> None:
        while True:
            cb_state = self.callback_queue.get()
            if cb_state == CallbackState._KILL:
                return
            self._run_callbacks(cb_state)

    def start_monitoring(self) -> None:
        with self.callback_lock:
            if not self.monitoring:
                self.monitoring = True
                self.callback_thread = threading.Thread(
                    target=self._callback_monitor, args=()
                )
                self.callback_thread.start()

    def stop_monitoring(self) -> None:
        with self.callback_lock:
            if self.monitoring:
                self.callback_queue.put(CallbackState._KILL)
                self.callback_thread.join()
                self.monitoring = False

    def add_callback(self, cb: Callable[[CallbackState], None]) -> int:
        """
        Adds a callback function to the session, returning the index used to
        remove the callback.
        """
        with self.callback_lock:
            self.callback_count += 1
            self.callback[self.callback_count] = cb
            return self.callback_count

    def remove_callback(self, cb_num: int) -> bool:
        """
        Removes a callback function from the session by the callback index.
        """
        with self.callback_lock:
            if cb_num in self.callback:
                del self.callback[cb_num]
                return True
            return False

    def call_callbacks(self, cb: CallbackState) -> None:
        self.callback_queue.put(cb)


class Debugger(object):

    debug = None

    def __init__(
        self,
        target: Any,
        avatar: Any,
        stack_trace: Optional[Any] = None,
    ) -> None:
        """
        Parameters
        ----------
        target
            The emulator target. Historically an avatar2 QemuTarget; in the
            pluggable-backend world this is any HalBackend (Avatar2Backend,
            QEMUBackend, UnicornBackend, RenodeBackend, GhidraBackend). The
            debugger falls back to HalBackend-native methods when the target
            doesn't expose avatar2's protocols.memory / dictify / get_status.
        avatar
            The avatar2 Avatar instance, or — when driving a non-avatar2
            backend — a SimpleNamespace shim grafted onto the backend that
            exposes just `output_directory`, `stop()`, and `shutdown()`.
            Kept as `avatar` for backward compatibility with handlers that
            reach into `.config` / `.memory_ranges`; those paths still only
            work on true avatar2 setups.
        stack_trace
            Optional stack-trace parser used by the step-out / finish flow.
        """
        self.target = target
        self.avatar = avatar
        self.stack_trace = stack_trace

        self.request_queue: """Queue[
            Union[
                Tuple[
                    Literal[RequestType.REQUEST],
                    Callable[..., Any],
                    Dict[str, Any],
                    Queue[Any],
                ],
                Tuple[
                    Literal[RequestType.ACTION],
                    Callable[[], bool],
                    CallbackState,
                    Queue[Any],
                ],
                Tuple[Literal[RequestType.STOP], Queue[bool],],
                Tuple[Literal[RequestType.KILL]]
            ]
        ]""" = Queue()
        self.monitoring = False
        self.monitor_lock = threading.Lock()

        self.state = DebugState.STOPPED
        self.last_action: CallbackState = CallbackState.STOP

        self.callback = DebuggerCallback()

        # In future versions, we may want to use avatar.arch.capstone_arch and
        #  avatar.arch.capstone_mode to support more devices
        self.md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    def monitor_emulating(self) -> None:
        stopped = False
        while True:
            time.sleep(THREAD_SLEEP)
            try:
                req = self.request_queue.get_nowait()
            except Empty:
                pass
            else:
                if req[0] == RequestType.KILL:
                    self.monitoring = False
                    return
                elif req[0] == RequestType.STOP:
                    intercepts.pass_breakpoint = False
                    resps = req[1]
                    resps.put(True)
                    stopped = True
                else:
                    resp = req[-1]
                    resp.put((False, WrongStateError()))
            if intercepts.emulation_complete:
                intercepts.emulation_complete = False
                break
        if intercepts.pass_breakpoint:
            # The intercept watchman (bp_handlers.intercepts.interceptor)
            # already calls target.cont() at the end of every interception,
            # so the target is already running. Just track the state.
            self.state = DebugState.RUNNING
        else:
            self.state = DebugState.STOPPED
            if stopped:
                self._call_callbacks(CallbackState.HAL_STOP)
            else:
                self._call_callbacks(CallbackState.HAL_BP)

    def monitor_stopped(self) -> None:
        while True:
            req = self.request_queue.get()
            if req[0] == RequestType.KILL:
                self.monitoring = False
                return
            elif req[0] == RequestType.REQUEST:
                func, kwargs, resp = req[1:]
                try:
                    resp.put((True, func(**kwargs)))
                except Exception as e:
                    resp.put((False, e))
            elif req[0] == RequestType.ACTION:
                func, action, resp = req[1:]
                try:
                    val = func()
                except Exception as e:
                    resp.put((False, e))
                else:
                    resp.put((True, val))
                    if val:
                        self.last_action = action
                        self.state = DebugState.RUNNING
                        return
            elif req[0] == RequestType.STOP:
                resps = req[1]
                resps.put(False)

    def monitor_running(self) -> None:
        while True:
            time.sleep(THREAD_SLEEP)
            target_state = self.get_target_state()
            if (
                target_state == TargetStates.STOPPED
                or target_state == TargetStates.EXITED
            ):
                break
            elif target_state != TargetStates.RUNNING:
                log.error("Unexpected Target State: %s", target_state)
                while self.get_target_state() not in [
                    TargetStates.STOPPED,
                    TargetStates.RUNNING,
                    TargetStates.EXITED,
                ]:
                    time.sleep(THREAD_SLEEP)

            try:
                req = self.request_queue.get_nowait()
            except Empty:
                pass
            else:
                if req[0] == RequestType.KILL:
                    self.monitoring = False
                    return
                elif req[0] == RequestType.STOP:
                    try:
                        self.target.stop()
                    except:
                        # stop raises an exception when the target is not
                        #  stopped, likely indicating the debugger encountered
                        #  a breakpoint
                        self.request_queue.put(req)
                    else:
                        resps = req[1]
                        resps.put(True)
                        self.state = DebugState.STOPPED
                        self._call_callbacks(CallbackState.STOP)
                        return
                elif req[0] == RequestType.REQUEST:
                    # Service the request inline: pause the target, run
                    # the requested function, then resume. This supports
                    # DAP clients that send setBreakpoints / read memory
                    # / etc. while the firmware is running, without
                    # making them wait for the next stop.
                    func, kwargs, resp = req[1:]
                    try:
                        self.target.stop()
                    except Exception:  # noqa: BLE001
                        # Couldn't pause cleanly — re-queue for the next
                        # iteration once the target reaches STOPPED.
                        self.request_queue.put(req)
                    else:
                        try:
                            resp.put((True, func(**kwargs)))
                        except Exception as e:  # noqa: BLE001
                            resp.put((False, e))
                        try:
                            self.target.cont()
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    resp = req[-1]
                    resp.put((False, WrongStateError()))

        if target_state == TargetStates.EXITED:
            self.state = DebugState.EXITED
            self._call_callbacks(CallbackState.EXIT)
            return
        # Guard the PC read: between the STOPPED detection above and this
        # call, a HAL intercept watchman may have run the handler and
        # resumed the target (when debug_session is not set, or on a
        # non-HAL transient stop). In that case, treat it as still-running
        # and return to let the monitor loop re-enter monitor_running.
        try:
            pc = self._read_register("pc", False)
        except Exception as e:
            log.debug(
                "PC read after stop failed (target likely resumed): %s", e,
            )
            return
        if pc is None or (isinstance(pc, int) and pc < 0):
            # avatar2 sometimes returns None silently when the target state
            # changes mid-read. Treat as "still running" and let the monitor
            # loop re-enter monitor_running.
            log.debug("PC read returned %r; target likely resumed", pc)
            return
        if check_hal_bp(pc):
            self.state = DebugState.EMULATING
            intercepts.emulation_detected = True
        elif check_debug_bp(pc):
            self.state = DebugState.STOPPED
            hal_logger.info(
                "Debugging Breakpoint Encountered at Address %s", hex(pc),
            )
            self._call_callbacks(CallbackState.DEBUG_BP)
        else:
            self.state = DebugState.STOPPED
            self._call_callbacks(self.last_action)

    def monitor(self) -> None:
        """
        Manages the state machine for the debugging session by monitoring for
        state changes in the target and function call requests. This function
        loops infinitely and is meant to be called in its own thread.
        """
        self.callback.start_monitoring()
        while self.monitoring:

            if self.state == DebugState.EMULATING:
                self.monitor_emulating()

            elif self.state == DebugState.STOPPED:
                self.monitor_stopped()

            elif self.state == DebugState.RUNNING:
                self.monitor_running()

            else:
                break
        self.callback.stop_monitoring()

    def start_monitoring(self, add_shell_callback: bool = True) -> None:
        with self.monitor_lock:
            if not self.monitoring:
                if add_shell_callback:
                    self.add_callback(shell_cb)
                self.monitoring = True
                self.monitor_thread = threading.Thread(target=self.monitor)
                self.monitor_thread.start()

    def stop_monitoring(self) -> None:
        with self.monitor_lock:
            if self.monitoring:
                self.request_queue.put((RequestType.KILL,))
                self.monitor_thread.join()

    def shutdown(self) -> None:
        self.stop_monitoring()
        periph_server.stop()
        self.avatar.stop()
        self.avatar.shutdown()

    def add_callback(self, cb: Callable[[CallbackState], None]) -> int:
        """
        Adds a callback function to the session, returning the index used to
        remove the callback.
        """
        return self.callback.add_callback(cb)

    def remove_callback(self, cb_num: int) -> bool:
        """
        Removes a callback function from the session by the callback index.
        """
        return self.callback.remove_callback(cb_num)

    def _call_callbacks(self, cb: CallbackState) -> None:
        self.callback.call_callbacks(cb)

    def send_action(
        self, func: Callable[[], bool], action: CallbackState
    ) -> bool:
        resp: Queue[Any] = Queue()
        act: Literal[RequestType.ACTION] = RequestType.ACTION
        request = (act, func, action, resp)
        self.request_queue.put(request)
        succ, val = resp.get()
        if succ:
            return val
        else:
            raise val

    def send_request(
        self, func: Callable[..., Any], args: Dict[str, Any],
    ) -> Any:
        resp: Queue[Any] = Queue()
        act: Literal[RequestType.REQUEST] = RequestType.REQUEST
        request = (act, func, args, resp)
        self.request_queue.put(request)
        succ, val = resp.get()
        if succ:
            return val
        else:
            raise val

    @overload
    def read_register(self, reg: str, hex_mode: Literal[False]) -> int:
        ...

    @overload
    def read_register(self, reg: str, hex_mode: Literal[True]) -> str:
        ...

    @overload
    def read_register(self, reg: str, hex_mode: bool) -> Union[int, str]:
        ...

    @overload
    def read_register(self, reg: str) -> str:
        ...

    def read_register(
        self, reg: str, hex_mode: bool = True
    ) -> Union[int, str]:
        """
        Reads and returns the contents of the register reg. If hex_mode,
        the result of the function call will be returned as a string of
        the hexidecimal representation of the value, otherwise the result
        will be returned as an integer. If reg is not the name of a valid
        register, the function will log an error and return "" if hex_mode
        or -1 otherwise.
        """
        kwargs = {"reg": reg, "hex_mode": hex_mode}

        try:
            return self.send_request(self._read_register, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Register Read when Halucinator is not STOPPED"
            )

        if hex_mode:
            return ""
        else:
            return -1

    @overload
    def _read_register(self, reg: str, hex_mode: Literal[False]) -> int:
        ...

    @overload
    def _read_register(self, reg: str, hex_mode: Literal[True]) -> str:
        ...

    @overload
    def _read_register(self, reg: str, hex_mode: bool) -> Union[int, str]:
        ...

    def _read_register(
        self, reg: str, hex_mode: bool = True
    ) -> Union[int, str]:
        if reg in self.list_all_regs_names():
            val = self.target.read_register(reg)
            if hex_mode:
                return hex(val)
            else:
                return val
        else:
            log.error("Invalid Register Name in read_register: %s", reg)
            if hex_mode:
                return ""
            else:
                return -1

    def write_register(self, reg: str, val: int) -> bool:
        """
        Writes the value of val into the register reg modulo 2^32. Returns
        True on Success, False otherwise. If reg is not the name of a valid
        register, returns False and logs an error.
        """
        kwargs = {"reg": reg, "val": val}

        try:
            return self.send_request(self._write_register, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Register Write when Halucinator is not STOPPED"
            )

        return False

    def _write_register(self, reg: str, val: int) -> bool:
        if reg not in self.list_all_regs_names():
            log.error("Invalid register name in write_register: %s", reg)
            return False
        return self.target.write_register(reg, val)

    @staticmethod
    def check_memory_args(
        size: int, words: int, raw: bool, func_name: str
    ) -> bool:
        if words < 1:
            log.error(
                "Expected integer words >= 1 in %s, got %s", func_name, words
            )
            return False
        if size < 1:
            log.error(
                "Expected integer size >= 1 in %s, got %s", func_name, size
            )
            return False
        if not raw and size not in [1, 2, 4, 8]:
            log.error(
                "%s supports sizes in [1,2,4,8] when not raw, got %s",
                func_name,
                size,
            )
            return False
        return True

    @overload
    def read_memory(
        self, addrs: int, size: int, words: int, raw: Literal[True]
    ) -> bytes:
        ...

    @overload
    def read_memory(
        self,
        addrs: int,
        size: int,
        words: int = 1,
        raw: Literal[False] = False,
    ) -> List[int]:
        ...

    @overload
    def read_memory(
        self, addrs: int, size: int, words: int = 1, raw: bool = False
    ) -> Union[bytes, List[int]]:
        ...

    def read_memory(
        self, addrs: int, size: int, words: int = 1, raw: bool = False
    ) -> Union[bytes, List[int]]:
        """
        Returns the value stored in memory at the address addrs. size indicates
        the size of each word, and words marks the number of words. If raw then
        the returned value will be of type bytes, otherwise return will be a
        list of integers. Non-raw memory reads only support sizes of 1,2,4,8.
        """
        if not Debugger.check_memory_args(size, words, raw, "read_memory"):
            if raw:
                return b""
            else:
                return []

        kwargs = {"addrs": addrs, "size": size, "words": words, "raw": raw}

        try:
            return self.send_request(self._read_memory, kwargs)
        except WrongStateError:
            log.error("Attempted Memory Read when Halucinator is not STOPPED")

        if raw:
            return b""
        else:
            return []

    def _read_memory(
        self, addrs: int, size: int, words: int = 1, raw: bool = False
    ) -> Union[bytes, List[int]]:
        if not Debugger.check_memory_args(size, words, raw, "read_memory"):
            if raw:
                return b""
            else:
                return []
        # The function target.read_memory only returns an integer if num_words == 1
        #  and raw = False. Mypy does not allow overloading to encode a type of
        #  int not equal to 1, so target.read_memory must return a
        #  Union[int, List[int], bytes]. So, typing is turned off for the returns
        #  below.
        if words == 1 and raw == False:
            return [self.target.read_memory(addrs, size, num_words=1, raw=False)]  # type: ignore
        else:
            return self.target.read_memory(addrs, size, words, raw)  # type: ignore

    def write_memory(
        self,
        addrs: int,
        size: int,
        val: Union[int, bytes, List[int]],
        words: int = 1,
        raw: bool = False,
    ) -> bool:
        """
        Writes the value val into memory, starting at the address addrs.
        val can only be an int if words == 1 and raw == False.
        val can be a List[int] if words >= 1, words == len(val), and raw == False.
        val must be bytes if raw == True.
        If the above ar violated, False will be returned and an error logged.
        Non-raw memory reads only support sizes of 1,2,4,8.
        """
        if not Debugger.check_memory_args(size, words, raw, "write_memory"):
            return False
        if raw:
            if not isinstance(val, bytes):
                log.error("Expected byte values when writing memory raw")
                return False
        else:
            if words == 1:
                if isinstance(val, list) and len(val) == 1:
                    val = val[0]
                if not isinstance(val, int):
                    log.error(
                        "Expected integer or length one integer list value in write_memory"
                    )
                    return False
            else:
                if not isinstance(val, list) or len(val) != words:
                    log.error(
                        "Expected length %s integer list value in write_memory",
                        words,
                    )
                    return False

        kwargs = {
            "addrs": addrs,
            "size": size,
            "words": words,
            "val": val,
            "raw": raw,
        }

        try:
            return self.send_request(self._write_memory, kwargs)
        except WrongStateError:
            log.error("Attempted Memory Write when Halucinator is not STOPPED")

        return False

    def _write_memory(
        self,
        addrs: int,
        size: int,
        val: Union[int, bytes, List[int]],
        words: int = 1,
        raw: bool = False,
    ) -> bool:
        if not Debugger.check_memory_args(size, words, raw, "write_memory"):
            return False
        if raw:
            if not isinstance(val, bytes):
                log.error("Expected byte values when writing memory raw")
                return False
        else:
            if words == 1:
                if isinstance(val, list) and len(val) == 1:
                    val = val[0]
                if not isinstance(val, int):
                    log.error(
                        "Expected integer or length one integer list value in write_memory"
                    )
                    return False
            else:
                if not isinstance(val, list) or len(val) != words:
                    log.error(
                        "Expected length %s integer list value in write_memory",
                        words,
                    )
                    return False
        return self.target.write_memory(addrs, size, val, words, raw)

    def memory_info(self, addr: int) -> Optional[MemoryMatch]:
        """
        Checks whether the specified address belongs to a valid memory range
        in avatar, returning information about that range if it does.
        """
        mr_list = list(self.avatar.memory_ranges[addr])
        if len(mr_list) == 1:
            mr = mr_list[0]
            return MemoryMatch(addr, mr.end, mr.data.name, True)
        return None

    def step(self) -> bool:
        """
        Steps the emulated firmware one instruction, returning True on success.
        Returns False and logs an error if called while target state is not
        stopped.
        """
        try:
            val = self.send_action(self._step, CallbackState.STEP)
        except WrongStateError:
            log.error("Attempted Step when Debugger not stopped")
            return False
        else:
            if not val:
                log.error("Unable to execute step command")
            return val

    def _step(self) -> bool:
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted Step when target not stopped")
            return False
        intercepts.pass_breakpoint = False
        return self.target.step()

    def get_info(self) -> Dict[str, Any]:
        """
        Returns a dictionary of information about the target. Avatar2
        QemuTargets provide this via .dictify(); HalBackends fall back
        to a small synthetic dict.
        """
        if hasattr(self.target, "dictify"):
            return self.target.dictify()
        return {
            "name": getattr(self.target, "name", "halbackend"),
            "arch": getattr(self.target, "arch", "unknown"),
        }

    def get_target_state(self) -> "TargetStates":
        """
        Returns the state of the underyling target, ie running, stopped, etc...
        Avatar2 has .get_status(); HalBackends fall back to STOPPED when
        bp dispatch is idle, RUNNING otherwise.
        """
        if hasattr(self.target, "get_status"):
            return self.target.get_status()["state"]
        # HalBackend fallback — infer from our own DebugState
        return (TargetStates.STOPPED if self.state == DebugState.STOPPED
                else TargetStates.RUNNING)

    def get_state(self) -> DebugState:
        """
        Returns the state of the debugger, ie running, stopped, etc...
        """
        return self.state

    def cont(self) -> bool:
        """
        Runs the target firmware until the next breakpoint, whether a debugger
        or halucinator breakpoint, is encountered, returning True on success.
        Returns False and logs an error if called while target state is not
        stopped.
        """
        try:
            val = self.send_action(self._cont, CallbackState.CONT)
        except WrongStateError:
            log.error("Attempted Cont when Debugger not stopped")
            return False
        else:
            if not val:
                log.error("Unable to execute cont command")
            return val

    def _cont(self) -> bool:
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted Continue when target not stopped")
            return False
        intercepts.pass_breakpoint = False
        return self.target.cont()

    def cont_through(self) -> bool:
        """
        Runs the target until encountering the next debugging breakpoint is
        encountered, returning True on success. Returns False and logs an
        error if called while target state is not stopped.
        """
        try:
            val = self.send_action(self._cont_through, CallbackState.CONT)
        except WrongStateError:
            log.error("Attempted Cont when Debugger not stopped")
            return False
        else:
            if not val:
                log.error("Unable to execute cont_through command")
            return val

    def _cont_through(self) -> bool:
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted Continue Through when target not stopped")
            return False
        intercepts.pass_breakpoint = True
        return self.target.cont()

    def stop(self) -> bool:
        """
        Stop the target. Inspects the *target's* state via get_status():
        if the target is already STOPPED we still normalise debugger
        state to STOPPED and fire a STOP callback so any DAP client
        sees the "stopped" event. Otherwise we queue a stop request on
        the monitor thread and wait for it to ack.

        Always returns True. (The previous "log-and-return-False on
        already-stopped" path was removed because real DAP clients send
        spurious stop requests after a breakpoint already paused us.)
        """
        try:
            target_status = self.target.get_status()
        except Exception:  # noqa: BLE001 — not all backends implement this
            target_status = None
        if target_status == TargetStates.STOPPED \
                or self.state == DebugState.STOPPED:
            self.state = DebugState.STOPPED
            self._call_callbacks(CallbackState.STOP)
            return True

        resp: Queue = Queue()
        self.request_queue.put((RequestType.STOP, resp))
        resp.get()
        return True

    def set_watchpoint(self, addr: int) -> int:
        """
        Sets a debugging data watchpoint at the address addr, returning the
        breakpoint number.
        """
        kwargs = {"addr": addr}

        try:
            return self.send_request(self._set_watchpoint, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Watchpoint Set when Halucinator is not STOPPED"
            )
            return 0

    def _set_watchpoint(self, addr: int) -> int:
        bp = self.target.set_watchpoint(addr)
        intercepts.watchpoint_bps[bp] = addr
        return bp

    def set_debug_breakpoint(self, addr: int) -> int:
        """
        Sets a debugging breakpoint at the address addr, returning the
        breakpoint number.
        """
        kwargs = {"addr": addr}

        try:
            return self.send_request(self._set_debug_breakpoint, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Breakpoint Set when Halucinator is not STOPPED"
            )

        return False

    def _set_debug_breakpoint(self, addr: int) -> int:
        bp = self.target.set_breakpoint(addr)
        intercepts.debugging_bps[bp] = addr
        return bp

    def get_stack_trace(self) -> None:
        """
        Prints the stack trace (if available)
        """

        try:
            log.info(self.send_request(self._get_stack_trace, {}))
        except WrongStateError:
            log.error("Attempted Stack Trace when Halucinator is not STOPPED")

    def _get_stack_trace(self) -> str:
        if not self.stack_trace:
            return "Stack Trace unavailable"

        pc = self._read_register("pc", False)
        self.stack_trace.refresh(pc)

        trace_string = ""
        layer = 0
        for info in reversed(self.stack_trace.stack_record):
            trace_string += (
                f"\n#{layer}  {hex(info.address)} in {info.name} ()"
            )
            layer += 1
        return trace_string

    def remove_debug_breakpoint(self, bpnum: int) -> Optional[bool]:
        """
        Removes the debugging breakpoint or data watchpoint by the
        breakpoint's number, returning true on success. If not a valid
        debugging breakpoint number, False is returned.
        """
        kwargs = {"bpnum": bpnum}

        try:
            return self.send_request(self._remove_debug_breakpoint, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Breakpoint Remove when Halucinator is not STOPPED"
            )

        return False

    def _remove_debug_breakpoint(self, bpnum: int) -> Optional[bool]:
        if bpnum in intercepts.debugging_bps:
            del intercepts.debugging_bps[bpnum]
            return self.target.remove_breakpoint(bpnum)
        elif bpnum in intercepts.watchpoint_bps:
            del intercepts.watchpoint_bps[bpnum]
            return self.target.remove_breakpoint(bpnum)
        else:
            return False

    def set_hal_breakpoint(
        self,
        bp_addr: int,
        cls_str: str,
        func_name: str,
        run_once: bool = False,
        class_args: Dict[str, Any] = {},
        registration_args: Dict[str, Any] = {},
    ) -> int:
        """
        Sets a Halucinator Breakpoint at the address bp_addr. cls_str must
        contain a valid import path to a Python class which inherits from
        BPHandler. func_name must contain the name of a bp_handler function
        defined in the above class. If run_once, then the breakpoint will be
        deconstructed after being first encountered, otherwise the emulated
        functionality will run every time the breakpoint is encountered. The
        breakpoint number of the breakpoint is returned.
        """
        kwargs = {
            "bp_addr": bp_addr,
            "cls_str": cls_str,
            "func_name": func_name,
            "run_once": run_once,
            "class_args": class_args,
            "registration_args": registration_args,
        }

        try:
            return self.send_request(self._set_hal_breakpoint, kwargs)
        except WrongStateError:
            log.error(
                "Attempted HAL Breakpoint Set when Halucinator is not STOPPED"
            )

        return False

    def _set_hal_breakpoint(
        self,
        bp_addr: int,
        cls_str: str,
        func_name: str,
        run_once: bool = False,
        class_args: Dict[str, Any] = {},
        registration_args: Dict[str, Any] = {},
    ) -> int:
        bp_cls = get_bp_handler_debug(cls_str, **class_args)
        handler = bp_cls.register_handler(
            self.target, bp_addr, func_name, **registration_args
        )
        bp = self.target.set_breakpoint(bp_addr, temporary=run_once)
        intercepts.bp2handler_lut[bp] = BPHandlerInfo(
            address=bp_addr,
            bp_class=bp_cls,
            filename="",
            bp_handler=handler,
            run_once=run_once,
        )
        hal_stats.stats[bp] = {
            "function": func_name,
            "desc": "",
            "count": 0,
            "method": handler.__name__,
            "active": True,
            "removed": False,
            "ran_once": False,
        }
        return bp

    def remove_hal_breakpoint(self, bpnum: int) -> Optional[bool]:
        """
        Removes Halucinator breakpoints by the breakpoint number, returning
        True on success. If bpnum is not a Halucinator breakpoint number,
        False is returned.
        """
        kwargs = {"bpnum": bpnum}

        try:
            return self.send_request(self._remove_hal_breakpoint, kwargs)
        except WrongStateError:
            log.error(
                "Attempted HAL Breakpoint Remove when Halucinator is not STOPPED"
            )

        return False

    def _remove_hal_breakpoint(self, bpnum: int) -> Optional[bool]:
        return intercepts.remove_bp_handler(self.target, bpnum)

    def reload_hal_config(
        self, yaml_filename: hal_config.Openable
    ) -> Optional[bool]:
        """
        Reloads a HALucinator configuration file and updates intercepts.
        """
        kwargs = {"yaml_filename": yaml_filename}

        try:
            return self.send_request(self._reload_hal_config, kwargs)
        except WrongStateError:
            log.error(
                "Attempted HAL Config Reload when Halucinator is not STOPPED"
            )

        return False

    def _reload_hal_config(
        self, yaml_filename: hal_config.Openable
    ) -> Optional[bool]:
        if yaml_filename == "":
            return False

        # Remove existing breakpoints set by this file
        bkpts = list(intercepts.bp2handler_lut.keys())
        for bpnum in bkpts:
            bpentry = intercepts.bp2handler_lut[bpnum]
            if bpentry.filename != yaml_filename:
                continue
            if not intercepts.remove_bp_handler(self.target, bpnum):
                log.error(f"Failed to remove breakpoint: {bpnum}")
                return False
            log.info(f"Removed breakpoint {bpnum} : {bpentry}")

        # Load new breakpoints from the yaml file
        config = self.avatar.config  # type: ignore
        if not config.reload_yaml_intercepts(yaml_filename):
            return False

        for intercept in config.intercepts:
            if intercept.config_file != yaml_filename:
                continue
            if intercept.bp_addr is not None:
                intercepts.register_bp_handler(self.target, intercept)

        return True

    def list_debug_breakpoints(self) -> Dict[int, int]:
        """
        Returns a dictionary of debugging breakpoints, where the key is the
        breakpoint number and the value is the address of the breakpoint.
        """
        return intercepts.debugging_bps

    def list_watchpoints(self) -> Dict[int, int]:
        """
        Returns a dictionary of debugging data watchpoints, where the key is
        the breakpoint number and the value is the watched data address.
        """
        return intercepts.watchpoint_bps

    def list_hal_breakpoints(self) -> Dict[int, BPHandlerInfo]:
        """
        Returns a dictionary of the Halucinator breakpoints.
        The key of each entry is the breakpoint number.
        The value of entry is a BPHandlerInfo instance consisting of:
        - The Breakpoint Address
        - The Breakpoint Handler Class
        - The Breakpoint origin filename (if applicable)
        - The Breakpoint Handler
        - Run Once (meaning whether the breakpoint is caught more than once)
        """
        return intercepts.bp2handler_lut

    def list_all_regs_names(self) -> List[str]:
        """
        Returns a list of the names of the target's registers. Works on
        both avatar2 QemuTargets (via protocols.memory.get_register_names)
        and HalBackend instances (via list_registers).
        """
        if hasattr(self.target, "list_registers"):
            return [r for r in self.target.list_registers()
                    if r and "_" not in r]
        if hasattr(self.target, "protocols"):
            try:
                return [
                    r for r in self.target.protocols.memory.get_register_names()
                    if r and "_" not in r
                ]
            except AttributeError:
                pass
        return []

    @overload
    def list_all_regs_values(self, hex_mode: Literal[False]) -> Dict[str, int]:
        ...

    @overload
    def list_all_regs_values(self, hex_mode: Literal[True]) -> Dict[str, str]:
        ...

    @overload
    def list_all_regs_values(self) -> Dict[str, str]:
        ...

    def list_all_regs_values(
        self, hex_mode: bool = True
    ) -> Mapping[str, Union[int, str]]:
        """
        Returns a dictionary with the keys as the register names
        and the values as the register values.
        """
        kwargs = {"hex_mode": hex_mode}

        try:
            return self.send_request(self._list_all_regs_values, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Register Read when Halucinator is not STOPPED"
            )

        return {}

    @overload
    def _list_all_regs_values(
        self, hex_mode: Literal[False]
    ) -> Dict[str, int]:
        ...

    @overload
    def _list_all_regs_values(self, hex_mode: Literal[True]) -> Dict[str, str]:
        ...

    @overload
    def _list_all_regs_values(self) -> Dict[str, str]:
        ...

    def _list_all_regs_values(
        self, hex_mode: bool = True
    ) -> Mapping[str, Union[int, str]]:
        regs = self.list_all_regs_names()
        res = {}
        for r in regs:
            v = self._read_register(r, hex_mode)
            res[r] = v
        return res

    def current_instr(self, hex_mode: bool = True) -> List[Union[str, int]]:
        """
        Returns the current instruction as a list of the form
        [addresss, mnemonic, op string]. If hex_mode is True, then the returned
        address is represented in base 10. If hex_mode is False, then the
        returned address will be in hexadecimal. If called when the target is
        not stopped, an empty list is returned and an error logged.
        """
        kwargs = {"hex_mode": hex_mode}

        try:
            return self.send_request(self._current_instr, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Instruction Read when Halucinator is not STOPPED"
            )

        return []

    def _current_instr(self, hex_mode: bool = True) -> List[Union[str, int]]:
        if self.get_target_state() != TargetStates.STOPPED:
            log.error(
                "Attempted to Read Current Instruction when target not stopped"
            )
            return []
        pc = self._read_register("pc", False)
        code = bytes(self._read_memory(pc, 4, raw=True))

        for i in self.md.disasm(code, pc):
            if hex_mode:
                return [hex(i.address), i.mnemonic, i.op_str]
            else:
                return [i.address, i.mnemonic, i.op_str]
        return []

    def read_instructions(
        self, addr: int, num_instr: int = 1, hex_mode: bool = True
    ) -> List[List[Union[str, int]]]:
        """
        Read one or more instructions, starting at the address addr, returning
        a list of instructions, each of the form [addresss, mnemonic, op string].
        Memory is read at the address and is interpreted as an instruction
        regardless of whether that range of memory is binary instructions.
        num_instr is the number of instructions that will be read.If called
        when the target is not stopped, an empty list is returned and an
        error logged.
        """
        kwargs = {"addr": addr, "num_instr": num_instr, "hex_mode": hex_mode}

        try:
            return self.send_request(self._read_instructions, kwargs)
        except WrongStateError:
            log.error(
                "Attempted Instruction Read when Halucinator is not STOPPED"
            )

        return []

    def _read_instructions(
        self, addr: int, num_instr: int = 1, hex_mode: bool = True
    ) -> List[List[Union[str, int]]]:
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted to Read Instructions when target not stopped")
            return []
        code = bytes(self._read_memory(addr, 4 * num_instr, raw=True))
        instrs: List[List[Union[str, int]]] = []

        for i in self.md.disasm(code, addr, num_instr):
            if hex_mode:
                instrs += [[hex(i.address), i.mnemonic, i.op_str]]
            else:
                instrs += [[i.address, i.mnemonic, i.op_str]]

        return instrs

    def finish(self) -> bool:
        """
        Executes the gdb command "finish", and returns True only if it the
        command is run successfully. False is returned and an error logged if
        the target is not stopped when the function is called.
        """
        try:
            val = self.send_action(self._finish, CallbackState.FINISH)
        except WrongStateError:
            log.error("Attempted Finish when Debugger not stopped")
            return False
        else:
            if not val:
                log.error("Unable to execute finish command")
            return val

    def _finish(self) -> bool:
        intercepts.pass_breakpoint = False
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted Finish when target not stopped")
            return False
        # Avatar2's QemuTarget exposes gdb "finish" via the protocols
        # dispatcher. HalBackends emulate it: set a one-shot breakpoint
        # at LR and continue.
        if (hasattr(self.target, "protocols")
                and getattr(self.target.protocols, "memory", None) is not None):
            res = self.target.protocols.memory._sync_request(["finish"], "running")
            if not res[0]:
                hal_logger.info(
                    "%s recieved when running finish: %s",
                    res[1]["message"],
                    res[1]["payload"]["msg"],
                )
            return res[0]
        try:
            ret_addr = self.target.read_register("lr") & ~1
            bp = self.target.set_breakpoint(ret_addr, temporary=True)
            self.target.cont()
            return True
        except Exception:
            log.exception("finish fallback failed")
            return False

    def next(self) -> bool:
        """
        Executes the gdb command "nexti", and returns True only if it the
        command is run successfully. False is returned and an error logged if
        the target is not stopped when the function is called.
        """
        try:
            val = self.send_action(self._next, CallbackState.NEXT)
        except WrongStateError:
            log.error("Attempted Next when Debugger not stopped")
            return False
        else:
            if not val:
                log.error("Unable to execute next command")
            return val

    def _next(self) -> bool:
        intercepts.pass_breakpoint = False
        if self.get_target_state() != TargetStates.STOPPED:
            log.error("Attempted Next when target not stopped")
            return False
        # Avatar2 QemuTargets use gdb's "nexti" via protocols.memory;
        # HalBackends fall back to stepi (same thing for a non-call
        # instruction; we'd need disassembly to distinguish call vs
        # non-call for a proper nexti — step is a reasonable default).
        if (hasattr(self.target, "protocols")
                and getattr(self.target.protocols, "memory", None) is not None):
            res = self.target.protocols.memory._sync_request(["nexti"], "running")
            if not res[0]:
                hal_logger.info(
                    "%s recieved when running finish: %s",
                    res[1]["message"],
                    res[1]["payload"]["msg"],
                )
            return res[0]
        try:
            self.target.step()
            return True
        except Exception:
            log.exception("next fallback failed")
            return False


class HalPrompt(Prompts):
    def __init__(self, ip: InteractiveShellEmbed, debug: Debugger) -> None:
        self.debug = debug
        self.cur_prompt_count = 0
        super().__init__(ip)

    def in_prompt_tokens(self) -> List[Tuple[_TokenType, str]]:
        h = hal_log_conf.getHalLogger()
        h.handlers[0].flush()
        self.cur_prompt_count += 1
        if self.debug.monitoring:
            status = self.debug.get_state()
            if status == DebugState.STOPPED:
                pc = self.debug.read_register("pc", True)
            else:
                pc = "????"
            prompt = "Halucinator State: " + status.name + ", pc: "

            return [
                (Token.Prompt, prompt),
                (Token.PromptNum, pc),
                (Token.Prompt, ", ["),
                (Token.PromptNum, str(self.cur_prompt_count)),
                (Token.Prompt, "]> "),
            ]

        else:
            return [
                (Token.Prompt, "Halucinator Stopped"),
                (Token.Prompt, ", ["),
                (Token.PromptNum, str(self.cur_prompt_count)),
                (Token.Prompt, "]> "),
            ]

    def out_prompt_tokens(self) -> List[Tuple[_TokenType, str]]:
        res = [
            (Token.OutPrompt, "\n["),
            (Token.OutPromptNum, str(self.cur_prompt_count)),
            (Token.OutPrompt, "]> "),
        ]
        return res
