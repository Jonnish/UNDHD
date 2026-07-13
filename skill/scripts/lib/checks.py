"""A5 — warning checks surfaced in the daily history entry.

Three checks (TODO.md task A5):
  * new/modified files larger than `cleanup.large_file_warn_mb`
  * any change inside an input zone (inputs are supposed to be immutable)
  * stray files sitting directly in the workdir root (current state, not just new)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import Config
from .diffs import DiffResult
from .history import human_size

MAX_PER_CHECK = 10


def _cap(lines: List[str], label: str) -> List[str]:
    if len(lines) > MAX_PER_CHECK:
        return lines[:MAX_PER_CHECK] + ["… and %d more %s" % (len(lines) - MAX_PER_CHECK, label)]
    return lines


def compute_warnings(diff: DiffResult, manifest: Dict[str, Any], config: Config) -> List[str]:
    warnings: List[str] = []

    threshold = config.cleanup.large_file_warn_mb * 1024 * 1024
    large = [
        "Large file: `%s` (%s)" % (c.path, human_size(c.size))
        for c in diff.added + diff.modified
        if c.size > threshold
    ]
    warnings += _cap(large, "large files")

    input_changes = []
    for kind in ("added", "removed", "modified"):
        input_changes += ["Input zone %s: `%s`" % (kind, c.path) for c in getattr(diff, kind) if c.zone == "input"]
    warnings += _cap(input_changes, "input-zone changes")

    strays = [
        "Stray file in workdir root: `%s`" % path
        for path in sorted(manifest["files"])
        if "/" not in path and config.zones.zone_of(path) == "other"
    ]
    warnings += _cap(strays, "stray files")

    return warnings
