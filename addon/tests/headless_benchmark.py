"""Headless tests for the benchmark harness's quality tier (``benchmark/``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_benchmark.py

The socket port is **9918** — 9876 belongs to the artist's live session and
9878 through 9917 to the other suites (9917 is ``headless_seating``).  The
socket is booted and pinged so the add-on is proven live beside the harness;
the measurements themselves are in-process calls, because the harness reads
meshes with the add-on's own ``evaluated_mesh_mm`` rather than over the wire.

Why every fixture is constructed
--------------------------------
The quality tier's claim is that it *measures*: overall dimensions, ray spans,
wall thickness, enclosed volume, face yaw and mirror symmetry.  The only honest
test is one where every answer is known before anything is measured, so each
fixture is built from numbers this file wrote down:

* **the dish** — a lathe of the frozen ``parametric-dish`` task's own profile
  (outer diameter 120 mm, height 25, wall 3, floor 4) with 96 segments, run
  against the REAL ``benchmark/tasks/parametric-dish/task.json``: all four
  print gates and all six fidelity metrics must pass.  A "wrong" dish (radius
  used as diameter, 2 mm floor) must fail exactly the metrics it breaks;
* **the ears**, against the REAL ``benchmark/tasks/ear-sculpt/task.json`` —
  (A) two 4 x 30 x 60 mm slabs mirrored at yaw +-30 deg: everything passes;
  (B) one tilted transform (local yaw 90, tilt 30) rotated to +-43.7 deg about
  Z — the artist's "angled the same direction" defect: only the mirror clause
  fails, at the analytic ``acos|cos^2(30) cos(180) + sin^2(30)|`` = 60 deg;
  (C) two 1.2 mm curved shells, mirrored: only the volume ratio fails, at the
  analytic polygon-sector ratio — the "no volume to them" defect.  A slab and a
  shell are rectangles face-on, so (A)-(C) also fail the silhouette metric and
  their per-OBB claims are asserted over the other four metrics;
* **the silhouette calibration** (``ear_silhouette_iou``, benchmark/silhouette.py)
  — (D) the DELIVERED blade ears in ``projects/bench-ear-sculpt`` (the pair the
  artist answered with "ears are more curved like this"): only the silhouette
  fails; (E) a constructed curved leaf pair, mirrored: all five metrics pass;
  (F) the frozen reference outline itself extruded into a 4 mm prism: the
  pipeline's own loss.  The floor must sit between (D) and (E);
* **round trips** — the dish exported to ``.glb`` and written to ``.blend``,
  cleared, loaded back through the harness's own ``load_artifact`` and
  re-measured: the same numbers;
* **the spawn path** — :func:`benchmark.quality.evaluate` on that ``.glb``,
  which launches ONE nested hidden headless Blender exactly as the runner
  will, must grade it identically;
* **determinism** — the same measurement twice, byte-identical.
"""

