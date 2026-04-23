"""
Tests for halucinator.external_devices.trigger_interrupt - SendInterrupt class
"""

from unittest import mock

import pytest

from halucinator.external_devices.trigger_interrupt import SendInterrupt


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def interrupter(mock_ioserver):
    return SendInterrupt(mock_ioserver)


class TestSendInterruptInit:
    def test_stores_ioserver(self, mock_ioserver):
        si = SendInterrupt(mock_ioserver)
        assert si.ioserver is mock_ioserver


class TestTriggerInterrupt:
    def test_sends_single_interrupt(self, interrupter, mock_ioserver):
        interrupter.trigger_interrupt(5)
        mock_ioserver.send_msg.assert_called_once_with(
            "Interrupt.Trigger", {"num": 5}
        )

    def test_sends_walk_of_interrupts(self, interrupter, mock_ioserver):
        interrupter.trigger_interrupt(10, walk=3)
        assert mock_ioserver.send_msg.call_count == 3
        mock_ioserver.send_msg.assert_any_call("Interrupt.Trigger", {"num": 10})
        mock_ioserver.send_msg.assert_any_call("Interrupt.Trigger", {"num": 11})
        mock_ioserver.send_msg.assert_any_call("Interrupt.Trigger", {"num": 12})

    def test_walk_default_is_one(self, interrupter, mock_ioserver):
        interrupter.trigger_interrupt(0)
        assert mock_ioserver.send_msg.call_count == 1


class TestSetVectorBase:
    def test_sends_base_address(self, interrupter, mock_ioserver):
        interrupter.set_vector_base(0x08000000)
        mock_ioserver.send_msg.assert_called_once_with(
            "Interrupt.Base", {"base": 0x08000000}
        )


class TestMain:
    def test_main_with_interrupt(self):
        with mock.patch("halucinator.external_devices.trigger_interrupt.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.trigger_interrupt.time"), \
             mock.patch("sys.argv", ["trigger_interrupt", "-i", "5", "-r", "5556", "-t", "5555"]):
            from halucinator.external_devices.trigger_interrupt import main
            mock_io_instance = mock.Mock()
            MockIO.return_value = mock_io_instance
            main()
            mock_io_instance.send_msg.assert_called()

    def test_main_with_base_addr_hex(self):
        with mock.patch("halucinator.external_devices.trigger_interrupt.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.trigger_interrupt.time"), \
             mock.patch("sys.argv", ["trigger_interrupt", "-b", "0x8000000", "-r", "5556", "-t", "5555"]):
            from halucinator.external_devices.trigger_interrupt import main
            mock_io_instance = mock.Mock()
            MockIO.return_value = mock_io_instance
            main()
            mock_io_instance.send_msg.assert_called_once_with(
                "Interrupt.Base", {"base": 0x8000000}
            )

    def test_main_with_base_addr_decimal(self):
        with mock.patch("halucinator.external_devices.trigger_interrupt.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.trigger_interrupt.time"), \
             mock.patch("sys.argv", ["trigger_interrupt", "-b", "1024", "-r", "5556", "-t", "5555"]):
            from halucinator.external_devices.trigger_interrupt import main
            mock_io_instance = mock.Mock()
            MockIO.return_value = mock_io_instance
            main()
            mock_io_instance.send_msg.assert_called_once_with(
                "Interrupt.Base", {"base": 1024}
            )

    def test_main_with_walk(self):
        with mock.patch("halucinator.external_devices.trigger_interrupt.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.trigger_interrupt.time"), \
             mock.patch("sys.argv", ["trigger_interrupt", "-i", "10", "-w", "3", "-r", "5556", "-t", "5555"]):
            from halucinator.external_devices.trigger_interrupt import main
            mock_io_instance = mock.Mock()
            MockIO.return_value = mock_io_instance
            main()
            assert mock_io_instance.send_msg.call_count == 3

    def test_main_no_args_prints_usage(self, capsys):
        with mock.patch("halucinator.external_devices.trigger_interrupt.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.trigger_interrupt.time"), \
             mock.patch("sys.argv", ["trigger_interrupt", "-r", "5556", "-t", "5555"]):
            from halucinator.external_devices.trigger_interrupt import main
            mock_io_instance = mock.Mock()
            MockIO.return_value = mock_io_instance
            main()
            captured = capsys.readouterr()
            assert "Either -i or -b required" in captured.out
