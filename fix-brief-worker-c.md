# Fix brief — Worker C (cleanup.py / tests / demo)

Paste this whole file to Claude Code and say:
**"Take these into account while building C1/C2/C3 so we don't ship cleanup.py with the same
gaps the review already found in Worker A's code."**

## Design notes to bake in from day one

1. **No lock file around maintenance runs.** Nothing in `maintenance.run_maintenance()` currently
   prevents two overlapping invocations (e.g. a manual `undhd.py maintain` while a cron tick is
   also running) from racing on `manifest.json` and today's history file. This becomes a real risk
   once C1/C2 add a trash pass that *moves files on disk* — two concurrent cleanup passes could
   both try to move the same file. Recommend `cleanup.py` (or `maintenance.py`, whichever owns the
   orchestration) take a simple lock file (e.g. `.undhd/.lock`, created with `os.O_CREAT|os.O_EXCL`
   so it's atomic, removed in a `finally`) at the start of `run_maintenance()`, and exit cleanly
   with a clear message ("another unDHD run is already in progress") if it can't acquire it.

2. **Respect the corrected zone matching once Worker A's fix lands.** Worker A is fixing a bug
   where zones configured with backslashes (`"raw\subdir"`, a natural Windows typo) never match
   real files. `cleanup.py`'s trash/archive logic will use `config.zones` / `Zones.zone_of()` to
   decide what's safe to touch (e.g. archiving output-zone files) — make sure you're calling into
   the shared `zone_of()` helper rather than doing your own path-prefix matching, so cleanup
   doesn't reintroduce the same class of bug independently.

3. **Add regression tests (C3) for the two confirmed bugs, not just future ones:**
   - **Tamper evasion:** overwrite an input-zone file with different content but identical
     size, restore the original mtime via `os.utime`, and assert the diff reports it as
     `modified` (this currently fails — see Worker A's brief, bug 1 — should pass once fixed).
   - **Backslash zone paths:** a `Zones` configured with a backslash-containing zone path should
     still classify real POSIX-style relpaths correctly (currently fails — see Worker A's brief,
     bug 2).
   - Also worth locking in as regression tests since they already pass and should stay passing:
     path traversal rejection (`../etc`, absolute paths) in `Config.validate()`, and symlinks
     being skipped (not followed/hashed) in `take_snapshot()`.

4. **Trash-pass ordering:** confirmed today that `maintenance.run_maintenance()` already runs
   cleanup actions *before* computing warnings, so once C1 lands, a `.DS_Store`/`*.tmp` file that
   gets trashed in a given run won't also trigger a "stray file" warning in that same run's
   history entry. No change needed here — just don't reorder it when wiring in `cleanup.run_cleanup()`.

## Not a bug — no action needed yet

`cleanup.py` doesn't exist yet, so there's nothing to review in it directly. This review re-runs
automatically on new commits and will check C1/C2 in detail once they land.
