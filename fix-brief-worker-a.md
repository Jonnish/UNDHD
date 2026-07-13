# Fix brief — Worker A (config.py / diffs.py)

Paste this whole file to Claude Code in your working copy of `Jonnish/UNDHD` and say:
**"Implement these fixes, then run the existing manual repro steps below to confirm each one."**

Found by an automated review that actually executed the library (not just read it) against a
simulated bioinformatics workdir. All three bugs are confirmed with a repro, not speculative.

---

## Bug 1 (🔴 security/correctness) — input files can be tampered with undetected

**File:** `skill/scripts/lib/diffs.py`, function `_same()`

**What's wrong:** A file is treated as unchanged if `size` and `mtime` match — hashes are only
compared when *both* the old and new snapshot happen to have one. Since `snapshot.py` always
computes a fresh sha256 for the *new* snapshot (files < 50MB), the hash is available on the new
side even when we skip it. Result: overwrite a file with different content but the same length,
then `os.utime()` it back to the original mtime, and the diff shows **no change at all** — not
`modified`, no warning. That defeats the project's own stated goal that input-zone files are
"supposed to be immutable" (see `checks.py` A5).

**Repro (paste into a Python shell from the repo root):**
```python
import sys, os
sys.path.insert(0, "skill/scripts")
from pathlib import Path
from lib.snapshot import take_snapshot
from lib.diffs import diff_manifests
from lib.config import Zones

root = Path("/tmp/undhd_demo"); root.mkdir(exist_ok=True)
(root / "raw").mkdir(exist_ok=True)
f = root / "raw" / "sample.fastq"
f.write_text("original data")
before = take_snapshot(root)

st = f.stat()
f.write_bytes(("TAMPERED!!!!!" + "X"*20)[:st.st_size].encode())
os.utime(f, (st.st_atime, st.st_mtime))
after = take_snapshot(root)

diff = diff_manifests(before, after, Zones(input=["raw"], scripts="scripts", output="results"))
print("modified:", diff.modified)  # currently prints [] — should show the tampered file
```

**Fix direction:** In `_same()`, always prefer the hash comparison when `new.get("sha256")` is
present, regardless of whether `old` has one (fall back to size+mtime only when no fresh hash
exists, e.g. the file is ≥ 50MB or unreadable). Concretely:

```python
def _same(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    if new.get("sha256") is not None:
        if old.get("sha256") is not None:
            return old["sha256"] == new["sha256"]
        # old snapshot predates hashing this file (e.g. it just shrank under 50MB) —
        # size+mtime is the best we can do, but don't silently trust a same-size rewrite
        # without at least also requiring the old entry's size/mtime to match.
    return old["size"] == new["size"] and old["mtime"] == new["mtime"]
```

Add a regression test for this exact repro to whatever test file Worker C sets up (C3) — this is
the highest-value test in the whole suite since it's the project's core integrity promise.

---

## Bug 2 (🟡 cross-platform, Windows) — zone matching breaks on backslash paths

**File:** `skill/scripts/lib/config.py`, function `_norm_zone()`

**What's wrong:** `_norm_zone()` only strips leading/trailing `/`, never converts `\` to `/`. Real
file paths from `snapshot.py` are always POSIX-style (`Path.relative_to().as_posix()`). A zone
configured as `"raw\subdir"` — which is exactly what a Windows user types naturally, or what a
pasted Explorer path looks like — never matches any real file. Every file under it silently
becomes zone `"other"`: no input-immutability warnings, no zone-scoped cleanup, and false
"stray file" warnings for legitimate input files.

**Repro:**
```python
from lib.config import Zones
z = Zones(input=["raw\\subdir"], scripts="scripts", output="results")
print(z.zone_of("raw/subdir/file.txt"))  # prints "other" — should print "input"
```

**Fix direction:** normalize backslashes in `_norm_zone()`:
```python
def _norm_zone(z: str) -> str:
    return z.replace("\\", "/").strip().strip("/")
```
Also apply this normalization at `Config.from_dict()` / `Config.validate()` time so a config
authored with backslashes gets normalized once and stays consistent on disk, rather than
re-normalizing on every `zone_of()` call.

---

## Bug 3 (🟡 cross-platform) — absolute-path rejection is OS-dependent

**File:** `skill/scripts/lib/config.py`, function `Config.validate()`

**What's wrong:** `validate()` uses `os.path.isabs(z)` to reject absolute zone paths (this is the
path-traversal guard). But `os.path.isabs()` answers based on the OS *running* the check. A config
containing `"C:\\Users\\name\\data"` **passes validation when validated on Linux/macOS** — Python's
posix `isabs` doesn't recognize a Windows drive-letter path as absolute. Since `.undhd/config.json`
is meant to be inspectable/portable across machines, this silently accepts garbage on non-Windows.

**Fix direction:** add an explicit check for Windows-style absolute paths (drive letter or UNC)
regardless of host OS, e.g.:
```python
import re
_WIN_ABS_RE = re.compile(r"^([a-zA-Z]:[\\/]|\\\\)")

def _is_abs_anywhere(z: str) -> bool:
    return os.path.isabs(z) or bool(_WIN_ABS_RE.match(z))
```
and use `_is_abs_anywhere(z)` in place of `os.path.isabs(z)` in `validate()`.

---

## Not a bug, confirmed working — keep as-is

- Path traversal (`../etc`) and POSIX absolute paths (`/etc/passwd`) are correctly rejected today.
- Symlinks are correctly detected via `stat.S_ISLNK` and skipped (not followed, not hashed).
- `atomic_write_text()` is genuinely atomic on both POSIX and Windows via `os.replace()`.

No changes needed there — just don't regress them while fixing the three bugs above.
