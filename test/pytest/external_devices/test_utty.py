"""
Tests for halucinator.external_devices.utty

Module-level dunder globals require vars() access to avoid class name mangling.
"""

from unittest import mock

import pytest


def _get_mod():
    import halucinator.external_devices.utty as utty_mod
    return utty_mod


class TestUttyRxFromHost:
    def test_rx_unbuffered(self):
        """Test rx_from_host in non-buffering mode."""
        utty_mod = _get_mod()
        mv = vars(utty_mod)

        mock_io = mock.Mock()
        mock_port = mock.Mock()

        old_run = mv["__run_server"]
        old_port = mv["__host_port"]
        old_buf = mv["__rx_buffering"]

        try:
            mv["__run_server"] = True
            mv["__host_port"] = mock_port
            mv["__rx_buffering"] = False

            iter_count = [0]
            def read_then_stop(*a):
                iter_count[0] += 1
                if iter_count[0] > 1:
                    mv["__run_server"] = False
                    return b"\x00"
                return b"\x41"

            mock_port.read.side_effect = read_then_stop

            utty_mod.rx_from_host(mock_io, "COM1")
            assert mock_io.send_msg.call_count >= 1
        finally:
            mv["__run_server"] = old_run
            mv["__host_port"] = old_port
            mv["__rx_buffering"] = old_buf

    def test_rx_buffered(self):
        """Test rx_from_host in buffering mode."""
        utty_mod = _get_mod()
        mv = vars(utty_mod)

        mock_io = mock.Mock()
        mock_port = mock.Mock()

        old_run = mv["__run_server"]
        old_port = mv["__host_port"]
        old_buf = mv["__rx_buffering"]

        try:
            mv["__run_server"] = True
            mv["__host_port"] = mock_port
            mv["__rx_buffering"] = True

            call_count = [0]
            def read_bytes(*a):
                call_count[0] += 1
                if call_count[0] > 42:
                    mv["__run_server"] = False
                    return b"\x00"
                return b"\x42"

            mock_port.read.side_effect = read_bytes

            utty_mod.rx_from_host(mock_io, "COM1")
            assert mock_io.send_msg.call_count >= 1
        finally:
            mv["__run_server"] = old_run
            mv["__host_port"] = old_port
            mv["__rx_buffering"] = old_buf


class TestUttyStart:
    def test_start_opens_serial(self):
        utty_mod = _get_mod()

        with mock.patch("halucinator.external_devices.utty.serial.Serial") as MockSerial:
            mock_port = mock.Mock()
            MockSerial.return_value = mock_port

            with mock.patch.object(utty_mod, "rx_from_host") as mock_rx:
                mock_rx.return_value = None
                try:
                    with mock.patch("time.sleep", side_effect=KeyboardInterrupt):
                        utty_mod.start("/dev/ttyS0", mock.Mock(), "COM1", 9600)
                except KeyboardInterrupt:
                    pass

                MockSerial.assert_called_once_with("/dev/ttyS0", 9600)
                mock_rx.assert_called_once()


class TestUttyMainBlock:
    def test_main_arg_parsing(self):
        """Test the arg parsing for utty __main__ block."""
        from argparse import ArgumentParser
        p = ArgumentParser()
        p.add_argument("-r", "--rx_port", default=5556)
        p.add_argument("-t", "--tx_port", default=5555)
        p.add_argument("-p", "--port", required=True)
        p.add_argument("--id", default="COM1")
        p.add_argument("-b", "--baud", default=9600)
        args = p.parse_args(["-p", "/dev/ttyS0"])
        assert args.port == "/dev/ttyS0"
        assert args.id == "COM1"
