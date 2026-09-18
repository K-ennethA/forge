"""Headless add-on tests for the rigging bridge (thesis build #2).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_rigbridge.py

Two things are under test, and neither needs a GPU:

* **the joints bridge** — ``rigforge_metarig``'s third landmark source.  The
  detector's output is a *file*, so the suite writes its own: a hand-written
  ``forge.joints/1`` document derived from the tag-only fit with known offsets
  baked in.  That is the whole point of the file handoff — the blending rules
  are testable to the millimetre with no model, no CUDA and no download.  (The
  real UniRig smoke test runs once, by hand, outside this harness; see
  ``C:\\forge-models\\unirig\\FORGE-NOTES.md``.)
* **the deformation harness** — ``rig_check`` on the generated Rigify rig: does
  it produce per-joint numbers, does it prove the control actually drove the
  flesh, and does it put the pose back.

The synthetic tagged biped is imported from the Phase 3/4 suites rather than
copied, so the three files cannot drift apart.
"""

import json
import os
import shutil
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bpy
from mathutils import Vector

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9901  # not 9876 (a live session), 9878/9879/9880 (phases 2/3/4)
SCULPT = "Sculpt"
RETOPO = SCULPT + "_retopo"

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


# --- the tagged biped, from the Phase 4 suite --------------------------------

def build_and_tag():
    import headless_phase4 as phase4

    obj, regions = phase4.build_tagged_biped()
    for name, faces in sorted(regions.items()):
        call("rigforge_tag", {"object": obj.name, "tag": name, "faces": faces,
                              "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get", "archetype": "biped",
                               "motion_notes": "ears are floppy and lag behind the head"})
    call("rigforge_retopo", {"object": obj.name, "target_faces": 5000,
                             "platform": "mobile", "lods": 0})
    retopo = bpy.data.objects.get(RETOPO)
    return obj, retopo


def bone_points(meta):
    """``{bone: (world head, world tail)}`` for every bone of a metarig."""
    matrix = meta.matrix_world
    return {bone.name: (matrix @ bone.head_local.copy(), matrix @ bone.tail_local.copy())
            for bone in meta.data.bones}


