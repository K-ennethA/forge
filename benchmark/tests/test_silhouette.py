"""benchmark/silhouette.py: outline extraction, normalization, IoU, the mesh side.

Pure Python (stdlib); PIL is used only to cross-check the stdlib PNG decoder
when it is importable.
"""

import json
import math
import os
import sys

import pytest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark import quality, silhouette as sil  # noqa: E402

EAR_DIR = os.path.join(REPO_ROOT, "benchmark", "tasks", "ear-sculpt")
OUTLINE = os.path.join(EAR_DIR, "ear_outline.json")


def _reference():
    return sil.load_outline(OUTLINE)


def _payload():
    with open(OUTLINE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def leaf(height=60.0, half_width=0.165, bend=0.12, thick=4.0, n=48, power=0.7, tip=1.0):
    """A closed curved-leaf prism: thin along X, width along Y, height along Z.

    The same fixture as ``addon/tests/headless_benchmark.py`` ``_leaf``:
    midline ``y = bend * H * t^2``, half-width ``max(tip, half_width * H *
    (1-t)^power)`` for t in [0, 1] — a wide flat base, a narrow tip bent sideways.
    """
    left, right = [], []
    for i in range(n + 1):
        t = i / n
        mid = bend * height * t * t
        w = max(tip, half_width * height * (1.0 - t) ** power)
        left.append((mid - w, t * height))
        right.append((mid + w, t * height))
    ring = right + left[::-1]  # R_i -> i, L_i -> 2n + 1 - i
    count = len(ring)
    verts = [(x, y, z) for x in (thick / 2.0, -thick / 2.0) for (y, z) in ring]
    faces = []
    for side, flip in ((0, False), (count, True)):
        for i in range(n):
            quad = (2 * n + 1 - i, i, i + 1, 2 * n - i)
            tris = ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3]))
            for a, b, c in tris:
                faces.append((side + a, side + c, side + b) if flip
                             else (side + a, side + b, side + c))
    for i in range(count):
        j = (i + 1) % count
        faces.append((i, count + j, j))
        faces.append((i, count + i, count + j))
    return verts, faces


def rotate(verts, yaw_deg, tilt_deg, offset):
    ya, ta = math.radians(yaw_deg), math.radians(tilt_deg)
    out = []
    for x, y, z in verts:
        x, z = x * math.cos(ta) + z * math.sin(ta), -x * math.sin(ta) + z * math.cos(ta)
        x, y = x * math.cos(ya) - y * math.sin(ya), x * math.sin(ya) + y * math.cos(ya)
        out.append((x + offset[0], y + offset[1], z + offset[2]))
    return out


# --- the frozen reference ----------------------------------------------------

def test_sheet_is_the_frozen_one():
    assert sil.sha256_file(os.path.join(EAR_DIR, sil.EAR_SHEET)) == sil.EAR_SHEET_SHA256


def test_stdlib_png_matches_pil_on_the_crop():
    image_mod = pytest.importorskip("PIL.Image")
    width, height, channels, rows = sil.read_png(os.path.join(EAR_DIR, sil.EAR_SHEET))
    im = image_mod.open(os.path.join(EAR_DIR, sil.EAR_SHEET)).convert("RGB")
    assert (width, height) == im.size
    px = im.load()
    x0, y0, x1, y1 = sil.EAR_CROP
    assert all(sil._rgb(rows, channels, x, y) == px[x, y]
               for y in range(y0, y1) for x in range(x0, x1))


def test_outline_file_is_byte_stable():
    with open(OUTLINE, "rb") as handle:
        committed = handle.read()
    fresh = sil.outline_bytes(sil.extract_ear_outline())
    assert fresh == committed
    assert sil.outline_bytes(sil.extract_ear_outline()) == fresh


def test_outline_extraction_is_well_conditioned():
    p = _payload()
    assert p["source"]["sha256"] == sil.EAR_SHEET_SHA256
    assert p["island"]["islands"] == 1 and p["island"]["loops"] == 1
    # the two notches stand far clear of every other concavity in frame
    depths = p["junction"]["depths_px"]
    assert min(depths) > 5.0 * p["junction"]["next_depth_px"]
    # the background threshold sits on a plateau, not a cliff
    assert abs(p["background"]["ear_cells_margin"]["swing"]) < 0.05
    outline = p["outline"]["polygon"]
    vs = [v for _u, v in outline]
    assert abs(max(vs) - min(vs) - 1.0) < 1e-5


