# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''
Uses config file format to control logging, first looks in local
directory for config file and uses it if set, else uses the
default on from halucinator

For file format see: https://docs.python.org/3/library/logging.config.html#logging-config-fileformat
'''
from __future__ import annotations

from typing import List, Tuple

import logging
import logging.config
import logging.handlers
import sys
from logging import Logger, StreamHandler
from logging.handlers import MemoryHandler
from os import path


LOG_CONFIG_NAME = 'logging.cfg'
DEFAULT_LOG_CONFIG = path.join(path.dirname(__file__),'logging.cfg')
HAL_LOGGER = "HAL_LOG"

def getHalLogger() -> Logger:
    return logging.getLogger(HAL_LOGGER)

def setLogConfig() -> None:
    hal_log = getHalLogger()
    if path.isfile(LOG_CONFIG_NAME):
        hal_log.info("USING LOGGING CONFIG From: %s" % LOG_CONFIG_NAME)
        logging.config.fileConfig(fname=LOG_CONFIG_NAME, disable_existing_loggers=True)
    else:  # Default logging
        hal_log.info("USING DEFAULT LOGGING CONFIG")
        hal_log.info("This behavior can be overwritten by defining %s"% LOG_CONFIG_NAME)
        logging.config.fileConfig(fname=DEFAULT_LOG_CONFIG, disable_existing_loggers=False)

def streamHalHandler() -> None:
    """
    Replaces stdout/stderr stream handlers on the HAL logger with
    MemoryHandlers, which buffer log messages and flush at ERROR level.
    Useful when running a debug shell to prevent log spam.
    """
    hal_log = getHalLogger()
    replacements = []
    for old in hal_log.handlers:
        if isinstance(old, StreamHandler) and old.stream in (
            sys.stdout,
            sys.stderr,
        ):
            new = MemoryHandler(
                capacity=100, flushLevel=logging.ERROR, target=old,
            )
            replacements.append((old, new))
    for old, new in replacements:
        hal_log.removeHandler(old)
        hal_log.addHandler(new)
