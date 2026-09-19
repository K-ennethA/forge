"""Headless add-on tests for the **torso chain split** (rigforge_autotag + rigforge_skin).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_torsosubtags.py

The third application of one recipe.  ``headless_legsubtags`` pins the leg split
— a tag cut into slabs at its own chain's joints — and this pins the same cut
applied to the **trunk**, plus the seam rule the trunk's failure exposed.

It runs against ``werewolf-wip-11``, on a **copy**: the original is never opened
for writing and never saved.  That file carries the artist's own joint edits
(``projects/werewolf/design/artist-edits.json``: ankles down 44 mm, head top
down 52 mm), and those placements are **law** — the split reads them and must
never "correct" them, which is asserted below rather than assumed.

That working asset is not tracked, so a checkout without it is normal; the suite
says so and exits clean rather than failing.

What is pinned here
-------------------
1. **The trunk is cut at its own spine joints**, to within
   :data:`CUT_TOLERANCE_MM` of where the rig puts them, and a vertebra too short
   to own a measurable slab merges instead of producing one.
2. **The seam bounds what it lends.**  The defect this lane was opened for:
   ``DEF-spine.005`` held 46 ``Arm.L`` deltoid vertices from 344 mm away,
   because a bone lent across a tag seam was bounded by the *lending tag's*
   girth — 458 mm for a trunk — rather than by the 68 mm seam it came across.
3. **The gate.**  After a re-skin the whole-figure stray mass is at or under
   :data:`STRAY_BUDGET`, the leg-internal bleed stays at zero, and the arm swing
   still moves the lower body 0.00 mm.
4. **A trunk too coarse to carry the cut refuses it** and falls back to the
   three-landmark split rather than producing slabs that are entirely band.
"""

import json
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
                      "werewolf-wip-11.blend")
EDITS = os.path.join(REPO_DIR, "projects", "werewolf", "design", "artist-edits.json")
MESH_NAME = "werewolf-form-a_retopo"
RIG_NAME = "werewolf-form-a_retopo_rig"

#: How far a cut may land from the joint it is cut at.  The cut is a point on
#: the tag's own principal axis and the joint is a point on the rig, so a
#: millimetre or two of axis tilt is expected and more than that means the cut
#: came from something other than that joint.
CUT_TOLERANCE_MM = 5.0

#: What the whole figure's stray influence mass must come in under after a
#: re-skin.  ``rigforge_landmarks.OVERLAP_THRESHOLDS`` calls 1.0 the edge of
#: "attention"; the lane that opened this suite started at 8.34.
STRAY_BUDGET = 1.0

#: What the leg-internal bleed must stay at — it was cleared in the previous
#: lane and this one must not spend it.
LEG_BUDGET = 0.5

#: How far the arm swing may move the pelvis and the thighs.
ISOLATION_BUDGET_MM = 1.0

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


def open_copy(copy_path):
    """Open the copy and resolve its pieces **the way the stage does**.

    The rig is resolved with :func:`~forge.tools.rigforge_rig._rig_for_mesh`
    rather than by name, and that is not fussiness: ``werewolf-wip-11`` carries
    two armatures, ``..._rig`` and ``..._rig.001``, and it is the **.001** that
    drives the mesh and holds the artist's joint edits — its ankle sits at
    64.4 mm where the older one still has 173.0 mm.  A suite that picks the rig
    by name measures a rig nothing is skinned to, and would have reported this
    whole lane against joints the artist had already moved.
    """
    from forge.tools import rigforge_rig

    bpy.ops.wm.open_mainfile(filepath=copy_path)
    mesh = bpy.data.objects.get(MESH_NAME)
    if mesh is None:
        raise AssertionError("the copy has no %r" % MESH_NAME)
    rig = rigforge_rig._rig_for_mesh(mesh, {})
    if rig is None:
        raise AssertionError("no rig drives %r in the copy" % MESH_NAME)
    stored = str(rigforge_rig._prop(mesh, rigforge_rig.PROP_METARIG, "") or "")
    metarig = bpy.data.objects.get(stored) if stored else None
    regions, _empty = rigforge_rig.measure_tags(mesh)
    return mesh, rig, metarig, regions


def head_mm(rig, name):
    bone = rig.data.bones.get(name)
    if bone is None:
        return None
    return (rig.matrix_world @ bone.head_local).z * 1000.0