import hashlib
import json
import math
import os
import shutil
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bmesh
import bpy
from mathutils import Matrix, Vector

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
for _path in (ADDON_DIR, REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

PORT = 9918  # not 9876 (a live session) and not 9878..9917 (every other suite)

MM = 0.001
M_TO_MM = 1000.0

#: Blender stores vertices as float32 metres: a 0.06 m coordinate carries
#: ~4e-9 m of rounding, so a 120 mm dimension measured 119.9999973 mm on the
#: first run of this suite (2.7e-6 mm off), a +-30 deg slab 30.0000195 deg and
#: a box's volume ratio 0.99999885.  These bounds are float32-honest with
#: ~20x margin, and a thousand times tighter than any task tolerance.
F32_MM = 5e-5
F32_DEG = 5e-4
F32_RATIO = 2e-5

DISH_TASK = os.path.join(REPO_ROOT, "benchmark", "tasks", "parametric-dish")
EAR_TASK = os.path.join(REPO_ROOT, "benchmark", "tasks", "ear-sculpt")

SEGMENTS = 96
#: the dish profile (r, z) in mm, outer bottom -> outer top -> inner top -> inner floor
DISH = {"outer_r": 60.0, "height": 25.0, "wall": 3.0, "floor": 4.0}
WRONG_DISH = {"outer_r": 30.0, "height": 25.0, "wall": 3.0, "floor": 2.0}

SLAB_MM = (4.0, 30.0, 60.0)
EAR_YAW_DEG = 30.0
RIM_DEG = 43.7          # projects/eevee-bowl-v2/part.py _EAR_ANGLES_DEG
COPY_LOCAL_YAW_DEG = 90.0
COPY_TILT_DEG = 30.0

SHELL_R_IN = 20.0
SHELL_R_OUT = 21.2
SHELL_SPAN_DEG = 120.0
SHELL_SEGMENTS = 48
SHELL_HEIGHT = 60.0

#: the curved-leaf fixture (E), in units of its height: half-width 0.165 H at
#: the base (the reference outline's measured aspect is 0.326, i.e. a 0.163 H
#: half-width), tapering as (1 - t)^0.7, the midline bent 0.12 H * t^2
#: sideways — a wide base, a curved edge, a pointed tip.  Not fit to the
#: outline: a family member picked by its aspect and a visible bend.  The tip
#: keeps a 1 mm half-width: tapered to a true point, its last 1.3 rows are
#: under the 0.8 mm wall and min_wall measured 0.587 mm there on the first run.
LEAF_HEIGHT = 60.0
LEAF_HALF_WIDTH = 0.165
LEAF_BEND = 0.12
LEAF_POWER = 0.7
LEAF_THICK = 4.0
LEAF_ROWS = 48
LEAF_TIP_HALF_MM = 1.0
BLADE_BLEND = os.path.join(REPO_ROOT, "projects", "bench-ear-sculpt", "bench-ear-sculpt.blend")
SILHOUETTE = "ear_silhouette_iou"

_RESULTS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


# --- wire -------------------------------------------------------------------

def enable_addon():
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=30.0):
    from forge import server as forge_server

    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                box["reply"] = json.loads(buffer.split(b"\n")[0].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=talk, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        if forge_server._server is not None:
            forge_server._server.drain()
        time.sleep(0.005)
    thread.join(timeout=2.0)
    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error", "message": "no reply"}


# --- fixture builders -------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _object(name, verts_mm, faces, matrix_mm=None):
    """A mesh object from mm vertices, stored in metres, normals made outward."""
    if matrix_mm is not None:
        verts_mm = [tuple(matrix_mm @ Vector(v)) for v in verts_mm]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x * MM, y * MM, z * MM) for x, y, z in verts_mm], [], faces)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def build_dish(name, spec):
    """A lathe of the dish profile: bottom fan, outer wall, rim, inner wall, floor fan."""
    r_out, h = spec["outer_r"], spec["height"]
    r_in, floor = r_out - spec["wall"], spec["floor"]
    profile = [(r_out, 0.0), (r_out, h), (r_in, h), (r_in, floor)]
    verts = []
    for r, z in profile:
        for i in range(SEGMENTS):
            a = 2.0 * math.pi * i / SEGMENTS
            verts.append((r * math.cos(a), r * math.sin(a), z))
    bottom = len(verts)
    verts.append((0.0, 0.0, 0.0))
    top = len(verts)
    verts.append((0.0, 0.0, floor))

    def ring(k, i):
        return k * SEGMENTS + (i % SEGMENTS)

    faces = []
    for i in range(SEGMENTS):
        faces.append((bottom, ring(0, i + 1), ring(0, i)))
        for k in range(len(profile) - 1):
            faces.append((ring(k, i), ring(k, i + 1), ring(k + 1, i + 1), ring(k + 1, i)))
        faces.append((top, ring(3, i), ring(3, i + 1)))
    return _object(name, verts, faces)


def _box(size):
    sx, sy, sz = size
    verts = [(((i & 1) - 0.5) * sx, (((i >> 1) & 1) - 0.5) * sy, (((i >> 2) & 1) - 0.5) * sz)
             for i in range(8)]
    faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return verts, faces


