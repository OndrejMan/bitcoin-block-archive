from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    commands = {
        "curl": """
import os
import sys
from pathlib import Path

url = next(arg for arg in sys.argv if arg.startswith("https://"))
if os.environ.get("DEPLOY_TEST_FAILURE") == "download":
    sys.exit(22)
output = Path(sys.argv[sys.argv.index("-o") + 1])
source = Path(os.environ["DEPLOY_TEST_REPO"]) / url.rsplit("/", 1)[-1]
output.write_bytes(source.read_bytes())
""",
        "docker": """
import json
import os
import sys
from pathlib import Path

with Path(os.environ["DEPLOY_TEST_LOG"]).open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("DEPLOY_TEST_FAILURE") in sys.argv[1:]:
    sys.exit(1)
""",
    }
    for name, body in commands.items():
        executable = binaries / name
        executable.write_text(f"#!{sys.executable}\n{body}")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DEPLOY_TEST_REPO", str(REPO))
    monkeypatch.setenv("DEPLOY_TEST_LOG", str(tmp_path / "commands.jsonl"))
    monkeypatch.delenv("DEPLOY_TEST_FAILURE", raising=False)
    return tmp_path / "vm with spaces"


def run_deploy(deployment: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REPO / "deploy.sh"), str(deployment)],
        capture_output=True,
        text=True,
        check=False,
    )


def docker_calls(deployment: Path) -> list[list[str]]:
    log = deployment.parent / "commands.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_first_run_creates_private_env_without_starting_services(
    deployment: Path,
) -> None:
    result = run_deploy(deployment)

    assert result.returncode == 1
    assert (deployment / ".env").read_bytes() == (REPO / ".env.example").read_bytes()
    assert stat.S_IMODE((deployment / ".env").stat().st_mode) == 0o600
    assert not (deployment / "compose.yaml").exists()
    assert not any("pull" in call or "up" in call for call in docker_calls(deployment))
    assert not list(deployment.glob(".deploy.*"))


def test_update_preserves_env_and_pulls_before_recreating_services(
    deployment: Path,
) -> None:
    deployment.mkdir()
    env = "S3_ACCESS_KEY_ID=existing-key\nBITCOIN_DATA_SOURCE=/mnt/bitcoin-data\n"
    (deployment / ".env").write_text(env)
    (deployment / "compose.yaml").write_text("old deployment\n")

    result = run_deploy(deployment)

    assert result.returncode == 0, result.stderr
    assert (deployment / ".env").read_text() == env
    assert (deployment / "compose.yaml").read_bytes() == (
        REPO / "compose.yaml"
    ).read_bytes()
    calls = docker_calls(deployment)
    pull = next(i for i, call in enumerate(calls) if "pull" in call)
    starts = [call for call in calls[pull + 1 :] if "up" in call]
    assert len(starts) == 2
    assert "--wait" in starts[0] and starts[0][-1] == "bitcoin-core"
    assert "--force-recreate" in starts[1]
    assert starts[1][-2:] == ["archiver", "dumplings"]
    for call in calls[1:]:
        assert call[call.index("--project-name") + 1] == "bitcoin-block-archive"
    assert not list(deployment.glob(".deploy.*"))


@pytest.mark.parametrize("failure", ["download", "config", "pull"])
def test_failed_preparation_keeps_existing_files_and_services(
    deployment: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    deployment.mkdir()
    (deployment / ".env").write_text("existing credentials\n")
    (deployment / "compose.yaml").write_text("existing configuration\n")
    monkeypatch.setenv("DEPLOY_TEST_FAILURE", failure)

    result = run_deploy(deployment)

    assert result.returncode != 0
    assert (deployment / ".env").read_text() == "existing credentials\n"
    assert (deployment / "compose.yaml").read_text() == "existing configuration\n"
    assert not any("up" in call for call in docker_calls(deployment))
    assert not list(deployment.glob(".deploy.*"))
