"""Watch disk space throughout uploads and the wait between archive passes."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from bitcoin_block_archive.bitcoin import stop_bitcoin
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.disk import format_size, free_bytes
from bitcoin_block_archive.logging_setup import LOG

DISK_CHECK_INTERVAL = 30


def space_is_critical(config: Config) -> bool:
    if config.min_free_space <= 0 or not config.block_dir.is_dir():
        return False
    free = free_bytes(config.block_dir)
    if free >= config.min_free_space:
        return False
    LOG.critical(
        "Only %s free in %s, below the %s threshold",
        format_size(free),
        config.block_dir,
        format_size(config.min_free_space),
    )
    return True


def _check(config: Config, stop: threading.Event, tripped: threading.Event) -> None:
    try:
        if not space_is_critical(config):
            return
    except Exception:
        LOG.exception("Cannot check free disk space; requesting node shutdown")

    tripped.set()
    stop.set()
    try:
        stop_bitcoin(config)
    except Exception:
        LOG.exception("Low-disk Bitcoin Core stop request failed")


@contextmanager
def watch_disk(config: Config, stop: threading.Event) -> Iterator[threading.Event]:
    """Latch on low space, stop Core and wake the scheduler; never restart Core.

    The caller checks the latch between files and batches. An operation already
    in flight may finish after a trip.
    Stop RPCs retain their configured timeout. Shutdown joins the watcher so
    no background RPC can outlive the CLI or go unreported on exit.
    """
    tripped = threading.Event()
    if config.min_free_space <= 0:
        yield tripped
        return

    _check(config, stop, tripped)
    if tripped.is_set():
        yield tripped
        return

    finished = threading.Event()

    def monitor() -> None:
        while not finished.wait(DISK_CHECK_INTERVAL):
            _check(config, stop, tripped)
            if tripped.is_set():
                break

    thread = threading.Thread(target=monitor, name="archive-disk-watchdog")
    thread.start()
    try:
        yield tripped
    finally:
        finished.set()
        thread.join()
        if not tripped.is_set():
            _check(config, stop, tripped)
