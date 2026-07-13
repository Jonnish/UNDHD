"""Regression tests for lib.diffs — fix-brief bug 1 (tamper evasion) and the
modification-rule invariants around it."""

from __future__ import annotations

import os

from lib import snapshot
from lib.config import Zones
from lib.diffs import diff_manifests
from lib.snapshot import take_snapshot

ZONES = Zones(input=["raw"], scripts="scripts", output="results")


def test_same_size_restored_mtime_rewrite_is_detected(tmp_path):
    """Fix-brief bug 1: overwrite with same-length content, restore mtime with
    os.utime — the hash must still flag the file as modified."""
    (tmp_path / "raw").mkdir()
    tampered = tmp_path / "raw" / "sample.fastq"
    tampered.write_text("original data")
    before = take_snapshot(tmp_path)

    st = tampered.stat()
    tampered.write_bytes(("TAMPERED!!!!!" + "X" * 20)[: st.st_size].encode())
    os.utime(tampered, (st.st_atime, st.st_mtime))
    after = take_snapshot(tmp_path)

    diff = diff_manifests(before, after, ZONES)
    assert [(c.path, c.zone) for c in diff.modified] == [("raw/sample.fastq", "input")]
    assert not diff.added and not diff.removed


def test_touch_without_content_change_is_not_modified(tmp_path):
    (tmp_path / "raw").mkdir()
    touched = tmp_path / "raw" / "sample.fastq"
    touched.write_text("stable data")
    before = take_snapshot(tmp_path)

    st = touched.stat()
    os.utime(touched, (st.st_atime + 100, st.st_mtime + 100))
    after = take_snapshot(tmp_path)

    assert diff_manifests(before, after, ZONES).is_empty()


def test_unhashed_files_fall_back_to_size_and_mtime(tmp_path, monkeypatch):
    """Files over the hash cap (simulated by shrinking it) still get change
    detection via size+mtime."""
    monkeypatch.setattr(snapshot, "HASH_MAX_BYTES", 1)
    (tmp_path / "results").mkdir()
    big = tmp_path / "results" / "coverage.bw"
    big.write_text("v1 content")
    before = take_snapshot(tmp_path)
    assert before["files"]["results/coverage.bw"]["sha256"] is None

    big.write_text("v2 content")  # same length
    st = big.stat()
    os.utime(big, (st.st_atime, st.st_mtime + 60))
    after = take_snapshot(tmp_path)

    diff = diff_manifests(before, after, ZONES)
    assert [c.path for c in diff.modified] == ["results/coverage.bw"]
