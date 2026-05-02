# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, cast

from ..bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
import re
from binascii import hexlify
from os import path
import sys

import logging
from ... import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()

class ArgumentLogger(BPHandler):
    '''
        Logs function arguments to standard out or input file

        Halucinator configuration usage:
        - class: halucinator.bp_handlers.ArgumentLogger
          function: <func_name>
          symbol: <symbol> or
          addr: <addr> 
          registration_args:{num_args: <int>, log_ret_addr:true,
                             intercept:false, ret_value:null}
    '''
    def __init__(self, filename: Optional[str] = None):
        self.fd = None
        if filename != None:
            self.fd = open(filename, 'wt')
        self.loggers: Dict[int, ArgumentLogger.Logger] = {}

    def register_handler(self, target: "HalBackend", addr: int, func_name: str,
                         num_args: int = 0,
                         log_ret_addr: bool = True, intercept: bool = False,
                         ret_value: Optional[int] = None,
                         silent: bool = False) -> HandlerFunction:
        '''
            :param target       The QemuTarget
            :param addr         Address of the break point
            :param func_name    Function name being logged
            :param num_args     Number of arguments to log
            :param log_ret_addr Log the address this function will return to
            :param intercept    Intercept execution and return without executing function
            :param ret_value    Return value ignored if intercept != True
        '''
        ret_value_str = "%#x " % ret_value if ret_value is not None else "None"
        log.debug("Registration Args: Fun: %s, num_args %i, log_ret_addr %s intercept %s, ret_value %s, silent %s " % \
                                (func_name, num_args, log_ret_addr, intercept, ret_value_str, silent))
        self.loggers[addr] = ArgumentLogger.Logger(target,func_name, num_args, 
                                                  log_ret_addr, intercept, ret_value, silent)
        return cast(HandlerFunction, ArgumentLogger.log_handler)

    class Logger():
        def __init__(self, target: "HalBackend", func_name: str, num_args: int,
                     log_caller: bool, intercept: bool, ret_value: Optional[int], silent: bool):
            self.func_name = func_name
            self.num_args = num_args
            self.target = target
            self.log_caller= log_caller
            self.silent = silent
            self.ret_value = ret_value
            self.intercept = intercept


        def log(self) -> None:
            hal_log.info("###### Arg Logger ######")
            hal_log.info("Func: %s" % self.func_name)
            if self.num_args > 0:
                args = [hex(self.target.get_arg(i)) for i in range(self.num_args)]
                hal_log.info("Args: %s,".join(args))
            if self.log_caller:
                hal_log.info("Return addr: %#x" % self.target.get_ret_addr())

    @bp_handler  # bp_handler no args, can intercept any function
    def log_handler(self, target: "HalBackend", addr: int) -> HandlerReturn:
        logger = self.loggers[addr]
        if not logger.silent:
            logger.log()
        if logger.intercept:
            return True, logger.ret_value
        return False, None