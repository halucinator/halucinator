# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS). 
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains 
# certain rights in this software.

from halucinator.peripherals.hal_peripheral import HalPeripheral as AvatarPeripheral  # noqa: F401
from .. import hal_stats as hal_stats
import logging

log = logging.getLogger(__name__)

hal_stats.stats['MMIO_read_addresses'] = set()
hal_stats.stats['MMIO_write_addresses'] = set()
hal_stats.stats['MMIO_addresses'] = set()
hal_stats.stats['MMIO_addr_pc'] = set()


class GenericPeripheral(AvatarPeripheral):
    read_addresses = set()

    def hw_read(self, offset, size, pc=0xBAADBAAD, **kwargs):
        log.info("%s: Read from addr, 0x%08x size %i, pc: %s" %
                 (self.name, self.address + offset, size, hex(pc)))
        addr = self.address + offset
        hal_stats.write_on_update('MMIO_read_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update(
            'MMIO_addr_pc', "0x%08x,0x%08x,%s" % (addr, pc, 'r'))

        ret = 0
        return ret

    def hw_write(self, offset, size, value, pc=0xBAADBAAD, **kwargs):
        log.info("%s: Write to addr: 0x%08x, size: %i, value: 0x%08x, pc %s" % (
            self.name, self.address + offset, size, value, hex(pc)))
        addr = self.address + offset
        hal_stats.write_on_update('MMIO_write_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update(
            'MMIO_addr_pc', "0x%08x,0x%08x,%s" % (addr, pc, 'w'))
        return True

    def __init__(self, name, address, size, **kwargs):
        AvatarPeripheral.__init__(self, name, address, size)

        self.read_handler[0:size] = self.hw_read
        self.write_handler[0:size] = self.hw_write

        log.info("Setting Handlers %s" % str(self.read_handler[0:10]))


class HaltPeripheral(AvatarPeripheral):
    '''
        Just halts on first address read/written.
        Set infinite_loop = False (or mock it) to skip the halt in tests.
    '''

    def infinite_loop(self):
        """Loops forever — patch/mock this method to prevent halting in tests."""
        while 1:
            pass

    def hw_read(self, offset, size, pc=0xBAADBAAD, **kwargs):
        addr = self.address + offset
        log.info("%s: Read from addr, 0x%08x size %i, pc: %s" %
                 (self.name, addr, size, hex(pc)))
        hal_stats.write_on_update('MMIO_read_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addr_pc', (hex(addr), hex(pc), 'r'))
        print("HALTING on MMIO READ")
        self.infinite_loop()
        return None

    def hw_write(self, offset, size, value, pc=0xBAADBAAD, **kwargs):
        addr = self.address + offset
        log.info("%s: Write to addr: 0x%08x, size: %i, value: 0x%08x, pc %s" % (
            self.name, addr, size, value, hex(pc)))
        hal_stats.write_on_update('MMIO_write_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addresses', hex(addr))
        hal_stats.write_on_update('MMIO_addr_pc', (hex(addr), hex(pc), 'w'))
        print("HALTING on MMIO Write")
        self.infinite_loop()
        return None

    def __init__(self, name, address, size, **kwargs):
        AvatarPeripheral.__init__(self, name, address, size)
        self.read_handler[0:size] = self.hw_read
        self.write_handler[0:size] = self.hw_write

        log.info("Setting Halt Handlers %s" % str(self.read_handler[0:10]))
