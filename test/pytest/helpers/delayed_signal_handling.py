import signal
from multiprocessing import Value
from time import sleep


def delayed_signal_handling(signum):
    """
    Return the class for delayed 'signum' handling, to be used in a
    with-statement context.
    """

    class DelayedSignalHandling:
        def __enter__(self):
            self.signal_received = None
            self.old_handler = signal.signal(signum, self.handler)

        def handler(self, signum, frame):
            self.signal_received = (signum, frame)

        def __exit__(self, type, value, traceback):
            signal.signal(signum, self.old_handler)
            if self.signal_received:
                self.old_handler(*self.signal_received)

    return DelayedSignalHandling


class AtomicCallCount:
    """
    Count the number of calls for a method. Optionally, add a sleep time after the call.
    """

    def __init__(self, counted_method, wait_sec=0):
        self.counted_method = counted_method
        self._num_calls = Value("i", 0)
        self.wait_sec = wait_sec

    def __call__(self, *args, **kwargs):
        # Delay SIGNINT handling, considering the method call to be atomic.
        DelayedSigintHandling = delayed_signal_handling(signal.SIGINT)
        with DelayedSigintHandling():
            self.counted_method(*args, **kwargs)
            self._num_calls.value += 1
        # Space out method calls.
        if self.wait_sec:
            sleep(self.wait_sec)

    @property
    def num_calls(self):
        return self._num_calls.value
