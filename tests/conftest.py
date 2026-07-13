"""Test configuration for the source-tree layout."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "skill" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

