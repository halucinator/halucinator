"""Tests for halucinator.config.target_archs module."""

from unittest import mock

from halucinator.config.target_archs import _LazyTargets


class TestLazyTargets:
    def _make_targets(self):
        """Create a fresh _LazyTargets instance with mock data."""
        t = _LazyTargets()
        t._loaded = False
        # Manually populate to avoid importing qemu_targets
        t._ensure_loaded = lambda: (
            t.__class__.__dict__["_ensure_loaded"](t)
            if not t._loaded
            else None
        )
        return t

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_getitem_triggers_load(self, mock_get):
        mock_get.return_value = {"cortex-m3": {"avatar_arch": "ARM_CM3"}}
        t = _LazyTargets()
        t._loaded = False
        result = t["cortex-m3"]
        assert result == {"avatar_arch": "ARM_CM3"}
        mock_get.assert_called_once()

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_contains_triggers_load(self, mock_get):
        mock_get.return_value = {"arm": {"qemu_target": "ARMQemu"}}
        t = _LazyTargets()
        t._loaded = False
        assert "arm" in t
        assert "nonexistent" not in t

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_iter_triggers_load(self, mock_get):
        mock_get.return_value = {"a": 1, "b": 2}
        t = _LazyTargets()
        t._loaded = False
        keys = list(t)
        assert "a" in keys
        assert "b" in keys

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_keys_triggers_load(self, mock_get):
        mock_get.return_value = {"k1": "v1", "k2": "v2"}
        t = _LazyTargets()
        t._loaded = False
        keys = list(t.keys())
        assert "k1" in keys
        assert "k2" in keys

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_values_triggers_load(self, mock_get):
        mock_get.return_value = {"k": "val"}
        t = _LazyTargets()
        t._loaded = False
        vals = list(t.values())
        assert "val" in vals

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_items_triggers_load(self, mock_get):
        mock_get.return_value = {"k": "v"}
        t = _LazyTargets()
        t._loaded = False
        items = list(t.items())
        assert ("k", "v") in items

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_get_triggers_load(self, mock_get):
        mock_get.return_value = {"k": "v"}
        t = _LazyTargets()
        t._loaded = False
        assert t.get("k") == "v"
        assert t.get("missing", "default") == "default"

    @mock.patch("halucinator.config.target_archs._get_halucinator_targets")
    def test_only_loads_once(self, mock_get):
        mock_get.return_value = {"k": "v"}
        t = _LazyTargets()
        t._loaded = False
        _ = t["k"]
        _ = t["k"]
        # _get_halucinator_targets called only once
        mock_get.assert_called_once()
