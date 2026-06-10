# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''sys clock module for handling halvxworks clock bps'''
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple, Type

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.timer_model import TimerModel
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()


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

    @bp_handler(['sysClkConnect', 'vxbSysClkConnect'])
    def sys_clk_connect(self, qemu: "HalBackend", addr: int) -> Tuple[bool, None]:
        '''Record the routine the firmware connects as its system-clock
        ISR (arg0). On x86 there is no NVIC/GIC vector table to read the
        handler from, so the X86PicController needs the connected C
        routine's address to vector the synthesised tick at it. Pass it
        through if the backend's IRQ controller can learn it.'''
        isr_addr = qemu.get_arg(0)
        ctrl = getattr(qemu, '_irq_controller', None)
        if ctrl is not None and hasattr(ctrl, 'register_clock_isr'):
            ctrl.register_clock_isr(isr_addr)
        # Only RECORD the ISR here — do NOT start the tick yet. VxWorks
        # connects the clock ISR early in init but enables it (sysClkEnable)
        # only after the kernel is ready; ticking before then drives
        # tickAnnounce into an uninitialised scheduler and hangs.
        hlog.info('SysClock: clock ISR connected, routine=0x%x (tick starts '
                  'on enable)', isr_addr)
        # Do NOT skip the real connect (return False): on vxBus VxWorks the
        # connect registers the timer device and wires later init; forcing a
        # return here diverges the boot. We only observe + record the ISR.
        return False, None

    @bp_handler(['sysClkEnable', 'vxbSysClkEnable'])
    def sys_clk_enable(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        '''sys_clk_enable'''
        hlog.info('SysClock: sysClkEnable -> starting tick timer %s @ %.4fs',
                  self.name, self.rate)
        self.model.start_timer(self.name, self.irq_num, self.rate, self.delay)
        # Let the real enable run too (return False) — running it natively is
        # boot-safe and avoids diverging vxBus init; our software TimerModel
        # additionally drives the periodic tick via the IRQ controller.
        return False, 0

    @bp_handler(['sysClkRateSet', 'vxbSysClkRateSet'])
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


class ClockTickStarter(BPHandler):
    '''Start the periodic clock tick only once the kernel reaches a known
    steady-state function (e.g. the scheduler `reschedule`).

    VxWorks connects + enables the system clock early in usrRoot, but the
    scheduler/kernel structures `tickAnnounce` touches are only fully ready
    once the kernel is multitasking. Delivering a tick before then drives
    `tickAnnounce` into an uninitialised scheduler and stalls boot. Hooking
    the tick-start at a steady-state symbol guarantees the kernel is ready
    when the first tick lands. start_timer is idempotent (keyed on name), so
    a hot symbol like `reschedule` only starts it once.'''

    def __init__(self, irq_num: int, name: str = 'sysClk',
                 rate: float = 0.1, delay: int = 0,
                 skip_real: bool = False,
                 unmask_arm_irqs: bool = False) -> None:
        self.irq_num = irq_num
        self.name = name
        self.rate = rate
        self.delay = delay
        self.skip_real = skip_real
        self.unmask_arm_irqs = unmask_arm_irqs
        self.model: Type[TimerModel] = TimerModel
        self._started = False

    @bp_handler(['reschedule', 'windExit', 'kernelTimeSlice'])
    def start_tick(self, qemu: "HalBackend", addr: int) -> Tuple[bool, None]:  # noqa: E501
        if not self._started:
            self._started = True
            hlog.info('ClockTickStarter: kernel steady state reached -> '
                      'starting tick timer %s @ %.4fs (irq=%d)',
                      self.name, self.rate, self.irq_num)
            self.model.start_timer(self.name, self.irq_num, self.rate,
                                   self.delay)
            # On ARM the reset stub leaves CPSR.I=1 (IRQs masked). When the
            # kernel scheduler enters multitasking it normally restores CPSR
            # from the first task's saved state with I=0. When skip_real
            # bypasses the scheduler body, that restoration never happens
            # and the synthesised arm_vic ticks are suppressed by
            # ArmVicController.deliver()'s CPSR.I check. unmask_arm_irqs
            # models the missing CPSR restore: clear bit 7 so subsequent
            # ticks actually deliver.
            if self.unmask_arm_irqs:
                try:
                    cpsr = qemu.read_register('cpsr')
                    qemu.write_register('cpsr', cpsr & ~0x80)
                    hlog.info('ClockTickStarter: cleared CPSR.I '
                              '(0x%08x -> 0x%08x)',
                              cpsr, cpsr & ~0x80)
                except Exception as _e:  # noqa: BLE001
                    hlog.error('ClockTickStarter: unmask_arm_irqs failed: %s',
                               _e)
        # skip_real=true: synthesize a SkipFunc-style return after the
        # tick is armed. Use this when the hooked function (typically the
        # kernel scheduler) cannot run cleanly without fully-initialised
        # TCBs, but we still want a periodic tick driving the
        # ArmVicController.
        return self.skip_real, None