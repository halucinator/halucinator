# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import logging
from typing import Any

from halucinator.peripherals.hal_peripheral import HalPeripheral as AvatarPeripheral  # noqa: F401
from .. import hal_stats as hal_stats

log = logging.getLogger(__name__)

hal_stats.stats['MMIO_read_addresses'] = set()
hal_stats.stats['MMIO_write_addresses'] = set()
hal_stats.stats['MMIO_addresses'] = set()
hal_stats.stats['MMIO_addr_pc'] = set()


class GenericPeripheral(AvatarPeripheral):
    read_addresses = set()

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        log.info("%s: Read from addr, 0x%08x size %i, pc: %s" %
                 (self.name, self.address + offset, size, hex(pc)))
        addr = self.address + offset
        hal_stats.write_on_update('MMIO_read_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update(
            'MMIO_addr_pc', "0x%08x,0x%08x,%s" % (addr, pc, 'r'))

        ret = 0
        return ret

    def hw_write(self, offset: int, size: int, value: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        log.info("%s: Write to addr: 0x%08x, size: %i, value: 0x%08x, pc %s" % (
            self.name, self.address + offset, size, value, hex(pc)))
        addr = self.address + offset
        hal_stats.write_on_update('MMIO_write_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update(
            'MMIO_addr_pc', "0x%08x,0x%08x,%s" % (addr, pc, 'w'))
        return True

    def __init__(self, name: str, address: int, size: int, **kwargs: Any) -> None:
        AvatarPeripheral.__init__(self, name, address, size)

        self.read_handler[0:size] = self.hw_read
        self.write_handler[0:size] = self.hw_write

        log.info("Setting Handlers %s" % str(self.read_handler[0:10]))


class IrqOnWritePeripheral(AvatarPeripheral):
    '''
        Test fixture for upstream issue #31: a peripheral that asserts an
        interrupt from inside its MMIO ``hw_write`` handler (forwarded-MMIO
        context, where the vCPU is mid-access and holds the QEMU global
        lock).

        The injection path is selected by the ``HAL_REPRO_INJECT_METHOD``
        env var:

          ``deferred`` (default) — ``peripheral_server.inject_irq_deferred``,
            the fix: the inject is handed to the IRQ worker thread, the
            write returns, and the IRQ wakes the (WFI-halted) core.
          ``qmp`` — ``peripheral_server.inject_irq`` inline, the original
            bug: the QMP inject can't be dispatched because the stalled
            write holds the BQL, so QEMU and Python deadlock.

        IRQ number is taken from the ``irq_num`` kwarg (default 17).
    '''

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> int:
        return 0

    def hw_write(self, offset: int, size: int, value: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> bool:
        import os
        from . import peripheral_server
        method = os.environ.get("HAL_REPRO_INJECT_METHOD", "deferred")
        log.info("%s: hw_write 0x%08x=0x%x -> inject method=%s irq=%d (issue #31)",
                 self.name, self.address + offset, value, method, self.irq_num)
        if method == "qmp":
            # Original bug: inline QMP inject from the MMIO thread.
            peripheral_server.inject_irq(self.irq_num)
        else:
            # The fix: defer the inject off the MMIO thread.
            peripheral_server.inject_irq_deferred(self.irq_num)
        log.info("%s: inject(method=%s) returned", self.name, method)
        return True

    def __init__(self, name: str, address: int, size: int, irq_num: int = 17, **kwargs: Any) -> None:
        AvatarPeripheral.__init__(self, name, address, size)
        self.irq_num = irq_num
        self.read_handler[0:size] = self.hw_read
        self.write_handler[0:size] = self.hw_write
        log.info("Setting IrqOnWrite Handlers %s (irq_num=%d)",
                 str(self.write_handler[0:4]), self.irq_num)


class HaltPeripheral(AvatarPeripheral):
    '''
        Just halts on first address read/written.
        Set infinite_loop = False (or mock it) to skip the halt in tests.
    '''

    def infinite_loop(self) -> None:
        """Loops forever - patch/mock this method to prevent halting in tests."""
        while 1:
            pass

    def hw_read(self, offset: int, size: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> None:
        addr = self.address + offset
        log.info("%s: Read from addr, 0x%08x size %i, pc: %s" %
                 (self.name, addr, size, hex(pc)))
        hal_stats.write_on_update('MMIO_read_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addr_pc', (hex(addr), hex(pc), 'r'))
        print("HALTING on MMIO READ")
        self.infinite_loop()
        return None

    def hw_write(self, offset: int, size: int, value: int, pc: int = 0xBAADBAAD, **kwargs: Any) -> None:
        addr = self.address + offset
        log.info("%s: Write to addr: 0x%08x, size: %i, value: 0x%08x, pc %s" % (
            self.name, addr, size, value, hex(pc)))
        hal_stats.write_on_update('MMIO_write_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addr_pc', (hex(addr), hex(pc), 'w'))
        print("HALTING on MMIO Write")
        self.infinite_loop()
        return None

    def __init__(self, name: str, address: int, size: int, **kwargs: Any) -> None:
        AvatarPeripheral.__init__(self, name, address, size)
        self.read_handler[0:size] = self.hw_read
        self.write_handler[0:size] = self.hw_write

        log.info("Setting Halt Handlers %s" % str(self.read_handler[0:10]))
