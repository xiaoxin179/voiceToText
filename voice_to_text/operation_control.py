"""Cancellation at safe boundaries shared by GUI and CLI video operations."""
from __future__ import annotations

import signal
import threading
from contextlib import contextmanager


class OperationCancelled(RuntimeError):
    pass


def check_cancelled(stop_event: threading.Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise OperationCancelled("Operation cancelled; any completed output files are retained")


@contextmanager
def cancel_on_interrupt():
    stop_event = threading.Event()
    previous = signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        if stop_event.is_set():
            raise KeyboardInterrupt
        stop_event.set()

    signal.signal(signal.SIGINT, interrupt)
    try:
        yield stop_event
    finally:
        signal.signal(signal.SIGINT, previous)
