"""
Tests for the GDB RSP server plugin (avatar2/plugins/gdbserver.py).

Covers:
  - GDB RSP packet encoding/decoding
  - Handler routing (q, g, p, P, m, M, c, s, Z, z, ?, D)
  - stop_filter callback for suppressing stops
  - cont() run/stop model (returns None, not OK)
  - step() waits for target to stop
  - halt_reason returns T05 when stopped
  - p/P single register read/write
  - detach cleanup
  - check_breakpoint_hit with and without stop_filter
"""

import socket
import struct
import threading
import time
from unittest import mock

import pytest
from avatar2.targets import TargetStates

from avatar2.plugins.gdbserver import GDBRSPServer, chksum


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeProtocols:
    """Minimal protocols mock — no GDB forwarding."""
    pass


class FakeRegs:
    """Register attribute proxy — returns self.pc for .pc, etc."""
    def __init__(self, pc=0):
        self.pc = pc


class FakeTarget:
    """Minimal target mock for GDBRSPServer tests."""
    def __init__(self, state=TargetStates.STOPPED, pc=0x08001000):
        self.state = state
        self._pc = pc
        self.regs = FakeRegs(pc)
        self.protocols = FakeProtocols()
        self._breakpoints = {}
        self._bp_counter = 0

        self.cont_called = False
        self.step_called = False
        self.stop_called = False
        self._registers = {
            'r0': 0, 'r1': 0, 'r2': 0, 'r3': 0,
            'sp': 0x20004000, 'lr': 0x08000100, 'pc': pc,
        }

    def read_register(self, name):
        return self._registers.get(name)

    def write_register(self, name, val):
        if name in self._registers:
            self._registers[name] = val
            if name == 'pc':
                self.regs.pc = val
            return True
        return False

    def read_memory(self, addr, size, raw=False, num_words=1):
        if raw:
            return b'\xaa' * size
        return 0xDEADBEEF

    def write_memory(self, addr, size, val, raw=False, num_words=1):
        return True

    def cont(self):
        self.cont_called = True
        self.state = TargetStates.RUNNING
        return True

    def step(self):
        self.step_called = True
        # Simulate immediate stop after step
        self.state = TargetStates.STOPPED
        return True

    def stop(self):
        self.stop_called = True
        self.state = TargetStates.STOPPED
        return True

    def set_breakpoint(self, addr):
        self._bp_counter += 1
        self._breakpoints[self._bp_counter] = addr
        return self._bp_counter

    def remove_breakpoint(self, bpno):
        if bpno in self._breakpoints:
            del self._breakpoints[bpno]
            return True
        return False

    def get_status(self):
        return {"state": self.state}

    def dictify(self):
        return {}


class FakeAvatar:
    """Minimal avatar mock."""
    def __init__(self):
        self.arch = mock.Mock()

    def get_memory_range(self, addr):
        return None


def make_server(target=None, port=0, stop_filter=None):
    """Create a GDBRSPServer with sane defaults for testing."""
    if target is None:
        target = FakeTarget()
    avatar = FakeAvatar()

    # Need a minimal XML file for the constructor
    import os
    xml_path = os.path.join(
        os.path.dirname(__file__),
        '..', '..', 'deps', 'avatar2', 'avatar2', 'plugins', 'gdb',
        'arm-target.xml',
    )
    server = GDBRSPServer(avatar, target, port=port, xml_file=xml_path)
    if stop_filter:
        server.stop_filter = stop_filter

    # Pre-populate registers for tests that need them
    server.registers = [
        {'name': 'r0', 'bitsize': '32'},
        {'name': 'r1', 'bitsize': '32'},
        {'name': 'r2', 'bitsize': '32'},
        {'name': 'r3', 'bitsize': '32'},
        {'name': 'sp', 'bitsize': '32'},
        {'name': 'lr', 'bitsize': '32'},
        {'name': 'pc', 'bitsize': '32'},
    ]
    return server


# ---------------------------------------------------------------------------
# Unit tests — individual handlers
# ---------------------------------------------------------------------------

