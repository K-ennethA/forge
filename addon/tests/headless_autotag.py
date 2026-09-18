"""Headless add-on tests for axis-following auto-tagging (rigforge_autotag).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_autotag.py

**Nothing here needs a GPU, a checkpoint or a download.**  The detector's output
is a *file* — a ``forge.joints/1`` document — so this suite writes its own: a
hand-built skeleton for a hand-built biped, exact to the millimetre.  That is
the point of the file handoff.  The subprocess runner is tested the same way,
with a batch file standing in for the virtualenv's interpreter, so the cache,
the lock and every failure path are exercised without CUDA ever being asked a
question.

The mesh is a **hanging-arm biped**, and that shape is the whole test.  The live
failure this module exists for was measured on a werewolf whose arms hang at its
sides: an axis-aligned box band tagged ``Arm.L`` as a wedge of shoulder, deltoid
and rib whose principal axis pointed *down the body*, and the landmark fitter
refused it — correctly — for not being shaped like a limb.  A T-posed test biped
cannot reproduce that, because for a T-posed biped a box band happens to be
nearly right.  So this one hangs its arms, and two checks are pinned against
each other on the same mesh:

* the **box band** puts a shoulder-height torso vertex in the arm tag, and the
  landmark fitter refuses the result — the failure, reproduced;
* the **axis tag** does not, and the fitter accepts it — the fix, measured.

Port 9910: not 9876 (a live session), not 9901/9907/9908/9909 (the rigging
suites), not 9878/9879/9880 (phases 2/3/4).
"""

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
from mathutils import Vector

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
RIGBRIDGE_DIR = os.path.join(REPO_DIR, "rigbridge")

PORT = 9910
BIPED = "HangingArmBiped"

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


def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def call(command, params=None, timeout=900.0, expect_error=False):
    """One socket round trip, pumping the main-thread queue while it is in flight."""
    from forge import server as forge_server

    payload = {"type": command, "params": params or {}}
    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(1 << 20)
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
        reply = {"status": "error", "message": "harness: %s" % box["error"]}
    else:
        reply = box.get("reply") or {"status": "error",
                                     "message": "no reply within %.0fs" % timeout}
    if expect_error:
        return reply
    if reply.get("status") != "success":
        raise AssertionError("%s failed: %s" % (command, reply.get("message")))
    return reply.get("result") or {}


# --- the hanging-arm biped ---------------------------------------------------
#
# Every dimension is deliberate and the comments say why, because the assertions
# below are about millimetres and a reader has to be able to check them.

#: A **broad** torso with the arms hanging close beside it, which is the
#: geometry a box band cannot describe and the reason these numbers are what
#: they are.  With a 180 mm torso, a 45 mm arm whose axis is 280 mm out and a
#: hand splaying to ~340 mm:
#:
#: * the torso's own flank is 100 mm from the arm's axis, and 100 > 1.6 x 45,
#:   so the girth cap keeps it out of the arm tag;
#: * but 180 mm out is well past 0.45 x 340 mm, so the **box band takes the
#:   whole flank into the arm** — the wedge, reproduced.
#:
#: Both margins are deliberate, and both are asserted below.
TORSO_RADIUS = 0.180
TORSO_LOW, TORSO_HIGH = 0.88, 1.50
ARM_RADIUS = 0.045
ARM_TOP = (0.280, 0.0, 1.460)
ARM_BOTTOM = (0.300, 0.0, 0.780)
LEG_RADIUS = 0.075
LEG_TOP = (0.090, 0.0, 0.900)
LEG_BOTTOM = (0.105, 0.0, 0.060)
HEAD_CENTRE = (0.0, 0.0, 1.660)
HEAD_RADIUS = 0.120

#: The height the pin vertex sits at.  A torso ring is built exactly here so
#: the assertion is about a vertex that really is on the torso's flank rather
#: than about whatever happened to be nearest.
PIN_Z = 1.420

#: The vertex the whole suite turns on: on the torso's own flank, at shoulder
#: height, 180 mm out — 100 mm clear of the arm's axis and 55 mm clear of the
#: arm's surface.
PIN_POINT = Vector((TORSO_RADIUS, 0.0, PIN_Z))


def _tube(bm, points, radius, segments=16):
    """A closed tube of ``radius`` along a polyline, capped at both ends."""
    points = [Vector(p) for p in points]
    rings = []
    for index, centre in enumerate(points):
        if index == 0:
            direction = points[1] - points[0]
        elif index == len(points) - 1:
            direction = points[-1] - points[-2]
        else:
            direction = points[index + 1] - points[index - 1]
        direction.normalize()
        # Any two vectors perpendicular to the tangent; deterministic seed so the
        # mesh is byte-identical on every run (the cache key is a file hash).
        seed = Vector((0.0, 0.0, 1.0))
        if abs(direction.dot(seed)) > 0.9:
            seed = Vector((1.0, 0.0, 0.0))
        side = direction.cross(seed).normalized()
        other = direction.cross(side).normalized()
        ring = []
        for step in range(segments):
            angle = 2.0 * math.pi * step / segments
            offset = side * (radius * math.cos(angle)) + other * (radius * math.sin(angle))
            ring.append(bm.verts.new(centre + offset))
        rings.append(ring)
    bm.verts.ensure_lookup_table()
    for index in range(len(rings) - 1):
        lower, upper = rings[index], rings[index + 1]
        for step in range(segments):
            nxt = (step + 1) % segments
            bm.faces.new((lower[step], lower[nxt], upper[nxt], upper[step]))
    bm.faces.new(tuple(reversed(rings[0])))
    bm.faces.new(tuple(rings[-1]))
    return rings


def build_hanging_arm_biped(name=BIPED):
    """A biped whose arms hang at its sides, facing -Y, its left at +X.

    Built rather than imported: the Phase 3/4 synthetic sculpt is T-posed, and a
    T-posed biped cannot reproduce the failure this module fixes.
    """
    existing = bpy.data.objects.get(name)
    if existing is not None:
        data = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if data is not None and getattr(data, "users", 1) == 0:
            bpy.data.meshes.remove(data)

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    # torso, straight up the midline, with a ring at PIN_Z so the vertex the
    # assertions are about actually exists rather than being approximated
    _tube(bm, [(0.0, 0.0, TORSO_LOW), (0.0, 0.0, 1.03), (0.0, 0.0, 1.18),
               (0.0, 0.0, 1.30), (0.0, 0.0, PIN_Z), (0.0, 0.0, TORSO_HIGH)],
          TORSO_RADIUS, segments=20)
    # neck
    _tube(bm, [(0.0, 0.0, 1.470), (0.0, 0.0, 1.570)], 0.055, segments=12)
    # head, with a nose at -Y so the orientation gate has a measurable front
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12,
                              radius=HEAD_RADIUS,
                              matrix=__import__("mathutils").Matrix.Translation(HEAD_CENTRE))
    _tube(bm, [(0.0, -0.09, 1.660), (0.0, -0.165, 1.645)], 0.035, segments=10)
    for sign in (1.0, -1.0):
        top = Vector((sign * ARM_TOP[0], ARM_TOP[1], ARM_TOP[2]))
        bottom = Vector((sign * ARM_BOTTOM[0], ARM_BOTTOM[1], ARM_BOTTOM[2]))
        _tube(bm, [top, top.lerp(bottom, 0.5), bottom], ARM_RADIUS, segments=14)
        # a hand, on the end of the arm rather than a separate lump
        hand = bottom + (bottom - top).normalized() * 0.075
        _tube(bm, [bottom, hand], 0.050, segments=12)

        leg_top = Vector((sign * LEG_TOP[0], LEG_TOP[1], LEG_TOP[2]))
        leg_bottom = Vector((sign * LEG_BOTTOM[0], LEG_BOTTOM[1], LEG_BOTTOM[2]))
        _tube(bm, [leg_top, leg_top.lerp(leg_bottom, 0.5), leg_bottom],
              LEG_RADIUS, segments=14)
        # a foot pointing -Y, which is also what makes the figure lopsided front
        # to back and therefore gives the orientation gate something to measure
        _tube(bm, [(sign * LEG_BOTTOM[0], 0.0, 0.035),
                   (sign * LEG_BOTTOM[0], -0.115, 0.030)], 0.045, segments=10)

    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    from forge.tools.common import refresh_view_layer

    refresh_view_layer()
    return obj


