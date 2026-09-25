from __future__ import annotations

import subprocess
import sys

import pytest

from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.process import run


def test_subprocess_timeout_stops_waiting() -> None:
    with pytest.raises(ArchiveError, match="time limit") as error:
        run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=1)
    assert isinstance(error.value.__cause__, subprocess.TimeoutExpired)


def test_subprocess_output_is_text() -> None:
    result = run([sys.executable, "-c", "print('ok')"], timeout=5)
    assert result.stdout == "ok\n"
    assert result.returncode == 0
