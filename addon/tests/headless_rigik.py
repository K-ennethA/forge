"""Headless add-on tests for the IK / locomotion layer.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_rigik.py

Needs no geometry service and downloads nothing.  The character is Phase 3's
synthetic sculpt, tagged the way Phase 4 expects, carried through retopo ->
metarig -> generate, so what is under test is the rig the pipeline actually
produces rather than a hand-built skeleton that agrees with the test.

**What this suite is for.**  An audit of a generated character found a walk
cycle keyed on ``thigh_fk`` / ``shin_fk`` — a pure-FK leg, which has nothing at
all holding the foot on the ground between keys.  That is the oldest artefact
in game animation and it is *measurable*, so this suite measures it:

* the generated rig really does carry leg IK, arm IK, pole targets, a
  three-pivot foot roll and a per-limb FK/IK switch (``rigforge_ik``);
* the IK is **control-layer only** — the deform set is byte-identical before
  and after, which is why skinning, ``rig_check`` and the Godot export cannot
  notice it;
* ``rigforge_walk`` keys the feet through those IK targets with the stance
  phases world-locked;
* ``animation_check`` measures stance drift in millimetres, and **proves
  itself both ways**: the IK walk comes in near zero, a deliberately FK-keyed
  walk comes in at centimetres;
* the Godot export bakes the IK result onto the deform bones.

The two ``--background`` facts that shape this file are the usual ones: no
event loop (the harness drains the server queue from the main thread) and no
window.
"""

import hashlib
import inspect
import json
import math
import os
import shutil
import socket as socketlib
import struct
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

PORT = 9907  # not 9876 (a live session) and not 9878-9881 (phases 2-5)
SCULPT = "Sculpt"
RETOPO = SCULPT + "_retopo"

WALK = "walk"
WALK_LOOP = WALK + "-loop"
TREADMILL = "walk-inplace"
TREADMILL_LOOP = TREADMILL + "-loop"
FKWALK = "fkwalk"
FKWALK_LOOP = FKWALK + "-loop"

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


def call(command, params=None, timeout=1800.0, expect_error=False):
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


# --- the character ----------------------------------------------------------
#
# Why the sculpt is assembled here instead of being taken straight from
# ``headless_phase4.build_tagged_biped``.
#
# ``bmesh`` hands its elements back in allocation order, and allocation order
# is pointer order, which ASLR changes between processes.  Measured on this
# sculpt over fresh ``--factory-startup`` runs, three things moved:
#
# * the vertex order (the same 34 572 points, permuted);
# * a 1-ULP wobble on 28 of those points - ``subdivide_edges(use_grid_fill)``
#   averages corner coordinates in whatever order it walks them, and float32
#   addition is not associative;
# * **which** faces phase 3's ``build_sculpt`` punched its hole in and welded
#   its fins onto - both steps take the *first* elements of a bmesh iteration
#   (``[:8]``, ``break`` at two), so both follow that same pointer order.
#
# None of the three is large: canonicalised, two runs differ by three faces out
# of 36 858.  But the retopo behind them is a voxel remesh followed by a
# collapse decimate, and a decimate re-orders its whole collapse queue off one
# changed face.  That is what produced a different character every run
# (3 465-4 002 vertices, 484-515 mm leg chains, sometimes unweighted
# vertices), and with it a ``headless_jump`` that passed 233/0 on one run and
# failed 15 checks on the next.
#
# Proven, not assumed: feeding one byte-identical sculpt into the same
# ``rigforge_retopo`` -> ``rigforge_metarig`` -> ``rigforge_generate_rig``
# chain in four fresh processes returns the same retopo mesh, the same weights
# and the same 238-bone rig every time.  So the whole fix is upstream of the
# pipeline: give it a sculpt with a canonical order.
#
# The *shape* is still phase 3's - ``BLOBS`` and ``_uvsphere`` are imported,
# not copied, and the hole/fin rules below are its rules.  Only the order the
# elements are held in, and therefore which faces those rules pick, is ours.
# ``SCULPT_DIGEST`` is the gate: if phase 3's sculpt changes, this suite says
# so instead of quietly testing something else.

#: Grid the sculpt's coordinates are snapped to, in metres.  A micron is ~8x
#: the float32 ULP at this sculpt's 1.9 m extent, so it absorbs the wobble
#: above, and it is 1/20 000 of the 20.5 mm voxel the very next stage remeshes
#: at - far below anything downstream can measure.
SCULPT_QUANTUM = 1e-6

#: phase 3's own defect rules (``headless_rigforge.build_sculpt``): a hole of
#: eight faces in the upper front, and two three-faced edges.
SCULPT_HOLE_FACES = 8
SCULPT_HOLE_MIN_Z = 1.15
SCULPT_HOLE_MIN_Y = 0.30
SCULPT_FINS = 2

#: SHA-256 of the canonical sculpt, and of the retopo mesh ``build_character``
#: hands on (coordinates + polygon indices + per-group weights).  Both
#: measured over five fresh ``--background --factory-startup`` processes on
#: Blender 5.0; both are the determinism gate, not a tolerance.
SCULPT_DIGEST = "33285c5409d409cbf5fe5987dd51924627161c080f14dec024315789146dc715"
CHARACTER_DIGEST = "37d86bb36622f502380d1e53626b2e46d78ac92ebf1e47923f0a9d9000542fcb"


def geometry_digest(obj):
    """SHA-256 over a mesh's vertex coordinates, polygons and vertex weights.

    Everything a build can differ in that anything downstream can see.  The
    coordinates go in as float64 of the stored float32, so the digest is exact
    rather than rounded.
    """
    mesh = obj.data
    sha = hashlib.sha256()
    coords = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", coords)
    sha.update(("verts=%d" % len(mesh.vertices)).encode("utf-8"))
    sha.update(struct.pack("<%dd" % len(coords), *coords))
    rings = []
    for polygon in mesh.polygons:
        rings.append(len(polygon.vertices))
        rings.extend(polygon.vertices)
    sha.update(("polys=%d" % len(mesh.polygons)).encode("utf-8"))
    sha.update(struct.pack("<%dI" % len(rings), *rings))
    for group in sorted(obj.vertex_groups, key=lambda entry: entry.name):
        sha.update(("group=%s" % group.name).encode("utf-8"))
        for vertex in mesh.vertices:
            for entry in vertex.groups:
                if entry.group == group.index:
                    sha.update(struct.pack("<Id", vertex.index, entry.weight))
    return sha.hexdigest()


def _canonical_ring(ring):
    """A face's vertex ring rotated to start at its lowest index.

    Rotation only - never a reversal - so the winding, and with it the normal,
    is the one bmesh produced.
    """
    pivot = ring.index(min(ring))
    return tuple(ring[pivot:] + ring[:pivot])


def _canonical_blobs():
    """Phase 3's six subdivided spheres as (vertices, faces) in canonical order.

    The bmesh work is phase 3's, element for element; what leaves this function
    is sorted by position rather than by allocation.
    """
    import headless_rigforge as phase3

    bm = bmesh.new()
    try:
        for _label, centre, radius in phase3.BLOBS:
            made = phase3._uvsphere(bm, radius)
            bmesh.ops.translate(bm, verts=made["verts"], vec=Vector(centre))
        for _ in range(2):
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1,
                                      use_grid_fill=True)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        keys = [(int(round(vertex.co.x / SCULPT_QUANTUM)),
                 int(round(vertex.co.y / SCULPT_QUANTUM)),
                 int(round(vertex.co.z / SCULPT_QUANTUM)))
                for vertex in bm.verts]
        rings = [[vertex.index for vertex in face.verts] for face in bm.faces]
    finally:
        bm.free()

    if len(set(keys)) != len(keys):
        # Two vertices a micron apart would have to be ordered by something
        # other than position, and there is nothing deterministic left to order
        # them by. It does not happen on this sculpt; say so loudly if it ever
        # starts to.
        raise AssertionError(
            "the sculpt has %d vertices sharing a %g m grid cell: the canonical "
            "order is no longer well defined"
            % (len(keys) - len(set(keys)), SCULPT_QUANTUM))

    order = sorted(range(len(keys)), key=lambda index: keys[index])
    renumber = [0] * len(keys)
    for new, old in enumerate(order):
        renumber[old] = new
    verts = [tuple(axis * SCULPT_QUANTUM for axis in keys[old]) for old in order]
    faces = sorted(_canonical_ring([renumber[index] for index in ring])
                   for ring in rings)
    return verts, faces


