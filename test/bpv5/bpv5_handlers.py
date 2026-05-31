from typing import Tuple, Dict, Any, Type
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.qemu_targets.hal_qemu import HALQemuTarget
from halucinator.peripheral_models.utty import UTTYModel

class RP2040Init(BPHandler):
    def __init__(self) -> None:
        pass

    @bp_handler(["init_spinlocks"])
    def init_spinlocks(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, int]:
        print("Initializing RP2040 Spinlocks and Debug levels...", flush=True)
        for i in range(32):
            qemu.write_memory(0xd0000100 + (i * 4), 4, 1)
        
        # Initialize _DEBUG_LEVELS (200018c0) to all 0xFF to enable all logs
        for i in range(8):
            qemu.write_memory(0x200018c0 + (i * 4), 4, 0xffffffff)
            
        return True, 0

    @bp_handler(["break_blx"])
    def break_blx(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, int]:
        r3 = qemu.regs.r3
        name = qemu.avatar.config.get_symbol_name(r3)
        print(f"Calling function via blx r3: {hex(r3)} ({name})", flush=True)
        return False, 0

class BusPirateConsole(BPHandler):
    def __init__(self, model: Type[UTTYModel] = UTTYModel) -> None:
        super().__init__()
        self.utty_model = model
        # Add interface "BP5"
        self.utty_model.add_interface("BP5", enabled=True)
        self.utty_model.attach_interface("BP5")
        self.injected = False
        self.inject_chars = [13, 10]

    @bp_handler(["rx_fifo_try_get"])
    def rx_fifo_try_get(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, int]:
        # bool rx_fifo_try_get(char *c)
        char_ptr = qemu.get_arg(0)
        
        if not self.injected and self.inject_chars:
            char = self.inject_chars.pop(0)
            qemu.write_memory(char_ptr, 1, char)
            if not self.inject_chars:
                self.injected = True
            return True, 1

        if self.utty_model.get_rx_buff_size("BP5") > 0:
            char = self.utty_model.get_rx_char("BP5")
            qemu.write_memory(char_ptr, 1, char)
            return True, 1
        return True, 0

    @bp_handler(["write"])
    def write(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, int]:
        # uint32_t tud_cdc_n_write(uint8_t itf, void const* buffer, uint32_t bufsize)
        buffer_ptr = qemu.get_arg(1)
        bufsize = qemu.get_arg(2)
        
        data = qemu.read_memory(buffer_ptr, 1, bufsize, raw=True)
        self.utty_model.tx_buf("BP5", data)
        
        return True, bufsize

    @bp_handler(["write_flush"])
    def write_flush(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, int]:
        return True, 1
