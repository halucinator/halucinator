# Copyright 2026 Christopher Wright

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


class SpiFlashTarget(BPHandler):
    """A modeled SPI NOR flash wired to the Bus Pirate's hardware SPI mode.

    This replaces the SPI *spoof* (SSP MMIO-as-RAM + ``skip_spi_init``) with a
    real **target device** answering at the controller's byte-transfer
    primitive. Rather than emulate the PL022 SSP register state machine, we
    do high-level emulation at the firmware's leaf SPI helpers — the same
    bytes flow, but a Python flash model supplies the MISO data:

    * ``hwspi_select``   — CS asserted: start a new SPI command (reset the
      per-transaction byte counter / command decoder).
    * ``hwspi_deselect`` — CS deasserted: end the command.
    * ``spi_write_read(tx) -> rx`` — full-duplex byte exchange (MOSI ``tx`` in
      r0, MISO returned in r0). Used by the mode's ``spi_write``.
    * ``hwspi_read() -> rx`` — the firmware clocks out 0xFF and returns MISO.
      Used by the mode's ``spi_read`` (the ``r:N`` syntax).

    Modeled command set (enough for the Bus Pirate flash tooling and a
    manual ``[0x9f r:3]`` JEDEC probe):

    * 0x9F  RDID  — JEDEC ID: manufacturer + 2 device-id bytes.
    * 0x90  REMS  — manufacturer/device id (after 3 dummy address bytes).
    * 0xAB  RDP/Res — release power-down + electronic signature.
    * 0x05  RDSR  — status register (returns 0x00: ready, not write-protected).
    * 0x03  READ  — read data: 3 address bytes then streamed content bytes.

    The default identity is a Winbond W25Q128 (JEDEC ``EF 40 18``); override
    via ``registration_args`` (``jedec``, ``content``).
    """

    # Winbond W25Q128JV — manufacturer 0xEF, type 0x40, capacity 0x18 (16 MiB).
    DEFAULT_JEDEC = (0xEF, 0x40, 0x18)

    def __init__(self, jedec=None, content=None) -> None:
        super().__init__()
        self.jedec = tuple(jedec) if jedec else self.DEFAULT_JEDEC
        # Backing content for READ (0x03). Default: a recognizable ramp so a
        # captured dump is obviously real data, not zeros.
        if content is None:
            content = bytes((i & 0xFF) for i in range(256))
        self.content = bytes(content)
        # Per-transaction state.
        self._cs = False          # chip-select asserted?
        self._cmd = None          # current command byte
        self._idx = 0             # byte index within the command
        self._addr = 0            # accumulated read address
        print(
            f"[SpiFlashTarget] modeled SPI NOR flash attached "
            f"(JEDEC {self.jedec[0]:#04x} {self.jedec[1]:#04x} "
            f"{self.jedec[2]:#04x})",
            flush=True,
        )

    # --- chip select ----------------------------------------------------
    @bp_handler(["select"])
    def select(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwspi_select()`` — CS low: begin a command."""
        self._cs = True
        self._cmd = None
        self._idx = 0
        self._addr = 0
        return True, 0

    @bp_handler(["deselect"])
    def deselect(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwspi_deselect()`` — CS high: end the command."""
        self._cs = False
        self._cmd = None
        self._idx = 0
        return True, 0

    # --- byte exchange --------------------------------------------------
    def _exchange(self, tx: int) -> int:
        """Clock one byte: consume MOSI ``tx``, return the MISO byte the
        modeled flash drives for this position in the current command."""
        tx &= 0xFF
        if self._cmd is None:
            # First byte after CS-low is the command opcode.
            self._cmd = tx
            self._idx = 1
            self._addr = 0
            return 0x00  # flash drives 0 while it reads the opcode

        miso = 0x00
        cmd = self._cmd
        n = self._idx

        if cmd == 0x9F:  # RDID — JEDEC ID
            if 1 <= n <= 3:
                miso = self.jedec[n - 1]
        elif cmd == 0x90:  # REMS — mfr/dev after 3 address bytes
            if n == 4:
                miso = self.jedec[0]
            elif n == 5:
                miso = self.jedec[1]
        elif cmd == 0xAB:  # RDP / electronic signature after 3 dummy bytes
            if n == 4:
                miso = self.jedec[2]
        elif cmd == 0x05:  # RDSR — status: ready, not WP
            miso = 0x00
        elif cmd == 0x03:  # READ — 3 address bytes then data
            if 1 <= n <= 3:
                self._addr = (self._addr << 8) | tx
            else:
                off = (self._addr + (n - 4)) % len(self.content)
                miso = self.content[off]

        self._idx += 1
        return miso

    @bp_handler(["write_read"])
    def write_read(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint8_t spi_write_read(uint8_t tx)`` — full-duplex exchange."""
        tx = qemu.get_arg(0) & 0xFF
        rx = self._exchange(tx)
        print(f"[SpiFlashTarget] MOSI=0x{tx:02X} -> MISO=0x{rx:02X}",
              flush=True)
        return True, rx

    @bp_handler(["read"])
    def read(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``uint8_t hwspi_read()`` — clock 0xFF, return MISO."""
        rx = self._exchange(0xFF)
        print(f"[SpiFlashTarget] MOSI=0xFF -> MISO=0x{rx:02X}", flush=True)
        return True, rx


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
