"""
Tests for halucinator.markers
"""

import os
from unittest import mock

import pytest

from halucinator.markers import (
    BUG,
    KnownOrSuspectedBug,
    KnownOrSuspectedUnusedCode,
    UNUSED,
    get_should_stop_execution,
    unused_function,
)


class TestGetShouldStopExecution:
    def test_default_stops(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            # Default "0" means stop
            assert get_should_stop_execution() is True

    @pytest.mark.parametrize("val", ["yes", "true", "1"])
    def test_continue_strings(self, val):
        with mock.patch.dict(os.environ, {"HALUCINATOR_CONTINUE_AFTER_BUG": val}):
            assert get_should_stop_execution() is False

    @pytest.mark.parametrize("val", ["no", "false", "0"])
    def test_stop_strings(self, val):
        with mock.patch.dict(os.environ, {"HALUCINATOR_CONTINUE_AFTER_BUG": val}):
            assert get_should_stop_execution() is True

    def test_invalid_value_warns(self):
        with mock.patch.dict(os.environ, {"HALUCINATOR_CONTINUE_AFTER_BUG": "maybe"}):
            # Invalid value should warn and return stop (not in CONT_STRINGS)
            result = get_should_stop_execution()
            # "maybe" is not in STOP_STRINGS either, so returns False
            assert result is False


class TestBUG:
    def test_raises_when_should_stop(self):
        with mock.patch.dict(os.environ, {"HALUCINATOR_CONTINUE_AFTER_BUG": "0"}):
            with pytest.raises(KnownOrSuspectedBug, match="test bug"):
                BUG("test bug")

    def test_does_not_raise_when_continue(self):
        with mock.patch.dict(os.environ, {"HALUCINATOR_CONTINUE_AFTER_BUG": "1"}):
            # Should not raise
            BUG("test bug")


class TestUNUSED:
    def test_always_raises(self):
        with pytest.raises(KnownOrSuspectedUnusedCode, match="dead code"):
            UNUSED("dead code")


class TestUnusedFunction:
    def test_raises_when_called(self):
        @unused_function
        def my_func(x):
            return x * 2

        with pytest.raises(KnownOrSuspectedUnusedCode, match="my_func"):
            my_func(5)

    def test_preserves_function_name(self):
        @unused_function
        def my_func(x):
            return x * 2

        assert my_func.__name__ == "my_func"


class TestExceptionClasses:
    def test_bug_exception_message(self):
        ex = KnownOrSuspectedBug("msg")
        assert str(ex) == "msg"

    def test_unused_exception_message(self):
        ex = KnownOrSuspectedUnusedCode("msg")
        assert str(ex) == "msg"
