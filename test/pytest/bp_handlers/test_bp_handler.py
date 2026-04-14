"""Tests for halucinator.bp_handlers.bp_handler module."""

import struct
from unittest import mock

import pytest

from halucinator.bp_handlers.bp_handler import BPHandler, BPStruct, bp_handler


# ---------------------------------------------------------------------------
# bp_handler decorator
# ---------------------------------------------------------------------------


class TestBpHandlerDecorator:
    """Tests for the @bp_handler decorator."""

    def test_bp_handler_without_args_marks_function(self):
        @bp_handler
        def my_func(self, qemu, addr):
            return False, 0

        assert hasattr(my_func, "is_bp_handler")
        assert my_func.is_bp_handler is True

    def test_bp_handler_with_function_list(self):
        @bp_handler(["malloc", "calloc"])
        def my_func(self, qemu, addr):
            return False, 0

        assert hasattr(my_func, "bp_func_list")
        assert my_func.bp_func_list == ["malloc", "calloc"]

    def test_bp_handler_with_empty_list(self):
        @bp_handler([])
        def my_func(self, qemu, addr):
            return False, 0

        assert my_func.bp_func_list == []

    def test_bp_handler_decorated_function_is_still_callable(self):
        @bp_handler
        def my_func(self, qemu, addr):
            return True, 42

        result = my_func(None, None, None)
        assert result == (True, 42)


# ---------------------------------------------------------------------------
# BPHandler base class
# ---------------------------------------------------------------------------


class TestBPHandler:
    """Tests for the BPHandler base class."""

    def test_register_handler_finds_matching_function(self):
        class MyHandler(BPHandler):
            @bp_handler(["target_func"])
            def handle_it(self, qemu, addr):
                return True, 0

        handler = MyHandler()
        result = handler.register_handler(mock.Mock(), 0x1000, "target_func")
        assert result is MyHandler.handle_it

    def test_register_handler_raises_for_unknown_function(self):
        class MyHandler(BPHandler):
            @bp_handler(["known_func"])
            def handle_it(self, qemu, addr):
                return True, 0

        handler = MyHandler()
        with pytest.raises(ValueError, match="does not have bp_handler"):
            handler.register_handler(mock.Mock(), 0x1000, "unknown_func")

    def test_register_handler_with_multiple_candidates(self):
        class MyHandler(BPHandler):
            @bp_handler(["func_a"])
            def handle_a(self, qemu, addr):
                return True, 1

            @bp_handler(["func_b"])
            def handle_b(self, qemu, addr):
                return True, 2

        handler = MyHandler()
        result_a = handler.register_handler(mock.Mock(), 0x1000, "func_a")
        result_b = handler.register_handler(mock.Mock(), 0x2000, "func_b")
        assert result_a is MyHandler.handle_a
        assert result_b is MyHandler.handle_b


# ---------------------------------------------------------------------------
# BPStruct
# ---------------------------------------------------------------------------


class TestBPStruct:
    """Tests for the BPStruct class."""

    def test_basic_construction(self):
        s = BPStruct(field_a="<I", field_b="<H")
        assert s.field_a is None
        assert s.field_b is None

    def test_len(self):
        s = BPStruct(field_a="<I", field_b="<H")
        assert len(s) == 6  # 4 + 2

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Bad Fields"):
            BPStruct(bad_field="ZZZ")

    def test_overlapping_keys_raises(self):
        with pytest.raises(KeyError, match="Invalid field names"):
            BPStruct(_fields_desc="<I")

    def test_parse_buffer(self):
        s = BPStruct(val_a="<I", val_b="<H")
        buf = struct.pack("<I", 0xDEADBEEF) + struct.pack("<H", 0x1234)
        s.parse_buffer(buf)
        assert s.val_a == 0xDEADBEEF
        assert s.val_b == 0x1234

    def test_build_buffer(self):
        s = BPStruct(val_a="<I", val_b="<H")
        s.val_a = 0xCAFE
        s.val_b = 0x00FF
        buf = s.build_buffer()
        assert struct.unpack("<I", buf[:4])[0] == 0xCAFE
        assert struct.unpack("<H", buf[4:6])[0] == 0x00FF

    def test_build_buffer_with_none_field_uses_default(self):
        s = BPStruct(val_a="<I")
        # val_a is None, so it should be filled with zeros
        buf = s.build_buffer()
        assert buf == b"\x00" * 4

    def test_read_from_qemu(self):
        s = BPStruct(val_a="<I", val_b="<B")
        qemu = mock.Mock()
        buf = struct.pack("<I", 42) + struct.pack("<B", 7)
        qemu.read_memory.return_value = buf

        s.read(qemu, 0x2000)
        qemu.read_memory.assert_called_once_with(0x2000, 1, 5, raw=True)
        assert s.val_a == 42
        assert s.val_b == 7

    def test_repr(self):
        s = BPStruct(val_a="<I")
        s.val_a = 16
        r = repr(s)
        assert "val_a" in r
        assert "16" in r
        assert "0x10" in r

    def test_repr_with_none_value(self):
        s = BPStruct(val_a="<I")
        r = repr(s)
        assert "val_a" in r
        assert "None" in r

    def test_repr_with_non_int_value(self):
        s = BPStruct(val_a="4s")
        s.val_a = b"test"
        r = repr(s)
        assert "val_a" in r
        assert "test" in r

    def test_subclass_with_format(self):
        class MyStruct(BPStruct):
            FORMAT = {"x": "<I", "y": "<H"}

        s = MyStruct()
        assert s.x is None
        assert s.y is None
        assert len(s) == 6

    def test_write_to_qemu(self):
        s = BPStruct(val_a="<I")
        s.val_a = 99
        qemu = mock.Mock()
        s.write(qemu, 0x3000)
        expected_buf = struct.pack("<I", 99)
        qemu.write_memory.assert_called_once_with(0x3000, expected_buf)