class TestHandlerRouting:
    """Verify handlers are wired to the correct methods."""

    def test_handler_table_keys(self):
        server = make_server()
        expected_keys = {
            'q', 'v', 'H', '?', 'g', 'G', 'p', 'P',
            'm', 'M', 'c', 'C', 's', 'S', 'Z', 'z', 'D', 'k',
        }
        assert set(server.handlers.keys()) == expected_keys

    def test_q_routes_to_query(self):
        server = make_server()
        assert server.handlers['q'] == server.query

    def test_halt_reason_routes_correctly(self):
        server = make_server()
        assert server.handlers['?'] == server.halt_reason

    def test_g_handler_returns_register_data(self):
        """g handler goes through _forward_or_fallback; verify it produces output."""
        server = make_server()
        result = server.handlers['g'](b'g')
        # Should return a hex-encoded register blob (non-empty)
        assert result is not None and len(result) > 0

    def test_p_handler_returns_single_register(self):
        """p handler reads a single register via _forward_or_fallback."""
        server = make_server()
        # Request register 0 (first in the XML-derived register list)
        result = server.handlers['p'](b'p0')
        assert result is not None

    def test_P_handler_writes_single_register(self):
        """P handler writes a single register via _forward_or_fallback."""
        server = make_server()
        # Write 0x42 to register 0
        result = server.handlers['P'](b'P0=42000000')
        assert result == b'OK'

    def test_no_duplicate_S_handler(self):
        """Verify the old duplicate 'S' key bug (step_signal overwriting step) is fixed."""
        server = make_server()
        assert server.handlers['S'] == server.step


class TestHaltReason:

    def test_returns_T05_when_stopped(self):
        target = FakeTarget(state=TargetStates.STOPPED)
        server = make_server(target=target)
        result = server.halt_reason(b'?')
        assert result == b'T05'

    def test_returns_S00_when_running(self):
        target = FakeTarget(state=TargetStates.RUNNING)
        server = make_server(target=target)
        result = server.halt_reason(b'?')
        assert result == b'S00'


class TestContinue:

    def test_cont_returns_none(self):
        """GDB RSP: cont must NOT return a response; stop reply comes later."""
        target = FakeTarget()
        server = make_server(target=target)
        result = server.cont(b'c')
        assert result is None

    def test_cont_calls_target_cont(self):
        target = FakeTarget()
        server = make_server(target=target)
        server.cont(b'c')
        assert target.cont_called is True

    def test_cont_sets_running_flag(self):
        target = FakeTarget()
        server = make_server(target=target)
        server.cont(b'c')
        assert server.running is True


class TestStep:

    def test_step_returns_T05(self):
        target = FakeTarget()
        server = make_server(target=target)
        result = server.step(b's')
        assert result == b'T05'

    def test_step_calls_target_step(self):
        target = FakeTarget()
        server = make_server(target=target)
        server.step(b's')
        assert target.step_called is True

    def test_step_waits_for_stopped(self):
        """step() should poll until target is STOPPED."""
        target = FakeTarget()
        target.state = TargetStates.RUNNING  # Start running

        step_count = 0
        original_step = target.step

        def delayed_step():
            nonlocal step_count
            step_count += 1
            # Don't stop immediately — simulate a brief delay
            target.state = TargetStates.RUNNING

            def stop_later():
                time.sleep(0.01)
                target.state = TargetStates.STOPPED

            threading.Thread(target=stop_later, daemon=True).start()
            return True

        target.step = delayed_step

        server = make_server(target=target)
        result = server.step(b's')
        assert result == b'T05'


class TestReadRegisters:

    def test_read_all_registers(self):
        target = FakeTarget()
        target._registers['r0'] = 0x12345678
        target._registers['r1'] = 0xAABBCCDD
        server = make_server(target=target)
        result = server.read_registers(b'g')

        # r0 = 0x12345678 little-endian = 78563412
        assert result[:8] == b'78563412'
        # r1 = 0xAABBCCDD little-endian = DDCCBBAA
        assert result[8:16] == b'ddccbbaa'

    def test_read_single_reg_by_index(self):
        target = FakeTarget()
        target._registers['r0'] = 0x42
        server = make_server(target=target)
        # p0 = read register index 0
        result = server.read_single_reg(b'p0')
        # 0x42 in 32-bit little-endian hex = 42000000
        assert result == b'42000000'

    def test_read_single_reg_hex_index(self):
        """Register index is hex in GDB RSP."""
        target = FakeTarget()
        target._registers['pc'] = 0x08001000
        server = make_server(target=target)
        # pc is index 6 in our test register list
        result = server.read_single_reg(b'p6')
        assert result == b'00100008'

    def test_read_invalid_reg_returns_error(self):
        server = make_server()
        # Index 99 doesn't exist
        result = server.read_single_reg(b'p63')
        assert result == b'E00'


