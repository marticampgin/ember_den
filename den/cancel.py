"""Cooperative cancellation across the request, model queue and provider socket."""

import threading
from contextlib import contextmanager


class Stopped(RuntimeError):
    pass


class Cancellation:
    def __init__(self):
        self._lock = threading.Lock()
        self._reason = None
        self._interrupt = None

    def stop(self, reason="Response stopped."):
        with self._lock:
            if self._reason is not None:
                return
            self._reason = reason
            if self._interrupt:
                self._interrupt()

    def check(self):
        with self._lock:
            if self._reason:
                raise Stopped(self._reason)

    @contextmanager
    def interruptible(self, interrupt):
        with self._lock:
            if self._reason:
                raise Stopped(self._reason)
            self._interrupt = interrupt
        try:
            yield
        finally:
            with self._lock:
                self._interrupt = None

    @contextmanager
    def slot(self, lock):
        self.check()
        while not lock.acquire(timeout=0.1):
            self.check()
        try:
            self.check()
            yield
        finally:
            lock.release()
