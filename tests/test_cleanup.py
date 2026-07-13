from __future__ import annotations

import gzip
import os
from datetime import datetime, timedelta
from pathlib import Path

from lib.cleanup import run_cleanup


NOW = datetime(2026, 7, 13, 12, 0, 0)


def config() -> dict:
    return {
        "zones": {"input": ["raw/"], "scripts": "scripts/", "output": "results/"},
        "cleanup": {
            "temp_patterns": ["*.tmp", "*~", ".DS_Store", "__pycache__", "*.swp"],
            "log_gzip_after_days": 7,
            "archive_output_after_days": 14,
            "trash_retention_days": 7,
        },
    }


def create_tree(root: Path) -> None:
    for directory in ("raw", "scripts", "results", "notes"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "raw" / "sample.fastq.gz").write_bytes(b"reads")
    (root / "results" / "scratch.tmp").write_bytes(b"temporary")
    (root / "notes" / "draft~").write_text("draft")
    (root / "notes" / "__pycache__").mkdir()
    (root / "notes" / "__pycache__" / "cache.pyc").write_bytes(b"cache")


def fingerprint(root: Path) -> list[tuple[str, str, bytes | None]]:
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        entries.append((relative, "dir" if path.is_dir() else "file", None if path.is_dir() else path.read_bytes()))
    return entries


def backdate(path: Path, days: int) -> None:
    timestamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_dry_run_reports_trash_without_changing_tree(tmp_path: Path) -> None:
    create_tree(tmp_path)
    before = fingerprint(tmp_path)

    actions = run_cleanup(tmp_path, config(), dry_run=True, now=NOW)

    assert fingerprint(tmp_path) == before
    assert [(item["action"], item["path"]) for item in actions] == [
        ("trash", "notes/__pycache__"),
        ("trash", "notes/draft~"),
        ("trash", "results/scratch.tmp"),
    ]


def test_real_trash_preserves_relative_paths_and_protected_zones(tmp_path: Path) -> None:
    create_tree(tmp_path)
    (tmp_path / "raw" / "do-not-touch.tmp").write_text("protected")
    (tmp_path / "scripts" / "do-not-touch.swp").write_text("protected")
    (tmp_path / "raw" / ".DS_Store").write_text("universal junk")

    actions = run_cleanup(tmp_path, config(), now=NOW)
    trash = tmp_path / ".undhd" / "trash" / "2026-07-13"

    assert (trash / "results" / "scratch.tmp").read_bytes() == b"temporary"
    assert (trash / "notes" / "draft~").read_text() == "draft"
    assert (trash / "notes" / "__pycache__" / "cache.pyc").read_bytes() == b"cache"
    assert (trash / "raw" / ".DS_Store").read_text() == "universal junk"
    assert (tmp_path / "raw" / "do-not-touch.tmp").exists()
    assert (tmp_path / "scripts" / "do-not-touch.swp").exists()
    assert sum(item["action"] == "trash" for item in actions) == 4