def write_joints(path, mesh, entries, axis_up="Z", unit="mm", source="test"):
    """A ``forge.joints/1`` file from world-space points (the detector's stand-in)."""
    inverse = mesh.matrix_world.inverted()
    scale = 1000.0 if unit == "mm" else 1.0
    joints = []
    for index, (name, world_point) in enumerate(entries):
        local = inverse @ Vector(world_point)
        if axis_up == "Y":  # write it the way a glTF-frame producer would
            local = Vector((local.x, local.z, -local.y))
        joints.append({
            "index": index,
            "name": name,
            "head_mm": [local.x * scale, local.y * scale, local.z * scale],
            "tail_mm": None,
            "parent": None,
            "confidence": None,
        })
    document = {
        "schema": "forge.joints/1",
        "source": source,
        "detector": {"name": "hand-written", "commit": "n/a", "weights": "n/a",
                     "seconds": 0.0, "vram_peak_mb": 0, "device": "cpu"},
        "mesh": {"file": path, "faces": len(mesh.data.polygons),
                 "vertices": len(mesh.data.vertices)},
        "frame": {"unit": unit, "space": "mesh_local", "axis_up": axis_up},
        "joints": joints,
        "notes": ["written by headless_rigbridge.py: a detector stand-in"],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
    return document


# --- tests: the joints bridge -----------------------------------------------

def test_tags_only(retopo):
    section("the tag-only fit (the baseline every comparison is against)")
    # ``method: "tags"`` explicitly: predictions refine the *tag* fit, so the
    # baseline every comparison below is measured against has to be the same
    # fit. (A `joints_file` on its own selects this path too, and the next test
    # checks that it says so.)
    result = call("rigforge_metarig", {"object": retopo.name, "archetype": "auto",
                                       "method": "tags"})
    meta = bpy.data.objects.get(result["metarig"])
    check("the tag-only metarig exists", meta is not None, str(result.get("metarig")))
    check("it is the tag fit", result.get("fit_method") == "tags",
          str(result.get("fit_method")))
    check("it reports no joints file", result.get("joints") is None,
          str(result.get("joints")))
    check("it fitted the usual bones", len(result.get("fitted_bones") or []) >= 10,
          str(result.get("fitted_bones")))
    return meta, bone_points(meta)


def test_joints_refine(retopo, baseline, workspace):
    """The contract: predictions inside tolerance move the fit measurably."""
    section("joints_file - predictions refine the tag fit")
    span = max(retopo.dimensions)
    near = span * 0.025          # comfortably inside the 12% tolerance
    far = span * 0.10            # outside the elbow's own territory, inside the band
    offset = Vector((0.0, near, 0.0))

    # Every prediction is the tag-only fit's own landmark, moved a known amount:
    # the detector "sees" the same skeleton, slightly differently.
    wanted = [
        (None, baseline["spine"][0] + offset),                    # hips
        (None, baseline["spine.001"][0] + offset),                # spine_01
        (None, baseline["spine.003"][0] + offset),                # spine_03
        (None, baseline["spine.004"][0] + offset),                # neck base
        (None, baseline["spine.006"][1] + offset),                # head top
    ]
    for side in ("L", "R"):
        wanted.extend([
            (None, baseline["shoulder.%s" % side][0] + offset),   # clavicle root
            (None, baseline["upper_arm.%s" % side][0] + offset),  # shoulder
            (None, baseline["forearm.%s" % side][1] + offset),    # wrist
            (None, baseline["thigh.%s" % side][0] + offset),      # hip
            (None, baseline["shin.%s" % side][1] + offset),       # ankle
        ])
    # ... and one joint the detector gets badly wrong, on purpose.
    disagreement = baseline["forearm.L"][0] + Vector((0.0, far, 0.0))
    wanted.append((None, disagreement))

    path = os.path.join(workspace, "joints.json")
    write_joints(path, retopo, wanted)
    note("wrote %d predictions, offset %.1f mm (one at %.1f mm)"
         % (len(wanted), near * 1000.0, far * 1000.0))

    result = call("rigforge_metarig", {"object": retopo.name, "archetype": "auto",
                                       "joints_file": path})
    report = result.get("joints") or {}
    check("a joints file selects the tag fit, and the result says which fit ran",
          result.get("fit_method") == "tags", str(result.get("fit_method")))
    check("and the warnings explain why, rather than ignoring the file silently",
          any("joints file" in w and "tag fit" in w
              for w in result.get("warnings") or []),
          str(result.get("warnings"))[:200])
    check("the result carries a joints report", bool(report), str(sorted(report)))
    check("the file was accepted (its frame checks out)", report.get("enabled") is True,
          "%s inside %s" % (report.get("inside_bbox"), report.get("joints")))
    check("every prediction landed inside the mesh box",
          report.get("inside_fraction", 0) >= 0.9, str(report.get("inside_fraction")))
    check("it read all %d joints" % len(wanted), report.get("joints") == len(wanted),
          str(report.get("joints")))
    check("an unnamed file is matched by position, not by name",
          report.get("named_joints") == 0
          and all(entry.get("matched_by") == "position"
                  for entry in report.get("refined") or []),
          str([e.get("matched_by") for e in report.get("refined") or []]))

    meta = bpy.data.objects.get(result["metarig"])
    after = bone_points(meta)

    # --- the measurable shift, on landmarks no later step nudges
    moved_any = False
    for label, bone, end, predicted in (
        ("the hips", "spine", 0, baseline["spine"][0] + offset),
        ("the head top", "spine.006", 1, baseline["spine.006"][1] + offset),
        ("the left wrist", "forearm.L", 1, baseline["forearm.L"][1] + offset),
        ("the right ankle", "shin.R", 1, baseline["shin.R"][1] + offset),
    ):
        was = baseline[bone][end]
        now = after[bone][end]
        before_gap = (predicted - was).length
        after_gap = (predicted - now).length
        shift = (now - was).length
        moved_any = moved_any or shift > 1e-6
        check("%s moved towards the prediction" % label, after_gap < before_gap - 1e-6,
              "%.2f mm -> %.2f mm" % (before_gap * 1000.0, after_gap * 1000.0))
        check("%s moved about half way (weight 0.5), not all the way" % label,
              abs(shift - before_gap * 0.5) <= before_gap * 0.15,
              "moved %.2f mm of %.2f mm" % (shift * 1000.0, before_gap * 1000.0))
    check("something actually moved", moved_any)

    refined = report.get("refined") or []
    check("the report lists the refinements", len(refined) >= 8, str(len(refined)))
    check("each refinement carries both positions and the distance it moved",
          all({"tag_mm", "predicted_mm", "used_mm", "moved_mm"} <= set(entry)
              for entry in refined),
          str(refined[:1]))

    # --- the disagreement
    disagreements = report.get("disagreements") or []
    roles = {entry["role"]: entry for entry in disagreements}
    check("the bad prediction is reported as a disagreement", "elbow.L" in roles,
          str([(d["role"], d["distance_mm"]) for d in disagreements]))
    served = {"hips", "shoulder.L", "shoulder.R", "wrist.L", "wrist.R",
              "hip.L", "hip.R", "ankle.L", "ankle.R"}
    check("a landmark with a prediction of its own is never called a conflict",
          not (served & set(roles)), str(sorted(served & set(roles))))
    if "elbow.L" in roles:
        entry = roles["elbow.L"]
        check("it is beyond tolerance", entry["distance_mm"] > entry["tolerance_mm"],
              "%.1f mm vs %.1f mm" % (entry["distance_mm"], entry["tolerance_mm"]))
        check("it carries both positions so the reader can judge",
              {"tag_mm", "predicted_mm"} <= set(entry), str(sorted(entry)))
    # The tags win the disagreement: the elbow is NOT refined towards the
    # prediction. It is not pinned in place, though, and the reason is worth
    # stating rather than loosening a threshold over: the fit gives every limb
    # an **anatomical pre-bend** relative to its own shoulder-to-wrist chord
    # (rigforge_rig._bend), and the shoulder and the wrist here *were* refined,
    # so the chord moved under it and the pre-bend is re-applied against the new
    # one. That move is a few percent of the arm's own length -- measured,
    # 21.3 mm -- while a refined joint moves half of the 10%-of-span
    # disagreement, which is four times further.
    elbow_shift = (after["forearm.L"][0] - baseline["forearm.L"][0]).length
    towards = ((disagreement - baseline["forearm.L"][0]).length
               - (disagreement - after["forearm.L"][0]).length)
    note("the elbow moved %.1f mm; a refined joint would move %.1f mm (half of the "
         "%.1f mm disagreement)" % (elbow_shift * 1000.0, far * 500.0, far * 1000.0))
    check("the tags won the disagreement: the elbow was not refined towards the "
          "prediction", towards < far * 0.25,
          "%.1f mm closer of %.1f mm" % (towards * 1000.0, far * 1000.0))
    check("...and what it did move is the anatomical pre-bend, not a refinement",
          elbow_shift < far * 0.25,
          "%.1f mm of a %.1f mm half-way move" % (elbow_shift * 1000.0, far * 500.0))

    warnings = " ".join(result.get("warnings") or [])
    check("the warnings say a detector disagreed", "disagreed with the tags" in warnings,
          warnings[:200])
    check("the warnings say what was refined", "were refined by" in warnings,
          warnings[:200])
    return path


def test_joints_weight_extremes(retopo, baseline, workspace):
    section("joints_weight - the blend is a dial, and 0 changes nothing")
    span = max(retopo.dimensions)
    offset = Vector((0.0, span * 0.025, 0.0))
    entries = [(None, baseline["forearm.%s" % side][1] + offset) for side in ("L", "R")]
    path = os.path.join(workspace, "joints_wrist.json")
    write_joints(path, retopo, entries)

    zero = call("rigforge_metarig", {"object": retopo.name, "joints_file": path,
                                     "joints_weight": 0.0})
    meta = bpy.data.objects.get(zero["metarig"])
    after = bone_points(meta)
    check("weight 0 leaves the fit exactly where the tags put it",
          (after["forearm.L"][1] - baseline["forearm.L"][1]).length < 1e-6,
          "%.4f mm" % ((after["forearm.L"][1] - baseline["forearm.L"][1]).length * 1000.0))
    check("but the refinement is still reported, so the dial is visible",
          len((zero.get("joints") or {}).get("refined") or []) >= 2,
          str(len((zero.get("joints") or {}).get("refined") or [])))

    one = call("rigforge_metarig", {"object": retopo.name, "joints_file": path,
                                    "joints_weight": 1.0})
    after = bone_points(bpy.data.objects.get(one["metarig"]))
    target = baseline["forearm.L"][1] + offset
    check("weight 1 hands the landmark to the detector outright",
          (after["forearm.L"][1] - target).length < span * 1e-3,
          "%.2f mm away" % ((after["forearm.L"][1] - target).length * 1000.0))


def test_joints_frame_guard(retopo, baseline, workspace):
    section("the frame guard - a file in the wrong frame is refused, not trusted")
    span = max(retopo.dimensions)
    offset = Vector((0.0, span * 0.02, 0.0))
    entries = [(None, baseline["spine"][0] + offset),
               (None, baseline["spine.006"][1] + offset),
               (None, baseline["forearm.L"][1] + offset),
               (None, baseline["shin.R"][1] + offset)]

    # Same numbers, declared Y-up: every joint lands somewhere else entirely.
    path = os.path.join(workspace, "joints_yup.json")
    write_joints(path, retopo, entries)
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    document["frame"]["axis_up"] = "Y"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)

    result = call("rigforge_metarig", {"object": retopo.name, "joints_file": path})
    report = result.get("joints") or {}
    check("a mis-framed file is disabled, not believed", report.get("enabled") is False,
          str(report.get("inside_fraction")))
    warnings = " ".join(result.get("warnings") or [])
    check("the warning says why", "frame or its units do not match" in warnings,
          warnings[:240])
    check("and it suggests the frame that would have worked",
          "would land inside the mesh if it were read as" in warnings, warnings[:240])
    after = bone_points(bpy.data.objects.get(result["metarig"]))
    check("the fit fell back to tags alone, unchanged",
          (after["spine"][0] - baseline["spine"][0]).length < 1e-6,
          "%.3f mm" % ((after["spine"][0] - baseline["spine"][0]).length * 1000.0))

    # A unit mistake (metres written into a millimetre field) is the same class.
    metres = os.path.join(workspace, "joints_metres.json")
    write_joints(metres, retopo, entries, unit="m")
    with open(metres, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    document["frame"]["unit"] = "mm"
    with open(metres, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    result = call("rigforge_metarig", {"object": retopo.name, "joints_file": metres})
    check("a unit mistake is caught by the same gate",
          (result.get("joints") or {}).get("enabled") is False,
          str((result.get("joints") or {}).get("inside_fraction")))

    missing = call("rigforge_metarig",
                   {"object": retopo.name,
                    "joints_file": os.path.join(workspace, "not_here.json")},
                   expect_error=True)
    check("a missing joints file is an error with the path in it",
          missing.get("status") == "error" and "not_here.json" in missing.get("message", ""),
          str(missing.get("message"))[:160])

    broken = os.path.join(workspace, "joints_broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        json.dump({"schema": "forge.joints/1", "joints": []}, handle)
    reply = call("rigforge_metarig", {"object": retopo.name, "joints_file": broken},
                 expect_error=True)
    check("an empty joints array is an error, not a silent no-op",
          reply.get("status") == "error", str(reply.get("message"))[:160])


def test_joints_best_effort(retopo, workspace):
    section("best effort - named predictions place what no tag can (fingers)")
    tags_only = call("rigforge_metarig", {"object": retopo.name, "preset": "human",
                                          "method": "tags"})
    meta = bpy.data.objects.get(tags_only["metarig"])
    before = bone_points(meta)
    finger = "f_index.01.L"
    if not check("the full human template has finger bones", finger in before,
                 str(sorted(n for n in before if n.startswith("f_"))[:6])):
        return
    span = max(retopo.dimensions)
    offset = Vector((0.0, 0.0, span * 0.01))
    entries = [
        ("index_01_L", before[finger][0] + offset),
        ("hand_L", before["hand.L"][0] + offset),
    ]
    path = os.path.join(workspace, "joints_named.json")
    write_joints(path, retopo, entries)

    result = call("rigforge_metarig", {"object": retopo.name, "preset": "human",
                                       "joints_file": path})
    report = result.get("joints") or {}
    check("the named file is recognised as named", report.get("named_joints") == 2,
          str(report.get("named_joints")))
    best = report.get("best_effort") or []
    check("best-effort placements were recorded", bool(best),
          str([(e.get("bone"), e.get("role")) for e in best]))
    placed = [entry for entry in best if entry.get("bone") == finger
              and entry.get("moved_mm")]
    check("the index finger was placed from its named prediction", bool(placed),
          str(best))
    after = bone_points(bpy.data.objects.get(result["metarig"]))
    moved = (after[finger][0] - before[finger][0]).length
    check("and the bone really moved", moved > span * 1e-4, "%.2f mm" % (moved * 1000.0))
    # "hand" and "wrist" are the same joint under two names, so the wrist
    # landmark takes that prediction by NAME during the fit and the best-effort
    # pass finds it already spoken for. One prediction, one landmark, never two.
    wrist = [entry for entry in report.get("refined") or []
             if entry["role"] == "wrist.L"]
    check("a joint named 'hand' refines the wrist landmark, by name",
          bool(wrist) and wrist[0].get("matched_by") == "name",
          str([(e["role"], e.get("matched_by")) for e in report.get("refined") or []]))
    check("and it is not used a second time for the hand bone",
          not any(entry.get("bone") == "hand.L" and entry.get("moved_mm")
                  for entry in best), str(best))
    check("nothing best-effort is counted as fitted from tags",
          finger not in (result.get("fitted_bones") or []),
          str(result.get("fitted_bones"))[:120])


# --- tests: the deformation harness -----------------------------------------

def test_generate(retopo):
    section("generate the rig the harness will pose")
    call("rigforge_metarig", {"object": retopo.name, "archetype": "auto"})
    result = call("rigforge_generate_rig", {"mesh": retopo.name})
    rig = bpy.data.objects.get(result["rig"])
    check("the rig generated", rig is not None, str(result.get("rig")))
    check("the mesh is skinned to it", result.get("weighted") is True,
          str(result.get("weights")))
    note("rig %s: %d deform bones, %d controls"
         % (result["rig"], result["deform_bones"], result["control_bones"]))
    return rig


def pose_fingerprint(rig):
    return {bone.name: tuple(round(v, 9) for row in bone.matrix_basis for v in row)
            for bone in rig.pose.bones}


def test_rig_check(rig, retopo):
    section("rig_check - the deformation harness")
    before = pose_fingerprint(rig)
    started = time.monotonic()
    result = call("rig_check", {"rig": rig.name, "mesh": retopo.name})
    elapsed = time.monotonic() - started
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    note("%d joints, %d poses, %.1fs (%s)"
         % (result["joints_measured"], result["poses_run"], result["seconds"],
            result["gate"]))

    check("it measured the limb and spine joints", result["joints_measured"] >= 6,
          "%d measured, %d skipped"
          % (result["joints_measured"], len(result["joints_skipped"])))
    check("it ran three poses per joint (the extreme set)",
          result["poses_per_joint"] == 3, str(result["poses_per_joint"]))
    check("it finished in minutes, not hours", elapsed < 900.0, "%.1fs" % elapsed)
    check("the gate is one of pass / attention / fail",
          result["gate"] in ("pass", "attention", "fail"), result["gate"])
    check("the thresholds are stated with the numbers they judged",
          set(result["thresholds"]) == {"volume_loss_pct", "new_intersections",
                                        "twist_collapse_pct"},
          str(sorted(result["thresholds"])))
    check("and stamped as a heuristic tier, not a measurement of taste",
          "heuristic" in result["threshold_tier"], result["threshold_tier"][:60])
    check("it says something a human can read", bool(result.get("says")),
          str(result.get("says")))

    joints = {entry["joint"]: entry for entry in result["joints"]}
    note("joints: %s" % ", ".join("%s=%s" % (name, entry["verdict"])
                                  for name, entry in sorted(joints.items())))
    expected = {"knee.L", "knee.R", "elbow.L", "elbow.R", "hip.L", "hip.R",
                "shoulder.L", "shoulder.R"}
    check("both knees, elbows, hips and shoulders were measured",
          expected <= set(joints), str(sorted(expected - set(joints))))

    # The eight limb joints are always measurable on this biped; the spine ones
    # depend on where Quadriflow put its vertices that run, so they are checked
    # in aggregate rather than one assertion at a time (a suite whose check
    # count moves between runs is a suite nobody can read a regression out of).
    check("every measured joint has a rest volume to compare against",
          all(entry["rest_hull_volume_mm3"] > 0.0 for entry in joints.values()),
          str({name: entry["rest_hull_volume_mm3"] for name, entry in joints.items()
               if entry["rest_hull_volume_mm3"] <= 0.0}))
    check("every skipped joint says why it was skipped",
          all(entry.get("reason") for entry in result["joints_skipped"]),
          str(result["joints_skipped"]))
    for name in sorted(expected & set(joints)):
        entry = joints[name]
        check("%s reports a volume loss for every pose" % name,
              all(pose["volume_loss_pct"] is not None for pose in entry["poses"]),
              str([pose["volume_loss_pct"] for pose in entry["poses"]]))
        check("%s proved its control actually moved the flesh" % name,
              max(pose["moved_mm"] for pose in entry["poses"]) > 0.5,
              str([pose["moved_mm"] for pose in entry["poses"]]))
        check("%s got a verdict per metric" % name,
              set(entry["verdicts"]) == {"volume", "intersections", "twist"},
              str(entry["verdicts"]))
        check("%s says which way it bends (measured, not assumed)" % name,
              entry["bend_sign"] in ("positive", "negative"), entry["bend_sign"])

    twisted = [entry for entry in result["joints"]
               if entry["worst_twist_collapse_pct"] is not None]
    check("the candy-wrapper metric produced numbers", bool(twisted),
          str([(e["joint"], e["worst_twist_collapse_pct"]) for e in result["joints"]]))
    clipped = [entry for entry in result["joints"]
               if entry["worst_new_intersections"] is not None]
    check("the self-intersection metric ran on the posed mesh", bool(clipped),
          str(result.get("rest_intersections")))

    check("the pose was restored", result["pose_restored"] is True)
    after = pose_fingerprint(rig)
    check("and it really was: every bone is back where it started", before == after,
          str([name for name in before if before[name] != after.get(name)])[:200])

    knee = joints.get("knee.L")
    if knee:
        note("knee.L: volume loss %.1f%%, twist collapse %s%%, new clips %s -> %s"
             % (knee["worst_volume_loss_pct"] or 0.0,
                knee["worst_twist_collapse_pct"], knee["worst_new_intersections"],
                knee["verdict"]))
    return result


def test_rig_check_options(rig, retopo):
    section("rig_check - the knobs")
    one = call("rig_check", {"rig": rig.name, "joints": ["knee.L"], "poses": "quick",
                             "intersections": False})
    check("a joint filter measures just that joint", one["joints_measured"] == 1,
          str([entry["joint"] for entry in one["joints"]]))
    check("the quick set is one pose", one["poses_run"] == 1, str(one["poses_run"]))
    check("intersections can be switched off",
          all(pose.get("new_intersections") is None
              for entry in one["joints"] for pose in entry["poses"]),
          "intersections were still scanned")

    explicit = call("rig_check", {"rig": rig.name, "joints": ["elbow.L"],
                                  "poses": [{"label": "half", "flex_deg": 75.0},
                                            {"label": "twist", "flex_deg": 0.0,
                                             "twist_deg": 60.0}],
                                  "intersections": False})
    check("an explicit pose list is honoured", explicit["pose_set"] == "explicit"
          and explicit["poses_run"] == 2, str(explicit["poses_run"]))
    poses = explicit["joints"][0]["poses"] if explicit["joints"] else []
    check("the requested angles are the ones reported",
          [abs(pose["flex_deg"]) for pose in poses] == [75.0, 0.0],
          str([pose["flex_deg"] for pose in poses]))

    bad = call("rig_check", {"rig": "no_such_rig"}, expect_error=True)
    check("an unknown rig is an error naming it", bad.get("status") == "error"
          and "no_such_rig" in bad.get("message", ""), str(bad.get("message"))[:140])
    bad = call("rig_check", {"rig": retopo.name}, expect_error=True)
    check("pointing it at a mesh says so", bad.get("status") == "error"
          and "not an armature" in bad.get("message", ""), str(bad.get("message"))[:140])
    bad = call("rig_check", {"rig": rig.name, "poses": "sideways"}, expect_error=True)
    check("an unknown pose set lists the known ones", bad.get("status") == "error"
          and "extreme" in bad.get("message", ""), str(bad.get("message"))[:140])


def test_rig_check_catches_a_break(rig, retopo):
    """A rig that cannot deform must not pass: break the weights, expect a change."""
    section("rig_check - it is a gate, so it has to be able to fail")
    good = call("rig_check", {"rig": rig.name, "joints": ["elbow.L"], "poses": "quick",
                              "intersections": False})
    baseline = good["joints"][0]["worst_volume_loss_pct"] if good["joints"] else None

    # Rigid-bind the forearm: every vertex the elbow owns snaps to one bone, which
    # is exactly the "no falloff" mistake that eats a joint's volume.
    mesh = bpy.data.objects.get(retopo.name)
    from forge.tools import rigforge_rig as rr

    names = rr.def_bones_for(rig, "forearm.L") + rr.def_bones_for(rig, "upper_arm.L")
    groups = [mesh.vertex_groups.get(name) for name in names]
    groups = [group for group in groups if group is not None]
    touched = 0
    if groups:
        by_index = {group.index: group for group in groups}
        assign = {}
        strip = {}
        for vertex in mesh.data.vertices:
            owned = [(entry.weight, entry.group) for entry in vertex.groups
                     if entry.group in by_index]
            if not owned:
                continue
            _weight, winner = max(owned)
            assign.setdefault(winner, []).append(vertex.index)
            for _w, group_index in owned:
                if group_index != winner:
                    strip.setdefault(group_index, []).append(vertex.index)
            touched += 1
        for group_index, verts in assign.items():
            by_index[group_index].add(verts, 1.0, "REPLACE")
        for group_index, verts in strip.items():
            by_index[group_index].remove(verts)
    note("hard-bound %d vertices across %d groups" % (touched, len(groups)))
    broken = call("rig_check", {"rig": rig.name, "joints": ["elbow.L"], "poses": "quick",
                                "intersections": False})
    after = broken["joints"][0]["worst_volume_loss_pct"] if broken["joints"] else None
    check("the harness notices a hard-bound joint deforms differently",
          baseline is not None and after is not None and abs(after - baseline) > 0.5,
          "%s%% -> %s%%" % (baseline, after))
    note("elbow.L volume loss: %s%% smooth-bound, %s%% hard-bound" % (baseline, after))


def test_panel_button(retopo, rig):
    section("the panel button")
    import re

    from forge.ui import panels

    source = open(panels.__file__, "r", encoding="utf-8").read()
    check("the Rig panel offers Check Deformation",
          'operator("forge.rig_check"' in source)
    check("and it maps to a registered operator",
          hasattr(bpy.ops.forge, "rig_check"),
          str(sorted(name for name in dir(bpy.ops.forge) if "rig" in name)))
    check("the operator is guarded off an unrigged mesh (it is drawn disabled)",
          bool(re.search(r"row\.enabled = rigged", source)))

    bpy.context.view_layer.objects.active = retopo
    result = bpy.ops.forge.rig_check()
    check("clicking it runs and finishes", "FINISHED" in result, str(result))
    props = bpy.context.scene.forge_rigforge
    check("and it reports through the RigForge status line",
          "rig_check" in (props.summary or ""), str(props.summary))


def test_server_frees_its_port():
    section("teardown")
    from forge import server as forge_server

    forge_server.stop_server()
    check("the command socket closed", not forge_server.is_running())


# --- entry point ------------------------------------------------------------

def main():
    print("Forge add-on rigging-bridge headless tests (joints_file + rig_check)")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_rigbridge_test_")
    try:
        _sculpt, retopo = build_and_tag()
        if retopo is None:
            raise AssertionError("no retopo mesh; the whole suite needs one")
        note("retopo: %d faces" % len(retopo.data.polygons))

        _meta, baseline = test_tags_only(retopo)
        test_joints_refine(retopo, baseline, workspace)
        test_joints_weight_extremes(retopo, baseline, workspace)
        test_joints_frame_guard(retopo, baseline, workspace)
        test_joints_best_effort(retopo, workspace)

        rig = test_generate(retopo)
        if rig is None:
            raise AssertionError("no rig; the harness tests need one")
        test_rig_check(rig, retopo)
        test_rig_check_options(rig, retopo)
        test_panel_button(retopo, rig)
        test_rig_check_catches_a_break(rig, retopo)
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
