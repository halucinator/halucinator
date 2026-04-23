# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Defines the peripheral model decorators and the methods for
working with breakpoints
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from functools import wraps
import importlib
import logging
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .. import hal_log as hal_log_conf
from .. import hal_stats
from ..hal_config import HalInterceptConfig

log = logging.getLogger(__name__)

hal_log = hal_log_conf.getHalLogger()


hal_stats.stats["used_intercepts"] = set()
hal_stats.stats["bypassed_funcs"] = set()

# The following indicates whether a debugging session is active
debug_session = False

# This variable is used to resolve a race condition, specifically ensuring that
#  the debugger can identify the HAL breakpoint before the return gets executed
emulation_detected = False

# When True, intercept handler resumes execution after running (transparent
# HAL emulation, default for non-debugged runs and 'continue through HAL').
# When False (set by debugger step/next/etc.), the intercept handler signals
# emulation_complete=True and lets the debugger transition to stopped state.
pass_breakpoint = True

# Set by intercept handler when it finishes; debugger.monitor_emulating polls
# this flag to know when the HAL handler is done so it can fire HAL_BP callback.
emulation_complete = False

# LUT to map bp to addr
__bp_addr_lut = {}


@dataclass
class BPHandlerInfo:
    address: int
    bp_class: Any
    filename: str
    bp_handler: Any
    run_once: bool


def tx_map(per_model_funct: Callable[[Any], None]) -> Callable:
    """
    Decorator that maps this function to the peripheral model that supports
    it. It registers the intercept and calls the
    Usage:  @intercept_tx_map(<PeripheralModel.method>, ['Funct1', 'Funct2'])

    Args:
        per_model_funct(PeripheralModel.method):  Method of child class that
            this method is providing a mapping for
    """
    print("In: intercept_tx_map", per_model_funct)

    def intercept_decorator(func: Callable[..., Tuple[bool, int, Any]]) -> Callable:
        print("In: intercept_decorator", func)

        @wraps(func)
        def intercept_wrapper(self: Any, target: Any, bp_addr: int) -> Tuple[bool, int]:
            bypass, ret_value, msg = func(self, target, bp_addr)
            log.debug("Values: %s", msg)
            per_model_funct(*msg)
            return bypass, ret_value

        return intercept_wrapper

    return intercept_decorator


def rx_map(
    per_model_funct: Callable[[], Sequence[Any]]
) -> Callable[[Callable[..., Tuple[bool, int]]], Callable]:
    """
    Decorator that maps this function to the peripheral model that supports
    it. It registers the intercept and calls the
    Usage:  @intercept_rx_map(<PeripheralModel.method>, ['Funct1', 'Funct2'])

    Args:
        per_model_funct(PeripheralModel.method):  Method of child class that
            this method is providing a mapping for
    """
    print("In: intercept_rx_map", per_model_funct)

    def intercept_decorator(
        func: Callable[..., Tuple[bool, int]]
    ) -> Callable:
        print("In: intercept_decorator", func)

        @wraps(func)
        def intercept_wrapper(self: Any, target: Any, bp_addr: int) -> Tuple[bool, int]:
            models_inputs = per_model_funct()
            return func(self, target, bp_addr, *models_inputs)

        return intercept_wrapper

    return intercept_decorator


initalized_classes: Dict[Any, Any] = {}
bp2handler_lut: Dict[int, BPHandlerInfo] = {}
debugging_bps: Dict[int, int] = {}
watchpoint_bps: Dict[int, int] = {}
addr2bp_lut: Dict[int, int] = {}


def check_hal_bp(pc: int) -> bool:
    """Check if pc is at a HAL intercept address."""
    for info in bp2handler_lut.values():
        if info.address == pc:
            return True
    return False


