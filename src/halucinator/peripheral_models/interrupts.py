# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Peripheral Model for interrupts, exposes interfaces that can be used over both
ZMQ and BP Handlers
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from . import peripheral_server

log = logging.getLogger(__name__)
# log.setLevel(logging.DEBUG)


# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class Interrupts:
    """
    Models and external interrupt controller
    Use when need to trigger and interrupt and need additional state
    about it
    """

    Active_Interrupts: Dict[str, bool] = defaultdict(bool)
    active: Dict[int, bool] = defaultdict(bool)
    enabled: Dict[int, bool] = defaultdict(bool)

    @classmethod
    @peripheral_server.reg_rx_handler
    def interrupt_request(cls, msg: Dict[str, Any]) -> None:
        """
        Creates ZMQ interface to trigger an interrupt
        """
        if "num" in msg:
            irq_num = msg["num"]
        else:
            log.error("Unsupported IRQ %s", msg)
            return

        cls.set_active_qmp(irq_num)

    @classmethod
    def trigger_interrupt(
        cls, isr_num: int, source: Optional[str] = None
    ) -> None:
        if source is not None:
            cls.set_active(source)
        log.info("Triggering Interrupt: %i" % isr_num)
        peripheral_server.trigger_interrupt(isr_num)

    @classmethod
    def set_active(cls, key: str) -> None:
        log.debug("Set Active: %s" % str(key))
        cls.Active_Interrupts[key] = True

    @classmethod
    def clear_active(cls, key: str) -> None:
        log.debug("Clear Active: %s" % str(key))
        cls.Active_Interrupts[key] = False

    @classmethod
    def is_active(cls, key: str) -> bool:
        log.debug("Is Active: %s" % str(key))
        return cls.Active_Interrupts[key]

    @classmethod
    def set_active_qmp(cls, irq_num: int) -> None:
        """
        Sets an interrupt using QMP Interface
        DO NOT use when executing from context of a BP Handler
        """
        log.debug("Set Active QMP: %s", hex(irq_num))
        cls.active[irq_num] = True
        cls._trigger_interrupt_qmp(irq_num)

    @classmethod
    def set_active_bp(cls, irq_num: int) -> None:
        """
        Sets an interrupt using GDB interface can be used in BP Handlers
        """
        log.debug("Set Active BP: %s", hex(irq_num))
        cls.active[irq_num] = True
        cls._trigger_interrupt_bp(irq_num)

    @classmethod
    def clear_active_bp(cls, irq_num: int) -> None:
        """
        Clears active interrupt.  Safe for use in BP Handler context
        """
        log.debug("Clear Active BP: %i", irq_num)
        peripheral_server.irq_clear_bp(irq_num)
        cls.active[irq_num] = False

    @classmethod
    def clear_active_qmp(cls, irq_num: int) -> None:
        """
        Clears an active interrupt using QMP interface
        DO NOT use from context of BP Handler
        """
        log.debug("Clear Active: %i", irq_num)
        peripheral_server.irq_clear_qmp(irq_num)
        cls.active[irq_num] = False

    @classmethod
    def get_first_irq(cls, highest_first: bool = False) -> Optional[int]:
        """
        Returns the number of the highest priority active and enabled interrupt

        :param highest_first:  If True, return highest irq number first
        :returns: irq_num or None
        """
        active_irqs = sorted(cls.get_active_irqs(), reverse=highest_first)
        if len(active_irqs) > 0:
            return active_irqs[0]
        return None

    @classmethod
    def get_active_irqs(cls) -> Set[int]:
        """
        Returns the set of IRQ numbers that are both active and enabled
        """
        active_irqs = set(
            irq_num for irq_num, state in cls.active.items() if state
        )
        enabled_irqs = set(
            irq_num for irq_num, state in cls.enabled.items() if state
        )
        return active_irqs.intersection(enabled_irqs)

    @classmethod
    def _trigger_interrupt_qmp(cls, irq_num: int) -> None:
        """
        Triggers an interrupt via QMP if the interrupt is both enabled and active.
        Should be used to trigger an interrupt from everywhere except in a bp_handler.
        """
        if cls.enabled[irq_num] and cls.active[irq_num]:
            log.info("Triggering Interrupt: %i", irq_num)
            peripheral_server.irq_set_qmp(irq_num)

    @classmethod
    def _trigger_interrupt_bp(cls, irq_num: int) -> None:
        """
        Triggers an interrupt via GDB/BP if the interrupt is both enabled and active.
        Can be used inside bp_handlers.
        """
        if cls.enabled[irq_num] and cls.active[irq_num]:
            log.info("Triggering Interrupt: %i", irq_num)
            peripheral_server.irq_set_bp(irq_num)

    @classmethod
    def enable_bp(cls, irq_num: int) -> None:
        """
        Enables an interrupt so it can be triggered
        Safe for use from BP Handler context
        """
        cls.enabled[irq_num] = True
        cls._trigger_interrupt_bp(irq_num)

    @classmethod
    def enable_qmp(cls, irq_num: int) -> None:
        """
        Enables an interrupt using QMP interface
        DO NOT use from BP Handler context
        """
        cls.enabled[irq_num] = True
        cls._trigger_interrupt_qmp(irq_num)

    @classmethod
    def disable_bp(cls, irq_num: int) -> None:
        """
        Disables an interrupt so it cannot be triggered
        Safe for use from BP Handler context
        """
        cls.enabled[irq_num] = False
        peripheral_server.irq_clear_bp(irq_num)

    @classmethod
    def disable_qmp(cls, irq_num: int) -> None:
        """
        Disables an interrupt using QMP interface
        DO NOT use from BP Handler context
        """
        cls.enabled[irq_num] = False
        peripheral_server.irq_disable_qmp(irq_num)
