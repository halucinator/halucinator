"""
HalPeripheral — emulator-agnostic base class for all HALucinator peripheral models.

Replaces avatar2's AvatarPeripheral while staying fully compatible with it:
  - If avatar2 is available, HalPeripheral inherits from AvatarPeripheral so
    existing peripherals using the avatar2 read/write handler framework keep
    working unchanged.
  - If avatar2 is not available (future: Unicorn/direct-QEMU paths),
    HalPeripheral provides its own handler-registration stub so the same
    peripheral code can still be used.
"""
from __future__ import annotations

from typing import Any

try:
    from avatar2.peripherals.avatar_peripheral import AvatarPeripheral as _Base
    _HAVE_AVATAR2 = True
except ImportError:
    _HAVE_AVATAR2 = False

    class _Base:  # type: ignore[no-redef]
        """Minimal stub used when avatar2 is not installed."""

        def __init__(self, name: str, address: int, size: int, **kwargs: Any):
            self.name = name
            self.address = address
            self.size = size

        def shutdown(self) -> None:  # noqa: D401
            pass


class HalPeripheral(_Base):
    """
    Emulator-agnostic peripheral base class.

    Subclass this instead of AvatarPeripheral.  When avatar2 is available the
    full avatar2 handler registration (read_handler / write_handler interval
    trees) is inherited automatically.
    """

    # Make avatar2 availability queryable at class level.
    has_avatar2: bool = _HAVE_AVATAR2