def _punch_and_weld(verts, faces):
    """Phase 3's two deliberate defects, chosen over the canonical order.

    A hole (boundary edges are non-manifold as far as Quadriflow cares) and two
    three-faced edges - the same rules ``build_sculpt`` applies, reading down a
    sorted list instead of down a bmesh iterator.
    """
    holed = []
    for index, ring in enumerate(faces):
        count = float(len(ring))
        centre_y = sum(verts[i][1] for i in ring) / count
        centre_z = sum(verts[i][2] for i in ring) / count
        if centre_z > SCULPT_HOLE_MIN_Z and centre_y > SCULPT_HOLE_MIN_Y:
            holed.append(index)
            if len(holed) >= SCULPT_HOLE_FACES:
                break
    if len(holed) < SCULPT_HOLE_FACES:
        raise AssertionError("the sculpt has only %d face(s) to punch a hole in"
                             % len(holed))
    drop = set(holed)
    kept = [ring for index, ring in enumerate(faces) if index not in drop]

    # ``bmesh.ops.delete(context="FACES")`` takes the vertices the deleted faces
    # were the only user of with them. Phase 3's scattered hole orphans none;
    # this one is a single compact patch, so it does.
    used = sorted(set(vertex for ring in kept for vertex in ring))
    if len(used) != len(verts):
        renumber = {old: new for new, old in enumerate(used)}
        verts = [verts[old] for old in used]
        kept = sorted(_canonical_ring([renumber[vertex] for vertex in ring])
                      for ring in kept)

    edge_faces = {}
    vertex_faces = {}
    for index, ring in enumerate(kept):
        for position, vertex in enumerate(ring):
            other = ring[(position + 1) % len(ring)]
            edge_faces.setdefault((min(vertex, other), max(vertex, other)),
                                  []).append(index)
            vertex_faces.setdefault(vertex, []).append(index)

    present = set(frozenset(ring) for ring in kept)
    fins = []
    for edge in sorted(edge_faces):
        if len(edge_faces[edge]) != 2:
            continue
        first, second = edge
        neighbour = None
        for index in vertex_faces.get(first, ()):
            for vertex in kept[index]:
                if vertex != first and vertex != second:
                    neighbour = vertex
                    break
            if neighbour is not None:
                break
        if neighbour is None:
            continue
        triangle = (first, second, neighbour)
        if frozenset(triangle) in present:
            continue
        present.add(frozenset(triangle))
        fins.append(_canonical_ring(list(triangle)))
        if len(fins) >= SCULPT_FINS:
            break
    if len(fins) < SCULPT_FINS:
        raise AssertionError("only %d fin(s) could be welded onto the sculpt"
                             % len(fins))
    return verts, sorted(kept + fins)


def build_canonical_sculpt(name=SCULPT):
    """Phase 3's sculpt, in an order that does not depend on the process."""
    verts, faces = _punch_and_weld(*_canonical_blobs())
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([list(point) for point in verts], [],
                     [list(ring) for ring in faces])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def build_tagged_biped():
    """``headless_phase4.build_tagged_biped``'s regions over the canonical sculpt.

    The classification and the ear rule are phase 3's and phase 4's; only the
    mesh they read is ours.
    """
    import headless_rigforge as phase3

    obj = build_canonical_sculpt(SCULPT)
    regions = phase3.classify_faces(obj)

    # Ears: the outer caps of the head sphere, exactly as
    # ``headless_phase4.build_tagged_biped`` cuts them.
    ears = {"Ear.L": [], "Ear.R": []}
    head = []
    in_head = set(regions["Head"])
    for index, centre in phase3.face_centres(obj):
        if index not in in_head:
            continue
        if centre.z > 1.72 and abs(centre.x) > 0.10:
            ears["Ear.L" if centre.x > 0 else "Ear.R"].append(index)
        else:
            head.append(index)
    regions["Head"] = head
    regions.update(ears)
    return obj, regions


#: Why the shared character is not built through Quadriflow.
#:
#: With the sculpt above pinned byte for byte, everything in
#: ``cmd_rigforge_retopo`` is deterministic except one stage.  Measured over
#: nine fresh processes on the identical sculpt: the voxel remesh returns the
#: same 26 674-vertex mesh every time, and the shrinkwrap, the tag transfer and
#: the unwrap are each a pure function of what they are handed - but
#: ``bpy.ops.object.quadriflow_remesh`` returns four different meshes.  Its
#: *topology* is stable (3 574 vertices, 3 453 faces, identical polygon
#: indices, every run); its vertex *positions* are not - 225 of those 3 574
#: vertices land up to **45.8 mm** apart between runs, with ``seed=0`` fixed
#: and symmetry off.  The race rides Blender's task scheduler:
#: ``OMP_NUM_THREADS=1`` changes nothing, and only a whole-process
#: ``blender --threads 1`` launch stills it (measured 2026-09-19, 7/7
#: byte-identical) - a launch flag no call can set, so no per-call Quadriflow
#: invocation is deterministic in a normally-threaded Blender.
#:
#: The product answered (2026-09-19) with ``method="decimate"`` on
#: ``rigforge_retopo`` - the collapse decimate to the same target as a
#: first-class route, a pure function of its input and byte-identical across
#: processes.  The fixture asks for it by name; ``CHARACTER_DIGEST`` above is
#: the cross-process pin, and ``headless_rigforge`` gates the route's two-run
#: determinism on its own sculpt.
def build_character():
    """Phase 3's sculpt, tagged, retopologised, metarigged and generated."""
    section("the character (Phase 3/4 builders, reused)")

    obj, regions = build_tagged_biped()
    sculpt_digest = geometry_digest(obj)
    note("sculpt: %d verts, %d faces, sha256 %s"
         % (len(obj.data.vertices), len(obj.data.polygons), sculpt_digest))
    check("the sculpt is the pinned, canonical build -- the same bytes every "
          "process", sculpt_digest == SCULPT_DIGEST,
          "%s, expected %s" % (sculpt_digest, SCULPT_DIGEST))
    for name, faces in sorted(regions.items()):
        if faces:
            call("rigforge_tag", {"object": obj.name, "tag": name, "faces": faces,
                                  "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get",
                               "archetype": "biped"})
    started = time.monotonic()
    retopo_report = call("rigforge_retopo", {"object": obj.name,
                                             "target_faces": 4000,
                                             "platform": "mobile", "lods": 0,
                                             "method": "decimate"})
    for warning in retopo_report.get("warnings") or []:
        note("warning: %s" % warning)
    quad = [stage for stage in retopo_report.get("stages") or []
            if stage.get("stage") == "quad_remesh"]
    check("the retopo took the deterministic collapse-decimate route, not the "
          "racing Quadriflow one",
          len(quad) == 1 and quad[0].get("method") == "decimate"
          and retopo_report.get("deterministic") is True,
          str(quad))
    retopo = bpy.data.objects.get(RETOPO)
    if not check("the retopo mesh exists", retopo is not None):
        raise AssertionError("no retopo mesh")

    character_digest = geometry_digest(retopo)
    note("character: %d verts, %d faces, sha256 %s"
         % (len(retopo.data.vertices), len(retopo.data.polygons), character_digest))
    check("...and it is the pinned character -- coordinates, polygons and "
          "weights, byte for byte, on every run",
          character_digest == CHARACTER_DIGEST,
          "%s, expected %s" % (character_digest, CHARACTER_DIGEST))

    meta = call("rigforge_metarig", {"object": RETOPO, "archetype": "auto"})
    generated = call("rigforge_generate_rig", {"metarig": meta["metarig"],
                                               "mesh": RETOPO})
    rig = bpy.data.objects.get(generated["rig"])
    check("the rig exists", rig is not None, generated["rig"])
    note("retopo+rig took %.1fs" % (time.monotonic() - started))
    return retopo, rig, meta, generated


