from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import cleanup as cleanup_module
from lib.cleanup import run_cleanup
from lib.config import Config, ConfigError, UndhdError, Zones
from lib.diffs import diff_manifests
from lib.snapshot import take_snapshot


NOW = datetime(2026, 7, 13, 12, 0, 0)


def settings(**overrides):
    values = {
        "temp_patterns": ["*.tmp", "*~", ".DS_Store", "__pycache__", "*.swp"],
        "log_gzip_after_days": 7,
        "archive_output_after_days": 14,
        "trash_retention_days": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TrackingZones:
    input = ["raw"]
    scripts = "scripts"
    output = "results"

    def __init__(self):
        self.calls = []

    def zone_of(self, relative: str) -> str:
        self.calls.append(relative)
        first = relative.split("/", 1)[0]
        return {"raw": "input", "scripts": "scripts", "results": "output"}.get(first, "other")


def test_cleanup_delegates_safety_classification_to_zones(tmp_path: Path) -> None:
    zones = TrackingZones()
    config = SimpleNamespace(zones=zones, cleanup=settings())
    protected = tmp_path / "raw" / "protected.tmp"
    disposable = tmp_path / "results" / "disposable.tmp"
    protected.parent.mkdir()
    disposable.parent.mkdir()
    protected.write_text("input")
    disposable.write_text("output")

    run_cleanup(tmp_path, config, now=NOW)

    assert protected.exists()
    assert not disposable.exists()
    assert "raw/protected.tmp" in zones.calls
    assert "results/disposable.tmp" in zones.calls


def test_archive_zero_disables_archiving(tmp_path: Path) -> None:
    old_output = tmp_path / "results" / "old.bw"
    old_output.parent.mkdir()
    old_output.write_text("coverage")
    timestamp = (NOW - timedelta(days=60)).timestamp()
    os.utime(old_output, (timestamp, timestamp))
    config = SimpleNamespace(
        zones=TrackingZones(),
        cleanup=settings(archive_output_after_days=0),
    )

    actions = run_cleanup(tmp_path, config, now=NOW)

    assert old_output.exists()
    assert not any(action["action"] == "archive" for action in actions)


def test_existing_cleanup_lock_refuses_second_run(tmp_path: Path) -> None:
    state = tmp_path / ".undhd"
    state.mkdir()
    lock = state / ".lock"
    lock.write_text("another pid\n")

    with pytest.raises(UndhdError, match="already in progress"):
        run_cleanup(tmp_path, {"zones": {}, "cleanup": {}}, now=NOW)

    assert lock.read_text() == "another pid\n"


def test_cleanup_lock_is_released_after_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".undhd").mkdir()

    def fail(*args, **kwargs):
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(cleanup_module, "_trash_pass", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        run_cleanup(tmp_path, {"zones": {}, "cleanup": {}}, now=NOW)

    assert not (tmp_path / ".undhd" / ".lock").exists()


def test_archive_skips_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.dat"
    target.write_text("real data")
    link = tmp_path / "results" / "linked.dat"
    link.parent.mkdir()
    link.symlink_to(target)
    timestamp = (NOW - timedelta(days=60)).timestamp()
    os.utime(target, (timestamp, timestamp))
    config = SimpleNamespace(zones=TrackingZones(), cleanup=settings())

    actions = run_cleanup(tmp_path, config, now=NOW)

    assert link.is_symlink()
    assert target.read_text() == "real data"
    assert not any(action["action"] == "archive" for action in actions)


def test_tamper_evasion_is_detected_by_diff(tmp_path: Path) -> None:
    input_file = tmp_path / "raw" / "sample.fastq"
    input_file.parent.mkdir()
    input_file.write_bytes(b"AAAA")
    before = take_snapshot(tmp_path)
    original = input_file.stat()
    input_file.write_bytes(b"TTTT")
    os.utime(input_file, ns=(original.st_atime_ns, original.st_mtime_ns))
    after = take_snapshot(tmp_path)

    diff = diff_manifests(before, after, Zones(input=["raw"], scripts="scripts", output="results"))

    assert [change.path for change in diff.modified] == ["raw/sample.fastq"]


def test_backslash_zone_path_classifies_posix_path() -> None:
    zones = Zones(input=[r"raw\subdir"], scripts=r"code\pipeline", output=r"results\final")

    assert zones.zone_of("raw/subdir/sample.fastq") == "input"
    assert zones.zone_of("code/pipeline/align.sh") == "scripts"
    assert zones.zone_of("results/final/coverage.bw") == "output"


@pytest.mark.parametrize("unsafe", ["../etc", "/etc"])
def test_config_rejects_traversal_and_absolute_zones(unsafe: str) -> None:
    config = Config(
        name="unsafe",
        created="2026-07-13",
        zones=Zones(input=[unsafe], scripts="scripts", output="results"),
    )

    with pytest.raises(ConfigError):
        config.validate()


def test_snapshot_skips_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    manifest = take_snapshot(tmp_path)

    assert "target.txt" in manifest["files"]
    assert "link.txt" not in manifest["files"]
    assert {"path": "link.txt", "reason": "symlink"} in manifest["skipped"]