# --- normalization -------------------------------------------------------------

def test_normalize_unit_height_centroid_and_pose_invariance():
    base = [tuple(p) for p in _payload()["ear_px"]["polygon"]]
    base = [(float(x), -float(y)) for x, y in base]
    canon = sil.normalize(base, up=(0.0, 1.0))
    a = math.radians(33.0)
    moved = [(3.7 * (x * math.cos(a) - y * math.sin(a)) + 250.0,
              3.7 * (x * math.sin(a) + y * math.cos(a)) - 80.0) for x, y in base]
    up = (-math.sin(a), math.cos(a))  # image up, carried by the same rotation
    again = sil.normalize(moved, up=up)
    vs = [v for _u, v in canon["polygon"]]
    assert abs(max(vs) - min(vs) - 1.0) < 1e-12
    area, (cx, cy), _ = sil.polygon_moments(canon["polygon"])
    assert abs(cx) < 1e-9 and abs(cy) < 1e-9
    assert abs(canon["aspect"] - again["aspect"]) < 1e-9
    for (u0, v0), (u1, v1) in zip(canon["polygon"], again["polygon"]):
        assert abs(u0 - u1) < 1e-9 and abs(v0 - v1) < 1e-9


def test_up_hint_decides_which_end_is_up():
    tri = [(0.0, 0.0), (4.0, 0.0), (2.0, 10.0)]
    tip_up = sil.normalize(tri, up=(0.0, 1.0))["polygon"]
    tip_down = sil.normalize(tri, up=(0.0, -1.0))["polygon"]
    assert max(tip_up, key=lambda p: p[1]) == pytest.approx((0.0, 2.0 / 3.0), abs=1e-9)
    assert min(tip_down, key=lambda p: p[1])[1] == pytest.approx(-2.0 / 3.0, abs=1e-9)
    # no hint: the extreme furthest from the centroid (the tip) goes up
    assert sil.normalize(tri)["polygon"] == pytest.approx(tip_up)


# --- IoU -------------------------------------------------------------------------

def test_iou_identity_is_exactly_one():
    ref = _reference()["polygon"]
    assert sil.silhouette_iou(ref, ref)["iou"] == 1.0


def test_iou_is_mirror_invariant():
    ref = _reference()
    pixel = [(float(x), -float(y)) for x, y in _payload()["ear_px"]["polygon"]]
    mirrored = sil.normalize([(-x, y) for x, y in pixel], up=(0.0, 1.0))
    score = sil.silhouette_iou(mirrored["polygon"], ref["polygon"])
    assert score["mirrored"] and score["iou"] > 0.999
    assert score["iou_plain"] < 0.9  # the mirror is what matches, not the shape itself


def test_iou_disjoint_is_zero_and_half_overlap_is_a_third():
    left = [(-0.75, -0.5), (-0.25, -0.5), (-0.25, 0.5), (-0.75, 0.5)]
    right = [(0.25, -0.5), (0.75, -0.5), (0.75, 0.5), (0.25, 0.5)]
    assert sil.iou_rows(sil.rasterize(left), sil.rasterize(right)) == 0.0
    # cell edges fall on multiples of 1/128, so these squares rasterize exactly
    a = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    b = [(0.0, -0.5), (1.0, -0.5), (1.0, 0.5), (0.0, 0.5)]
    assert sil.iou_rows(sil.rasterize(a), sil.rasterize(b)) == pytest.approx(1.0 / 3.0, abs=1e-12)


# --- the mesh side ---------------------------------------------------------------