def get_bp_handler(intercept: Any) -> Any:
    """
    gets the bp_handler class from the config file class name.
    Instantiates it if has not been instantiated before if
    has it just returns the instantiated instance

    :param intercept: HALInterceptConfig
    """
    split_str = intercept.cls.split(".")

    module_str = ".".join(split_str[:-1])
    class_str = split_str[-1]
    module = importlib.import_module(module_str)

    cls_obj = getattr(module, class_str)
    if cls_obj in initalized_classes:
        bp_class = initalized_classes[cls_obj]
    else:
        if intercept.class_args is not None:
            log.info("Class: %s", cls_obj)
            log.info("Class Args: %s", intercept.class_args)
            bp_class = cls_obj(**intercept.class_args)
        else:
            bp_class = cls_obj()
        initalized_classes[cls_obj] = bp_class
    return bp_class


def get_bp_handler_debug(cls: str, **class_args: Any) -> Any:
    """
    Gets or creates a bp_handler class from a class path string.
    Intended for use in debug/shell sessions.

    :param cls: Full class path string (e.g. 'halucinator.bp_handlers.generic.debug.IPythonShell')
    :param class_args: Keyword arguments to pass to the class constructor
    """
    split_str = cls.split(".")
    module_str = ".".join(split_str[:-1])
    class_str = split_str[-1]

    log.debug(
        "Finding handler %s in module %s with arguments %s",
        class_str,
        module_str,
        class_args,
    )

    module = importlib.import_module(module_str)
    cls_obj = getattr(module, class_str)

    if cls_obj in initalized_classes:
        return initalized_classes[cls_obj]

    bp_class = cls_obj(**class_args)
    initalized_classes[cls_obj] = bp_class
    return bp_class


def remove_bp_handler(qemu: Any, bp: int) -> bool:
    """
    Removes a breakpoint handler

    :param qemu:  Avatar qemu target
    :param bp:    The breakpoint number to remove
    :returns:     True on success, False otherwise
    """
    if bp not in bp2handler_lut:
        return False

    bp_info = bp2handler_lut[bp]
    if bp_info.address in addr2bp_lut:
        del addr2bp_lut[bp_info.address]
    if bp in __bp_addr_lut:
        del __bp_addr_lut[bp]
    del bp2handler_lut[bp]

    if bp in hal_stats.stats:
        hal_stats.stats[bp]["active"] = False
        hal_stats.stats[bp]["removed"] = True

    success = qemu.remove_breakpoint(bp)
    return success if success is not None else False


def register_bp_handler(qemu: Any, intercept: Any) -> Optional[int]:
    """
    Registers a BP handler for specific address

    :param qemu:    Avatar qemu target
    :param intercept: HALInterceptConfig
    """
    if intercept.bp_addr is None:
        log.debug("No address specified for %s ignoring intercept", intercept)
        return None
    bp_cls = get_bp_handler(intercept)

    try:
        if intercept.registration_args is not None:
            log.info(
                "Registering BP Handler: %s.%s : %s, registration_args: %s",
                intercept.cls,
                intercept.function,
                hex(intercept.bp_addr),
                str(intercept.registration_args),
            )
            handler = bp_cls.register_handler(
                qemu,
                intercept.bp_addr,
                intercept.function,
                **intercept.registration_args
            )
        else:
            log.info(
                "Registering BP Handler: %s.%s : %s",
                intercept.cls,
                intercept.function,
                hex(intercept.bp_addr),
            )
            handler = bp_cls.register_handler(
                qemu, intercept.bp_addr, intercept.function
            )
    except ValueError as error:
        hal_log.error("Invalid BP registration failed for %s", intercept)
        hal_log.error(error)
        hal_log.error("Input registration args are %s", intercept.registration_args)
        # exit(-1)
        sys.exit(-1)

    if intercept.run_once:
        bp_temp = True
        log.debug("Setting as Tempory")
    else:
        bp_temp = False

    if intercept.watchpoint:
        if intercept.watchpoint == "r":
            breakpoint_num = qemu.set_watchpoint(
                intercept.bp_addr, write=False, read=True
            )
        elif intercept.watchpoint == "w":
            breakpoint_num = qemu.set_watchpoint(
                intercept.bp_addr, write=True, read=False
            )

        else:
            breakpoint_num = qemu.set_watchpoint(
                intercept.bp_addr, write=True, read=True
            )

    else:
        breakpoint_num = qemu.set_breakpoint(intercept.bp_addr, temporary=bp_temp)

    # Check for duplicate address and remove old handler if present
    if addr2bp_lut.get(intercept.bp_addr) is not None:
        log.warning(
            "Multiple intercepts defined for address: %s", hex(intercept.bp_addr)
        )
        remove_bp_handler(qemu, addr2bp_lut[intercept.bp_addr])

    hal_stats.stats[breakpoint_num] = {
        "function": intercept.function,
        "desc": str(intercept),
        "count": 0,
        "method": handler.__name__,
        "active": True,
        "removed": False,
        "ran_once": False,
    }

    __bp_addr_lut[breakpoint_num] = intercept.bp_addr
    bp2handler_lut[breakpoint_num] = BPHandlerInfo(
        address=intercept.bp_addr,
        bp_class=bp_cls,
        filename=getattr(intercept, 'config_file', ''),
        bp_handler=handler,
        run_once=bp_temp,
    )
    addr2bp_lut[intercept.bp_addr] = breakpoint_num
    log.info("BP is %i", breakpoint_num)
    return breakpoint_num


