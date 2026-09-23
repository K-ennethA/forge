# Benchmark harness

Measures a forge task run's **time-to-complete** and **object quality**, and
says whether a candidate run is good enough to ship against a baseline.

- `runner.py` sends a frozen task's prompt to the live bridge (`POST /ask` on
  127.0.0.1:8901, a new conversation), polls `GET /job/<id>` until the job
  reaches `done` / `error` / `timeout` / `cancelled`, times it, measures the
  artifact the run wrote, and writes a report JSON (schema: `report.py`).
- `quality.py` is deterministic only. It runs the **hard gates** (the part
  pipeline's check stage: bed_fit, min_wall, overhangs, watertight, run
  through the geometry service's own check code in-process) and the
  **fidelity** metrics from `task.json` `expected`. It loads the artifact in
  one hidden headless Blender (`blender_measure.py`). There is no appearance
  scoring: `appearance_score` is `None`.
- `compare.py` gives the verdict. A change ships only if time decreased OR
  quality increased, AND neither regressed. Time regressed means more than 5%
  slower. Quality is ordered as gates passed, then fidelity passes, then score.

## Tasks (frozen inputs; never edit a task in place, bump `version` in a copy)

| task | artifact | gates | fidelity |
|---|---|---|---|
| `tasks/parametric-dish` | `projects/bench-parametric-dish/models/dish.stl` | 1 part x 4 | part count, diameter x/y, height, wall span, floor span |
| `tasks/ear-sculpt` | `projects/bench-ear-sculpt/bench-ear-sculpt.blend` | 2 ears x 4 | ear count, min wall >= 0.8 mm, mirrored yaw, volume ratio >= 0.15 |

`ear-sculpt` encodes the artist's latest ear defects: no volume, and both ears
angled the same direction. Its reference crops are copies of
`projects/eevee-bowl-v2/design/refs/crops/`, with their hashes in `task.json`.
Two ear thresholds are **assumptions** calibrated only against the rejected
pair in `eevee-bowl-wip.blend`: volume ratio 0.15 (the rejected ears measure
0.077/0.078) and mirror tolerance 20 deg (the rejected pair measures 66.9).
Re-pin both after measuring the first ear pair the artist accepts:

    "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup ^
      --python benchmark\blender_measure.py -- benchmark\tasks\ear-sculpt\task.json <accepted.blend> out.json

## Run a baseline, run a candidate, compare

The bridge must already be running (`start_forge.cmd`). Every run is a real,
paid model turn. Run each task on a clean checkout of the code under test:

    service\.venv\Scripts\python.exe benchmark\runner.py benchmark\tasks\parametric-dish bench-out\dish-baseline.json
    service\.venv\Scripts\python.exe benchmark\runner.py benchmark\tasks\ear-sculpt      bench-out\ear-baseline.json

    rem  apply the change, restart the bridge (its /health build.sha must match), then:
    service\.venv\Scripts\python.exe benchmark\runner.py benchmark\tasks\parametric-dish bench-out\dish-candidate.json

    service\.venv\Scripts\python.exe benchmark\compare.py bench-out\dish-baseline.json bench-out\dish-candidate.json

`compare.py` prints the verdict and its reasons and exits 0 for SHIP, 1 for
REJECT. Each report records `git_sha` and `git_dirty`. The measurement's raw
JSON and Blender log are written next to the report as `*.measure.json` and
`*.measure.log`. An artifact older than the run's start is treated as missing,
so delete or keep the old one as you like. `FORGE_BLENDER_EXE` overrides the
Blender path and `FORGE_BENCH_BRIDGE` overrides the bridge URL.

## Tests

    service\.venv\Scripts\python.exe -m pytest benchmark\tests -q
    "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" --background --factory-startup --python addon\tests\headless_benchmark.py

The pytest suites use a stub bridge on an OS-assigned port and never touch the
real one. The headless suite runs on port 9918.
