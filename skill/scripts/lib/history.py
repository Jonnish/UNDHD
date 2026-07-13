"""A4 — daily history log: one markdown file per day under `.undhd/history/`.

Each maintenance run appends a timestamped `## HH:MM — ...` section to today's
file, so a second run on the same day extends it rather than overwriting.
Setup writes a special "Day 0 — initialized" section.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import Config, undhd_dir
from .diffs import CHANGE_KINDS, DiffResult
from .snapshot import total_size

HISTORY_DIRNAME = "history"
MAX_DETAIL = 20  # per change kind, per entry

_KIND_MARK = {"added": "+", "removed": "−", "modified": "~"}


def human_size(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return "%s%d B" % (sign, int(value))
            return "%s%.1f %s" % (sign, value, unit)
        value /= 1024
    return "%s%d B" % (sign, n)  # unreachable, keeps type checkers calm


def history_dir(root: Path) -> Path:
    return undhd_dir(root) / HISTORY_DIRNAME


def entry_path(root: Path, day: Optional[str] = None) -> Path:
    day = day or date.today().isoformat()
    return history_dir(root) / ("%s.md" % day)


def append_section(root: Path, heading: str, body: str, now: Optional[datetime] = None) -> Path:
    """Append a `## HH:MM — heading` section to today's file, creating it if needed."""
    now = now or datetime.now()
    path = entry_path(root, now.date().isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    if not path.exists():
        parts.append("# unDHD history — %s\n" % now.date().isoformat())
    parts.append("\n## %s — %s\n\n%s\n" % (now.strftime("%H:%M"), heading, body.rstrip()))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return path


# -- rendering ---------------------------------------------------------------


def _details(changes, kind: str) -> List[str]:
    lines = []
    mark = _KIND_MARK[kind]
    for change in changes[:MAX_DETAIL]:
        lines.append("- %s `%s` (%s)" % (mark, change.path, human_size(change.size)))
    hidden = len(changes) - MAX_DETAIL
    if hidden > 0:
        lines.append("- … and %d more" % hidden)
    return lines


def render_diff(diff: DiffResult) -> str:
    """Per-zone counts table + capped per-file details."""
    if diff.is_empty():
        return "No file changes since the last run.\n"
    by_zone = diff.by_zone()
    counts = diff.counts()
    lines = [
        "%d added · %d removed · %d modified · Δ %s (total %s)"
        % (
            counts["added"],
            counts["removed"],
            counts["modified"],
            human_size(diff.size_delta()),
            human_size(diff.total_size_after),
        ),
        "",
        "| zone | added | removed | modified |",
        "|------|------:|--------:|---------:|",
    ]
    for zone, kinds in by_zone.items():
        lines.append(
            "| %s | %d | %d | %d |" % (zone, len(kinds["added"]), len(kinds["removed"]), len(kinds["modified"]))
        )
    for kind in CHANGE_KINDS:
        changes = getattr(diff, kind)
        if changes:
            lines += ["", "**%s (%d)**" % (kind.capitalize(), len(changes))] + _details(changes, kind)
    return "\n".join(lines) + "\n"


def render_maintenance_body(
    diff: DiffResult,
    cleanup_actions: Sequence[str] = (),
    warnings: Sequence[str] = (),
    pending_sync: Optional[Sequence[str]] = None,
    baseline: bool = False,
    dry_run: bool = False,
) -> str:
    parts = []
    if dry_run:
        parts.append("_Dry run — nothing below was written or moved._\n")
    if baseline:
        parts.append("_No previous manifest — this entry establishes the baseline._\n")
    parts.append(render_diff(diff))

    parts.append("### Cleanup\n")
    if cleanup_actions:
        parts.append("\n".join("- %s" % a for a in cleanup_actions) + "\n")
    else:
        parts.append("No cleanup actions.\n")

    if warnings:
        parts.append("### ⚠ Warnings\n")
        parts.append("\n".join("- ⚠ %s" % w for w in warnings) + "\n")

    if pending_sync:
        parts.append("### Pending code sync\n")
        parts.append(
            "%d changed file(s) in the scripts zone await approval:\n" % len(pending_sync)
            + "\n".join("- `%s`" % p for p in pending_sync)
            + "\n\nApprove via the skill, or run `undhd.py sync-code --root . --yes`.\n"
        )
    return "\n".join(parts)


def write_maintenance_entry(root: Path, body: str, now: Optional[datetime] = None) -> Path:
    return append_section(root, "Maintenance run", body, now=now)


def write_day0_entry(root: Path, config: Config, manifest: Dict, now: Optional[datetime] = None) -> Path:
    zones = config.zones
    body = "\n".join(
        [
            "unDHD now manages this directory.",
            "",
            "- **work**: %s" % (config.work or "(not described)"),
            "- **input**: %s" % ", ".join("`%s`" % z for z in zones.input),
            "- **scripts**: `%s`" % zones.scripts,
            "- **output**: `%s`" % zones.output,
            "- **cleanup policy**: %s" % config.cleanup.policy,
            "- **code sync**: %s" % config.git.sync_code,
            "",
            "Baseline: %d files, %s total." % (len(manifest["files"]), human_size(total_size(manifest))),
        ]
    )
    return append_section(root, "Day 0 — initialized", body, now=now)


# -- reading (for the status/history CLI, B4) --------------------------------


def list_days(root: Path) -> List[str]:
    """Days with history entries, newest first."""
    hd = history_dir(root)
    if not hd.is_dir():
        return []
    return sorted((p.stem for p in hd.glob("*.md")), reverse=True)


def read_day(root: Path, day: str) -> Optional[str]:
    path = entry_path(root, day)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def last_days(root: Path, days: int) -> List[str]:
    """Concatenable texts of the most recent `days` entries, newest first."""
    texts = []
    for day in list_days(root)[: max(days, 0)]:
        text = read_day(root, day)
        if text:
            texts.append(text)
    return texts
