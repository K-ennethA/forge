"""Headless add-on tests for the **leg sub-tag split** (rigforge_autotag + rigforge_skin).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_legsubtags.py

Unlike the other rigging suites this one does **not** build its own figure.  The
defect it pins is a property of a real retopo with a real Rigify leg on it — a
``Leg.R`` tag holding six deform bones, flesh 1.1 m long, and a foot bone
holding thigh flesh 340 mm away — and a synthetic tube three rings long cannot
express it.  So it runs against the werewolf, on a **copy**: the original in
``projects/werewolf/models/`` is never opened for writing and never saved.

That file is a working asset rather than a tracked one, so a checkout without it
is normal.  The suite says so and exits clean rather than failing, because a
missing asset is not a regression.

What is pinned here
-------------------
1. **The split is a measurement.**  Three slabs per leg, and each cut lands on
   *that* leg's own knee and ankle — the joints of its own deform chain — to
   within :data:`CUT_TOLERANCE_MM`.
2. **The gate the lane exists for.**  After a re-skin, the leg-internal stray
   mass — influence shared by two bones of the same ``Leg`` tag that sit far
   enough apart not to be a blend band — drops from 16.46 to under
   :data:`STRAY_BUDGET`.  The exact number is printed either way.
3. **The torso fix does not pay for it.**  The arm swing still moves the pelvis
   and the thighs 0.00 mm.
4. **Nothing assumes a mirror.**  One knee is moved 80 mm up the leg and only
   that leg's cut follows it, which is what ``symmetry: as_designed`` needs: two
   sides measured independently rather than one measured and copied.

No window, no port, no detector, no Rigify: the file already has its rig.
"""

import os
import shutil
import sys
import tempfile
import traceback

import bpy
from mathutils import Vector

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

SOURCE = os.path.join(REPO_DIR, "projects", "werewolf", "models",
                      "werewolf-wip-10.blend")
MESH_NAME = "werewolf-form-a_retopo"
RIG_NAME = "werewolf-form-a_retopo_rig"

#: How far a cut may land from the joint it is supposed to be at.  The cut is a
#: point on the tag's own principal axis and the joint is a point on the rig, so
#: a millimetre or two of axis tilt is expected and anything more is the cut
#: being derived from something other than that joint.  Measured on this figure:
#: 1.5 mm at the knee and 0.6 mm at the ankle.
CUT_TOLERANCE_MM = 5.0

#: What the leg-internal stray mass must come in under.  The gate this lane
#: exists for, and the number it started at is 16.46.
STRAY_BUDGET = 0.5

#: How far the arm swing may move the lower body.  ``rigforge_skin``'s own "ok"
#: band; the measurement is 0.00 mm and the band is what it is asserted against.
ISOLATION_BUDGET_MM = 1.0

#: How far up the leg the asymmetry check moves one knee.  Big enough that a
#: mirrored answer is unmistakable, small enough to leave three real slabs.
ASYMMETRY_NUDGE_MM = 80.0

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


# --- the copy ----------------------------------------------------------------

def open_copy(copy_path):
    """Open the scratch copy and hand back the pieces every test needs."""
    from forge.tools import rigforge_rig

    bpy.ops.wm.open_mainfile(filepath=copy_path)
    mesh = bpy.data.objects.get(MESH_NAME)
    rig = bpy.data.objects.get(RIG_NAME)
    if mesh is None or rig is None:
        raise AssertionError("the copy has no %r / %r" % (MESH_NAME, RIG_NAME))
    stored = str(rigforge_rig._prop(mesh, rigforge_rig.PROP_METARIG, "") or "")
    metarig = bpy.data.objects.get(stored) if stored else None
    regions, _empty = rigforge_rig.measure_tags(mesh)
    return mesh, rig, metarig, regions


def joint_heights(rig):
    """``{bone: world z in mm}`` for the heads the cuts are supposed to land on."""
    out = {}
    for name in sorted(rig.data.bones.keys()):
        bone = rig.data.bones[name]
        out[name] = (rig.matrix_world @ bone.head_local).z * 1000.0
    return out


def leg_internal_stray(rig, mesh, metarig, regions):
    """``(total, [(bone, bone, mass), ...])`` — stray mass **inside** one Leg tag.

    The overlap gate's own rows, filtered to the pairs this lane is about: two
    bones the *same* ``Leg`` tag owns, sharing influence while sitting far
    enough apart that the gate has already ruled out a blend band.  Ownership is
    read from the merged (un-split) contract on purpose — the question is
    "should one leg's own two ends be holding each other's flesh", and it has to
    be askable whether or not the split is in place.
    """
    from forge.tools import rigforge_landmarks, rigforge_skin

    owner, _source = rigforge_skin.bone_owners(rig, metarig, regions)
    overlap = rigforge_landmarks.influence_overlap(rig, mesh)
    total = 0.0
    rows = []
    for row in overlap["pairs"]:
        if row["kind"] != "stray":
            continue
        one, other = row["bones"]
        tag = owner.get(one)
        if tag and tag == owner.get(other) and tag.startswith("Leg"):
            total += row["mass"]
            rows.append((one, other, row["mass"]))
    return total, rows, overlap["stray_mass"]


