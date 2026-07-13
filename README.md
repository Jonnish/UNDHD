# unDHD

unDHD is a script-backed Claude Code skill for keeping research working directories tidy and
auditable. Claude guides the one-time setup conversation; deterministic, Python-standard-library
scripts handle snapshots, daily history, cleanup, and status. Maintenance is safe to run manually
or from cron: temporary files go to dated trash first, protected input and script zones are not
modified, and every action is recorded.

## What it manages

A managed directory is divided into input, scripts, and output zones. unDHD stores its own state in
`.undhd/`:

```text
my-project/
├── raw/                       # protected inputs
├── scripts/                   # protected pipeline code
├── results/                   # generated output
└── .undhd/
    ├── config.json
    ├── manifest.json
    ├── history/YYYY-MM-DD.md
    └── trash/YYYY-MM-DD/
```

On each maintenance run, unDHD compares the current tree with the last manifest, records changes,
moves configured junk to dated trash, gzips stale logs, archives stale output by month, and purges
trash only after its retention window. Use `--dry-run` to preview the same action report without
changing the filesystem.

## Requirements and installation

- Python 3.10 or newer; the runtime uses only the standard library.
- Bash for the installer and demo helper.
- `pytest` is needed only to run the development test suite.

From the repository root:

```bash
./install.sh
```

This links `skill/` into `~/.claude/skills/undhd`, so repository edits are immediately visible to
Claude Code. To install daily maintenance for an already managed directory:

```bash
./install.sh --cron /absolute/path/to/managed-project
```

You can also run the CLI directly without installing the skill:

```bash
python3 skill/scripts/undhd.py --help
```

## Command-line quick start

Set up a project once:

```bash
python3 skill/scripts/undhd.py setup \
  --root /path/to/project \
  --input raw \
  --scripts scripts \
  --output results \
  --work "Align FASTQ files and compute coverage tracks" \
  --policy standard
```

Then preview or perform maintenance and inspect its record:

```bash
python3 skill/scripts/undhd.py maintain --root /path/to/project --dry-run
python3 skill/scripts/undhd.py maintain --root /path/to/project
python3 skill/scripts/undhd.py status --root /path/to/project
python3 skill/scripts/undhd.py history --root /path/to/project --days 7
```

Running `setup` again refuses to overwrite existing state unless the CLI is explicitly given
`--force`.

## Five-minute demo

The demo generator accepts an optional destination and refuses to overwrite a non-empty directory:

```bash
./demo/make_demo.sh /tmp/undhd-demo
```

It creates tiny gzipped FASTQ stubs, a runnable fake alignment pipeline, recent and backdated
results, an old log, temp files, editor/OS junk, and stray root files.

Use this timed walkthrough:

1. **0:00–0:45 — Show the mess.** Run `find /tmp/undhd-demo -maxdepth 3 -type f` and point out
   protected raw reads, the pipeline, generated results, stale files, and junk.
2. **0:45–1:45 — Set up.** In Claude Code, say: “Set up unDHD on `/tmp/undhd-demo`.” Answer the
   four prompts with `raw`, the alignment/coverage description, `scripts`, `results`, and the
   standard cleanup policy. Show `.undhd/config.json` and the Day-0 history entry.
3. **1:45–2:30 — Simulate work.** Run `/tmp/undhd-demo/scripts/align.sh`, edit or add a result,
   and create another `*.tmp` file.
4. **2:30–3:15 — Preview safely.** Run `maintain --dry-run`; show planned trash, gzip, and archive
   actions, then verify that all source files still exist.
5. **3:15–4:30 — Maintain.** Run real maintenance. Show the per-zone history entry and warnings,
   then show that junk is under `.undhd/trash/<date>/`, the old log is gzipped, and old output is
   under `results/archive/YYYY-MM/`.
6. **4:30–5:00 — Close the loop.** Run `status`, then `history --days 2`. Show the cron line from
   `install.sh --cron` and explain that the same deterministic maintenance can now run each morning.

## Development

Run the suite from the repository root:

```bash
pytest -q
```

Tests use temporary directories and backdated mtimes; they do not touch real research data.

See [TODO.md](TODO.md) for the frozen schemas, CLI contract, and implementation task board.