class TestWriteRegisters:

    def test_write_single_reg(self):
        target = FakeTarget()
        server = make_server(target=target)
        # P0=78563412 => write r0 = 0x12345678 (little-endian)
        result = server.write_single_reg(b'P0=78563412')
        assert result == b'OK'
        assert target._registers['r0'] == 0x12345678

    def test_write_invalid_reg_returns_error(self):
        server = make_server()
        result = server.write_single_reg(b'P63=00000000')
        assert result == b'E00'


class TestMemory:

    def test_mem_read(self):
        target = FakeTarget()
        server = make_server(target=target)
        result = server.mem_read(b'm08001000,4')
        # FakeTarget returns b'\xaa' * size for raw reads
        assert result == b'aaaaaaaa'

    def test_mem_write(self):
        target = FakeTarget()
        server = make_server(target=target)
        result = server.mem_write(b'M08001000,4:deadbeef')
        assert result == b'OK'

    def test_mem_read_invalid_returns_error(self):
        target = FakeTarget()
        target.read_memory = mock.Mock(side_effect=Exception("bad addr"))
        server = make_server(target=target)
        result = server.mem_read(b'm00000000,4')
        assert result == b'E00'


class TestBreakpoints:

    def test_insert_breakpoint(self):
        target = FakeTarget()
        server = make_server(target=target)
        result = server.insert_breakpoint(b'Z0,08001000,4')
        assert result == b'OK'
        assert 0x08001000 in server.bps.values()

    def test_remove_breakpoint(self):
        target = FakeTarget()
        server = make_server(target=target)
        server.insert_breakpoint(b'Z0,08001000,4')
        result = server.remove_breakpoint(b'z0,08001000,4')
        assert result == b'OK'
        assert len(server.bps) == 0

    def test_remove_nonexistent_breakpoint_returns_error(self):
        server = make_server()
        result = server.remove_breakpoint(b'z0,FFFFFFFF,4')
        assert result == b'E00'


class TestDetach:

    def test_detach_cleans_up_breakpoints(self):
        target = FakeTarget()
        server = make_server(target=target)
        # Mock the connection
        server.conn = mock.Mock()
        server.conn._closed = False

        server.insert_breakpoint(b'Z0,08001000,4')
        server.insert_breakpoint(b'Z0,08002000,4')
        assert len(server.bps) == 2

        server.detach(b'D')
        assert len(server.bps) == 0
        assert target.cont_called is True

    def test_detach_resets_running_flag(self):
        target = FakeTarget()
        server = make_server(target=target)
        server.conn = mock.Mock()
        server.conn._closed = False
        server.running = True

        server.detach(b'D')
        assert server.running is False


class TestQuery:

    def test_qsupported(self):
        server = make_server()
        result = server.query(b'qSupported')
        assert b'PacketSize=' in result
        assert b'qXfer:features:read+' in result

    def test_qattached(self):
        server = make_server()
        result = server.query(b'qAttached')
        assert result == b'1'

    def test_thread_info(self):
        server = make_server()
        assert server.query(b'qfThreadInfo') == b'm1'
        assert server.query(b'qsThreadInfo') == b'l'

    def test_unknown_query_returns_empty(self):
        server = make_server()
        result = server.query(b'qUnknownQuery')
        assert result == b''

    def test_set_thread_op_returns_ok(self):
        server = make_server()
        result = server.set_thread_op(b'Hg0')
        assert result == b'OK'


class TestMultiLetterCmd:

    def test_vmustreplyempty(self):
        server = make_server()
        result = server.multi_letter_cmd(b'vMustReplyEmpty')
        assert result == b''


# ---------------------------------------------------------------------------
# stop_filter tests — the key HALucinator integration point
# ---------------------------------------------------------------------------