def reskin(mesh, rig, metarig, regions, split=True):
    from forge.tools import rigforge_skin
    from forge.tools.common import object_mode

    warnings = []
    with object_mode():
        result = rigforge_skin.constrain_weights(mesh, rig, metarig, regions,
                                                 warnings=warnings, split=split)
    return result, warnings


# --- 1: the split is a measurement -------------------------------------------

def test_split_is_three_slabs_at_the_joints(mesh, rig, metarig, regions):
    """Three slabs a side, each cut landing on that leg's own knee and ankle."""
    from forge.tools import rigforge_autotag, rigforge_skin

    section("the leg sub-tag view")
    split, torso_report, leg_reports = rigforge_skin.body_split(mesh, regions, rig,
                                                                metarig)
    check("a split was produced at all", split is not None,
          "torso: %s" % torso_report.get("refused"))
    if split is None:
        return None

    leg_tags = sorted(tag for tag in regions if tag.startswith("Leg"))
    check("both legs are tagged on this figure", len(leg_tags) == 2, str(leg_tags))
    check("the torso is still split too", torso_report.get("cuts") is not None,
          str(torso_report.get("refused")))

    heights = joint_heights(rig)
    for tag in leg_tags:
        report = leg_reports.get(tag) or {}
        if not check("%s was split" % tag, not report.get("refused"),
                     str(report.get("refused"))):
            continue
        names = rigforge_autotag.leg_sub_tags(tag)
        check("%s names three slabs, proximal first" % tag,
              tuple(report["names"]) == names, str(report.get("names")))
        check("%s has a member per slab in the split" % tag,
              all(name in split.names for name in names), str(split.names))
        counts = report.get("vertices") or {}
        check("%s put flesh in all three slabs" % tag,
              all(counts.get(name, 0) >= 12 for name in names), str(counts))
        note("%s  %s" % (tag, report["says"]))

        # The cuts, against the rig's own joints. The chain is read structurally
        # by leg_chain_joints, so the bone names here are only for the message.
        side = tag[len("Leg"):]
        knee = heights.get("DEF-shin%s" % side)
        ankle = heights.get("DEF-foot%s" % side)
        cut = report.get("cut_height_mm") or []
        check("%s knows where its own knee and ankle are" % tag,
              knee is not None and ankle is not None and len(cut) == 2,
              "knee=%s ankle=%s cut=%s" % (knee, ankle, cut))
        if knee is None or ankle is None or len(cut) != 2:
            continue
        check("%s knee cut sits on the DEF thigh/shin joint" % tag,
              abs(cut[0] - knee) <= CUT_TOLERANCE_MM,
              "cut %.1f mm vs joint %.1f mm" % (cut[0], knee))
        check("%s ankle cut sits on the DEF shin/foot joint" % tag,
              abs(cut[1] - ankle) <= CUT_TOLERANCE_MM,
              "cut %.1f mm vs joint %.1f mm" % (cut[1], ankle))
        note("%s knee cut %.1f mm (joint %.1f), ankle cut %.1f mm (joint %.1f)"
             % (tag, cut[0], knee, cut[1], ankle))

    # The contract the split buys, which is the point of cutting at all.
    contract = rigforge_skin.legal_bone_sets(rig, metarig, regions, split)
    legal = contract["legal"]
    for tag in leg_tags:
        names = rigforge_autotag.leg_sub_tags(tag)
        side = tag[len("Leg"):]
        thigh, shin, foot = names
        check("%s: the foot bone is no longer legal on thigh flesh" % tag,
              ("DEF-foot%s" % side) not in legal.get(thigh, ()),
              sorted(legal.get(thigh, ())))
        check("%s: the toe is no longer legal on shin flesh" % tag,
              ("DEF-toe%s" % side) not in legal.get(shin, ()),
              sorted(legal.get(shin, ())))
        check("%s: the foot slab still hangs from the shin - an ankle band" % tag,
              any(name.startswith("DEF-shin%s" % side)
                  for name in contract["hinges"].get(foot, ())),
              str(contract["hinges"].get(foot)))
        check("%s: the shin slab still hangs from the thigh - a knee band" % tag,
              any(name.startswith("DEF-thigh%s" % side)
                  for name in contract["hinges"].get(shin, ())),
              str(contract["hinges"].get(shin)))

    # The articulation rule: a band at every hinge and nowhere else.
    linked = rigforge_skin.articulations(contract, split)
    for tag in leg_tags:
        thigh, shin, foot = rigforge_autotag.leg_sub_tags(tag)
        for pair in ((thigh, shin), (shin, foot)):
            key = tuple(sorted(pair))
            check("%s articulates with %s" % key, key in linked, sorted(linked))
        stranger = tuple(sorted((thigh, foot)))
        check("%s does NOT articulate with %s" % stranger, stranger not in linked,
              "a thigh and a foot do not share a joint")
    across = tuple(sorted(("Leg.L.shin", "Leg.R.shin")))
    check("one leg's shin does not articulate with the other's",
          across not in linked, sorted(linked))
    return split


