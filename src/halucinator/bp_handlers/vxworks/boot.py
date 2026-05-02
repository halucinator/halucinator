# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''Boot class handler for all things related to bootline'''
import logging

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)

class Boot(BPHandler):
    '''Handles the vxworks boot string to struct and any other boot necessities'''
    def __init__(self):
        super().__init__()
        self.bootline = None

    def register_handler(self, qemu, addr, func_name, bootline=None):
        '''register the handler with halucinator, terminate boot string if needed'''
        if func_name == 'bootStringToStruct':
            if bootline is not None:
                self.bootline = bootline
                if self.bootline[-1] != '\x00':
                    self.bootline += '\x00'  #Null Terminate
            else:
                raise ValueError("bootline required on registration args for bootStringToStruct")
        return super().register_handler(qemu, addr, func_name)

    @bp_handler(['bootStringToStruct'])
    def usr_boot_string_to_struct(self, qemu, handler):
        '''the actual bp_handler for boot string. Write the reg_arg to memory'''
        log.debug("bootStringToStruct")
        log.debug("Setting boot string to: %s" % self.bootline)
        addr = qemu.get_arg(0)
        qemu.write_memory(
            addr,
            1,
            str.encode(self.bootline),
            len(self.bootline),
            raw=True)

        return False, None