#: The skeleton a detector would find in that biped, written by hand.  Indices
#: are the decode order a real detector emits (positional, never anatomical);
#: everything downstream has to work this out from geometry.
CANNED_JOINTS = [
    # index, parent, world mm
    (0, None, (0, 0, 900)),        # root / hips
    (1, 0, (0, 0, 1050)),          # spine
    (2, 1, (0, 0, 1220)),          # chest
    (3, 2, (0, 0, 1460)),          # shoulder girdle  <- branches
    (4, 3, (0, 0, 1570)),          # neck
    (5, 4, (0, 0, 1690)),          # head
    (6, 3, (280, 0, 1460)),        # left shoulder
    (7, 6, (290, 0, 1120)),        # left elbow
    (8, 7, (300, 0, 780)),         # left wrist
    (9, 3, (-280, 0, 1460)),       # right shoulder
    (10, 9, (-290, 0, 1120)),      # right elbow
    (11, 10, (-300, 0, 780)),      # right wrist
    (12, 0, (90, 0, 900)),         # left hip
    (13, 12, (98, 0, 480)),        # left knee
    (14, 13, (105, 0, 60)),        # left ankle
    (15, 0, (-90, 0, 900)),        # right hip
    (16, 15, (-98, 0, 480)),       # right knee
    (17, 16, (-105, 0, 60)),       # right ankle
]


def drop_joints(joints, doomed):
    """``joints`` without ``doomed``, **renumbered**, parents re-pointed.

    Renumbering is not a nicety.  A ``forge.joints/1`` document's ``parent`` is
    an index into its own array, so deleting an entry without repairing the rest
    does not make a smaller skeleton — it makes a *different* one, wired at
    random.  That is how this fixture first "crippled the left arm" and got a
    report blaming the right arm and the left leg.
    """
    doomed = set(doomed)
    kept = [entry for entry in joints if entry[0] not in doomed]
    renumber = {old[0]: new for new, old in enumerate(kept)}
    out = []
    for index, parent, head in kept:
        while parent is not None and parent in doomed:
            parent = next((p for i, p, _h in joints if i == parent), None)
        out.append((renumber[index], None if parent is None else renumber.get(parent),
                    head))
    return out


def canned_document(joints=CANNED_JOINTS, source="test"):
    """A ``forge.joints/1`` document in **world** millimetres."""
    return {
        "schema": "forge.joints/1",
        "source": source,
        "detector": {"name": "hand-written", "commit": "n/a", "weights": "n/a",
                     "seconds": 0.0, "vram_peak_mb": 0, "device": "cpu"},
        "mesh": {"file": "(synthetic)", "faces": 0, "vertices": 0},
        "frame": {"unit": "mm", "space": "world", "axis_up": "Z"},
        "joints": [{"index": index, "name": "bone_%d" % index,
                    "head_mm": list(head), "tail_mm": None,
                    "parent": parent, "confidence": None}
                   for index, parent, head in joints],
        "notes": ["written by headless_autotag.py: a detector stand-in"],
    }


def nearest_vertex(obj, point):
    matrix = obj.matrix_world
    best, best_distance = None, None
    for vertex in obj.data.vertices:
        distance = ((matrix @ vertex.co) - Vector(point)).length
        if best_distance is None or distance < best_distance:
            best, best_distance = vertex.index, distance
    return best, best_distance


def tag_of(obj, index):
    """Which ``tag_*`` group a vertex is in (there should be exactly one)."""
    from forge.tools import rigforge

    out = []
    for group in rigforge.tag_groups(obj):
        for entry in obj.data.vertices[index].groups:
            if entry.group == group.index and entry.weight > 0.0:
                out.append(rigforge.tag_display_name(group.name))
    return out


# --- the runner: cache, lock, and every way it can fail -----------------------

def load_runner():
    if RIGBRIDGE_DIR not in sys.path:
        sys.path.insert(0, RIGBRIDGE_DIR)
    import detector_runner

    return detector_runner


def fake_install(root, python_exit_code=3):
    """A UniRig install that looks complete and whose interpreter does nothing.

    Enough for ``diagnose`` to say "installed" without a checkout, a checkpoint
    or a GPU, so the cache and the lock can be tested on their own.
    """
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    weights = os.path.join(root, "model.ckpt")
    with open(weights, "wb") as handle:
        handle.write(b"not a checkpoint")
    if os.name == "nt":
        python = os.path.join(root, "fake_python.bat")
        with open(python, "w", encoding="ascii") as handle:
            handle.write("@echo off\r\necho fake detector\r\nexit /b %d\r\n"
                         % python_exit_code)
    else:
        python = os.path.join(root, "fake_python.sh")
        with open(python, "w", encoding="ascii") as handle:
            handle.write("#!/bin/sh\necho fake detector\nexit %d\n" % python_exit_code)
        os.chmod(python, 0o755)
    return python, weights


def test_runner_diagnosis(workspace):
    section("the runner: what is installed, and what is missing")
    runner = load_runner()
    check("detector_runner imports with nothing but the stdlib",
          hasattr(runner, "detect") and hasattr(runner, "diagnose"))
    check("it does not drag UniRig or torch into this interpreter",
          "torch" not in sys.modules and "src.data.extract" not in sys.modules,
          str([m for m in ("torch", "lightning") if m in sys.modules]))

    report = runner.diagnose(check_gpu=False)
    check("diagnose answers with installed/reason/says",
          isinstance(report, dict) and "installed" in report and report.get("says"),
          str(report)[:200])
    note("this machine: installed=%s reason=%s" % (report.get("installed"),
                                                   report.get("reason")))

    previous = os.environ.get("FORGE_RIGBRIDGE_DISABLE")
    os.environ["FORGE_RIGBRIDGE_DISABLE"] = "1"
    try:
        off = runner.diagnose()
        check("FORGE_RIGBRIDGE_DISABLE makes the install look absent",
              off["installed"] is False and off["reason"] == "disabled", str(off))
        mesh = os.path.join(workspace, "nothing.glb")
        with open(mesh, "wb") as handle:
            handle.write(b"glTF")
        result = runner.detect(mesh)
        check("and detect() degrades to a reason instead of raising",
              result["ok"] is False and result["reason"] == "disabled", str(result)[:200])
        check("the degraded result still carries a sentence to show the artist",
              bool(result.get("says")))
    finally:
        if previous is None:
            os.environ.pop("FORGE_RIGBRIDGE_DISABLE", None)
        else:
            os.environ["FORGE_RIGBRIDGE_DISABLE"] = previous

    missing = runner.detect(os.path.join(workspace, "no-such-mesh.glb"))
    check("a missing mesh is named, not raised",
          missing["ok"] is False and missing["reason"] == "mesh_missing", str(missing)[:160])
    wrong = os.path.join(workspace, "mesh.txt")
    with open(wrong, "w", encoding="ascii") as handle:
        handle.write("not a mesh")
    unsupported = runner.detect(wrong)
    check("an unsupported suffix is named, not raised",
          unsupported["ok"] is False and unsupported["reason"] == "mesh_unsupported",
          str(unsupported)[:160])
    check("every reason it can return is in the documented list",
          all(result["reason"] in runner.REASONS
              for result in (missing, unsupported)), str(runner.REASONS))


