"""Tests for halucinator.util.hexyaml module."""

import yaml

import halucinator.util.hexyaml  # noqa: F401 - importing registers the representer


class TestHexYaml:
    def test_int_represented_as_hex(self):
        data = {"value": 255}
        result = yaml.dump(data, default_flow_style=False)
        assert "0xff" in result

    def test_zero_represented_as_hex(self):
        data = {"value": 0}
        result = yaml.dump(data, default_flow_style=False)
        assert "0x0" in result

    def test_large_int_represented_as_hex(self):
        data = {"addr": 0x08001234}
        result = yaml.dump(data, default_flow_style=False)
        assert "0x8001234" in result

    def test_negative_int(self):
        data = {"value": -1}
        result = yaml.dump(data, default_flow_style=False)
        assert "-0x1" in result or "-1" in result

    def test_roundtrip(self):
        data = {"a": 16, "b": 256}
        dumped = yaml.dump(data)
        loaded = yaml.safe_load(dumped)
        assert loaded["a"] == 16
        assert loaded["b"] == 256
