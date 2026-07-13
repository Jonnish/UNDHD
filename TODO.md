# unDHD — Project Plan & Task Board

A **script-based Claude Code skill** that sets up and maintains research working directories.
Claude handles the conversation and judgment; small deterministic Python scripts do every
filesystem action — so routine daily runs work headless from cron, with no model in the loop.

---

## 1. Concept

Research workdirs rot: inputs mix with outputs, temp junk piles up, and nobody remembers what
changed last Tuesday. unDHD manages a directory in two stages:

- **Setup stage (once per directory, interactive).** The skill interviews the user:
  1. Where do the **input** subdirectories live?
  2. What **work** is done on them, and where do the **scripts** live?
  3. Where should **output** go?
  4. How aggressive should cleanup be? (conservative / standard / aggressive)

  It then writes a config, takes an initial snapshot, and starts the history log.

- **Maintenance stage (daily, scripted).** A cron-driven (or manually invoked) run that:
  - snapshots the tree and diffs it against yesterday's manifest,
  - appends a dated entry to the **daily history log** (added / removed / modified files per
    zone, sizes, warnings),
  - performs **cleanup**: temp files → trash, old logs gzipped, stale outputs archived,
    trash purged after a grace period,
  - detects **code changes** in the scripts zone and — **after an explicit OK from the user** —
    commits and pushes them to the workdir's git remote (see "Code sync" in §2).

**Safety rules (non-negotiable):**
- Nothing is ever hard-deleted: cleanup moves files to `.undhd/trash/<date>/`, purged only
  after `trash_retention_days`.
- `--dry-run` supported everywhere; input and scripts zones are never modified (except
  universal temp patterns like `.DS_Store`).
- Every action taken is recorded in that day's history entry.
- Git pushes are never automatic: a headless (cron) run only *records* pending code changes;
  the commit+push happens only after the user gives an OK in an interactive session
  (`sync_code: "auto"` is an explicit per-directory opt-in).

---

## 2. Architecture

### Repo layout (what we build this hackathon)

```
undhd/
├── README.md
├── TODO.md                  ← this file
├── install.sh               # symlink skill into ~/.claude/skills/undhd; --cron adds crontab entry
├── skill/
│   ├── SKILL.md             # skill instructions: setup interview + when/how to call scripts
│   └── scripts/
│       ├── undhd.py         # single CLI entrypoint: setup | maintain | status | history
│       └── lib/
│           ├── config.py    # load/save/validate .undhd/config.json
│           ├── snapshot.py  # walk tree → manifest
│           ├── diffs.py     # manifest diff → added/removed/modified per zone
│           ├── history.py   # render + read daily history entries
│           ├── checks.py    # warning checks: large files, input-zone changes, strays
│           ├── maintenance.py # daily orchestrator: snapshot → diff → cleanup → history
│           ├── cleanup.py   # trash / archive / rotate / purge
│           └── gitsync.py   # detect code changes; commit + push after user OK
├── tests/                   # pytest, tmp_path-based
└── demo/
    └── make_demo.sh         # generates a fake bioinformatics workdir for the demo
```

Python 3 **stdlib only** (json, pathlib, hashlib, shutil, argparse, gzip) — no deps, cron-safe.

### State inside a managed directory

```
<workdir>/
├── raw/ …                   # user's zones (whatever they named in setup)
├── scripts/ …
├── results/ …
└── .undhd/
    ├── config.json           # written by setup
    ├── manifest.json          # latest snapshot
    ├── history/2026-07-13.md  # one file per day
    └── trash/2026-07-13/…     # cleanup staging, purged after retention period
```

### Schemas (frozen now so all three workers can code in parallel)

`config.json`:
```json
{
  "name": "rnaseq-june",
  "created": "2026-07-13",
  "zones": { "input": ["raw/"], "scripts": "scripts/", "output": "results/" },
  "work": "Align FASTQ files and compute coverage tracks",
  "cleanup": {
    "policy": "standard",
    "temp_patterns": ["*.tmp", "*~", ".DS_Store", "__pycache__", "*.swp"],
    "log_gzip_after_days": 7,
    "archive_output_after_days": 14,
    "trash_retention_days": 7,
    "large_file_warn_mb": 1024
  },
  "git": { "sync_code": "ask", "remote": "origin" }
}
```

`git.sync_code`: `"ask"` (default — record pending changes, push only after user OK) ·
`"auto"` (push without asking, explicit opt-in) · `"off"`.

`manifest.json` (`sha256` is `null` for files ≥ 50 MB — genomics files are huge; size+mtime suffice):
```json
{
  "taken_at": "2026-07-13T07:00:02",
  "files": { "results/coverage.bw": { "size": 52428800, "mtime": 1752300000, "sha256": null } }
}
```