def interceptor(avatar: Any, message: Any) -> None:  # pylint: disable=unused-argument
    """
    Callback for Avatar2 break point watchman.  It then dispatches to
    correct handler
    """
    if message.__class__.__name__ == "WatchpointHitMessage":
        breakpoint_num = int(message.watchpoint_number)
    else:
        breakpoint_num = int(message.breakpoint_number)
    target = message.origin
    pc = target.regs.pc & 0xFFFFFFFE  # Clear Thumb bit

    if breakpoint_num not in bp2handler_lut:
        # No HAL handler — this is a user-set debug breakpoint.
        # The Debugger's monitor_running will detect the stopped target,
        # match the PC against debugging_bps, and fire DEBUG_BP callback.
        log.debug("BP %i is a debug breakpoint (no HAL handler)", breakpoint_num)
        return

    bp_info = bp2handler_lut[breakpoint_num]

    if bp_info.run_once:
        hal_stats.stats[breakpoint_num]["active"] = False
        hal_stats.stats[breakpoint_num]["ran_once"] = True
        del bp2handler_lut[breakpoint_num]

    hal_stats.stats[breakpoint_num]["count"] += 1
    hal_stats.write_on_update(
        "used_intercepts", hal_stats.stats[breakpoint_num]["function"]
    )

    log.debug(
        "Breakpoint Number %s encountered at address %s. running method %s",
        breakpoint_num,
        pc,
        bp_info.bp_handler,
    )
    try:
        intercept_result, ret_value = bp_info.bp_handler(
            bp_info.bp_class, target, pc
        )

        if intercept_result:
            hal_stats.write_on_update(
                "bypassed_funcs", hal_stats.stats[breakpoint_num]["function"]
            )
    except Exception as err:
        log.exception("Error executing handler %s", repr(bp_info.bp_handler))
        raise err

    global emulation_detected, emulation_complete
    if intercept_result:
        if debug_session:
            while not emulation_detected:
                time.sleep(0.0001)
        target.execute_return(ret_value)
        emulation_detected = False

    # Always resume execution after a HAL intercept. The HAL intercept system
    # is designed to be transparent — handler runs, returns a value, execution
    # continues. This means stepping into a HAL function won't pause inside it;
    # the user will land after the call. That's a known limitation when
    # debugging halucinator-emulated firmware (HAL functions are stubbed out
    # at runtime, so there's nothing to step through).
    #
    # Set emulation_complete in case monitor_emulating caught a brief stopped
    # state — without this it loops forever.
    emulation_complete = True
    target.cont()