def test_runner_cache_and_lock(workspace):
    section("the runner: the cache, and one job at a time")
    runner = load_runner()
    root = os.path.join(workspace, "fake_unirig")
    python, weights = fake_install(root)
    cache = os.path.join(workspace, "cache")
    mesh = os.path.join(workspace, "biped.glb")
    with open(mesh, "wb") as handle:
        handle.write(b"glTF" + b"\x00" * 64)

    saved = {key: os.environ.get(key) for key in
             ("FORGE_UNIRIG_ROOT", "FORGE_UNIRIG_PYTHON", "FORGE_UNIRIG_WEIGHTS",
              "FORGE_RIGBRIDGE_CACHE")}
    os.environ["FORGE_UNIRIG_ROOT"] = root
    os.environ["FORGE_UNIRIG_PYTHON"] = python
    os.environ["FORGE_UNIRIG_WEIGHTS"] = weights
    os.environ["FORGE_RIGBRIDGE_CACHE"] = cache
    try:
        report = runner.diagnose(check_gpu=False)
        check("a complete-looking install diagnoses as available",
              report["installed"] is True, str(report)[:200])

        key = runner.mesh_key(mesh, 12345, 50000, 1000.0, weights)
        same = runner.mesh_key(mesh, 12345, 50000, 1000.0, weights)
        other_seed = runner.mesh_key(mesh, 999, 50000, 1000.0, weights)
        check("the cache key is the same for the same mesh and arguments", key == same)
        check("and different for a different seed", key != other_seed)
        with open(mesh, "ab") as handle:
            handle.write(b"\x01")
        edited = runner.mesh_key(mesh, 12345, 50000, 1000.0, weights)
        check("and different when the mesh's own bytes change (not its name)",
              key != edited)

        # A miss must actually try to run the detector -- and the fake
        # interpreter writes nothing, so this is also the "the detector died"
        # path.
        miss = runner.detect(mesh, check_gpu=False)
        check("a cache miss runs the detector subprocess",
              miss["ok"] is False and miss["reason"] == "detector_failed",
              str(miss)[:200])
        check("and a detector that writes nothing is reported, not raised",
              "exited" in (miss.get("says") or ""), str(miss.get("says"))[:200])

        # Now seed the cache at the key this mesh hashes to, and detect again.
        document = canned_document()
        os.makedirs(cache, exist_ok=True)
        with open(os.path.join(cache, edited + ".json"), "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        hit = runner.detect(mesh, check_gpu=False)
        check("a cache hit returns the joints", hit["ok"] is True, str(hit)[:200])
        check("and says it came from the cache", hit["cached"] is True)
        check("and it skipped the subprocess entirely (no returncode)",
              "returncode" not in hit, str(sorted(hit)))
        check("the cached document is the one that was stored",
              len(hit["joints"]["joints"]) == len(document["joints"]))
        check("a cache hit costs no detector time",
              hit["seconds"] < 1.0, "%.3f s" % hit["seconds"])

        refreshed = runner.detect(mesh, check_gpu=False, refresh=True)
        check("refresh=True ignores the cache and runs the detector again",
              refreshed["ok"] is False and refreshed["reason"] == "detector_failed",
              str(refreshed)[:160])

        out = os.path.join(workspace, "joints_out.json")
        delivered = runner.detect(mesh, output=out, check_gpu=False)
        check("a cache hit still writes the output file the caller asked for",
              delivered["ok"] and os.path.isfile(out))

        lock = runner._Lock()
        check("the lock is taken", lock.acquire() is True)
        try:
            busy = runner.detect(mesh, check_gpu=False, use_cache=False)
            check("a second detection while one holds the lock is refused by name",
                  busy["ok"] is False and busy["reason"] == "busy", str(busy)[:200])
        finally:
            lock.release()
        check("and the lock file is gone once it is released",
              not os.path.isfile(lock.path))
        after = runner.detect(mesh, check_gpu=False)
        check("a released lock lets the next detection through",
              after["ok"] is True and after["cached"] is True, str(after)[:160])
    finally:
        for key_name, value in saved.items():
            if value is None:
                os.environ.pop(key_name, None)
            else:
                os.environ[key_name] = value


# --- the tag build ------------------------------------------------------------

def test_skeleton_reading(obj):
    section("reading a skeleton: segments, then roles, from geometry alone")
    from forge.tools import rigforge_autotag as autotag
    from forge.tools import rigforge_joints

    document = canned_document()
    hints = rigforge_joints.JointHints(document, obj, weight=0.0, path="(test)")
    check("the hand-written joints land inside the mesh",
          hints.enabled and hints.inside == hints.count,
          "%d of %d inside" % (hints.inside, hints.count))
    check("the detector's names are read as no names at all",
          hints.named == 0 and hints.placeholders == len(document["joints"]),
          "named=%d placeholders=%d" % (hints.named, hints.placeholders))

    segments, children, roots = autotag.segments_of(list(hints.parents))
    check("the tree has one root", roots == [0], str(roots))
    check("it cuts into six segments: spine, neck, two arms, two legs",
          len(segments) == 6, str(segments))

    points = list(hints.points)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    roles, why, unplaced = autotag.classify_segments(
        segments, children, points, Vector((-0.4, -0.2, 0.0)), Vector((0.4, 0.2, 1.79)),
        0.0, 1.0, root=0)
    check("every role is named", sorted(roles) ==
          ["arm.L", "arm.R", "head", "leg.L", "leg.R", "spine"], str(sorted(roles)))
    check("nothing is left unplaced", not unplaced, str(unplaced))
    check("the spine is the chain leaving the root upwards",
          roles["spine"] == [0, 1, 2, 3], str(roles.get("spine")))
    check("the head continues above it", roles["head"] == [3, 4, 5], str(roles.get("head")))
    check("arm.L is on the character's left (+X), not wherever index 6 fell",
          all(points[i].x >= 0.0 for i in roles["arm.L"][1:]), str(roles["arm.L"]))
    check("arm.R is on the character's right", all(points[i].x <= 0.0
                                                   for i in roles["arm.R"][1:]),
          str(roles["arm.R"]))
    check("the legs are the chains that end near the floor",
          points[roles["leg.L"][-1]].z < 0.1 and points[roles["leg.R"][-1]].z < 0.1)
    check("every role says in words why it was named that",
          all(why.get(role) for role in roles), str(why))

    axes = autotag.build_axes(roles, children, points, None)
    check("a limb axis drops the branch point it hangs off",
          axes["arm.L"].source_joints[0] == 6 and axes["leg.L"].source_joints[0] == 12,
          "%s / %s" % (axes["arm.L"].source_joints, axes["leg.L"].source_joints))
    check("the spine keeps its root", axes["spine"].source_joints[0] == 0)
    direction = axes["arm.L"].direction
    check("the arm axis runs DOWN the body, which is the whole point",
          direction.z < -0.8, str([round(v, 3) for v in direction]))
    check("and the leg axis runs down too",
          axes["leg.L"].direction.z < -0.9, str([round(v, 3) for v in axes["leg.L"].direction]))
    return axes


def test_axis_tags_are_cylinders(obj):
    section("the tags: cylinders around each limb's own axis")
    from forge.tools import rigforge_autotag as autotag
    from forge.tools import rigforge_landmarks

    report = autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                              character_left=1.0, apply=True)
    check("every tag came from the detected skeleton",
          set(report["sources"].values()) == {"unirig"}, str(report["sources"]))
    check("all six tags exist", sorted(report["tags"]) ==
          ["Arm.L", "Arm.R", "Head", "Leg.L", "Leg.R", "Torso"], str(sorted(report["tags"])))
    check("the assignment converged rather than running to its pass limit",
          (report["girth"]["moved_per_pass"] or [1])[-1] == 0
          or report["girth"]["passes"] <= autotag.GIRTH_PASSES,
          str(report["girth"].get("moved_per_pass")))

    total = len(obj.data.vertices)
    tagged = sum(entry["vertices"] for entry in report["tags"].values())
    check("every vertex landed in exactly one tag: none dropped, none doubled",
          tagged == total, "%d tagged of %d vertices" % (tagged, total))

    # --- THE PINNED FAILURE ---------------------------------------------
    index, distance = nearest_vertex(obj, PIN_POINT)
    tags = tag_of(obj, index)
    note("pin vertex %d is %.1f mm from (%.0f, %.0f, %.0f) mm"
         % (index, distance * 1000.0, PIN_POINT.x * 1000, PIN_POINT.y * 1000,
            PIN_POINT.z * 1000))
    check("a shoulder-height TORSO vertex is in Torso, not in the arm tag",
          tags == ["Torso"], "it is in %s" % (tags or "nothing"))

    # ... and the method this replaces gets it wrong on the same mesh, which is
    # what makes the check above a test rather than a coincidence.
    points = rigforge_landmarks.world_points(obj)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    bands = autotag.box_band_groups(points, low, high, 0.0, 1.0)
    check("the box band it replaces puts that same vertex in the arm",
          index in set(bands["arm.L"]),
          "box band put it in %s" % [role for role, members in bands.items()
                                     if index in set(members)])

    # --- the tag really is a tube ---------------------------------------
    clouds = rigforge_landmarks.tag_clouds(obj)
    for tag, role in (("Arm.L", "arm.L"), ("Arm.R", "arm.R"),
                      ("Leg.L", "leg.L"), ("Leg.R", "leg.R")):
        cloud = clouds[tag]
        centre = Vector((0.0, 0.0, 0.0))
        for point in cloud:
            centre += point
        centre /= float(len(cloud))
        hint = Vector(report["axis_hints"][role])
        principal = rigforge_landmarks._principal_axis(cloud, centre, seed=hint)
        angle = math.degrees(principal.angle(hint, math.pi))
        angle = min(angle, 180.0 - angle)
        check("%s's own principal axis follows its detected axis (%.1f deg)"
              % (tag, angle), angle < 20.0, "%.1f degrees off" % angle)
        reach = max(abs(point.x) for point in cloud)
        near = min(abs(point.x) for point in cloud)
        if tag.startswith("Arm"):
            check("%s never reaches the midplane (nearest vertex %.0f mm out)"
                  % (tag, near * 1000.0), near > 0.100,
                  "%.1f mm from x=0" % (near * 1000.0))
        note("  %s: %d vertices, |x| %.0f..%.0f mm" % (tag, len(cloud), near * 1000.0,
                                                       reach * 1000.0))

    box_arm = [points[i] for i in bands["arm.L"]]
    box_centre = Vector((0.0, 0.0, 0.0))
    for point in box_arm:
        box_centre += point
    box_centre /= float(len(box_arm))
    box_principal = rigforge_landmarks._principal_axis(
        box_arm, box_centre, seed=Vector(report["axis_hints"]["arm.L"]))
    box_angle = math.degrees(box_principal.angle(
        Vector(report["axis_hints"]["arm.L"]), math.pi))
    box_angle = min(box_angle, 180.0 - box_angle)
    note("the box band's Arm.L principal axis is %.1f degrees off the real arm"
         % box_angle)
    return report


