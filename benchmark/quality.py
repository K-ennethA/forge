"""The benchmark's quality tier — deterministic only.

Two halves, one file:

* **Measuring** (:func:`measure`) takes the produced parts as millimetre
  meshes — ``{object_name: (vertices_mm, faces)}`` — and runs

  (a) the **hard gates**: the part pipeline's own check stage
      (``mcp/forge_mcp/pipeline.py`` ``_part_stages`` "check": bed_fit,
      min_wall, overhangs, watertight).  Nothing is reimplemented: each part
      goes through exactly the ``/check_mesh`` body the geometry service runs
      (``service.worker.handle_check_mesh``) — ``mesh_input.load_mesh_input``
      (triangulate, weld, fix winding) -> ``runner.compute_mesh_stats`` ->
      ``checks.run_checks`` against ``printer.normalize_printer`` of the task's
      printer profile — called in-process instead of over HTTP, so no service
      has to be running to benchmark;
  (b) the **geometric fidelity** metrics named in ``task.json``'s
      ``expected`` block (see :data:`METRIC_KINDS`).

* **Grading** (:func:`grade`) folds those numbers into the report's
  ``gates`` / ``fidelity`` / ``score`` blocks.

Loading a ``.glb`` / ``.blend`` / ``.stl`` needs Blender, so :func:`evaluate`
spawns ONE hidden headless Blender (``--background``, ``CREATE_NO_WINDOW``,
output to a log file) running ``benchmark/blender_measure.py``, which reads the
meshes out with the add-on's own ``evaluated_mesh_mm`` and calls
:func:`measure`.  Everything below :func:`evaluate` is pure Python.

There is no appearance/VLM scoring here: ``appearance_score`` is ``None`` and
stays ``None`` until a separate lane fills the hook.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark import geometry  # noqa: E402

#: The part pipeline's check-stage gate, verbatim from
#: ``mcp/forge_mcp/pipeline.py`` (``_part_stages`` -> "check").
DEFAULT_GATE_CHECKS: Tuple[str, ...] = ("bed_fit", "min_wall", "overhangs", "watertight")

#: The worker's own default for ``/check_mesh`` (``service/worker.py``
#: ``handle_check_mesh``: ``max_wall_samples or 4000``).
MAX_WALL_SAMPLES = 4000

DEFAULT_PRINTER = os.path.join("templates", "printer.json")

#: Blender's metres to the service's millimetres — the add-on's ``M_TO_MM``.
#: STL written by ``partforge_export`` is already in millimetres.
DEFAULT_UNIT_SCALE = {".glb": 1000.0, ".gltf": 1000.0, ".blend": 1000.0,
                      ".stl": 1.0, ".obj": 1.0}

METRIC_KINDS = ("overall_dim", "part_count", "volume", "ray", "min_wall",
                "mirrored_yaw", "volume_ratio", "silhouette_iou")

#: A hook for a later appearance lane; deterministic tiers only in this one.
appearance_score = None

DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# task + printer
# ---------------------------------------------------------------------------

def load_task(task_dir: str) -> Dict[str, Any]:
    """``task_dir/task.json``, checked for the keys the harness relies on."""
    path = os.path.join(task_dir, "task.json")
    with open(path, "r", encoding="utf-8") as handle:
        task = json.load(handle)
    for key in ("name", "prompt", "mode", "timeout_s", "expected"):
        if key not in task:
            raise ValueError("%s is missing %r" % (path, key))
    if not isinstance(task["expected"], dict) or not task["expected"]:
        raise ValueError("%s: 'expected' must be a non-empty object" % path)
    for name, spec in task["expected"].items():
        kind = spec.get("kind")
        if kind not in METRIC_KINDS:
            raise ValueError("%s: metric %r has unknown kind %r (one of %s)"
                             % (path, name, kind, ", ".join(METRIC_KINDS)))
    task["_dir"] = os.path.abspath(task_dir)
    return task


def load_printer(task: Mapping[str, Any]) -> Dict[str, Any]:
    from service.printer import normalize_printer

    rel = task.get("printer") or DEFAULT_PRINTER
    path = rel if os.path.isabs(rel) else os.path.join(REPO_ROOT, rel)
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return normalize_printer(raw)


def artifact_path(task: Mapping[str, Any]) -> Optional[str]:
    rel = task.get("artifact")
    if not rel:
        return None
    return rel if os.path.isabs(rel) else os.path.normpath(os.path.join(REPO_ROOT, rel))


# ---------------------------------------------------------------------------
# measuring (pure Python; called inside Blender by blender_measure.py)
# ---------------------------------------------------------------------------

def select(names: Sequence[str], pattern: Optional[str]) -> List[str]:
    """Object names matching ``pattern`` (``re.search``), sorted — deterministic."""
    if not pattern:
        return sorted(names)
    rx = re.compile(pattern)
    return sorted(n for n in names if rx.search(n))


class _Part:
    """One object's mesh, welded and triangulated the way ``/check_mesh`` does."""

    def __init__(self, name, vertices, faces):
        from service.mesh_input import load_mesh_input

        loaded = load_mesh_input({"mesh": {"vertices": [list(v) for v in vertices],
                                           "faces": [list(f) for f in faces]}})
        self.name = name
        self.vertices = [tuple(v) for v in loaded["vertices"]]
        self.triangles = [tuple(f) for f in loaded["faces"]]
        self.info = loaded["info"]
        self._checks = None

    def checks(self, printer):
        if self._checks is None:
            from service.checks import run_checks
            from service.runner import compute_mesh_stats

            stats = compute_mesh_stats(self.vertices, self.triangles,
                                       self.info.get("degenerate_faces_dropped", 0))
            self._checks = run_checks(self.vertices, self.triangles, stats, printer,
                                      max_wall_samples=MAX_WALL_SAMPLES)
            self._checks["stats"] = stats
        return self._checks

    def check(self, printer, name):
        for entry in self.checks(printer)["checks"]:
            if entry["name"] == name:
                return entry
        return None


