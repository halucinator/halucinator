from multiprocessing import Value
from unittest import mock


class MainMock:
    """
    Mock calling main function with command-line arguments. Also, mock standard input
    with StringIO input, and count an number of calls for a method called from main.

    """

    def __init__(self, main, cmd_args, stdin, counted_method):
        self.main = main
        self.cmd_args = cmd_args
        self.stdin = stdin
        self.counted_method = counted_method
        self._quit_flag = Value("i", False)

    def main_mock(self):
        method_target = (
            self.counted_method.counted_method.__module__
            + "."
            + self.counted_method.counted_method.__qualname__
        )
        with mock.patch(
            "sys.argv", ["dummy_prog_name"] + self.cmd_args
        ), mock.patch("sys.stdin", self.stdin), mock.patch(
            method_target, lambda *args: self.counted_method(*args)
        ):
            try:
                self.main()
            except EOFError:
                pass
            except SystemExit:
                self._quit_flag.value = True

    @property
    def quit_flag(self):
        return self._quit_flag.value
