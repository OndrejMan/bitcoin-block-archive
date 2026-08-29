"""Free-space watchdog for the block directory."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from bitcoin_block_archive.errors import ArchiveError

SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)i?B?\s*$", re.I)
SIZE_UNITS = "KMGTP"


def parse_size(text: str) -> int:
    """Parse a binary size such as `20G`, `500M` or a plain byte count."""
    match = SIZE_PATTERN.match(text)

    if match is None:
        raise ArchiveError(f"cannot parse size: {text!r}")

    amount = float(match.group(1))
    unit = match.group(2).upper()

    if unit:
        amount *= 1024 ** (SIZE_UNITS.index(unit) + 1)

    return int(amount)


def format_size(size: int) -> str:
    value = float(size)

    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"

        value /= 1024

    raise AssertionError("unreachable")


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free
