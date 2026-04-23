from threading import Thread


class ThreadPropagateExceptions(Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exc = e

    def check_exception(self):
        if self.exc:
            raise self.exc

    def join(self, timeout):
        super().join(timeout)
        self.check_exception()