# --- 2 and 3: the gates -------------------------------------------------------

def test_reskin_clears_the_leg_internal_bleed(mesh, rig, metarig, regions):
    """The gate the lane exists for, measured before and after on the same copy."""
    from forge.tools import rigforge_skin

    section("the overlap gate, leg-internal")
    before, before_rows, before_all = leg_internal_stray(rig, mesh, metarig, regions)
    note("BEFORE (the weights as the file was saved): leg-internal stray %.4f, "
         "whole-figure stray %.4f" % (before, before_all))
    for one, other, mass in before_rows:
        note("   %s + %s  %.4f" % (one, other, mass))
    check("the defect reproduces on this copy", before > 1.0,
          "leg-internal stray was %.4f, so there is nothing to fix" % before)

    result, warnings = reskin(mesh, rig, metarig, regions, split=True)
    check("the leg splits were not taken back",
          not any("was not split" in text or "split was taken back" in text
                  for text in warnings),
          "; ".join(warnings))
    reports = result.get("leg_sub_tags") or {}
    check("the result reports a leg sub-tag view",
          bool(reports) and all(not entry.get("refused") for entry in reports.values()),
          str({tag: entry.get("refused") for tag, entry in reports.items()}))

    after, after_rows, after_all = leg_internal_stray(rig, mesh, metarig, regions)
    note("AFTER  (re-skinned with leg sub-tags):      leg-internal stray %.4f, "
         "whole-figure stray %.4f" % (after, after_all))
    for one, other, mass in after_rows:
        note("   %s + %s  %.4f" % (one, other, mass))
    check("leg-internal stray mass is cleared", after < STRAY_BUDGET,
          "%.4f, budget %.2f" % (after, STRAY_BUDGET))
    check("and it is an improvement, not a different bind", after < before,
          "%.4f -> %.4f" % (before, after))
    note("leg-internal stray mass: %.4f -> %.4f   (whole figure %.4f -> %.4f)"
         % (before, after, before_all, after_all))
    return result


def test_the_torso_fix_still_holds(mesh, rig, metarig, regions):
    """The arm swing still does not move the pelvis or the thighs."""
    from forge.tools import rigforge_skin
    from forge.tools.common import object_mode

    section("arm-swing isolation (the torso fix, unregressed)")
    split, _torso, _legs = rigforge_skin.body_split(mesh, regions, rig, metarig)
    with object_mode():
        isolation = rigforge_skin.arm_swing_isolation(rig, mesh, regions, split,
                                                      metarig)
    maximum = isolation.get("max_mm")
    check("the swing was measured at all", maximum is not None,
          isolation.get("says"))
    if maximum is None:
        return
    for group in isolation.get("groups", []):
        note("%s: %s vertices, worst %.2f mm"
             % (group.get("group") or group.get("name"), group.get("vertices"),
                group.get("max_mm") or 0.0))
    check("the swing still measures a thigh group at all",
          any((group.get("vertices") or 0) > 0
              for group in isolation.get("groups", [])
              if "thigh" in str(group.get("group") or group.get("name")).lower()),
          "the leg split must not hide the group this gate watches")
    check("the arm swing still moves the lower body 0.00 mm",
          maximum <= ISOLATION_BUDGET_MM,
          "%.2f mm, budget %.2f" % (maximum, ISOLATION_BUDGET_MM))
    check("and the rig was put back where it was",
          (isolation.get("restored_max_mm") or 0.0) <= 0.01,
          str(isolation.get("restored_max_mm")))
    note("isolation: max %.2f mm, mean %.2f mm, verdict %s"
         % (maximum, isolation.get("mean_mm") or 0.0, isolation.get("verdict")))


# --- 4: no mirror assumption --------------------------------------------------

