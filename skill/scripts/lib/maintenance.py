"""A6 — daily maintenance orchestrator and Day-0 state initialization.

Pipeline: snapshot → diff vs. previous manifest → cleanup (Worker C's module,
via a guarded import so this runs before C1 lands) → warnings → history entry
→ save post-cleanup manifest. The saved manifest is taken AFTER cleanup so that
trashed junk is not re-reported as user deletions on the next run.

Cleanup seam (C1 contract): `cleanup.run_cleanup(root, config, dry_run=False)
-> List[str]` returning one human-readable line per action (planned action when
dry_run). Until the module exists, maintenance skips cleanup and says so.

`dry_run=True` is fully side-effect-free: no history entry, no manifest write,
and cleanup is invoked with dry_run so it only reports planned actions; the
rendered entry is returned in the summary as `entry_preview`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from . import checks, history
from .config import Config
from .diffs import diff_manifests
from .snapshot import load_manifest, save_manifest, take_snapshot

try:  # Worker C's module (C1/C2); optional until it lands
    from . import cleanup as _cleanup
except ImportError:  # pragma: no cover
    _cleanup = None

CLEANUP_UNAVAILABLE_NOTE = "(cleanup module not available yet — cleanup skipped)"


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

    previous = load_manifest(root)
    baseline = previous is None
    pre = take_snapshot(root)
    diff = diff_manifests(previous, pre, config.zones)

    actions, cleanup_ran = _run_cleanup(root, config, dry_run)
    warnings = checks.compute_warnings(diff, pre, config)

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
    # Re-snapshot only if cleanup actually moved things; otherwise `pre` is current.
    post = take_snapshot(root) if (cleanup_ran and actions) else pre
    save_manifest(root, post)
    summary["entry_path"] = str(entry)
    return summary
