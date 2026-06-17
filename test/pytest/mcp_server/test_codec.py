# Copyright 2026 Christopher Wright

"""Unit tests for the wire codec (_codec.py). Pure, fast, SDK-free — runs on
every interpreter (no MCP SDK needed)."""
from __future__ import annotations

import pytest

from halucinator.mcp import _codec


class TestHex:
    @pytest.mark.parametrize("raw", [b"", b"\x00", b"halucinator", bytes(range(256))])
    def test_roundtrip(self, raw):
        assert _codec.hex_to_bytes(_codec.bytes_to_hex(raw)) == raw

    def test_prefix_and_case(self):
        assert _codec.hex_to_bytes("0xDEAD") == b"\xde\xad"
        assert _codec.hex_to_bytes("dEaD") == b"\xde\xad"

    def test_odd_length(self):
        with pytest.raises(ValueError, match="even length"):
            _codec.hex_to_bytes("abc")


class TestFraming:
    def test_request_roundtrip(self):
        frame = _codec.encode_frame(_codec.make_request(7, "read_memory",
                                                        {"addr": 16, "size": 4}))
        assert frame.endswith(b"\n")
        obj = _codec.decode_frame(frame)
        assert obj == {"id": 7, "method": "read_memory",
                       "params": {"addr": 16, "size": 4}}

    def test_ok_and_error_shapes(self):
        ok = _codec.decode_frame(_codec.encode_frame(_codec.make_ok(1, [1, 2])))
        assert ok == {"id": 1, "ok": True, "result": [1, 2]}
        err = _codec.decode_frame(_codec.encode_frame(
            _codec.make_error(2, "SessionError", "boom", "tb")))
        assert err["ok"] is False
        assert err["error"]["type"] == "SessionError"
        assert err["error"]["message"] == "boom"

    def test_bytes_method_tables(self):
        # The worker relies on these to (de)serialise memory transparently.
        assert "read_memory" in _codec.BYTES_RESULT_METHODS
        assert _codec.BYTES_PARAM_METHODS.get("write_memory") == "data"
