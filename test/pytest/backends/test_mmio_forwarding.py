"""Unit tests for the direct-QEMU backend's MMIO forwarding helpers."""
from unittest import mock

import pytest

from halucinator.main import _MMIOForwardingDispatcher


class _FakePeripheral:
    """Minimal AvatarPeripheral-shaped stub."""

    def __init__(self):
        self.reads = []
        self.writes = []

    def read_memory(self, address, size, num_words=1, raw=False):
        self.reads.append((address, size, num_words, raw))
        return 0xCAFE

    def write_memory(self, address, size, value):
        self.writes.append((address, size, value))


def _make_dispatcher_with_range(base=0x40000000, size=0x1000):
    """Build an _MMIOForwardingDispatcher with a stub avatar + range."""
    from avatar2.message import (
        RemoteMemoryReadMessage, RemoteMemoryWriteMessage,
    )

    periph = _FakePeripheral()
    rng = mock.Mock(forwarded=True, forwarded_to=periph)

    avatar = mock.Mock()
    avatar.get_memory_range = mock.Mock(return_value=rng)
    import queue as _queue
    avatar.queue = _queue.Queue()

    rmp = mock.Mock()
    rmp.send_response = mock.Mock()

    disp = _MMIOForwardingDispatcher(avatar, rmp)
    return disp, avatar, rmp, periph, RemoteMemoryReadMessage, RemoteMemoryWriteMessage


class TestMMIOForwardingDispatcher:
    def test_handles_read(self):
        disp, _, rmp, periph, Read, _ = _make_dispatcher_with_range()
        msg = Read(origin=None, id=42, pc=0x8000, address=0x40000100, size=4)
        disp._handle_read(msg)
        assert periph.reads == [(0x40000100, 4, 1, False)]
        rmp.send_response.assert_called_once_with(42, 0xCAFE, True)

    def test_handles_write(self):
        disp, _, rmp, periph, _, Write = _make_dispatcher_with_range()
        msg = Write(origin=None, id=7, pc=0x8000, address=0x40000200,
                    value=0xDEADBEEF, size=4)
        disp._handle_write(msg)
        assert periph.writes == [(0x40000200, 4, 0xDEADBEEF)]
        rmp.send_response.assert_called_once_with(7, 0, True)

    def test_read_returning_bytes_is_packed_little_endian(self):
        disp, _, rmp, periph, Read, _ = _make_dispatcher_with_range()
        periph.read_memory = lambda *a, **k: b"\x01\x02\x03\x04"
        msg = Read(origin=None, id=1, pc=0, address=0x40000000, size=4)
        disp._handle_read(msg)
        # bytes are packed LE so the first byte is the low-order byte
        rmp.send_response.assert_called_once_with(1, 0x04030201, True)

    def test_read_on_unforwarded_range_returns_failure(self):
        disp, avatar, rmp, _, Read, _ = _make_dispatcher_with_range()
        avatar.get_memory_range.return_value = None
        msg = Read(origin=None, id=9, pc=0, address=0xDEAD0000, size=4)
        disp._handle_read(msg)
        rmp.send_response.assert_called_once_with(9, 0, False)

    def test_read_exception_bubbles_to_failure_response(self):
        disp, _, rmp, periph, Read, _ = _make_dispatcher_with_range()
        periph.read_memory = mock.Mock(side_effect=RuntimeError("boom"))
        msg = Read(origin=None, id=3, pc=0, address=0x40000000, size=4)
        disp._handle_read(msg)
        rmp.send_response.assert_called_once_with(3, 0, False)


def test_start_mmio_forwarding_noop_when_nothing_forwarded(monkeypatch):
    """If no memory range has forwarded=True, _start_mmio_forwarding
    returns None and doesn't try to open mqueues."""
    from halucinator.main import _start_mmio_forwarding

    avatar = mock.Mock()
    avatar.memory_ranges = mock.Mock()
    avatar.memory_ranges.iter = mock.Mock(return_value=[])
    qemu_target = mock.Mock()

    disp = _start_mmio_forwarding(avatar, qemu_target)
    assert disp is None
