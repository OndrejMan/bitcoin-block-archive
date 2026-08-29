"""Archive completed Bitcoin Core blk*.dat files to S3-compatible storage."""

from __future__ import annotations

from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.errors import ArchiveError

__all__ = ["Config", "ArchiveError", "__version__"]

__version__ = "0.1.0"
