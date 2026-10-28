from __future__ import annotations

import signal

from bitcoin_block_archive.signals import stop_on_signals


def test_previous_signal_handlers_are_restored() -> None:
    before = signal.getsignal(signal.SIGTERM)

    with stop_on_signals():
        assert signal.getsignal(signal.SIGTERM) is not before

    assert signal.getsignal(signal.SIGTERM) is before
