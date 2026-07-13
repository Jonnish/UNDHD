"""run_maintenance hardening (lock, post-cleanup warning state) and A7:
pending code-sync detection that records but never touches git."""

from __future__ import annotations

import subprocess

import pytest

from lib.config import Config, UndhdError, Zones, default_cleanup
from lib.maintenance import initialize_state, pending_code_sync, run_maintenance


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


# -- A7: pending code sync ----------------------------------------------------


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


def git_managed_dir(tmp_path):
    root, cfg = managed_dir(tmp_path)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "init")
    return root, cfg


def test_maintain_records_pending_sync_and_leaves_git_untouched(tmp_path):
    root, _ = git_managed_dir(tmp_path)
    (root / "scripts" / "run.sh").write_text("echo changed\n")
    head_before = _git_out(root, "rev-parse", "HEAD")

    summary = run_maintenance(root)

    assert summary["pending_sync"] == ["scripts/run.sh"]
    entry_text = (root / ".undhd" / "history").glob("*.md").__next__().read_text()
    assert "Pending code sync" in entry_text and "scripts/run.sh" in entry_text
    # never pushes, never commits, never stages
    assert _git_out(root, "rev-parse", "HEAD") == head_before
    assert " M scripts/run.sh" in _git_out(root, "status", "--porcelain")
    # ... and stays pending on later runs until approved (B7's "no" path)
    assert run_maintenance(root)["pending_sync"] == ["scripts/run.sh"]


def test_pending_sync_empty_when_disabled_or_unconfigured(tmp_path):
    root, cfg = git_managed_dir(tmp_path)
    (root / "scripts" / "run.sh").write_text("echo changed\n")

    cfg.git.sync_code = "off"
    assert pending_code_sync(root, cfg) == []

    cfg.git.sync_code = "ask"
    _git(root, "remote", "rename", "origin", "elsewhere")
    assert pending_code_sync(root, cfg) == []  # configured remote missing


def test_pending_sync_empty_outside_a_repo(tmp_path):
    root, cfg = managed_dir(tmp_path)
    (root / "scripts" / "run.sh").write_text("echo changed\n")
    assert pending_code_sync(root, cfg) == []
    assert run_maintenance(root)["pending_sync"] == []
