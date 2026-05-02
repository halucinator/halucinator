# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''VxWorks interrupt handler to extend HALucinator interrupts'''
import logging

from halucinator import hal_log
from halucinator.bp_handlers import BPHandler, bp_handler
from halucinator.peripheral_models.interrupts import Interrupts as InterruptModel

log = logging.getLogger(__name__)

# Using this for critical messages as by default will go to std out
halucinator_log = hal_log.getHalLogger()

class Interrupts(BPHandler):
    """
    registration_args: irq_map{'isr_name':vxirq_number}
    """
    prev = ''
    def __init__(self, model=InterruptModel, mask=None,
                int_connect_log='tmp/intConnect.log', level=2):
        BPHandler.__init__(self)
        self.model = model  # This where the bp_handler gets a reference to
                            # the interrupts model
        self.int_connect_log = int_connect_log
        self.level = level
        with open(self.int_connect_log, 'w') as file: # Just want to clear file
            pass

    @bp_handler(['IntLvlVecChk'])
    def int_lvl_vec_chk(self, qemu, bp_addr):
        '''
            This is called by IntEnter
        '''
        #TODO: ADAPT MODEL
        log.debug("int_lvl_vec_chk")

        irq_num = self.model.get_first_irq()

        if irq_num is not None:
            qemu.write_memory(qemu.get_arg(0), 4, self.level)
            qemu.write_memory(qemu.get_arg(1), 4, irq_num)
            self.model.clear_active_bp(irq_num)
        else:
            #Shouldn't happen
            pass
        return True, 0

    @bp_handler(['intConnect'])
    def int_connect(self, qemu, bp_addr):
        '''connect a C routine to a hardware interrupt'''
        caller = qemu.avatar.config.get_symbol_name(qemu.regs.lr)
        caller = caller if caller is not None else hex(qemu.regs.lr)
        handler_addr = qemu.get_arg(1)
        handler = qemu.avatar.config.get_symbol_name(handler_addr)

        if handler is None:
            handler = handler_addr

        with open(self.int_connect_log, 'a') as file:
            file.write("\ncaller:%s\tvector:%s\tHandler:%s\tParam:%s"%
                       (caller,hex(qemu.regs.r0),handler,hex(qemu.get_arg(2))))
        log.debug("\ncaller:%svector:%s\tHandler:%s (%s)\tParam:%s",
                  caller,hex(qemu.regs.r0),handler,hex(handler_addr),hex(qemu.get_arg(2)))
        return False, None

    @bp_handler(['intExit'])
    def int_exit(self, qemu, addr):
        '''int_exit'''
        return False, None

    @bp_handler(['intEnable'])
    def int_enable(self, qemu, addr):
        '''int_enable'''
        irq_num = qemu.get_arg(0)
        log.debug("int_enable: %#x ", irq_num)
        self.model.enable_bp(irq_num)
        return False, None

    @bp_handler(['intDisable'])
    def int_disable(self, qemu, addr):
        '''int_disable'''
        irq_num = qemu.get_arg(0)
        log.debug("int_disable: %#x ", irq_num)
        self.model.disable_bp(irq_num)
        return False, None

    @bp_handler(['intLockLevelSet'])
    def int_lock_level_set(self, qemu, addr):
        '''int_lock_level_set'''
        # Sets lock level that is used when intLock is called
        func_name = qemu.avatar.config.get_symbol_name(addr)
        log.debug("In: %s", func_name)
        return False, None

    @bp_handler(['intLockLevelGet'])
    def int_lock_level_get(self, qemu, addr):
        '''int_lock_level_get'''
        # Gets current lock level
        func_name = qemu.avatar.config.get_symbol_name(addr)
        log.debug("In: %s", func_name)
        return False, None


    @bp_handler(['intLock'])
    def int_lock(self, qemu, addr):
        '''int_lock'''
        #Disable global interrupts globally
        # On arm this is disabling the CPU's interrupts so don't
        # Need to do anything
        func_name = qemu.avatar.config.get_symbol_name(addr)
        log.debug("In: %s", func_name)
        return False, None

    @bp_handler(['intUnlock'])
    def int_unlock(self, qemu, addr):
        '''int_unlock'''
        #Re-enable global interrupts
        func_name = qemu.avatar.config.get_symbol_name(addr)
        log.debug("In: %s", func_name)
        return False, None
