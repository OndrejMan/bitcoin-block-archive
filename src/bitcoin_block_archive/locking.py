"""Single-instance guard around the archival run."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    """Yield True when the lock was acquired, False when it is already held."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            yield False
            return

        try:
            yield True
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
