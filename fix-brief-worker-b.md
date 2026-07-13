# Fix brief — Worker B (CLI / setup / gitsync / cron install)

Paste this whole file to Claude Code and say:
**"Take these into account while building B1/B5/B6 — some are must-fix-before-ship, some are
just heads-up so you don't build on top of a bug Worker A is fixing."**

## 🟡 Must-decide-before-ship — B5's `install.sh --cron` has no Windows path

**File (planned):** `install.sh`, task B5

The TODO.md design for B5 is "symlink skill → `~/.claude/skills/undhd`; `--cron` appends a line to
the user's crontab." `crontab` does not exist on Windows — there is currently no equivalent branch
(Task Scheduler / `schtasks.exe`) anywhere in the plan. Since one of the three things this review
was asked to check was "does it work on Mac, Linux, AND Windows," this needs an explicit decision
before B5 lands, not an afterthought:

- **Option A (recommended for hackathon scope):** scope `--cron` to POSIX only; on Windows,
  `install.sh` should detect the OS and print a clear message ("automatic scheduling isn't
  supported on native Windows — run `undhd.py maintain` manually, via WSL cron, or set up a
  Task Scheduler entry yourself") rather than silently failing or writing a no-op crontab line.
- **Option B (more work, more complete):** add a `schtasks /create /sc minute /mo 5 /tn undhd-maintain
  /tr "python path\to\undhd.py maintain --root ..."` branch for Windows, mirroring the crontab
  line's idempotence requirement (don't duplicate the task on repeat installs).

Either is fine — just pick one on purpose and document it in the README, rather than letting
Windows users discover the gap by their maintenance silently never running.

## Heads-up — depends on Worker A's fixes landing first

- **B6 (`gitsync.py`)** will detect "changes in the scripts zone" by reading the diff Worker A's
  code produces. Once Worker A fixes the backslash-zone-matching bug (see Worker A's brief, bug
  2), make sure `gitsync.py` doesn't do its *own* separate zone-path normalization — reuse
  `Zones.zone_of()` / `config._norm_zone()` rather than re-implementing path matching, so the two
  don't drift out of sync again.
- **B1/B2 (CLI + setup)**: the `ConfigError` messages from `config.py` are good — multi-line, one
  bullet per problem, includes the offending value. Keep that pattern when the `setup` command
  surfaces validation errors from the interview answers; don't wrap them in a generic "invalid
  input" message.

## Not a bug — no action needed

Nothing else Worker B owns exists yet (no `undhd.py`, no `gitsync.py`), so there's nothing else to
flag until B1 lands. This review will re-check automatically once you push commits.
