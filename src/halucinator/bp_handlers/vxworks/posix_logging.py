# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''Posix Logging'''
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)

class PosixLogging(BPHandler):
    '''
        These handlers log the POSIX api functions like
        read, write, open, close.

        This SHOULD NOT do anything that modifies the
        program state. If you want to modify something
        make a posix.py file
    '''
    #######################################
    #              POSIX API              #
    #######################################
    @bp_handler(['creat'])
    def creat(self, qemu: "HalBackend", handler: int) -> Tuple[bool, None]:
        '''creat'''
        log.debug("creat")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        log.debug("\tFlags:    %d" % qemu.get_arg(1))
        return False, None

    @bp_handler(['open'])
    def open(self, qemu: "HalBackend", handler: int) -> Tuple[bool, None]:
        '''open'''
        log.debug("open")
        name = qemu.read_string(qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        log.debug("\tFlags:    0x%04x" % qemu.get_arg(1))
        log.debug("\tMode:     %d" % qemu.get_arg(2))
        return False, None

    @bp_handler(['mkdir'])
    def mkdir(self, qemu: "HalBackend", handler: int) -> Tuple[bool, None]:
        '''mkdir'''
        log.debug("mkdir")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        return False, None

    @bp_handler(['xdelete'])
    def x_delete(self, qemu: "HalBackend", handler: int) -> Tuple[bool, None]:
        '''x_delete'''
        log.debug("x_delete")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        return False, None