def test_landmark_fitter_accepts(obj, report):
    section("the landmark fitter: refused before, accepted now")
    from forge.tools import rigforge_autotag as autotag
    from forge.tools import rigforge_landmarks
    from forge.tools.registry import ForgeError

    hints = {role: Vector(vector) for role, vector in report["axis_hints"].items()}
    warnings = []
    landmarks = None
    error = None
    try:
        landmarks = rigforge_landmarks.biped_landmarks(
            obj, midplane=0.0, character_left=1.0, warnings=warnings, axis_hints=hints)
    except ForgeError as exc:
        error = str(exc)
    check("the fitter reads the auto-tagged arms instead of refusing them",
          landmarks is not None, error or "")
    if landmarks is not None:
        for role in ("shoulder.L", "elbow.L", "wrist.L", "hip.L", "knee.L", "ankle.L"):
            check("it measured %s" % role, role in landmarks["points"],
                  str(sorted(landmarks["points"])))
        arm = landmarks["limbs"]["arm.L"]
        check("the arm limb kept its own measured principal axis "
              "(it did not fall back to the hint)", not arm.axis_from_hint,
              "axis %s" % [round(v, 3) for v in arm.axis])
        check("and the limb records that its hint was measured, not assumed",
              arm.as_dict()["axis_hint_measured"] is True)
        elbow = landmarks["detail"]["elbow.L"]
        check("the elbow is a measurement, not a midpoint fallback",
              not str(elbow.get("how", "")).endswith("fallback"), str(elbow.get("how")))

    # The T-pose assumption on its own is not enough: the same tags with the old
    # anatomical hint are refused, which is why the hints are part of the fix.
    plain_error = None
    try:
        rigforge_landmarks.biped_landmarks(obj, midplane=0.0, character_left=1.0,
                                           warnings=[])
    except ForgeError as exc:
        plain_error = str(exc)
    check("without the measured hint the same tags are still refused "
          "(the T-pose assumption is half the bug)",
          plain_error is not None, "it was accepted")
    if plain_error:
        note("  refused with: %s" % plain_error[:150])

    # ... and what the box band hands the fitter instead.  On the werewolf this
    # is an outright refusal ("the tag 'Arm.L' is not shaped like a limb").  On
    # this smaller biped the band's arm tag is still *tube-ish* enough to be
    # accepted, and that is worth knowing rather than hiding: the band's defect
    # here is not the tag's shape but its **contents** — it has swallowed the
    # torso's flank, so the arm it measures is a fatter, more inboard arm than
    # the one on the mesh.  Both are measured.
    points = rigforge_landmarks.world_points(obj)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    bands = autotag.box_band_groups(points, low, high, 0.0, 1.0)
    axis_arm = set(_tag_counts(obj)["Arm.L"])
    axis_torso = set(_tag_counts(obj)["Torso"])
    band_arm = set(bands["arm.L"])
    contamination = band_arm & axis_torso
    check("the box band's arm tag contains torso flesh; the axis tag's does not",
          len(contamination) > 0, "%d shared" % len(contamination))
    note("  the band's Arm.L holds %d vertices, %d of them torso; the axis tag "
         "holds %d, none" % (len(band_arm), len(contamination), len(axis_arm)))

    def median_radius(indices, axis):
        radii = sorted(axis.closest(points[i])[0] for i in indices)
        return radii[len(radii) // 2]

    arm_axis = autotag.Axis("arm.L", [(0.28, 0.0, 1.46), (0.30, 0.0, 0.78)],
                            is_limb=True)
    band_girth = median_radius(band_arm, arm_axis)
    axis_girth = median_radius(axis_arm, arm_axis)
    check("so the arm the band measures is far thicker than the arm on the mesh",
          band_girth > 1.5 * axis_girth,
          "band %.0f mm vs axis %.0f mm" % (band_girth * 1000.0, axis_girth * 1000.0))
    note("  median radius about the true arm axis: band %.0f mm, axis tag %.0f mm"
         % (band_girth * 1000.0, axis_girth * 1000.0))

    saved = {}
    for role, tag in autotag.TAG_ROLES.items():
        saved[tag] = list(_tag_counts(obj).get(tag) or [])
        autotag.write_tag(obj, tag, bands.get(role, []), replace=True)
    obj.data.update()
    box_error = None
    try:
        rigforge_landmarks.biped_landmarks(obj, midplane=0.0, character_left=1.0,
                                           warnings=[], axis_hints=hints)
    except ForgeError as exc:
        box_error = str(exc)
    note("  the fitter on box-band tags: %s"
         % ("refused -- " + box_error[:140] if box_error else "accepted (this mesh is "
            "kinder than the werewolf, where it refuses)"))
    for tag, indices in saved.items():
        autotag.write_tag(obj, tag, indices, replace=True)
    obj.data.update()


def test_per_limb_fallback(obj):
    section("per-limb fallback, and what the report calls it")
    from forge.tools import rigforge_autotag as autotag

    # Keep the good tags so there is something to fall back *to*.
    autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                     character_left=1.0, apply=True)

    # The plausibility gate on its own, with nothing else in the way.
    from forge.tools import rigforge_landmarks

    points = rigforge_landmarks.world_points(obj)
    stub = autotag.Axis("arm.L", [(0.28, 0.0, 1.46), (0.29, 0.0, 1.40)], is_limb=True)
    ok, reasons = autotag.limb_plausible("arm.L", stub, list(range(200)), points, 1.79)
    check("a chain far shorter than the figure is refused as a limb",
          not ok and any("figure" in reason for reason in reasons), str(reasons))
    long_axis = autotag.Axis("arm.L", [(0.28, 0.0, 1.46), (0.30, 0.0, 0.78)],
                             is_limb=True)
    ok, reasons = autotag.limb_plausible("arm.L", long_axis, [1, 2, 3], points, 1.79)
    check("a tag too thin to cross-section is refused as a limb",
          not ok and any("cross-section" in reason for reason in reasons), str(reasons))
    check("and both refusals are sentences, not codes",
          all(len(reason) > 20 for reason in reasons), str(reasons))

    # A detection whose left arm collapsed into a stub: a real out-of-domain
    # miss (UniRig's F1 on extremities is ~0.105 and it drops whole chains).
    # The elbow and the wrist are gone and the shoulder is a 70 mm nub, which is
    # under the fraction of the figure's height a limb has to be.
    crippled = drop_joints(
        [(index, parent, (70, 0, 1450) if index == 6 else head)
         for index, parent, head in CANNED_JOINTS], (7, 8))
    warnings = []
    report = autotag.auto_tag(obj, joints=canned_document(crippled), midplane=0.0,
                              character_left=1.0, apply=True, warnings=warnings)
    sources = report["sources"]
    note("sources: %s" % sources)
    check("the crippled arm did not come from the detector",
          sources["Arm.L"] != "unirig", str(sources))
    check("it fell back to the tag that was already on the mesh",
          sources["Arm.L"] == "hand", str(sources))
    check("and the report says why in words",
          bool(report["tags"]["Arm.L"]["why"]), str(report["tags"]["Arm.L"]))
    check("the other limbs still came from the detector",
          sources["Arm.R"] == "unirig" and sources["Leg.L"] == "unirig"
          and sources["Leg.R"] == "unirig", str(sources))
    check("a mixed run warns that two kinds of tag now meet",
          any("fell back" in text for text in warnings), str(warnings)[:300])
    check("the fallen limb contributes no axis hint",
          "arm.L" not in report["axis_hints"], str(sorted(report["axis_hints"])))
    check("the limbs that were detected still do",
          "arm.R" in report["axis_hints"] and "leg.L" in report["axis_hints"])

    # With no tag to fall back to either, the last resort is the box band and it
    # is labelled as such rather than dressed up as a measurement.
    from forge.tools import rigforge

    group = obj.vertex_groups.get(rigforge.tag_group_name("Arm.L"))
    if group is not None:
        obj.vertex_groups.remove(group)
    obj.data.update()
    bare = autotag.auto_tag(obj, joints=canned_document(crippled), midplane=0.0,
                            character_left=1.0, apply=True, warnings=[])
    check("with no tag to keep, the last resort is the box band",
          bare["sources"]["Arm.L"] == "box", str(bare["sources"]))
    check("and it is named 'box', not passed off as a detection",
          bare["tags"]["Arm.L"]["source"] == "box")

    # Put the good tags back for whatever runs next.
    autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                     character_left=1.0, apply=True)


