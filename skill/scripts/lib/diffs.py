"""A3 — diff two manifests into added/removed/modified, aggregated per zone.

Modification rule: when both sides have a sha256, the hashes alone decide — a
same-size rewrite with a restored mtime is still modified, and a bare `touch`
is not. Size+mtime is only trusted when a hash is missing on either side
(>= 50 MB files, or an entry that predates hashing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import Zones
from .snapshot import total_size

ZONE_ORDER = ("input", "scripts", "output", "other")
CHANGE_KINDS = ("added", "removed", "modified")


@dataclass
class FileChange:
    path: str
    zone: str
    size: int  # new size for added/modified, old size for removed


@dataclass
class DiffResult:
    added: List[FileChange] = field(default_factory=list)
    removed: List[FileChange] = field(default_factory=list)
    modified: List[FileChange] = field(default_factory=list)
    total_size_before: int = 0
    total_size_after: int = 0

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    def counts(self) -> Dict[str, int]:
        return {"added": len(self.added), "removed": len(self.removed), "modified": len(self.modified)}

    def size_delta(self) -> int:
        return self.total_size_after - self.total_size_before

    def by_zone(self) -> Dict[str, Dict[str, List[FileChange]]]:
        """{zone: {added: [...], removed: [...], modified: [...]}} for zones with activity."""
        zones: Dict[str, Dict[str, List[FileChange]]] = {}
        for kind in CHANGE_KINDS:
            for change in getattr(self, kind):
                zones.setdefault(change.zone, {k: [] for k in CHANGE_KINDS})[kind].append(change)
        return {z: zones[z] for z in ZONE_ORDER if z in zones}

    def in_zone(self, zone: str) -> List[FileChange]:
        return [c for kind in CHANGE_KINDS for c in getattr(self, kind) if c.zone == zone]


def _same(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    # Hashes are authoritative whenever both sides have one: checking size+mtime
    # first would wave through a same-length rewrite whose mtime was restored
    # with os.utime (fix-brief bug 1 — tamper evasion on input files).
    if old.get("sha256") is not None and new.get("sha256") is not None:
        return old["sha256"] == new["sha256"]
    return old["size"] == new["size"] and old["mtime"] == new["mtime"]


def diff_manifests(
    old: Optional[Dict[str, Any]],
    new: Dict[str, Any],
    zones: Zones,
) -> DiffResult:
    """Diff `old` -> `new`. `old` may be None (first run): everything counts as added."""
    old_files: Dict[str, Any] = old["files"] if old else {}
    new_files: Dict[str, Any] = new["files"]
    result = DiffResult(total_size_before=total_size(old), total_size_after=total_size(new))

    for path in sorted(new_files):
        entry = new_files[path]
        if path not in old_files:
            result.added.append(FileChange(path, zones.zone_of(path), entry["size"]))
        elif not _same(old_files[path], entry):
            result.modified.append(FileChange(path, zones.zone_of(path), entry["size"]))
    for path in sorted(old_files):
        if path not in new_files:
            result.removed.append(FileChange(path, zones.zone_of(path), old_files[path]["size"]))
    return result