def stray_rows(rig, mesh):
    from forge.tools import rigforge_landmarks

    overlap = rigforge_landmarks.influence_overlap(rig, mesh)
    rows = [(row["bones"][0], row["bones"][1], row["mass"], row["vertices"],
             row["gap_mm"])
            for row in overlap["pairs"] if row["kind"] == "stray"]
    return overlap["stray_mass"], rows


def leg_internal(rig, mesh, metarig, regions):
    from forge.tools import rigforge_skin

    owner, _source = rigforge_skin.bone_owners(rig, metarig, regions)
    _total, rows = stray_rows(rig, mesh)
    return sum(mass for one, other, mass, _v, _g in rows
               if owner.get(one) and owner.get(one) == owner.get(other)
               and owner[one].startswith("Leg"))


# --- 1: the trunk is cut at its own spine joints ------------------------------

def test_trunk_is_cut_at_its_own_joints(mesh, rig, metarig, regions):
    from forge.tools import rigforge_autotag, rigforge_skin

    section("the torso, cut at its own spine's joints")
    split, torso, legs = rigforge_skin.body_split(mesh, regions, rig, metarig)
    check("a split was produced", split is not None, str(torso.get("refused")))
    if split is None:
        return None
    check("the trunk was cut at the chain, not at the landmarks",
          torso.get("measured_in") == "the spine chain's own joints",
          "%s (%s)" % (torso.get("measured_in"), torso.get("chain_refused")))
    names = list(torso.get("names") or ())
    check("the trunk came out as more than the three landmark slabs",
          len(names) > 3, str(names))
    check("every slab is named after the vertebra it holds",
          all(name.startswith(rigforge_autotag.SPLIT_PARENT + ".") for name in names),
          str(names))
    note(torso.get("says"))

    heights = torso.get("cut_height_mm") or []
    joints = list(torso.get("joints") or ())
    joint_heights = torso.get("joint_height_mm") or []
    check("every cut sits on a joint of the spine chain",
          len(heights) == len(names) - 1 and bool(heights), str(heights))
    for height in heights:
        nearest = min(joint_heights, key=lambda value: abs(value - height))
        check("the cut at %.0f mm is a spine joint (%.0f mm)" % (height, nearest),
              abs(nearest - height) <= CUT_TOLERANCE_MM,
              "%.1f mm from the nearest joint" % abs(nearest - height))

    dropped = list(torso.get("dropped") or ())
    counts = torso.get("vertices") or {}
    check("a vertebra too short to own a slab merged rather than made one",
          all(counts.get(name, 0) >= 12 for name in names),
          str(counts))
    if dropped:
        note("%d cut(s) dropped as too thin to measure: %s"
             % (len(dropped), ", ".join(dropped)))

    # The contract the cut buys: no two spine bones a chain apart share a slab.
    contract = rigforge_skin.legal_bone_sets(rig, metarig, regions, split)
    legal = contract["legal"]
    for slab in names:
        spine = sorted(name for name in legal.get(slab, ())
                       if name.startswith("DEF-spine"))
        check("%s holds a run of neighbouring vertebrae, not the whole spine"
              % slab, len(spine) <= 4, str(spine))
    check("DEF-spine.001 and DEF-spine.005 are no longer legal on one slab",
          not any("DEF-spine.001" in legal.get(slab, ())
                  and "DEF-spine.005" in legal.get(slab, ()) for slab in names),
          str({slab: sorted(legal.get(slab, ())) for slab in names}))
    return split


# --- 2: the seam bounds what it lends -----------------------------------------

