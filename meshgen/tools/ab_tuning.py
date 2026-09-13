"""A/B the surface-quality settings on real GPU runs, and score the results.

meshgen's ``image_to_3d.json`` began life as the official ComfyUI template, and
a template is a demo: several of its values are not the node's own default and
were never justified anywhere.  ``RemeshMesh.smooth_iters`` ships at **20**, the
maximum the node accepts, against a tooltip that says 2-3; ``qef`` is off, so
sharp-feature placement is off; ``project_back`` is 0, so nothing is snapped
back to the surface the model actually produced; both ``MeshSmoothNormals``
nodes sit at ``crease_angle`` 180, which means "no edge is ever hard".

None of that is knowable by reading.  So this runs the real pipeline at each
setting, on deterministic synthetic inputs whose ground truth is exact, and
scores every output with :mod:`meshgen.tools.mesh_metrics` — geometry only, no
VLM, no judgement.  Wall time and peak VRAM are recorded next to the score
because a variant that costs twice the time for one percent of IoU is a loss.

    service\\.venv\\Scripts\\python.exe -m meshgen.tools.ab_tuning run  --out <dir>
    service\\.venv\\Scripts\\python.exe -m meshgen.tools.ab_tuning report <dir>

``run`` needs meshgen up on 8902 with the weights present and a GPU behind it.
Without them it prints why and **exits 0** — the same contract the pytest suites
keep, so this file is safe to invoke from a machine that has none of the 23.8 GB.

Results are appended to ``<dir>/results.jsonl`` one row at a time, and ``run``
skips rows already there, so an interrupted sweep resumes instead of paying for
the finished runs twice.

One deliberate non-knob: **negative prompts**.  At the shape stage the template
wires ``negative`` from the same conditioning node's second output, which for
both backends is a zero tensor — the architecture has no text conditioning to
negate.  A ``negative_prompt`` option would be a control that does nothing, so
there is not one and there should never be one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen.tools import ab_fixtures, mesh_metrics  # noqa: E402

BASE_URL = "http://127.0.0.1:8902"
POLL_S = 2.0
VRAM_SAMPLE_S = 0.2

#: The template's own numbers, spelled out rather than inherited.  Every variant
#: states all five knobs, so a row stays meaningful after the defaults change -
#: which is the whole point of the exercise.
TEMPLATE = {
    "smooth_iters": 20,
    "qef": False,
    "project_back": 0.0,
    "crease_angle": 180.0,
    "steps": 12,
    "cfg": 7.5,
    "seed": 56,
}


def _variant(name, group, **overrides):
    return {"name": name, "group": group, "options": {**TEMPLATE, **overrides}}


#: The sweep.  Grouped so the report can show one knob at a time against the
#: same baseline, and ordered so the cheapest questions are answered first.
VARIANTS = [
    _variant("baseline", "baseline"),

    _variant("smooth3", "smooth_iters", smooth_iters=3),
    _variant("smooth2", "smooth_iters", smooth_iters=2),
    _variant("smooth0", "smooth_iters", smooth_iters=0),

    _variant("qef_on", "qef", qef=True),

    _variant("project05", "project_back", project_back=0.5),
    _variant("project10", "project_back", project_back=1.0),

    _variant("crease45", "crease_angle", crease_angle=45.0),

    # the checkpoint's numbers vs the demo's.  These move TOGETHER, so the pair
    # is what the question was about - and steps25_cfg75 is here because the
    # pair result was surprising enough to need the knobs separated.
    _variant("steps25_cfg5", "sampler", steps=25, cfg=5.0),
    _variant("steps25_cfg75", "sampler", steps=25),
    _variant("cfg5", "sampler", cfg=5.0),
]

#: Candidate stacks, built from what actually won rather than from what was
#: expected to.  ``project_back`` is deliberately NOT in here: at 0.5 it bought
#: +0.0007 IoU for 70 self-intersections, and at 1.0 it produced 13 391 of them
#: across 465 shells.  It snaps dual-contoured vertices back onto the raw model
#: surface, which is the self-intersecting mess the remesh exists to remove.
STACKS = [
    _variant("winner", "stack", smooth_iters=3, qef=True),
    _variant("winner_hard", "stack", smooth_iters=3, qef=True, crease_angle=45.0),
]

#: scene -> which variants to run it through.  The full sweep runs on the
#: hard-surface fixture because that is where these settings can do damage; the
#: control scenes run the baseline and the candidate stacks, which is what shows
#: whether a hard-surface win costs anything on an organic shape.
SWEEP_SCENE = "hard_steps"
CONTROL_SCENES = ("hard_slab", "organic_blob")
CONTROL_VARIANTS = ("baseline", "winner", "winner_hard")


# ---------------------------------------------------------------------------
# talking to meshgen
# ---------------------------------------------------------------------------
def _get(path, timeout=10):
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8"))


def _post(path, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8"))


def availability(backend):
    """``(ok, reason)`` - why this machine cannot run the sweep, in one line."""
    try:
        health = _get("/health", timeout=5)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"meshgen is not answering on {BASE_URL} ({exc})"
    for entry in health.get("available_backends") or []:
        if entry["name"] != backend:
            continue
        if not entry.get("ready"):
            missing = ", ".join(m["what"] for m in entry.get("missing") or [])
            return False, f"backend {backend!r} is not ready: {missing}"
        return True, "ready"
    return False, f"backend {backend!r} is not registered on this meshgen"


class VramSampler(threading.Thread):
    """Peak GPU memory from nvidia-smi, because ComfyUI's own report misses it.

    meshgen samples ComfyUI's device report on a coarse interval and came in
    1.0-1.5 GB low against nvidia-smi on the multi-view runs (see README).  A
    tuning table that decides what fits on a 12 GB card cannot be built on the
    low number, so both are recorded and the report prints this one.
    """

    def __init__(self, interval=VRAM_SAMPLE_S):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_mib = None
        self.samples = 0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, check=False)
                value = int(out.stdout.strip().splitlines()[0])
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                value = None
            if value is not None:
                self.samples += 1
                self.peak_mib = value if self.peak_mib is None else max(self.peak_mib, value)
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        self.join(timeout=5)
        return None if self.peak_mib is None else self.peak_mib / 1024.0


def run_one(image_path, options, out_path, backend, timeout_s=1800):
    """Submit one job, wait for it, return ``(job payload, peak GB)``."""
    sampler = VramSampler()
    sampler.start()
    started = time.time()
    try:
        job = _post("/generate3d", {
            "image_path": str(image_path),
            "backend": backend,
            "options": options,
            "output": str(out_path),
        })
        job_id = job["job_id"]
        while True:
            time.sleep(POLL_S)
            state = _get(f"/job/{job_id}")
            if state["state"] in ("done", "error", "cancelled"):
                break
            if time.time() - started > timeout_s:
                _post(f"/cancel/{job_id}", {})
                raise TimeoutError(f"job {job_id} exceeded {timeout_s}s")
    finally:
        peak_gb = sampler.stop()
    state["wall_s"] = time.time() - started
    return state, peak_gb


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
def plan(scenes=None, variants=None):
    """``[(scene, variant), ...]`` in run order."""
    by_name = {v["name"]: v for v in VARIANTS + STACKS}
    wanted = set(variants) if variants else None
    rows = []
    for variant in VARIANTS + STACKS:
        if wanted and variant["name"] not in wanted:
            continue
        rows.append((SWEEP_SCENE, variant))
    for scene in CONTROL_SCENES:
        for name in CONTROL_VARIANTS:
            variant = by_name.get(name)
            if variant is None or (wanted and name not in wanted):
                continue
            rows.append((scene, variant))
    if scenes:
        rows = [r for r in rows if r[0] in set(scenes)]
    return rows


def _done_keys(results_path):
    done = set()
    if results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["scene"], row["variant"]))
    return done


def run(out_dir, backend="trellis2", scenes=None, variants=None, fresh=False,
        self_intersections=True):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "results.jsonl"
    if fresh and results.exists():
        results.unlink()

    ok, reason = availability(backend)
    if not ok:
        print(f"skipping the A/B sweep: {reason}")
        print("Nothing was run and nothing failed - this needs the GPU and the "
              "23.8 GB of weights, exactly like the real-model runs in the README.")
        return 0

    fixtures = out_dir / "fixtures"
    cameras = {}
    masks = {}
    for scene in ab_fixtures.SCENES:
        camera = ab_fixtures.camera_for(scene)
        ab_fixtures.write_scene(scene, fixtures, camera)
        cameras[scene] = camera
        masks[scene] = ab_fixtures.render(scene, camera)[1]
    (out_dir / "cameras.json").write_text(
        json.dumps(cameras, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    todo = plan(scenes, variants)
    done = _done_keys(results)
    todo = [r for r in todo if (r[0], r[1]["name"]) not in done]
    print(f"{len(todo)} run(s) to go ({len(done)} already in {results.name})")

    for index, (scene, variant) in enumerate(todo, 1):
        name = variant["name"]
        mesh_path = out_dir / f"{scene}__{name}.glb"
        print(f"[{index}/{len(todo)}] {scene} / {name} ... ", end="", flush=True)
        try:
            state, peak_gb = run_one(fixtures / f"{scene}.png", variant["options"],
                                     mesh_path, backend)
        except Exception as exc:                      # noqa: BLE001 - recorded, not raised
            print(f"FAILED: {exc}")
            _append(results, {"scene": scene, "variant": name, "group": variant["group"],
                              "options": variant["options"], "error": str(exc)})
            continue

        row = {
            "scene": scene,
            "variant": name,
            "group": variant["group"],
            "options": variant["options"],
            "state": state["state"],
            "wall_s": round(state["wall_s"], 1),
            "peak_vram_gb": None if peak_gb is None else round(peak_gb, 2),
            "reported_vram_gb": ((state.get("result") or {}).get("vram") or {}).get("peak_gb"),
            "mesh_path": str(mesh_path),
        }
        if state["state"] != "done":
            row["error"] = state.get("error") or state["state"]
            print(f"FAILED: {row['error']}")
            _append(results, row)
            continue

        row["metrics"] = mesh_metrics.score(
            mesh_path, cameras[scene], masks[scene],
            want_self_intersections=self_intersections)
        _append(results, row)
        m = row["metrics"]
        print(f"iou {m['silhouette_iou']:.4f}  sharp {m['edge_sharpness']:.3f}  "
              f"{row['wall_s']:.0f}s  {row['peak_vram_gb']} GB")
    return 0


def _append(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------
_COLUMNS = [
    ("variant", "variant", lambda r: r["variant"]),
    ("smooth", "smooth_iters", lambda r: r["options"]["smooth_iters"]),
    ("qef", "qef", lambda r: "on" if r["options"]["qef"] else "off"),
    ("proj", "project_back", lambda r: r["options"]["project_back"]),
    ("crease", "crease_angle", lambda r: r["options"]["crease_angle"]),
    ("steps/cfg", "steps/cfg",
     lambda r: f"{r['options']['steps']}/{r['options']['cfg']:g}"),
    ("IoU", "silhouette_iou", lambda r: f"{r['metrics']['silhouette_iou']:.4f}"),
    ("sharp", "edge_sharpness", lambda r: f"{r['metrics']['edge_sharpness']:.3f}"),
    ("soft%", "soft_edge_fraction",
     lambda r: f"{100 * r['metrics']['soft_edge_fraction']:.1f}"),
    # total sharp edge length over the bbox diagonal.  The staircase detector:
    # a ziggurat has a handful of real creases, so a big jump here is Dual
    # Contouring's voxel steps being counted as detail, not detail appearing.
    ("creaseL", "crease_length", lambda r: f"{r['metrics']['crease_length']:.1f}"),
    ("crease n95", "crease_normal_p95",
     lambda r: "-" if r["metrics"].get("crease_normal_p95") is None
     else f"{r['metrics']['crease_normal_p95']:.1f}"),
    ("faces", "faces", lambda r: f"{r['metrics']['faces']:,}"),
    ("verts", "verts", lambda r: f"{r['metrics']['verts']:,}"),
    # exported verts over welded verts: what crease_angle costs the atlas, since
    # splitting a vertex to keep an edge hard is how MeshSmoothNormals works
    ("uv x", "uv_split_ratio", lambda r: f"{r['metrics']['uv_split_ratio']:.2f}"),
    ("bnd", "boundary_edges", lambda r: r["metrics"]["boundary_edges"]),
    ("nonman", "nonmanifold_edges", lambda r: r["metrics"]["nonmanifold_edges"]),
    ("selfX", "self_intersections",
     lambda r: r["metrics"].get("self_intersections", "-")),
    ("shells", "components", lambda r: r["metrics"]["components"]),
    ("time", "wall_s", lambda r: f"{r['wall_s']:.0f} s"),
    ("VRAM", "peak_vram_gb", lambda r: f"{r['peak_vram_gb']:.2f}"
     if r.get("peak_vram_gb") else "-"),
]


def report(out_dir, scene=None):
    rows = []
    results = Path(out_dir) / "results.jsonl"
    if not results.is_file():
        print(f"no results at {results}")
        return 1
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    scenes = [scene] if scene else sorted({r["scene"] for r in rows})
    for name in scenes:
        subset = [r for r in rows if r["scene"] == name and "metrics" in r]
        if not subset:
            continue
        print(f"\n### {name}\n")
        header = [c[0] for c in _COLUMNS]
        print("| " + " | ".join(header) + " |")
        print("|" + "|".join("---" for _ in header) + "|")
        for row in subset:
            print("| " + " | ".join(str(c[2](row)) for c in _COLUMNS) + " |")
        failed = [r for r in rows if r["scene"] == name and "metrics" not in r]
        for row in failed:
            print(f"\n{row['variant']}: FAILED - {row.get('error')}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    runner = sub.add_parser("run", help="run the sweep on the GPU")
    runner.add_argument("--out", required=True)
    runner.add_argument("--backend", default="trellis2")
    runner.add_argument("--scenes", default=None,
                        help="comma-separated subset of " + ",".join(ab_fixtures.SCENES))
    runner.add_argument("--variants", default=None, help="comma-separated subset")
    runner.add_argument("--fresh", action="store_true", help="discard previous results")
    runner.add_argument("--no-self-intersections", action="store_true")

    printer = sub.add_parser("report", help="print the measured table")
    printer.add_argument("out")
    printer.add_argument("--scene", default=None)

    fixtures = sub.add_parser("fixtures", help="write the test images and stop")
    fixtures.add_argument("out")

    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.out, args.backend,
                   scenes=args.scenes.split(",") if args.scenes else None,
                   variants=args.variants.split(",") if args.variants else None,
                   fresh=args.fresh,
                   self_intersections=not args.no_self_intersections)
    if args.command == "report":
        return report(args.out, args.scene)
    return ab_fixtures.main([args.out])


if __name__ == "__main__":
    raise SystemExit(main())
