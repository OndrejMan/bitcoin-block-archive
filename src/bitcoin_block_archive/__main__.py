"""Entry point for `python -m bitcoin_block_archive`."""

from __future__ import annotations

import sys

from bitcoin_block_archive.cli import main

if __name__ == "__main__":
    sys.exit(main())
