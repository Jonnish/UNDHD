"""A6/A7 — daily maintenance orchestrator and Day-0 state initialization.

Pipeline: acquire lock → snapshot → diff vs. previous manifest → cleanup
(Worker C's module, via a guarded import so this also runs without it) →
post-cleanup snapshot → warnings → history entry → save manifest.

The saved manifest and the warning checks both use the POST-cleanup state:
junk that cleanup just trashed is neither re-reported as a user deletion
tomorrow nor flagged as a stray file in today's entry.

A lock file (`.undhd/.lock`, O_CREAT|O_EXCL) keeps a manual run and a cron
tick from racing on manifest.json, today's history file, and the trash tree;
a concurrent invocation fails fast with a clear message.

Cleanup seam (C1 contract): `cleanup.run_cleanup(root, config, dry_run=False)
-> List[str]` returning one human-readable line per action (planned action when
dry_run). Until the module exists, maintenance skips cleanup and says so.

Code-sync state (A7): when `git.sync_code` != "off" and the scripts zone lives
in a git repo that has the configured remote, uncommitted changes under the
zone are listed in the history entry and in the summary as `pending_sync`.
Detection is read-only and persists across days until the user approves — the
actual commit+push only ever happens through `sync-code` (B6). A failing git
call downgrades to a warning instead of breaking the cron run.

`dry_run=True` writes nothing — no history entry, no manifest, no cleanup
moves — apart from the transient lock file; the rendered entry is returned in
the summary as `entry_preview`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from . import checks, gitsync, history
from .config import Config, UndhdError, undhd_dir
from .diffs import diff_manifests
from .snapshot import load_manifest, save_manifest, take_snapshot

try:  # Worker C's module (C1/C2); optional until it lands
    from . import cleanup as _cleanup
except ImportError:  # pragma: no cover
    _cleanup = None

CLEANUP_UNAVAILABLE_NOTE = "(cleanup module not available yet — cleanup skipped)"
LOCK_NAME = ".lock"


def _acquire_lock(root: Path) -> Path:
    lock = undhd_dir(root) / LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise UndhdError(
            "another unDHD run is already in progress (lock file %s exists; "
            "delete it if a previous run crashed)" % lock
        )
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    return lock


def _release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:  # pragma: no cover — nothing sane to do at teardown
        pass


def _run_cleanup(root: Path, config: Config, dry_run: bool):
    if _cleanup is None or not hasattr(_cleanup, "run_cleanup"):
        return [CLEANUP_UNAVAILABLE_NOTE], False
    return list(_cleanup.run_cleanup(root, config, dry_run=dry_run)), True


def pending_code_sync(root: Path, config: Config) -> List[str]:
    """A7 — files with uncommitted changes under the scripts zone, repo-relative.

    Read-only: never stages, commits, or pushes. Returns [] when sync is "off",
    the scripts zone is not inside a git repo, or the configured remote is
    missing (those are configurations, not errors). Git failures propagate as
    UndhdError/OSError for the caller to downgrade.
    """
    if config.git.sync_code == "off":
        return []
    scripts_dir = Path(root) / config.zones.scripts
    repo = gitsync.find_repo(scripts_dir)
    if repo is None or not gitsync.has_remote(repo, config.git.remote):
        return []
    scripts_rel = scripts_dir.resolve().relative_to(repo.resolve()).as_posix()
    return gitsync.dirty_scripts_files(repo, scripts_rel)


def initialize_state(root: Path, config: Config) -> Dict[str, Any]:
    """Day 0 (called by setup, B2): baseline snapshot + manifest + history entry.

    Assumes the config has already been saved via `Config.save(root)`.
    """
    root = Path(root)
    manifest = take_snapshot(root)
    save_manifest(root, manifest)
    entry = history.write_day0_entry(root, config, manifest)
    return {
        "files": len(manifest["files"]),
        "total_size": sum(e["size"] for e in manifest["files"].values()),
        "entry_path": str(entry),
    }


def run_maintenance(root: Path, dry_run: bool = False) -> Dict[str, Any]:
    """One maintenance pass. Returns a summary dict for the CLI/skill to print."""
    root = Path(root)
    config = Config.load(root)
    lock = _acquire_lock(root)
    try:
        return _run_locked(root, config, dry_run)
    finally:
        _release_lock(lock)


def _run_locked(root: Path, config: Config, dry_run: bool) -> Dict[str, Any]:
    previous = load_manifest(root)
    baseline = previous is None
    pre = take_snapshot(root)
    diff = diff_manifests(previous, pre, config.zones)

    actions, cleanup_ran = _run_cleanup(root, config, dry_run)
    moved = bool(cleanup_ran and actions and not dry_run)
    post = take_snapshot(root) if moved else pre

    warnings = checks.compute_warnings(diff, post, config)

    try:
        pending = pending_code_sync(root, config)
    except (UndhdError, OSError, ValueError) as exc:
        pending = []
        warnings = warnings + ["Code-sync check failed: %s" % exc]

    body = history.render_maintenance_body(
        diff,
        cleanup_actions=actions,
        warnings=warnings,
        pending_sync=pending,
        baseline=baseline,
        dry_run=dry_run,
    )

    summary: Dict[str, Any] = {
        "root": str(root),
        "dry_run": dry_run,
        "baseline": baseline,
        "counts": diff.counts(),
        "size_delta": diff.size_delta(),
        "total_size": diff.total_size_after,
        "cleanup_actions": actions,
        "cleanup_ran": cleanup_ran,
        "warnings": warnings,
        "pending_sync": pending,
    }

    if dry_run:
        summary["entry_preview"] = body
        return summary

    entry = history.write_maintenance_entry(root, body)
    save_manifest(root, post)
    summary["entry_path"] = str(entry)
    return summary
