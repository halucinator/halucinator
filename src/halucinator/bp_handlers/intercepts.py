# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Defines the peripheral model decorators and the methods for
working with breakpoints
"""
from __future__ import annotations

import sys
from functools import wraps
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple
import importlib
import logging
from .. import hal_log as hal_log_conf
from .. import hal_stats
from ..hal_config import HalInterceptConfig  # re-exported for convenience

log = logging.getLogger(__name__)

hal_log = hal_log_conf.getHalLogger()


hal_stats.stats["used_intercepts"] = set()
hal_stats.stats["bypassed_funcs"] = set()

# LUT to map bp to addr
__bp_addr_lut: Dict[int, int] = {}


def tx_map(per_model_funct: Callable[..., None]) -> Callable:
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


@dataclass
class BPHandlerInfo:
    """Structured record stored in bp2handler_lut for each registered breakpoint."""
    addr: int
    cls: Any          # handler class instance
    desc: str
    handler: Callable
    run_once: bool = False

    def __init__(self, addr: Optional[int] = None, cls: Any = None, desc: str = "",
                 handler: Optional[Callable] = None, run_once: bool = False,
                 # Alternative keyword names used by debugger.py
                 address: Optional[int] = None, bp_class: Any = None,
                 filename: Optional[str] = None, bp_handler: Optional[Callable] = None) -> None:
        self.addr = addr if addr is not None else address
        self.cls = cls if cls is not None else bp_class
        self.desc = desc if filename is None else filename
        self.handler = handler if handler is not None else bp_handler
        self.run_once = run_once

    @property
    def bp_class(self) -> Any:
        """Alias for cls."""
        return self.cls

    @property
    def address(self) -> Optional[int]:
        """Alias for addr."""
        return self.addr

    @property
    def filename(self) -> str:
        """Alias for desc (stores the config filename when set via filename= kwarg)."""
        return self.desc


def get_bp_handler_debug(cls_str: str, **class_args: Any) -> Any:
    """
    Instantiate a bp_handler class by dotted string name, forwarding class_args.
    Used by the debugger to create handlers dynamically.
    """
    split_str = cls_str.split(".")
    module_str = ".".join(split_str[:-1])
    class_str = split_str[-1]
    module = importlib.import_module(module_str)
    cls_obj = getattr(module, class_str)
    return cls_obj(**class_args) if class_args else cls_obj()


initalized_classes: Dict[Any, Any] = {}
bp2handler_lut: Dict[int, BPHandlerInfo] = {}
addr2bp_lut: Dict[int, int] = {}      # addr → bp_id (reverse of __bp_addr_lut, public)
debugging_bps: Dict[int, int] = {}    # addr → bp_id for debugger-added breakpoints
watchpoint_bps: Dict[int, int] = {}   # bp_id → addr for debugger-added watchpoints


def check_hal_bp(pc: int) -> bool:
    """Return True if pc is the address of any registered halucinator breakpoint."""
    for entry in bp2handler_lut.values():
        addr = entry.addr if isinstance(entry, BPHandlerInfo) else (entry[0] if entry else None)
        if addr == pc:
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
    addr2bp_lut[intercept.bp_addr] = breakpoint_num
    bp2handler_lut[breakpoint_num] = BPHandlerInfo(
        addr=intercept.bp_addr,
        cls=bp_cls,
        desc=intercept.config_file if intercept.config_file is not None else str(intercept),
        handler=handler,
        run_once=bp_temp,
    )
    log.info("BP is %i", breakpoint_num)
    return breakpoint_num


def interceptor(avatar: Any, message: Any) -> None:  # pylint: disable=unused-argument
    """
    Callback for Avatar2 break point watchman.  It then dispatches to
    correct handler
    """
    # HERE
    if message.__class__.__name__ == "WatchpointHitMessage":
        breakpoint_num = int(message.watchpoint_number)
    else:
        breakpoint_num = int(message.breakpoint_number)
    target = message.origin

    prog_counter = target.regs.pc & 0xFFFFFFFE  # Clear Thumb bit; this is passed as bp_addr to handlers

    try:
        bp_info = bp2handler_lut[breakpoint_num]
        cls, method = bp_info.cls, bp_info.handler
        hal_stats.stats[breakpoint_num]["count"] += 1
        hal_stats.write_on_update(
            "used_intercepts", hal_stats.stats[breakpoint_num]["function"]
        )
    except KeyError:
        log.info("BP Has no handler")
        return
    # print method
    try:
        intercept, ret_value = method(cls, target, prog_counter)

        if intercept:
            hal_stats.write_on_update(
                "bypassed_funcs", hal_stats.stats[breakpoint_num]["function"]
            )
    except Exception as err:
        log.exception("Error executing handler %s", repr(method))
        raise err
    if intercept:
        target.execute_return(ret_value)
    target.cont()


def remove_bp_handler(target: Any, bp_id: int) -> bool:
    """
    Remove a registered breakpoint handler by bp_id.
    Returns True if removed, False if bp_id was not found.
    """
    if bp_id not in bp2handler_lut:
        return False
    entry = bp2handler_lut.pop(bp_id)
    # Remove from private lut (if registered via register_bp_handler)
    __bp_addr_lut.pop(bp_id, None)
    # Remove from public addr2bp_lut using the entry's address
    addr = entry.addr if isinstance(entry, BPHandlerInfo) else None
    if addr is not None:
        addr2bp_lut.pop(addr, None)
    # Update stats to mark as removed rather than deleting the entry
    if bp_id in hal_stats.stats:
        hal_stats.stats[bp_id]["active"] = False
        hal_stats.stats[bp_id]["removed"] = True
    target.remove_breakpoint(bp_id)
    return True
