"""Measure the seed ensemble on real GPU runs, and score what it picked.

The claim under test is not "more samples are better" - that is free to assert.
It is that a **deterministic** picker, with no VLM and no CLIP anywhere, chooses
a better mesh than the single seed would have, and that the structure tier does
it for a small fraction of a full generation.  Both halves are numbers, so both
are measured here rather than argued.

    service\\.venv\\Scripts\\python.exe -m meshgen.tools.ab_ensemble repeat --out <dir>
    service\\.venv\\Scripts\\python.exe -m meshgen.tools.ab_ensemble run    --out <dir>
    service\\.venv\\Scripts\\python.exe -m meshgen.tools.ab_ensemble report <dir>

``repeat`` is the load-bearing one and should be run first.  The whole structure
tier rests on **seed replay**: the head is probed N times, the winner's seed is
handed back to one ordinary full generation, and that generation's head has to
decode the same grid the probe did.  ComfyUI core has no node that can take a
structure VOXEL back into a graph and meshgen never installs a custom one (see
``backends/structure_probe.py``), so replay is the only route - and an
assumption that the pipeline is deterministic at a fixed seed is exactly the
kind of thing that is true until a cuDNN kernel selection says otherwise.
``repeat`` runs the same seed K times and prints the grid IoU between the draws.
If that is not 1.0, the structure tier is picking a seed whose grid it will not
get back, and the README must say so.

``run`` and ``report`` follow ``ab_tuning.py`` exactly: real jobs through the
live service on 8902, scored with ``mesh_metrics`` against the synthetic
fixtures' exact ground truth, appended to ``results.jsonl`` one row at a time so
an interrupted sweep resumes instead of paying twice for finished GPU minutes.
Without meshgen up and the weights present it prints why and **exits 0**.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen.tools import ab_fixtures, ab_tuning, mesh_metrics  # noqa: E402

#: One row per (scene, arm).  ``baseline`` is what meshgen does today; the two
#: ensemble arms are the thing being paid for.  All three start from the same
#: base seed, so the baseline mesh is literally one of the candidates the
#: ensembles were choosing between - which is the comparison that matters.
BASE_SEED = 56

ARMS = [
    {"name": "baseline", "options": {"seed": BASE_SEED}},
    {"name": "structure5",
     "options": {"seed": BASE_SEED, "ensemble": {"structure_n": 5}}},
    {"name": "best_of3",
     "options": {"seed": BASE_SEED, "ensemble": {"best_of": 3}}},
]

SCENES = ("hard_steps", "hard_slab", "organic_blob")


# ---------------------------------------------------------------------------
# the determinism check the whole structure tier rests on
# ---------------------------------------------------------------------------
def repeat(out_dir, backend="trellis2", seed=BASE_SEED, times=3, scene="hard_steps"):
    """Probe the same seed K times and report the grid IoU between the draws.

    This one talks to the backend adapter directly rather than through
    /generate3d, because /generate3d has no way to ask for "just the head" - and
    because the number wanted here is about the probe, not about a job.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok, reason = ab_tuning.availability(backend)
    if not ok:
        print(f"skipping the determinism check: {reason}")
        print("Nothing was run and nothing failed - this needs the GPU and the "
              "23.8 GB of weights.")
        return 0

    from meshgen import config as config_module
    from meshgen import ensemble as ensemble_module
    from meshgen.backends import structure_probe
    from meshgen.comfyui_client import ComfyUIClient
    from meshgen import backends as backend_registry

    config = config_module.load()
    client = ComfyUIClient(config, log=lambda m: print(f"  [comfyui] {m}"))
    adapter = backend_registry.build(config, client)[backend]
    adapter.ensure_ready()
    client.ensure_running()

    image, _mask = ab_fixtures.write_scene(scene, out_dir / "fixtures")
    staged, names = adapter.stage_inputs(str(image))
    grids, timings = [], []
    try:
        for index in range(times):
            graph = adapter.graph_for(staged, {"seed": seed}, "forge/repeat")
            probe = structure_probe.build_structure_graph(graph, "forge/repeat")
            started = time.time()
            entry = client.run_graph(probe, prompt_id=None,
                                     progress=lambda *a: None, timeout_s=900)
            timings.append(time.time() - started)
            mesh = mesh_metrics.load_glb(
                client.find_mesh_outputs(entry, suffixes=(".glb",))[-1])
            grids.append(ensemble_module.occupancy_from_cube_mesh(
                mesh["verts"], mesh["faces"], 32))
            print(f"  draw {index + 1}/{times}: {timings[-1]:5.1f} s, "
                  f"{int(grids[-1].sum())} occupied voxels")
    finally:
        for name in names:
            client.unstage_image(name)

    ious = [ensemble_module.grid_iou(grids[0], other) for other in grids[1:]]
    row = {"scene": scene, "seed": seed, "draws": times,
           "occupied": [int(g.sum()) for g in grids],
           "probe_s": [round(t, 1) for t in timings],
           "iou_against_first": [round(v, 6) for v in ious],
           "deterministic": all(v == 1.0 for v in ious)}
    (out_dir / "determinism.json").write_text(
        json.dumps(row, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(row, indent=1))
    if not row["deterministic"]:
        print("\nNOT deterministic: the winning seed will not decode the grid it "
              "won with. The structure tier's premise does not hold on this "
              "machine and meshgen/README.md must say so.")
    return 0


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
def run(out_dir, backend="trellis2", scenes=None, arms=None, fresh=False,
        timeout_s=3600):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "results.jsonl"
    if fresh and results.exists():
        results.unlink()

    ok, reason = ab_tuning.availability(backend)
    if not ok:
        print(f"skipping the ensemble sweep: {reason}")
        print("Nothing was run and nothing failed - this needs the GPU and the "
              "23.8 GB of weights.")
        return 0

    fixtures = out_dir / "fixtures"
    cameras, masks = {}, {}
    for scene in SCENES:
        camera = ab_fixtures.camera_for(scene)
        ab_fixtures.write_scene(scene, fixtures, camera)
        cameras[scene] = camera
        masks[scene] = ab_fixtures.render(scene, camera)[1]

    wanted_scenes = set(scenes) if scenes else set(SCENES)
    wanted_arms = set(arms) if arms else {a["name"] for a in ARMS}
    todo = [(scene, arm) for scene in SCENES if scene in wanted_scenes
            for arm in ARMS if arm["name"] in wanted_arms]

    done = set()
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["scene"], row["arm"]))
    todo = [r for r in todo if (r[0], r[1]["name"]) not in done]
    print(f"{len(todo)} run(s) to go ({len(done)} already in {results.name})")

    for index, (scene, arm) in enumerate(todo, 1):
        mesh_path = out_dir / f"{scene}__{arm['name']}.glb"
        print(f"[{index}/{len(todo)}] {scene} / {arm['name']} ... ",
              end="", flush=True)
        try:
            state, peak_gb = ab_tuning.run_one(
                fixtures / f"{scene}.png", arm["options"], mesh_path, backend,
                timeout_s=timeout_s)
        except Exception as exc:                      # noqa: BLE001 - recorded
            print(f"FAILED: {exc}")
            _append(results, {"scene": scene, "arm": arm["name"],
                              "options": arm["options"], "error": str(exc)})
            continue

        row = {
            "scene": scene,
            "arm": arm["name"],
            "options": arm["options"],
            "state": state["state"],
            "wall_s": round(state["wall_s"], 1),
            "peak_vram_gb": None if peak_gb is None else round(peak_gb, 2),
            "mesh_path": str(mesh_path),
            # /job/<id> hoists the finished ensemble report to the top level,
            # replacing the request block it carried while queued
            "ensemble": state.get("ensemble"),
        }
        if state["state"] != "done":
            row["error"] = state.get("error") or state["state"]
            print(f"FAILED: {row['error']}")
            _append(results, row)
            continue

        row["metrics"] = mesh_metrics.score(mesh_path, cameras[scene], masks[scene])
        _append(results, row)
        m = row["metrics"]
        chosen = ((row["ensemble"] or {}).get("winner") or {}).get("seed", "-")
        print(f"iou {m['silhouette_iou']:.4f}  sharp {m['edge_sharpness']:.3f}  "
              f"seed {chosen}  {row['wall_s']:.0f}s  {row['peak_vram_gb']} GB")
    return 0