def _min_wall_mm(entry: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Thinnest wall the gate measured; the probe length when nothing was hit.

    ``check_min_wall`` reports "at least probe_mm" when every probe ran its
    full length — a lower bound, and the honest number to compare to a floor.
    """
    if not entry:
        return None
    data = entry.get("data") or {}
    if data.get("min_measured_thickness_mm") is not None:
        return float(data["min_measured_thickness_mm"])
    if data.get("measured") == 0 and data.get("unmeasured"):
        return float(data.get("probe_mm") or 0.0)
    return None


def measure(task: Mapping[str, Any], meshes: Mapping[str, Tuple[list, list]],
            printer: Mapping[str, Any]) -> Dict[str, Any]:
    """Gate results and raw fidelity measurements for one produced scene."""
    parts: Dict[str, _Part] = {}

    def part(name):
        if name not in parts:
            vertices, faces = meshes[name]
            parts[name] = _Part(name, vertices, faces)
        return parts[name]

    names = list(meshes)

    # -- (a) hard gates ------------------------------------------------------
    gate_spec = task.get("gates") or {}
    checks = list(gate_spec.get("checks") or DEFAULT_GATE_CHECKS)
    groups = gate_spec.get("parts") or [{"match": None, "count": 1}]
    gate_rows = []
    for group in groups:
        chosen = select(names, group.get("match"))
        count = int(group.get("count", 1))
        for slot in range(count):
            label = chosen[slot] if slot < len(chosen) else None
            for check_name in checks:
                if label is None:
                    gate_rows.append({"part": "<missing %s #%d>" % (group.get("match"), slot + 1),
                                      "check": check_name, "status": "missing",
                                      "passed": False,
                                      "details": "no object matched %r" % group.get("match")})
                    continue
                entry = part(label).check(printer, check_name)
                status = entry["status"] if entry else "absent"
                gate_rows.append({"part": label, "check": check_name, "status": status,
                                  # overhangs never fail, they warn (supports
                                  # exist) — the pipeline treats warn as green.
                                  "passed": status in ("pass", "warn"),
                                  "details": entry.get("details") if entry else
                                  "check %r not in the report" % check_name})

    # -- (b) fidelity ----------------------------------------------------------
    measured: Dict[str, Any] = {}
    for metric, spec in (task.get("expected") or {}).items():
        try:
            measured[metric] = _measure_metric(spec, names, part, meshes, printer, task)
        except Exception as exc:  # noqa: BLE001 - one bad metric never sinks the rest
            measured[metric] = {"value": None, "detail": "measurement failed: %s" % exc}

    summary = {}
    for name in sorted(names):
        vertices, faces = meshes[name]
        lo, hi = geometry.bounds(vertices) if vertices else ((0, 0, 0), (0, 0, 0))
        summary[name] = {"vertices": len(vertices), "faces": len(faces),
                         "bbox_min_mm": [round(v, 4) for v in lo],
                         "bbox_max_mm": [round(v, 4) for v in hi]}
    return {"gate_rows": gate_rows, "measured": measured, "parts": summary}


def _measure_metric(spec, names, part, meshes, printer, task=None):
    kind = spec["kind"]
    chosen = select(names, spec.get("match"))

    if kind == "part_count":
        return {"value": len(chosen), "detail": chosen}

    if not chosen:
        return {"value": None, "detail": "no object matched %r" % spec.get("match")}

    if kind == "overall_dim":
        axis = "xyz".index(spec["axis"].lower())
        lo = min(geometry.bounds(meshes[n][0])[0][axis] for n in chosen)
        hi = max(geometry.bounds(meshes[n][0])[1][axis] for n in chosen)
        return {"value": hi - lo, "detail": {"min_mm": lo, "max_mm": hi}}

    if kind == "volume":
        per = {}
        for n in chosen:
            p = part(n)
            per[n] = geometry.enclosed_volume(p.vertices, p.triangles)
        return {"value": math.fsum(per.values()), "detail": per}

    if kind == "ray":
        verts, tris = [], []
        for n in chosen:
            p = part(n)
            base = len(verts)
            verts.extend(p.vertices)
            tris.extend(tuple(base + i for i in t) for t in p.triangles)
        hits = geometry.ray_hits(verts, tris, spec["origin_mm"], spec["direction"],
                                 max_distance=float(spec.get("max_distance_mm", 1000.0)))
        pick = spec.get("measure", "hit")
        if pick == "hit":
            index = int(spec.get("index", 0))
            value = hits[index] if index < len(hits) else None
        elif pick == "span":
            i, j = spec["between"]
            value = hits[j] - hits[i] if max(i, j) < len(hits) else None
        else:
            raise ValueError("ray measure must be 'hit' or 'span', got %r" % pick)
        return {"value": value, "detail": {"hits_mm": hits}}

    if kind == "min_wall":
        per = {n: _min_wall_mm(part(n).check(printer, "min_wall")) for n in chosen}
        known = [v for v in per.values() if v is not None]
        value = min(known) if len(known) == len(per) else None
        return {"value": value, "detail": per}

    if kind == "volume_ratio":
        per = {}
        for n in chosen:
            p = part(n)
            per[n] = geometry.volume_ratio(p.vertices, p.triangles)
        return {"value": min(r["ratio"] for r in per.values()), "detail": per}

    if kind == "silhouette_iou":
        # benchmark/silhouette.py: each part projected onto its widest OBB plane,
        # outline traced, normalized (unit height, principal axes, up = up_axis),
        # IoU against the task's frozen outline on a fixed grid, mirror-invariant.
        from benchmark import silhouette

        base = (task or {}).get("_dir") or REPO_ROOT
        reference = silhouette.load_outline(os.path.join(base, spec["outline"]))
        up = spec.get("up_axis", (0.0, 0.0, 1.0))
        grid = int(spec.get("grid", silhouette.GRID))
        per = {}
        for n in chosen:
            p = part(n)
            per[n] = silhouette.measure_part(p.vertices, p.triangles, reference, up, grid)
        return {"value": min(r["iou"] for r in per.values()), "detail": per}

    if kind == "mirrored_yaw":
        front = spec.get("front_axis", (1.0, 0.0, 0.0))
        up = spec.get("up_axis", (0.0, 0.0, 1.0))
        per = {}
        for n in chosen:
            p = part(n)
            per[n] = geometry.facing_yaw_deg(p.vertices, p.triangles, front, up)
        yaws = {n: (r["yaw_deg"] if r else None) for n, r in per.items()}
        mirror = None
        if len(chosen) == 2 and all(per[n] for n in chosen):
            mirror = geometry.mirror_angle_deg(per[chosen[0]]["normal"],
                                               per[chosen[1]]["normal"], front, up)
        return {"value": {"yaw_deg": yaws, "mirror_angle_deg": mirror}, "detail": per}

    raise ValueError("unknown metric kind %r" % kind)


# ---------------------------------------------------------------------------
# grading (pure Python)
# ---------------------------------------------------------------------------

def _grade_one(spec: Mapping[str, Any], value: Any) -> Dict[str, Any]:
    """``{measured, expected, tol, op, pass, closeness}`` for one metric.

    ``closeness`` in [0, 1] is what :func:`score` averages — continuous, so a
    run that lands nearer the target scores higher even at equal pass counts:
    ``1 / (1 + |error| / tol)`` for a target±tol (0.5 exactly at the tolerance
    edge), 1 / 0 for pass/fail-only metrics, and for a bound 1 when met and
    ``0.5 / (1 + shortfall / |bound|)`` when missed.
    """
    kind = spec["kind"]
    row: Dict[str, Any] = {"kind": kind, "measured": value}

    if kind == "mirrored_yaw":
        min_abs = float(spec.get("min_abs_deg", 0.0))
        mirror_tol = float(spec.get("mirror_tol_deg", 180.0))
        row.update(expected="yaw signs opposite about up, |yaw| >= %g deg, face "
                            "axes mirror images within %g deg" % (min_abs, mirror_tol),
                   tol=mirror_tol, op="mirrored")
        value = value or {}
        yaws = list((value.get("yaw_deg") or {}).values())
        mirror = value.get("mirror_angle_deg")
        count_ok = len(yaws) == int(spec.get("count", 2))
        defined = count_ok and all(y is not None for y in yaws) and mirror is not None
        reasons = []
        if not defined:
            reasons.append("need exactly %d parts with a defined face axis"
                           % int(spec.get("count", 2)))
        else:
            if not yaws[0] * yaws[1] < 0.0:
                reasons.append("yaws %.1f / %.1f deg have the same sign (angled the "
                               "same direction)" % (yaws[0], yaws[1]))
            if not all(abs(y) >= min_abs for y in yaws):
                reasons.append("a yaw is under %g deg" % min_abs)
            if mirror > mirror_tol:
                reasons.append("face axes are %.1f deg off mirror images (one "
                               "transform rotated, not mirrored)" % mirror)
        ok = defined and not reasons
        row["pass"] = bool(ok)
        row["closeness"] = 1.0 if ok else 0.0
        if reasons:
            row["why_failed"] = reasons
        return row

    if value is None:
        row.update(expected=spec.get("expected", spec.get("min", spec.get("max"))),
                   tol=spec.get("tol"), op=_op(spec), **{"pass": False}, closeness=0.0)
        return row

    value = float(value)
    if "expected" in spec:
        expected = float(spec["expected"])
        tol = float(spec.get("tol", 0.0))
        error = abs(value - expected)
        ok = error <= tol + 1e-9
        closeness = 1.0 / (1.0 + error / tol) if tol > 0 else (1.0 if ok else 1.0 / (1.0 + error))
        row.update(expected=expected, tol=tol, op="==", error=error)
    elif "min" in spec:
        expected = float(spec["min"])
        ok = value >= expected - 1e-9
        shortfall = max(0.0, expected - value)
        closeness = 1.0 if ok else 0.5 / (1.0 + shortfall / max(abs(expected), 1e-9))
        row.update(expected=expected, tol=None, op=">=")
    elif "max" in spec:
        expected = float(spec["max"])
        ok = value <= expected + 1e-9
        excess = max(0.0, value - expected)
        closeness = 1.0 if ok else 0.5 / (1.0 + excess / max(abs(expected), 1e-9))
        row.update(expected=expected, tol=None, op="<=")
    else:
        raise ValueError("metric needs 'expected'(+'tol'), 'min' or 'max': %r" % (spec,))
    row["pass"] = bool(ok)
    row["closeness"] = closeness
    return row


def _op(spec):
    if "expected" in spec:
        return "=="
    if "min" in spec:
        return ">="
    if "max" in spec:
        return "<="
    return spec.get("kind")


def grade(task: Mapping[str, Any], measurement: Optional[Mapping[str, Any]],
          missing_reason: Optional[str] = None) -> Dict[str, Any]:
    """Report blocks ``gates`` / ``fidelity`` / ``score`` / ``appearance_score``.

    ``measurement`` is ``None`` when there was nothing to measure (no artifact,
    a stale one, Blender failed): every gate and every metric then fails with
    ``missing_reason``, and the totals stay what the task says they are, so a
    failed run and a good run are still comparable number for number.
    """
    expected = task.get("expected") or {}
    if measurement is None:
        gate_spec = task.get("gates") or {}
        checks = list(gate_spec.get("checks") or DEFAULT_GATE_CHECKS)
        groups = gate_spec.get("parts") or [{"match": None, "count": 1}]
        total = sum(int(g.get("count", 1)) for g in groups) * len(checks)
        gates = {"passed": 0, "total": total,
                 "failures": ["no measurement: %s" % (missing_reason or "unknown")]}
        fidelity = {}
        for name, spec in expected.items():
            row = _grade_one(spec, None if spec["kind"] != "mirrored_yaw" else {})
            row["detail"] = missing_reason
            fidelity[name] = row
        return {"gates": gates, "fidelity": fidelity, "score": 0.0,
                "appearance_score": appearance_score}

    rows = measurement.get("gate_rows") or []
    gates = {"passed": sum(1 for r in rows if r["passed"]),
             "total": len(rows),
             "failures": ["%s: %s %s — %s" % (r["part"], r["check"], r["status"],
                                              r.get("details"))
                          for r in rows if not r["passed"]]}

    fidelity = {}
    raw = measurement.get("measured") or {}
    for name, spec in expected.items():
        got = raw.get(name) or {}
        row = _grade_one(spec, got.get("value"))
        if got.get("detail") is not None:
            row["detail"] = got.get("detail")
        fidelity[name] = row
    return {"gates": gates, "fidelity": fidelity, "score": score(fidelity),
            "appearance_score": appearance_score}


def score(fidelity: Mapping[str, Mapping[str, Any]]) -> float:
    """100 x the mean closeness of every fidelity metric, 3 decimals."""
    if not fidelity:
        return 0.0
    values = [float(row.get("closeness") or 0.0) for row in fidelity.values()]
    return round(100.0 * math.fsum(values) / len(values), 3)


def quality_key(report: Mapping[str, Any]) -> Tuple[int, int, float]:
    """The binding quality order: gates passed, then fidelity passes, then score."""
    gates = report.get("gates") or {}
    fidelity = report.get("fidelity") or {}
    return (int(gates.get("passed") or 0),
            sum(1 for row in fidelity.values() if row.get("pass")),
            round(float(report.get("score") or 0.0), 3))


# ---------------------------------------------------------------------------
# evaluate: one hidden headless Blender per artifact
# ---------------------------------------------------------------------------

def find_blender() -> Optional[str]:
    for candidate in (os.environ.get("FORGE_BLENDER_EXE"), DEFAULT_BLENDER):
        if candidate and os.path.isfile(candidate):
            return candidate
    from shutil import which

    return which("blender")


def evaluate(task: Mapping[str, Any], artifact: Optional[str],
             log_path: Optional[str] = None, blender: Optional[str] = None,
             timeout_s: float = 1800.0) -> Dict[str, Any]:
    """Measure ``artifact`` against ``task`` and grade it.

    Returns the :func:`grade` blocks plus ``measure`` (the raw measurement, or
    ``{"error": ...}``) and ``measure_seconds``.
    """
    started = time.monotonic()
    if not artifact or not os.path.isfile(artifact):
        reason = "artifact not found: %s" % artifact
        out = grade(task, None, reason)
        out.update(measure={"error": reason}, measure_seconds=0.0)
        return out

    exe = blender or find_blender()
    if not exe:
        reason = "no Blender executable (set FORGE_BLENDER_EXE)"
        out = grade(task, None, reason)
        out.update(measure={"error": reason}, measure_seconds=0.0)
        return out

    task_json = os.path.join(task["_dir"], "task.json")
    # Never beside the frozen task: its folder is an input, not a scratch space.
    import tempfile

    stem = os.path.splitext(log_path)[0] if log_path else os.path.join(
        tempfile.gettempdir(), "forge-bench-%s" % task.get("name", "task"))
    out_json = stem + ".measure.json"
    log_file = stem + ".measure.log"
    if os.path.exists(out_json):
        os.remove(out_json)
    argv = [exe, "--background", "--factory-startup",
            "--python", os.path.join(HERE, "blender_measure.py"),
            "--", task_json, artifact, out_json]
    measurement = None
    reason = None
    with open(log_file, "w", encoding="utf-8", errors="replace") as log:
        try:
            proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=timeout_s,
                                  creationflags=_NO_WINDOW)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = None
            reason = "measurement timed out after %.0fs" % timeout_s
    if os.path.isfile(out_json):
        with open(out_json, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("error"):
            reason = payload["error"]
        else:
            measurement = payload
    elif reason is None:
        reason = "Blender exited %s without a measurement (see %s)" % (code, log_file)

    out = grade(task, measurement, reason)
    out["measure"] = measurement if measurement is not None else {"error": reason,
                                                                  "log": log_file}
    out["measure_seconds"] = round(time.monotonic() - started, 3)
    return out
