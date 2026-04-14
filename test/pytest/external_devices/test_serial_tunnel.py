"""
Tests for halucinator.external_devices.serial_tunnel
"""

from unittest import mock

import pytest

from halucinator.external_devices.serial_tunnel import SerialTunnel


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def mock_serial():
    with mock.patch("halucinator.external_devices.serial_tunnel.serial.Serial") as MockSerial:
        mock_port = mock.Mock()
        MockSerial.return_value = mock_port
        yield MockSerial, mock_port


class TestSerialTunnelInit:
    def test_with_serial_port(self, mock_ioserver, mock_serial):
        MockSerial, mock_port = mock_serial
        st = SerialTunnel("/dev/ttyS0", mock_ioserver, 9600)
        assert st.ioserver is mock_ioserver
        assert st.host_port is mock_port
        MockSerial.assert_called_once_with("/dev/ttyS0", 9600)

    def test_registers_topic(self, mock_ioserver, mock_serial):
        SerialTunnel("/dev/ttyS0", mock_ioserver, 9600)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.UTTYModel.tx_buf", mock.ANY
        )

    def test_prev_print_none(self, mock_ioserver, mock_serial):
        st = SerialTunnel("/dev/ttyS0", mock_ioserver, 9600)
        assert st.prev_print is None

    def test_with_pipe(self, mock_ioserver):
        with mock.patch("builtins.open", mock.mock_open()) as mock_open:
            st = SerialTunnel("/dev/ttyS0", mock_ioserver, 9600, use_pipe=True)
            mock_open.assert_called_once_with("port", "w+")

    def test_with_pipe_open_fails(self, mock_ioserver):
        with mock.patch("builtins.open", return_value=None), \
             pytest.raises(SystemExit):
            SerialTunnel("/dev/ttyS0", mock_ioserver, 9600, use_pipe=True)


class TestSerialTunnelWriteHandler:
    def test_writes_to_host_port(self, mock_ioserver, mock_serial, capsys):
        _, mock_port = mock_serial
        st = SerialTunnel("/dev/ttyS0", mock_ioserver, 9600)
        msg = {"chars": b"\x41\x42\x43"}
        st.write_handler(mock_ioserver, msg)
        mock_port.write.assert_called_once_with(b"\x41\x42\x43")
        out = capsys.readouterr().out
        assert "VM Response" in out


class TestSerialTunnelSendData:
    def test_sends_correct_message(self, mock_ioserver, mock_serial):
        st = SerialTunnel("/dev/ttyS0", mock_ioserver, 9600)
        st.send_data("COM1", [0x41])
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": "COM1", "char": [0x41]},
        )


class TestSerialTunnelMainBlock:
    def test_main_block_runs(self):
        import runpy

        with mock.patch(
            "halucinator.external_devices.serial_tunnel.serial.Serial"
        ) as MockSerial, mock.patch(
            "halucinator.external_devices.serial_tunnel.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ):
            mock_port = mock.Mock()
            MockSerial.return_value = mock_port
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            # The while loop calls serial.host_port.read(1)
            mock_port.read.side_effect = KeyboardInterrupt

            with mock.patch("sys.argv", [
                "serial_tunnel", "-p", "/dev/ttyS0",
            ]):
                runpy.run_module(
                    "halucinator.external_devices.serial_tunnel",
                    run_name="__main__",
                )

    def test_main_reads_and_sends(self):
        import runpy

        with mock.patch(
            "halucinator.external_devices.serial_tunnel.serial.Serial"
        ) as MockSerial, mock.patch(
            "halucinator.external_devices.serial_tunnel.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ):
            mock_port = mock.Mock()
            MockSerial.return_value = mock_port
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            call_count = [0]
            def read_effect(n):
                call_count[0] += 1
                if call_count[0] > 1:
                    raise KeyboardInterrupt
                return b"\x41"

            mock_port.read.side_effect = read_effect

            with mock.patch("sys.argv", [
                "serial_tunnel", "-p", "/dev/ttyS0",
            ]):
                runpy.run_module(
                    "halucinator.external_devices.serial_tunnel",
                    run_name="__main__",
                )
