"""A2 — walk the workdir into a manifest, save/load `.undhd/manifest.json`.

Manifest schema (TODO.md §2), plus an additive `skipped` list for entries the
walk could not or should not record (symlinks, sockets, unreadable files):

    {"taken_at": "...", "files": {rel: {"size": int, "mtime": int, "sha256": str|null}},
     "skipped": [{"path": rel, "reason": str}]}

`sha256` is null for files >= 50 MB — genomics files are tracked by size+mtime only.
`.undhd/` and `.git/` directories are skipped at any depth: the history log tracks
the user's files, not our own state or git internals.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import UndhdError, atomic_write_text, undhd_dir

MANIFEST_NAME = "manifest.json"
DEFAULT_EXCLUDES = (".undhd", ".git")
HASH_MAX_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


def manifest_path(root: Path) -> Path:
    return undhd_dir(root) / MANIFEST_NAME


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def take_snapshot(root: Path, excludes=DEFAULT_EXCLUDES) -> Dict[str, Any]:
    root = Path(root)
    if not root.is_dir():
        raise UndhdError("cannot snapshot %s: not a directory" % root)
    files: Dict[str, Dict[str, Any]] = {}
    skipped = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excludes)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            try:
                st = os.lstat(p)
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable (%s)" % exc.strerror})
                continue
            if stat.S_ISLNK(st.st_mode):
                skipped.append({"path": rel, "reason": "symlink"})
                continue
            if not stat.S_ISREG(st.st_mode):
                skipped.append({"path": rel, "reason": "special file"})
                continue
            sha: Optional[str] = None
            if st.st_size < HASH_MAX_BYTES:
                try:
                    sha = _sha256(p)
                except OSError as exc:
                    skipped.append({"path": rel, "reason": "unreadable (%s)" % exc.strerror})
                    continue
            files[rel] = {"size": st.st_size, "mtime": int(st.st_mtime), "sha256": sha}
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "skipped": skipped,
    }


def total_size(manifest: Optional[Dict[str, Any]]) -> int:
    if not manifest:
        return 0
    return sum(entry["size"] for entry in manifest["files"].values())


def save_manifest(root: Path, manifest: Dict[str, Any]) -> Path:
    path = manifest_path(root)
    atomic_write_text(path, json.dumps(manifest, indent=1) + "\n")
    return path


def load_manifest(root: Path) -> Optional[Dict[str, Any]]:
    """Return the last saved manifest, or None if this directory has none yet."""
    path = manifest_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UndhdError("corrupt manifest %s (%s) — delete it to re-baseline" % (path, exc))
    if not isinstance(data, dict) or "files" not in data:
        raise UndhdError("corrupt manifest %s (missing 'files') — delete it to re-baseline" % path)
    return data
