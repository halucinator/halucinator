# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''vxlogging'''
import logging
import re

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)

PRINTF_FORMAT_STR='''\
        (                                  # start of capture group 1
        %                                  # literal "%"
        (?:                                # first option
        (?:[-+0 #]{0,5})                   # optional flags
        (?:\d+|\*)?                        # width
        (?:\.(?:\d+|\*))?                  # precision
        (?:h|l|ll|w|I|I32|I64)?            # size
        [cCdiouxXeEfgGaAnpsSZ]             # type
        ) |                                # OR
        %%)                                # literal "%%"
'''

class VxLogging(BPHandler):
    '''vxlogging'''
    def __init__(self):
        super().__init__()
        self.log_msg_ptr = None

    def parse_printf_string(self, pf_str):
        '''parse_printf_string'''
        return tuple((m.start(1), m.group(1)) \
            for m in re.finditer(PRINTF_FORMAT_STR, pf_str, flags=re.X))

    def read_arg(self, idx, arg_type):
        '''read_arg'''
        if arg_type == '%%s':
            pass
        elif arg_type == '%%i':
            pass
        elif arg_type == '%%d':
            pass
        elif arg_type == '%%x':
            pass
        elif arg_type == '%%lx':
            pass
        elif arg_type == '%%ld':
            pass
        elif arg_type == '%%lu':
            pass
        elif arg_type == '%%p':
            pass
        elif arg_type == '%%u':
            pass
        elif arg_type == '%%f':
            pass
        else:
            raise TypeError('Unsupported format string type: %s' % arg_type)

    @bp_handler(['_applog'])
    def app_log(self, qemu, addr):
        '''app_log'''
        #TODO:  Figure out var args passing
        # prototype is (uint, char*, uint, char*, ...)
        app_log_id = qemu.get_arg(0)  # A hard coded number, perhaps a counter
        logger_addr = qemu.get_arg(1) # String identifier
        flags = qemu.get_arg(2)
        fmt_str_addr = qemu.get_arg(3) # Format string

        logger_str = qemu.read_string(logger_addr)
        fmt_str = qemu.read_string(fmt_str_addr)

        log.info("AppLog: %#x, %s, %x, %s"%(app_log_id,logger_str, flags, fmt_str))

    @bp_handler(['logMsg'])
    def log_msg(self, qemu, addr):
        '''log_msg'''
        #TODO Implement this
        self.log_msg_ptr = qemu.get_arg(0)
        self.log_msg = qemu.read_string(self.log_msg_ptr)
        log.debug("LogMsg: %s" % self.log_msg)
        return False, None
