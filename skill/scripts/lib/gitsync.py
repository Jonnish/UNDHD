"""B6 — detect the repo behind the scripts zone; commit + push scripts-zone changes.

Never called automatically: `maintain` only ever records that changes are
pending (A7). This module is only exercised by the explicit `sync-code`
command, and even then only pushes when `dry_run=False` and `yes=True`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config, UndhdError

MAX_FILES_IN_MESSAGE = 5


class GitSyncError(UndhdError):
    pass


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
    )


def find_repo(scripts_dir: Path) -> Optional[Path]:
    """Return the git repo root containing `scripts_dir`, or None if there isn't one."""
    scripts_dir = Path(scripts_dir)
    if not scripts_dir.is_dir():
        return None
    proc = _git(scripts_dir, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def has_remote(repo_root: Path, remote: str) -> bool:
    proc = _git(repo_root, "remote", "get-url", remote)
    return proc.returncode == 0


def current_branch(repo_root: Path) -> str:
    proc = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip() == "HEAD":
        raise GitSyncError(
            "cannot determine current branch in %s (detached HEAD or empty repo)" % repo_root
        )
    return proc.stdout.strip()


def dirty_scripts_files(repo_root: Path, scripts_relpath: str) -> List[str]:
    """Paths (relative to repo_root) with pending changes under the scripts zone."""
    # --untracked-files=all: without it, a wholly-untracked directory collapses
    # to one "?? scripts/" line instead of listing the files inside it.
    proc = _git(repo_root, "status", "--porcelain", "--untracked-files=all", "--", scripts_relpath)
    if proc.returncode != 0:
        raise GitSyncError("git status failed in %s: %s" % (repo_root, proc.stderr.strip()))
    files = []
    for line in proc.stdout.splitlines():
        # porcelain format: "XY path" (rename entries use "XY old -> new"; take the new path)
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(files)


def build_commit_message(files: List[str]) -> str:
    names = [Path(f).name for f in files]
    shown = names[:MAX_FILES_IN_MESSAGE]
    suffix = "" if len(names) <= MAX_FILES_IN_MESSAGE else ", +%d more" % (len(names) - MAX_FILES_IN_MESSAGE)
    return "unDHD daily sync: %d script%s modified (%s%s)" % (
        len(files),
        "" if len(files) == 1 else "s",
        ", ".join(shown),
        suffix,
    )


def run_sync(root: Path, config: Config, dry_run: bool = False, yes: bool = False) -> Dict[str, Any]:
    """Detect pending scripts-zone changes and, if approved, commit + push them.

    Returns a summary dict; never raises for "nothing to do" — only for
    misconfiguration (no repo, no remote).
    """
    root = Path(root)
    if config.git.sync_code == "off":
        raise GitSyncError("code sync is disabled for this directory (git.sync_code = \"off\")")

    scripts_dir = root / config.zones.scripts
    repo_root = find_repo(scripts_dir)
    if repo_root is None:
        raise GitSyncError("no git repository found containing the scripts zone (%s)" % scripts_dir)
    if not has_remote(repo_root, config.git.remote):
        raise GitSyncError("git remote %r not found in %s" % (config.git.remote, repo_root))

    scripts_relpath = scripts_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    files = dirty_scripts_files(repo_root, scripts_relpath)

    summary: Dict[str, Any] = {
        "repo": str(repo_root),
        "remote": config.git.remote,
        "files": files,
        "dry_run": dry_run,
        "pushed": False,
    }
    if not files:
        summary["message"] = None
        return summary

    message = build_commit_message(files)
    summary["message"] = message

    if dry_run or not yes:
        summary["plan_only"] = True
        return summary

    branch = current_branch(repo_root)
    add = _git(repo_root, "add", "--", *files)
    if add.returncode != 0:
        raise GitSyncError("git add failed in %s: %s" % (repo_root, add.stderr.strip()))
    commit = _git(repo_root, "commit", "-m", message)
    if commit.returncode != 0:
        raise GitSyncError("git commit failed in %s: %s" % (repo_root, commit.stderr.strip()))
    push = _git(repo_root, "push", config.git.remote, branch)
    if push.returncode != 0:
        raise GitSyncError("git push failed in %s: %s" % (repo_root, push.stderr.strip()))

    summary["branch"] = branch
    summary["pushed"] = True
    return summary
