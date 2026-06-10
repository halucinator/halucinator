# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

from __future__ import annotations

from typing import Any, Optional

from .arm_qemu import ARMQemuTarget


CORTEX_M_EXTERNAL_IRQ_OFFSET = 16


class ARMv7mQemuTarget(ARMQemuTarget):

    def trigger_interrupt(self, interrupt_number: int, cpu_number: int = 0) -> Any:
        """Inject *interrupt_number* directly as a Cortex-M *exception*
        number. Exception numbers 0-15 are system exceptions (reset,
        NMI, HardFault, ..., SysTick); exception N+16 corresponds to
        external IRQ N (NVIC ISER bit N). Callers wanting external IRQ
        semantics should use :meth:`inject_irq` instead.
        """
        self.protocols.monitor.execute_command(
            'avatar-armv7m-inject-irq',
            {'num-irq': int(interrupt_number), 'num-cpu': cpu_number})

    def inject_irq(self, irq_num: int) -> None:
        """Pend external IRQ *irq_num* (NVIC ISER bit *irq_num*) via
        avatar-qemu's ``avatar-armv7m-inject-irq`` QMP command.

        ``avatar-armv7m-inject-irq`` calls
        ``armv7m_nvic_set_pending(nvic, n, false)`` directly, where
        *n* is the full Cortex-M exception number (0-15 system, 16+N
        external). The halucinator-side convention names interrupts
        by external IRQ index (matching NVIC ISER bit positions and
        the Vendor IRQ_n_Handler vector table slot 16+N), so we add
        the 16-exception offset here.

        Used by the legacy avatar2 path (peripheral_server prefers
        ``__QEMU.inject_irq`` when available). The halucinator-irq
        sysbus device's output line is wired to a dummy sink in
        avatar-qemu's configurable_machine, so ``qom-set
        halucinator-irq.set-irq`` does NOT deliver to the CPU.
        """
        self.trigger_interrupt(int(irq_num) + CORTEX_M_EXTERNAL_IRQ_OFFSET)

    def set_vector_table_base(self, base: int, cpu_number: int = 0) -> Any:
        self.protocols.monitor.execute_command(
            'avatar-armv7m-set-vector-table-base',
            {'base': base, 'num-cpu': cpu_number})

    def enable_interrupt(self, interrupt_number: int, cpu_number: int = 0) -> Any:
        self.protocols.monitor.execute_command(
            'avatar-armv7m-enable-irq',
            {'num-irq': interrupt_number, 'num-cpu': cpu_number})

    def write_branch(self, addr: int, branch_target: int, options: Optional[Any] = None) -> None:
        '''
            Places an absolute branch at address addr to
            branch_target

            :param addr(int): Address to write the branch code to
            :param branch_target: Address to branch too
        '''
        raise NotImplemented("Write branch not implemented")