CLI contract (Worker B owns the shell, A and C own the functions it calls):
```
undhd.py setup    --root R --input D [--input D2] --scripts D --output D --work TEXT --policy P
undhd.py maintain --root R [--dry-run]
undhd.py status   --root R
undhd.py history  --root R [--days N]
undhd.py sync-code --root R [--dry-run] [--yes]
```

### Code sync (git-aware maintenance)

When the manifest diff shows changes inside the **scripts zone**, maintenance checks whether that
zone lives in a git repo (`git -C <scripts> rev-parse`). A headless run never pushes — it records a
**pending code sync** block (the changed files) in the history entry and in the `status` payload.
The next time the user interacts with the skill, Claude shows the pending changes and asks for an
explicit OK (AskUserQuestion); on approval it runs `sync-code --yes`, which commits only
scripts-zone paths with a generated message (e.g. `unDHD daily sync: 2 scripts modified (align.sh,
qc.py)`) and pushes to `git.remote`. No repo, no remote, or `sync_code: "off"` → the feature is
silently inert.

---

## 3. Worker split

Three parallel tracks with clean interfaces (the schemas above). Each task is **atomic**:
one commit/PR, independently reviewable, with its acceptance check stated. Code against the
schemas in this file — don't wait on other tracks; integrate at the checkpoints in §4.

### Worker A — Core engine (state, snapshot, diff, history)

- [x] **A1 · config.py** — dataclass + `load/save/validate` for `config.json`; clear errors on
      missing zones or bad policy. ✓ round-trips the example schema above.
- [x] **A2 · snapshot.py** — walk the tree (skip `.undhd/`), build manifest per schema; hash only
      files < 50 MB; `save/load` manifest.json. ✓ snapshotting twice with no changes is identical.
- [x] **A3 · diffs.py** — diff two manifests → `added / removed / modified` lists, aggregated per
      zone (input / scripts / output / other). ✓ detects each change type in a toy tree.
- [x] **A4 · history.py** — render a diff + cleanup report into `history/YYYY-MM-DD.md`; second
      run same day appends a new section; special "Day 0 — initialized" entry for setup.
      ✓ file is valid markdown with per-zone tables.
- [x] **A5 · warnings** — flag in the history entry: new files > `large_file_warn_mb`, any change
      inside an input zone, stray files in the workdir root. ✓ each fires in a crafted tree.
