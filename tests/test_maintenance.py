"""run_maintenance hardening: the concurrency lock and the invariant that
warnings reflect the post-cleanup state (a file trashed in this run must not
also fire a stray-file warning in the same entry)."""

from __future__ import annotations

import pytest

from lib.config import Config, UndhdError, Zones, default_cleanup
from lib.maintenance import initialize_state, run_maintenance


def managed_dir(tmp_path):
    root = tmp_path / "wd"
    for zone in ("raw", "scripts", "results"):
        (root / zone).mkdir(parents=True)
    (root / "scripts" / "run.sh").write_text("echo hi\n")
    (root / "raw" / "sample.fastq").write_text("ACGT\n")
    cfg = Config(
        name="t",
        created="2026-07-13",
        zones=Zones(input=["raw/"], scripts="scripts/", output="results/"),
        cleanup=default_cleanup("standard"),
    )
    cfg.save(root)
    initialize_state(root, cfg)
    return root, cfg


def test_stale_lock_blocks_run_with_clear_message(tmp_path):
    root, _ = managed_dir(tmp_path)
    lock = root / ".undhd" / ".lock"
    lock.write_text("12345")
    with pytest.raises(UndhdError, match="already in progress"):
        run_maintenance(root)
    lock.unlink()
    assert run_maintenance(root)["counts"] == {"added": 0, "removed": 0, "modified": 0}


def test_lock_released_after_normal_and_dry_runs(tmp_path):
    root, _ = managed_dir(tmp_path)
    run_maintenance(root)
    assert not (root / ".undhd" / ".lock").exists()
    run_maintenance(root, dry_run=True)
    assert not (root / ".undhd" / ".lock").exists()


def test_trashed_junk_not_flagged_stray_in_same_run(tmp_path):
    root, _ = managed_dir(tmp_path)
    (root / ".DS_Store").write_bytes(b"junk")  # temp pattern AND root-level stray
    summary = run_maintenance(root)
    assert any(".DS_Store" in action for action in summary["cleanup_actions"])
    assert not any(".DS_Store" in warning for warning in summary["warnings"])
    # ... and the trashed file is not re-reported as a user deletion tomorrow
    followup = run_maintenance(root)
    assert followup["counts"]["removed"] == 0


def test_dry_run_writes_nothing(tmp_path):
    root, _ = managed_dir(tmp_path)
    (root / ".DS_Store").write_bytes(b"junk")
    entry_files = sorted((root / ".undhd" / "history").glob("*.md"))
    before = [p.read_bytes() for p in entry_files]
    summary = run_maintenance(root, dry_run=True)
    assert "entry_preview" in summary
    assert (root / ".DS_Store").exists()  # cleanup planned, not performed
    assert [p.read_bytes() for p in sorted((root / ".undhd" / "history").glob("*.md"))] == before
