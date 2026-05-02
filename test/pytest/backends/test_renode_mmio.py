"""Tests for the Renode MMIO TCP bridge."""
import socket
import threading
import time
from unittest import mock

import pytest


class _FakePeripheral:
    def __init__(self):
        self.reads = []
        self.writes = []

    def read_memory(self, address, size, num_words=1, raw=False):
        self.reads.append((address, size))
        return 0xA5A5

    def write_memory(self, address, size, value):
        self.writes.append((address, size, value))


class TestRenodeMMIOServer:
    def test_read_rpc_dispatches_and_returns_value(self):
        from halucinator.backends.renode_mmio import RenodeMMIOServer
        server = RenodeMMIOServer()
        periph = _FakePeripheral()
        server.register(0x40000000, periph)
        port = server.start()
        try:
            # Fake a Renode bridge client.
            c = socket.socket()
            c.connect(("127.0.0.1", port))
            c.sendall(b"HELLO 40000000\n")
            c.sendall(b"R 40000010 4\n")
            resp = b""
            c.settimeout(2.0)
            while not resp.endswith(b"\n"):
                resp += c.recv(64)
            assert int(resp.strip(), 0) == 0xA5A5
            assert periph.reads == [(0x40000010, 4)]
            c.close()
        finally:
            server.stop()

    def test_write_rpc_dispatches_to_peripheral(self):
        from halucinator.backends.renode_mmio import RenodeMMIOServer
        server = RenodeMMIOServer()
        periph = _FakePeripheral()
        server.register(0x40000000, periph)
        port = server.start()
        try:
            c = socket.socket()
            c.connect(("127.0.0.1", port))
            c.sendall(b"HELLO 40000000\n")
            c.sendall(b"W 40000020 4 3735928559\n")  # 0xDEADBEEF
            ack = c.recv(64)
            assert b"OK" in ack
            assert periph.writes == [(0x40000020, 4, 0xDEADBEEF)]
            c.close()
        finally:
            server.stop()


def test_bridge_script_contains_port_and_base(tmp_path):
    from halucinator.backends.renode_mmio import write_bridge_script
    path = write_bridge_script(str(tmp_path), base=0x40013800, port=12345)
    text = open(path).read()
    assert "_HAL_PORT = 12345" in text
    assert "_BASE = 1073821696" in text  # 0x40013800
    assert "request.IsInit" in text


def test_emit_repl_python_peripherals(tmp_path):
    from halucinator.backends.renode_mmio import emit_repl_python_peripherals
    lines = emit_repl_python_peripherals(
        [("uart0", 0x40013800, 0x400)],
        str(tmp_path), port=5678,
    )
    assert any("uart0: Python.PythonPeripheral @ sysbus 0x40013800" in L
               for L in lines)
    assert any("size: 0x400" in L for L in lines)
    assert any("filename:" in L and "halmmio_40013800.py" in L for L in lines)