def test_the_seam_bounds_what_it_lends(mesh, rig, metarig, regions, split):
    """The 344 mm pair, and the mechanism that allowed it."""
    from forge.tools import rigforge_skin

    section("a bone lent across a seam is bounded by that seam")
    contract = rigforge_skin.legal_bone_sets(rig, metarig, regions, split)
    tags = rigforge_skin.tag_membership(mesh, split)
    edges = rigforge_skin.vertex_edges(mesh)
    used = rigforge_skin.split_regions(regions, split)
    _blend, report = rigforge_skin.blend_zones(
        mesh, tags, edges, used, rigforge_skin.BLEND_GIRTH_FRACTION,
        rigforge_skin.sub_tag_seam_widths(split),
        rigforge_skin.articulations(contract, split))

    girths = rigforge_skin.tag_girths(used)
    arm_seam = None
    for row in report["seams"]:
        if "Arm.L" in row["tags"]:
            arm_seam = row
            break
    check("the arm meets the trunk at a measured seam", arm_seam is not None,
          str([row["tags"] for row in report["seams"]]))
    if arm_seam is None:
        return
    lender = [tag for tag in arm_seam["tags"] if tag != "Arm.L"][0]
    into_arm = (arm_seam.get("width_by_tag_mm") or {}).get("Arm.L",
                                                           arm_seam["width_mm"])
    girth_reach = 3.0 * girths.get(lender, 0.0) * 1000.0
    note("the %s/Arm.L seam reaches %.0f mm into the arm; that lender's girth "
         "would have reached %.0f mm" % (lender, into_arm, girth_reach))
    check("the seam is far narrower than the lending tag's girth reach",
          into_arm < girth_reach,
          "%.0f mm vs %.0f mm" % (into_arm, girth_reach))

    # The defect itself, as a distance: the bone that was reaching, and how far.
    far = None
    for name in sorted(contract["legal"].get(lender, ())):
        bone = rig.data.bones.get(name)
        if bone is None:
            continue
        middle = rig.matrix_world @ ((bone.head_local + bone.tail_local) * 0.5)
        arm = rig.data.bones.get("DEF-upper_arm.L")
        if arm is None:
            continue
        seam_point = rig.matrix_world @ arm.head_local
        gap = (middle - seam_point).length * 1000.0
        if far is None or gap > far[1]:
            far = (name, gap)
    if far is not None:
        note("the furthest bone %s owns sits %.0f mm from the shoulder"
             % (lender, far[1]))
        check("and it is further from the shoulder than the seam is wide - so "
              "only the bound keeps it off the arm", far[1] > into_arm,
              "%s at %.0f mm vs a %.0f mm seam" % (far[0], far[1], into_arm))


# --- 2b: every band carries its own falloff -----------------------------------

def test_every_band_carries_a_falloff(mesh, rig, metarig, regions, split):
    """Each side of each seam gets a band at least its own floor deep.

    The invariant :data:`~forge.tools.rigforge_skin.MIN_ARTICULATION_RINGS`
    exists for — a band thinner than that cannot hold a ramp — measured per side
    in that side's own rings.

    The *other* half of that rule, a cap tying a band to a fraction of the slab
    it enters, was implemented and **taken back out**; the numbers printed here
    are why, and they are printed rather than asserted so the next person can
    re-measure them rather than take this comment's word for it.  This trunk's
    vertebrae sit ~166 mm apart while its own topology asks for ~73 mm bands, so
    a band is ~44% of its slab: any cap at or under 40% makes the vertebral cut
    unsatisfiable and merges the trunk back into two slabs, which costs the
    whole 6.97 of stray mass the cut was made for.  The only fractions that keep
    both are within a couple of points of the failure, which is a fitted number
    rather than a derived one.
    """
    from forge.tools import rigforge_skin

    section("every band is at least as deep as its own falloff needs")
    contract = rigforge_skin.legal_bone_sets(rig, metarig, regions, split)
    tags = rigforge_skin.tag_membership(mesh, split)
    used = rigforge_skin.split_regions(regions, split)
    _blend, report = rigforge_skin.blend_zones(
        mesh, tags, rigforge_skin.vertex_edges(mesh), used,
        rigforge_skin.BLEND_GIRTH_FRACTION,
        rigforge_skin.sub_tag_seam_widths(split),
        rigforge_skin.articulations(contract, split))

    parent = __import__("forge.tools.rigforge_autotag", fromlist=["x"]).SPLIT_PARENT
    ratios = []
    for row in report["seams"]:
        widths = row.get("width_by_tag_mm") or {}
        floors = row.get("floor_by_tag_mm") or {}
        for side, floor in sorted(floors.items()):
            width = widths.get(side, row["width_mm"])
            check("%s/%s: the band into %s carries its own falloff"
                  % (row["tags"][0], row["tags"][1], side),
                  width + 1e-6 >= floor,
                  "%.1f mm against a %.1f mm floor" % (width, floor))
            extent = getattr(used.get(side), "length", 0.0) * 1000.0
            if extent > 0.0:
                ratios.append((width / extent, side, width, extent))
    ratios.sort(reverse=True)
    for share, side, width, extent in ratios[:1]:
        note("deepest band anywhere: %.0f%% of %s (%.0f mm into a %.0f mm slab)"
             % (100.0 * share, side, width, extent))
    trunk = [row for row in ratios if row[1].startswith(parent + ".")]
    if trunk:
        share, side, width, extent = trunk[0]
        note("deepest band in the trunk: %.0f%% of %s (%.0f mm into a %.0f mm "
             "slab) - any cap under that merges the vertebral cut away"
             % (100.0 * share, side, width, extent))


