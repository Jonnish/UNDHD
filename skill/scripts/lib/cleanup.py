"""Safe, deterministic cleanup operations for managed unDHD directories."""

from __future__ import annotations

import fnmatch
import gzip
import os
import shutil
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any


UNIVERSAL_TEMP_PATTERNS = {".DS_Store"}


class CleanupAction(str):
    """Human-readable history line with structured fields for callers and tests."""

    details: dict[str, str]

    def __new__(
        cls,
        kind: str,
        path: str,
        destination: str | None = None,
    ) -> "CleanupAction":
        verb = {
            "trash": "Moved to trash",
            "gzip": "Gzipped",
            "archive": "Archived",
            "purge": "Purged trash",
        }[kind]
        message = f"{verb}: `{path}`"
        if destination is not None:
            message += f" → `{destination}`"
        instance = super().__new__(cls, message)
        instance.details = {"action": kind, "path": path}
        if destination is not None:
            instance.details["destination"] = destination
        return instance

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.details[key]
        return super().__getitem__(key)


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalise_relative(path: str | Path) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"zone paths must be relative to the managed root: {path}")
    return relative


def _zone_paths(root: Path, config: Any) -> tuple[list[Path], Path, Path]:
    zones = _value(config, "zones", {})
    inputs = [_normalise_relative(item) for item in _value(zones, "input", [])]
    scripts = _normalise_relative(_value(zones, "scripts", "scripts/"))
    output = _normalise_relative(_value(zones, "output", "results/"))
    return [root / item for item in inputs], root / scripts, root / output


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _matches(path: Path, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path.name, pattern) for pattern in patterns)


def _has_matching_component(path: Path, root: Path, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(component, pattern)
        for component in path.relative_to(root).parts
        for pattern in patterns
    )


def _collision_free(destination: Path) -> Path:
    if not destination.exists():
        return destination
    index = 1
    while True:
        candidate = destination.with_name(f"{destination.name}.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _action(kind: str, root: Path, source: Path, destination: Path | None = None) -> CleanupAction:
    return CleanupAction(
        kind,
        _relative(source, root),
        _relative(destination, root) if destination is not None else None,
    )


def _trash_pass(
    root: Path,
    config: Any,
    trash_day: date,
    dry_run: bool,
) -> list[dict[str, str]]:
    cleanup_config = _value(config, "cleanup", {})
    patterns = list(_value(cleanup_config, "temp_patterns", []))
    inputs, scripts, _ = _zone_paths(root, config)
    protected = inputs + [scripts]
    trash_root = root / ".undhd" / "trash" / trash_day.isoformat()
    actions: list[dict[str, str]] = []

    # topdown lets us prune a matching directory after planning one move.
    for current, directories, files in os.walk(root, topdown=True):
        current_path = Path(current)
        if current_path == root:
            directories[:] = [item for item in directories if item != ".undhd"]
        directories.sort()
        files.sort()

        for name in list(directories):
            source = current_path / name
            if not _matches(source, patterns):
                continue
            in_protected_zone = any(_is_within(source, zone) for zone in protected)
            if in_protected_zone and not _matches(source, list(UNIVERSAL_TEMP_PATTERNS)):
                continue
            destination = _collision_free(trash_root / source.relative_to(root))
            actions.append(_action("trash", root, source, destination))
            directories.remove(name)
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))

        for name in files:
            source = current_path / name
            if not _matches(source, patterns):
                continue
            in_protected_zone = any(_is_within(source, zone) for zone in protected)
            if in_protected_zone and not _matches(source, list(UNIVERSAL_TEMP_PATTERNS)):
                continue
            destination = _collision_free(trash_root / source.relative_to(root))
            actions.append(_action("trash", root, source, destination))
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))

    return actions


def _old_enough(path: Path, now: datetime, days: int) -> bool:
    return now.timestamp() - path.stat().st_mtime >= days * 86_400


