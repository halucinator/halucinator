# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''sys clock module for handling halvxworks clock bps'''
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple, Type

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.timer_model import TimerModel

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)


class SysClock(BPHandler):
    '''SysClock'''
    def __init__(self, irq_num: int, name: str = 'sysClk', scale: int = 10, rate: int = 1, delay: int = 0) -> None:
        '''
            :param irq_num:  The Irq Number to trigger
            :param scale:
            :param rate:    Float( rate to fire irq in seconds)
        '''
        self.irq_num: int = irq_num
        self.name: str = name
        self.scale: int = scale
        self.rate: float = rate
        self.delay: int = delay
        self.model: Type[TimerModel] = TimerModel

    @bp_handler(['sysClkEnable'])
    def sys_clk_enable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        '''sys_clk_enable'''
        self.model.start_timer(self.name, self.irq_num, self.rate, self.delay)
        return False, 0

    @bp_handler(['sysClkRateSet'])
    def sys_clock_rate_set(self, qemu: "HalBackend", addr: int) -> Tuple[bool, None]:
        '''sys_clock_rate_set'''
        ticks_persec = qemu.get_arg(0)
        self.rate = (1.0 / ticks_persec) * self.scale
        return False, None

    @bp_handler(['sysClkDisable'])
    def sys_clk_disable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, None]:
        '''sys_clk_disable'''
        self.model.stop_timer(self.name)
        return False, None