class TestStopFilter:

    def test_no_filter_reports_all_stops(self):
        """Without a stop_filter, all stops are reported."""
        target = FakeTarget(state=TargetStates.STOPPED, pc=0x08001000)
        server = make_server(target=target)
        server.running = True
        server.conn = mock.Mock()

        server.check_breakpoint_hit()

        assert server.running is False
        server.conn.send.assert_called()
        # Should have sent T05 as a packet
        sent_data = server.conn.send.call_args[0][0]
        assert b'T05' in sent_data

    def test_filter_suppresses_stop(self):
        """When stop_filter returns True, the stop is not reported."""
        target = FakeTarget(state=TargetStates.STOPPED, pc=0x08001000)
        server = make_server(
            target=target,
            stop_filter=lambda t, pc: pc == 0x08001000,
        )
        server.running = True
        server.conn = mock.Mock()

        server.check_breakpoint_hit()

        # Stop was suppressed — still running, nothing sent
        assert server.running is True
        server.conn.send.assert_not_called()

    def test_filter_allows_other_stops(self):
        """stop_filter returning False allows the stop to be reported."""
        target = FakeTarget(state=TargetStates.STOPPED, pc=0x08002000)
        server = make_server(
            target=target,
            stop_filter=lambda t, pc: pc == 0x08001000,  # only suppress 0x08001000
        )
        server.running = True
        server.conn = mock.Mock()

        server.check_breakpoint_hit()

        assert server.running is False
        sent_data = server.conn.send.call_args[0][0]
        assert b'T05' in sent_data

    def test_filter_not_called_when_not_running(self):
        """stop_filter is only checked when server is in running state."""
        target = FakeTarget(state=TargetStates.STOPPED)
        filter_mock = mock.Mock(return_value=True)
        server = make_server(target=target, stop_filter=filter_mock)
        server.running = False

        server.check_breakpoint_hit()

        filter_mock.assert_not_called()

    def test_filter_not_called_when_target_not_stopped(self):
        """stop_filter is only checked when target is actually stopped."""
        target = FakeTarget(state=TargetStates.RUNNING)
        filter_mock = mock.Mock(return_value=True)
        server = make_server(target=target, stop_filter=filter_mock)
        server.running = True

        server.check_breakpoint_hit()

        filter_mock.assert_not_called()

    def test_filter_receives_masked_pc(self):
        """PC should have thumb bit masked (& 0xFFFFFFFE) before filter."""
        target = FakeTarget(state=TargetStates.STOPPED, pc=0x08001001)  # thumb bit set
        received_pcs = []

        def capture_filter(t, pc):
            received_pcs.append(pc)
            return False

        server = make_server(target=target, stop_filter=capture_filter)
        server.running = True
        server.conn = mock.Mock()

        server.check_breakpoint_hit()

        assert len(received_pcs) == 1
        assert received_pcs[0] == 0x08001000  # thumb bit cleared


# ---------------------------------------------------------------------------
# Packet encoding
# ---------------------------------------------------------------------------

class TestPacketEncoding:

    def test_chksum(self):
        assert chksum(b'OK') == (ord('O') + ord('K')) & 0xff

    def test_send_packet_format(self):
        """Packets should be $data#checksum."""
        server = make_server()
        server.conn = mock.Mock()
        server.send_packet(b'OK')

        sent = server.conn.send.call_args[0][0]
        assert sent.startswith(b'$')
        assert b'OK' in sent
        assert b'#' in sent

    def test_send_packet_rejects_strings(self):
        server = make_server()
        server.conn = mock.Mock()
        with pytest.raises(Exception, match="bytes"):
            server.send_packet("OK")


# ---------------------------------------------------------------------------
# spawn_gdb_server with stop_filter
# ---------------------------------------------------------------------------

class TestSpawnGdbServer:

    def test_stop_filter_passed_through(self):
        """spawn_gdb_server should set stop_filter on the server."""
        from avatar2.plugins.gdbserver import spawn_gdb_server, load_plugin
        import os

        avatar = mock.Mock()
        avatar._gdb_servers = []

        xml_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', 'deps', 'avatar2', 'avatar2', 'plugins', 'gdb',
            'arm-target.xml',
        )

        my_filter = lambda t, pc: True
        target = FakeTarget()

        # Patch socket.bind to prevent actual port binding
        with mock.patch.object(GDBRSPServer, 'run'):
            server = spawn_gdb_server(
                avatar, target, port=0, xml_file=xml_path,
                stop_filter=my_filter,
            )
            assert server.stop_filter is my_filter
