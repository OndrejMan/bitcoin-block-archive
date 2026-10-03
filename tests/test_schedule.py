from __future__ import annotations

import os
import signal
import threading
import time

import pytest

from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.schedule import (
    parse_duration,
    run_repeatedly,
    stop_on_signals,
)


@pytest.mark.parametrize(
    "text,seconds",
    [("0", 0), ("90", 90), ("90s", 90), ("30m", 1800), ("2h", 7200), (" 5M ", 300)],
)
def test_durations_parse_into_seconds(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "m", "-5m", "1.5h", "3d", "30 minutes"])
def test_invalid_durations_are_rejected(text: str) -> None:
    with pytest.raises(ArchiveError):
        parse_duration(text)


def test_failed_passes_do_not_end_the_loop() -> None:
    stop = threading.Event()
    results = iter([False, False, True])
    calls = 0

    def run_pass() -> bool:
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()
        return next(results)

    run_repeatedly(run_pass, 0, stop)

    assert calls == 3


def test_stop_requested_before_the_first_pass_runs_nothing() -> None:
    stop = threading.Event()
    stop.set()

    run_repeatedly(lambda: pytest.fail("pass must not run"), 0, stop)


def test_sigterm_during_a_pass_lets_it_finish_and_skips_the_wait() -> None:
    finished = []

    def run_pass() -> bool:
        os.kill(os.getpid(), signal.SIGTERM)
        finished.append(True)
        return True

    started = time.monotonic()
    with stop_on_signals() as stop:
        run_repeatedly(run_pass, 60, stop)

    assert finished == [True]
    assert time.monotonic() - started < 5


def test_sigterm_interrupts_the_wait_between_passes() -> None:
    calls = 0

    def run_pass() -> bool:
        nonlocal calls
        calls += 1
        threading.Timer(0.1, os.kill, (os.getpid(), signal.SIGTERM)).start()
        return True

    started = time.monotonic()
    with stop_on_signals() as stop:
        run_repeatedly(run_pass, 60, stop)

    assert calls == 1
    assert time.monotonic() - started < 5


def test_previous_signal_handlers_are_restored() -> None:
    before = signal.getsignal(signal.SIGTERM)

    with stop_on_signals():
        assert signal.getsignal(signal.SIGTERM) is not before

    assert signal.getsignal(signal.SIGTERM) is before
