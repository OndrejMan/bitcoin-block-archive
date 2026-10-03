"""Repeat archive passes at a fixed interval until a stop signal arrives.

A pass that is already running always finishes: the signal only cancels the
wait before the next one. Being killed mid-pass anyway is harmless, because
a marker is written only after the remote copy has been verified.
"""

from __future__ import annotations

import re
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.logging_setup import LOG

DURATION_PATTERN = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$", re.I)
DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600}
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def parse_duration(text: str) -> int:
    """Parse `90`, `90s`, `30m` or `2h` into whole seconds."""
    match = DURATION_PATTERN.match(text)

    if match is None:
        raise ArchiveError(f"cannot parse duration: {text!r}")

    amount: str = match.group(1)
    unit: str = match.group(2)

    return int(amount) * DURATION_UNITS[unit.lower()]


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


def run_repeatedly(
    run_pass: Callable[[], bool],
    interval: int,
    stop: threading.Event,
) -> None:
    """Call `run_pass` every `interval` seconds until `stop` is set.

    A failed pass is retried on the normal schedule rather than ending the
    loop, so a temporary S3 or RPC outage heals without a restart.
    """
    while not stop.is_set():
        succeeded = run_pass()

        if stop.is_set():
            break

        if succeeded:
            LOG.info("Next archive pass in %ds", interval)
        else:
            LOG.warning("Archive pass failed; retrying in %ds", interval)

        if stop.wait(interval):
            break

    LOG.info("Stop requested; exiting")
