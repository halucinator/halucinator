# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''Posix Logging'''
import logging

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

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
    def creat(self, qemu, handler):
        '''creat'''
        log.debug("creat")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        log.debug("\tFlags:    %d" % qemu.get_arg(1))
        return False, None

    @bp_handler(['open'])
    def open(self, qemu, handler):
        '''open'''
        log.debug("open")
        name = qemu.read_string(qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        log.debug("\tFlags:    0x%04x" % qemu.get_arg(1))
        log.debug("\tMode:     %d" % qemu.get_arg(2))
        return False, None

    @bp_handler(['mkdir'])
    def mkdir(self, qemu, handler):
        '''mkdir'''
        log.debug("mkdir")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        return False, None

    @bp_handler(['xdelete'])
    def x_delete(self, qemu, handler):
        '''x_delete'''
        log.debug("x_delete")
        name = qemu.read_string( qemu.get_arg(0))
        log.debug("\tName:     %s" % name)
        return False, None