def test_no_mirror_assumption(copy_path):
    """Move one knee and check only that leg's cut follows it.

    ``symmetry: as_designed`` means the two sides are measured independently, so
    the thing to prove is not that a mirrored figure comes out mirrored — it
    would whatever the code did — but that an **un**-mirrored one comes out
    un-mirrored.  So one leg's knee joint is moved 80 mm up its own chain and
    the two sides are read back: the moved leg's cut follows its own joint, the
    other leg's does not move at all, and both still split into three slabs.
    """
    from forge.tools import rigforge_skin

    section("no mirror assumption (symmetry: as_designed)")
    mesh, rig, metarig, regions = open_copy(copy_path)
    _split, _torso, before = rigforge_skin.body_split(mesh, regions, rig, metarig)
    was = {tag: (before[tag].get("cut_height_mm") or [None, None])
           for tag in sorted(before)}

    nudge = ASYMMETRY_NUDGE_MM / 1000.0
    bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bones = rig.data.edit_bones
        # The knee, from both sides of it, so a connected chain cannot drag it
        # back: the thigh's distal end and the shin's proximal end are the same
        # point and both are moved.
        for name in ("DEF-thigh.R.001", "ORG-thigh.R.001", "MCH-thigh.R.001"):
            bone = bones.get(name)
            if bone is not None:
                bone.tail.z += nudge
        for name in ("DEF-shin.R", "ORG-shin.R"):
            bone = bones.get(name)
            if bone is not None:
                bone.head.z += nudge
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    moved = joint_heights(rig).get("DEF-shin.R")
    note("DEF-shin.R's head is now %.1f mm up (was %.1f)"
         % (moved, moved - ASYMMETRY_NUDGE_MM))

    regions, _empty = __import__("forge.tools.rigforge_rig", fromlist=["x"]) \
        .measure_tags(mesh)
    _split, _torso, after = rigforge_skin.body_split(mesh, regions, rig, metarig)
    for tag in sorted(after):
        report = after[tag]
        check("%s still splits with the knee moved" % tag,
              not report.get("refused"), str(report.get("refused")))
    if any(after[tag].get("refused") for tag in after):
        return

    right = after["Leg.R"]["cut_height_mm"][0]
    left = after["Leg.L"]["cut_height_mm"][0]
    check("the moved leg's knee cut followed its own joint",
          abs(right - moved) <= CUT_TOLERANCE_MM,
          "cut %.1f mm vs joint %.1f mm" % (right, moved))
    check("the moved leg's cut actually moved",
          abs(right - (was["Leg.R"][0] or 0.0)) > ASYMMETRY_NUDGE_MM * 0.5,
          "%.1f -> %.1f mm" % (was["Leg.R"][0] or 0.0, right))
    check("the OTHER leg's cut did not move - no mirror was assumed",
          abs(left - (was["Leg.L"][0] or 0.0)) <= CUT_TOLERANCE_MM,
          "%.1f -> %.1f mm" % (was["Leg.L"][0] or 0.0, left))
    check("and the two legs now disagree, which is the whole point",
          abs(left - right) > ASYMMETRY_NUDGE_MM * 0.5,
          "left %.1f mm, right %.1f mm" % (left, right))
    note("knee cuts after the nudge: left %.1f mm, right %.1f mm" % (left, right))


# --- main --------------------------------------------------------------------

def main():
    print("== headless_legsubtags ==")
    if not os.path.exists(SOURCE):
        print("  SKIP: %s is not in this checkout." % SOURCE)
        print("     It is a working asset rather than a tracked one, so this is "
              "normal on a fresh clone; the suite has nothing to measure and is "
              "not a regression.")
        print("\n0 checks, 0 failed")
        print("RESULT: SKIPPED")
        sys.exit(0)

    enable_addon()
    workspace = tempfile.mkdtemp(prefix="forge_legsubtags_")
    copy_path = os.path.join(workspace, "werewolf-legsubtags-copy.blend")
    try:
        shutil.copyfile(SOURCE, copy_path)
        note("testing against a copy at %s" % copy_path)
        note("the original is never opened for writing")

        mesh, rig, metarig, regions = open_copy(copy_path)
        note("%s: %d vertices, %d tags; rig %s"
             % (mesh.name, len(mesh.data.vertices), len(regions), rig.name))
        test_split_is_three_slabs_at_the_joints(mesh, rig, metarig, regions)
        test_reskin_clears_the_leg_internal_bleed(mesh, rig, metarig, regions)
        test_the_torso_fix_still_holds(mesh, rig, metarig, regions)
        test_no_mirror_assumption(copy_path)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        # Never leave the copy behind, and never touch the original.
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
