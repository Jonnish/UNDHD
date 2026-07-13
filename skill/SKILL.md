---
name: undhd
description: Set up and maintain a research working directory — snapshot/diff hygiene, dated history log, trash-not-delete cleanup, and approval-gated git sync for a scripts folder. Trigger on requests like "set up unDHD on this directory", "clean up / organize this workdir", "run maintenance on this project dir", "what changed in this directory", or "sync my scripts".
---

# unDHD — research working directory hygiene

unDHD manages a directory in two stages: an interactive **setup** (once), and headless-safe
**maintenance** (daily, cron or manual). All state lives under `<root>/.undhd/`. Every filesystem
action goes through `skill/scripts/undhd.py` — never edit or delete files in a managed directory
yourself; always go through the CLI so actions are logged and nothing is hard-deleted.

Entrypoint: `python3 <this-skill-dir>/scripts/undhd.py <command> ...` (resolve `<this-skill-dir>`
from this file's own location, e.g. `~/.claude/skills/undhd/scripts/undhd.py`).

## Setup flow (first time on a directory)

Run when the user asks to set up, register, or start managing a directory with unDHD, and
`<root>/.undhd/config.json` does not already exist (check with `status` — a missing-manifest
error means it isn't set up yet).

Ask four questions with `AskUserQuestion`, in this order, then map the answers to `setup` flags.
Prefer offering common conventions as options (with "Other" always available for a custom path);
if the user's answer names more than one directory (e.g. "raw/ and fastq/", or a comma-separated
list), pass each as a separate `--input` flag — `--input` is repeatable.

1. **"Where do the input subdirectories live?"** — options like `raw/`, `data/`, `input/`, each
   described as "read-only source files"; `multiSelect: true`. → one or more `--input DIR`.
2. **"What work is done on them, and where do the scripts live?"** — get a short free-text
   description of the work (→ `--work "..."`) and the scripts subdirectory (options like
   `scripts/`, `bin/`, `src/`) → `--scripts DIR`.
3. **"Where should output go?"** — options like `results/`, `output/`, `out/` → `--output DIR`.
4. **"How aggressive should cleanup be?"** — options `conservative`, `standard` (recommended),
   `aggressive`, each described using the presets in `lib/config.py` (retention days, archive
   timing) → `--policy P`.

Then run:

```
undhd.py setup --root <R> --input <D1> [--input <D2> ...] --scripts <D> --output <D> --work "<TEXT>" --policy <P>
```

If it fails because a config already exists, tell the user and ask whether to re-run with
`--force` (this overwrites `config.json` and re-baselines — confirm before doing that, it's not
reversible in the same way trash is).

On success, show the printed summary (zones, baseline file count/size, config + history paths).
Then offer to install a daily cron entry:

```
install.sh --cron <R>
```

Only run `install.sh` after the user says yes — it edits their crontab. On systems without
`crontab` (native Windows outside WSL), this exits with a clear message instead of installing
anything — relay that message to the user rather than retrying or working around it yourself.

## Day-to-day commands

- **"what changed" / "run maintenance" / "clean this up"** → `undhd.py maintain --root <R>`.
  Use `--dry-run` first if the user seems unsure, or if it's the first maintenance run on a
  directory that already has real data in it — show them the plan before touching anything.
- **"how's this directory doing" / "status"** → `undhd.py status --root <R>`. Reports zone
  sizes, last history entry, warnings, and trash size. Note its "since last manifest" line is a
  live preview, not a committed record — nothing is written until `maintain` runs.
- **"show me the history" / "what happened last week"** → `undhd.py history --root <R> --days N`.

Surface warnings from `maintain`/`status` output verbatim (large files, input-zone changes,
strays) — don't silently drop them.

## Pending code-sync approval flow

`maintain` never pushes code by itself (non-negotiable safety rule — see TODO.md §1). When a
`maintain` or `status` run shows scripts-zone changes (a "Pending code sync" section in the
history entry, or `since last manifest` touching `scripts/` in `status`), do the following instead
of running `sync-code` on your own initiative:

1. Run `undhd.py sync-code --root <R> --dry-run` to get the exact file list and commit message
   `sync-code` would use (this only reads git state, it changes nothing).
2. Show the user that file list and message.
3. Ask via `AskUserQuestion`: "Commit and push these N script changes?" with an explicit
   yes/no choice.
4. On **yes** → run `undhd.py sync-code --root <R> --yes`, then report the pushed commit.
5. On **no** → tell the user the changes are left pending; nothing else to do, they'll surface
   again next time `maintain` or `status` runs.

Never pass `--yes` without a prior explicit approval in the current conversation, even if a past
conversation approved a similar sync — each sync's file list is different and needs its own OK.
If `config.json` has `git.sync_code: "auto"`, this is a per-directory opt-in the user made
knowingly during setup (not the default) — still show what's about to be pushed, but you may skip
the yes/no question and go straight to `sync-code --yes`.

If `sync-code` reports "no pending script changes to sync," there's nothing to do — don't ask.
If it errors (no repo / no remote), relay the error; don't try to `git init` or add a remote for
the user without asking first.
