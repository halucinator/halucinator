"""The MMIO trace has to actually reach disk.

`emulate: RecordingPeripheral` is the *record* half of the
record -> infer -> synthesize loop that peripheral-model generation consumes.
Two independent defects meant it wrote nothing:

1. `_instantiate_peripheral` accepted a `db_path` argument and never forwarded
   it, so the recording peripherals were always constructed with their default
   `db_path=None`. The failure is silent: the peripheral loads, the log says it
   is active, the run works, and only the trace is missing.

2. The flush condition is evaluated *inside an access*, by count or elapsed
   time. A run that issues fewer than `FLUSH_EVERY` accesses -- or that stops
   touching MMIO after early boot -- never wrote its tail. That is most of a
   short bring-up run, which is exactly when the trace is wanted.

These tests avoid constructing a real `GenericPeripheral` subclass, which needs
avatar2's interval-tree handler maps. The forwarding logic is exercised through
the fully-qualified `module.Class` branch with the stand-ins below, so the tests
run with or without avatar2 installed.
"""
from __future__ import annotations

import os
import sqlite3
import types

import pytest

from halucinator import main as M


# --- stand-ins, addressed via the fully-qualified `emulate:` branch ---------

class TakesDbPath:
    """A peripheral whose constructor accepts db_path."""

    def __init__(self, name, address, size, db_path=None, **kwargs):
        self.name, self.address, self.size = name, address, size
        self.db_path = db_path
        self.kwargs = kwargs


class TakesKwargsOnly:
    """Accepts **kwargs — must be treated as accepting db_path."""

    def __init__(self, name, address, size, **kwargs):
        self.name, self.address, self.size = name, address, size
        self.kwargs = kwargs


class TakesNoDbPath:
    """Like GenericPeripheral: passing db_path would be a TypeError."""

    def __init__(self, name, address, size):
        self.name, self.address, self.size = name, address, size


_HERE = __name__  # this test module, importable by _instantiate_peripheral


def _mem(properties=None):
    return types.SimpleNamespace(name="mmio", base_addr=0x40000000,
                                 size=0x1000, properties=properties)


def test_db_path_is_forwarded(tmp_path):
    db = str(tmp_path / "trace.sqlite")
    inst = M._instantiate_peripheral(f"{_HERE}.TakesDbPath", _mem(), db)
    assert inst is not None
    assert inst.db_path == db, "db_path was accepted but not passed through"


def test_kwargs_constructor_also_receives_it(tmp_path):
    db = str(tmp_path / "trace.sqlite")
    inst = M._instantiate_peripheral(f"{_HERE}.TakesKwargsOnly", _mem(), db)
    assert inst.kwargs.get("db_path") == db


def test_peripheral_without_db_path_is_not_broken(tmp_path):
    """The forwarding must be gated on the signature: most peripherals take no
    db_path and would raise TypeError."""
    inst = M._instantiate_peripheral(f"{_HERE}.TakesNoDbPath", _mem(),
                                     str(tmp_path / "trace.sqlite"))
    assert inst is not None and inst.size == 0x1000


def test_yaml_properties_win_over_the_run_default(tmp_path):
    """A device that sets its own db_path in `properties:` keeps it."""
    chosen = str(tmp_path / "device-chosen.sqlite")
    inst = M._instantiate_peripheral(
        f"{_HERE}.TakesDbPath", _mem(properties={"db_path": chosen}),
        str(tmp_path / "run-default.sqlite"))
    assert inst.db_path == chosen


def test_no_db_path_available_means_none_is_passed():
    inst = M._instantiate_peripheral(f"{_HERE}.TakesDbPath", _mem(), None)
    assert inst.db_path is None


# --- persistence -----------------------------------------------------------

def _recording(tmp_path):
    """A RecordingPeripheral, skipping if this environment cannot build one
    (GenericPeripheral needs avatar2's handler maps)."""
    from halucinator.peripheral_models.auto_model import RecordingPeripheral
    db = str(tmp_path / "trace.sqlite")
    try:
        return RecordingPeripheral("mmio", 0x40000000, 0x1000, db_path=db), db
    except AttributeError as exc:      # no avatar2 handler maps here
        pytest.skip(f"cannot construct GenericPeripheral: {exc}")


def test_short_run_persists_its_trace_on_flush(tmp_path):
    """Fewer accesses than FLUSH_EVERY: the tail only reaches disk on flush."""
    from halucinator.peripheral_models.auto_model import RecordingPeripheral
    p, db = _recording(tmp_path)
    n = 10
    assert n < RecordingPeripheral.FLUSH_EVERY
    for _ in range(n):
        p.hw_read(0x10, 4, pc=0x8000)
    p.flush()
    assert os.path.exists(db), "flush() did not create the trace database"
    rows = sqlite3.connect(db).execute(
        "select count(*) from mmio_trace").fetchone()[0]
    assert rows == n, f"expected {n} recorded accesses, got {rows}"


def test_flush_is_idempotent(tmp_path):
    p, db = _recording(tmp_path)
    for _ in range(5):
        p.hw_read(0x10, 4, pc=0x8000)
    p.flush()
    first = sqlite3.connect(db).execute(
        "select count(*) from mmio_trace").fetchone()[0]
    p.flush()
    second = sqlite3.connect(db).execute(
        "select count(*) from mmio_trace").fetchone()[0]
    assert first == second == 5