def _shell():
    """A curved 1.2 mm shell: an annular sector swept up Z, concave side to -X."""
    verts, faces = [], []
    half = math.radians(SHELL_SPAN_DEG) / 2.0
    for i in range(SHELL_SEGMENTS + 1):
        a = -half + 2.0 * half * i / SHELL_SEGMENTS
        c, s = math.cos(a), math.sin(a)
        for r, z in ((SHELL_R_IN, -SHELL_HEIGHT / 2), (SHELL_R_OUT, -SHELL_HEIGHT / 2),
                     (SHELL_R_OUT, SHELL_HEIGHT / 2), (SHELL_R_IN, SHELL_HEIGHT / 2)):
            verts.append((r * c, r * s, z))
    for i in range(SHELL_SEGMENTS):
        for k in range(4):
            a0, a1 = 4 * i + k, 4 * i + (k + 1) % 4
            faces.append((a0, a1, a1 + 4, a0 + 4))
    faces.append((0, 3, 2, 1))
    last = 4 * SHELL_SEGMENTS
    faces.append((last, last + 1, last + 2, last + 3))
    return verts, faces


def _leaf():
    """A closed curved-leaf prism: thin along X, width along Y, height along Z."""
    n, h = LEAF_ROWS, LEAF_HEIGHT
    left, right = [], []
    for i in range(n + 1):
        t = i / n
        mid = LEAF_BEND * h * t * t
        w = max(LEAF_TIP_HALF_MM, LEAF_HALF_WIDTH * h * (1.0 - t) ** LEAF_POWER)
        left.append((mid - w, t * h))
        right.append((mid + w, t * h))
    ring = right + left[::-1]  # R_i -> i, L_i -> 2n + 1 - i
    count = len(ring)
    verts = [(x, y, z) for x in (LEAF_THICK / 2.0, -LEAF_THICK / 2.0) for (y, z) in ring]
    faces = []
    for side in (0, count):
        for i in range(n):
            faces.append((side + 2 * n + 1 - i, side + i, side + i + 1, side + 2 * n - i))
    for i in range(count):
        j = (i + 1) % count
        faces.append((i, j, count + j, count + i))
    return verts, faces


def _extruded_outline(payload, mm_per_px=0.7):
    """The frozen outline's pixel polygon as a 4 mm prism (ngon caps, ear-clipped)."""
    ring = [(x * mm_per_px, -y * mm_per_px) for x, y in payload["ear_px"]["polygon"]]
    n = len(ring)
    verts = [(x, y, z) for x in (2.0, -2.0) for (y, z) in ring]
    faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    return verts, faces


