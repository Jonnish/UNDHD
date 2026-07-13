#!/usr/bin/env python3
"""B1/B2/B4/B6 — unDHD CLI entrypoint: setup | maintain | status | history | sync-code.

Thin shell: all real logic lives in `lib/`. This module only does argument
parsing, wiring, and printing.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from lib import gitsync, history
from lib.checks import compute_warnings
from lib.config import Config, GitSettings, POLICIES, UndhdError, Zones, config_path, default_cleanup
from lib.diffs import diff_manifests
from lib.maintenance import initialize_state, run_maintenance
from lib.snapshot import load_manifest, take_snapshot, total_size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="undhd.py", description="Manage a research working directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    setup_p = sub.add_parser("setup", help="Register a directory with unDHD and take the Day-0 snapshot")
    setup_p.add_argument("--root", required=True, type=Path, help="workdir to manage")
    setup_p.add_argument("--input", action="append", required=True, metavar="DIR", help="input zone subdir (repeatable)")
    setup_p.add_argument("--scripts", required=True, metavar="DIR", help="scripts zone subdir")
    setup_p.add_argument("--output", required=True, metavar="DIR", help="output zone subdir")
    setup_p.add_argument("--work", default="", help="short description of what work is done on this dir")
    setup_p.add_argument("--policy", choices=POLICIES, default="standard", help="cleanup aggressiveness")
    setup_p.add_argument("--force", action="store_true", help="overwrite an existing config.json")
    setup_p.set_defaults(func=cmd_setup)

    maintain_p = sub.add_parser("maintain", help="Run the daily snapshot / diff / cleanup / history pass")
    maintain_p.add_argument("--root", required=True, type=Path)
    maintain_p.add_argument("--dry-run", action="store_true", help="report planned actions, change nothing on disk")
    maintain_p.set_defaults(func=cmd_maintain)

    status_p = sub.add_parser("status", help="Show zone sizes, last run, warnings, trash size")
    status_p.add_argument("--root", required=True, type=Path)
    status_p.set_defaults(func=cmd_status)

    history_p = sub.add_parser("history", help="Print recent daily history entries")
    history_p.add_argument("--root", required=True, type=Path)
    history_p.add_argument("--days", type=int, default=7, help="how many days back to print")
    history_p.set_defaults(func=cmd_history)

    sync_p = sub.add_parser("sync-code", help="Commit + push pending scripts-zone changes")
    sync_p.add_argument("--root", required=True, type=Path)
    sync_p.add_argument("--dry-run", action="store_true", help="show the plan, touch nothing")
    sync_p.add_argument("--yes", action="store_true", help="actually commit and push (default: plan only)")
    sync_p.set_defaults(func=cmd_sync_code)

    return parser


# -- setup --------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    if config_path(root).is_file() and not args.force:
        raise UndhdError(
            "%s already exists — this directory is already managed by unDHD "
            "(pass --force to overwrite)" % config_path(root)
        )

    for zone_dir in [*args.input, args.scripts, args.output]:
        (root / zone_dir).mkdir(parents=True, exist_ok=True)

    cfg = Config(
        name=root.name,
        created=date.today().isoformat(),
        zones=Zones(input=list(args.input), scripts=args.scripts, output=args.output),
        work=args.work,
        cleanup=default_cleanup(args.policy),
        git=GitSettings(),
    )
    cfg.save(root)
    result = initialize_state(root, cfg)

    print("unDHD is now managing %s" % root)
    print("  name:      %s" % cfg.name)
    print("  input:     %s" % ", ".join(cfg.zones.input))
    print("  scripts:   %s" % cfg.zones.scripts)
    print("  output:    %s" % cfg.zones.output)
    print("  policy:    %s" % cfg.cleanup.policy)
    print("  work:      %s" % (cfg.work or "(not described)"))
    print("  baseline:  %d files, %s" % (result["files"], history.human_size(result["total_size"])))
    print("  config:    %s" % config_path(root))
    print("  history:   %s" % result["entry_path"])
    return 0


# -- maintain -------------------------------------------------------------------


def cmd_maintain(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    summary = run_maintenance(root, dry_run=args.dry_run)
    counts = summary["counts"]
    delta = summary["size_delta"]

    print(
        "%s%s: %d added, %d removed, %d modified (%s%s)"
        % (
            "[dry-run] " if summary["dry_run"] else "",
            root,
            counts["added"],
            counts["removed"],
            counts["modified"],
            "+" if delta >= 0 else "",
            history.human_size(delta),
        )
    )
    for action in summary["cleanup_actions"]:
        print("  cleanup: %s" % action)
    for warning in summary["warnings"]:
        print("  warning: %s" % warning)

    if summary["dry_run"]:
        print("\n--- entry preview ---")
        print(summary["entry_preview"])
    else:
        print("  history: %s" % summary["entry_path"])
    return 0


# -- status -----------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    cfg = Config.load(root)
    manifest = load_manifest(root)
    if manifest is None:
        print("%s: no manifest yet — run `setup` first" % root)
        return 1

    zone_stats = {z: {"files": 0, "size": 0} for z in ("input", "scripts", "output", "other")}
    for path, entry in manifest["files"].items():
        zone = cfg.zones.zone_of(path)
        zone_stats[zone]["files"] += 1
        zone_stats[zone]["size"] += entry["size"]

    days = history.list_days(root)
    last_entry = days[0] if days else None

    # Read-only "what would maintain see right now" preview — no writes.
    current = take_snapshot(root)
    diff = diff_manifests(manifest, current, cfg.zones)
    warnings = compute_warnings(diff, current, cfg)

    trash_dir = root / ".undhd" / "trash"
    trash_size = sum(f.stat().st_size for f in trash_dir.rglob("*") if f.is_file()) if trash_dir.is_dir() else 0

    print("unDHD status — %s" % root)
    print("  name:    %s" % cfg.name)
    print("  work:    %s" % (cfg.work or "(not described)"))
    print("  policy:  %s   code sync: %s" % (cfg.cleanup.policy, cfg.git.sync_code))
    print()
    print("  zone      files       size")
    for zone in ("input", "scripts", "output", "other"):
        s = zone_stats[zone]
        if s["files"] or zone != "other":
            print("  %-8s %6d   %10s" % (zone, s["files"], history.human_size(s["size"])))
    print()
    print("  total:   %d files, %s" % (len(manifest["files"]), history.human_size(total_size(manifest))))
    print("  last history entry: %s" % (last_entry or "none yet"))
    print(
        "  since last manifest: %d added, %d removed, %d modified (unsaved — run `maintain`)"
        % (len(diff.added), len(diff.removed), len(diff.modified))
    )
    print("  trash:   %s" % history.human_size(trash_size))
    if warnings:
        print("  warnings:")
        for w in warnings:
            print("    - %s" % w)
    else:
        print("  warnings: none")
    return 0


# -- history ------------------------------------------------------------------


def cmd_history(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    Config.load(root)  # fail clearly if this isn't a managed dir
    texts = history.last_days(root, args.days)
    if not texts:
        print("%s: no history yet" % root)
        return 0
    print("\n\n".join(texts))
    return 0


# -- sync-code ------------------------------------------------------------------


def cmd_sync_code(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    cfg = Config.load(root)
    summary = gitsync.run_sync(root, cfg, dry_run=args.dry_run, yes=args.yes)

    if not summary["files"]:
        print("%s: no pending script changes to sync." % root)
        return 0

    print("repo:    %s" % summary["repo"])
    print("remote:  %s" % summary["remote"])
    print("files:")
    for f in summary["files"]:
        print("  - %s" % f)
    print("message: %s" % summary["message"])

    if summary["pushed"]:
        print("pushed %s -> %s/%s" % (summary["branch"], summary["remote"], summary["branch"]))
    else:
        reason = "dry run" if args.dry_run else "pass --yes to commit and push"
        print("(not pushed — %s)" % reason)
    return 0


# -- entrypoint -----------------------------------------------------------------


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UndhdError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
