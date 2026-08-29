#!/usr/bin/env python3
"""Compatibility shim: `python3 script.py` still runs the archiver.

The implementation now lives in `src/bitcoin_block_archive/`; install the
project (`pip install -e .`) and use the `bitcoin-block-archive` command.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bitcoin_block_archive.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