def test_mesh_outline_keeps_concavity():
    """A crescent prism's canonical area is the crescent's, not its hull's."""
    # outer arc r=30 and a flattened inner arc (30 x 12) sharing both end points
    ring = [(30.0 * math.cos(math.pi * i / 32), 30.0 * math.sin(math.pi * i / 32))
            for i in range(33)]
    ring += [(30.0 * math.cos(math.pi * i / 32), 12.0 * math.sin(math.pi * i / 32))
             for i in range(31, 0, -1)]
    n = len(ring)  # 64: outer i -> i, inner i (1..31) -> 64 - i

    def outer(i):
        return i

    def inner(i):
        return i if i in (0, 32) else n - i

    verts = [(x, y, z) for z in (2.0, -2.0) for x, y in ring]
    faces = []
    for k in (0, n):
        for i in range(32):
            for tri in ((outer(i), outer(i + 1), inner(i + 1)),
                        (outer(i), inner(i + 1), inner(i))):
                if len(set(tri)) == 3:
                    faces.append(tuple(k + t for t in tri))
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j))
        faces.append((i, n + j, n + i))
    canon = sil.mesh_outline(verts, faces, up_axis=(0.0, 1.0, 0.0))
    true_area, _c, _m = sil.polygon_moments(ring)
    hull_area, _c, _m = sil.polygon_moments(sil.convex_hull(ring))
    got = canon["area"] * canon["height"] ** 2
    assert abs(got - true_area) / true_area < 0.02
    assert got < 0.75 * hull_area


def test_mesh_silhouette_ignores_placement():
    ref = _reference()
    v, f = leaf()
    here = sil.measure_part(v, f, ref, (0.0, 0.0, 1.0))
    moved = sil.measure_part(rotate(v, 43.7, 12.0, (55.0, 40.0, 100.0)), f, ref, (0.0, 0.0, 1.0))
    assert abs(here["iou"] - moved["iou"]) < 0.01
    assert here["loops"] == 1


def test_curved_leaf_scores_above_a_narrow_blade():
    ref = _reference()
    good = sil.measure_part(*leaf(), ref, (0.0, 0.0, 1.0))["iou"]
    blade = sil.measure_part(*leaf(half_width=0.1, bend=0.0), ref, (0.0, 0.0, 1.0))["iou"]
    assert good > blade + 0.1


# --- wired into the quality tier -------------------------------------------------

def test_metric_through_quality_measure_and_grade():
    task = {"name": "t", "prompt": "p", "mode": "part", "timeout_s": 1,
            "gates": {"parts": [{"match": "Ear", "count": 2}]},
            "expected": {"s": {"kind": "silhouette_iou", "match": "Ear",
                               "outline": os.path.join("benchmark", "tasks", "ear-sculpt",
                                                       "ear_outline.json"),
                               "up_axis": [0, 0, 1], "min": 0.74}}}
    v, f = leaf()
    mirrored = [(x, -y, z) for x, y, z in v]
    meshes = {"Ear_L": (rotate(v, 30.0, 0.0, (55.0, 50.0, 100.0)), f),
              "Ear_R": (rotate(mirrored, -30.0, 0.0, (55.0, -50.0, 100.0)),
                        [(a, c, b) for a, b, c in f])}
    graded = quality.grade(task, quality.measure(task, meshes, quality.load_printer({})))
    row = graded["fidelity"]["s"]
    assert row["pass"] and row["op"] == ">=" and row["expected"] == 0.74
    assert set(row["detail"]) == {"Ear_L", "Ear_R"}
    assert abs(row["detail"]["Ear_L"]["iou"] - row["detail"]["Ear_R"]["iou"]) < 0.01

    thin = leaf(half_width=0.1, bend=0.0)
    graded = quality.grade(task, quality.measure(task, {"Ear_L": thin, "Ear_R": thin},
                                                 quality.load_printer({})))
    assert not graded["fidelity"]["s"]["pass"]


def test_ear_task_carries_the_metric():
    task = quality.load_task(EAR_DIR)
    spec = task["expected"]["ear_silhouette_iou"]
    assert spec["kind"] == "silhouette_iou"
    assert os.path.isfile(os.path.join(task["_dir"], spec["outline"]))
    assert 0.0 < spec["min"] < 1.0
    assert "eevee_sheet.png" in task["prompt"]
    assert "ears are more curved like this" in task["prompt"]
