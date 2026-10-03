from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import archive as archive_module
from bitcoin_block_archive import cli, schedule, watchdog
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.state import already_archived
from tests.conftest import FakeClient, fake_header, write_block_file


@pytest.fixture(autouse=True)
def fast_disk_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog, "DISK_CHECK_INTERVAL", 0.01)


@pytest.mark.parametrize("stop_fails", [False, True])
def test_disk_guard_stops_core_while_upload_is_blocked_and_skips_next_file(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stop_fails: bool,
) -> None:
    uploading = threading.Event()
    stopped = threading.Event()
    for number in range(4):
        write_block_file(
            config.block_dir / f"blk{number:05d}.dat", [fake_header(number)]
        )
    monkeypatch.setattr(archive_module, "block_height", lambda *_: 1)
    monkeypatch.setattr(
        watchdog, "free_bytes", lambda _: 0 if uploading.is_set() else 10
    )

    def stop_core(_: Config) -> None:
        stopped.set()
        if stop_fails:
            raise ArchiveError("stop RPC unavailable")

    monkeypatch.setattr(watchdog, "stop_bitcoin", stop_core)

    class BlockedUpload(FakeClient):
        def upload(self, source: Path, destination: str) -> None:
            if source.name == "blk00000.dat":
                uploading.set()
                assert stopped.wait(1), "Core must stop before this upload returns"
            super().upload(source, destination)

    client = BlockedUpload()

    def upload(_: Config, *, cancelled: threading.Event | None = None) -> None:
        archive_module.archive(config, client, cancelled=cancelled)

    monkeypatch.setattr(cli, "archive", upload)
    monkeypatch.delenv("ARCHIVE_INTERVAL", raising=False)
    assert (
        cli.main(["--block-dir", str(config.block_dir), "--min-free-space", "1"]) == 1
    )
    assert stopped.is_set()
    assert len(client.uploads) == 2  # First file and its verified sidecar only.
    assert already_archived(config, config.block_dir / "blk00000.dat")
    assert not already_archived(config, config.block_dir / "blk00001.dat")
    if stop_fails:
        assert "stop RPC unavailable" in caplog.text


def test_disk_guard_interrupts_the_long_wait_between_passes(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = threading.Event()
    stopped = threading.Event()
    calls: list[str] = []

    class SchedulerStop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout == 1800
            waiting.set()
            assert super().wait(1), "Watchdog must wake the thirty-minute wait"
            return True

    monkeypatch.setattr(watchdog, "free_bytes", lambda _: 0 if waiting.is_set() else 10)
    monkeypatch.setattr(watchdog, "stop_bitcoin", lambda _: stopped.set())
    stop = SchedulerStop()

    def run_pass() -> bool:
        calls.append("pass")
        return True

    with watchdog.watch_disk(replace(config, min_free_space=1), stop) as tripped:
        schedule.run_repeatedly(run_pass, 1800, stop)

    assert tripped.is_set()
    assert stopped.is_set()
    assert calls == ["pass"]


def test_disabled_watchdog_never_checks_or_stops_core(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(_: Config) -> bool:
        pytest.fail("Disabled watchdog must not check disk space")

    monkeypatch.setattr(watchdog, "space_is_critical", unexpected)
    stop = threading.Event()
    with watchdog.watch_disk(config, stop) as tripped:
        assert not tripped.is_set()
    assert not stop.is_set()


def test_watchdog_checks_short_pass_on_exit(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    low = threading.Event()
    stopped: list[bool] = []
    monkeypatch.setattr(watchdog, "DISK_CHECK_INTERVAL", 30)
    monkeypatch.setattr(watchdog, "free_bytes", lambda _: 0 if low.is_set() else 10)
    monkeypatch.setattr(watchdog, "stop_bitcoin", lambda _: stopped.append(True))
    stop = threading.Event()
    with watchdog.watch_disk(replace(config, min_free_space=1), stop) as tripped:
        low.set()
    assert tripped.is_set()
    assert stop.is_set()
    assert stopped == [True]


def test_unreadable_free_space_requests_shutdown_and_reports_failure(
    config: Config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unreadable(_: Path) -> int:
        raise OSError("disk unavailable")

    stopped: list[bool] = []
    monkeypatch.setattr(watchdog, "free_bytes", unreadable)
    monkeypatch.setattr(watchdog, "stop_bitcoin", lambda _: stopped.append(True))
    with watchdog.watch_disk(
        replace(config, min_free_space=1), threading.Event()
    ) as tripped:
        assert tripped.is_set()
    assert stopped == [True]
    assert "disk unavailable" in caplog.text
