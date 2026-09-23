"""quality.measure / grade and the geometry under them, on constructed boxes.

Pure Python: the gates go through the geometry service's own check code
in-process (service.mesh_input / runner / checks), so this needs the service
package importable but no running service and no Blender.
"""

import math
import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark import geometry, quality  # noqa: E402

# a box's 12 outward-wound triangles over corners indexed by (x, y, z) bits
_BOX_FACES = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
              (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]


def box(size, center=(0.0, 0.0, 0.0), rot=None):
    sx, sy, sz = size
    verts = []
    for i in range(8):
        p = ((i & 1) - 0.5) * sx, (((i >> 1) & 1) - 0.5) * sy, (((i >> 2) & 1) - 0.5) * sz
        if rot is not None:
            p = tuple(sum(rot[r][c] * p[c] for c in range(3)) for r in range(3))
        verts.append((p[0] + center[0], p[1] + center[1], p[2] + center[2]))
    return verts, [list(f) for f in _BOX_FACES]


def rz(deg):
    a = math.radians(deg)
    return [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]


def ry(deg):
    a = math.radians(deg)
    return [[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]


def matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def test_box_volume_ratio_is_one_and_rotation_invariant():
    for rot in (None, matmul(rz(37), ry(21))):
        v, f = box((10, 20, 40), rot=rot)
        out = geometry.volume_ratio(v, [tuple(x) for x in f])
        assert abs(out["volume_mm3"] - 8000.0) < 1e-6
        assert abs(out["ratio"] - 1.0) < 1e-6
        assert sorted(round(e, 6) for e in out["obb_extents_mm"]) == [10.0, 20.0, 40.0]


def test_flat_sheet_has_zero_volume_ratio():
    verts = [(0, 0, 0), (10, 0, 0), (10, 30, 0), (0, 30, 0)]
    assert geometry.volume_ratio(verts, [(0, 1, 2), (0, 2, 3)])["ratio"] == 0.0


def test_mirror_angle_and_yaw_on_constructed_slabs():
    front, up = (1, 0, 0), (0, 0, 1)
    slab = (4, 30, 60)  # thin along local X: face normal = local X
    a = box(slab, (60, 50, 0), rz(30))
    b = box(slab, (60, -50, 0), rz(-30))
    ya = geometry.facing_yaw_deg(*a, front, up)
    yb = geometry.facing_yaw_deg(*b, front, up)
    assert abs(ya["yaw_deg"] - 30.0) < 1e-6 and abs(yb["yaw_deg"] + 30.0) < 1e-6
    assert geometry.mirror_angle_deg(ya["normal"], yb["normal"], front, up) < 1e-4
    # one transform (tilted back 30 deg, local yaw 90) rotated to +-40 about Z
    local = matmul(rz(90), ry(-30))
    c = box(slab, (60, 50, 0), matmul(rz(40), local))
    d = box(slab, (60, -50, 0), matmul(rz(-40), local))
    yc = geometry.facing_yaw_deg(*c, front, up)
    yd = geometry.facing_yaw_deg(*d, front, up)
    angle = geometry.mirror_angle_deg(yc["normal"], yd["normal"], front, up)
    # acos|cos^2(tau) cos(2 phi) + sin^2(tau)| with phi = 90, tau = 30
    expected = math.degrees(math.acos(abs(math.cos(math.radians(30)) ** 2 * -1
                                          + math.sin(math.radians(30)) ** 2)))
    assert abs(angle - expected) < 1e-4 and angle > 20.0


def _task(expected, gates=None):
    return {"name": "t", "prompt": "p", "mode": "part", "timeout_s": 1,
            "expected": expected, "gates": gates or {"parts": [{"match": "box", "count": 1}]}}


def test_measure_and_grade_a_box_scene():
    printer = quality.load_printer({})
    meshes = {"box": box((10, 20, 40), (0, 0, 20))}
    task = _task({
        "h": {"kind": "overall_dim", "axis": "z", "expected": 40.0, "tol": 0.1},
        "w": {"kind": "overall_dim", "axis": "x", "expected": 12.0, "tol": 0.5},
        "n": {"kind": "part_count", "match": "box", "expected": 1, "tol": 0},
        "floor": {"kind": "ray", "origin_mm": [0.3, 0.4, 100], "direction": [0, 0, -1],
                  "measure": "span", "between": [0, 1], "expected": 40.0, "tol": 0.01},
        "vol": {"kind": "volume", "expected": 8000.0, "tol": 1.0},
        "wall": {"kind": "min_wall", "match": "box", "min": 0.8},
    })
    graded = quality.grade(task, quality.measure(task, meshes, printer))
    assert graded["gates"] == {"passed": 4, "total": 4, "failures": []}
    f = graded["fidelity"]
    assert f["h"]["pass"] and f["n"]["pass"] and f["floor"]["pass"] and f["vol"]["pass"]
    assert f["wall"]["pass"] and f["wall"]["measured"] >= 3.2  # "at least the probe"
    assert not f["w"]["pass"] and abs(f["w"]["measured"] - 10.0) < 1e-9
    assert 0.0 < graded["score"] < 100.0
    assert graded["appearance_score"] is None


def test_missing_parts_fail_their_gates_and_keep_totals():
    printer = quality.load_printer({})
    task = _task({"n": {"kind": "part_count", "match": "ear", "expected": 2, "tol": 0}},
                 gates={"parts": [{"match": "ear", "count": 2}]})
    graded = quality.grade(task, quality.measure(task, {"Ear_L": box((4, 30, 60))}, printer))
    # "ear" is case-sensitive here: nothing matches, both slots missing
    assert graded["gates"]["passed"] == 0 and graded["gates"]["total"] == 8
    assert graded["fidelity"]["n"]["measured"] == 0


def test_grade_without_measurement_fails_everything_with_fixed_totals():
    task = _task({"y": {"kind": "mirrored_yaw", "match": "ear"},
                  "h": {"kind": "overall_dim", "axis": "z", "expected": 1, "tol": 1}},
                 gates={"parts": [{"match": "ear", "count": 2}]})
    graded = quality.grade(task, None, "no artifact")
    assert graded["gates"]["total"] == 8 and graded["gates"]["passed"] == 0
    assert not any(row["pass"] for row in graded["fidelity"].values())
    assert graded["score"] == 0.0


def test_frozen_tasks_load():
    for name in ("parametric-dish", "ear-sculpt"):
        task = quality.load_task(os.path.join(REPO_ROOT, "benchmark", "tasks", name))
        assert task["mode"] == "part" and task["artifact"]
    ear = quality.load_task(os.path.join(REPO_ROOT, "benchmark", "tasks", "ear-sculpt"))
    assert os.path.isfile(os.path.join(ear["_dir"], ear["image"]))
    kinds = {spec["kind"] for spec in ear["expected"].values()}
    assert kinds == {"part_count", "min_wall", "mirrored_yaw", "volume_ratio",
                     "silhouette_iou"}
    assert ear["expected"]["ear_min_wall_mm"]["min"] == quality.load_printer(ear)["min_wall_thickness"]