# --- 3: the gates -------------------------------------------------------------

def test_reskin_clears_the_trunk(mesh, rig, metarig, regions):
    from forge.tools import rigforge_skin
    from forge.tools.common import object_mode

    section("the overlap gate, whole figure")
    before, before_rows = stray_rows(rig, mesh)
    note("BEFORE (the weights as the pipeline left them): stray %.4f" % before)
    for one, other, mass, verts, gap in before_rows[:6]:
        note("   %-22s %-22s %7.4f  %3d vertices  %.0f mm apart"
             % (one, other, mass, verts, gap))
    check("the defect reproduces on this copy", before > STRAY_BUDGET,
          "stray was %.4f, so there is nothing to fix" % before)

    warnings = []
    with object_mode():
        rigforge_skin.constrain_weights(mesh, rig, metarig, regions,
                                        warnings=warnings, split=True)
    after, after_rows = stray_rows(rig, mesh)
    note("AFTER  (re-skinned with the full split set):     stray %.4f" % after)
    for one, other, mass, verts, gap in after_rows[:6]:
        note("   %-22s %-22s %7.4f  %3d vertices  %.0f mm apart"
             % (one, other, mass, verts, gap))
    check("whole-figure stray mass is inside the budget", after <= STRAY_BUDGET,
          "%.4f, budget %.2f" % (after, STRAY_BUDGET))
    check("and it is an improvement, not a different bind", after < before,
          "%.4f -> %.4f" % (before, after))
    note("whole-figure stray mass: %.4f -> %.4f" % (before, after))

    legs = leg_internal(rig, mesh, metarig, regions)
    check("the leg-internal bleed stays cleared", legs <= LEG_BUDGET,
          "%.4f, budget %.2f" % (legs, LEG_BUDGET))
    note("leg-internal stray mass: %.4f" % legs)

    split, _torso, _legs = rigforge_skin.body_split(mesh, regions, rig, metarig)
    with object_mode():
        isolation = rigforge_skin.arm_swing_isolation(rig, mesh, regions, split,
                                                      metarig)
    maximum = isolation.get("max_mm")
    if maximum is None:
        note("unmeasured: %s" % isolation.get("says"))
    check("the arm swing still moves the lower body 0.00 mm",
          maximum is not None and maximum <= ISOLATION_BUDGET_MM,
          str(maximum))
    check("and the rig was put back where it was",
          (isolation.get("restored_max_mm") or 0.0) <= 0.01,
          str(isolation.get("restored_max_mm")))
    note("isolation: max %.2f mm, mean %.2f mm, verdict %s"
         % (maximum or 0.0, isolation.get("mean_mm") or 0.0,
            isolation.get("verdict")))


# --- 4: the artist's joints are law -------------------------------------------