- [x] **A6 · maintenance orchestrator** — `run_maintenance(root, dry_run)` in lib: snapshot →
      diff → cleanup (C's module) → history entry → save new manifest. Depends on A1–A4 + C1.
      ✓ two consecutive runs on the demo tree produce sane day-1/day-2 entries.
- [x] **A7 · pending code-sync state** — in the orchestrator: when the diff shows scripts-zone
      changes and a git repo is detected (helper from B6), add a "pending code sync" block (file
      list) to the history entry and expose it in the `status` payload. Never pushes itself.
      Depends on A6 + B6. ✓ headless maintain on a repo with edited scripts records the block
      and leaves git state untouched.

### Worker B — CLI, setup flow, skill packaging

- [ ] **B1 · undhd.py skeleton** — argparse with the four subcommands per the CLI contract,
      routing to stub lib calls; proper exit codes. **Land this first — it's the shared scaffold.**
      ✓ `undhd.py --help` and each subcommand's `--help` are correct.
- [ ] **B2 · setup command** — validate `--root`, create missing zone dirs, write `config.json`
      (A1), take initial snapshot (A2), write Day-0 history entry (A4), print a summary block.
      ✓ running twice refuses to clobber an existing config without `--force`.
- [ ] **B3 · SKILL.md** — frontmatter (name `undhd`, trigger description) + instructions: run the
      4-question setup interview via AskUserQuestion, map answers to `setup` flags, how/when to
      invoke `maintain`/`status`/`history`, offer cron install after setup.
      ✓ fresh Claude Code session discovers the skill and completes setup on a scratch dir.
- [ ] **B4 · status + history commands** — `status`: zones, sizes, file counts, last maintenance
      time, outstanding warnings, trash size. `history --days N`: print last N entries.
      ✓ readable output on the demo dir.
- [ ] **B5 · install.sh** — symlink `skill/` → `~/.claude/skills/undhd`; `--cron` appends
      `0 7 * * * python3 <abs>/undhd.py maintain --root <R> >> ~/.undhd/cron.log 2>&1` to the
      user's crontab (idempotent — never duplicates the line). ✓ install → skill loads; second
      run changes nothing.
- [ ] **B6 · gitsync.py + sync-code command** — helper to detect the repo containing the scripts
      zone; build a commit message from the day's diff; commit **only scripts-zone paths**; push
      to `git.remote`. `--dry-run` prints the plan; clear errors when there is no repo or remote.
      ✓ on a scratch repo with a local bare remote: dry-run touches nothing, `--yes` lands the push.
- [ ] **B7 · SKILL.md approval flow** — extend B3: when `maintain`/`status` reports pending code
      changes, show the file list, ask the user for an explicit OK, and only then run
      `sync-code --yes`; a "no" leaves everything pending for next time.
      ✓ walkthrough on a scratch repo: decline → no push; accept → push.

### Worker C — Cleanup engine, tests, demo

- [ ] **C1 · cleanup.py: trash pass** — match `temp_patterns` (files & dirs), move to
      `.undhd/trash/<date>/<relative-path>`; `dry_run` returns planned actions without touching
      disk; returns an action list for the history entry. ✓ dry-run leaves tree byte-identical.
- [ ] **C2 · cleanup.py: rotate / archive / purge** — gzip `*.log` older than
      `log_gzip_after_days`; move output-zone files older than `archive_output_after_days` to
      `<output>/archive/YYYY-MM/`; delete trash dirs older than `trash_retention_days`.
      ✓ verified with backdated mtimes (`os.utime`).
- [ ] **C3 · pytest suite** — tmp_path scenarios: setup idempotence, diff correctness
      (add/remove/modify), dry-run vs real cleanup, trash purge timing, malformed config errors.
      ✓ `pytest -q` green; this gates the MVP.
- [ ] **C4 · demo/make_demo.sh** — generate a fake bioinformatics workdir: `raw/*.fastq.gz`
      stubs, `scripts/align.sh`, `results/` outputs, scattered junk (`.DS_Store`, `tmp_*`,
      stray files in root), with backdated mtimes so archive/rotate rules fire on day one;
      `git init` it with a local bare "remote" so the code-sync beat has something to push to.
      ✓ one command yields a convincingly messy directory.
- [ ] **C5 · README + demo script** — install steps and a timed 5-minute walkthrough (see §5).
- [ ] **C6 · gitsync tests** — tmp_path fixture with a scratch repo + local bare remote: `ask`
      mode never pushes from maintain; `sync-code --yes` pushes exactly the scripts-zone changes;
      dry-run is side-effect-free; graceful behavior with no repo/remote. ✓ part of `pytest -q`.

---

## 4. Timeline & integration checkpoints

| When | What |
|---|---|
| Hour 0–1 | All: read this file, agree schemas are final. B lands **B1** scaffold; A and C branch off it. |
| Phase 1 (parallel) | A1–A4 · B2–B3 · C1–C2 |
| **Checkpoint 1** | Wire `maintain` end-to-end (A6). Run on a scratch dir together. |
| Phase 2 (parallel) | A5, A7 · B4–B7 · C3–C4, C6 |
| **Checkpoint 2** | `pytest` green; full demo dry-run from `make_demo.sh` through two simulated days, including the code-sync approve → push beat. |
| Final | C5 polish, rehearse demo, tag `v0.1`. |

**MVP line:** setup + maintain (history log + trash-pass cleanup) + status + demo dir.
Archive/rotate (C2), warnings (A5), and cron install (B5) are the first things to drop if time
runs short. Code sync (B6–B7, A7, C6) is a headline feature — cut it only as a last resort.

---

## 5. Demo script (5 minutes)

1. `demo/make_demo.sh` → show the messy workdir.
2. In Claude Code: "set up unDHD on this directory" → the 4-question interview → show
   `.undhd/config.json` and the Day-0 history entry.
3. Simulate a workday: run the fake pipeline, scatter temp junk.
4. `undhd.py maintain` → show the daily history entry (per-zone diff, actions, warnings) and the
   cleaned tree; junk is in trash, not gone.
5. Simulate day 2 — this time also edit `scripts/align.sh` → maintain flags **pending code
   changes** → Claude shows the changed files and asks for an OK → approve → `sync-code` commits
   and pushes (show the commit landing on the demo's bare remote) → `status` shows the trend.
   Close on the crontab line: "and from here it runs itself every morning."

---

## 6. Stretch goals (post-MVP only)

- Registry (`~/.undhd/registry.json`) + `maintain --all` for multiple managed dirs.
- Pipeline runner: maintenance optionally *executes* the registered work scripts.
- HTML status artifact with directory-size trend chart.
- Auto-commit the `.undhd/history/` log files themselves when the workdir is a git repo
  (distinct from code sync, which is core — see B6).
- "Monthly review": Claude reads a month of history and suggests reorganization.

## 7. Design defaults chosen (flag if you disagree)

1. Maintenance is **hygiene-only** — it logs and cleans but does not run the user's pipeline
   (that's the stretch goal). "What work is done" is recorded as metadata to classify zones.
2. **Single managed dir** per config for MVP; multi-dir registry is stretch.
3. Files ≥ 50 MB are tracked by size+mtime only (no hashing) — hackathon laptops vs. FASTQ files.
4. Skill installs to `~/.claude/skills/` (personal) via symlink, so repo edits are live instantly.
5. Code sync covers the **scripts zone only** and defaults to `ask`: cron runs surface pending
   changes but never push; the push happens through the interactive approval flow, with `auto`
   as an explicit per-directory opt-in.
