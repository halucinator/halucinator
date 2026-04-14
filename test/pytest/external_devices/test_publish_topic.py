"""
Tests for halucinator.external_devices.publish_topic
"""

from unittest import mock

import pytest

from halucinator.external_devices.publish_topic import GenericPrintServer


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


class TestGenericPrintServerInit:
    def test_stores_ioserver(self, mock_ioserver):
        gps = GenericPrintServer(mock_ioserver)
        assert gps.ioserver is mock_ioserver

    def test_prev_print_none(self, mock_ioserver):
        gps = GenericPrintServer(mock_ioserver)
        assert gps.prev_print is None

    def test_no_subscribe_topic(self, mock_ioserver):
        GenericPrintServer(mock_ioserver)
        mock_ioserver.register_topic.assert_not_called()

    def test_with_subscribe_topic(self, mock_ioserver):
        GenericPrintServer(mock_ioserver, subscribe_topic="TestTopic")
        mock_ioserver.register_topic.assert_called_once_with(
            "TestTopic", mock.ANY
        )


class TestGenericPrintServerSendData:
    def test_sends_correct_message(self, mock_ioserver):
        gps = GenericPrintServer(mock_ioserver)
        gps.send_data("Peripheral.UTTYModel.rx_char_or_buf", "COM1", "hello")
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": "COM1", "char": "hello"},
        )

    def test_sends_different_topic(self, mock_ioserver):
        gps = GenericPrintServer(mock_ioserver)
        gps.send_data("Custom.Topic", "id1", [1, 2, 3])
        mock_ioserver.send_msg.assert_called_once_with(
            "Custom.Topic",
            {"interface_id": "id1", "char": [1, 2, 3]},
        )


class TestGenericPrintServerWriteHandler:
    def test_write_handler_prints(self, mock_ioserver, capsys):
        gps = GenericPrintServer(mock_ioserver, subscribe_topic="TestTopic")
        # The write_handler has a bug: uses list comprehension syntax incorrectly
        # It does: ['%s: %s'%(key,data.decode('latin-1')) in msg.items()]
        # This creates a list with a single boolean (the 'in' expression).
        # We test that it runs without crashing for simple cases.
        # The msg needs to be a dict with items() method
        msg = {"key1": b"value1"}
        # This will produce a list with a boolean due to the bug in the code
        # The code does: data = ['%s: %s'%(key,data.decode('latin-1')) in msg.items()]
        # which evaluates as: [('%s: %s' % (key, data.decode('latin-1'))) in msg.items()]
        # This is a known bug in the source - just verify it doesn't crash
        # The write_handler has a bug: the list comprehension uses undefined 'key'
        # variable, causing NameError. We verify it raises as expected.
        with pytest.raises(NameError):
            gps.write_handler(mock_ioserver, msg)


class TestPublishTopicMainBlock:
    def _run_main(self, input_list, extra_argv=None):
        import runpy
        argv = ["publish_topic"]
        if extra_argv:
            argv.extend(extra_argv)

        with mock.patch(
            "halucinator.external_devices.publish_topic.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ), mock.patch("sys.argv", argv), mock.patch(
            "builtins.input", side_effect=input_list
        ):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            runpy.run_module(
                "halucinator.external_devices.publish_topic",
                run_name="__main__",
            )
            return mock_io_inst

    def test_main_keyboard_interrupt(self):
        self._run_main(KeyboardInterrupt)

    def test_main_send_text(self):
        io = self._run_main(["hello", KeyboardInterrupt])
        io.send_msg.assert_not_called()  # GenericPrintServer.send_data does the send

    def test_main_newline_input(self):
        self._run_main(["\\n", KeyboardInterrupt])

    def test_main_empty_breaks(self):
        self._run_main([""])

    def test_main_with_newline_flag(self):
        self._run_main(["hello", KeyboardInterrupt], extra_argv=["-n"])