def test_artist_edits_are_law(mesh, rig, metarig, regions, split):
    """The split reads the artist's placements; it never moves them."""
    from forge.tools import rigforge_skin

    section("the artist's joint edits are law")
    if not os.path.exists(EDITS):
        note("no artist-edits.json beside this model; nothing to hold the code to")
        return
    try:
        edits = json.load(open(EDITS, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("artist-edits.json is readable", False, str(exc))
        return
    note("%d recorded edit(s): %s"
         % (len(edits), "; ".join("%s %s mm" % (entry.get("joint"),
                                                entry.get("delta_mm"))
                                  for entry in edits)))

    # The ankle is the one a *split* reads, so it is the one that can be
    # checked: the leg's distal cut must sit on the moved joint, not on where
    # the joint would have been before the artist moved it.
    moved = [entry for entry in edits if entry.get("joint") == "ankle"]
    if moved:
        drop = sum(float(entry.get("delta_mm", [0, 0, 0])[2]) for entry in moved)
        _split, _torso, legs = rigforge_skin.body_split(mesh, regions, rig, metarig)
        for tag in sorted(legs):
            report = legs[tag]
            if report.get("refused"):
                continue
            side = tag[len("Leg"):]
            ankle = head_mm(rig, "DEF-foot%s" % side)
            cut = (report.get("cut_height_mm") or [None, None])[1]
            if ankle is None or cut is None:
                continue
            check("%s's ankle cut follows the artist's own ankle" % tag,
                  abs(cut - ankle) <= CUT_TOLERANCE_MM,
                  "cut %.1f mm vs joint %.1f mm" % (cut, ankle))
            note("%s ankle joint %.1f mm (artist moved it %.0f mm), cut %.1f mm"
                 % (tag, ankle, drop, cut))

    # And nothing in the split writes to the rig at all.
    before = {name: head_mm(rig, name) for name in sorted(rig.data.bones.keys())}
    rigforge_skin.body_split(mesh, regions, rig, metarig)
    after = {name: head_mm(rig, name) for name in sorted(rig.data.bones.keys())}
    drifted = sorted(name for name in before
                     if abs((before[name] or 0.0) - (after[name] or 0.0)) > 1e-6)
    check("measuring the split moves no joint of the rig", not drifted,
          str(drifted[:6]))


# --- 5: a trunk too coarse to carry the cut refuses it ------------------------

def test_a_coarse_trunk_refuses_the_cut(mesh, rig, metarig, regions):
    """The guard that keeps a slab from being nothing but blend band."""
    from forge.tools import rigforge_autotag, rigforge_skin

    section("a trunk too coarse for the cut falls back rather than forcing it")
    parent = rigforge_autotag.SPLIT_PARENT
    spacing = rigforge_skin.tag_ring_spacing(mesh, parent)
    check("the trunk's own ring spacing is measurable", spacing > 0.0,
          str(spacing))
    floor = (rigforge_skin.MIN_SLAB_BANDS * rigforge_skin.MIN_ARTICULATION_RINGS
             * spacing)
    note("this trunk is sampled every %.1f mm, so a slab must be %.0f mm to have "
         "an interior once its bands are cut" % (spacing * 1000.0, floor * 1000.0))

    # Drive the rule directly: a trunk sampled ten times more coarsely cannot
    # carry the cut, and must say so rather than return slabs that are all band.
    region = regions.get(parent)
    owner, _source = rigforge_skin.bone_owners(rig, metarig, regions)
    root, groups = rigforge_skin.chain_groups(rig, owner, parent, metarig)
    check("the trunk's own chain is readable", root is not None and len(groups) >= 2,
          str(len(groups)))
    if root is None or len(groups) < 2:
        return
    names = rigforge_autotag.torso_sub_tags(base for base, _p in groups)
    joints = [(groups[index][0], groups[index][1])
              for index in range(1, len(groups))]
    report, axis = rigforge_autotag.split_from_torso_cloud(
        parent, region._points, root, joints, names, seed=Vector(region.axis),
        min_slab=floor * 10.0)
    check("a trunk too coarse for the cut refuses it", bool(report.get("refused")),
          str(report.get("names")))
    check("and says so in terms of the slab length it could not reach",
          "interior" in str(report.get("refused", "")),
          str(report.get("refused")))
    note(str(report.get("refused")))


# --- main --------------------------------------------------------------------

def main():
    print("== headless_torsosubtags ==")
    if not os.path.exists(SOURCE):
        print("  SKIP: %s is not in this checkout." % SOURCE)
        print("     It is a working asset rather than a tracked one, so this is "
              "normal on a fresh clone; the suite has nothing to measure and is "
              "not a regression.")
        print("\n0 checks, 0 failed")
        print("RESULT: SKIPPED")
        sys.exit(0)

    enable_addon()
    workspace = tempfile.mkdtemp(prefix="forge_torsosubtags_")
    copy_path = os.path.join(workspace, "werewolf-torsosubtags-copy.blend")
    try:
        shutil.copyfile(SOURCE, copy_path)
        note("testing against a copy at %s" % copy_path)
        note("the original is never opened for writing")

        mesh, rig, metarig, regions = open_copy(copy_path)
        note("%s: %d vertices, %d tags; rig %s"
             % (mesh.name, len(mesh.data.vertices), len(regions), rig.name))
        split = test_trunk_is_cut_at_its_own_joints(mesh, rig, metarig, regions)
        if split is not None:
            test_the_seam_bounds_what_it_lends(mesh, rig, metarig, regions, split)
            test_every_band_carries_a_falloff(mesh, rig, metarig, regions, split)
            test_artist_edits_are_law(mesh, rig, metarig, regions, split)
        test_a_coarse_trunk_refuses_the_cut(mesh, rig, metarig, regions)
        test_reskin_clears_the_trunk(mesh, rig, metarig, regions)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
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
