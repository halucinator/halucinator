"""Bus Pirate v5 (RP2040) demo intercepts.

Two handler classes:

* ``RP2040Init`` — chip-level boot fix-ups: pre-claim every SIO spinlock so
  ``claim_unused_lock()`` succeeds without an emulated SIO model, and
  optionally log indirect calls (``blx r3``) with the resolved symbol
  name when the backend can provide one.
* ``BusPirateConsole`` — wires the firmware's USB-CDC ``rx_fifo_try_get`` /
  ``tud_cdc_n_write`` paths to a ``UTTYModel`` "BP5" interface so an
  external device (``bpv5_terminal``) can drive the firmware over ZMQ.

Annotations target ``HalBackend`` (the abstract base) rather than the
avatar2-specific ``HALQemuTarget`` so these handlers work on any backend
that implements the standard ``read_memory`` / ``write_memory`` /
``get_arg`` / ``regs`` interface — verified on unicorn and avatar2.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Tuple, Type

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.utty import UTTYModel

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# RP2040 SIO — datasheet §2.3.1.
# Spinlock array: 32 × u32 at offset 0x100. Any non-zero write claims a lock;
# reads return 0 if unclaimed.
_SIO_BASE = 0xD0000000
_SIO_SPINLOCK_BASE = _SIO_BASE + 0x100
_SIO_SPINLOCK_COUNT = 32

# Bus Pirate firmware: _DEBUG_LEVELS bitmap (8 × u32) — all-ones enables
# every log category at the source.
_BPV5_DEBUG_LEVELS_BASE = 0x200018C0
_BPV5_DEBUG_LEVELS_COUNT = 8


def _resolve_symbol(qemu: "HalBackend", addr: int) -> str:
    """Best-effort symbol-name lookup. Falls back to a hex address when the
    backend doesn't expose ``get_symbol_name`` (currently only the
    avatar2-side ``qemu_targets/*.py`` subclasses do)."""
    getter = getattr(qemu, "get_symbol_name", None)
    if getter is None:
        return f"0x{addr:x}"
    try:
        return getter(addr) or f"0x{addr:x}"
    except Exception:  # noqa: BLE001
        return f"0x{addr:x}"


class RP2040Init(BPHandler):
    """Chip-level boot fix-ups for the RP2040 / Bus Pirate v5 firmware."""

    def __init__(self) -> None:
        super().__init__()

    @bp_handler(["init_spinlocks"])
    def init_spinlocks(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Pre-claim every SIO spinlock + enable every BPv5 debug category.

        Bypasses the real SIO peripheral by writing directly to the memory
        region the YAML maps as RAM. ``claim_unused_lock()`` then walks the
        array and finds the first non-zero entry without invoking the real
        spinlock acquire path.
        """
        print("Initializing RP2040 Spinlocks and Debug levels...", flush=True)
        for i in range(_SIO_SPINLOCK_COUNT):
            qemu.write_memory(_SIO_SPINLOCK_BASE + (i * 4), 4, 1)
        for i in range(_BPV5_DEBUG_LEVELS_COUNT):
            qemu.write_memory(_BPV5_DEBUG_LEVELS_BASE + (i * 4), 4, 0xFFFFFFFF)
        return True, 0

    @bp_handler(["break_blx"])
    def break_blx(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Log indirect ``blx r3`` jumps with the resolved symbol name.

        Uses :func:`_resolve_symbol` so the lookup works on backends that
        provide ``get_symbol_name`` (avatar2 path) and falls back to a
        hex address on backends that don't (unicorn, etc.).
        """
        r3 = qemu.regs.r3
        name = _resolve_symbol(qemu, r3)
        print(f"Calling function via blx r3: {hex(r3)} ({name})", flush=True)
        return False, 0


class BusPirateConsole(BPHandler):
    """UTTY-backed USB-CDC console bridge for the BPv5 command shell.

    The firmware reads keystrokes via ``rx_fifo_try_get(char *c)`` and writes
    output via ``tud_cdc_n_write(itf, buffer, bufsize)``. This bridges both
    sides to a ``UTTYModel`` "BP5" interface so an external device can drive
    the firmware over ZMQ — matching the two-process pattern used by
    ``hal_dev_uart`` for the STM32 UART example.

    No in-handler character injection: ``bpv5_terminal`` provides the boot
    keystroke via its ``--prelude`` option once halucinator's ZMQ sockets
    are bound.
    """

    def __init__(self, model: Type[UTTYModel] = UTTYModel) -> None:
        super().__init__()
        # NOTE: UTTYModel is used as a class-level singleton — every call
        # routes through class methods like ``add_interface`` /
        # ``get_rx_buff_size``. The annotation reflects that we hold the
        # class, not an instance.
        self.utty_model: Type[UTTYModel] = model
        self.utty_model.add_interface("BP5", enabled=True)
        self.utty_model.attach_interface("BP5")

    @bp_handler(["rx_fifo_try_get"])
    def rx_fifo_try_get(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``bool rx_fifo_try_get(char *c)`` — true if a char was read."""
        char_ptr = qemu.get_arg(0)
        if self.utty_model.get_rx_buff_size("BP5") > 0:
            char = self.utty_model.get_rx_char("BP5")
            qemu.write_memory(char_ptr, 1, char)
            return True, 1
        return True, 0

    @bp_handler(["write"])
    def write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint32_t tud_cdc_n_write(uint8_t itf, void const* buffer, uint32_t bufsize)``"""
        buffer_ptr = qemu.get_arg(1)
        bufsize = qemu.get_arg(2)
        data = qemu.read_memory(buffer_ptr, 1, bufsize, raw=True)
        self.utty_model.tx_buf("BP5", data)
        return True, bufsize

    @bp_handler(["write_flush"])
    def write_flush(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint32_t tud_cdc_n_write_flush(uint8_t itf)`` — no-op in emulation."""
        return True, 1
