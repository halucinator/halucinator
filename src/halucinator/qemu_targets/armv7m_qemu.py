# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

from __future__ import annotations

from typing import Any, Optional

from .arm_qemu import ARMQemuTarget


class ARMv7mQemuTarget(ARMQemuTarget):

    def trigger_interrupt(self, interrupt_number: int, cpu_number: int = 0) -> Any:
        self.protocols.monitor.execute_command(
            'avatar-armv7m-inject-irq',
            {'num_irq': interrupt_number, 'num_cpu': cpu_number})

    def set_vector_table_base(self, base: int, cpu_number: int = 0) -> Any:
        self.protocols.monitor.execute_command(
            'avatar-armv7m-set-vector-table-base',
            {'base': base, 'num_cpu': cpu_number})

    def enable_interrupt(self, interrupt_number: int, cpu_number: int = 0) -> Any:
        self.protocols.monitor.execute_command(
            'avatar-armv7m-enable-irq',
            {'num_irq': interrupt_number, 'num_cpu': cpu_number})

    def write_branch(self, addr: int, branch_target: int, options: Optional[Any] = None) -> None:
        '''
            Places an absolute branch at address addr to
            branch_target

            :param addr(int): Address to write the branch code to
            :param branch_target: Address to branch too
        '''
        raise NotImplemented("Write branch not implemented")
