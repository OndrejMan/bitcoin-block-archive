"""Entry point for `python -m bitcoin_s3_archive`."""

from __future__ import annotations

import sys

from bitcoin_s3_archive.cli import main

if __name__ == "__main__":
    sys.exit(main())