def test_provenance_and_hints(obj):
    section("what is recorded on the object")
    from forge.tools import rigforge_autotag as autotag

    report = autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                              character_left=1.0, apply=True)
    stored = json.loads(obj[autotag.PROP_TAG_SOURCE])
    check("the provenance of every tag is stored on the object",
          stored == report["sources"], str(stored))
    hints, complaint = autotag.stored_axis_hints(obj)
    check("the measured limb directions are stored too", bool(hints), str(complaint))
    check("and they match what the run returned",
          all((Vector(report["axis_hints"][role]) - hints[role]).length < 1e-4
              for role in report["axis_hints"]), str(hints))

    # A hint measured on a different shape is worse than no hint, so it expires.
    previous = obj.location.copy()
    obj.location = (previous.x + 0.25, previous.y, previous.z)
    from forge.tools.common import refresh_view_layer

    refresh_view_layer()
    stale, complaint = autotag.stored_axis_hints(obj)
    check("a hint stops applying once the mesh has moved",
          stale is None and bool(complaint), str(complaint))
    note("  %s" % complaint)
    obj.location = previous
    refresh_view_layer()
    back, _ = autotag.stored_axis_hints(obj)
    check("and applies again when it has not", bool(back))


#: The radius of the mitten hand on :func:`build_mitten_biped` — nearly twice
#: the forearm's, which is what a palm is.
MITTEN_RADIUS = 0.085

#: How far past the wrist the mitten's centre sits.
MITTEN_OFFSET = 0.055


def build_mitten_biped(name="MittenArmBiped"):
    """The same figure with a **mitten** on the end of each arm.

    A limb is a tube right up until its extremity, where it is a hand: wider
    than the wrist it hangs off, and *not a tube* — a ball of flesh the limb's
    own axis runs into rather than along.  That shape is why the girth cap needs
    a different statistic out there and the reason is visible in this fixture: a
    median of the mitten's cross-section is pulled down by the flesh near the
    axis (the near side of the palm, and on a real paw the fingers), so a cap
    read off it clips the far side of the palm off the limb it belongs to.

    Measured live on the werewolf, whose ``Arm.L`` girth fell to 28.8 mm at its
    last station while its palm reaches 75 mm from the hand bone — a 46 mm cap
    around a 75 mm palm, with a 79 mm-girth thigh hanging next to it waiting to
    accept whatever the arm threw out.
    """
    existing = bpy.data.objects.get(name)
    if existing is not None:
        data = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if data is not None and getattr(data, "users", 1) == 0:
            bpy.data.meshes.remove(data)

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    _tube(bm, [(0.0, 0.0, TORSO_LOW), (0.0, 0.0, 1.03), (0.0, 0.0, 1.18),
               (0.0, 0.0, 1.30), (0.0, 0.0, PIN_Z), (0.0, 0.0, TORSO_HIGH)],
          TORSO_RADIUS, segments=20)
    _tube(bm, [(0.0, 0.0, 1.470), (0.0, 0.0, 1.570)], 0.055, segments=12)
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12,
                              radius=HEAD_RADIUS,
                              matrix=__import__("mathutils").Matrix.Translation(
                                  HEAD_CENTRE))
    _tube(bm, [(0.0, -0.09, 1.660), (0.0, -0.165, 1.645)], 0.035, segments=10)
    for sign in (1.0, -1.0):
        top = Vector((sign * ARM_TOP[0], ARM_TOP[1], ARM_TOP[2]))
        bottom = Vector((sign * ARM_BOTTOM[0], ARM_BOTTOM[1], ARM_BOTTOM[2]))
        _tube(bm, [top, top.lerp(bottom, 0.5), bottom], ARM_RADIUS, segments=14)
        direction = (bottom - top).normalized()
        bmesh.ops.create_uvsphere(
            bm, u_segments=18, v_segments=12, radius=MITTEN_RADIUS,
            matrix=__import__("mathutils").Matrix.Translation(
                bottom + direction * MITTEN_OFFSET))
        leg_top = Vector((sign * LEG_TOP[0], LEG_TOP[1], LEG_TOP[2]))
        leg_bottom = Vector((sign * LEG_BOTTOM[0], LEG_BOTTOM[1], LEG_BOTTOM[2]))
        _tube(bm, [leg_top, leg_top.lerp(leg_bottom, 0.5), leg_bottom],
              LEG_RADIUS, segments=14)
        _tube(bm, [(sign * LEG_BOTTOM[0], 0.0, 0.035),
                   (sign * LEG_BOTTOM[0], -0.115, 0.030)], 0.045, segments=10)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    from forge.tools.common import refresh_view_layer

    refresh_view_layer()
    return obj


