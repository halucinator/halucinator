# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

import logging
from typing import Dict, Optional

from halucinator.bp_handlers.bp_handler import (
    BPHandler,  # type: ignore
    HandlerFunction,
    HandlerReturn,
    bp_handler,
)
from halucinator.qemu_targets.hal_qemu import HALQemuTarget  # type: ignore

log = logging.getLogger(__name__)


class LIBOPENCM3_Flash(BPHandler):
    def __init__(self) -> None:
        self.addr_names: Dict[int, str] = {}
        self.flash_size: Optional[int] = None
        self.page_size: Optional[int] = None

        # This implementation will never have a programming error, a
        # write protection error, or display a busy state, but these
        # are here for completeness if such an extension is needed for
        # testing.
        self.flag_pgerr: bool = False
        self.flag_wperr: bool = False
        self.flag_eop: bool = False
        self.flag_bsy: bool = False

    def register_handler(
        self,
        qemu: HALQemuTarget,
        addr: int,
        func_name: str,
        flash_size: Optional[int] = 128,
        page_size: Optional[int] = 1024,
    ) -> HandlerFunction:
        """
        Modified from parent class to support registration arguments.
        Only one instance will exist; it does not make sense to support more than
        one Flash since the device has only one.

        registration_args {'flash_size': int (pages), 'page_size': int (bytes) }
        """

        self.addr_names[addr] = func_name
        # We populate with defaults if this is the first registered
        # function, but we also allow overwriting using the values
        # on the flash_get_status_flags member so that we have a way
        # of superseding the defaults without being order dependent.
        if func_name == "flash_get_status_flags":
            self.flash_size = flash_size
            self.page_size = page_size
        else:
            if self.flash_size is None:
                self.flash_size = flash_size
            if self.page_size is None:
                self.page_size = page_size
        return BPHandler.register_handler(self, qemu, addr, func_name)

    @bp_handler(["flash_set_ws"])
    def hal_flash_set_ws(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # flash_set_ws (
        #   uint32_t ws
        # )
        # The intercepted function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L55
        ws = qemu.regs.r0
        log.info("Flash Wait States set set to %i" % ws)
        return True, 0

    @bp_handler(["flash_lock"])
    def hal_flash_lock(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # flash_lock (
        #   void
        # )
        # The intercepted function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L60
        log.debug("Flash memory lock")
        return True, 0

    @bp_handler(["flash_unlock"])
    def hal_flash_unlock(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # flash_unlock (
        #   void
        # )
        # The intercepted function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_all.h#L66
        log.debug("Flash memory unlock")
        return True, 0

    @bp_handler(["flash_wait_for_last_operation"])
    def hal_ok(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # flash_wait_for_last_operation (
        #   void
        # )
        # The intercepted function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/flash_common_f.h#L42
        log.debug("Flash wait for last operation called")
        return True, 0

    @bp_handler(["flash_get_status_flags"])
    def hal_flash_get_status_flags(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Intercepted HAL function declaration and description can be found here:
        # https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f.h#L36
        #
        #   uint32_t flash_get_status_flags(void)
        #
        # The bits we return depend on the flash size.
        flags = 0
        if self.flag_eop:
            flags |= 0x20
        if self.flag_wperr:
            flags |= 0x10
        if self.flag_pgerr:
            flags |= 0x04
        if self.flag_bsy:
            flags |= 0x01
        log.debug("Flash get status flags called")
        return True, flags

    @bp_handler(
        [
            "flash_clear_status_flags",
            "flash_clear_eop_flag",
            "flash_clear_pgerr_flag",
            "flash_clear_wrprterr_flag",
        ]
    )
    def hal_flash_clear_flags(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        """Clear one or all flags as directed."""
        # Intercepted HAL function declarations can be found here:
        # (status) https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f.h#L36
        # (eop) https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f.h#L30
        # (pgerr) https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f01.h#L105
        # (wrprterr) https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f01.h#L106
        #
        #   void flash_clear_status_flags(void);
        #   void flash_clear_eop_flag(void);
        #   void flash_clear_pgperr_flag(void);
        #   void flash_clear_wrprterr_flag(void);
        if self.addr_names[bp_addr] == "flash_clear_status_flags":
            self.flag_pgerr = False
            self.flag_eop = False
            self.flag_wperr = False
            self.flag_bsy = False
        elif self.addr_names[bp_addr] == "flash_clear_eop_flag":
            self.flag_eop = False
        elif self.addr_names[bp_addr] == "flash_clear_pgerr_flag":
            self.flag_pgerr = False
        elif self.addr_names[bp_addr] == "flash_clear_wrprterr_flag":
            self.flag_wperr = False

        log.debug("%s called" % self.addr_names[bp_addr])
        return True, 0

    @bp_handler(["flash_program_word"])
    def hal_flash_program_word(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Associated HAL function declaration
        # void
        # flash_program_word (
        #   uint32_t address,
        #   uint32_t data
        # )
        # The intercepted function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/flash_common_f01.c#L82
        #
        # This function does not really change the bin image. It just
        # changes the memory.  If real changes are required it makes
        # sense to add functionality to this function that updates the
        # bin file directly instead of using QEMU mechanism.
        address = qemu.regs.r0
        data = qemu.regs.r1
        if (
            address < 0x8000000
            or address >= 0x8000000 + self.flash_size * self.page_size
        ):
            log.error("Flash Program address %i out of range." % address)
            return False, 0

        log.info("Flash Program Word %s at address %i" % (hex(data), address))
        qemu.write_memory_word(address, data)
        self.flag_eop = True
        return True, 0

    @bp_handler(["flash_erase_page"])
    def hal_flash_erase_page(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> HandlerReturn:
        # Intercepted HAL function declaration can be found here:
        # https://github.com/libopencm3/libopencm3/blob/v0.8.0/include/libopencm3/stm32/common/flash_common_f01.h#L110
        #
        #   void flash_erase_page(uint32_t page_address)
        #
        # This function does not really change the bin image. It just
        # changes the memory.  If real changes are required it makes
        # sense to add functionality to this function that updates the
        # bin file directly instead of using QEMU mechanism.
        address = qemu.regs.r0
        if (
            address < 0x8000000
            or address >= 0x8000000 + self.flash_size * self.page_size
        ):
            log.error("Flash Erase address %i out of range." % address)
            return False, 0

        # Data sheet suggests that any address within the page is fair game,
        # but since we need the addresses to execute the write_memory, it is
        # nice to tell the user what we're really doing.
        pgaddr = address & ~(self.page_size - 1)
        pgend = address + self.page_size - 1
        log.info("Flash Erase page %i - %i" % (pgaddr, pgend))
        eraseblock = b"\xff" * self.page_size
        qemu.write_memory(pgaddr, 1, eraseblock, self.page_size, raw=True)
        self.flag_eop = True
        return True, 0
