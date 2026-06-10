from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from halucinator import hal_log as hal_log_conf
log = logging.getLogger(__name__)
hal_log = hal_log_conf.getHalLogger()

class HalMemConfig(object):
    '''
        Parses the memory portions of halucinator's config file
        and represents that data with some helper functions
    '''
    def __init__(
        self,
        name: str,
        config_filename: str,
        base_addr: int,
        size: int,
        permissions: str = 'rwx',
        file: Optional[str] = None,
        emulate: Optional[str] = None,
        qemu_name: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        irq: Optional[Any] = None,
        regions: Optional[Any] = None,
        alias_at: Optional[int] = None,
    ) -> None:
        '''
            Reads in config
        '''
        self.name: str = name
        self.config_file: str = config_filename  # For reporting where problems are
        self.file: Optional[str] = file
        self.size: int = size
        self.permissions: str = permissions
        self.emulate: Optional[str] = emulate
        self.emulate_required: bool = False
        self.base_addr: int = base_addr
        self.qemu_name: Optional[str] = qemu_name
        self.irq_config: Optional[Any] = irq
        self.properties: Optional[Dict[str, Any]] = properties
        # Multi-region MMIO mapping for sysbus devices that expose
        # more than one region (e.g. arm_gic: distributor + cpu
        # interface). Format: list of {region: int, address: int}.
        # Region 0 is implicitly mapped at base_addr; entries here
        # cover any additional regions (region: 1, 2, ...).
        self.regions: Optional[Any] = regions
        # Optional second mapping for the same memory region. Useful
        # for MIPS where firmware lives in kseg0 (0x80000000-0x9FFFFFFF)
        # at link time but the CPU's hardware mapping makes the only
        # reachable physical addresses 0x00000000-0x1FFFFFFF — listing
        # the kseg0 view at base_addr and an alias_at: 0x00000000
        # makes both unicorn (no MMU) and avatar2/qemu (real MIPS MMU)
        # find the firmware bytes.
        self.alias_at: Optional[int] = alias_at

        if self.file != None:
            self.get_full_path()

    def get_full_path(self) -> None:
        '''
            This make the file used by a memory relative to the config file
            containing it
        '''
        base_dir = os.path.dirname(self.config_file)
        if base_dir != None and not os.path.isabs(self.file):
            self.file = os.path.join(base_dir, self.file)

    def overlaps(self, other_mem: HalMemConfig) -> bool:
        '''
            Checks to see if this memory description overlaps with
            another

            :param (HalMemConfig) other_mem:
        '''
        if  self.base_addr >= other_mem.base_addr and \
            self.base_addr < other_mem.base_addr+ other_mem.size:
            return True

        elif other_mem.base_addr >= self.base_addr and \
            other_mem.base_addr < self.base_addr+ self.size:
            return True
        return False

    def is_valid(self) -> bool:
        valid: bool = True
        if self.size %(4096) != 0:
            hal_log.error("Memory/Peripheral: has invalid size, must be multiple of 4kB\n\t%s" % self)
            valid = False

        if self.emulate_required and self.emulate is None:
            hal_log.error("Memory/Peripheral: requires emulate field\n\t%s" % self)
            valid = False
        return valid

    def __repr__(self) -> str:
        return "(%s){name:%s, base_addr:%#x, size:%#x, emulate:%s}" % \
          (self.config_file, self.name, self.base_addr, self.size, self.emulate)
