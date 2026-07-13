# unDHD — Run & Test Guide

Verified today by actually running every command below against a freshly generated demo
directory (`demo/make_demo.sh`) and the full test suite. Everything in this guide works as
written on Linux (tested directly) and macOS (same code paths, pure stdlib). Windows caveats are
called out explicitly — see the note at the end.

## 1. Requirements

- Python 3.9+ (stdlib only for the core library — no pip installs needed to run unDHD itself)
- `pytest` only if you want to run the test suite: `pip install pytest`
- Git (for the `sync-code` feature and for cloning the repo)
- bash (for `install.sh` and `demo/make_demo.sh` — macOS/Linux native; on Windows use WSL or Git Bash)

## 2. Get the code

```bash
git clone https://github.com/Jonnish/UNDHD.git
cd UNDHD
```

## 3. Try it on the built-in demo (safest first run)

The repo ships a script that builds a deliberately messy fake bioinformatics directory —
FASTQ stubs, a pipeline script, junk files (`.DS_Store`, `*.tmp`, `*~`, `__pycache__`), and
backdated old outputs/logs so archiving and log-rotation rules actually fire.

```bash
chmod +x demo/make_demo.sh
./demo/make_demo.sh            # creates demo/workdir/
find demo/workdir              # look at the mess before you touch anything
```

Register it with unDHD (this is the one-time "setup" step):

```bash
python3 skill/scripts/undhd.py setup \
  --root demo/workdir \
  --input raw \
  --scripts scripts \
  --output results \
  --work "Align FASTQ files and compute coverage tracks" \
  --policy standard
```

You'll see a summary: zones, baseline file count/size, and where the config + history live
(`demo/workdir/.undhd/`).

**Always preview before the first real run:**

```bash
python3 skill/scripts/undhd.py maintain --root demo/workdir --dry-run
```

This prints exactly what cleanup *would* do — move junk to trash, gzip old logs, archive old
outputs — without touching a single file. Confirmed output on the demo dir: 5 files trashed, 1
log gzipped, 1 old output archived, plus 2 "stray file in root" warnings for files that aren't
junk but also aren't in a recognized zone.

When you're happy with the plan, run it for real:

```bash
python3 skill/scripts/undhd.py maintain --root demo/workdir
```

Nothing is ever hard-deleted — junk goes to `demo/workdir/.undhd/trash/<date>/`, purged only
after the configured retention window (7 days on the `standard` policy).

Check on it any time:

```bash
python3 skill/scripts/undhd.py status --root demo/workdir     # zone sizes, warnings, trash size
python3 skill/scripts/undhd.py history --root demo/workdir --days 7   # the dated log
```

If your scripts folder is a git repo and you've edited a script, `maintain`/`status` will flag it
as a "pending code sync" — nothing is ever pushed automatically. Review and push explicitly:

```bash
python3 skill/scripts/undhd.py sync-code --root demo/workdir --dry-run   # see the plan
python3 skill/scripts/undhd.py sync-code --root demo/workdir --yes       # commit + push
```

## 4. Try it on a real directory

Same commands, pointed at your own workdir instead of `demo/workdir`. Strongly recommended: run
`maintain --dry-run` the first time on any directory that already has real data in it, so you can
see the plan before anything moves.

## 5. Install the Claude skill + daily automation (optional)

```bash
./install.sh                              # symlinks skill/ into ~/.claude/skills/undhd
./install.sh --cron --root /path/to/dir   # also adds a 7am daily cron entry (idempotent)
```

Once installed, you can just tell Claude Code things like "set up unDHD on this directory" or
"what changed in this project this week" and it drives the CLI for you (see `skill/SKILL.md`).

## 6. Run the test suite

```bash
pip install pytest
pytest -q
```

Confirmed today: **14 passed** — covers cleanup (trash/gzip/archive/purge, dry-run parity, path
safety) and CLI integration (setup idempotence, add/remove/modify detection, malformed-config
error handling).

## 7. Platform notes

- **macOS / Linux:** everything above works as-is — this is what was actually tested.
- **Windows:** the core Python library (`setup`/`maintain`/`status`/`history`/`sync-code`) works
  fine under plain Windows Python — paths are normalized internally and file operations use
  `os.replace`, which is atomic on Windows too. Two things won't work natively on Windows:
  `demo/make_demo.sh` and `install.sh` are bash scripts (use WSL or Git Bash), and
  `install.sh --cron` writes a `crontab` entry, which doesn't exist on Windows — there's no
  Task Scheduler equivalent built yet, so on native Windows you'd run `maintain` manually or
  via WSL cron until that's added (flagged in the fix brief for Worker B).
- If you type zone paths with backslashes in a config by hand (e.g. `raw\subdir` on Windows),
  see `fix-brief-worker-a.md` in the repo root — that's a known bug already reported, not yet
  fixed as of this guide.
