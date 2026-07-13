"""unDHD core library — deterministic filesystem state, no model in the loop.

Modules (ownership per TODO.md §3):
  config       .undhd/config.json load/save/validate          (Worker A)
  snapshot     tree walk -> manifest.json                     (Worker A)
  diffs        manifest diff, aggregated per zone             (Worker A)
  history      daily history log rendering + reading          (Worker A)
  checks       warning detection for history entries          (Worker A)
  maintenance  daily orchestrator                             (Worker A)
  cleanup      trash / archive / rotate / purge               (Worker C)
  gitsync      code-change detection, commit+push after OK    (Worker B)
"""
