"""Cross-track CLI checks activated when Workers A/B's entrypoint is present."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI = REPOSITORY_ROOT / "skill" / "scripts" / "undhd.py"


def require_cli() -> None:
    if not CLI.exists():
        pytest.skip("Worker B CLI has not been integrated yet")


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def setup_arguments(root: Path) -> list[str]:
    return [
        "setup",
        "--root",
        str(root),
        "--input",
        "raw",
        "--scripts",
        "scripts",
        "--output",
        "results",
        "--work",
        "Align reads",
        "--policy",
        "standard",
    ]


def test_setup_is_idempotent_and_refuses_to_clobber(tmp_path: Path) -> None:
    require_cli()
    first = run_cli(*setup_arguments(tmp_path))
    assert first.returncode == 0, first.stderr
    original = (tmp_path / ".undhd" / "config.json").read_bytes()

    second = run_cli(*setup_arguments(tmp_path))

    assert second.returncode != 0
    assert (tmp_path / ".undhd" / "config.json").read_bytes() == original


def test_maintenance_records_add_remove_and_modify(tmp_path: Path) -> None:
    require_cli()
    for directory in ("raw", "scripts", "results"):
        (tmp_path / directory).mkdir()
    (tmp_path / "raw" / "existing.fastq").write_text("ACGT")
    modified = tmp_path / "scripts" / "pipeline.sh"
    modified.write_text("version one")
    removed = tmp_path / "results" / "obsolete.txt"
    removed.write_text("old output")
    setup = run_cli(*setup_arguments(tmp_path))
    assert setup.returncode == 0, setup.stderr

    (tmp_path / "raw" / "new.fastq").write_text("TGCA")
    modified.write_text("version two with a different size")
    removed.unlink()
    maintained = run_cli("maintain", "--root", str(tmp_path))
    assert maintained.returncode == 0, maintained.stderr

    history = "\n".join(
        path.read_text() for path in sorted((tmp_path / ".undhd" / "history").glob("*.md"))
    ).lower()
    for expected in ("new.fastq", "pipeline.sh", "obsolete.txt", "added", "modified", "removed"):
        assert expected in history


def test_malformed_config_returns_clear_error(tmp_path: Path) -> None:
    require_cli()
    setup = run_cli(*setup_arguments(tmp_path))
    assert setup.returncode == 0, setup.stderr
    config_path = tmp_path / ".undhd" / "config.json"
    data = json.loads(config_path.read_text())
    data["cleanup"]["policy"] = "reckless"
    config_path.write_text(json.dumps(data))

    result = run_cli("status", "--root", str(tmp_path))

    assert result.returncode != 0
    assert "policy" in (result.stderr + result.stdout).lower()