def _triangulate(obj):
    """Ear-clip every ngon in place: the service's own triangulation fans, and a
    fan of a concave cap is not the cap."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces, quad_method="BEAUTY", ngon_method="EAR_CLIP")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _rz(deg):
    return Matrix.Rotation(math.radians(deg), 4, "Z")


def _ry(deg):
    return Matrix.Rotation(math.radians(deg), 4, "Y")


def _place(yaw_deg, where_mm, local=None):
    matrix = Matrix.Translation(Vector(where_mm)) @ _rz(yaw_deg)
    return matrix @ local if local is not None else matrix


# --- harness glue -------------------------------------------------------------

def measure_scene(task):
    from benchmark import blender_measure, quality

    names = sorted(o.name for o in bpy.context.scene.objects)
    meshes = blender_measure.meshes_mm(names, M_TO_MM)
    measurement = quality.measure(task, meshes, quality.load_printer(task))
    return measurement, quality.grade(task, measurement)


def passes(graded):
    return {name for name, row in graded["fidelity"].items() if row["pass"]}


def digest(graded):
    blob = json.dumps({"g": graded["gates"], "f": graded["fidelity"], "s": graded["score"]},
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# --- tests --------------------------------------------------------------------

def test_socket():
    section("the add-on socket on %d" % PORT)
    reply = _roundtrip({"type": "ping", "params": {}})
    check("ping answers over the socket", reply.get("status") == "success", reply)
    from benchmark import quality

    check("appearance scoring is a stub hook (None) in this lane",
          quality.appearance_score is None)


def test_dish(task):
    section("parametric-dish: the frozen task against its own profile")
    clear_scene()
    build_dish("Dish", DISH)
    _m, graded = measure_scene(task)
    f = graded["fidelity"]
    check("all 4 print gates pass", graded["gates"]["passed"] == 4 == graded["gates"]["total"],
          graded["gates"])
    check("all 6 fidelity metrics pass", passes(graded) == set(task["expected"]),
          {k: (r["measured"], r["pass"]) for k, r in f.items()})
    check("diameter x is 120 mm to float32 (vertices at 0 and 180 deg)",
          abs(f["diameter_x_mm"]["measured"] - 120.0) < F32_MM, f["diameter_x_mm"]["measured"])
    check("diameter y is 120 mm to float32 (96 segments put vertices at 90 / 270)",
          abs(f["diameter_y_mm"]["measured"] - 120.0) < F32_MM, f["diameter_y_mm"]["measured"])
    check("height is 25 mm", abs(f["height_mm"]["measured"] - 25.0) < F32_MM,
          f["height_mm"]["measured"])
    # the ray at y = 0.37 crosses two chords of 96-gons; the analytic chord span
    wall = f["wall_thickness_mm"]["measured"]
    check("wall span within 0.01 mm of 3.0", abs(wall - 3.0) < 0.01, wall)
    check("floor span is 4 mm", abs(f["floor_thickness_mm"]["measured"] - 4.0) < F32_MM,
          f["floor_thickness_mm"]["measured"])
    note("score %.3f, wall %.5f mm" % (graded["score"], wall))
    return graded


def test_wrong_dish(task):
    section("parametric-dish: a wrong dish fails exactly what it breaks")
    clear_scene()
    build_dish("Dish", WRONG_DISH)
    _m, graded = measure_scene(task)
    failed = set(task["expected"]) - passes(graded)
    check("diameters and floor fail; count, height and wall pass",
          failed == {"diameter_x_mm", "diameter_y_mm", "floor_thickness_mm"}, sorted(failed))
    check("the gates still pass (a wrong dish is still printable)",
          graded["gates"]["passed"] == 4, graded["gates"])
    check("its score is below a right dish's", graded["score"] < 90.0, graded["score"])
    return graded


def test_ears_mirrored(task):
    section("ear-sculpt (A): mirrored slabs pass everything")
    clear_scene()
    verts, faces = _box(SLAB_MM)
    _object("Ear_L", verts, faces, _place(EAR_YAW_DEG, (55.0, 50.0, 100.0)))
    _object("Ear_R", verts, faces, _place(-EAR_YAW_DEG, (55.0, -50.0, 100.0)))
    _m, graded = measure_scene(task)
    f = graded["fidelity"]
    check("8/8 gates (2 ears x 4 checks)", graded["gates"]["passed"] == 8 == graded["gates"]["total"],
          graded["gates"])
    check("all 4 per-OBB ear metrics pass", passes(graded) == set(task["expected"]) - {SILHOUETTE},
          {k: (r["measured"], r["pass"]) for k, r in f.items()})
    check("a rectangular slab is not the ear outline (silhouette fails)",
          not f[SILHOUETTE]["pass"], f[SILHOUETTE]["measured"])
    note("slab silhouette IoU %.4f" % f[SILHOUETTE]["measured"])
    yaws = f["ear_mirrored_yaw"]["measured"]["yaw_deg"]
    check("yaws are +30 / -30 to float32",
          abs(yaws["Ear_L"] - EAR_YAW_DEG) < F32_DEG and abs(yaws["Ear_R"] + EAR_YAW_DEG) < F32_DEG, yaws)
    check("mirror angle is 0", f["ear_mirrored_yaw"]["measured"]["mirror_angle_deg"] < 1e-4,
          f["ear_mirrored_yaw"]["measured"])
    check("a box fills its own box (volume ratio 1)",
          abs(f["ear_volume_ratio"]["measured"] - 1.0) < F32_RATIO, f["ear_volume_ratio"]["measured"])
    check("4 mm walls read as 'at least the probe' (3.2 mm)",
          f["ear_min_wall_mm"]["measured"] >= 3.2 - 1e-9, f["ear_min_wall_mm"]["measured"])
    return graded


def test_ears_rotated_copy(task):
    section("ear-sculpt (B): one transform rotated to both sides - 'angled the same direction'")
    clear_scene()
    verts, faces = _box(SLAB_MM)
    local = _rz(COPY_LOCAL_YAW_DEG) @ _ry(-COPY_TILT_DEG)
    _object("Ear_L", verts, faces, _place(RIM_DEG, (60.0, 50.0, 100.0), local))
    _object("Ear_R", verts, faces, _place(-RIM_DEG, (60.0, -50.0, 100.0), local))
    _m, graded = measure_scene(task)
    f = graded["fidelity"]
    failed = set(task["expected"]) - passes(graded)
    check("of the per-OBB metrics only mirrored yaw fails",
          failed - {SILHOUETTE} == {"ear_mirrored_yaw"}, sorted(failed))
    tau, phi = math.radians(COPY_TILT_DEG), math.radians(COPY_LOCAL_YAW_DEG)
    analytic = math.degrees(math.acos(abs(math.cos(tau) ** 2 * math.cos(2 * phi)
                                          + math.sin(tau) ** 2)))
    got = f["ear_mirrored_yaw"]["measured"]["mirror_angle_deg"]
    check("mirror angle is the analytic %.3f deg" % analytic, abs(got - analytic) < 1e-4, got)
    check("the failure says it was rotated, not mirrored",
          any("not mirrored" in r for r in f["ear_mirrored_yaw"].get("why_failed", [])),
          f["ear_mirrored_yaw"].get("why_failed"))
    note("yaws %s, mirror %.3f deg" % (
        {k: round(v, 3) for k, v in f["ear_mirrored_yaw"]["measured"]["yaw_deg"].items()}, got))
    return graded


def test_ears_thin_shells(task):
    section("ear-sculpt (C): curved 1.2 mm shells - 'no volume to them'")
    clear_scene()
    verts, faces = _shell()
    _object("Ear_L", verts, faces, _place(EAR_YAW_DEG, (40.0, 50.0, 100.0)))
    _object("Ear_R", verts, faces, _place(-EAR_YAW_DEG, (40.0, -50.0, 100.0)))
    _m, graded = measure_scene(task)
    f = graded["fidelity"]
    failed = set(task["expected"]) - passes(graded)
    check("of the per-OBB metrics only the volume ratio fails",
          failed - {SILHOUETTE} == {"ear_volume_ratio"}, sorted(failed))
    # analytic: N trapezoids of a polygonal annular sector, in a height x chord x sagitta box
    half = math.radians(SHELL_SPAN_DEG) / 2.0
    step = 2.0 * half / SHELL_SEGMENTS
    area = SHELL_SEGMENTS * 0.5 * (SHELL_R_OUT ** 2 - SHELL_R_IN ** 2) * math.sin(step)
    chord = 2.0 * SHELL_R_OUT * math.sin(half)
    sagitta = SHELL_R_OUT - SHELL_R_IN * math.cos(half)
    analytic = area * SHELL_HEIGHT / (SHELL_HEIGHT * chord * sagitta)
    got = f["ear_volume_ratio"]["measured"]
    check("volume ratio is the analytic %.5f" % analytic, abs(got - analytic) < 1e-6, got)
    check("walls are 1.2 mm to tessellation (>= 0.8 floor)",
          abs(f["ear_min_wall_mm"]["measured"] - 1.2) < 0.01, f["ear_min_wall_mm"]["measured"])
    check("and the shells are still mirrored", f["ear_mirrored_yaw"]["pass"],
          f["ear_mirrored_yaw"]["measured"])
    return graded


def test_blade_calibration(task):
    section("ear-sculpt (D): the DELIVERED blade ears - 'ears are more curved like this'")
    from benchmark import blender_measure, quality

    if not check("the delivered blend exists", os.path.isfile(BLADE_BLEND), BLADE_BLEND):
        return None
    clear_scene()
    measurement = blender_measure.measure_artifact(task, BLADE_BLEND)
    graded = quality.grade(task, measurement)
    f = graded["fidelity"]
    check("the blades pass the per-OBB shape metrics (mirrored yaw, volume ratio)",
          f["ear_mirrored_yaw"]["pass"] and f["ear_volume_ratio"]["pass"],
          {k: (r["measured"], r["pass"]) for k, r in f.items()})
    check("and fail the silhouette - the defect the artist named",
          not f[SILHOUETTE]["pass"], f[SILHOUETTE]["measured"])
    # the delivered pair also fails min_wall (measured 0.0572 mm, 2026-09-23) -
    # a second, separate defect of that artifact, recorded not asserted here
    note("delivered min wall %s mm (pass=%s)" % (f["ear_min_wall_mm"]["measured"],
                                                 f["ear_min_wall_mm"]["pass"]))
    detail = f[SILHOUETTE].get("detail") or {}
    check("both blades measured (Ear_L, Ear_R)", sorted(detail) == ["Ear_L", "Ear_R"], sorted(detail))
    iou = f[SILHOUETTE]["measured"]
    note("blade IoU %s, aspect %s (reference %.4f); mirror %.3f deg, volume ratio %.4f"
         % ({k: round(v["iou"], 4) for k, v in detail.items()},
            {k: round(v["aspect"], 4) for k, v in detail.items()},
            next(iter(detail.values()))["reference_aspect"],
            f["ear_mirrored_yaw"]["measured"]["mirror_angle_deg"], f["ear_volume_ratio"]["measured"]))
    return iou


def test_leaf_calibration(task):
    section("ear-sculpt (E): a curved leaf pair, mirrored - every metric passes")
    clear_scene()
    verts, faces = _leaf()
    mirrored = [(x, -y, z) for x, y, z in verts]
    _object("Ear_L", verts, faces, _place(EAR_YAW_DEG, (55.0, 50.0, 100.0)))
    _object("Ear_R", mirrored, faces, _place(-EAR_YAW_DEG, (55.0, -50.0, 100.0)))
    _m, graded = measure_scene(task)
    f = graded["fidelity"]
    check("8/8 gates", graded["gates"]["passed"] == 8 == graded["gates"]["total"], graded["gates"])
    check("all 5 ear metrics pass", passes(graded) == set(task["expected"]),
          {k: (r["measured"], r["pass"]) for k, r in f.items()})
    detail = f[SILHOUETTE].get("detail") or {}
    # not bit-equal: the working raster is anchored at the projection's min
    # corner, so a mirrored projection is sampled at different cell offsets
    # (0.0028 apart on the first run) - bounded well under any floor margin
    check("the mirrored pair scores the same to 0.005 (mirror-invariant)",
          abs(detail["Ear_L"]["iou"] - detail["Ear_R"]["iou"]) < 0.005,
          {k: v["iou"] for k, v in detail.items()})
    iou = f[SILHOUETTE]["measured"]
    note("leaf IoU %s, aspect %.4f" % ({k: round(v["iou"], 4) for k, v in detail.items()},
                                      detail["Ear_L"]["aspect"]))
    return iou


def test_reference_extruded(task):
    section("ear-sculpt (F): the frozen outline itself, extruded - the pipeline's own loss")
    from benchmark import silhouette

    with open(os.path.join(EAR_TASK, "ear_outline.json"), "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    reference = silhouette.load_outline(os.path.join(EAR_TASK, "ear_outline.json"))
    check("the outline against itself is exactly 1.0",
          silhouette.silhouette_iou(reference["polygon"], reference["polygon"])["iou"] == 1.0)
    pixel = [(float(x), -float(y)) for x, y in payload["ear_px"]["polygon"]]
    mirror = silhouette.normalize([(-x, y) for x, y in pixel], up=(0.0, 1.0))
    score = silhouette.silhouette_iou(mirror["polygon"], reference["polygon"])
    check("the outline against its mirror image is 1.0 to 1e-9 (mirror-invariant)",
          score["iou"] > 1.0 - 1e-9 and score["mirrored"], score)
    note("reference vs its mirror: %.6f (unmirrored overlap %.4f - the ear is not symmetric)"
         % (score["iou"], score["iou_plain"]))
    clear_scene()
    verts, faces = _extruded_outline(payload)
    obj = _object("Ear_L", verts, faces, _place(-20.0, (40.0, 30.0, 90.0), _ry(-15.0)))
    _triangulate(obj)
    _m, graded = measure_scene(task)
    iou = graded["fidelity"][SILHOUETTE]["measured"]
    # the canonical cell is 1/128 of the height and the trace 4x finer, so only
    # boundary cells can disagree: measured 0.9939 in the pure-Python run
    check("the extruded outline scores >= 0.98", iou is not None and iou >= 0.98, iou)
    note("extruded reference IoU %.4f (tilted 15 deg, yawed -20 deg)" % iou)
    return iou


def test_floor_between(task, blade, leaf):
    section("the silhouette floor sits between the blade and the leaf")
    floor = task["expected"][SILHOUETTE]["min"]
    ok = blade is not None and leaf is not None and blade < floor < leaf
    check("blade %s < floor %.2f < leaf %s" % (blade and round(blade, 4), floor,
                                                leaf and round(leaf, 4)), ok)
    if ok:
        check("closer to the leaf than to the blade", leaf - floor < floor - blade,
              (leaf - floor, floor - blade))


def test_round_trips(task, reference, workdir):
    section("round trips: .glb and .blend back through load_artifact")
    from benchmark import blender_measure, quality

    clear_scene()
    dish = build_dish("Dish", DISH)
    glb = os.path.join(workdir, "dish.glb")
    blend = os.path.join(workdir, "dish.blend")
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB")
    bpy.data.libraries.write(blend, {dish})
    check("fixture files written", os.path.isfile(glb) and os.path.isfile(blend))

    results = {}
    for path in (glb, blend):
        clear_scene()
        measurement = blender_measure.measure_artifact(task, path)
        loaded = sorted(o.name for o in bpy.context.scene.objects)
        graded = quality.grade(task, measurement)
        results[path] = graded
        ext = os.path.splitext(path)[1]
        check("%s loads the Dish object" % ext, "Dish" in loaded, loaded)
        same = all(abs(graded["fidelity"][k]["measured"] - reference["fidelity"][k]["measured"]) < 1e-3
                   for k in reference["fidelity"])
        check("%s re-measures the in-scene numbers to 1 micron" % ext, same,
              {k: (graded["fidelity"][k]["measured"], reference["fidelity"][k]["measured"])
               for k in reference["fidelity"]})
        check("%s passes the same gates" % ext,
              graded["gates"]["passed"] == reference["gates"]["passed"], graded["gates"])
    return glb, results[glb]


def test_spawn_path(task, glb, in_process, workdir):
    section("the runner's spawn path: quality.evaluate -> one hidden headless Blender")
    from benchmark import quality

    started = time.monotonic()
    graded = quality.evaluate(task, glb, log_path=os.path.join(workdir, "spawn.json"),
                              blender=bpy.app.binary_path, timeout_s=300)
    elapsed = time.monotonic() - started
    check("the nested measurement succeeded", "error" not in (graded.get("measure") or {}),
          graded.get("measure"))
    check("it grades the .glb exactly as the in-process load did",
          digest(graded) == digest(in_process), (digest(graded), digest(in_process)))
    note("nested Blender measure took %.1fs (measure_seconds %.3f)"
         % (elapsed, graded.get("measure_seconds") or 0.0))


def test_determinism(task):
    section("determinism - the same scene measured twice")
    clear_scene()
    build_dish("Dish", DISH)
    first = digest(measure_scene(task)[1])
    second = digest(measure_scene(task)[1])
    check("byte-identical grading run to run", first == second, (first, second))
    note("dish grading digest %s" % first)


def test_port_is_free_after():
    section("teardown")
    from forge import server as forge_server

    forge_server.stop_server()
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    probe.setsockopt(socketlib.SOL_SOCKET, socketlib.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


def main():
    print("Forge benchmark harness headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    started = time.monotonic()

    enable_addon()
    from forge import server as forge_server
    from benchmark import quality

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    workdir = tempfile.mkdtemp(prefix="forge-bench-")
    try:
        dish_task = quality.load_task(DISH_TASK)
        ear_task = quality.load_task(EAR_TASK)
        test_socket()
        reference = test_dish(dish_task)
        test_wrong_dish(dish_task)
        test_ears_mirrored(ear_task)
        test_ears_rotated_copy(ear_task)
        test_ears_thin_shells(ear_task)
        blade = test_blade_calibration(ear_task)
        leaf = test_leaf_calibration(ear_task)
        test_reference_extruded(ear_task)
        test_floor_between(ear_task, blade, leaf)
        glb, in_process = test_round_trips(dish_task, reference, workdir)
        test_spawn_path(dish_task, glb, in_process, workdir)
        test_determinism(dish_task)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed  (%.1fs)" % (len(_RESULTS), len(failed),
                                              time.monotonic() - started))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