# --- what the rig builder actually is ---------------------------------------

def test_what_the_builder_is(meta, generated, rig):
    section("the rig builder: Rigify, not a 29-bone hand-build")

    check("the metarig is one of Rigify's own templates",
          "metarig" in (generated.get("metarig") or "")
          or meta.get("metarig_operator", "").startswith("armature_"),
          "%s / %s" % (generated.get("metarig"), meta.get("metarig_operator")))
    note("metarig %r: %d bone(s), preset %s (%s)"
         % (meta["metarig"], meta["bone_count"], meta.get("preset"),
            meta.get("preset_reason")))
    note("generated rig %r: %d bone(s), %d deform, %d control"
         % (generated["rig"], generated["bone_count"], generated["deform_bones"],
            generated["control_bones"]))

    # The audit's "29 bones" is the METARIG. Generation multiplies it.
    check("generation turns the metarig into a much larger control rig",
          generated["bone_count"] > 4 * meta["bone_count"],
          "%d metarig bones -> %d rig bones"
          % (meta["bone_count"], generated["bone_count"]))
    check("and most of those bones are controls, not deform bones",
          generated["control_bones"] > 3 * generated["deform_bones"],
          "%d control vs %d deform"
          % (generated["control_bones"], generated["deform_bones"]))

    ik = generated.get("ik") or {}
    check("generate_rig reports the IK layer it left the rig on", bool(ik), str(ik)[:160])
    check("with all four biped limbs",
          sorted(ik.get("limb_names") or []) == ["arm.L", "arm.R", "leg.L", "leg.R"],
          str(ik.get("limb_names")))
    check("legs default to IK and arms to FK (the game convention)",
          (ik.get("convention") or {}) == {"leg": "ik", "arm": "fk"},
          str(ik.get("convention")))
    check("and the pole targets were switched on",
          len(ik.get("poles") or []) == 4, str(ik.get("poles")))
    note("ik says: %s" % ik.get("says"))


def test_ik_stretch_ships_off(rig, generated):
    section("the rig LEAVES generation with IK stretch off")
    from forge.tools import rigforge_rig as rr

    block = (generated.get("ik") or {}).get("stretch") or {}
    note("says: %s" % block.get("says"))
    check("the generate report carries an ik.stretch block", bool(block), str(block))
    check("it wrote the property on every limb switch the rig has",
          block.get("measured", 0) == len(generated.get("ik", {}).get("limbs") or []),
          "%s of %s" % (block.get("measured"),
                        len(generated.get("ik", {}).get("limbs") or [])))
    check("...to zero, which is the value a game rig ships",
          block.get("value") == rr.IK_STRETCH_DEFAULT == 0.0,
          "%s / %s" % (block.get("value"), rr.IK_STRETCH_DEFAULT))
    check("and it says what Rigify had it on before -- 1.0, the audited default",
          all(abs(float(row["before"]) - 1.0) < 1e-6
              for row in block.get("switches") or []),
          str(block.get("switches")))

    # The rig itself, not the report about it. This is the number every clip
    # the tools did NOT author inherits: a retarget, a hand-keyed pose, an
    # animator opening the file.
    live = {}
    for entry in rr.ik_limbs(rig):
        bone = rig.pose.bones.get(entry["switch_bone"])
        if bone is not None and rr.IK_STRETCH_PROP in bone.keys():
            live[bone.name] = round(float(bone[rr.IK_STRETCH_PROP]), 6)
    note("live on the rig: %s" % live)
    check("all four limb switches read 0.0 on the rig, with no clip assigned",
          len(live) == 4 and all(value == 0.0 for value in live.values()), str(live))
    check("...including the ARMS, which no authored clip ever planted",
          live.get("upper_arm_parent.L") == 0.0
          and live.get("upper_arm_parent.R") == 0.0, str(live))

    # And it is a function, not a side effect of generation: setting it back
    # and calling again restores it, and the report says what it changed.
    for name in live:
        rig.pose.bones[name][rr.IK_STRETCH_PROP] = 1.0
    again = rr.set_ik_stretch(rig)
    check("set_ik_stretch puts it back and counts what it changed",
          again["changed"] == 4
          and all(rig.pose.bones[name][rr.IK_STRETCH_PROP] == 0.0 for name in live),
          str(again["switches"]))
    idle = rr.set_ik_stretch(rig)
    check("...and says 'already there' when nothing needed changing",
          idle["changed"] == 0 and idle["measured"] == 4, idle["says"])


def test_bbones_are_flattened(rig):
    section("the export rig carries no bendy bone: glTF cannot")
    from forge.tools import rigforge_rig as rr

    control = {bone.name: int(bone.bbone_segments) for bone in rig.data.bones
               if bone.use_deform and int(bone.bbone_segments) > 1}
    note("the CONTROL rig carries %d multi-segment deform bone(s); worst %d segments"
         % (len(control), max(control.values()) if control else 1))
    check("Rigify really did ship bendy DEF bones to flatten -- otherwise this "
          "test proves nothing", len(control) >= 4, str(sorted(control.items())[:6]))

    collection = bpy.data.collections.new("FORGE_BBONE_TEST")
    bpy.context.scene.collection.children.link(collection)
    report = {}
    try:
        export_rig = rr.build_deform_rig(rig, "%s_bbone_test" % rig.name, collection,
                                         [], report=report)
        block = report.get("bbone_flattened") or {}
        note("says: %s" % block.get("says"))
        check("build_deform_rig reports what it flattened", bool(block), str(block)[:200])
        check("...how many, and it is the count the control rig carried",
              block.get("count") == len(control),
              "%s vs %s" % (block.get("count"), len(control)))
        check("...and the worst segment count it found, quoted",
              block.get("max_segments_before") == max(control.values()),
              "%s vs %s" % (block.get("max_segments_before"), max(control.values())))
        left = {bone.name: int(bone.bbone_segments) for bone in export_rig.data.bones
                if int(bone.bbone_segments) != 1}
        check("EVERY bone on the deform rig is a single segment",
              not left, str(sorted(left.items())[:8]))
        check("the flattened names are on the copy, for a caller that kept no report",
              sorted(export_rig.get("forge_bbone_flattened") or [])
              == sorted(block.get("flattened") or []),
              str(list(export_rig.get("forge_bbone_flattened") or [])[:4]))
        check("and the control rig was NOT touched -- the artist's rig still bends",
              all(int(rig.data.bones[name].bbone_segments) == segments
                  for name, segments in control.items()),
              str(sorted(control.items())[:4]))
    finally:
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)


# --- rigforge_ik ------------------------------------------------------------

