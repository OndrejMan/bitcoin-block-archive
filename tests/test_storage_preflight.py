from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "layout_status,probe_status,layout,signatures,accepted",
    [
        (0, 0, "fake disk", "", True),
        (0, 0, "fake disk", "ext4", False),
        (0, 0, "fake disk\nfake1 part", "", False),
        (1, 0, "", "", False),
        (0, 4, "fake disk", "", False),
        (0, 8, "fake disk", "ext4\nxfs", False),
    ],
)
def test_device_probe_errors_never_allow_formatting(
    layout_status: int,
    probe_status: int,
    layout: str,
    signatures: str,
    accepted: bool,
) -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts/init-bitcoin-data-volume.sh"
    ).read_text()
    # Execute only the read-only preflight, without root/device guards or
    # any partitioning, formatting, mounting or fstab-writing commands.
    start = script.index('if findmnt -rn -S "$device"')
    end = script.index('if mountpoint -q "$mountpoint"')
    preflight = script[start:end]
    stubs = r"""
set -Eeuo pipefail
device=/dev/test-only
findmnt() { return 1; }
lsblk() { printf '%s\n' "$TEST_LAYOUT"; return "$TEST_LAYOUT_STATUS"; }
blkid() {
    if (( TEST_PROBE_STATUS != 0 )); then return "$TEST_PROBE_STATUS"; fi
    [[ -n "$TEST_SIGNATURES" ]] || return 2
}
wipefs() { printf '%s' "$TEST_SIGNATURES"; return "$TEST_PROBE_STATUS"; }
"""
    result = subprocess.run(
        ["bash", "-c", stubs + preflight + "\necho PREFLIGHT_ACCEPTED\n"],
        env={
            **os.environ,
            "TEST_LAYOUT": layout,
            "TEST_LAYOUT_STATUS": str(layout_status),
            "TEST_SIGNATURES": signatures,
            "TEST_PROBE_STATUS": str(probe_status),
        },
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert (result.returncode == 0) is accepted
    assert ("PREFLIGHT_ACCEPTED" in result.stdout) is accepted
