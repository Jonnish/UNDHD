"""A6 — daily maintenance orchestrator and Day-0 state initialization.

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

`dry_run=True` writes nothing — no history entry, no manifest, no cleanup
moves — apart from the transient lock file; the rendered entry is returned in
the summary as `entry_preview`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from . import checks, history
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

    body = history.render_maintenance_body(
        diff,
        cleanup_actions=actions,
        warnings=warnings,
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
    }

    if dry_run:
        summary["entry_preview"] = body
        return summary

    entry = history.write_maintenance_entry(root, body)
    save_manifest(root, post)
    summary["entry_path"] = str(entry)
    return summary
