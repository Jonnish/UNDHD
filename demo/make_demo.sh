#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TARGET=${1:-"$SCRIPT_DIR/workdir"}

if [[ -d "$TARGET" ]] && [[ -n "$(find "$TARGET" -mindepth 1 -print -quit)" ]]; then
  echo "Refusing to overwrite non-empty demo directory: $TARGET" >&2
  echo "Choose a new path, or remove the old demo directory yourself." >&2
  exit 2
fi

mkdir -p "$TARGET"/{raw,scripts,results/qc,notes,__pycache__}

printf '@READ_A\nACGTACGT\n+\nFFFFFFFF\n' | gzip -c > "$TARGET/raw/patient_A_R1.fastq.gz"
printf '@READ_B\nTGCATGCA\n+\nFFFFFFFF\n' | gzip -c > "$TARGET/raw/patient_A_R2.fastq.gz"

cat > "$TARGET/scripts/align.sh" <<'PIPELINE'
#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$ROOT/results/qc"
printf 'BAM-STUB\n' > "$ROOT/results/patient_A.sorted.bam"
printf 'BW-STUB\n' > "$ROOT/results/patient_A.coverage.bw"
printf 'sample,reads,mapped\npatient_A,2,2\n' > "$ROOT/results/qc/alignment_metrics.csv"
printf '%s alignment completed\n' "$(date -Iseconds)" >> "$ROOT/results/pipeline.log"
printf 'interrupted intermediate\n' > "$ROOT/results/alignment.tmp"
PIPELINE
chmod +x "$TARGET/scripts/align.sh"

"$TARGET/scripts/align.sh"
printf 'old coverage result\n' > "$TARGET/results/previous_run.coverage.bw"
printf 'old pipeline log\n' > "$TARGET/results/previous_run.log"
printf 'Finder metadata\n' > "$TARGET/raw/.DS_Store"
printf 'editor backup\n' > "$TARGET/notes/analysis_notes.md~"
printf 'bytecode stub\n' > "$TARGET/__pycache__/notebook.cpython-312.pyc"
printf 'temporary root scratch\n' > "$TARGET/scratch.tmp"
printf 'Remember to rename cohort labels\n' > "$TARGET/COHORT_NOTES.txt"

python3 - "$TARGET" <<'PY'
import os
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
ages = {
    root / "results" / "previous_run.coverage.bw": 20,
    root / "results" / "previous_run.log": 10,
}
now = time.time()
for path, days in ages.items():
    timestamp = now - days * 86_400
    os.utime(path, (timestamp, timestamp))
PY

echo "Created messy bioinformatics demo at: $TARGET"
echo "Inputs: 2 FASTQ stubs | pipeline: scripts/align.sh | output: results/"
echo "Includes temp files, editor junk, root-level clutter, and backdated output/log files."

