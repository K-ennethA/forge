"""Headless add-on tests for **cross-part mesh bridges** (``diagnose.py``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_bridges.py

No window and no port: everything is driven through ``registry.dispatch``
directly.  (Ports 9878-9914 are taken as of 2026-09-19; this suite needs none
of them.)  The first rule here is pure mesh work and needs no rig; the second
(misplaced junctions) reads the tag contract off an armature, so the checks
for it build one — ``headless_skin``'s biped and its metarig, imported rather
than rebuilt — and say so where they do.

The defect
----------
The artist's wip-15 clips show a membrane stretching between the arms and the
legs whenever they separate — *"the arms mesh or skin pulls from the legs, this
happens on every animation"*.  Every weight gate reads clean, because every
**vertex** follows its correct bone.  What is broken is the **faces**: the
sculpt had the hands resting beside the thighs, the retopo welded the touching
surfaces, and any relative motion stretches the bridging faces into a web.

What is pinned here
-------------------
1. **Red on a constructed bridge** — two boxes carrying different tags, welded
   along a shared face, are detected: the pair, the edge and face counts, the
   bridged area and a location in world millimetres.
2. **The anatomy rule, both ways** — ``Arm``/``Leg`` and ``Arm.L``/``Arm.R``
   are bridges; ``Arm``/``Torso``, ``Leg``/``Torso`` and ``Head``/``Torso`` are
   legitimate junctions and stay green; an unknown part name is reported under
   ``unclassified`` and never reddens the gate; an untagged mesh is not scanned
   at all.
3. **The gate is wired** — ``mesh_diagnose`` carries ``part_bridges``, drops
   ``clean`` to ``False`` while a bridge exists, and says so in ``verdict``
   with the count, the area and the worst location.
4. **Green after repair** — ``mesh_repair_bridges`` on the same constructed
   bridge leaves the detector clean, the mesh watertight, and every surviving
   vertex's groups and weights untouched.
5. **The guard rails** — ``dry_run`` changes nothing; a ``max_fraction`` the
   bridge exceeds refuses with a sentence saying why and leaves the mesh alone;
   the repair is deterministic (two identical inputs, identical numbers).
6. **The werewolf numbers, pinned** — on a COPY of wip-15 (the original is
   never opened for writing and never saved), the numbers this lane measured:
   :data:`WEREWOLF_EDGES` welding edges over :data:`WEREWOLF_FACES` faces and
   :data:`WEREWOLF_AREA_MM2` of bridging surface between ``Arm.R``/``Leg.R``
   and ``Arm.L``/``Leg.L``, then zero after the repair, with the tags and
   weights of every surviving vertex unchanged.
7. **Misplaced junctions** — the second category (``diagnose.JunctionRule``):
   a contact inside a pair that IS adjacent but in a place where the junction
   cannot bend. Pinned three ways: the rule's own derivation on a hand-built
   contract (no rig, no mesh); a constructed weld on the skin suite's biped,
   welded once below its shoulder (red, cut, green) and once at it (green,
   untouched); and the werewolf **wip-16** numbers — the lower-armpit weld this
   lane exists for, :data:`WEREWOLF16_MISPLACED_EDGES` edges over
   :data:`WEREWOLF16_MISPLACED_FACES` faces at z 1.17-1.38 m, with the
   legitimate shoulder contact and the whole hip seam left alone.

The werewolf blends are working assets rather than tracked ones, so a checkout
without them is normal: those checks say so and are skipped rather than failed.
"""

import os
import shutil
import sys
import tempfile
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

SOURCE = os.path.join(REPO_DIR, "projects", "werewolf", "models",
                      "werewolf-wip-15.blend")
#: The current working asset, and the one the misplaced-junction rule was
#: derived and measured against.  wip-15 stays beside it because it is the mesh
#: the ``Arm``/``Leg`` membrane was filmed on and the two categories are pinned
#: on the meshes that carry them.
SOURCE16 = os.path.join(REPO_DIR, "projects", "werewolf", "models",
                        "werewolf-wip-16.blend")
MESH_NAME = "werewolf-form-a_retopo"

#: What the detector measures on werewolf-wip-15, exactly.  Two welds per side:
#: the hand fused to the thigh (z ~ 0.85 m) and the elbow fused to the flank
#: (z ~ 1.16 m).  Pinned as equalities — this is a fixed asset and a fixed rule,
#: so a change in either of these numbers is a change in the product.
WEREWOLF_EDGES = 73
WEREWOLF_FACES = 72
WEREWOLF_AREA_MM2 = 43310.71
#: The repair's own numbers on that mesh.  **Re-pinned 2026-09-19** from 77/10:
#: the repair now cuts the misplaced junctions on the same pass, and wip-15 has
#: the same lower-armpit weld wip-16 does (:data:`WEREWOLF_MISPLACED_EDGES`
#: edges on top of the 73 ``Arm``/``Leg`` ones).  The membrane numbers above are
#: unchanged, which is the point: the second category added work, it did not
#: move the first one's answer.
WEREWOLF_FACES_REMOVED = 162
WEREWOLF_RIMS = 11
#: The misplaced-junction half of wip-15's welds.
WEREWOLF_MISPLACED_EDGES = 84

#: werewolf-wip-16, the mesh this lane's rule was derived on.  The whole defect
#: is the arm fused to the ribcage below its own shoulder: 45 edges on the left
#: (``Torso.spine.002`` 35 + ``.001`` 9, and one more face than edges) and 44 on
#: the right (37 + 8).  There is no ``Arm``/``Leg`` membrane left on this wip,
#: so the first category reads zero and every number here is the second one.
WEREWOLF16_MISPLACED_EDGES = 89
WEREWOLF16_MISPLACED_FACES = 93
WEREWOLF16_MISPLACED_AREA_MM2 = 58552.73
WEREWOLF16_FACES_REMOVED = 94
WEREWOLF16_RIMS = 5
#: The height band the weld sits in, in world millimetres — 300 mm below the
#: shoulder joint the arm actually hangs from.
WEREWOLF16_Z_LOW = 1100.0
WEREWOLF16_Z_HIGH = 1400.0

#: Area comparisons are in square millimetres on a metre-scale mesh, so an
#: absolute tolerance of a hundredth of a square millimetre is the float noise
#: floor rather than a fudge.
AREA_EPSILON = 0.01

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


def command(name, **params):
    """Dispatch a Forge command and fail loudly rather than returning junk."""
    from forge.tools import registry

    status, result, message = registry.dispatch(name, params)
    if status != "success":
        raise AssertionError("%s failed: %s" % (name, message))
    return result