def test_the_hand_rides_the_arm():
    """The palm belongs to the arm, and the girth cap has to say so."""
    section("the mitten hand: a limb's extremity is wider than its shaft")
    from forge.tools import rigforge, rigforge_autotag as autotag
    from forge.tools.common import M_TO_MM

    obj = build_mitten_biped()
    note("mitten biped: %d vertices; forearm radius %.0f mm, palm %.0f mm"
         % (len(obj.data.vertices), ARM_RADIUS * 1000.0, MITTEN_RADIUS * 1000.0))
    report = autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                              character_left=1.0, apply=True)
    check("the arms were still built from the detected chain, mitten and all",
          report["sources"]["Arm.L"] == "unirig"
          and report["sources"]["Arm.R"] == "unirig", str(report["sources"]))

    # Every vertex of the left palm, by construction rather than by eye.
    top = Vector(ARM_TOP)
    bottom = Vector(ARM_BOTTOM)
    direction = (bottom - top).normalized()
    centre = bottom + direction * MITTEN_OFFSET
    matrix = obj.matrix_world
    palm = [vertex.index for vertex in obj.data.vertices
            if ((matrix @ vertex.co) - centre).length <= MITTEN_RADIUS + 1e-4]
    owners = {}
    for index in palm:
        name = "+".join(tag_of(obj, index)) or "<none>"
        owners[name] = owners.get(name, 0) + 1
    note("the left palm is %d vertices; they landed in %s" % (len(palm), owners))
    check("the palm is a real bulge on this figure, not a rounding error",
          len(palm) > 40, "%d vertices" % len(palm))
    check("EVERY palm vertex lands in the arm tag, not in whatever is beside it",
          owners == {"Arm.L": len(palm)}, str(owners))

    profile = report["girth"]["girth_mm"]["arm.L"]
    distal = profile[-autotag.DISTAL_STATIONS:]
    note("arm.L girth profile mm: %s" % profile)
    check("and the girth profile at the distal stations reads the palm, not the "
          "wrist it tapered from",
          max(distal) > 1.5 * ARM_RADIUS * 1000.0,
          "distal %s against a %.0f mm forearm" % (distal, ARM_RADIUS * 1000.0))
    check("it reads the radius the palm was actually built at",
          abs(max(distal) - MITTEN_RADIUS * 1000.0) < 8.0,
          "%s vs %.0f mm" % (distal, MITTEN_RADIUS * 1000.0))
    check("the profile widens at the extremity instead of tapering to nothing",
          max(distal) > profile[len(profile) // 2 + 2],
          "distal %s, mid-limb %.1f" % (distal, profile[len(profile) // 2 + 2]))

    # The A/B that pins the rule: the same flesh, the same stations, the median
    # against the distal percentile. A hand is a ball the axis runs *into*, so
    # its near side sits close to that axis and drags a median down.
    axis_points = [Vector([value / M_TO_MM for value in point])
                   for point in report["axes"]["arm.L"]["points_mm"]]
    axis = autotag.Axis("arm.L", axis_points, is_limb=True)
    own = [axis.closest(matrix @ obj.data.vertices[index].co)
           for index in _tagged_indices(obj, "Arm.L")]
    median_only = autotag._girth_profile(own, distal=0)
    with_bulge = autotag._girth_profile(own, distal=autotag.DISTAL_STATIONS)
    note("the same flesh, median vs p%d at the tip: %s vs %s"
         % (100 * autotag.DISTAL_PERCENTILE,
            [round(v * M_TO_MM) for v in median_only[-autotag.DISTAL_STATIONS:]],
            [round(v * M_TO_MM) for v in with_bulge[-autotag.DISTAL_STATIONS:]]))
    check("a median of a hand under-reads the palm it is supposed to cap",
          max(with_bulge[-autotag.DISTAL_STATIONS:])
          > 1.10 * max(median_only[-autotag.DISTAL_STATIONS:]),
          "%.1f vs %.1f mm"
          % (max(with_bulge[-autotag.DISTAL_STATIONS:]) * M_TO_MM,
             max(median_only[-autotag.DISTAL_STATIONS:]) * M_TO_MM))
    check("and the two rules agree everywhere else, so nothing along the shaft "
          "moved",
          all(abs(a - b) < 1e-9 for a, b
              in zip(median_only[:-autotag.DISTAL_STATIONS - 1],
                     with_bulge[:-autotag.DISTAL_STATIONS - 1])),
          str([round((a - b) * M_TO_MM, 2) for a, b
               in zip(median_only, with_bulge)]))

    # The median is still the rule along the shaft, where a rib is the risk.
    shaft = profile[9:-autotag.DISTAL_STATIONS]
    check("and the shaft is still capped on its median and stays a forearm",
          all(value < 2.0 * ARM_RADIUS * 1000.0 for value in shaft),
          "shaft %s" % shaft)

    # And a mitten must not cost the arm its own credibility gate: a tag that
    # has grown a palm is still a tube around its own axis, and the check the
    # landmark fitter is about to apply is the one applied here.
    check("the arm tag with a palm on it still passes its own limb gate",
          report["tags"]["Arm.L"]["source"] == "unirig"
          and not report["tags"]["Arm.L"]["why"],
          str(report["tags"]["Arm.L"]))
    check("and the arm's own axis hint was measured and stored",
          "arm.L" in report["axis_hints"], str(sorted(report["axis_hints"])))
    bpy.data.objects.remove(obj, do_unlink=True)


def _tagged_indices(obj, tag):
    from forge.tools import rigforge

    group = obj.vertex_groups.get(rigforge.tag_group_name(tag))
    if group is None:
        return []
    return [vertex.index for vertex in obj.data.vertices
            for element in vertex.groups
            if element.group == group.index and element.weight > 0.0]


def _ring_cloud(z_low, z_high, radius_at, rings=60, around=20):
    """A tube of rings whose radius is a function of height — a spine to split."""
    out = []
    for step in range(rings + 1):
        z = z_low + (z_high - z_low) * step / float(rings)
        radius = radius_at(z)
        for turn in range(around):
            angle = 2.0 * math.pi * turn / around
            out.append(Vector((radius * math.cos(angle), radius * math.sin(angle), z)))
    return out


def test_spine_split_math(obj):
    """The Torso cut into three slabs along its own spine, on clouds not meshes.

    The split is pure geometry — an axis, a station grid and two point clouds —
    so it is tested as pure geometry, where a waist can be *built* rather than
    hoped for.  The mesh-level consequences are ``headless_skin``'s job.
    """
    section("the Torso split: two cuts on the spine's own station grid")
    from forge.tools import rigforge_autotag as autotag

    # A trunk with a real waist: wide chest, 25% narrower waist, wide hips.
    def waisted(z):
        return 0.180 - 0.045 * math.exp(-((z - 1.150) / 0.090) ** 2)

    torso = _ring_cloud(0.880, 1.500, waisted)
    legs = _ring_cloud(0.300, 0.960, lambda _z: 0.075)
    legs = [Vector((p.x + 0.090, p.y, p.z)) for p in legs]
    axis = autotag.axis_from_cloud(torso, "spine")
    check("the axis a cloud gets runs proximal (low) to distal (high)",
          axis.points[0].z < axis.points[-1].z,
          "%.0f -> %.0f mm" % (axis.points[0].z * 1000.0, axis.points[-1].z * 1000.0))
    check("and it spans the cloud's own extent, not the detected chain's",
          abs(axis.length - 0.620) < 0.002, "%.1f mm" % (axis.length * 1000.0))

    split = autotag.spine_split(axis, torso, legs)
    note(split["says"])
    check("the split is not refused on a trunk with legs beside it",
          "refused" not in split, split.get("refused"))
    stations = split["stations"]
    check("both cuts land exactly on a station of the grid the girth cap uses",
          all(abs(split["cuts"][i] - split["cut_stations"][i] / float(stations - 1))
              < 1e-9 for i in (0, 1))
          and stations == autotag.GIRTH_STATIONS,
          "%s of %d stations" % (split["cut_stations"], stations))

    # 1. the pelvis cut is a measurement of where the legs are, not a fraction.
    legs_t = sorted(axis.closest(p)[1] for p in legs)
    p95 = legs_t[int(round(0.95 * (len(legs_t) - 1)))]
    check("the pelvis cut is the station nearest the legs' own p95 on the spine",
          split["cut_stations"][0] == max(1, int(round(p95 * (stations - 1)))),
          "cut at station %d, p95 is t=%.3f" % (split["cut_stations"][0], p95))
    check("and the report says so rather than leaving it to be inferred",
          "legs join" in split["how"][0], split["how"][0])

    # 2. the chest cut is the waist, and this trunk has one.
    girths = split["girth_mm"]
    second = split["cut_stations"][1]
    check("the chest cut is the waist: narrower than both its neighbours",
          girths[second] < girths[second - 1] and girths[second] < girths[second + 1],
          "%s at %d, neighbours %s / %s" % (girths[second], second,
                                            girths[second - 1], girths[second + 1]))
    check("and it is the station the waist was actually modelled at",
          abs((axis.points[0] + (axis.points[-1] - axis.points[0])
               * split["cuts"][1]).z - 1.150) < 0.030,
          "cut at z=%.0f mm, waist built at 1150 mm"
          % ((axis.points[0] + (axis.points[-1] - axis.points[0])
              * split["cuts"][1]).z * 1000.0))
    check("the report names the rule that fired", "waist" in split["how"][1],
          split["how"][1])
    check("the two cuts are far enough apart to leave an abdomen",
          split["cut_stations"][1] - split["cut_stations"][0]
          >= autotag.MIN_CUT_SEPARATION, str(split["cut_stations"]))

    # 3. a uniform tube has no waist, and must say so rather than cut at noise.
    plain = _ring_cloud(0.880, 1.500, lambda _z: 0.180)
    flat = autotag.spine_split(autotag.axis_from_cloud(plain, "spine"), plain, legs)
    check("a uniform trunk falls back instead of cutting at the median's noise",
          "no measurable waist" in flat["how"][1], flat["how"][1])
    check("and the fallback still lands on a station, between the same bounds",
          abs(flat["cuts"][1] - flat["cut_stations"][1] / float(stations - 1)) < 1e-9
          and flat["cut_stations"][1] > flat["cut_stations"][0],
          str(flat["cut_stations"]))
    note("  waisted trunk cut at station %d, uniform one at %d"
         % (split["cut_stations"][1], flat["cut_stations"][1]))

    # 4. every vertex lands in exactly one slab, and the slabs are in order.
    membership = autotag.split_membership(axis, split["cuts"], torso)
    counts = {}
    for name in membership:
        counts[name] = counts.get(name, 0) + 1
    check("every torso point lands in exactly one slab",
          sum(counts.values()) == len(torso) and counts == split["vertices"],
          "%s vs %s" % (counts, split["vertices"]))
    heights = {name: [] for name in autotag.TORSO_SUB_TAGS}
    for point, name in zip(torso, membership):
        heights[name].append(point.z)
    order = autotag.TORSO_SUB_TAGS
    check("and the slabs stack up the spine in the order they are named",
          max(heights[order[0]]) <= min(heights[order[1]]) + 1e-9
          and max(heights[order[1]]) <= min(heights[order[2]]) + 1e-9,
          str({name: (round(min(v) * 1000), round(max(v) * 1000))
               for name, v in heights.items()}))

    # 5. a bone is a span, and a span that crosses a cut is in both slabs.
    cuts = split["cuts"]
    inside = autotag.sub_tags_spanning(cuts[0] + 0.02, cuts[1] - 0.02, cuts)
    crossing = autotag.sub_tags_spanning(cuts[0] - 0.05, cuts[0] + 0.05, cuts)
    both_cuts = autotag.sub_tags_spanning(-0.2, 1.2, cuts)
    check("a bone that lies inside one slab is in that slab alone",
          inside == (order[1],), str(inside))
    check("a bone whose span crosses a cut is in the slabs on BOTH sides of it",
          crossing == (order[0], order[1]), str(crossing))
    check("and one that crosses both cuts is in all three",
          both_cuts == tuple(order), str(both_cuts))
    check("a zero-length span still lands somewhere",
          autotag.sub_tags_spanning(cuts[1] + 0.1, cuts[1] + 0.1, cuts)
          == (order[2],),
          str(autotag.sub_tags_spanning(cuts[1] + 0.1, cuts[1] + 0.1, cuts)))

    # 6. refusals, named.
    lonely = autotag.spine_split(axis, torso, [])
    check("a trunk with no leg tag beside it is refused, not guessed at",
          "refused" in lonely and "nothing measures where the pelvis ends"
          in lonely["refused"], str(lonely.get("refused")))
    tiny = _ring_cloud(0.880, 1.500, lambda _z: 0.180, rings=2, around=4)
    thin = autotag.spine_split(autotag.axis_from_cloud(tiny, "spine"), tiny, legs)
    check("and so is a Torso with too few vertices to make three slabs",
          "refused" in thin and "three slabs" in thin["refused"],
          str(thin.get("refused")))
    note("  %s" % thin.get("refused"))


def test_split_is_a_view_not_a_tag(obj):
    section("the split is a derived view: the mesh still carries one Torso")
    from forge.tools import rigforge, rigforge_autotag as autotag, rigforge_rig

    before = {tag: len(members) for tag, members in _tag_counts(obj).items()}
    groups_before = sorted(group.name for group in rigforge.tag_groups(obj))
    properties_before = sorted(obj.keys())
    report = autotag.auto_tag(obj, joints=canned_document(), midplane=0.0,
                             character_left=1.0, apply=True)

    split = report["sub_tags"]
    note(split.get("says") or split.get("refused"))
    check("every run answers the split question, with cuts or with a reason",
          bool(split.get("cuts")) != bool(split.get("refused")),
          str(split)[:200])
    check("and names the three slabs and the tag they are slabs of",
          split["names"] == list(autotag.TORSO_SUB_TAGS)
          and split["parent"] == autotag.SPLIT_PARENT, str(split["names"]))
    if split.get("cuts"):
        check("the slab counts add up to the Torso tag itself",
              sum(split["vertices"].values()) == before["Torso"],
              "%d vs %d" % (sum(split["vertices"].values()), before["Torso"]))
    else:
        # This suite's biped is three rings from shoulder to wrist - enough to
        # answer *which limb is this vertex on*, which is all tagging needs -
        # and its whole Torso is 60-odd vertices. A slab of it is too thin to
        # measure, so the split refuses, and refusing is the behaviour under
        # test here: the tag stays whole and everything downstream is unmoved.
        # The applied split lives on headless_skin's resampled figure.
        check("a Torso too coarse to slab refuses, and says which slab was thin",
              "cannot be measured" in split["refused"], split["refused"])
        check("a refusal is a warning, not a failure - the tags were still written",
              any("stays one tag" in text for text in report["warnings"]),
              str(report["warnings"])[:300])

    check("NO sub-tag vertex group was written - the fitter still sees six tags",
          sorted(group.name for group in rigforge.tag_groups(obj)) == groups_before,
          str(sorted(group.name for group in rigforge.tag_groups(obj))))
    check("and no new custom property was stamped on the mesh either",
          sorted(obj.keys()) == properties_before, str(sorted(obj.keys())))
    check("the tags themselves are unchanged in size", _tag_counts_equal(obj, before),
          str(before))

    regions, _empty = rigforge_rig.measure_tags(obj)
    check("measure_tags - what the landmark fitter and the metarig read - still "
          "returns exactly the six tags",
          sorted(regions) == ["Arm.L", "Arm.R", "Head", "Leg.L", "Leg.R", "Torso"],
          str(sorted(regions)))
    check("and the Torso region it measures is the whole torso, not a slab",
          regions["Torso"].count == before["Torso"],
          "%d vs %d" % (regions["Torso"].count, before["Torso"]))

    landmarks = call("rigforge_landmarks", {"object": obj.name, "action": "report"})
    check("the landmark fitter reads the merged Torso and still fits the figure",
          landmarks.get("landmarks") and "hips" in landmarks["landmarks"],
          str(sorted(landmarks.get("landmarks") or {}))[:200])


def test_command_surface(obj, workspace):
    section("the command: report, apply, and a detector that is not there")
    joints_path = os.path.join(workspace, "canned.json")
    with open(joints_path, "w", encoding="utf-8") as handle:
        json.dump(canned_document(), handle)

    before = {tag: len(members) for tag, members in _tag_counts(obj).items()}
    result = call("rigforge_autotag", {"object": obj.name, "action": "report",
                                       "joints_file": joints_path})
    check("action='report' answers without writing", result["applied"] is False)
    check("and the tags on the mesh are untouched", _tag_counts_equal(obj, before),
          str(before))
    check("it still names a source for every tag",
          sorted(result["sources"]) == ["Arm.L", "Arm.R", "Head", "Leg.L", "Leg.R",
                                        "Torso"], str(result["sources"]))
    check("a joints_file is used instead of running the detector",
          (result.get("detector") or {}).get("source") == "file",
          str(result.get("detector"))[:200])

    applied = call("rigforge_autotag", {"object": obj.name, "action": "apply",
                                        "joints_file": joints_path})
    check("action='apply' writes them", applied["applied"] is True)
    check("and says what it did in one line", bool(applied.get("says")),
          str(applied.get("says")))
    note("  says: %s" % applied.get("says"))

    previous = os.environ.get("FORGE_RIGBRIDGE_DISABLE")
    os.environ["FORGE_RIGBRIDGE_DISABLE"] = "1"
    try:
        degraded = call("rigforge_autotag", {"object": obj.name, "action": "report"})
        check("with no detector the command still answers",
              isinstance(degraded.get("sources"), dict), str(degraded)[:200])
        check("every tag comes from the fallback ladder",
              "unirig" not in set(degraded["sources"].values()),
              str(degraded["sources"]))
        check("and a warning says the detector did not run",
              any("detection did not run" in text
                  for text in degraded.get("warnings") or []),
              str(degraded.get("warnings"))[:300])
        refused = call("rigforge_autotag",
                       {"object": obj.name, "source": "detector", "action": "report"},
                       expect_error=True)
        check("source='detector' refuses rather than falling back",
              refused.get("status") == "error"
              and "source='detector'" in (refused.get("message") or ""),
              str(refused.get("message"))[:200])
        check("and the refusal quotes what the runner said",
              "FORGE_RIGBRIDGE_DISABLE" in (refused.get("message") or ""),
              str(refused.get("message"))[:200])

        meta = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                         "echo": False})
        tags = meta.get("auto_tags") or {}
        check("rigforge_metarig degrades quietly when the detector is absent",
              tags.get("used") is False and bool(tags.get("why")), str(tags)[:300])
        check("and it is a line in the result, not a warning "
              "(an absent optional tool is not a defect)",
              not any("detector" in text.lower() and "not installed" in text.lower()
                      for text in meta.get("warnings") or []),
              str(meta.get("warnings"))[:200])
    finally:
        if previous is None:
            os.environ.pop("FORGE_RIGBRIDGE_DISABLE", None)
        else:
            os.environ["FORGE_RIGBRIDGE_DISABLE"] = previous

    kept = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                     "echo": False, "tags": "keep"})
    tags = kept.get("auto_tags") or {}
    check("tags='keep' never touches the tags",
          tags.get("used") is False and "keep" in (tags.get("why") or ""),
          str(tags)[:200])
    check("the metarig result carries the auto-tag block either way",
          "auto_tags" in kept)


