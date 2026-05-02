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

    active: Dict[Any, bool] = defaultdict(bool)
    Active_Interrupts: Dict[Any, bool] = active  # alias used by tests
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
    def set_active_qmp(cls, irq_num: int) -> None:
        """
        Sets an interrupt using QMP Interface.
        Always marks the interrupt active; only fires if enabled.
        DO NOT use when executing from context of a BP Handler.
        """
        log.debug("Set Active QMP: %s", hex(irq_num))
        cls.active[irq_num] = True
        if cls.enabled[irq_num]:
            peripheral_server.irq_set_qmp(irq_num)

    @classmethod
    def set_active_bp(cls, irq_num: int) -> None:
        """
        Sets an interrupt using GDB interface.
        Always marks the interrupt active; only fires if enabled.
        Safe to use from BP Handler context.
        """
        log.debug("Set Active BP: %s", hex(irq_num))
        cls.active[irq_num] = True
        if cls.enabled[irq_num]:
            peripheral_server.irq_set_bp(irq_num)

    @classmethod
    def clear_active_bp(cls, irq_num: int) -> None:
        """
        Clears active interrupt.  Safe for use in BP Handler context.
        """
        log.debug("Clear Active BP: %i", irq_num)
        cls.active[irq_num] = False
        peripheral_server.irq_clear_bp(irq_num)

    @classmethod
    def clear_active_qmp(cls, irq_num: int) -> None:
        """
        Clears an active interrupt using QMP interface.
        DO NOT use from context of BP Handler.
        """
        log.debug("Clear Active: %i", irq_num)
        cls.active[irq_num] = False
        peripheral_server.irq_clear_qmp(irq_num)

    @classmethod
    def _trigger_interrupt_qmp(cls, irq_num: int) -> None:
        """Fire the interrupt via QMP only if both enabled and active."""
        if cls.enabled[irq_num] and cls.active[irq_num]:
            peripheral_server.irq_set_qmp(irq_num)

    @classmethod
    def _trigger_interrupt_bp(cls, irq_num: int) -> None:
        """Fire the interrupt via BP only if both enabled and active."""
        if cls.enabled[irq_num] and cls.active[irq_num]:
            peripheral_server.irq_set_bp(irq_num)

    @classmethod
    def enable_bp(cls, irq_num: int) -> None:
        """Enable an interrupt; if already active, fire it via BP."""
        cls.enabled[irq_num] = True
        if cls.active[irq_num]:
            peripheral_server.irq_set_bp(irq_num)

    @classmethod
    def enable_qmp(cls, irq_num: int) -> None:
        """Enable an interrupt; if already active, fire it via QMP."""
        cls.enabled[irq_num] = True
        if cls.active[irq_num]:
            peripheral_server.irq_set_qmp(irq_num)

    @classmethod
    def disable_bp(cls, irq_num: int) -> None:
        """Disable an interrupt via BP interface."""
        cls.enabled[irq_num] = False
        peripheral_server.irq_clear_bp(irq_num)

    @classmethod
    def disable_qmp(cls, irq_num: int) -> None:
        """Disable an interrupt via QMP interface."""
        cls.enabled[irq_num] = False
        peripheral_server.irq_disable_qmp(irq_num)

    @classmethod
    def set_active(cls, key: Any, value: bool = True) -> None:
        """Set named interrupt active state (for use with Active_Interrupts dict)."""
        cls.Active_Interrupts[key] = value

    @classmethod
    def clear_active(cls, key: Any) -> None:
        """Clear named interrupt active state."""
        cls.Active_Interrupts[key] = False

    @classmethod
    def is_active(cls, key: Any) -> bool:
        """Return True if the named interrupt is active."""
        return bool(cls.Active_Interrupts[key])

    @classmethod
    def get_active_irqs(cls) -> Set[int]:
        """Return set of irq_nums that are both active and enabled."""
        return {k for k, v in cls.active.items() if v and cls.enabled[k]}

    @classmethod
    def get_first_irq(cls, highest_first: bool = False) -> Optional[int]:
        """Return the lowest (or highest) active+enabled irq_num, or None."""
        active = cls.get_active_irqs()
        if not active:
            return None
        return max(active) if highest_first else min(active)

    @classmethod
    def trigger_interrupt(cls, irq_num: int, source: Optional[str] = None) -> None:
        """Trigger an interrupt by number, optionally noting the source."""
        if source is not None:
            cls.Active_Interrupts[source] = True
        peripheral_server.trigger_interrupt(irq_num)