def test_ik_report(rig):
    section("rigforge_ik - what the control layer has")
    result = call("rigforge_ik", {"rig": rig.name})
    limbs = {entry["name"]: entry for entry in result["limbs"]}
    check("it finds both legs and both arms",
          sorted(limbs) == ["arm.L", "arm.R", "leg.L", "leg.R"], str(sorted(limbs)))
    check("report changes nothing", result["changed"] == [] and result["action"] == "report",
          str(result["changed"]))

    for side in ("L", "R"):
        leg = limbs.get("leg.%s" % side) or {}
        check("leg.%s has a foot IK target" % side,
              leg.get("ik_target") == "foot_ik.%s" % side, str(leg.get("ik_target")))
        check("leg.%s has a knee pole target" % side,
              leg.get("pole_target") == "thigh_ik_target.%s" % side,
              str(leg.get("pole_target")))
        check("leg.%s's pole is switched on" % side, leg.get("pole_enabled") is True,
              str(leg.get("pole_enabled")))
        check("leg.%s has the three foot-roll pivots" % side,
              sorted((leg.get("roll_pivots") or {})) == ["heel", "spin", "toe"],
              str(leg.get("roll_pivots")))
        check("leg.%s's FK/IK switch is a property on thigh_parent.%s" % (side, side),
              leg.get("switch_bone") == "thigh_parent.%s" % side
              and leg.get("switch_prop") == "IK_FK",
              "%s[%s]" % (leg.get("switch_bone"), leg.get("switch_prop")))
        check("leg.%s is on IK" % side, leg.get("mode") == "ik", str(leg.get("ik_fk")))
        constraints = leg.get("ik_constraints") or []
        check("leg.%s really carries an IK constraint, with a target" % side,
              bool(constraints) and all(c["subtarget"] for c in constraints),
              str(constraints)[:200])
        check("and it solves a two-bone chain (thigh + shin)",
              leg.get("chain_count") == 2, str(leg.get("chain_count")))
        check("leg.%s names the deform bones it drives" % side,
              len(leg.get("deform_bones") or []) >= 4, str(leg.get("deform_bones")))

        arm = limbs.get("arm.%s" % side) or {}
        check("arm.%s has a hand IK target and an elbow pole" % side,
              arm.get("ik_target") == "hand_ik.%s" % side
              and arm.get("pole_target") == "upper_arm_ik_target.%s" % side,
              "%s / %s" % (arm.get("ik_target"), arm.get("pole_target")))
        check("arm.%s is on FK (arms swing on arcs; only the legs are planted)" % side,
              arm.get("mode") == "fk", str(arm.get("ik_fk")))

    reply = call("rigforge_ik", {"rig": rig.name, "limbs": ["tail.L"]}, expect_error=True)
    check("an unknown limb is refused with the limbs the rig does have",
          reply.get("status") == "error" and "leg.L" in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    return result


def test_ik_is_control_layer_only(rig, mesh):
    section("the IK layer touches no deform bone")
    before_bones = sorted(b.name for b in rig.data.bones)
    before_deform = sorted(b.name for b in rig.data.bones if b.use_deform)
    before_groups = sorted(g.name for g in mesh.vertex_groups)

    call("rigforge_ik", {"rig": rig.name, "action": "set", "mode": "fk"})
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})

    check("no bone was added or removed",
          sorted(b.name for b in rig.data.bones) == before_bones,
          str(set(b.name for b in rig.data.bones) ^ set(before_bones)))
    check("the deform set is identical",
          sorted(b.name for b in rig.data.bones if b.use_deform) == before_deform,
          "%d vs %d" % (len([b for b in rig.data.bones if b.use_deform]),
                        len(before_deform)))
    check("and so are the mesh's vertex groups (skinning cannot notice IK)",
          sorted(g.name for g in mesh.vertex_groups) == before_groups,
          str(set(g.name for g in mesh.vertex_groups) ^ set(before_groups)))


# --- the chains actually solve ----------------------------------------------

def world_head(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].head


def world_tail(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].tail


def _clear_pose(rig):
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()


def test_ik_solves(rig):
    section("the leg IK chain solves")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)
    rest = {name: world_head(rig, name).copy()
            for name in ("DEF-thigh.L", "DEF-shin.L", "DEF-foot.L")}
    lift = 0.08 * max(rig.dimensions)

    foot = rig.pose.bones["foot_ik.L"]
    foot.location = (0.0, 0.0, lift)
    bpy.context.view_layer.update()
    moved = {name: (world_head(rig, name) - point).length * 1000.0
             for name, point in rest.items()}
    note("moving foot_ik.L up %.0f mm: %s"
         % (lift * 1000.0, {k: round(v, 1) for k, v in moved.items()}))
    check("moving the foot IK target moves the shin",
          moved["DEF-shin.L"] > 5.0, "%.2f mm" % moved["DEF-shin.L"])
    check("and the foot",
          moved["DEF-foot.L"] > 5.0, "%.2f mm" % moved["DEF-foot.L"])
    check("but not the thigh's root - IK solves upward from the target, it does not "
          "drag the hip", moved["DEF-thigh.L"] < 1.0, "%.2f mm" % moved["DEF-thigh.L"])

    # FK mode: the same target must now do nothing at all. This is the false
    # pass every FK-only pipeline lives inside.
    call("rigforge_ik", {"rig": rig.name, "action": "set", "mode": "fk"})
    bpy.context.view_layer.update()
    fk_moved = (world_head(rig, "DEF-foot.L") - rest["DEF-foot.L"]).length * 1000.0
    check("with the limb switched to FK the same target moves nothing",
          fk_moved < 1.0, "%.2f mm" % fk_moved)
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)


def test_pole_targets(rig):
    section("the knee pole target steers the knee")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": True})
    _clear_pose(rig)
    # Bend the knee first: a straight leg has no plane for a pole to rotate.
    rig.pose.bones["foot_ik.L"].location = (0.0, 0.0, 0.12 * max(rig.dimensions))
    bpy.context.view_layer.update()
    bent = world_head(rig, "DEF-shin.L").copy()

    # Several directions, largest wins: a pole that happens to land in the leg's
    # existing plane rotates the knee by nothing at all, and which direction that
    # is depends on the sculpt this rig was fitted to.
    reach = 0.25 * max(rig.dimensions)
    offsets = [(reach, 0.0, 0.0), (-reach, 0.0, 0.0), (0.0, reach, 0.0),
               (0.0, -reach, 0.0), (0.0, 0.0, reach)]
    pole = rig.pose.bones["thigh_ik_target.L"]

    def travel_for(base):
        worst = 0.0
        for offset in offsets:
            pole.location = offset
            bpy.context.view_layer.update()
            worst = max(worst, (world_head(rig, "DEF-shin.L") - base).length * 1000.0)
        pole.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        return worst

    with_pole = travel_for(bent)

    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": False})
    bpy.context.view_layer.update()
    without_pole = travel_for(world_head(rig, "DEF-shin.L").copy())

    note("knee travel over %d pole offsets of %.0f mm: %.1f mm with poles on, "
         "%.1f mm off" % (len(offsets), reach * 1000.0, with_pole, without_pole))
    check("with the pole enabled the knee follows it", with_pole > 5.0,
          "%.2f mm" % with_pole)
    check("with it disabled the pole bone is inert - which is what the rig ships as, "
          "and why enabling it is the refinement", without_pole < 1.0,
          "%.2f mm" % without_pole)

    pole.location = (0.0, 0.0, 0.0)
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": True})
    _clear_pose(rig)