def _tag_counts(obj):
    from forge.tools import rigforge

    out = {}
    for group in rigforge.tag_groups(obj):
        members = []
        for vertex in obj.data.vertices:
            for entry in vertex.groups:
                if entry.group == group.index and entry.weight > 0.0:
                    members.append(vertex.index)
                    break
        out[rigforge.tag_display_name(group.name)] = members
    return out


def _tag_counts_equal(obj, before):
    now = {tag: len(members) for tag, members in _tag_counts(obj).items()}
    return now == before


def test_metarig_end_to_end(obj, workspace):
    section("end to end: auto tags -> landmarks -> a fitted metarig")
    joints_path = os.path.join(workspace, "canned.json")
    call("rigforge_autotag", {"object": obj.name, "action": "apply",
                              "joints_file": joints_path})
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False, "tags": "keep"})
    check("the metarig fit is the landmark fit, not the tag fallback",
          result.get("fit_method") == "landmarks",
          "%s -- %s" % (result.get("fit_method"),
                        [w for w in result.get("warnings") or []
                         if "could not read" in w]))
    mirror = result.get("mirror") or {}
    residual = mirror.get("residual_asymmetry_mm")
    check("the two sides are an exact mirror",
          residual is not None and abs(float(residual)) < 0.001, str(residual))
    landmarks = result.get("joint_landmarks") or {}
    check("the elbow was measured off the mesh",
          "elbow.L" in landmarks, str(sorted(landmarks))[:200])
    if "elbow.L" in landmarks:
        note("  elbow.L at %s mm (%s)" % (landmarks["elbow.L"].get("mm"),
                                          landmarks["elbow.L"].get("how")))