def dispatch(name, **params):
    """Like :func:`command` but hands back the error instead of raising."""
    from forge.tools import registry

    return registry.dispatch(name, params)


# ---------------------------------------------------------------------------
# the constructed bridge
# ---------------------------------------------------------------------------

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


#: Segments per side of the constructed fixture, and the side of one segment.
#:
#: The count is not decoration.  The repair refuses when a bridge would take
#: more than :data:`~forge.tools.diagnose.BRIDGE_REPAIR_MAX_FRACTION` (5%) of a
#: part's faces, so a fixture whose parts are a handful of faces each can only
#: ever test the refusal.  Thirty segments gives each part 125 faces against a
#: four-face weld — 3.2%, comfortably under the ceiling — so the same fixture
#: proves the repair AND (with an explicit ``max_fraction``) the refusal.
#: 20 mm segments keep the whole thing 1.2 m long, inside the size band
#: ``scale_report`` is happy with, so nothing else in the report goes red.
FIXTURE_SEGMENTS = 30
FIXTURE_SIZE = 0.02

#: Faces the weld is expected to cost, and the area they add up to.
FIXTURE_BRIDGE_FACES = 4
FIXTURE_BRIDGE_AREA_MM2 = 4 * (FIXTURE_SIZE * 1000.0) ** 2


def build_welded_pair(name, tag_a, tag_b, segments=FIXTURE_SEGMENTS,
                      size=FIXTURE_SIZE):
    """One square tube along X, welded in the middle, a tag on each half.

    This is the defect in its simplest honest form: the geometry is continuous
    across the join, every vertex is unambiguously in one part, the four quads
    straddling the join are the membrane and the four long edges through them
    are the welds.  Built by hand rather than with a boolean or a modifier, so
    the vertex order — and therefore every number this suite pins — is the same
    on every run and every machine.
    """
    half = size / 2.0
    columns = 2 * segments + 1
    verts = []
    for column in range(columns):
        x = (column - segments) * size
        for y, z in ((-half, -half), (-half, half), (half, half), (half, -half)):
            verts.append((x, y, z))

    faces = [(0, 3, 2, 1)]  # the -X cap
    for column in range(columns - 1):
        low = column * 4
        high = low + 4
        for corner in range(4):
            nxt = (corner + 1) % 4
            faces.append((low + corner, high + corner, high + nxt, low + nxt))
    last = (columns - 1) * 4
    faces.append((last + 0, last + 1, last + 2, last + 3))  # the +X cap

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    group_a = obj.vertex_groups.new(name="tag_" + tag_a)
    group_b = obj.vertex_groups.new(name="tag_" + tag_b)
    split = (segments + 1) * 4  # columns 0..segments are A, the rest are B
    group_a.add(list(range(split)), 1.0, "REPLACE")
    group_b.add(list(range(split, len(verts))), 1.0, "REPLACE")
    # A deform-style group as well, so "weights preserved" has something to
    # preserve that is not a tag. Weights stay inside [0, 1] so Blender does not
    # clamp them into being equal.
    deform = obj.vertex_groups.new(name="DEF-probe")
    for index in range(len(verts)):
        deform.add([index], ((index % 9) + 1) / 10.0, "REPLACE")
    return obj


def group_snapshot(obj):
    """``{rounded position: ((group name, weight), ...)}`` for every vertex."""
    names = {group.index: group.name for group in obj.vertex_groups}
    out = {}
    for vertex in obj.data.vertices:
        key = tuple(round(float(value), 6) for value in vertex.co)
        out[key] = tuple(sorted((names[entry.group], round(float(entry.weight), 6))
                                for entry in vertex.groups))
    return out


def mesh_signature(obj):
    """Vertex/edge/face counts plus a positional hash — for determinism."""
    digest = 0
    for vertex in obj.data.vertices:
        for value in vertex.co:
            digest = (digest * 1000003 + int(round(float(value) * 1e6))) % (2 ** 61 - 1)
    for polygon in obj.data.polygons:
        for index in polygon.vertices:
            digest = (digest * 1000003 + int(index)) % (2 ** 61 - 1)
    return (len(obj.data.vertices), len(obj.data.edges),
            len(obj.data.polygons), digest)


# ---------------------------------------------------------------------------
# 1 + 2. the detector and the anatomy rule
# ---------------------------------------------------------------------------

def test_detector_red_on_a_constructed_bridge():
    section("red on a constructed bridge (two boxes, Arm.L welded to Leg.L)")
    clear_scene()
    obj = build_welded_pair("Welded", "Arm.L", "Leg.L")
    result = command("mesh_diagnose", object=obj.name, examples=5)
    bridges = result["part_bridges"]
    note("edges=%d faces=%d area=%.3f mm2 places=%s"
         % (bridges["count"], bridges["faces"], bridges["area_mm2"],
            [place["location_mm"] for pair in bridges["pairs"]
             for place in pair["places"]]))

    check("the scan ran", bridges.get("scanned") is True, bridges.get("note"))
    check("it found the weld", bridges["count"] == 4, bridges["count"])
    check("and the faces that carry it", bridges["faces"] == 4, bridges["faces"])
    check("with a bridged area, not just a count", bridges["area_mm2"] > 0.0,
          bridges["area_mm2"])
    check("named as Arm.L / Leg.L",
          [pair["parts"] for pair in bridges["pairs"]] == [["Arm.L", "Leg.L"]],
          [pair["parts"] for pair in bridges["pairs"]])
    check("in one place, not four",
          bridges["pairs"][0]["place_count"] == 1,
          bridges["pairs"][0]["place_count"])

    # The weld sits one segment either side of x = 0, so its location has to as
    # well — anywhere else and the report would be pointing at the wrong end of
    # a 1.2 m tube.
    place = bridges["pairs"][0]["places"][0]
    check("located in world millimetres at the weld",
          abs(place["location_mm"][0]) <= FIXTURE_SIZE * 1000.0,
          place["location_mm"])
    check("and not somewhere else on the tube",
          abs(place["location_mm"][1]) < 1.0 and abs(place["location_mm"][2]) < 1.0,
          place["location_mm"])
    check("the example list carries the same place",
          bridges["examples"] and
          bridges["examples"][0]["location_mm"] == place["location_mm"],
          bridges.get("examples"))

    check("the area is the four straddling faces",
          abs(bridges["area_mm2"] - FIXTURE_BRIDGE_AREA_MM2) < AREA_EPSILON,
          (bridges["area_mm2"], FIXTURE_BRIDGE_AREA_MM2))
    return obj


