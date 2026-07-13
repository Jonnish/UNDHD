"""Regression tests for lib.config — fix-brief bug 2 (backslash zone paths),
bug 3 (Windows absolute paths accepted on POSIX), and the protections the
review confirmed must not regress."""

from __future__ import annotations

import json

import pytest

from lib.config import Config, ConfigError, Zones

EXAMPLE = {
    "name": "rnaseq-june",
    "created": "2026-07-13",
    "zones": {"input": ["raw/"], "scripts": "scripts/", "output": "results/"},
    "work": "Align FASTQ files and compute coverage tracks",
    "cleanup": {
        "policy": "standard",
        "temp_patterns": ["*.tmp", "*~", ".DS_Store", "__pycache__", "*.swp"],
        "log_gzip_after_days": 7,
        "archive_output_after_days": 14,
        "trash_retention_days": 7,
        "large_file_warn_mb": 1024,
    },
    "git": {"sync_code": "ask", "remote": "origin"},
}


def _example(**zone_overrides):
    data = json.loads(json.dumps(EXAMPLE))
    data["zones"].update(zone_overrides)
    return data


def test_backslash_zone_classifies_posix_relpaths():
    """Fix-brief bug 2: a Windows-style zone path must match real snapshot paths."""
    zones = Zones(input=["raw\\subdir"], scripts="scripts", output="results")
    assert zones.zone_of("raw/subdir/file.txt") == "input"


def test_backslash_zones_normalized_once_at_load():
    cfg = Config.from_dict(_example(input=["raw\\subdir"], scripts="code\\bin"))
    assert cfg.zones.input == ["raw/subdir"]
    assert cfg.zones.scripts == "code/bin"
    # ... and stay consistent on the next save
    assert cfg.to_dict()["zones"]["input"] == ["raw/subdir"]


@pytest.mark.parametrize(
    "bad",
    ["C:\\Users\\me\\data", "C:/Users/me/data", "\\\\server\\share", "//server/share"],
)
def test_windows_absolute_zone_rejected_on_any_os(bad):
    """Fix-brief bug 3: drive-letter/UNC paths must fail validation on POSIX hosts too."""
    with pytest.raises(ConfigError, match="relative path inside"):
        Config.from_dict(_example(output=bad))


@pytest.mark.parametrize("bad", ["/etc/passwd", "../etc", "..\\etc", "raw/../../etc"])
def test_posix_absolute_and_traversal_still_rejected(bad):
    with pytest.raises(ConfigError, match="relative path inside"):
        Config.from_dict(_example(output=bad))


def test_frozen_example_schema_round_trips_exactly():
    assert Config.from_dict(json.loads(json.dumps(EXAMPLE))).to_dict() == EXAMPLE
