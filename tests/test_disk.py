from __future__ import annotations

import pytest

from bitcoin_s3_archive.disk import format_size, parse_size
from bitcoin_s3_archive.errors import ArchiveError


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("512", 512),
        ("1K", 1024),
        ("20G", 20 * 1024**3),
        ("1.5G", int(1.5 * 1024**3)),
        ("10GiB", 10 * 1024**3),
        (" 4 M ", 4 * 1024**2),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


def test_parse_size_rejects_nonsense() -> None:
    with pytest.raises(ArchiveError, match="cannot parse size"):
        parse_size("plenty")


def test_format_size() -> None:
    assert format_size(20 * 1024**3) == "20.0 GiB"