def test_the_anatomy_rule():
    section("the anatomy rule: which pairs are a bridge and which are a seam")
    from forge.tools import diagnose

    cases = [
        (("Arm.L", "Leg.L"), False, "a hand welded to a thigh"),
        (("Arm.R", "Leg.L"), False, "and across the midline too"),
        (("Arm.L", "Arm.R"), False, "two arms are never one surface"),
        (("Leg.L", "Leg.R"), False, "nor two legs"),
        (("Head", "Leg.R"), False, "a head does not touch a leg"),
        (("Arm.L", "Torso"), True, "the shoulder is a real junction"),
        (("Leg.R", "Torso"), True, "so is the hip"),
        (("Head", "Torso"), True, "so is the neck"),
        (("Head", "Arm.L"), True, "and the trapezius meets the deltoid"),
        (("Torso", "Torso"), True, "a part is adjacent to itself"),
        (("Arm.L", "Arm.L"), True, "including a sided one"),
    ]
    for (one, other), expected, why in cases:
        check("%s <-> %s: %s" % (one, other, why),
              diagnose.tags_adjacent(one, other) is expected,
              diagnose.tags_adjacent(one, other))
    check("an unknown part has no opinion, rather than a wrong one",
          diagnose.tags_adjacent("Wing.L", "Leg.R") is None,
          diagnose.tags_adjacent("Wing.L", "Leg.R"))
    check("tag_ prefixed names work the same",
          diagnose.tags_adjacent("tag_Arm.L", "tag_Leg.L") is False)
    check("split_tag reads a side off", diagnose.split_tag("Arm.L") == ("Arm", "L"),
          diagnose.split_tag("Arm.L"))
    check("and leaves an unsided name alone",
          diagnose.split_tag("Torso") == ("Torso", ""), diagnose.split_tag("Torso"))


def test_a_legitimate_junction_stays_green():
    section("green on a legitimate junction (Arm.L welded to Torso)")
    clear_scene()
    obj = build_welded_pair("Junction", "Arm.L", "Torso")
    result = command("mesh_diagnose", object=obj.name)
    bridges = result["part_bridges"]
    check("the scan ran", bridges.get("scanned") is True)
    check("no bridge between a shoulder and a torso", bridges["count"] == 0,
          bridges["count"])
    check("and no bridging faces either", bridges["faces"] == 0, bridges["faces"])
    check("so the gate does not redden on it",
          not any("weld body parts" in line for line in result["verdict"]),
          result["verdict"])


def test_the_side_rule():
    section("the side rule (Arm.L welded to Arm.R)")
    clear_scene()
    obj = build_welded_pair("Mirror", "Arm.L", "Arm.R")
    bridges = command("mesh_diagnose", object=obj.name)["part_bridges"]
    check("a left arm welded to a right arm is a bridge", bridges["count"] == 4,
          bridges["count"])
    check("named as such",
          [pair["parts"] for pair in bridges["pairs"]] == [["Arm.L", "Arm.R"]],
          [pair["parts"] for pair in bridges["pairs"]])


def test_unknown_parts_do_not_gate():
    section("an unknown part name is reported, never gated on")
    clear_scene()
    obj = build_welded_pair("Custom", "Wing.L", "Leg.L")
    result = command("mesh_diagnose", object=obj.name)
    bridges = result["part_bridges"]
    check("nothing is called a bridge", bridges["count"] == 0, bridges["count"])
    check("but the pair is surfaced",
          [entry["parts"] for entry in bridges["unclassified"]]
          == [["Leg.L", "Wing.L"]], bridges["unclassified"])
    check("with its edge count", bridges["unclassified"][0]["edges"] == 4,
          bridges["unclassified"])


def test_untagged_mesh_is_not_scanned():
    section("an untagged mesh has no anatomy to check")
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=0.2)
    obj = bpy.context.active_object
    obj.name = "Bare"
    result = command("mesh_diagnose", object=obj.name)
    bridges = result["part_bridges"]
    check("the scan says it did not run", bridges.get("scanned") is False,
          bridges.get("scanned"))
    check("and says why", "tag_*" in str(bridges.get("note", "")),
          bridges.get("note"))
    check("no bridges are invented", bridges["count"] == 0, bridges["count"])
    check("and a clean cube is still clean", result["clean"] is True,
          result["verdict"])


# ---------------------------------------------------------------------------
# 3. the gate
# ---------------------------------------------------------------------------

