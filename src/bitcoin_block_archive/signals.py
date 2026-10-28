"""Turn SIGTERM and SIGINT into a stop request for the archive pass.

A pass that is already running still finishes; the request only keeps one from
starting. Being killed mid-pass anyway is harmless, because a marker is written
only after the remote copy has been verified.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


@contextmanager
def stop_on_signals() -> Iterator[threading.Event]:
    """Yield an event set by SIGTERM or SIGINT; restore the old handlers."""
    stop = threading.Event()

    # Only set the event: logging from a signal handler can interleave with
    # a log record the interrupted code is writing.
    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    previous = [
        (signum, signal.signal(signum, request_stop)) for signum in STOP_SIGNALS
    ]

    try:
        yield stop
    finally:
        for signum, handler in previous:
            signal.signal(signum, handler)