def fake_detector_install(root, document):
    """A UniRig install whose 'interpreter' hands back ``document``.

    The runner's contract with the detector is a *command line and a file*, so a
    batch script that copies a prepared JSON to ``--output`` is a complete,
    honest stand-in.  That is what lets the whole pipeline — metarig asks for a
    repair, the repair shells out, the tags come back, the fit is retried — run
    end to end in this suite with no GPU, no checkpoint and no download.
    """
    os.makedirs(root, exist_ok=True)
    weights = os.path.join(root, "model.ckpt")
    with open(weights, "wb") as handle:
        handle.write(b"not a checkpoint")
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    canned = os.path.join(root, "canned.json")
    with open(canned, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    python = os.path.join(root, "fake_python.bat")
    # argv is: detect_joints.py --input <mesh> --output <path> ...
    with open(python, "w", encoding="ascii") as handle:
        handle.write("@echo off\r\ncopy /y \"%s\" %%5 >nul\r\nexit /b 0\r\n" % canned)
    return {"FORGE_UNIRIG_ROOT": root, "FORGE_UNIRIG_PYTHON": python,
            "FORGE_UNIRIG_WEIGHTS": weights,
            "FORGE_RIGBRIDGE_CACHE": os.path.join(root, "cache")}


def test_repair_not_rewrite(obj, workspace):
    section("the default: a repair that has to prove it repaired something")
    from forge.tools import rigforge_autotag as autotag
    from forge.tools import rigforge_landmarks

    if os.name != "nt":
        note("the fake interpreter is a .bat; skipped off Windows")
        return

    # Good tags on the mesh: auto must leave them exactly alone.
    call("rigforge_autotag", {"object": obj.name, "action": "apply",
                              "joints_file": os.path.join(workspace, "canned.json")})
    before = _tag_counts(obj)
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False})
    tags = result.get("auto_tags") or {}
    check("tags that already measure as limbs are not touched",
          tags.get("used") is False and "left" in (tags.get("why") or ""),
          str(tags)[:200])
    check("and not one vertex moved between tags",
          _tag_counts(obj) == before)
    check("the fit is still the landmark fit", result.get("fit_method") == "landmarks",
          str(result.get("fit_method")))

    # Now break the tags into the box band, which the fitter refuses, and let a
    # fake detector hand back the good skeleton.
    points = rigforge_landmarks.world_points(obj)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    bands = autotag.box_band_groups(points, low, high, 0.0, 1.0)
    for role, tag in autotag.TAG_ROLES.items():
        autotag.write_tag(obj, tag, bands.get(role, []), replace=True)
    obj.data.update()
    if autotag.PROP_TAG_AXES in obj.keys():
        del obj[autotag.PROP_TAG_AXES]
    broken = _tag_counts(obj)

    saved = {key: os.environ.get(key) for key in
             ("FORGE_UNIRIG_ROOT", "FORGE_UNIRIG_PYTHON", "FORGE_UNIRIG_WEIGHTS",
              "FORGE_RIGBRIDGE_CACHE")}
    try:
        good = fake_detector_install(os.path.join(workspace, "fake_good"),
                                     canned_document())
        os.environ.update(good)
        repaired = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                             "echo": False})
        tags = repaired.get("auto_tags") or {}
        check("tags the fitter refuses are rebuilt from the detected skeleton",
              tags.get("used") is True, str(tags)[:250])
        check("and the rebuild moved the fit from refused to fitted",
              repaired.get("fit_method") == "landmarks",
              str(repaired.get("fit_method")))
        check("the tags really did change", _tag_counts(obj) != broken)
        check("a warning quotes the refusal that triggered the repair",
              any("could not be read as limbs" in text
                  for text in repaired.get("warnings") or []),
              str(repaired.get("warnings"))[:300])

        # ... and a detection that does NOT fix the fit must be taken back.
        for role, tag in autotag.TAG_ROLES.items():
            autotag.write_tag(obj, tag, bands.get(role, []), replace=True)
        obj.data.update()
        if autotag.PROP_TAG_AXES in obj.keys():
            del obj[autotag.PROP_TAG_AXES]
        broken_again = _tag_counts(obj)

        # A skeleton with no limbs at all: a spine and nothing else.  Every limb
        # falls back, the fitter still refuses, and the rebuild has bought
        # nothing -- which on a real mesh measured *worse*, not merely equal
        # (thigh.L's head moved from 726 mm to 252 mm on the Phase 3 sculpt).
        spine_only = [(0, None, (0, 0, 900)), (1, 0, (0, 0, 1150)),
                      (2, 1, (0, 0, 1400)), (3, 2, (0, 0, 1650))]
        useless = fake_detector_install(os.path.join(workspace, "fake_useless"),
                                        canned_document(spine_only))
        os.environ.update(useless)
        reverted = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                             "echo": False})
        tags = reverted.get("auto_tags") or {}
        check("a rebuild that does not fix the fit is reverted",
              tags.get("used") is False and tags.get("reverted") is True,
              str(tags)[:250])
        check("and every tag is put back exactly as it was",
              _tag_counts(obj) == broken_again,
              "%d tags differ" % sum(1 for tag in broken_again
                                     if _tag_counts(obj).get(tag) != broken_again[tag]))
        check("the report keeps both refusals, before and after",
              bool(tags.get("why_refused_before")) and bool(tags.get("why_refused_after")),
              str(tags)[:250])
        check("and a warning says the repair did not take",
              any("did not take" in text for text in reverted.get("warnings") or []),
              str(reverted.get("warnings"))[:300])
        check("so the fit is exactly the fallback it would have been anyway",
              reverted.get("fit_method") in ("tags", "tags+mirror"),
              str(reverted.get("fit_method")))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # An untagged mesh keeps the refusal it has always had, and costs no GPU.
    bare_mesh = bpy.data.meshes.new("BareForAutotag")
    bare_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    bare = bpy.data.objects.new("BareForAutotag", bare_mesh)
    bpy.context.scene.collection.objects.link(bare)
    from forge.tools.common import refresh_view_layer

    refresh_view_layer()
    reply = call("rigforge_metarig", {"object": "BareForAutotag"}, expect_error=True)
    check("an untagged mesh is still refused, not tagged out of thin air",
          reply.get("status") == "error" and "no tagged geometry" in
          (reply.get("message") or ""), str(reply.get("message"))[:200])
    bpy.data.objects.remove(bare, do_unlink=True)


def test_server_frees_its_port():
    section("teardown")
    from forge import server as forge_server

    forge_server.stop_server()
    check("the command socket closed", not forge_server.is_running())


# --- entry point --------------------------------------------------------------

def main():
    print("Forge add-on auto-tagging headless tests (rigforge_autotag + the runner)")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_autotag_test_")
    try:
        test_runner_diagnosis(workspace)
        test_runner_cache_and_lock(workspace)

        obj = build_hanging_arm_biped()
        note("biped: %d vertices, %d faces" % (len(obj.data.vertices),
                                               len(obj.data.polygons)))
        test_skeleton_reading(obj)
        report = test_axis_tags_are_cylinders(obj)
        test_landmark_fitter_accepts(obj, report)
        test_per_limb_fallback(obj)
        test_the_hand_rides_the_arm()
        test_spine_split_math(obj)
        test_split_is_a_view_not_a_tag(obj)
        test_provenance_and_hints(obj)
        test_command_surface(obj, workspace)
        test_metarig_end_to_end(obj, workspace)
        test_repair_not_rewrite(obj, workspace)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_server_frees_its_port()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