def _append(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------
def _seed(row):
    return ((row.get("ensemble") or {}).get("winner") or {}).get(
        "seed", row["options"].get("seed", "-"))


def _probe(row):
    block = row.get("ensemble") or {}
    ms = block.get("probe_ms") or block.get("mask_probe_ms")
    return "-" if not ms else f"{ms / 1000:.0f} s"


_COLUMNS = [
    ("arm", lambda r: r["arm"]),
    ("picked seed", _seed),
    ("IoU", lambda r: f"{r['metrics']['silhouette_iou']:.4f}"),
    ("sharp", lambda r: f"{r['metrics']['edge_sharpness']:.3f}"),
    ("creaseL", lambda r: f"{r['metrics']['crease_length']:.1f}"),
    ("selfX", lambda r: r["metrics"].get("self_intersections", "-")),
    ("shells", lambda r: r["metrics"]["components"]),
    ("faces", lambda r: f"{r['metrics']['faces']:,}"),
    ("probe", _probe),
    ("wall", lambda r: f"{r['wall_s']:.0f} s"),
    ("VRAM", lambda r: f"{r['peak_vram_gb']:.2f}" if r.get("peak_vram_gb") else "-"),
]


def report(out_dir, scene=None):
    results = Path(out_dir) / "results.jsonl"
    if not results.is_file():
        print(f"no results at {results}")
        return 1
    rows = [json.loads(line) for line in
            results.read_text(encoding="utf-8").splitlines() if line.strip()]

    determinism = Path(out_dir) / "determinism.json"
    if determinism.is_file():
        row = json.loads(determinism.read_text(encoding="utf-8"))
        print(f"\n**Seed replay**: {row['draws']} probes of seed {row['seed']} on "
              f"`{row['scene']}` agreed at IoU "
              f"{', '.join(str(v) for v in row['iou_against_first'])} "
              f"({'deterministic' if row['deterministic'] else 'NOT deterministic'}); "
              f"probe cost {row['probe_s']} s each.\n")

    for name in ([scene] if scene else sorted({r["scene"] for r in rows})):
        subset = [r for r in rows if r["scene"] == name and "metrics" in r]
        if not subset:
            continue
        print(f"\n### {name}\n")
        print("| " + " | ".join(c[0] for c in _COLUMNS) + " |")
        print("|" + "|".join("---" for _ in _COLUMNS) + "|")
        for row in subset:
            print("| " + " | ".join(str(c[1](row)) for c in _COLUMNS) + " |")
        for row in [r for r in rows if r["scene"] == name and "metrics" not in r]:
            print(f"\n{row['arm']}: FAILED - {row.get('error')}")
        for row in subset:
            why = (row.get("ensemble") or {}).get("why")
            if why:
                print(f"\n`{row['arm']}` -> {why}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    checker = sub.add_parser("repeat", help="is seed replay deterministic here?")
    checker.add_argument("--out", required=True)
    checker.add_argument("--backend", default="trellis2")
    checker.add_argument("--seed", type=int, default=BASE_SEED)
    checker.add_argument("--times", type=int, default=3)
    checker.add_argument("--scene", default="hard_steps")

    runner = sub.add_parser("run", help="run the arms on the GPU")
    runner.add_argument("--out", required=True)
    runner.add_argument("--backend", default="trellis2")
    runner.add_argument("--scenes", default=None,
                        help="comma-separated subset of " + ",".join(SCENES))
    runner.add_argument("--arms", default=None, help="comma-separated subset")
    runner.add_argument("--fresh", action="store_true")

    printer = sub.add_parser("report", help="print the measured table")
    printer.add_argument("out")
    printer.add_argument("--scene", default=None)

    args = parser.parse_args(argv)
    if args.command == "repeat":
        return repeat(args.out, args.backend, args.seed, args.times, args.scene)
    if args.command == "run":
        return run(args.out, args.backend,
                   scenes=args.scenes.split(",") if args.scenes else None,
                   arms=args.arms.split(",") if args.arms else None,
                   fresh=args.fresh)
    return report(args.out, args.scene)


if __name__ == "__main__":
    raise SystemExit(main())