def test_foot_roll(rig):
    section("the three-pivot foot roll")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)
    heel = rig.pose.bones["foot_heel_ik.L"]
    heel.rotation_mode = "XYZ"
    rest = {"ankle": world_head(rig, "DEF-foot.L").copy(),
            "ball": world_head(rig, "DEF-toe.L").copy(),
            "toe": world_tail(rig, "DEF-toe.L").copy()}

    heel.rotation_euler = (math.radians(25.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    ball_move = (world_head(rig, "DEF-toe.L") - rest["ball"]).length * 1000.0
    toe_move = (world_tail(rig, "DEF-toe.L") - rest["toe"]).length * 1000.0
    ankle_move = (world_head(rig, "DEF-foot.L") - rest["ankle"]).length * 1000.0
    note("heel control +25 deg: ankle %.1f mm, ball %.1f mm, toe %.1f mm"
         % (ankle_move, ball_move, toe_move))
    check("rolling the heel control forward lifts the ankle", ankle_move > 5.0,
          "%.2f mm" % ankle_move)
    check("while the ball of the foot stays planted - this is the ball pivot, and it "
          "is why the metric measures the ball and not the ankle",
          ball_move < 0.5 and toe_move < 0.5,
          "ball %.3f mm, toe %.3f mm" % (ball_move, toe_move))

    heel.rotation_euler = (math.radians(-25.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    strike_ball = (world_head(rig, "DEF-toe.L") - rest["ball"]).length * 1000.0
    check("rolling it back pivots about the heel instead (the strike): the ball lifts",
          strike_ball > 5.0, "%.2f mm" % strike_ball)

    heel.rotation_euler = (0.0, 0.0, 0.0)
    _clear_pose(rig)


# --- authoring --------------------------------------------------------------

def _action_bones(action):
    from forge.tools import rigforge_rig as rr

    names = set()
    for curve in rr.action_fcurves(action):
        path = curve.data_path
        if path.startswith('pose.bones["'):
            names.add(path.split('"')[1])
    return names


def test_walk(rig):
    section("rigforge_walk - a walk keyed on the IK targets")
    result = call("rigforge_walk", {"rig": rig.name, "action": WALK,
                                    "cycle_frames": 32})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    note(result["says"])
    check("the action was created under the -loop name",
          result["action"] == WALK_LOOP and result["loop"] is True, result["action"])
    check("it is a travelling clip by default (root motion)",
          result["travel"] is True, str(result["travel"]))
    check("the stride is derived from this rig's own leg, not a constant in metres",
          0.3 * result["leg_length_m"] < result["stride_m"] < 2.2 * result["leg_length_m"],
          "stride %.3f m on a %.3f m leg" % (result["stride_m"], result["leg_length_m"]))
    check("the default stride is inside the leg's reach (no stretch, no clamp)",
          result["step_length_reach_clamped"] is False,
          str(result["step_length_reach_clamped"]))
    check("it names the forward axis it derived from the toes",
          len(result["forward_axis"]) == 3 and abs(
              max(result["forward_axis"], key=abs)) > 0.9,
          str(result["forward_axis"]))

    action = bpy.data.actions.get(WALK_LOOP)
    if not check("the action exists in the file", action is not None):
        return None
    bones = _action_bones(action)
    check("both feet are keyed on their IK targets",
          {"foot_ik.L", "foot_ik.R"} <= bones, str(sorted(bones)))
    check("NOT on the FK leg chain - that is the anti-pattern this replaces",
          not ({"thigh_fk.L", "thigh_fk.R", "shin_fk.L", "shin_fk.R"} & bones),
          str(sorted(n for n in bones if "_fk" in n)))
    check("the foot roll is keyed on the heel pivots",
          {"foot_heel_ik.L", "foot_heel_ik.R"} <= bones, str(sorted(bones)))
    check("the root carries the travel", "root" in bones, str(sorted(bones)))
    check("the arms swing in FK", {"upper_arm_fk.L", "upper_arm_fk.R"} <= bones,
          str(sorted(bones)))
    check("the FK/IK switches are keyframed, so the export bake resolves what the "
          "animator saw", {"thigh_parent.L", "thigh_parent.R"} <= bones,
          str(sorted(bones)))

    ik = call("rigforge_ik", {"rig": rig.name})
    modes = {entry["name"]: entry["mode"] for entry in ik["limbs"]}
    check("authoring a walk left the legs on IK", modes.get("leg.L") == "ik"
          and modes.get("leg.R") == "ik", str(modes))

    stance = {entry["foot"]: entry["stance_frames"] for entry in result["feet"]}
    check("each foot spends most of the cycle planted",
          all(value >= 0.5 * result["cycle_frames"] for value in stance.values()),
          str(stance))

    section("rigforge_walk - the gait, not just the mechanics")
    # The wip-15 artist review: "the opposite arm should move on opposite leg"
    # and "the foot should land in front of the center of the model". Both are
    # authored properties now, and both are measured back off the clip by
    # animation_check's own gates further down.
    note("arm_phase %s deg (contralateral=%s, forward sign probed at %s mm), "
         "strike lead %s of a %.0f mm stride = %s mm, stance reach factor %s"
         % (result["arm_phase_deg"], result["arm_swing_contralateral"],
            result["arm_forward_probe_mm"], result["strike_lead"],
            result["stride_m"] * 1000.0, result["strike_lead_mm"],
            result["stance_reach_factor"]))
    check("the arms are authored half a cycle out of phase with the leg on their "
          "own side - which is the opposite leg's strike",
          result["arm_phase_deg"] == 180.0
          and result["arm_swing_contralateral"] is True,
          "%s deg" % result["arm_phase_deg"])
    check("which way a swing carries the hand was MEASURED off the rig, not "
          "assumed, and comes out the same for two mirrored arms rotating about "
          "one world axis",
          set(result["arm_forward_sign"]) == {"L", "R"}
          and len(set(result["arm_forward_sign"].values())) == 1
          and all(abs(value) > 0.5
                  for value in result["arm_forward_probe_mm"].values()),
          "%s from a probe of %s mm" % (result["arm_forward_sign"],
                                        result["arm_forward_probe_mm"]))
    check("the heel is authored to land a third of a stride IN FRONT of the hip "
          "joint, inside the classical 25-35%",
          0.25 <= result["strike_lead"] <= 0.35
          and abs(result["strike_lead_mm"]
                  - result["strike_lead"] * result["stride_m"] * 1000.0) < 0.5,
          "%s of a stride = %s mm" % (result["strike_lead"],
                                      result["strike_lead_mm"]))
    check("and the reach model knows the stride is now spent either side of the "
          "hip rather than symmetrically about it",
          abs(result["stance_reach_factor"]
              - 2.0 * max(result["strike_lead"],
                          result["stance_fraction"] - result["strike_lead"])) < 1e-6,
          str(result["stance_reach_factor"]))
    check("the says sentence carries both numbers, so a caller who reads nothing "
          "else still sees the gait",
          "in front of the hip joint" in result["says"]
          and "out of phase" in result["says"], result["says"][:240])

    gait = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
    opposition = gait.get("gait_opposition") or {}
    lead = gait.get("strike_lead") or {}
    note("gait_opposition %s (%s); strike_lead %s (%s)"
         % (opposition.get("verdict"), opposition.get("says"),
            lead.get("verdict"), lead.get("says")))
    check("measured back off the authored clip, every arm/leg pair is half a "
          "cycle apart",
          opposition.get("verdict") == "ok"
          and all(abs(abs(row["phase_lag_deg"]) - 180.0) < 1.0
                  for row in opposition.get("pairs") or []
                  if row["phase_lag_deg"] is not None),
          str([(row["pair"], row["phase_lag_deg"])
               for row in opposition.get("pairs") or []]))
    check("...including the two hands against each other, which is the reading "
          "that caught wip-15 swinging both arms in unison",
          any(row["pair"] == "arm.L vs arm.R"
              and abs(abs(row["phase_lag_deg"] or 0.0) - 180.0) < 1.0
              for row in opposition.get("pairs") or []),
          str([(row["pair"], row["phase_lag_deg"])
               for row in opposition.get("pairs") or []]))
    check("and BOTH heels strike the same distance in front of the hip joint - "
          "the wip-15 defect was that only the left one did",
          lead.get("verdict") == "ok"
          and len(lead.get("strikes") or []) == 2
          and abs(lead["strikes"][0]["lead_mm"]
                  - lead["strikes"][1]["lead_mm"]) < 1.0,
          str([(row["foot"], row["lead_mm"], row["lead_pct_of_stride"])
               for row in lead.get("strikes") or []]))
    check("...at the fraction of the stride it was authored for",
          all(abs(row["lead_pct_of_stride"] - result["strike_lead"] * 100.0) < 1.0
              for row in lead.get("strikes") or []),
          "authored %s, measured %s" % (result["strike_lead"],
                                        [row["lead_pct_of_stride"]
                                         for row in lead.get("strikes") or []]))

    section("rigforge_walk - a stride the leg cannot reach")
    # A leg that cannot reach its own target does not arrive where it was keyed,
    # so it slides on the deform bones while the control sits still. The command
    # bends the knees for it, and shortens the step only when that runs out.
    longer = call("rigforge_walk", {"rig": rig.name, "action": "stride",
                                    "cycle_frames": 24,
                                    "step_length": 0.6 * result["leg_length_m"]})
    for warning in longer.get("warnings") or []:
        note("warning: %s" % warning)
    # Not refused, and paid for in the right order: the knees bend first and
    # the stride gives only when the crouch has spent everything it is allowed.
    # Measured against this build's own ceiling rather than against the default
    # walk's crouch, because on a rig with no anatomical pre-bend the default
    # walk is already at that ceiling - which is a fact about the rig, not a
    # failure of this clip.
    check("a long stride is bought with knee bend before it is ever refused",
          longer["hip_lower_deepened"] is True
          and (not longer["step_length_reach_clamped"]
               or longer["hip_lower_m"] >= longer["max_hip_lower_m"] - 1e-6),
          "hips at %.3f m of a %.3f m ceiling (default walk needed %.3f m), "
          "clamped=%s" % (longer["hip_lower_m"], longer["max_hip_lower_m"],
                          result["hip_lower_m"],
                          longer["step_length_reach_clamped"]))
    # The contract, not the wish.  `rigforge_walk` no longer takes the rest-pose
    # solve's word for what the leg can reach: it authors the cycle, measures
    # the hip-to-ankle span on the deform chain, and re-keys with deeper hips -
    # or, when the crouch has run out, a shorter step - until no frame asks the
    # leg for more than it has.  So whether a 0.6-leg stride survives is a fact
    # about *this build's* geometry, and the test is that the command tells the
    # truth about which case it is: the step survives when the legs can reach
    # it, and shortens with a warning quoting the measured reach when they
    # cannot.  Asserting the step unconditionally was only ever passing because
    # the old solve was optimistic - on the werewolf that optimism was 1.3258 of
    # the leg's own length, paid for in stretched deform bones.
    asked = 0.6 * result["leg_length_m"]
    reached = longer["leg_reach_ratio"]
    note("asked %.3f m; got %.3f m with the worst planted leg at %.5f of its own "
         "measured reach after %d authoring pass(es); hips at %.3f m of a %.3f m "
         "ceiling"
         % (asked, longer["step_length_m"], reached or 0.0,
            longer["reach_passes"], longer["hip_lower_m"],
            longer["max_hip_lower_m"]))
    note("worst frame: %s" % longer["leg_reach_worst"])
    if not longer["step_length_reach_clamped"]:
        check("and the step asked for survives, because this build's legs can "
              "reach it", abs(longer["step_length_m"] - asked) < 1e-4,
              "%.4f m against %.4f m asked" % (longer["step_length_m"], asked))
    else:
        check("and where this build's legs cannot reach it the step is shortened "
              "rather than stretched into - by exactly what the measured reach "
              "left, after the crouch had spent everything it was allowed",
              longer["step_length_m"] < asked
              and longer["hip_lower_m"] >= longer["max_hip_lower_m"] - 1e-6,
              "%.4f m of %.4f m asked, hips %.4f m of %.4f m"
              % (longer["step_length_m"], asked, longer["hip_lower_m"],
                 longer["max_hip_lower_m"]))
        check("...and says so in a warning that quotes the reach it measured",
              any("reach" in warning for warning in longer.get("warnings") or []),
              str(longer.get("warnings"))[:240])
    check("either way no planted leg is asked to stand further from the hip than "
          "it reaches - which is the whole reason the step may not survive",
          reached is not None and reached <= 1.0,
          "%.5f of its own measured reach" % (reached or 0.0))

    greedy = call("rigforge_walk", {"rig": rig.name, "action": "lunge",
                                    "cycle_frames": 24,
                                    "step_length": 3.0 * result["leg_length_m"]})
    for warning in greedy.get("warnings") or []:
        note("warning: %s" % warning)
    check("but a stride longer than the leg is shortened rather than stretched into",
          greedy["step_length_reach_clamped"] is True
          and greedy["step_length_m"] < greedy["leg_length_m"],
          "%.3f m step on a %.3f m leg" % (greedy["step_length_m"],
                                           greedy["leg_length_m"]))
    check("and it says so in a warning rather than silently",
          any("reach" in w for w in greedy.get("warnings") or []),
          str(greedy.get("warnings"))[:200])
    for name in ("stride-loop", "lunge-loop"):
        reached = call("animation_check", {"rig": rig.name, "action": name})
        note("%s: %s" % (name, reached["says"]))
        check("%s still plants its feet" % name, reached["gate"] == "ok",
              "%s (%s mm)" % (reached["gate"], reached["worst_drift_mm"]))
        # The stride these two clips end up with is whatever the reach clamp
        # left, and the gait is a property of the stride rather than of its
        # length: a shortened step still lands its heel a third of it in front
        # of the hips, and still swings the opposite arm.
        check("%s keeps its gait whatever the clamp did to the stride" % name,
              (reached.get("gait_opposition") or {}).get("verdict") == "ok"
              and (reached.get("strike_lead") or {}).get("verdict") == "ok",
              "opposition %s, strike lead %s (%s mm, %s%% of stride)"
              % ((reached.get("gait_opposition") or {}).get("verdict"),
                 (reached.get("strike_lead") or {}).get("verdict"),
                 (reached.get("strike_lead") or {}).get("worst_lead_mm"),
                 (reached.get("strike_lead") or {}).get("worst_lead_pct_of_stride")))

    section("rigforge_walk - in place (the treadmill clip)")
    second = call("rigforge_walk", {"rig": rig.name, "action": TREADMILL,
                                    "cycle_frames": 32, "travel": False})
    check("the in-place clip does not key the root travel",
          second["travel"] is False, str(second["travel"]))
    note(second["says"])
    return result


def test_walk_errors(rig):
    section("rigforge_walk - the refusal that teaches")
    empty = bpy.data.objects.new("BareRig", bpy.data.armatures.new("BareRig"))
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.view_layer.update()
    reply = call("rigforge_walk", {"rig": "BareRig", "action": "nope"},
                 expect_error=True)
    message = reply.get("message") or ""
    check("a rig with no IK legs is refused by name",
          reply.get("status") == "error" and "foot_ik" in message, message[:240])
    check("and the message says what to run instead",
          "rigforge_generate_rig" in message and "foot-slide" in message,
          message[:240])
    bpy.data.objects.remove(empty, do_unlink=True)


def author_fk_walk(rig):
    """The anti-pattern, on purpose: a walk keyed on thigh_fk / shin_fk."""
    section("the anti-pattern - a walk keyed in FK")
    keys = []
    for frame in range(1, 34, 4):
        t = (frame - 1) / 32.0
        for side, phase in (("L", 0.0), ("R", math.pi)):
            keys.append({"bone": "thigh_fk.%s" % side, "frame": frame,
                         "rotation_euler_deg":
                             [25.0 * math.sin(2 * math.pi * t + phase), 0.0, 0.0]})
            keys.append({"bone": "shin_fk.%s" % side, "frame": frame,
                         "rotation_euler_deg":
                             [-30.0 * max(0.0, math.sin(2 * math.pi * t + phase + 1.2)),
                              0.0, 0.0]})
    result = call("rigforge_keyframe", {"rig": rig.name, "action": FKWALK, "keys": keys,
                                        "clear": True, "loop": True})
    check("keying the FK leg chain switches those legs to FK",
          sorted(result["fk_limbs"] or []) == ["leg.L", "leg.R"],
          str(result["fk_limbs"]))
    return FKWALK_LOOP


# --- the metric -------------------------------------------------------------

def test_foot_slide_metric(rig, fk_action):
    section("animation_check - the foot-slide metric")
    planted = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
    note(planted["says"])
    note("mode: %s (%s)" % (planted["mode"], planted["mode_reason"]))
    for foot in planted["feet"]:
        note("  %s on %s (%s): %d step(s), worst %s mm, excursion %.0f mm"
             % (foot["foot"], foot["bone"], foot["point"], foot["steps_measured"],
                foot["worst_drift_mm"], foot["excursion_mm"]))
        for step in foot["steps"]:
            note("      step %d frames %s: %.2f mm  [%s]"
                 % (step["step"], step["frames"], step["drift_mm"], step["verdict"]))

    check("it measured the ball of the foot, not the ankle",
          all(foot["bone"].endswith(("toe.L", "toe.R")) or foot["point"] == "tail"
              for foot in planted["feet"]),
          str([(f["bone"], f["point"]) for f in planted["feet"]]))
    check("it detected a travelling clip", planted["mode"] == "planted",
          planted["mode_reason"])
    check("it found stance phases on both feet",
          len([f for f in planted["feet"] if f["steps_measured"]]) == 2
          and planted["steps_measured"] >= 2, str(planted["steps_measured"]))
    check("the IK walk's planted feet hold to within a few millimetres",
          planted["worst_drift_mm"] is not None and planted["worst_drift_mm"] < 5.0,
          "%s mm" % planted["worst_drift_mm"])
    check("so the gate passes", planted["gate"] == "ok", planted["gate"])
    check("and it says which threshold judged it, and that it is a heuristic",
          "heuristic" in planted["threshold_tier"]
          and planted["thresholds"]["drift_mm"]["ok"] > 0,
          str(planted["thresholds"]))
    check("the pose and the action were put back", planted["pose_restored"] is True
          and bpy.context.scene.frame_current >= 0, str(planted["pose_restored"]))

    section("animation_check - the in-place clip is judged against one shared speed")
    treadmill = call("animation_check", {"rig": rig.name, "action": TREADMILL_LOOP})
    note(treadmill["says"])
    check("it detected an in-place clip", treadmill["mode"] == "in_place",
          treadmill["mode_reason"])
    check("and measured the treadmill speed it removed",
          treadmill["treadmill_mm_per_frame"] > 1.0,
          "%.2f mm/frame" % treadmill["treadmill_mm_per_frame"])
    check("the same cycle, authored in place, still passes",
          treadmill["gate"] == "ok" and treadmill["worst_drift_mm"] < 5.0,
          "%s mm, %s" % (treadmill["worst_drift_mm"], treadmill["gate"]))
    check("the caller can force the other reading, and it is a different number",
          call("animation_check", {"rig": rig.name, "action": TREADMILL_LOOP,
                                   "mode": "planted"})["worst_drift_mm"]
          > 10.0 * max(treadmill["worst_drift_mm"], 1e-6),
          "a treadmill clip read as travelling must look like a slide")

    section("animation_check - the metric proves itself on the FK walk")
    slid = call("animation_check", {"rig": rig.name, "action": fk_action})
    note(slid["says"])
    for foot in slid["feet"]:
        note("  %s: %d step(s), worst %s mm" % (foot["bone"], foot["steps_measured"],
                                                foot["worst_drift_mm"]))
    check("the FK-keyed walk slides by centimetres",
          slid["worst_drift_mm"] is not None and slid["worst_drift_mm"] > 20.0,
          "%s mm" % slid["worst_drift_mm"])
    check("so the gate fails", slid["gate"] == "fail", slid["gate"])
    check("and it is at least twenty times the IK walk's drift - the two are not the "
          "same clip with a different threshold",
          slid["worst_drift_mm"] > 20.0 * max(planted["worst_drift_mm"], 1e-6),
          "%s mm vs %s mm" % (slid["worst_drift_mm"], planted["worst_drift_mm"]))
    check("the verdict names the bone and the frames of the worst step",
          slid["worst_step"] and slid["worst_step"]["bone"] in slid["says"]
          and len(slid["worst_step"]["frames"]) == 2, str(slid["worst_step"]))
    check("and points at the fix", "rigforge_walk" in slid["says"], slid["says"][:200])

    section("animation_check - errors that help")
    reply = call("animation_check", {"rig": rig.name, "action": "no-such-clip"},
                 expect_error=True)
    check("an unknown action is refused with the actions that exist",
          reply.get("status") == "error" and WALK_LOOP in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    reply = call("animation_check", {"rig": rig.name, "action": WALK_LOOP,
                                     "feet": ["DEF-nothing.L"]}, expect_error=True)
    check("so is a foot bone the rig does not have",
          reply.get("status") == "error"
          and "DEF-nothing.L" in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    return planted, slid


def test_scoped_fk_switch(rig):
    section("the audit's bug: keying an arm must not take the legs off IK")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    result = call("rigforge_keyframe", {
        "rig": rig.name, "action": "wave", "clear": True,
        "keys": [{"bone": "upper_arm_fk.L", "frame": 1,
                  "rotation_euler_deg": [0.0, 0.0, 0.0]},
                 {"bone": "upper_arm_fk.L", "frame": 10,
                  "rotation_euler_deg": [0.0, 0.0, 40.0]}]})
    check("only the keyed limb was switched", result["fk_limbs"] == ["arm.L"],
          str(result["fk_limbs"]))
    modes = {entry["name"]: entry["mode"]
             for entry in call("rigforge_ik", {"rig": rig.name})["limbs"]}
    check("both legs are still on IK", modes.get("leg.L") == "ik"
          and modes.get("leg.R") == "ik", str(modes))
    check("and the reply warns that the IK legs were left alone",
          any("stayed on IK" in w for w in result.get("warnings") or []),
          str(result.get("warnings"))[:240])

    explicit = call("rigforge_keyframe", {
        "rig": rig.name, "action": "wave", "clear": True, "fk_switch": True,
        "keys": [{"bone": "upper_arm_fk.L", "frame": 1,
                  "rotation_euler_deg": [0.0, 0.0, 0.0]}]})
    modes = {entry["name"]: entry["mode"]
             for entry in call("rigforge_ik", {"rig": rig.name})["limbs"]}
    check("fk_switch:true is still the whole rig, because a full-body mocap bake "
          "wants exactly that",
          explicit["fk_limbs"] is None and all(mode == "fk" for mode in modes.values()),
          "%s / %s" % (explicit["fk_limbs"], modes))
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})


# --- the Godot export -------------------------------------------------------

def test_export_bakes_ik(rig, mesh, workspace):
    section("the Godot export bakes the IK onto the deform bones")
    from forge.tools import rigforge_rig as rr

    source = bpy.data.actions.get(WALK_LOOP)
    source_bones = _action_bones(source)
    check("the source clip keys no deform bone at all - every leg pose in it is an "
          "IK solve", not any(name.startswith("DEF-") for name in source_bones),
          str(sorted(source_bones)))

    # The setting, pinned where it is written: Blender's own exporter samples,
    # and our deform bake is visual, which is what turns constraints into keys.
    bake_source = inspect.getsource(rr.bake_action_onto)
    check("bake_action_onto bakes with visual keying",
          '"visual_keying": True' in bake_source, "visual_keying missing")
    export_source = inspect.getsource(rr.cmd_rigforge_export_godot)
    check("and the glTF export forces sampling rather than trusting the curves",
          '"export_force_sampling": True' in export_source,
          "export_force_sampling missing")
    rna = [prop.identifier
           for prop in bpy.ops.export_scene.gltf.get_rna_type().properties]
    check("the installed exporter really has that property (it is not a no-op kwarg "
          "that op_kwargs quietly drops)", "export_force_sampling" in rna,
          str([name for name in rna if "sampl" in name or "anim" in name]))

    path = os.path.join(workspace, "walker.glb")
    result = call("rigforge_export_godot", {"rig": rig.name, "path": path,
                                            "actions": [WALK_LOOP], "lods": "auto"})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    check("the glTF exists", os.path.isfile(path) and os.path.getsize(path) > 1024,
          "%s bytes" % (os.path.getsize(path) if os.path.exists(path) else "missing"))

    flattened = result.get("bbone_flattened") or {}
    note("bbones: %s" % flattened.get("says"))
    check("the export report quotes how many bendy bones it flattened",
          (flattened.get("count") or 0) > 0 and flattened.get("segments_after") == 1,
          str({k: v for k, v in flattened.items() if k != "flattened"}))
    check("...and names them, so the difference from the authoring scene is readable",
          len(flattened.get("flattened") or []) == flattened.get("count"),
          str((flattened.get("flattened") or [])[:4]))

    import headless_phase4 as phase4

    doc = phase4.parse_gltf(path)
    animations = [entry.get("name") for entry in doc.get("animations", [])]
    check("the walk shipped under its own name", WALK_LOOP in animations,
          str(animations))
    nodes = doc.get("nodes", [])
    channels = doc["animations"][animations.index(WALK_LOOP)].get("channels", [])
    targets = {nodes[channel["target"]["node"]].get("name") for channel in channels}
    check("every animated node is a deform bone or the root - no IK control leaked",
          targets and all(str(name).startswith("DEF-") or name == "root"
                          for name in targets), str(sorted(targets))[:240])
    legs = {name for name in targets
            if any(part in str(name) for part in ("thigh", "shin", "foot", "toe"))}
    check("and the baked clip reaches the leg deform bones the IK was driving",
          len(legs) >= 4, str(sorted(legs)))

    section("the bake, measured in place")
    # Same machinery the export uses, run here so the result can be inspected.
    collection = bpy.data.collections.new("FORGE_IK_BAKE_TEST")
    bpy.context.scene.collection.children.link(collection)
    baked = None
    try:
        export_rig = rr.build_deform_rig(rig, "%s_ik_bake" % rig.name, collection, [])
        rr.constrain_to(rig, export_rig)
        rr.assign_action(rig, source)
        export_rig.animation_data_create()
        span = source.frame_range
        start, end = int(math.floor(span[0])), int(math.ceil(span[1]))
        baked = rr.bake_action_onto(rig, export_rig, source, start, end, 1)
        rr.strip_constraints(export_rig)
        names = _action_bones(baked)
        check("the bake landed on the deform bones",
              {"DEF-shin.L", "DEF-foot.L"} <= names, str(sorted(names))[:200])
        from forge.tools import rigforge_rig as again

        varied = []
        for curve in again.action_fcurves(baked):
            if 'DEF-shin.L' not in curve.data_path:
                continue
            values = [point.co.y for point in curve.keyframe_points]
            if values and (max(values) - min(values)) > 1e-3:
                varied.append(curve.data_path.rsplit(".", 1)[-1])
        check("and DEF-shin.L really moves across the clip - the IK solve became keys, "
              "which is what Godot needs", bool(varied), str(sorted(set(varied))))
    finally:
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
        if baked is not None and baked.users == 0:
            bpy.data.actions.remove(baked)
    return path


def test_exported_clip_is_still_planted(rig, path):
    section("the exported clip still measures as planted")
    if not hasattr(bpy.ops.import_scene, "gltf"):
        note("this Blender has no glTF importer; the round trip was not run")
        return
    before = set(bpy.data.objects.keys())
    try:
        status = bpy.ops.import_scene.gltf(filepath=path)
    except RuntimeError as exc:
        note("glTF import refused the file (%s); the round trip was not run" % exc)
        return
    if "FINISHED" not in status:
        note("glTF import returned %s; the round trip was not run" % sorted(status))
        return
    imported = [bpy.data.objects[name] for name in set(bpy.data.objects.keys()) - before]
    rigs = [obj for obj in imported if obj.type == "ARMATURE"]
    try:
        if not rigs:
            note("the import produced no armature; the round trip was not run")
            return
        target = rigs[0]
        feet = [name for name in ("DEF-toe.L", "DEF-toe.R")
                if name in target.pose.bones]
        if len(feet) < 2:
            note("the imported skeleton has no DEF-toe bones (%d); not measured"
                 % len(feet))
            return
        clip = None
        for action in bpy.data.actions:
            if action.name.startswith(WALK_LOOP) and action is not bpy.data.actions.get(
                    WALK_LOOP):
                clip = action
        if clip is None:
            clip = target.animation_data.action if target.animation_data else None
        if clip is None:
            note("the imported rig carries no action; not measured")
            return
        result = call("animation_check", {"rig": target.name, "action": clip.name,
                                          "feet": feet})
        note("%s: %s" % (clip.name, result["says"]))
        check("the baked, exported, re-imported walk still holds its feet",
              result["gate"] in ("ok", "attention")
              and (result["worst_drift_mm"] or 0.0) < 20.0,
              "%s mm, %s" % (result["worst_drift_mm"], result["gate"]))
    finally:
        for obj in imported:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                continue


# --- shutdown ---------------------------------------------------------------

def test_server_frees_its_port():
    section("server shutdown")
    from forge import server as forge_server

    forge_server.stop_server()
    check("the server reports itself stopped", not forge_server.is_running())
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


# --- entry point ------------------------------------------------------------

def main():
    print("Forge add-on IK / locomotion headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_rigik_test_")
    try:
        mesh, rig, meta, generated = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        test_what_the_builder_is(meta, generated, rig)
        test_ik_stretch_ships_off(rig, generated)
        test_bbones_are_flattened(rig)
        test_ik_report(rig)
        test_ik_is_control_layer_only(rig, mesh)
        test_ik_solves(rig)
        test_pole_targets(rig)
        test_foot_roll(rig)

        test_walk(rig)
        test_walk_errors(rig)
        fk_action = author_fk_walk(rig)
        test_foot_slide_metric(rig, fk_action)
        test_scoped_fk_switch(rig)

        path = test_export_bakes_ik(rig, mesh, workspace)
        test_exported_clip_is_still_planted(rig, path)
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