def _rotate_logs(root: Path, config: Any, now: datetime, dry_run: bool) -> list[dict[str, str]]:
    cleanup_config = _value(config, "cleanup", {})
    after_days = int(_value(cleanup_config, "log_gzip_after_days", 7))
    temp_patterns = list(_value(cleanup_config, "temp_patterns", []))
    inputs, scripts, _ = _zone_paths(root, config)
    protected = inputs + [scripts]
    actions: list[dict[str, str]] = []

    for source in sorted(root.rglob("*.log")):
        if _is_within(source, root / ".undhd") or any(
            _is_within(source, zone) for zone in protected
        ):
            continue
        if _has_matching_component(source, root, temp_patterns):
            continue
        if not source.is_file() or not _old_enough(source, now, after_days):
            continue
        destination = _collision_free(source.with_suffix(source.suffix + ".gz"))
        actions.append(_action("gzip", root, source, destination))
        if dry_run:
            continue
        source_stat = source.stat()
        with source.open("rb") as input_handle, destination.open("wb") as raw_output:
            with gzip.GzipFile(
                filename=source.name,
                mode="wb",
                fileobj=raw_output,
                mtime=int(source_stat.st_mtime),
            ) as output_handle:
                shutil.copyfileobj(input_handle, output_handle)
        os.utime(destination, (source_stat.st_atime, source_stat.st_mtime))
        source.unlink()
    return actions


def _archive_outputs(root: Path, config: Any, now: datetime, dry_run: bool) -> list[dict[str, str]]:
    cleanup_config = _value(config, "cleanup", {})
    after_days = int(_value(cleanup_config, "archive_output_after_days", 14))
    temp_patterns = list(_value(cleanup_config, "temp_patterns", []))
    _, _, output = _zone_paths(root, config)
    archive_root = output / "archive"
    actions: list[dict[str, str]] = []
    if not output.exists():
        return actions

    for source in sorted(path for path in output.rglob("*") if path.is_file()):
        if _is_within(source, archive_root) or not _old_enough(source, now, after_days):
            continue
        if source.name.endswith((".log", ".log.gz")):
            continue
        if _has_matching_component(source, output, temp_patterns):
            continue
        modified = datetime.fromtimestamp(source.stat().st_mtime)
        month_root = archive_root / modified.strftime("%Y-%m")
        destination = _collision_free(month_root / source.relative_to(output))
        actions.append(_action("archive", root, source, destination))
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
    return actions


def _purge_trash(root: Path, config: Any, today: date, dry_run: bool) -> list[dict[str, str]]:
    cleanup_config = _value(config, "cleanup", {})
    retention_days = int(_value(cleanup_config, "trash_retention_days", 7))
    trash_root = root / ".undhd" / "trash"
    actions: list[dict[str, str]] = []
    if not trash_root.exists():
        return actions

    for source in sorted(trash_root.iterdir()):
        if not source.is_dir():
            continue
        try:
            trash_date = date.fromisoformat(source.name)
        except ValueError:
            continue
        if (today - trash_date).days <= retention_days:
            continue
        actions.append(_action("purge", root, source))
        if not dry_run:
            shutil.rmtree(source)
    return actions


def run_cleanup(
    root: str | Path,
    config: Any,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Run all cleanup passes and return actions suitable for history rendering.

    ``config`` may be the JSON-like mapping from TODO.md or Worker A's dataclass.
    Supplying ``now`` makes age-sensitive behavior deterministic in tests.
    """

    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"managed root is not a directory: {root_path}")
    current = now or datetime.now()
    actions: list[dict[str, str]] = []
    actions.extend(_trash_pass(root_path, config, current.date(), dry_run))
    actions.extend(_rotate_logs(root_path, config, current, dry_run))
    actions.extend(_archive_outputs(root_path, config, current, dry_run))
    actions.extend(_purge_trash(root_path, config, current.date(), dry_run))
    return actions


# Compatibility names for the maintenance orchestrator while tracks integrate.
cleanup = run_cleanup
perform_cleanup = run_cleanup


__all__ = ["cleanup", "perform_cleanup", "run_cleanup"]
