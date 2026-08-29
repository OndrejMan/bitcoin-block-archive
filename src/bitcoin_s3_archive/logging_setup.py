"""Central logger for the archiver."""

from __future__ import annotations

import logging

LOG = logging.getLogger("bitcoin-s3-archive")


def configure(*, verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
