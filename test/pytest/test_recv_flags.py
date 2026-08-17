# Copyright 2026 Christopher Wright
"""SocketBridge must honour recv()'s flags argument.

Before this, ``_h_recv`` read arguments 0, 1 and 2 and never argument 3, so
every recv was serviced as though ``flags`` were 0. ``MSG_PEEK`` is the one
that quietly destroys a session: it promises the bytes stay queued, and we
consumed them, so firmware that peeks a header to learn a frame length and then
recv()s the frame lost the header and every later read was misframed by exactly
that many bytes -- with no error raised anywhere.
"""
import pytest

from halucinator.bp_handlers.generic.socket_bridge import (
    SocketBridge, _HostConn, _MSG_PEEK, _MSG_DONTWAIT, NEG1,
)

MSG_PEEK = 0x2


class FakeQemu:
    """Just enough backend to drive _h_recv: argv in, memory writes out."""

    def __init__(self, args):
        self._args = args
        self.written = {}

    def get_arg(self, i):
        return self._args[i] if i < len(self._args) else 0

    def write_memory_bytes(self, addr, data):
        self.written[addr] = bytes(data)

    def write_memory(self, addr, size, val):
        self.written[addr] = val


def _bridge_with(rx: bytes):
    br = SocketBridge()
    conn = _HostConn(sock=None, peer=("127.0.0.1", 1))
    conn.state = "open"
    conn.rx = bytearray(rx)
    br._fd2conn[7] = conn
    return br, conn


def test_peek_returns_the_bytes():
    br, _ = _bridge_with(b"ABCDEFGH")
    q = FakeQemu([7, 0x2000, 4, MSG_PEEK])
    handled, n = br._h_recv(q)
    assert handled is True
    assert n == 4
    assert q.written[0x2000] == b"ABCD"


def test_peek_does_not_consume():
    """The defect, stated directly."""
    br, conn = _bridge_with(b"ABCDEFGH")
    br._h_recv(FakeQemu([7, 0x2000, 4, MSG_PEEK]))
    assert bytes(conn.rx) == b"ABCDEFGH", "MSG_PEEK consumed the peeked bytes"


def test_peek_then_recv_sees_the_same_bytes():
    """The end-to-end shape: peek a 4-byte header, then read the frame."""
    br, conn = _bridge_with(b"HDR!payload")
    br._h_recv(FakeQemu([7, 0x2000, 4, MSG_PEEK]))
    q2 = FakeQemu([7, 0x3000, 11, 0])
    _, n = br._h_recv(q2)
    assert n == 11
    assert q2.written[0x3000] == b"HDR!payload"
    assert bytes(conn.rx) == b""


def test_flags_zero_still_consumes():
    br, conn = _bridge_with(b"ABCDEFGH")
    br._h_recv(FakeQemu([7, 0x2000, 4, 0]))
    assert bytes(conn.rx) == b"EFGH"


def test_recv_flags_arg_minus_one_ignores_argument_three():
    """A 3-arg read()-style binding must not have junk read as flags."""
    br, conn = _bridge_with(b"ABCDEFGH")
    br.recv_flags_arg = -1
    br._h_recv(FakeQemu([7, 0x2000, 4, MSG_PEEK]))   # arg 3 is register junk
    assert bytes(conn.rx) == b"EFGH", "junk in arg 3 was interpreted as flags"


def test_dontwait_skips_the_block_wait():
    """MSG_DONTWAIT must not sit in the recv_block_ms poll loop."""
    import time
    br, _ = _bridge_with(b"")
    br.recv_block_ms = 2000
    br.errno_scratch = 0x4000
    q = FakeQemu([7, 0x2000, 4, _MSG_DONTWAIT])
    t0 = time.time()
    handled, n = br._h_recv(q)
    assert handled is True and n == NEG1
    assert time.time() - t0 < 0.5, "MSG_DONTWAIT still blocked for recv_block_ms"


def test_constants_match_posix():
    assert _MSG_PEEK == 0x2
    assert _MSG_DONTWAIT == 0x40
