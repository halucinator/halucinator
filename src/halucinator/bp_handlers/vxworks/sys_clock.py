# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''sys clock module for handling halvxworks clock bps'''
import logging

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.timer_model import TimerModel

log = logging.getLogger(__name__)


class SysClock(BPHandler):
    '''SysClock'''
    def __init__(self, irq_num, name='sysClk', scale=10, rate=1, delay=0):
        '''
            :param irq_num:  The Irq Number to trigger
            :param scale:
            :param rate:    Float( rate to fire irq in seconds)
        '''
        self.irq_num = irq_num
        self.name = name
        self.scale = scale
        self.rate = rate
        self.delay = delay
        self.model = TimerModel

    @bp_handler(['sysClkEnable'])
    def sys_clk_enable(self, qemu, addr):
        '''sys_clk_enable'''
        self.model.start_timer(self.name, self.irq_num, self.rate, self.delay)
        return False, 0

    @bp_handler(['sysClkRateSet'])
    def sys_clock_rate_set(self, qemu, addr):
        '''sys_clock_rate_set'''
        ticks_persec = qemu.get_arg(0)
        self.rate = (1.0 / ticks_persec) * self.scale
        return False, None

    @bp_handler(['sysClkDisable'])
    def sys_clk_disable(self, qemu, addr):
        '''sys_clk_disable'''
        self.model.stop_timer(self.name)
        return False, None
