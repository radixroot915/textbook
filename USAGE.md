# Harvester — Usage

End-to-end recipe for going from a topic name to a finished textbook PDF. Everything below uses `workflow.py` as the front door.

## Canonical lifecycle

```
# 1. See what you already have
python workflow.py status

# 2. Harvest a new topic (full pipeline: harvest → compile → export)
python workflow.py harvest "leatherworking" --min-files 120 --iterations 6 --cycles 2

# 3. Re-check state — look for 'complete' or 'needs PDF export'
python workflow.py status

# 4. Inspect quality — cycle scores, claim DB stats, drift
python workflow.py quality leatherworking

# 5. Open the PDF
#    vault/leatherworking/curriculum/leatherworking_textbook.pdf

# 6. Optional: spot-check the claim DB by sampling
python workflow.py verify leatherworking --sample 40

# 7. If a previous run was interrupted (Ollama outage), fill missing state
python workflow.py repair leatherworking
```

## Commands

| Command | What it does |
|---|---|
| `status` | Build state of every topic in the vault. Shows pipeline progress (V/G/T/P/O) and suggests the next command per topic. |
| `quality <topic>` | Quality scores per cycle, claim DB breakdown by source and type, low-trust ratio, drift summary. Use after every run. |
| `verify <topic> [--sample N]` | Random-sample N claims and write `verify_<topic>.md` for manual review (checkboxes per claim). |
| `repair <topic>` | Recovery pass — fill missing classifications and claims for vault files whose processing was interrupted. |
| `smoke <topic> [--min-files N]` | Comprehensive single-cycle pipeline test (~20-25 min, default 5 files). Exits 0 if benchmarks healthy, 1 if `investigate`, 2 on error. Use this for change-iteration loops instead of `harvest`. |
| `probe <name>` | Run one small isolated probe (~30s-2min). Use BEFORE expensive changes to de-risk pivots. `probe list` shows available probes. Exit 0=PASS, 2=INCONCLUSIVE, 1=FAIL. |
| `compile <topic>` | Compile curriculum + textbook from existing vault files. No new harvesting. |
| `export <topic>` | Render PDF from an already-compiled textbook. |
| `run <topic>` | `compile` + `export` in one shot. |
| `harvest <topic> [--min-files N --iterations N --cycles N]` | Full pipeline: harvest sources → compile → export. Default `min-files=100 iterations=5 cycles=1`. |
| `expand <topic>` | Additive harvest on an existing vault. Doesn't replace anything. |

## When to use which command

- **Fresh topic** → `harvest "topic name" --min-files 100 --cycles 2`
- **Already have a vault, want to recompile** → `run topic`
- **Want more sources** → `expand topic`
- **Want to see where things stand** → `status`, then `quality topic`
- **PDF is stale but textbook is fine** → `export topic`

## Files produced

Per topic, in `vault/<topic>/curriculum/`:

- `<topic>_textbook.md` — the compiled textbook
- `<topic>_textbook.pdf` — PDF render of the above
- `<topic>_grit.json` — extracted grit (atomic teaching units)
- `<topic>_curriculum.json` — chapter plan
- `<topic>_materials.json` — materials list
- `<topic>_videos.json` — video guide

Plus, in the project root:

- `claims_<topic>.json` — claim DB (persists across runs, compounds)
- `drift_log_<topic>.json` — per-save classification + density log
- `runs/<topic>_<timestamp>/scorecard.json` — quality + alert snapshot for that run

## Quality target

A textbook is "usable" when the quality gate score is ≥ 0.74 with facts ≥ 70% and pedagogy ≥ 60%. `quality <topic>` shows this.