def test_trash_collision_never_overwrites_existing_file(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    source = tmp_path / "results" / "scratch.tmp"
    source.write_text("new")
    existing = tmp_path / ".undhd" / "trash" / "2026-07-13" / "results" / "scratch.tmp"
    existing.parent.mkdir(parents=True)
    existing.write_text("old")

    actions = run_cleanup(tmp_path, config(), now=NOW)

    assert existing.read_text() == "old"
    assert existing.with_name("scratch.tmp.1").read_text() == "new"
    trash_action = next(item for item in actions if item["action"] == "trash")
    assert trash_action["destination"].endswith("scratch.tmp.1")


def test_old_logs_are_gzipped_and_original_removed(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    log = tmp_path / "pipeline.log"
    log.write_bytes(b"alignment complete\n")
    backdate(log, 8)

    actions = run_cleanup(tmp_path, config(), now=NOW)

    assert not log.exists()
    with gzip.open(tmp_path / "pipeline.log.gz", "rb") as handle:
        assert handle.read() == b"alignment complete\n"
    assert any(item["action"] == "gzip" and item["path"] == "pipeline.log" for item in actions)


def test_logs_at_age_boundary_rotate(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    log = tmp_path / "boundary.log"
    log.write_text("old enough")
    backdate(log, 7)

    run_cleanup(tmp_path, config(), now=NOW)

    assert (tmp_path / "boundary.log.gz").exists()


def test_old_output_is_archived_by_mtime_month(tmp_path: Path) -> None:
    output = tmp_path / "results" / "sample-a"
    output.mkdir(parents=True)
    result = output / "coverage.bw"
    result.write_bytes(b"coverage")
    backdate(result, 20)

    actions = run_cleanup(tmp_path, config(), now=NOW)

    destination = tmp_path / "results" / "archive" / "2026-06" / "sample-a" / "coverage.bw"
    assert destination.read_bytes() == b"coverage"
    assert not result.exists()
    assert any(item["action"] == "archive" for item in actions)


def test_archive_tree_is_not_rearchived(tmp_path: Path) -> None:
    archived = tmp_path / "results" / "archive" / "2026-05" / "old.bw"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"already archived")
    backdate(archived, 60)

    actions = run_cleanup(tmp_path, config(), now=NOW)

    assert archived.exists()
    assert not any(item["action"] == "archive" for item in actions)


def test_trash_purge_timing_and_unrecognised_directories(tmp_path: Path) -> None:
    trash = tmp_path / ".undhd" / "trash"
    expired = trash / "2026-07-05"
    retained = trash / "2026-07-06"
    unknown = trash / "keep-me"
    for directory in (expired, retained, unknown):
        directory.mkdir(parents=True)
        (directory / "item").write_text("junk")
    (tmp_path / "results").mkdir()

    actions = run_cleanup(tmp_path, config(), now=NOW)

    assert not expired.exists()
    assert retained.exists()  # Exactly seven days old is still retained.
    assert unknown.exists()
    assert {item["path"] for item in actions if item["action"] == "purge"} == {
        ".undhd/trash/2026-07-05"
    }


def test_dry_run_does_not_gzip_archive_or_purge(tmp_path: Path) -> None:
    result = tmp_path / "results" / "old.txt"
    result.parent.mkdir()
    result.write_text("result")
    log = tmp_path / "old.log"
    log.write_text("log")
    expired = tmp_path / ".undhd" / "trash" / "2026-07-01"
    expired.mkdir(parents=True)
    (expired / "junk").write_text("junk")
    backdate(result, 20)
    backdate(log, 8)
    before = fingerprint(tmp_path)

    actions = run_cleanup(tmp_path, config(), dry_run=True, now=NOW)

    assert fingerprint(tmp_path) == before
    assert {item["action"] for item in actions} == {"gzip", "archive", "purge"}


def test_dry_run_actions_match_real_run_for_overlapping_ages(tmp_path: Path) -> None:
    old_log = tmp_path / "results" / "old.log"
    old_temp = tmp_path / "results" / "cache.tmp"
    old_temp.parent.mkdir()
    old_log.write_text("log")
    old_temp.write_text("temporary output")
    backdate(old_log, 20)
    backdate(old_temp, 20)

    planned = run_cleanup(tmp_path, config(), dry_run=True, now=NOW)
    actual = run_cleanup(tmp_path, config(), now=NOW)

    assert planned == actual
    assert [(item["action"], item["path"]) for item in actual] == [
        ("trash", "results/cache.tmp"),
        ("gzip", "results/old.log"),
    ]


def test_rejects_unsafe_zone_path(tmp_path: Path) -> None:
    bad_config = config()
    bad_config["zones"]["output"] = "../outside"

    try:
        run_cleanup(tmp_path, bad_config, now=NOW)
    except ValueError as error:
        assert "relative" in str(error)
    else:
        raise AssertionError("unsafe zone path was accepted")