def test_the_gate_is_wired():
    section("the gate: mesh_diagnose reddens and says where")
    clear_scene()
    obj = build_welded_pair("Gated", "Arm.L", "Leg.L")
    result = command("mesh_diagnose", object=obj.name)
    check("part_bridges rides in the report", "part_bridges" in result)
    check("clean is False while a bridge exists", result["clean"] is False)
    lines = [line for line in result["verdict"] if "weld body parts" in line]
    check("the verdict says it, first", bool(lines) and lines[0] == result["verdict"][0],
          result["verdict"])
    if lines:
        note(lines[0])
        check("quoting the face count", "4 faces weld" in lines[0], lines[0])
        check("quoting the area", "mm2" in lines[0], lines[0])
        check("quoting the worst location", "around" in lines[0], lines[0])
        check("and naming the two parts",
              "Arm.L and Leg.L" in lines[0], lines[0])

    # The same mesh minus the tags must be clean, which proves the bridge is the
    # only thing reddening it and the two boxes are otherwise a fine mesh.
    for group in list(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    again = command("mesh_diagnose", object=obj.name)
    check("without tags the same geometry is clean", again["clean"] is True,
          again["verdict"])


# ---------------------------------------------------------------------------
# 4 + 5. the repair
# ---------------------------------------------------------------------------

def test_repair_clears_the_constructed_bridge():
    section("green after repair, on the constructed bridge")
    clear_scene()
    obj = build_welded_pair("Repairable", "Arm.L", "Leg.L")
    before_groups = group_snapshot(obj)
    before_counts = (len(obj.data.vertices), len(obj.data.polygons))

    dry = command("mesh_repair_bridges", object=obj.name, dry_run=True)
    check("the dry run plans the cut", dry["faces_removed"] == 4,
          dry["faces_removed"])
    check("and changes nothing",
          (len(obj.data.vertices), len(obj.data.polygons)) == before_counts,
          (len(obj.data.vertices), len(obj.data.polygons)))
    check("and says it was a dry run", dry["dry_run"] is True)

    result = command("mesh_repair_bridges", object=obj.name)
    note(result["message"])
    check("it repaired", result["repaired"] is True)
    check("removing the four straddling faces", result["faces_removed"] == 4,
          result["faces_removed"])
    check("and the four welding edges", result["edges_removed"] == 4,
          result["edges_removed"])
    check("closing every rim it opened",
          result["holes_filled"] == result["rims"] and result["rims"] > 0,
          (result["holes_filled"], result["rims"]))
    check("with no rim refused", not result["holes_refused"],
          result["holes_refused"])

    after = command("mesh_diagnose", object=obj.name)
    check("the detector is clean afterwards",
          after["part_bridges"]["count"] == 0 and after["part_bridges"]["faces"] == 0,
          after["part_bridges"])
    check("and the repair agrees", result["clean"] is True)
    check("the mesh is sealed again", after["topology"]["watertight"] is True,
          after["topology"])
    check("the verdict no longer mentions a weld",
          not any("weld body parts" in line for line in after["verdict"]),
          after["verdict"])

    after_groups = group_snapshot(obj)
    changed = [key for key in after_groups
               if before_groups.get(key) != after_groups[key]]
    check("every surviving vertex kept its tags and its weights", not changed,
          changed[:3])
    check("no vertex was invented", set(after_groups) <= set(before_groups),
          sorted(set(after_groups) - set(before_groups))[:3])


def test_repair_is_deterministic():
    section("the repair is deterministic")
    signatures = []
    for _ in range(2):
        clear_scene()
        obj = build_welded_pair("Twice", "Arm.L", "Leg.L")
        command("mesh_repair_bridges", object=obj.name)
        signatures.append(mesh_signature(obj))
    check("two identical inputs give one identical mesh",
          signatures[0] == signatures[1], signatures)


def test_repair_refuses_when_the_bridge_is_too_big():
    section("the refusal: a bridge bigger than max_fraction is not auto-cut")
    clear_scene()
    obj = build_welded_pair("TooBig", "Arm.L", "Leg.L")
    before = mesh_signature(obj)
    result = command("mesh_repair_bridges", object=obj.name, max_fraction=0.001)
    check("it refused", result.get("refused") is True, result.get("message"))
    check("it did not repair", result["repaired"] is False)
    check("and said why, with the numbers",
          "above the" in result["message"] and "%" in result["message"],
          result["message"])
    note(result["message"])
    check("the mesh is untouched", mesh_signature(obj) == before)
    check("the share it measured is reported",
          bool(result.get("part_shares")), result.get("part_shares"))

    # And the same call with the default ceiling goes through, so the refusal is
    # the threshold talking rather than the repair being broken.
    ok = command("mesh_repair_bridges", object=obj.name)
    check("the default ceiling lets the same repair through",
          ok["repaired"] is True and ok["clean"] is True, ok.get("message"))


def test_repair_on_a_clean_mesh_is_a_no_op():
    section("nothing to repair says so")
    clear_scene()
    obj = build_welded_pair("Fine", "Arm.L", "Torso")
    before = mesh_signature(obj)
    result = command("mesh_repair_bridges", object=obj.name)
    check("it reports no bridges", result["repaired"] is False,
          result.get("message"))
    check("with a sentence rather than a silence",
          "No cross-part bridges" in result["message"], result["message"])
    check("and leaves the mesh alone", mesh_signature(obj) == before)


def test_bad_parameters():
    section("parameter validation")
    clear_scene()
    obj = build_welded_pair("Params", "Arm.L", "Leg.L")
    for params, fragment in (
        ({"object": obj.name, "examples": 0}, ">="),
        ({"object": obj.name, "examples": 99}, "<="),
        ({"object": obj.name, "max_fraction": 2.0}, "<="),
        ({"object": "Nessie"}, "No object named"),
    ):
        status, _result, message = dispatch("mesh_repair_bridges", **params)
        check("mesh_repair_bridges %s is refused with one sentence" % params,
              status == "error" and fragment in message, message)
    check("mesh_repair_bridges is NOT read-only (it gets an undo checkpoint)",
          "mesh_repair_bridges" not in _read_only_commands(),
          "it is in READ_ONLY_COMMANDS")
    check("mesh_diagnose still is",
          "mesh_diagnose" in _read_only_commands())


def _read_only_commands():
    from forge.tools import registry

    return registry.READ_ONLY_COMMANDS


# ---------------------------------------------------------------------------
# 7. misplaced junctions — the rule, on a hand-built contract
# ---------------------------------------------------------------------------

def _rule(hinges, legal, membership=None):
    """A :class:`JunctionRule` over a two-slab trunk, with no rig and no mesh."""
    from forge.tools import diagnose

    return diagnose.JunctionRule(
        membership or {},
        {"Torso.spine": "Torso", "Torso.spine.001": "Torso"},
        hinges, legal)


def test_the_junction_rule():
    """The derivation, stated as cases, so a change to it has to be deliberate.

    The trunk here is two slabs: ``Torso.spine`` owns the hip bone ``H`` and
    ``Torso.spine.001`` owns the shoulder bone ``S`` and hinges on ``H``.  That
    is the werewolf's shape in miniature — a limb hanging off the top slab, and
    a lower slab the hip bone still reaches.
    """
    section("the misplaced-junction rule, on a hand-built contract")
    hinges = {"Arm.L": {"S"}, "Leg.L": {"H"}, "Torso.spine.001": {"H"}}
    legal = {
        "Arm.L": {"UA", "S"},
        "Leg.L": {"TH", "H"},
        "Torso.spine": {"H", "P"},
        "Torso.spine.001": {"S", "H"},
    }
    rule = _rule(hinges, legal)

    check("an arm on the slab that OWNS its shoulder bone is a junction",
          rule.misplaced("Arm.L", "Torso.spine.001") is False)
    check("the same arm on the slab below it is misplaced",
          rule.misplaced("Arm.L", "Torso.spine") is True)
    check("a leg on the slab that owns the hips is a junction",
          rule.misplaced("Leg.L", "Torso.spine") is False)
    check("and on the slab ABOVE it too, because that slab hinges on the same "
          "hip bone — legality, not ownership, is the line",
          rule.misplaced("Leg.L", "Torso.spine.001") is False)
    check("a contact with no slab granularity gets no opinion (the trapezius)",
          rule.misplaced("Arm.L", "Head") is False)
    check("nor does one inside a single part",
          rule.misplaced("Torso.spine", "Torso.spine.001") is False)

    # No anchor: an arm whose hinge resolves to nothing the trunk carries. The
    # rule then has no right place to measure a wrong one against and keeps
    # quiet, rather than reddening the whole seam.
    orphan = _rule({"Arm.L": {"GHOST"}}, legal)
    check("with no legitimate place anywhere, no place is called wrong",
          orphan.misplaced("Arm.L", "Torso.spine") is False
          and orphan.anchored("Arm.L", "Torso") is False)
    check("and with one, the rule is back on",
          rule.anchored("Arm.L", "Torso") is True)

    check("merged() folds a slab back to its tag",
          rule.merged("Torso.spine.001") == "Torso"
          and rule.merged("Arm.L") == "Arm.L")
    check("refine() only replaces a tag with a slab OF that tag",
          _rule(hinges, legal, {7: "Torso.spine"}).refine(7, "Arm.L") == "Arm.L",
          "a Torso slab must not be handed to an Arm vertex")


# ---------------------------------------------------------------------------
# 7b. misplaced junctions — a constructed weld on a real rigged figure
# ---------------------------------------------------------------------------

def build_rigged_biped():
    """The skin suite's hanging-arm biped, tagged, rigged and bound.

    Imported rather than rebuilt: it is the one synthetic figure in the repo
    whose metarig carries the real parent tree and the ``forge_tag_bones``
    mapping, which is exactly what ``legal_bone_sets`` (and so this rule) has
    to read.  Its arms hang 100 mm clear of the flank and touch nothing, so any
    cross-part contact on it is one this suite put there on purpose.
    """
    import headless_autotag
    import headless_skin
    from forge.tools import rigforge_autotag

    clear_scene()
    mesh = headless_skin.build_fine_biped()
    rigforge_autotag.auto_tag(mesh, joints=headless_autotag.canned_document(),
                             midplane=0.0, character_left=1.0, apply=True)
    metarig = headless_skin.build_metarig()
    rig = headless_skin.build_deform_rig(metarig)
    headless_skin.bind(mesh, rig, method="DISTANCE")
    bpy.context.view_layer.objects.active = mesh
    return mesh, rig


def weld_to_slab(mesh, rig, limb, slab):
    """Fuse ``limb``'s nearest flesh to ``slab``'s with one quad. Returns its verts.

    The smallest honest weld: two existing limb vertices joined to the two
    trunk vertices nearest them, so the geometry is continuous across a
    junction that should not be one and nothing else about the figure moves.
    """
    import bmesh
    from forge.tools import diagnose

    dominant, _groups = diagnose.dominant_tags(mesh)
    rule, skipped = diagnose.junction_rule(mesh, rig)
    if rule is None:
        return None, skipped
    matrix = mesh.matrix_world
    verts = mesh.data.vertices
    neighbours = {}
    for edge in mesh.data.edges:
        a, b = edge.vertices
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    mine = [v.index for v in verts if dominant.get(v.index) == limb]
    theirs = [v.index for v in verts
              if rule.refine(v.index, dominant.get(v.index) or "") == slab]
    if not mine or not theirs:
        return None, "no %s or no %s on this figure" % (limb, slab)
    pair = min(((matrix @ verts[a].co - matrix @ verts[b].co).length, a, b)
               for a in mine for b in theirs)
    _gap, a0, t0 = pair
    partner = min(
        ((matrix @ verts[a].co - matrix @ verts[b].co).length, a, b)
        for a in neighbours.get(a0, ()) if dominant.get(a) == limb
        for b in neighbours.get(t0, ())
        if rule.refine(b, dominant.get(b) or "") == slab)
    _gap, a1, t1 = partner

    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    bm.verts.ensure_lookup_table()
    try:
        bm.faces.new((bm.verts[a0], bm.verts[a1], bm.verts[t1], bm.verts[t0]))
    except ValueError as error:
        bm.free()
        return None, str(error)
    bm.to_mesh(mesh.data)
    bm.free()
    mesh.data.update()
    return (a0, a1, t0, t1), ""


def test_constructed_misplaced_weld():
    section("a constructed misplaced junction: red, cut, green")
    from forge.tools import diagnose

    mesh, rig = build_rigged_biped()
    rule, skipped = diagnose.junction_rule(mesh, rig)
    check("the slab view comes off the rig", rule is not None, skipped)
    if rule is None:
        return
    note("slabs: %s" % (rule.slabs,))

    trunk = [name for name in rule.slabs if rule.merged(name) == "Torso"]
    shoulder = [name for name in trunk if not rule.misplaced("Arm.L", name)]
    below = [name for name in trunk if rule.misplaced("Arm.L", name)]
    check("the contract names the slab the arm hangs from", len(shoulder) >= 1,
          trunk)
    check("and at least one it does not", bool(below), trunk)
    if not (shoulder and below):
        return
    note("Arm.L articulates with %s, not with %s" % (shoulder, below))

    # The baseline, not a zero. This figure is a bag of separate tubes whose
    # caps put two corner faces across the midline at the crotch and the
    # shoulders, and its tag adjacency has always read that way — so what is
    # asserted below is that the weld changes nothing except the weld.
    baseline = command("mesh_diagnose", object=mesh.name)["part_bridges"]
    note("baseline: count=%d faces=%d (%d corner) misplaced=%d"
         % (baseline["count"], baseline["faces"],
            baseline["corner_faces"]["count"], baseline["misplaced_edges"]))
    check("the untouched figure carries no misplaced junction",
          baseline["misplaced_edges"] == 0, baseline["misplaced"])
    before_counts = mesh_signature(mesh)[:3]
    before_groups = group_snapshot(mesh)

    # --- the legitimate junction first: welded AT the shoulder slab, green ---
    made, why = weld_to_slab(mesh, rig, "Arm.L", shoulder[0])
    check("a weld at the shoulder slab was constructed", made is not None, why)
    legit = command("mesh_diagnose", object=mesh.name)["part_bridges"]
    check("contact on the slab the arm hangs from is NOT flagged",
          legit["misplaced_edges"] == 0 and legit["count"] == 0,
          (legit["misplaced_edges"], legit["misplaced"]))

    # --- and the same weld one slab down, red -------------------------------
    mesh, rig = build_rigged_biped()
    made, why = weld_to_slab(mesh, rig, "Arm.L", below[0])
    check("a weld below the shoulder slab was constructed", made is not None, why)
    if made is None:
        return
    result = command("mesh_diagnose", object=mesh.name)
    bridges = result["part_bridges"]
    note("misplaced: %d edges / %d faces, pairs %s"
         % (bridges["misplaced_edges"], bridges["misplaced_faces"],
            [row["parts"] for row in bridges["misplaced"]]))
    check("the detector finds it", bridges["misplaced_edges"] == 2,
          bridges["misplaced_edges"])
    check("and the face that carries it", bridges["misplaced_faces"] == 1,
          bridges["misplaced_faces"])
    check("named by the SLAB, not by the part",
          [row["parts"] for row in bridges["misplaced"]]
          == [sorted(["Arm.L", below[0]])],
          [row["parts"] for row in bridges["misplaced"]])
    check("in one place, with an area and a location",
          bridges["misplaced"][0]["place_count"] == 1
          and bridges["misplaced"][0]["area_mm2"] > 0.0
          and len(bridges["misplaced_examples"][0]["location_mm"]) == 3,
          bridges["misplaced"][0])
    check("the first category does not move — this is not a bridge",
          bridges["count"] == baseline["count"]
          and bridges["faces"] == baseline["faces"],
          (bridges["count"], bridges["faces"]))
    check("the gate is red", result["clean"] is False)
    lines = [line for line in result["verdict"] if "not here" in line]
    check("and the verdict says where and why", bool(lines), result["verdict"])
    if lines:
        note(lines[0])

    repaired = command("mesh_repair_bridges", object=mesh.name)
    note(repaired["message"])
    check("the repair cut it", repaired["repaired"] is True
          and repaired["faces_removed"] == 1, repaired.get("faces_removed"))
    after = command("mesh_diagnose", object=mesh.name)
    check("the detector is clean of the category afterwards",
          after["part_bridges"]["misplaced_edges"] == 0
          and after["part_bridges"]["count"] == baseline["count"]
          and after["part_bridges"]["faces"] == baseline["faces"],
          after["part_bridges"])
    check("and the verdict stops saying it",
          not any("not here" in line for line in after["verdict"]),
          after["verdict"])
    check("the figure is back to the counts we started from",
          mesh_signature(mesh)[:3] == before_counts,
          (mesh_signature(mesh)[:3], before_counts))
    check("with every vertex's tags and weights untouched",
          group_snapshot(mesh) == before_groups)


def test_no_rig_means_no_opinion():
    section("no rig: the category is skipped out loud, never guessed at")
    mesh, rig = build_rigged_biped()
    made, why = weld_to_slab(mesh, rig, "Arm.L", [
        name for name in _slabs(mesh, rig) if "Torso" in name][0])
    # Drop the binding so the mesh cannot find an armature at all.
    for modifier in list(mesh.modifiers):
        mesh.modifiers.remove(modifier)
    mesh.parent = None
    result = command("mesh_diagnose", object=mesh.name)
    bridges = result["part_bridges"]
    check("the junction scan says it did not run",
          bridges["junction"]["scanned"] is False, bridges["junction"])
    check("and says why, in a sentence with the word rig in it",
          "armature" in str(bridges["junction"].get("note", "")),
          bridges["junction"].get("note"))
    check("the note reaches the report's own notes",
          any("misplaced-junction" in line for line in result["notes"]),
          result["notes"])
    check("nothing is called misplaced without the contract to say so",
          bridges["misplaced_edges"] == 0, bridges["misplaced"])
    check("the tag-adjacency category still runs", bridges["scanned"] is True)
    # And naming a rig explicitly brings it back.
    named = command("mesh_diagnose", object=mesh.name, rig=rig.name)
    check("naming the rig turns the category back on",
          named["part_bridges"]["junction"]["scanned"] is True
          and named["part_bridges"]["misplaced_edges"] > 0,
          named["part_bridges"]["misplaced_edges"])
    status, _result, message = dispatch("mesh_diagnose", object=mesh.name,
                                        rig="Nessie")
    check("and a rig name that is a typo is refused with one sentence",
          status == "error" and "No object named" in message, message)


def _slabs(mesh, rig):
    from forge.tools import diagnose

    rule, _skipped = diagnose.junction_rule(mesh, rig)
    return [name for name in (rule.slabs if rule else ())
            if rule.misplaced("Arm.L", name)]


# ---------------------------------------------------------------------------
# 6. the werewolf
# ---------------------------------------------------------------------------

def test_werewolf_numbers(copy_path):
    section("werewolf-wip-15: the membrane the artist filmed, measured")
    bpy.ops.wm.open_mainfile(filepath=copy_path)
    obj = bpy.data.objects.get(MESH_NAME)
    if obj is None:
        check("the copy has %r" % MESH_NAME, False,
              sorted(o.name for o in bpy.data.objects)[:5])
        return
    bpy.context.view_layer.objects.active = obj
    before_groups = group_snapshot(obj)

    result = command("mesh_diagnose", object=obj.name, examples=8)
    bridges = result["part_bridges"]
    note("%d welding edges over %d faces, %.2f mm2, in %d pair(s)"
         % (bridges["count"], bridges["faces"], bridges["area_mm2"],
            len(bridges["pairs"])))
    for pair in bridges["pairs"]:
        for place in pair["places"]:
            note("   %s at %s mm  (%d faces, %.1f mm2)"
                 % (" / ".join(pair["parts"]), place["location_mm"],
                    place["faces"], place["area_mm2"]))

    check("the welding edges are pinned", bridges["count"] == WEREWOLF_EDGES,
          bridges["count"])
    check("the bridging faces are pinned", bridges["faces"] == WEREWOLF_FACES,
          bridges["faces"])
    check("the bridged area is pinned",
          abs(bridges["area_mm2"] - WEREWOLF_AREA_MM2) < AREA_EPSILON,
          bridges["area_mm2"])
    check("both sides are named",
          sorted(tuple(pair["parts"]) for pair in bridges["pairs"])
          == [("Arm.L", "Leg.L"), ("Arm.R", "Leg.R")],
          [pair["parts"] for pair in bridges["pairs"]])
    check("and the gate is red on it", result["clean"] is False)
    check("with the membrane named in the verdict, first",
          "weld body parts" in result["verdict"][0], result["verdict"][0])

    # This wip carries the lower-armpit weld too, on top of the membrane. It is
    # pinned here rather than only on wip-16 because the two categories have to
    # coexist on one mesh without either moving the other's answer.
    note("misplaced as well: %d edges over %d faces in %s"
         % (bridges["misplaced_edges"], bridges["misplaced_faces"],
            [row["parts"] for row in bridges["misplaced"]]))
    check("the second category sees the arm root on this wip as well",
          bridges["misplaced_edges"] == WEREWOLF_MISPLACED_EDGES,
          bridges["misplaced_edges"])
    check("and it did not disturb the membrane's own numbers",
          bridges["count"] == WEREWOLF_EDGES, bridges["count"])

    # The hands-beside-thighs weld is the big one and it is where the artist
    # says it is: low on the outside of the figure, not up at the shoulder.
    worst = bridges["examples"][0]
    note("worst place: %s mm between %s"
         % (worst["location_mm"], " and ".join(worst["parts"])))
    check("the worst weld is at hand/thigh height, not at the shoulder",
          700.0 < worst["location_mm"][2] < 1000.0, worst["location_mm"])
    check("and out at the side of the figure",
          abs(worst["location_mm"][0]) > 100.0, worst["location_mm"])

    repaired = command("mesh_repair_bridges", object=obj.name, examples=8)
    note(repaired["message"])
    check("the repair cut the membrane",
          repaired["faces_removed"] == WEREWOLF_FACES_REMOVED,
          repaired["faces_removed"])
    check("and opened the rims this lane measured",
          repaired["rims"] == WEREWOLF_RIMS, repaired["rims"])
    check("closing every one of them",
          repaired["holes_filled"] == repaired["rims"]
          and not repaired["holes_refused"],
          (repaired["holes_filled"], repaired["holes_refused"]))

    after = command("mesh_diagnose", object=obj.name, examples=8)
    check("the detector is clean afterwards",
          after["part_bridges"]["count"] == 0
          and after["part_bridges"]["faces"] == 0,
          after["part_bridges"])
    check("the verdict no longer names a weld",
          not any("weld body parts" in line for line in after["verdict"]),
          after["verdict"])
    check("and the mesh is sealed", after["topology"]["watertight"] is True,
          after["topology"])

    after_groups = group_snapshot(obj)
    changed = [key for key in after_groups
               if before_groups.get(key) != after_groups[key]]
    check("every surviving vertex kept its tags and its DEF weights",
          not changed, changed[:3])
    check("no vertex was invented", set(after_groups) <= set(before_groups))
    note("%d of %d vertices survived; %d went with the membrane"
         % (len(after_groups), len(before_groups),
            len(before_groups) - len(after_groups)))

    # Freeing a weld can set a flap loose. That is the right answer and it is
    # said out loud rather than discovered later.
    if repaired.get("detached_pieces"):
        for piece in repaired["detached_pieces"]:
            note("detached: %d vertices of %s at %s mm"
                 % (piece["vertices"], "/".join(piece["parts"]),
                    piece["location_mm"]))
        check("every piece the cut set free is reported",
              repaired["shells_after"] - repaired["shells_before"]
              == len(repaired["detached_pieces"]),
              (repaired["shells_before"], repaired["shells_after"]))


def test_werewolf_separation(copy_path):
    """The arm and the thigh are two surfaces now, measured on the rest mesh."""
    section("werewolf-wip-15: arm and thigh are separate surfaces")
    from forge.tools import diagnose

    bpy.ops.wm.open_mainfile(filepath=copy_path)
    obj = bpy.data.objects.get(MESH_NAME)
    if obj is None:
        return
    bpy.context.view_layer.objects.active = obj

    dominant, _groups = diagnose.dominant_tags(obj)
    shores = []
    for edge in obj.data.edges:
        a, b = edge.vertices
        tag_a, tag_b = dominant.get(a), dominant.get(b)
        if not (tag_a and tag_b):
            continue
        if diagnose.tags_adjacent(tag_a, tag_b) is not False:
            continue
        shores.append((tuple(round(float(c), 6) for c in obj.data.vertices[a].co),
                       tuple(round(float(c), 6) for c in obj.data.vertices[b].co)))
    check("every welded pair starts one edge apart", len(shores) == WEREWOLF_EDGES,
          len(shores))

    command("mesh_repair_bridges", object=obj.name)
    index_of = {tuple(round(float(c), 6) for c in vertex.co): vertex.index
                for vertex in obj.data.vertices}
    adjacency = {}
    for edge in obj.data.edges:
        a, b = edge.vertices
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    still_adjacent = 0
    checked = 0
    for left, right in shores:
        one, other = index_of.get(left), index_of.get(right)
        if one is None or other is None:
            continue
        checked += 1
        if other in adjacency.get(one, ()):
            still_adjacent += 1
    check("no welded pair shares an edge any more",
          checked > 0 and still_adjacent == 0, (checked, still_adjacent))
    note("%d of %d shore pairs still have both vertices; none of them touch"
         % (checked, len(shores)))

    # And the face count moved by exactly what the repair said it did, which is
    # the cheap sanity that the two surfaces really are two surfaces.
    after = command("mesh_diagnose", object=obj.name)
    note("mesh is now %d vertices / %d faces in %d piece(s)"
         % (after["vertex_count"], after["face_count"], after["loose"]["shells"]))
    check("the thigh and the hand no longer share geometry",
          after["part_bridges"]["count"] == 0)


def test_werewolf16_misplaced(copy_path):
    """The defect this lane exists for, on the current wip, measured."""
    section("werewolf-wip-16: the arm welded to the ribcage below its shoulder")
    bpy.ops.wm.open_mainfile(filepath=copy_path)
    obj = bpy.data.objects.get(MESH_NAME)
    if obj is None:
        check("the copy has %r" % MESH_NAME, False,
              sorted(o.name for o in bpy.data.objects)[:5])
        return
    bpy.context.view_layer.objects.active = obj
    before_groups = group_snapshot(obj)

    result = command("mesh_diagnose", object=obj.name, examples=10)
    bridges = result["part_bridges"]
    note("slabs: %s" % bridges["junction"]["slabs"])
    note("%d misplaced edges over %d faces, %.2f mm2, in %d pair(s)"
         % (bridges["misplaced_edges"], bridges["misplaced_faces"],
            bridges["misplaced_area_mm2"], len(bridges["misplaced"])))
    for row in bridges["misplaced"]:
        for place in row["places"]:
            note("   %s at %s mm  (%d faces, %.1f mm2)"
                 % (" / ".join(row["parts"]), place["location_mm"],
                    place["faces"], place["area_mm2"]))

    check("the junction scan ran off the mesh's own rig",
          bridges["junction"]["scanned"] is True, bridges["junction"])
    check("the misplaced edges are pinned",
          bridges["misplaced_edges"] == WEREWOLF16_MISPLACED_EDGES,
          bridges["misplaced_edges"])
    check("the faces that carry them are pinned",
          bridges["misplaced_faces"] == WEREWOLF16_MISPLACED_FACES,
          bridges["misplaced_faces"])
    check("and the area",
          abs(bridges["misplaced_area_mm2"] - WEREWOLF16_MISPLACED_AREA_MM2)
          < AREA_EPSILON, bridges["misplaced_area_mm2"])

    found = {tuple(row["parts"]): row["edges"] for row in bridges["misplaced"]}
    check("both sides, on both slabs below the shoulder",
          found == {("Arm.L", "Torso.spine.001"): 9,
                    ("Arm.L", "Torso.spine.002"): 35,
                    ("Arm.R", "Torso.spine.001"): 8,
                    ("Arm.R", "Torso.spine.002"): 37}, found)
    heights = [place["location_mm"][2] for row in bridges["misplaced"]
               for place in row["places"]]
    check("every patch is in the lower-armpit band, not at the shoulder",
          heights and all(WEREWOLF16_Z_LOW < z < WEREWOLF16_Z_HIGH
                          for z in heights), sorted(heights))

    # The two things the rule must NOT take. `Torso.spine.003` owns
    # `DEF-shoulder.*`, so the shoulder junction is anatomy; the hip seam runs
    # onto `Torso.spine.001`, which does not own the hips but hinges on them.
    check("the legitimate shoulder contact is not flagged, at all",
          not [row for row in bridges["misplaced"]
               if "Torso.spine.003" in row["parts"]],
          [row["parts"] for row in bridges["misplaced"]])
    check("nor is one millimetre of the hip seam",
          not [row for row in bridges["misplaced"]
               if any(part.startswith("Leg") for part in row["parts"])],
          [row["parts"] for row in bridges["misplaced"]])
    check("and there is no Arm/Leg membrane left on this wip to confuse it",
          bridges["count"] == 0 and bridges["faces"] == 0, bridges["pairs"])
    check("the gate is red on it", result["clean"] is False)

    repaired = command("mesh_repair_bridges", object=obj.name, examples=10)
    note(repaired["message"])
    check("the repair cut the weld",
          repaired["faces_removed"] == WEREWOLF16_FACES_REMOVED,
          repaired["faces_removed"])
    check("and opened the rims this lane measured",
          repaired["rims"] == WEREWOLF16_RIMS, repaired["rims"])
    check("closing every one of them inside its own parts",
          repaired["holes_filled"] == repaired["rims"]
          and not repaired["holes_refused"],
          (repaired["holes_filled"], repaired["holes_refused"]))
    check("the repair says it is clean", repaired["clean"] is True,
          repaired.get("message"))
    check("no piece of the figure was set free",
          repaired["shells_after"] == repaired["shells_before"] == 1,
          (repaired["shells_before"], repaired["shells_after"]))

    after = command("mesh_diagnose", object=obj.name, examples=10)
    check("the detector is clean afterwards",
          after["part_bridges"]["misplaced_edges"] == 0
          and after["part_bridges"]["count"] == 0, after["part_bridges"])
    check("the junction scan still ran, so that zero is a measurement",
          after["part_bridges"]["junction"]["scanned"] is True)
    check("and the mesh is sealed", after["topology"]["watertight"] is True,
          after["topology"])

    after_groups = group_snapshot(obj)
    changed = [key for key in after_groups
               if before_groups.get(key) != after_groups[key]]
    check("every surviving vertex kept its tags and its DEF weights",
          not changed, changed[:3])
    check("no vertex was invented", set(after_groups) <= set(before_groups))
    note("%d of %d vertices survived; %d went with the weld"
         % (len(after_groups), len(before_groups),
            len(before_groups) - len(after_groups)))

    # The production skin path over the repaired mesh: the numbers the lane's
    # report quotes, and the proof the cut left something skinnable behind.
    skin = command("rigforge_skin", object=obj.name, action="apply")
    isolation = command("rigforge_skin", object=obj.name, action="isolation")
    note(isolation["says"])
    check("an arm swing still moves the lower body by 0.00 mm",
          isolation["isolation"]["max_mm"] == 0.0
          and isolation["isolation"]["mean_mm"] == 0.0,
          isolation["isolation"])
    from forge.tools import rigforge_landmarks
    rig = bpy.data.objects.get("werewolf-form-a_retopo_rig")
    overlap = rigforge_landmarks.influence_overlap(rig, obj)
    note("stray_mass after the re-skin: %s" % overlap["stray_mass"])
    check("and stray influence is still nil",
          float(overlap["stray_mass"]) == 0.0, overlap["stray_mass"])
    check("the re-skin reported what it constrained", bool(skin.get("says")),
          skin.get("says"))


# ---------------------------------------------------------------------------

def main():
    print("== headless_bridges ==")
    enable_addon()

    try:
        test_detector_red_on_a_constructed_bridge()
        test_the_anatomy_rule()
        test_a_legitimate_junction_stays_green()
        test_the_side_rule()
        test_unknown_parts_do_not_gate()
        test_untagged_mesh_is_not_scanned()
        test_the_gate_is_wired()
        test_repair_clears_the_constructed_bridge()
        test_repair_is_deterministic()
        test_repair_refuses_when_the_bridge_is_too_big()
        test_repair_on_a_clean_mesh_is_a_no_op()
        test_bad_parameters()
        test_the_junction_rule()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("the constructed-bridge harness ran to completion", False,
              "unhandled exception")

    try:
        test_constructed_misplaced_weld()
        test_no_rig_means_no_opinion()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("the constructed misplaced-junction harness ran to completion",
              False, "unhandled exception")

    for source, tests in ((SOURCE, (test_werewolf_numbers,
                                    test_werewolf_separation)),
                          (SOURCE16, (test_werewolf16_misplaced,))):
        label = os.path.basename(source)
        if not os.path.exists(source):
            section(label)
            note("SKIP: %s is not in this checkout." % source)
            note("It is a working asset rather than a tracked one, so this is "
                 "normal on a fresh clone; those checks have nothing to measure "
                 "and are not a regression.")
            continue
        workspace = tempfile.mkdtemp(prefix="forge_bridges_")
        copy_path = os.path.join(workspace, "werewolf-bridges-copy.blend")
        try:
            shutil.copyfile(source, copy_path)
            note("testing against a copy of %s at %s" % (label, copy_path))
            note("the original is never opened for writing")
            for test in tests:
                test(copy_path)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            check("the %s harness ran to completion" % label, False,
                  "unhandled exception")
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
