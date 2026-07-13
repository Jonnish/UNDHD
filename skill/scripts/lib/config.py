"""A1 — load/save/validate `.undhd/config.json` (schema frozen in TODO.md §2)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

UNDHD_DIR = ".undhd"
CONFIG_NAME = "config.json"

POLICIES = ("conservative", "standard", "aggressive")
SYNC_MODES = ("ask", "auto", "off")

DEFAULT_TEMP_PATTERNS = ["*.tmp", "*~", ".DS_Store", "__pycache__", "*.swp"]

# policy -> (log_gzip_after_days, archive_output_after_days [0 = off], trash_retention_days)
POLICY_PRESETS = {
    "conservative": (14, 0, 14),
    "standard": (7, 14, 7),
    "aggressive": (3, 7, 3),
}


class UndhdError(Exception):
    """Base class for all unDHD errors."""


class ConfigError(UndhdError):
    pass


def undhd_dir(root: Path) -> Path:
    return Path(root) / UNDHD_DIR


def config_path(root: Path) -> Path:
    return undhd_dir(root) / CONFIG_NAME


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crashed cron run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _posix_seps(z: str) -> str:
    """Backslash-authored zone paths (Windows habit) must match POSIX relpaths."""
    return z.replace("\\", "/")


def _norm_zone(z: str) -> str:
    return _posix_seps(z).strip().strip("/")


# Windows absolute paths (drive letter or UNC) must be rejected on every host OS,
# not just on Windows — os.path.isabs("C:\\x") is False on POSIX (fix-brief bug 3).
_WIN_ABS_RE = re.compile(r"^([a-zA-Z]:[\\/]|\\\\|//)")


def _is_abs_anywhere(z: str) -> bool:
    return os.path.isabs(z) or os.path.isabs(_posix_seps(z)) or bool(_WIN_ABS_RE.match(z))


@dataclass
class Zones:
    input: List[str]
    scripts: str
    output: str

    def zone_of(self, relpath: str) -> str:
        """Classify a posix relpath as input/scripts/output/other (longest prefix wins)."""
        best_zone, best_len = "other", -1
        groups = (("input", list(self.input)), ("scripts", [self.scripts]), ("output", [self.output]))
        for name, dirs in groups:
            for d in dirs:
                nd = _norm_zone(d)
                if not nd:
                    continue
                if (relpath == nd or relpath.startswith(nd + "/")) and len(nd) > best_len:
                    best_zone, best_len = name, len(nd)
        return best_zone

    def all_dirs(self) -> Dict[str, List[str]]:
        return {
            "input": [_norm_zone(d) for d in self.input],
            "scripts": [_norm_zone(self.scripts)],
            "output": [_norm_zone(self.output)],
        }


@dataclass
class CleanupSettings:
    policy: str = "standard"
    temp_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_TEMP_PATTERNS))
    log_gzip_after_days: int = 7
    archive_output_after_days: int = 14
    trash_retention_days: int = 7
    large_file_warn_mb: int = 1024


@dataclass
class GitSettings:
    sync_code: str = "ask"
    remote: str = "origin"


def default_cleanup(policy: str = "standard") -> CleanupSettings:
    """Preset CleanupSettings for a policy — used by setup (B2) to map the interview answer."""
    if policy not in POLICY_PRESETS:
        raise ConfigError("unknown cleanup policy %r (expected one of %s)" % (policy, ", ".join(POLICIES)))
    gzip_d, archive_d, trash_d = POLICY_PRESETS[policy]
    return CleanupSettings(
        policy=policy,
        log_gzip_after_days=gzip_d,
        archive_output_after_days=archive_d,
        trash_retention_days=trash_d,
    )


@dataclass
class Config:
    name: str
    created: str
    zones: Zones
    work: str = ""
    cleanup: CleanupSettings = field(default_factory=CleanupSettings)
    git: GitSettings = field(default_factory=GitSettings)

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "created": self.created,
            "zones": {"input": list(self.zones.input), "scripts": self.zones.scripts, "output": self.zones.output},
            "work": self.work,
            "cleanup": {
                "policy": self.cleanup.policy,
                "temp_patterns": list(self.cleanup.temp_patterns),
                "log_gzip_after_days": self.cleanup.log_gzip_after_days,
                "archive_output_after_days": self.cleanup.archive_output_after_days,
                "trash_retention_days": self.cleanup.trash_retention_days,
                "large_file_warn_mb": self.cleanup.large_file_warn_mb,
            },
            "git": {"sync_code": self.git.sync_code, "remote": self.git.remote},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        if not isinstance(data, dict):
            raise ConfigError("config root must be a JSON object")
        try:
            zones_raw = data["zones"]
        except KeyError:
            raise ConfigError("config is missing required key 'zones'")
        if not isinstance(zones_raw, dict):
            raise ConfigError("'zones' must be an object with input/scripts/output")
        missing = [k for k in ("input", "scripts", "output") if k not in zones_raw]
        if missing:
            raise ConfigError("'zones' is missing: %s" % ", ".join(missing))
        inputs = zones_raw["input"]
        if isinstance(inputs, str):  # tolerate a single string for input
            inputs = [inputs]
        # Normalize separators once at load time so a backslash-authored config
        # becomes consistent on disk after the next save (fix-brief bug 2).
        # Trailing slashes are kept as written — the frozen schema example uses them.
        zones = Zones(
            input=[_posix_seps(z) if isinstance(z, str) else z for z in inputs],
            scripts=_posix_seps(zones_raw["scripts"]) if isinstance(zones_raw["scripts"], str) else zones_raw["scripts"],
            output=_posix_seps(zones_raw["output"]) if isinstance(zones_raw["output"], str) else zones_raw["output"],
        )

        cleanup_raw = data.get("cleanup", {})
        cleanup = CleanupSettings(
            policy=cleanup_raw.get("policy", "standard"),
            temp_patterns=list(cleanup_raw.get("temp_patterns", DEFAULT_TEMP_PATTERNS)),
            log_gzip_after_days=cleanup_raw.get("log_gzip_after_days", 7),
            archive_output_after_days=cleanup_raw.get("archive_output_after_days", 14),
            trash_retention_days=cleanup_raw.get("trash_retention_days", 7),
            large_file_warn_mb=cleanup_raw.get("large_file_warn_mb", 1024),
        )
        git_raw = data.get("git", {})
        git = GitSettings(sync_code=git_raw.get("sync_code", "ask"), remote=git_raw.get("remote", "origin"))

        cfg = cls(
            name=data.get("name", ""),
            created=data.get("created", ""),
            zones=zones,
            work=data.get("work", ""),
            cleanup=cleanup,
            git=git,
        )
        cfg.validate()
        return cfg

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        problems: List[str] = []
        if not self.name or not str(self.name).strip():
            problems.append("'name' must be a non-empty string")

        zone_fields = [("zones.scripts", self.zones.scripts), ("zones.output", self.zones.output)]
        if not isinstance(self.zones.input, list) or not self.zones.input:
            problems.append("'zones.input' must be a non-empty list of subdirectories")
        else:
            zone_fields += [("zones.input[%d]" % i, z) for i, z in enumerate(self.zones.input)]
        for label, z in zone_fields:
            if not isinstance(z, str) or not _norm_zone(z):
                problems.append("%s must be a non-empty relative path" % label)
            elif _is_abs_anywhere(z) or ".." in PurePosixPath(_posix_seps(z)).parts:
                problems.append("%s must be a relative path inside the workdir (got %r)" % (label, z))

        if self.cleanup.policy not in POLICIES:
            problems.append("cleanup.policy %r invalid (expected one of %s)" % (self.cleanup.policy, ", ".join(POLICIES)))
        for label, v, allow_zero in (
            ("cleanup.log_gzip_after_days", self.cleanup.log_gzip_after_days, False),
            ("cleanup.archive_output_after_days", self.cleanup.archive_output_after_days, True),
            ("cleanup.trash_retention_days", self.cleanup.trash_retention_days, False),
            ("cleanup.large_file_warn_mb", self.cleanup.large_file_warn_mb, False),
        ):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0 or (v == 0 and not allow_zero):
                floor = "a non-negative integer (0 disables it)" if allow_zero else "a positive integer"
                problems.append("%s must be %s (got %r)" % (label, floor, v))
        if not isinstance(self.cleanup.temp_patterns, list) or not all(
            isinstance(p, str) and p.strip() for p in self.cleanup.temp_patterns
        ):
            problems.append("cleanup.temp_patterns must be a list of non-empty glob strings")

        if self.git.sync_code not in SYNC_MODES:
            problems.append("git.sync_code %r invalid (expected one of %s)" % (self.git.sync_code, ", ".join(SYNC_MODES)))
        if not isinstance(self.git.remote, str) or not self.git.remote.strip():
            problems.append("git.remote must be a non-empty string")

        if problems:
            raise ConfigError("invalid config:\n  - " + "\n  - ".join(problems))

    # -- disk ---------------------------------------------------------------

    def save(self, root: Path) -> Path:
        self.validate()
        path = config_path(root)
        atomic_write_text(path, json.dumps(self.to_dict(), indent=2) + "\n")
        return path

    @classmethod
    def load(cls, root: Path) -> "Config":
        path = config_path(root)
        if not path.is_file():
            raise ConfigError("%s not found — this directory is not managed by unDHD yet (run setup)" % path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError("cannot read %s: %s" % (path, exc))
        return cls.from_dict(data)
