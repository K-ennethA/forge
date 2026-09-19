"""``rig_check`` — the headless deformation harness, and ``animation_check``.

Two gates live here.  ``rig_check`` poses the rig and measures the flesh (below).
``animation_check`` is its sibling for a *clip*: it measures **foot slide** —
how far a planted foot drifts through the frames it is supposed to be standing
still — in millimetres per step, with no render and nothing judged by eye.  The
section that implements it, near the bottom of this file, argues for every
choice it makes.


Every rigging tool on the market stops at "the rig generated".  Nobody ships an
answer to the question the artist asks next, which is *does it deform*.  This
module poses the rig into the extremes a game character will actually hit,
evaluates the skinned mesh at each pose, and **measures** what happened to the
flesh: how much volume the joint lost, whether the surface started passing
through itself, and how far the cross-section collapsed under a twist.  Numbers,
per joint, with a verdict — a rig gate, not a screenshot.

What is measured, and why those three
-------------------------------------
Three failures account for nearly every "the rig is fine but it looks wrong"
report, and all three are geometric, which means they can be measured rather
than judged (``docs/automation-thesis.md``: *renders judge beauty, geometry
judges truth*):

1. **Volume loss** — the collapsed elbow.  Measured as the volume of the
   **convex hull of the vertices around the joint**, posed against rest.  A hull
   is used, not the raw surface, because the surface around a joint is an open
   patch whose signed volume means nothing; the hull of the same vertices is
   closed, cheap (``bmesh.ops.convex_hull``) and collapses exactly when the
   flesh does.  The whole mesh's signed volume is reported alongside it as a
   global sanity number when the mesh is closed.
2. **New self-intersections** — the arm passing through the ribs, the calf
   through the thigh.  The same BVH overlap :mod:`forge.tools.diagnose` uses on
   a sculpt, run on the *posed, evaluated* mesh and differenced against the rest
   pose, so a mesh that already clips itself at rest does not fail every joint.
3. **Candy-wrapper twist** — the forearm pinched to a thread halfway along.
   Measured as the area of the 2D convex hull of the vertices in a thin slab
   perpendicular to the bone, a little way down it, posed against rest.  That is
   the cross-section, and its collapse is the artefact.

How the poses are chosen
------------------------
For each deform-relevant joint (FK limb controls, spine, neck) the harness finds
the control that actually drives it — ``upper_arm_fk.L``, ``chest``, ``neck`` —
preferring **unconstrained** bones, because a Rigify ``ORG-``/``DEF-`` bone is
driven by constraints and rotating it moves nothing at all.  It flips any
``IK_FK`` switch to full FK for the same reason, then applies a small number of
poses from a per-joint table of anatomically sensible ranges (knee 0..140
degrees, elbow 0..150, hip 0..110, shoulder 0..95, spine and neck bends, plus an
axial twist for the twist metric).

**Three poses per joint by default**, not a sweep: mid-flex, max-flex and
max-twist.  A sweep is minutes per joint and hours per rig; the extremes are
where the failures live, and the mid-flex catches the joint that is already
wrong before it gets anywhere.  ``poses`` takes an explicit list when a
particular angle is the question.

The bend *direction* is computed, not hardcoded: both signs are tried on the
bone's rest matrix and the one that folds the limb (shrinks the angle to its
parent) wins, so a knee fitted backwards is still tested the way it bends.

And every joint is **proved to drive geometry** before it is judged: if the
first pose moves no vertex more than a hair, the joint is reported as
unmeasured with the reason, rather than passing for having done nothing.

What "the mesh" means here (shape keys and their drivers)
---------------------------------------------------------
Every measurement is taken on the **depsgraph-evaluated** mesh
(``evaluated_get`` in :func:`_vertex_coords` and :func:`_evaluated_bmesh`), so
shape keys, the drivers on their values and the armature modifier are all
already applied.  That is what makes a **corrective shape key measurable**: with
:mod:`forge.tools.correctives`' bend-angle drivers live, posing the joint fires
the key and the volume-loss number this harness reports is the corrected one.
The rest region each joint is measured over is built from those same evaluated
rest coordinates rather than from the raw ``mesh.vertices``, because a mesh with
shape keys is evaluated key-mix-first and the raw vertices are not the shape the
armature deforms.  The result carries a ``shape_keys`` block saying what was
live, so nobody has to take the paragraph's word for it.

Restoring the pose
------------------
Every pose bone's ``matrix_basis`` and rotation mode, and every ``IK_FK``
property, is captured before anything moves and restored in a ``finally``,
whether the harness finished, raised, or was interrupted.  A measurement tool
that leaves the character in a lunge is worse than no measurement tool.

Runs under ``blender --background``.  Nothing renders, nothing downloads,
nothing is written to disk.
"""

import math
import re
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import diagnose
from . import rigforge
from . import rigforge_landmarks
from . import rigforge_rig
# The fifth placement gate: in-limb weight continuity, which is a property of
# the skinning and therefore lives with the skinning.
from . import rigforge_skin
from .common import (
    M_TO_MM,
    find_object,
    get_bool,
    get_choice,
    get_float,
    get_int,
    object_mode,
    refresh_view_layer,
)
from .registry import ForgeError, command

__all__ = [
    "JOINT_TABLE",
    "THRESHOLDS",
    "POSE_SETS",
    "FOOT_SLIDE_THRESHOLDS",
    "CONTACT_CANDIDATES",
    "enumerate_joints",
    "contact_points",
    "stance_runs",
    "grounded_flags",
    "airborne_windows",
    "knee_points",
    "AIRBORNE_CLEARANCE",
    "MIN_AIRBORNE_FRAMES",
    "PARABOLA_TOLERANCE",
    "shape_key_state",
    "evaluated_vertex_coords",
    "bend_direction",
    "BEND_LIFT_FRACTION",
    "BEND_TRAVEL_FRACTION",
    "hand_containment",
    "foot_height",
    "HAND_CONTAINMENT_MM",
    "HAND_CONTAINMENT_STATIONS",
    "FOOT_HEIGHT_TOLERANCE_MM",
    "JointProbe",
    "cmd_rig_check",
    "cmd_animation_check",
]


# ---------------------------------------------------------------------------
# what a joint is, and how far it is allowed to move
# ---------------------------------------------------------------------------

#: The joints worth measuring, in report order.
#:
#: ``controls`` is a preference list of the bones that might drive the joint on
#: a *generated* rig — Rigify's limbs get ``<bone>_fk``, its spine gets named
#: torso controls (``hips``, ``chest``, ``neck``, ``head``) and no per-vertebra
#: FK bones at all, which is why this cannot be derived from the metarig names
#: alone.  ``deform`` names the *metarig* bones whose ``DEF-`` children carry
#: the flesh; :func:`rigforge_rig.def_bones_for` expands them.
#:
#: Ranges are the conservative end of the clinical ones: a game character's knee
#: reaches 140 degrees long before a gymnast's 160, and a rig that survives 140
#: has been asked a fair question. ``twist`` is the axial rotation used for the
#: candy-wrapper measurement.
JOINT_TABLE = (
    {"joint": "hip", "label": "hip", "sided": True,
     "controls": ("thigh_fk.%s", "thigh.%s"), "deform": ("thigh.%s",),
     "flex": 110.0, "twist": 45.0},
    {"joint": "knee", "label": "knee", "sided": True,
     "controls": ("shin_fk.%s", "shin.%s"), "deform": ("shin.%s",),
     "flex": 140.0, "twist": 25.0},
    {"joint": "shoulder", "label": "shoulder", "sided": True,
     "controls": ("upper_arm_fk.%s", "upper_arm.%s"), "deform": ("upper_arm.%s",),
     "flex": 95.0, "twist": 60.0},
    {"joint": "elbow", "label": "elbow", "sided": True,
     "controls": ("forearm_fk.%s", "forearm.%s"), "deform": ("forearm.%s",),
     "flex": 150.0, "twist": 80.0},
    {"joint": "spine_lower", "label": "lower spine", "sided": False,
     "controls": ("spine_fk.001", "hips", "torso", "spine.001"),
     "deform": ("spine", "spine.001"), "flex": 30.0, "twist": 25.0},
    {"joint": "chest", "label": "chest", "sided": False,
     "controls": ("spine_fk.003", "chest", "spine.003"),
     "deform": ("spine.002", "spine.003"), "flex": 28.0, "twist": 25.0},
    {"joint": "neck", "label": "neck", "sided": False,
     "controls": ("neck", "spine_fk.004", "spine.004"),
     "deform": ("spine.004", "spine.005"), "flex": 45.0, "twist": 45.0},
    {"joint": "head", "label": "head", "sided": False,
     "controls": ("head", "spine_fk.006", "spine.006"),
     "deform": ("spine.006",), "flex": 35.0, "twist": 45.0},
    # quadruped front limbs — same machinery, two more table rows
    {"joint": "front_hip", "label": "front hip", "sided": True,
     "controls": ("front_thigh_fk.%s", "front_thigh.%s"), "deform": ("front_thigh.%s",),
     "flex": 90.0, "twist": 30.0},
    {"joint": "front_knee", "label": "front knee", "sided": True,
     "controls": ("front_shin_fk.%s", "front_shin.%s"), "deform": ("front_shin.%s",),
     "flex": 120.0, "twist": 25.0},
)

#: The pose sets. Each entry is ``(label, flex fraction, twist fraction)``.
#: ``extreme`` is the default: enough to find the failure, few enough to finish
#: in minutes rather than hours.
POSE_SETS = {
    "extreme": (("mid flex", 0.5, 0.0), ("max flex", 1.0, 0.0), ("max twist", 0.0, 1.0)),
    "quick": (("max flex", 1.0, 0.0),),
    "full": (("quarter flex", 0.25, 0.0), ("mid flex", 0.5, 0.0),
             ("three quarter flex", 0.75, 0.0), ("max flex", 1.0, 0.0),
             ("half twist", 0.0, 0.5), ("max twist", 0.0, 1.0),
             ("max flex and twist", 1.0, 0.5)),
}

#: The verdict bands.
#:
#: **Credibility tier: heuristic (proxy).**  These are not calibrated against
#: artist accept/reject decisions — no such calibration set exists here yet.
#: They are the points at which each artefact becomes visible in practice,
#: chosen to be quiet on a good rig rather than exactly right on a marginal one,
#: and every measurement is reported next to the band that judged it so a reader
#: can disagree with the threshold and keep the number.
THRESHOLDS = {
    "volume_loss_pct": {"ok": 8.0, "attention": 20.0},
    "new_intersections": {"ok": 0.0, "attention": 20.0},
    "twist_collapse_pct": {"ok": 15.0, "attention": 35.0},
}

#: Face budget for the intersection scan (``mesh_diagnose``'s, reused).
INTERSECTION_FACE_LIMIT = diagnose.SELF_INTERSECT_FACE_LIMIT

#: How thick the cross-section slab is, as a fraction of the bone's length.
SLAB_FRACTION = 0.08

#: How far along the bone the cross-section is taken — close enough to the joint
#: that a twist concentrates there, far enough that the joint's own geometry is
#: not what is being measured.
SLAB_OFFSET = 0.30

#: Vertices within this multiple of the shorter adjacent bone are "around the
#: joint" for the volume hull.
HULL_RADIUS = 0.6

#: A pose that moves no vertex further than this fraction of the mesh's span has
#: not actually driven the deformation, whatever the rig claims.
MOVEMENT_EPSILON = 0.002


# ---------------------------------------------------------------------------
# finding the rig, the mesh, and the controls
# ---------------------------------------------------------------------------

def _rigged_meshes(rig):
    out = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is rig:
                out.append(obj)
                break
    return out


def _resolve_rig(params):
    """The armature to test: named, or the active object's, or the only one."""
    name = params.get("rig")
    if isinstance(name, str) and name.strip():
        rig = find_object(name.strip())
        if rig.type != "ARMATURE":
            raise ForgeError("Object %r is a %s, not an armature." % (rig.name, rig.type))
        return rig
    active = bpy.context.view_layer.objects.active
    if active is not None:
        if active.type == "ARMATURE" and _rigged_meshes(active):
            return active
        if active.type == "MESH":
            for modifier in active.modifiers:
                if modifier.type == "ARMATURE" and modifier.object is not None:
                    return modifier.object
            stored = str(rigforge._prop(active, rigforge_rig.PROP_RIG, "") or "")
            if stored and bpy.data.objects.get(stored) is not None:
                return bpy.data.objects[stored]
    candidates = [obj for obj in bpy.data.objects
                  if obj.type == "ARMATURE" and _rigged_meshes(obj)]
    if len(candidates) == 1:
        return candidates[0]
    raise ForgeError(
        "No rig given and none could be guessed (%d armature(s) drive a mesh). Pass "
        "'rig', or select the character." % len(candidates))


def _mesh_for(rig, params):
    """The skinned mesh whose deformation is measured."""
    wanted = params.get("mesh")
    if isinstance(wanted, str) and wanted.strip():
        return find_object(wanted.strip(), mesh_only=True)
    meshes = _rigged_meshes(rig)
    if not meshes:
        raise ForgeError(
            "No mesh is skinned to %r, so there is no deformation to measure. Run "
            "rigforge_generate_rig first, or pass 'mesh'." % rig.name)
    stored = str(rigforge._prop(rig, rigforge_rig.PROP_RIG_MESH, "") or "")
    for mesh in meshes:
        if mesh.name == stored:
            return mesh
    # An LOD stack would multiply the work for the same answer: measure the
    # heaviest, which is the mesh the artist looks at.
    meshes.sort(key=lambda obj: len(obj.data.polygons), reverse=True)
    return meshes[0]


def _control_for(rig, candidates):
    """The first candidate bone that exists and can actually be posed.

    Constrained bones are skipped where an unconstrained one exists: Rigify's
    ``ORG-``/``DEF-`` bones and its tweak mechanisms are *driven*, and rotating
    a driven bone changes nothing while looking exactly like a passing test.
    """
    fallback = None
    for name in candidates:
        bone = rig.pose.bones.get(name)
        if bone is None:
            continue
        if not len(bone.constraints):
            return bone, name, False
        if fallback is None:
            fallback = (bone, name, True)
    return fallback if fallback is not None else (None, None, False)


def enumerate_joints(rig, wanted=None):
    """Every testable joint on ``rig``: control, deform bones, ranges.

    Driven by :data:`JOINT_TABLE` rather than by whatever bones exist, because
    "every bone" on a Rigify rig is hundreds of controls, widgets and
    mechanisms, and the ones that carry flesh are a dozen.
    """
    joints = []
    for row in JOINT_TABLE:
        for side in (("L", "R") if row["sided"] else (None,)):
            def named(pattern):
                return pattern % side if side else pattern

            control, control_name, constrained = _control_for(
                rig, [named(c) for c in row["controls"]])
            if control is None:
                continue
            deform = []
            for pattern in row["deform"]:
                deform.extend(_deform_for(rig, named(pattern)))
            if not deform:
                continue
            joints.append({
                "joint": "%s.%s" % (row["joint"], side) if side else row["joint"],
                "label": ("%s %s" % (row["label"], side)) if side else row["label"],
                "control": control_name,
                "control_constrained": constrained,
                "deform_bones": sorted(set(deform)),
                "flex_deg": float(row["flex"]),
                "twist_deg": float(row["twist"]),
                "side": side,
            })
    if wanted:
        keep = {str(w).lower() for w in wanted}
        joints = [j for j in joints if j["joint"].lower() in keep
                  or j["label"].lower() in keep or j["control"].lower() in keep]
    return joints


def _ik_switches(rig):
    """Every ``IK_FK``-ish custom property on the rig, with its current value.

    Rigify's limbs blend between an IK and an FK chain through a float property
    on the limb's parent control.  Rotating the FK control while that sits on IK
    moves nothing — the classic false pass — so the harness forces full FK for
    the duration and puts every one of them back afterwards.
    """
    found = {}
    for bone in rig.pose.bones:
        for key in bone.keys():
            if not isinstance(key, str):
                continue
            if key.lower().replace("/", "_") in ("ik_fk", "ikfk", "fk_ik"):
                try:
                    found[(bone.name, key)] = float(bone[key])
                except (TypeError, ValueError):
                    continue
    return found


# ---------------------------------------------------------------------------
# measuring
# ---------------------------------------------------------------------------

def _evaluated_bmesh(obj):
    """A bmesh of ``obj`` **after** its armature modifier, in world space."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
    finally:
        evaluated.to_mesh_clear()
    bm.transform(obj.matrix_world)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    return bm


def _vertex_coords(obj):
    """World-space vertex positions of the evaluated (deformed) mesh."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    matrix = obj.matrix_world
    try:
        return [matrix @ vertex.co.copy() for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


#: The same function under a public name.  ``rigforge_correctives`` measures the
#: flesh with the harness's own reader rather than a second implementation that
#: could drift from it — if one of them ever stops seeing shape keys, both do.
evaluated_vertex_coords = _vertex_coords


def _hull_volume(points):
    """Volume of the convex hull of a point cloud, in cubic metres."""
    if len(points) < 4:
        return 0.0
    bm = bmesh.new()
    try:
        for point in points:
            bm.verts.new(point)
        bm.verts.ensure_lookup_table()
        try:
            bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
        except (RuntimeError, ValueError, TypeError):  # pragma: no cover - degenerate
            return 0.0
        loose = [vert for vert in bm.verts if not vert.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        if not bm.faces:
            return 0.0
        return abs(bm.calc_volume(signed=True))
    finally:
        bm.free()


def _hull_area_2d(points_2d):
    """Area of the convex hull of 2D points (monotone chain, no numpy)."""
    points = sorted(set((round(x, 9), round(y, 9)) for x, y in points_2d))
    if len(points) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return 0.0
    area = 0.0
    for index in range(len(hull)):
        x1, y1 = hull[index]
        x2, y2 = hull[(index + 1) % len(hull)]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def _group_indices(obj, bone_names):
    return {group.index for group in obj.vertex_groups if group.name in bone_names}


def _weighted_vertices(obj, indices, minimum):
    """Indices of the vertices a set of deform bones actually owns."""
    out = set()
    if not indices:
        return out
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.group in indices and entry.weight >= minimum:
                out.add(vertex.index)
                break
    return out


def _deform_for(rig, metarig_bone):
    """The ``DEF-`` bones of one metarig bone, without swallowing its siblings.

    :func:`rigforge_rig.def_bones_for` treats ``DEF-<name>.<digits>`` as a
    subdivision of ``<name>`` — true for ``DEF-thigh.L.001``, and false for
    ``DEF-spine.001``, which is the deform bone of the *metarig bone*
    ``spine.001``.  The generated rig can tell the two apart: Rigify keeps an
    ``ORG-`` bone for every metarig bone, so a numbered candidate that has its
    own ``ORG-`` belongs to itself, not to the stem above it.  Without this the
    "lower spine" joint would claim the whole spine, head included.
    """
    out = []
    for name in rigforge_rig.def_bones_for(rig, metarig_bone):
        suffix = name[len(rigforge_rig.DEF_PREFIX + metarig_bone):]
        if suffix and rig.data.bones.get("ORG-%s%s" % (metarig_bone, suffix)) is not None:
            continue
        out.append(name)
    return out


def _parent_deform_names(rig, deform_names):
    """The nearest ``DEF-`` ancestor of each of ``deform_names`` — the flesh above.

    One bone per chain, not its whole numbered family: the elbow's neighbourhood
    is the lower half of the upper arm, and pulling in the shoulder as well
    would measure the wrong thing.
    """
    out = set()
    for name in deform_names:
        bone = rig.data.bones.get(name)
        parent = bone.parent if bone is not None else None
        while parent is not None and not parent.name.startswith(rigforge_rig.DEF_PREFIX):
            parent = parent.parent
        if parent is not None:
            out.add(parent.name)
    return out - set(deform_names)


class JointProbe(object):
    """Everything about one joint that is measured the same way every pose.

    Built once, at rest: which vertices belong to the joint, where the
    cross-section plane sits, how big the neighbourhood is.  A pose then only
    reads coordinates out of the evaluated mesh.
    """

    def __init__(self, rig, mesh, joint, weight_floor=0.05, rest_world=None):
        """``rest_world`` is the **evaluated** rest position of every vertex.

        Without it the region is built from ``mesh.data.vertices[i].co``, which
        is the raw base mesh — not what the armature deforms once the mesh
        carries shape keys, because Blender evaluates the key mix *before* the
        modifier stack.  A character with a form key at 1 (or a corrective at
        rest) would then have its joint neighbourhood, its girth and its
        cross-section slab measured on a shape nobody is looking at.  The caller
        passes the evaluated rest coordinates; the fallback is the old
        behaviour, which is identical whenever there are no shape keys.
        """
        self.joint = joint
        self.mesh = mesh
        self.reason = None
        matrix = rig.matrix_world
        bones = [rig.data.bones[name] for name in joint["deform_bones"]
                 if name in rig.data.bones]
        if not bones:
            self.reason = "its deform bones are missing from the generated rig"
            self.head = None
            return
        bones.sort(key=lambda bone: bone.name)
        self.head = matrix @ bones[0].head_local
        self.tail = matrix @ bones[-1].tail_local
        direction = self.tail - self.head
        self.length = max(direction.length, 1e-6)
        self.axis = direction / self.length

        parent_names = _parent_deform_names(rig, joint["deform_bones"])
        parent_length = self.length
        if parent_names:
            lengths = [((matrix @ rig.data.bones[name].tail_local)
                        - (matrix @ rig.data.bones[name].head_local)).length
                       for name in parent_names if name in rig.data.bones]
            if lengths:
                parent_length = max(max(lengths), 1e-6)

        world = mesh.matrix_world
        if rest_world is not None:
            def at(index):
                return rest_world[index]
        else:
            def at(index):
                return world @ mesh.data.vertices[index].co
        self.rest_from_evaluated = rest_world is not None
        own = _weighted_vertices(
            mesh, _group_indices(mesh, set(joint["deform_bones"])), weight_floor)
        # How thick the limb is, not just how long the bone is. A blob leg is
        # wider than 60% of its own shin, and a neighbourhood measured only in
        # bone lengths would sit entirely inside the flesh and catch no surface
        # at all — which is exactly how a joint gets silently skipped.
        girth = 0.0
        if own:
            distances = sorted(
                rigforge_rig._point_segment_distance(at(i), self.head, self.tail)
                for i in own)
            girth = distances[len(distances) // 2]
        self.girth = girth
        self.radius = max(HULL_RADIUS * min(self.length, parent_length), 1.25 * girth)

        names = set(joint["deform_bones"]) | parent_names
        owned = _weighted_vertices(mesh, _group_indices(mesh, names), weight_floor)
        self.hull_verts = []
        self.slab_verts = []
        slab_centre = self.head + self.axis * (self.length * SLAB_OFFSET)
        half = max(self.length * SLAB_FRACTION, 1e-6)
        for index in owned:
            point = at(index)
            if (point - self.head).length <= self.radius:
                self.hull_verts.append(index)
            if abs((point - slab_centre).dot(self.axis)) <= half:
                self.slab_verts.append(index)
        self.slab_centre = slab_centre
        seed = Vector((0.0, 0.0, 1.0))
        if abs(seed.dot(self.axis)) > 0.9:
            seed = Vector((1.0, 0.0, 0.0))
        self.u = (seed - self.axis * seed.dot(self.axis)).normalized()
        self.v = self.axis.cross(self.u).normalized()
        if len(self.hull_verts) < 4:
            self.reason = ("its deform bones own fewer than 4 vertices near the joint "
                           "(weight >= %.2f)" % weight_floor)

    @property
    def usable(self):
        return self.head is not None and self.reason is None

    def measure(self, coords):
        """Hull volume and cross-section area from one pose's vertex positions."""
        hull = _hull_volume([coords[i] for i in self.hull_verts])
        if len(self.slab_verts) >= 3:
            section = _hull_area_2d([
                ((coords[i] - self.slab_centre).dot(self.u),
                 (coords[i] - self.slab_centre).dot(self.v))
                for i in self.slab_verts])
        else:
            section = 0.0
        return hull, section

    def moved(self, rest_coords, coords):
        """The largest movement of any of this joint's own vertices, in metres."""
        biggest = 0.0
        for index in self.hull_verts:
            distance = (coords[index] - rest_coords[index]).length
            if distance > biggest:
                biggest = distance
        return biggest


def _intersection_count(mesh, limit_faces):
    """Non-adjacent intersecting face pairs on the evaluated mesh right now."""
    bm = _evaluated_bmesh(mesh)
    try:
        if len(bm.faces) > limit_faces:
            return None, ("%d faces is above the %d-face budget for the BVH overlap scan"
                          % (len(bm.faces), limit_faces))
        result = diagnose.self_intersections(bm, limit=3)
        if not result.get("scanned"):
            return None, result.get("note") or "the scan did not run"
        return int(result.get("count") or 0), None
    finally:
        bm.free()


# ---------------------------------------------------------------------------
# posing
# ---------------------------------------------------------------------------

def _bend_sign(rig, joint):
    """Which way this joint folds: ``+1`` or ``-1`` about the control's local X.

    Computed, not assumed.  The control's rest direction is rotated both ways
    and compared with the direction of the chain above it; the sign that makes
    the angle *smaller* is the fold.
    """
    control = rig.data.bones.get(joint["control"])
    if control is None:
        return 1.0
    rest = control.tail_local - control.head_local
    if rest.length < 1e-9:
        return 1.0
    rest = rest.normalized()
    parent_direction = None
    parent_names = _parent_deform_names(rig, joint["deform_bones"])
    if parent_names:
        total = Vector((0.0, 0.0, 0.0))
        for name in parent_names:
            bone = rig.data.bones.get(name)
            if bone is not None:
                total += (bone.tail_local - bone.head_local)
        if total.length > 1e-9:
            parent_direction = total.normalized()
    if parent_direction is None and control.parent is not None:
        candidate = control.parent.tail_local - control.parent.head_local
        if candidate.length > 1e-9:
            parent_direction = candidate.normalized()
    if parent_direction is None:
        return 1.0
    basis = control.matrix_local.to_3x3()
    try:
        inverse = basis.inverted()
    except ValueError:  # pragma: no cover - a degenerate bone matrix
        return 1.0
    best = 1.0
    best_angle = None
    for sign in (1.0, -1.0):
        rotation = Matrix.Rotation(math.radians(35.0) * sign, 3, "X")
        turned = (basis @ rotation @ inverse @ rest).normalized()
        angle = turned.angle(parent_direction, math.pi)
        if best_angle is None or angle < best_angle:
            best, best_angle = sign, angle
    return best


#: How far the IK target is moved, as a fraction of the limb's own rest chord
#: (root joint to end joint).  A quarter of the chord is a long stride's worth
#: of knee bend — large enough that a limb with a real bend plane travels a long
#: way, small enough that no leg is asked to fold past what it can reach.
BEND_LIFT_FRACTION = 0.25

#: How far the joint must travel **in the anatomical direction**, as a fraction
#: of the lift, before the bend counts as unambiguous.
#:
#: **Credibility tier: heuristic (proxy).**  The geometry says a straight limb
#: whose chord shortens by ``d`` must move its mid joint sideways by
#: ``0.5 * sqrt(d * (2L - d))`` — on the werewolf's 569 mm leg and a 142 mm lift
#: that is **188 mm**, so 15% of the lift (21 mm) is a floor a correct rig clears
#: by an order of magnitude, and a wrong one cannot reach by accident.  The
#: measured travel is always reported next to it.
BEND_TRAVEL_FRACTION = 0.15

#: The joint must be back where it started once the pose is restored.  A tenth
#: of a millimetre is solver dust; anything more means the harness changed the
#: rig, which is the one thing a measurement may never do.
BEND_RETURN_MM = 0.1

#: The chain each IK limb is judged on: ``(root, mid, end)`` metarig bone names,
#: ``%s`` the side.  ``mid`` is the joint whose travel is the measurement.
BEND_CHAIN = {
    "leg": ("thigh.%s", "shin.%s", "foot.%s"),
    "front_leg": ("front_thigh.%s", "front_shin.%s", "front_foot.%s"),
    "arm": ("upper_arm.%s", "forearm.%s", "hand.%s"),
}


def _capture_pose(rig):
    return {bone.name: (bone.matrix_basis.copy(), bone.rotation_mode)
            for bone in rig.pose.bones}


def _restore_pose(rig, snapshot, switches):
    for name, (matrix, mode) in snapshot.items():
        bone = rig.pose.bones.get(name)
        if bone is None:
            continue
        try:
            bone.rotation_mode = mode
            bone.matrix_basis = matrix
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            pass
    for (bone_name, key), value in switches.items():
        bone = rig.pose.bones.get(bone_name)
        if bone is None:
            continue
        try:
            bone[key] = value
        except (KeyError, TypeError, ValueError):  # pragma: no cover
            pass
    refresh_view_layer()


def _apply_rotation(bone, flex_deg, twist_deg, sign):
    """Flex about the bone's local X; twist about its own axis (local Y)."""
    bone.rotation_mode = "XYZ"
    bone.rotation_euler = (math.radians(flex_deg) * sign, math.radians(twist_deg), 0.0)


# ---------------------------------------------------------------------------
# the sixth placement gate: which way does the limb bend
# ---------------------------------------------------------------------------

def _world_head(rig, bone_name):
    """The evaluated world position of a pose bone's head."""
    bone = rig.pose.bones.get(bone_name)
    return None if bone is None else rig.matrix_world @ bone.head.copy()


def bend_direction(rig, lift_fraction=BEND_LIFT_FRACTION,
                   travel_fraction=BEND_TRAVEL_FRACTION, limbs=None):
    """**Does each limb bend the way the animal bends?**  Driven, not inspected.

    The defect this exists for was found by a human watching a walk cycle: the
    werewolf's knees folded **backwards**.  Nothing in the harness moved.
    ``centering`` was happy (the bone was inside the flesh), ``asymmetry`` was
    0.0 (both knees were wrong identically), ``side_naming`` was clean and the
    foot slide was 1.1 mm — a rig can be perfectly placed, perfectly mirrored,
    perfectly planted and still be a rig whose knees hinge the wrong way.

    So this gate does not inspect anything.  It **drives the rig the way an
    animator would** and measures what happened:

    1. the limb is switched to IK with its pole live, and the pose is zeroed, so
       the answer is a property of the rig rather than of whatever pose the file
       was saved in;
    2. the IK target is translated ``lift_fraction`` of the limb's own rest
       chord **towards the limb's root** — for a leg that is the owner's own
       test, lifting the foot;
    3. the mid joint's travel is projected onto the direction it is *supposed*
       to apex: a knee forward, an elbow backward, both relative to the way the
       rig's own toes point (:func:`rigforge_rig.rig_forward_axis`);
    4. and the joint is put back, and checked to be back.

    A straight limb fails here even though it is not *wrong* anywhere: with no
    bend plane the solver picks one, the travel is small and its sign is luck.
    That is the point — an ambiguous rest pose is a defect, and the fix is the
    anatomical pre-bend the landmark pass applies
    (:func:`forge.tools.rigforge_landmarks.prebend_joint`).

    Restores the pose exactly, in a ``finally``, whatever happens.
    """
    limbs = limbs if limbs is not None else rigforge_rig.ik_limbs(rig)
    forward, forward_how = rigforge_rig.rig_forward_axis(rig, limbs)
    snapshot = _capture_pose(rig)
    switches = _ik_switches(rig)
    # ``_ik_switches`` captures the IK_FK blends and nothing else, and this gate
    # also switches each limb's *pole* on. A rig shipped with a pole disabled
    # must get it back, so the pole properties join the snapshot -- same shape,
    # same restore, one ``finally``.
    for entry in limbs:
        bone = rig.pose.bones.get(entry["switch_bone"] or "")
        if bone is not None and rigforge_rig.POLE_PROP in bone.keys():
            try:
                switches[(bone.name, rigforge_rig.POLE_PROP)] = bone[
                    rigforge_rig.POLE_PROP]
            except (KeyError, TypeError, ValueError):  # pragma: no cover
                pass
    rows = []
    restored_worst = 0.0
    try:
        with object_mode():
            for entry in limbs:
                chain = BEND_CHAIN.get(entry["limb"])
                target_name = entry.get("ik_target")
                if chain is None or not target_name:
                    continue
                side = entry["side"]
                roots = _deform_for(rig, chain[0] % side)
                mids = _deform_for(rig, chain[1] % side)
                ends = _deform_for(rig, chain[2] % side)
                if not roots or not mids:
                    continue
                want_sign = rigforge_rig.POLE_DIRECTION.get(entry["limb"], 1.0)

                for bone in rig.pose.bones:
                    bone.matrix_basis.identity()
                rigforge_rig.set_limb_mode(rig, entry, "ik")
                if entry.get("pole_target"):
                    rigforge_rig.set_pole_vector(rig, entry, True)
                refresh_view_layer()

                root = _world_head(rig, roots[0])
                joint = _world_head(rig, mids[0])
                end = (_world_head(rig, ends[0]) if ends
                       else rig.matrix_world @ rig.pose.bones[mids[-1]].tail.copy())
                target_rest = _world_head(rig, target_name)
                if root is None or joint is None or end is None or target_rest is None:
                    continue
                chord = (end - root).length
                if chord < 1e-6:
                    continue
                want = forward * want_sign
                axis = (end - root).normalized()
                want = want - axis * want.dot(axis)
                if want.length < 1e-9:
                    continue
                want.normalize()

                lift = lift_fraction * chord
                push = (root - end)
                push = push.normalized() if push.length > 1e-9 else Vector((0.0, 0.0, 1.0))
                delta = rig.matrix_world.to_3x3().inverted() @ (push * lift)
                pose_target = rig.pose.bones[target_name]
                pose_target.matrix = Matrix.Translation(delta) @ pose_target.matrix
                refresh_view_layer()

                target_moved = (_world_head(rig, target_name) - target_rest).length
                travel = _world_head(rig, mids[0]) - joint
                along = travel.dot(want)
                required = travel_fraction * lift
                # What a straight chain of two equal halves would do: the chord
                # shortens by ``lift``, so the joint must swing out by half the
                # leg of that triangle. Reported for scale, never gated on -- a
                # limb already pre-bent moves less than this and is correct.
                ideal = 0.5 * math.sqrt(max(lift * (2.0 * chord - lift), 0.0))

                for name, (matrix, mode) in snapshot.items():
                    bone = rig.pose.bones.get(name)
                    if bone is not None:
                        bone.matrix_basis = matrix.copy()
                for bone in rig.pose.bones:
                    bone.matrix_basis.identity()
                refresh_view_layer()
                returned = (_world_head(rig, mids[0]) - joint).length
                restored_worst = max(restored_worst, returned)

                rows.append({
                    "limb": entry["name"],
                    "label": entry["label"],
                    "joint": mids[0],
                    "ik_target": target_name,
                    "pole_target": entry.get("pole_target"),
                    "expected": "forward" if want_sign > 0 else "backward",
                    "chord_mm": round(chord * M_TO_MM, 1),
                    "lift_mm": round(lift * M_TO_MM, 1),
                    "target_moved_mm": round(target_moved * M_TO_MM, 1),
                    "travel_mm": round(travel.length * M_TO_MM, 2),
                    "travel_along_mm": round(along * M_TO_MM, 2),
                    "required_mm": round(required * M_TO_MM, 2),
                    "straight_limb_ideal_mm": round(ideal * M_TO_MM, 1),
                    "returned_mm": round(returned * M_TO_MM, 3),
                    "correct": bool(along >= required),
                    "backwards": bool(along < 0.0),
                })
    finally:
        _restore_pose(rig, snapshot, switches)

    wrong = [row for row in rows if not row["correct"]]
    backwards = [row for row in rows if row["backwards"]]
    if not rows:
        verdict = "unmeasured"
        says = ("This rig has no IK limb whose bend direction could be driven, so which "
                "way its joints fold was not measured.")
    elif wrong:
        verdict = "fail"
        worst = min(wrong, key=lambda row: row["travel_along_mm"])
        says = (
            "%s BENDS THE WRONG WAY: lifting %r by %.0f mm moved %s %+.1f mm %s when it "
            "must travel at least %.0f mm %s (a straight limb would swing %.0f mm). "
            "%sThe rest pose has no anatomical pre-bend, so the IK solver has no plane "
            "to prefer and the joint folds whichever way it falls. The fix is upstream "
            "and it is what a human rigger does before anything else: pre-bend the rest "
            "pose — a knee apexes forward, an elbow backward — and generate again."
            % (worst["label"], worst["ik_target"], worst["lift_mm"], worst["joint"],
               abs(worst["travel_along_mm"]),
               ("backward" if worst["expected"] == "forward" else "forward")
               if worst["backwards"] else worst["expected"],
               worst["required_mm"], worst["expected"],
               worst["straight_limb_ideal_mm"],
               "%d of %d limbs travel the wrong way outright. "
               % (len(backwards), len(rows)) if backwards else ""))
    else:
        verdict = "ok"
        worst = min(rows, key=lambda row: row["travel_along_mm"] / max(row["lift_mm"], 1e-9))
        says = ("Every one of the %d IK limbs folds the way it should: the tightest is "
                "%s, whose %s travels %.0f mm %s on a %.0f mm lift (minimum %.0f)."
                % (len(rows), worst["label"], worst["joint"], worst["travel_along_mm"],
                   worst["expected"], worst["lift_mm"], worst["required_mm"]))
    if rows and restored_worst * M_TO_MM > BEND_RETURN_MM:
        verdict = "fail"
        says += (" And the pose did NOT come back: the worst joint is %.2f mm from where "
                 "it started after restore, over a %.2f mm tolerance."
                 % (restored_worst * M_TO_MM, BEND_RETURN_MM))
    return {
        "forward": [round(v, 4) for v in forward],
        "forward_from": forward_how,
        "limbs": rows,
        "wrong": [row["limb"] for row in wrong],
        "backwards": [row["limb"] for row in backwards],
        "lift_fraction": lift_fraction,
        "travel_fraction": travel_fraction,
        "worst_return_mm": round(restored_worst * M_TO_MM, 3),
        "pose_restored": True,
        "verdict": verdict,
        "threshold_tier": (
            "heuristic (proxy tier): the travel floor is %.0f%% of the lift, an order of "
            "magnitude under what a limb with a real bend plane does and out of reach of "
            "one without. Every measurement is reported next to the band that judged it."
            % (100.0 * travel_fraction)),
        "says": says,
    }


# ---------------------------------------------------------------------------
# the seventh placement gate: is the hand bone in the hand
# ---------------------------------------------------------------------------

#: How far outside its own flesh a hand bone's midpoint or tail may sit before
#: the gate calls it out, in millimetres.  **Credibility tier: heuristic
#: (proxy).**  Five millimetres is inside the retopo noise of any hand and a long
#: way under the 29.7 mm the live werewolf measured.
HAND_CONTAINMENT_MM = 5.0

#: Where along the hand bone containment is measured.  The head is the wrist and
#: belongs to the forearm as much as to the hand, so it is reported and not
#: gated — exactly as :func:`~forge.tools.rigforge_landmarks.bone_centering`
#: treats a bone's ends.  The **midpoint and the tail** are what the gate judges,
#: because those are the two the owner saw outside the silhouette.
HAND_CONTAINMENT_STATIONS = (0.0, 0.35, 0.5, 0.65, 1.0)
HAND_GATED_STATIONS = (0.5, 1.0)

#: Bones this gate is about.
HAND_BONE_RE = re.compile(r"(?:^|[-_.])hand(?:[._]|$)", re.IGNORECASE)
FOOT_BONE_RE = re.compile(r"(?:^|[-_.])foot(?:[._]|$)", re.IGNORECASE)
TOE_BONE_RE = re.compile(r"(?:^|[-_.])toe(?:[._]|$)", re.IGNORECASE)
SHIN_BONE_RE = re.compile(r"(?:^|[-_.])(?:shin|calf)(?:[._]|$)", re.IGNORECASE)


def _side_suffix(name):
    """``"DEF-hand.L"`` -> ``"L"``; unsided -> ``None``."""
    _base, side = rigforge_landmarks._side_of(name)
    return side


def _group_points(mesh, groups, names, weight_floor):
    """World-space vertices weighted at least ``weight_floor`` to any of ``names``."""
    wanted = {index for index, bone in groups.items() if bone in names}
    if not wanted:
        return []
    matrix = mesh.matrix_world
    out = []
    for vertex in mesh.data.vertices:
        for entry in vertex.groups:
            if entry.group in wanted and entry.weight >= weight_floor:
                out.append(matrix @ vertex.co)
                break
    return out


def _section_excursion(region, origin, axis, reach, sample, band):
    """How far ``sample`` sticks out of the flesh, in metres.

    The same question :meth:`~forge.tools.rigforge_landmarks.Limb.section_reach`
    asks for the pre-bend, asked of a point instead of a push: cut the region
    across ``axis`` at the sample's own station, and compare how far the sample
    is from that section's centre with **how far the flesh reaches that way**.
    Zero means inside.  Anything else is the millimetres of daylight, and it is
    two numbers added together — how far past the end of the hand the sample is,
    and how far out of the side of it — so a tail beyond the fingertips and a
    tail out through the back of the palm both show up.

    Deliberately not "is it inside the mesh": this character's retopo is a closed
    **shell**, so a ray from any bone crosses it an even number of times and
    parity says every bone in the body is outside.  Distance to the surface is no
    better on a splayed paw, where the centreline runs in the air between the
    fingers.  The flesh's own cross-section is the only honest judge.
    """
    axis = Vector(axis)
    origin = Vector(origin)
    sample = Vector(sample)
    projection = (sample - origin).dot(axis)
    along = max(0.0, projection - reach) + max(0.0, -projection)
    station = min(max(projection, 0.0), reach)
    half = band
    slab = []
    for _widen in range(6):
        slab = [p for p in region if abs((p - origin).dot(axis) - station) <= half]
        if len(slab) >= rigforge_landmarks.MIN_SECTION_POINTS:
            break
        half *= 1.6
    if len(slab) < 3:
        return None, None, along
    centre = rigforge_landmarks._centroid(slab)
    delta = sample - centre
    across = delta - axis * delta.dot(axis)
    if across.length < 1e-9:
        return 0.0, along, along
    push = across.normalized()
    far = max((p - centre).dot(push) for p in slab)
    out = max(0.0, across.length - max(far, 0.0))
    return across.length, far, out + along


def hand_containment(rig, mesh, weight_floor=0.2):
    """**Is the hand bone in the hand, and does it point the way the palm does?**

    The defect this exists for was found on a rest-pose render: the werewolf's
    ``DEF-hand.L`` ran from the wrist **outboard**, away from the body, and its
    tail sat 29.7 mm outside the mesh beside a hand that hangs straight down
    against the thigh.  Six placement gates were clean.  They had to be: the
    hand bone is not one of the long bones ``centering`` judges (a hand has no
    centreline of its own), both hands were wrong identically so ``asymmetry``
    read 0.0, the names were right, the weights were tidy and ``bend_direction``
    only drives knees and elbows.  Nothing in the harness asks where a hand
    points.

    Two numbers, per hand, and both come off the mesh rather than off the rig:

    1. **the angle** between the bone and the palm direction
       :func:`~forge.tools.rigforge_landmarks.hand_axis` measures from the flesh
       past the wrist — gated at
       :data:`~forge.tools.rigforge_landmarks.HAND_AXIS_TOLERANCE_DEG`;
    2. **containment** at the bone's midpoint and tail: how far each sits outside
       the hand's own cross-section there (:func:`_section_excursion`), gated at
       :data:`HAND_CONTAINMENT_MM`.

    The region is cut from the hand bone's flesh **and its parent's**, along the
    *parent's* direction, so a badly aimed hand bone cannot define the frame it
    is then judged in.
    """
    groups = rigforge_landmarks._deform_groups(rig, mesh)
    if not groups:
        return {"hands": [], "verdict": "unmeasured",
                "says": "%r has no deform vertex groups, so no hand could be measured "
                        "against its flesh." % mesh.name}
    matrix = rig.matrix_world
    rows = []
    for bone in rig.data.bones:
        if not bone.use_deform or not HAND_BONE_RE.search(bone.name):
            continue
        head = matrix @ bone.head_local
        tail = matrix @ bone.tail_local
        span = tail - head
        if span.length < 1e-6:
            continue
        bone_axis = span.normalized()
        parent = bone.parent
        arm_axis = None
        if parent is not None:
            arm = (matrix @ parent.tail_local) - (matrix @ parent.head_local)
            if arm.length > 1e-6:
                arm_axis = arm.normalized()
        names = {bone.name}
        if parent is not None:
            names.add(parent.name)
        points = _group_points(mesh, groups, names, weight_floor)
        row = {"bone": bone.name, "side": _side_suffix(bone.name),
               "parent": parent.name if parent is not None else None,
               "length_mm": round(span.length * M_TO_MM, 2),
               "flesh_vertices": len(points)}
        if arm_axis is None:
            row.update({"verdict": "unmeasured",
                        "says": "%s has no parent bone to take the forearm's direction "
                                "from, so the hand region could not be cut." % bone.name})
            rows.append(row)
            continue
        if len(points) < 12:
            row.update({"verdict": "unmeasured",
                        "says": "%s and its parent drive %d vertices, too few to measure "
                                "a palm from." % (bone.name, len(points))})
            rows.append(row)
            continue
        palm = rigforge_landmarks.hand_axis(points, head, arm_axis, label=bone.name)
        if palm is None:
            row.update({"verdict": "unmeasured",
                        "says": "%s has no flesh past its own head along the forearm, so "
                                "there is no hand to contain it." % bone.name})
            rows.append(row)
            continue
        direction = palm["direction"]
        reach = palm["reach"]
        region = palm["region"]
        band = max(reach / float(rigforge_landmarks.HAND_STATIONS - 1), 1e-6) * 0.75
        angle = math.degrees(bone_axis.angle(direction, 0.0))
        stations = []
        for fraction in HAND_CONTAINMENT_STATIONS:
            sample = head + span * fraction
            offset, far, out = _section_excursion(region, head, direction, reach,
                                                  sample, band)
            stations.append({
                "fraction": round(fraction, 3),
                "offset_mm": None if offset is None else round(offset * M_TO_MM, 2),
                "flesh_reach_mm": None if far is None else round(far * M_TO_MM, 2),
                "outside_mm": round(out * M_TO_MM, 2),
            })
        # A station whose section could not be measured does not get to *pass*:
        # "there was no flesh there to compare with" is not "the bone is inside
        # the flesh". It drops out, and a bone with nothing left to judge is
        # reported unmeasured rather than clean.
        gated = [entry for entry in stations
                 if entry["fraction"] in HAND_GATED_STATIONS
                 and entry["offset_mm"] is not None]
        worst = max(gated, key=lambda e: e["outside_mm"]) if gated else None
        tail_entry = stations[-1]
        row.update({
            "palm_axis": [round(v, 5) for v in direction],
            "palm_reach_mm": round(reach * M_TO_MM, 2),
            "palm_vertices": palm["points"],
            "angle_deg": round(angle, 2),
            "angle_limit_deg": rigforge_landmarks.HAND_AXIS_TOLERANCE_DEG,
            "arm_angle_deg": palm["arm_angle_deg"],
            "tail_outside_mm": tail_entry["outside_mm"],
            "worst_outside_mm": worst["outside_mm"] if worst else None,
            "worst_at_fraction": worst["fraction"] if worst else None,
            "stations": stations,
            "axis_how": palm["how"],
        })
        aimed = angle <= rigforge_landmarks.HAND_AXIS_TOLERANCE_DEG
        held = bool(worst is not None and worst["outside_mm"] <= HAND_CONTAINMENT_MM)
        row["aimed"] = bool(aimed)
        row["contained"] = held
        if worst is None:
            row["verdict"] = "unmeasured"
            row["says"] = ("%s's midpoint and tail have no cross-section of hand flesh "
                           "around them to be inside or outside of, so its containment "
                           "was not judged (it points %.1f degrees off the palm "
                           "direction, for what that is worth)." % (bone.name, angle))
            rows.append(row)
            continue
        row["verdict"] = "ok" if (aimed and held) else "fail"
        if row["verdict"] == "ok":
            row["says"] = ("%s points within %.1f degrees of the palm the mesh measures "
                           "and stays inside it (worst %.1f mm at %.0f%% along)."
                           % (bone.name, angle, worst["outside_mm"],
                              100.0 * worst["fraction"]))
        else:
            row["says"] = (
                "%s IS NOT IN ITS HAND: it points %.1f degrees off the palm direction "
                "the mesh measures (limit %.0f), and its %s sits %.1f mm outside the "
                "hand's own cross-section (limit %.1f). The hand region is %d vertices "
                "reaching %.1f mm past the wrist; %s"
                % (bone.name, angle, rigforge_landmarks.HAND_AXIS_TOLERANCE_DEG,
                   "tail" if worst["fraction"] >= 1.0 else "midpoint",
                   worst["outside_mm"], HAND_CONTAINMENT_MM, palm["points"],
                   reach * M_TO_MM, palm["how"]))
        rows.append(row)

    rows.sort(key=lambda row: -(row.get("worst_outside_mm") or 0.0))
    judged = [row for row in rows if row["verdict"] in ("ok", "fail")]
    if not judged:
        verdict = "unmeasured"
        says = ("No hand bone on %r had a parent and enough flesh for its palm "
                "direction to be measured." % rig.name)
    else:
        verdict = _worst([row["verdict"] for row in judged])
        bad = [row for row in judged if row["verdict"] == "fail"]
        if bad:
            says = bad[0]["says"]
            if len(bad) > 1:
                says += (" %d of %d hands are wrong the same way, which is why the "
                         "asymmetry gate reads 0.0 on them."
                         % (len(bad), len(judged)))
        else:
            worst_row = judged[0]
            says = ("Every hand bone runs down its own palm: worst %s, %.1f degrees off "
                    "the measured palm axis and %.1f mm outside its cross-section."
                    % (worst_row["bone"], worst_row["angle_deg"],
                       worst_row["worst_outside_mm"] or 0.0))
    return {
        "hands": rows,
        "measured": len(judged),
        "verdict": verdict,
        "containment_mm": HAND_CONTAINMENT_MM,
        "angle_limit_deg": rigforge_landmarks.HAND_AXIS_TOLERANCE_DEG,
        "gated_at": ("the bone's midpoint and tail; the head is the wrist, which "
                     "belongs to the forearm as much as to the hand, so it is reported "
                     "and not gated"),
        "threshold_tier": (
            "heuristic (proxy tier): 25 degrees is the slack a hand bone genuinely "
            "needs (a knuckle line is not square to the palm) and 5 mm is inside any "
            "hand's retopo noise. The live defect measured 62 degrees and 29.7 mm."),
        "says": says,
    }


# ---------------------------------------------------------------------------
# the eighth placement gate: how high the ankle and the toe run
# ---------------------------------------------------------------------------

#: How far the ankle or the toe may sit from the height the **mesh** puts it at,
#: in millimetres.  **Credibility tier: heuristic (proxy).**  Two centimetres is
#: a station's worth of slack on a foot this size and far under the 65 mm and
#: 52 mm the live werewolf measured.
FOOT_HEIGHT_TOLERANCE_MM = 20.0


def foot_height(rig, mesh, weight_floor=0.2):
    """**Is the ankle at the ankle, and does the toe run on the ground?**

    The sole-fit gate already asks whether the foot chain reaches the front of
    the boot.  It says nothing about how high off it the chain runs, and on the
    live werewolf that is where the defect was: ``DEF-foot.L``'s head — the ankle
    — sat at ``z = 173 mm`` on a foot whose mass stops at 110, and ``DEF-toe.L``
    ran at ``z = 79 mm``, eight centimetres of air under a bone that is supposed
    to roll on the floor.  Both are **measurable against the mesh**: the ankle is
    where the leg's forward reach collapses and the toe belongs a fraction of the
    foot's own thickness above the sole, which is exactly what
    :func:`~forge.tools.rigforge_landmarks.foot_landmarks` now computes.

    So this gate re-measures the foot from the flesh the leg chain drives and
    quotes the difference, per side, in millimetres above the sole.
    """
    groups = rigforge_landmarks._deform_groups(rig, mesh)
    if not groups:
        return {"feet": [], "verdict": "unmeasured",
                "says": "%r has no deform vertex groups, so no foot could be measured "
                        "against its flesh." % mesh.name}
    forward, forward_how = rigforge_rig.rig_forward_axis(rig)
    matrix = rig.matrix_world
    feet = {}
    for bone in rig.data.bones:
        if not bone.use_deform:
            continue
        side = _side_suffix(bone.name)
        if side is None:
            continue
        if FOOT_BONE_RE.search(bone.name):
            feet.setdefault(side, {}).setdefault("foot", bone)
        elif TOE_BONE_RE.search(bone.name):
            feet.setdefault(side, {}).setdefault("toe", bone)
        elif SHIN_BONE_RE.search(bone.name):
            feet.setdefault(side, {})["shin"] = bone

    rows = []
    for side in sorted(feet):
        entry = feet[side]
        foot_bone = entry.get("foot")
        toe_bone = entry.get("toe")
        shin_bone = entry.get("shin")
        if foot_bone is None or shin_bone is None:
            continue
        names = {bone.name for bone in entry.values()}
        # Every shin segment, not just the one that happened to sort first: a
        # Rigify shin is two DEF bones and the leg column above the ankle is what
        # the collapse rule needs to see.
        for bone in rig.data.bones:
            if (bone.use_deform and _side_suffix(bone.name) == side
                    and (SHIN_BONE_RE.search(bone.name)
                         or FOOT_BONE_RE.search(bone.name)
                         or TOE_BONE_RE.search(bone.name))):
                names.add(bone.name)
        points = _group_points(mesh, groups, names, weight_floor)
        row = {"side": side, "foot": foot_bone.name,
               "toe": toe_bone.name if toe_bone is not None else None,
               "flesh_vertices": len(points), "forward_from": forward_how}
        if len(points) < 24:
            row.update({"verdict": "unmeasured",
                        "says": "the %s leg chain drives %d vertices, too few to measure "
                                "a foot from." % (side, len(points))})
            rows.append(row)
            continue
        ankle_now = matrix @ foot_bone.head_local
        knee = matrix @ shin_bone.head_local
        measured = rigforge_landmarks.foot_landmarks(points, ankle_now, knee, forward,
                                                     label="leg.%s" % side)
        if measured is None:
            row.update({"verdict": "unmeasured",
                        "says": "the %s leg chain's flesh has no foot in it to measure "
                                "against (nothing below the ankle is long enough front "
                                "to back)." % side})
            rows.append(row)
            continue
        ground = measured["ground"]
        ankle_want = Vector(measured["ankle"])
        ball_want = Vector(measured["ball"])
        ankle_mm = (ankle_now.z - ground) * M_TO_MM
        ankle_target_mm = (ankle_want.z - ground) * M_TO_MM
        row.update({
            "sole_z_mm": round(ground * M_TO_MM, 2),
            "ankle_height_mm": round(ankle_mm, 2),
            "ankle_target_mm": round(ankle_target_mm, 2),
            "ankle_error_mm": round(abs(ankle_mm - ankle_target_mm), 2),
            "ankle_how": measured["detail"]["ankle"]["how"],
            "foot_length_mm": measured["length_mm"],
        })
        errors = [row["ankle_error_mm"]]
        if toe_bone is not None:
            toe_now = matrix @ toe_bone.head_local
            toe_mm = (toe_now.z - ground) * M_TO_MM
            toe_target_mm = (ball_want.z - ground) * M_TO_MM
            row.update({
                "toe_height_mm": round(toe_mm, 2),
                "toe_target_mm": round(toe_target_mm, 2),
                "toe_error_mm": round(abs(toe_mm - toe_target_mm), 2),
                "toe_how": measured["detail"]["ball"]["how"],
            })
            errors.append(row["toe_error_mm"])
        row["worst_error_mm"] = round(max(errors), 2)
        row["verdict"] = ("ok" if row["worst_error_mm"] <= FOOT_HEIGHT_TOLERANCE_MM
                          else "fail")
        if row["verdict"] == "ok":
            row["says"] = ("the %s ankle sits %.0f mm above the sole where the mesh puts "
                           "it at %.0f, and the toe at %.0f against %.0f."
                           % (side, row["ankle_height_mm"], row["ankle_target_mm"],
                              row.get("toe_height_mm") or 0.0,
                              row.get("toe_target_mm") or 0.0))
        else:
            row["says"] = (
                "THE %s FOOT CHAIN RUNS TOO HIGH: its ankle is %.0f mm above the sole "
                "where the flesh puts it at %.0f (%.0f mm out, limit %.0f), and its toe "
                "is at %.0f against %.0f. %s"
                % (side, row["ankle_height_mm"], row["ankle_target_mm"],
                   row["ankle_error_mm"], FOOT_HEIGHT_TOLERANCE_MM,
                   row.get("toe_height_mm") or 0.0, row.get("toe_target_mm") or 0.0,
                   measured["detail"]["ankle"]["how"]))
        rows.append(row)

    judged = [row for row in rows if row["verdict"] in ("ok", "fail")]
    if not judged:
        verdict = "unmeasured"
        says = ("No leg chain on %r had a shin, a foot and enough flesh for its ankle "
                "height to be measured." % rig.name)
    else:
        verdict = _worst([row["verdict"] for row in judged])
        bad = [row for row in judged if row["verdict"] == "fail"]
        says = (bad[0]["says"] if bad
                else "Every foot chain sits where the sole puts it: %s"
                     % "; ".join(row["says"] for row in judged))
    return {
        "feet": rows,
        "measured": len(judged),
        "verdict": verdict,
        "tolerance_mm": FOOT_HEIGHT_TOLERANCE_MM,
        "threshold_tier": (
            "heuristic (proxy tier): 20 mm is a measuring station's worth of slack on a "
            "foot this size. The live defect measured 65 mm at the ankle and 52 mm at "
            "the toe."),
        "says": says,
    }


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def _band(value, key):
    if value is None:
        return "unmeasured"
    bands = THRESHOLDS[key]
    if value <= bands["ok"]:
        return "ok"
    if value <= bands["attention"]:
        return "attention"
    return "fail"


def _worst(verdicts):
    order = {"ok": 0, "unmeasured": 1, "attention": 2, "fail": 3}
    worst = "ok"
    for verdict in verdicts:
        if order.get(verdict, 0) > order.get(worst, 0):
            worst = verdict
    return worst


def shape_key_state(mesh):
    """What morph targets are live on the mesh being measured, and what drives them.

    The harness reads the **depsgraph-evaluated** mesh (``_vertex_coords`` and
    ``_evaluated_bmesh`` both go through ``evaluated_get``), so shape keys and
    the drivers on their values are already part of every number it reports —
    which is the whole reason a corrective shape key can be *measured* rather
    than admired.  That is easy to assume and easy to get silently wrong, so the
    report states it: how many keys there are, which are non-zero right now, and
    which are driven rather than set by hand.
    """
    keys = getattr(mesh.data, "shape_keys", None)
    if keys is None:
        return {"count": 0, "driven": [], "active": [], "muted": [],
                "evaluated": True,
                "says": "%r has no shape keys." % mesh.name}
    driven = []
    if keys.animation_data is not None:
        for fcurve in keys.animation_data.drivers:
            path = fcurve.data_path or ""
            if '"' in path:
                driven.append(path.split('"')[1])
    blocks = list(keys.key_blocks)[1:]  # the basis is the shape, not a target
    active = [block.name for block in blocks
              if not block.mute and abs(float(block.value)) > 1e-6]
    muted = [block.name for block in blocks if block.mute]
    return {
        "count": len(blocks),
        "driven": sorted(set(driven)),
        "active": active,
        "muted": muted,
        "evaluated": True,
        "says": ("%r carries %d morph target(s), %d of them driven; every number "
                 "below is measured on the depsgraph-evaluated mesh, so those keys "
                 "and their drivers are already in it."
                 % (mesh.name, len(blocks), len(set(driven)))),
    }


def _sentence(label, worst, volume, twist, clip):
    if worst == "ok":
        return "The %s holds up." % label
    parts = []
    if volume is not None and _band(volume, "volume_loss_pct") != "ok":
        parts.append("loses %.0f%% of its volume" % volume)
    if clip is not None and _band(float(clip), "new_intersections") != "ok":
        parts.append("clips itself (%d new face pairs)" % clip)
    if twist is not None and _band(twist, "twist_collapse_pct") != "ok":
        parts.append("pinches %.0f%% of its cross-section under twist" % twist)
    detail = ", ".join(parts) or "could not be measured"
    verb = "breaks down" if worst == "fail" else "is worth a look"
    return "The %s %s: it %s at the extremes tested." % (label, verb, detail)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

@command("rig_check")
def cmd_rig_check(params):
    """Pose the rig into its extremes and measure what happens to the flesh.

    ``rig_check {"rig"?, "mesh"?, "poses"?: "extreme"|"quick"|"full"|[...],
    "joints"?: [names], "max_poses"?, "intersections"?, "weight_floor"?,
    "render_weights"?: dir, "weight_map_bones"?: [names], "max_weight_maps"?}``

    Returns a per-joint report — worst volume loss, new self-intersections and
    twist collapse, each with a band — plus an overall gate.  The pose is always
    restored.

    **Eight placement gates run on every call** and are reported whether or not
    they fail, because the defects they catch were found by a human staring at a
    render and must never need that again:

    * ``centering`` — how far each deform bone sits from the centroid of its own
      limb's cross-section, in millimetres and as a percentage of that section's
      radius;
    * ``asymmetry`` — the left/right table: every ``.R`` bone against the mirror
      of its ``.L`` twin.  A rigger who mirrors gets 0.0;
    * ``side_naming`` — whether the ``.L`` bones are on the character's left at
      all, measured against the facing the **mesh** says it has;
    * ``overlap`` — the bone-to-bone influence overlap matrix, with every pair
      three or more joints apart named as a defect;
    * ``continuity`` — per bone, how many vertices *inside* its own region are
      punctured: weighted far below every neighbour around them
      (:func:`forge.tools.rigforge_skin.weight_continuity`).  Overlap catches
      flesh shared between bones; this catches flesh missing from one, which is
      what a patchy automatic bind looks like and what nothing else measured;
    * ``bend_direction`` — the only gate that **drives** the rig: each limb's IK
      target is pulled towards its own root and the joint's travel is projected
      onto the way it is supposed to fold, a knee forward and an elbow backward
      (:func:`bend_direction`).  The other five all passed on a werewolf whose
      knees bent backwards;
    * ``hand_containment`` — whether each hand bone points the way its **palm**
      does and stays inside it (:func:`hand_containment`).  The first six all
      passed on a werewolf whose hand bones ran outboard, out of the mesh: a
      hand is not one of the long bones ``centering`` judges, and both hands
      were wrong identically so ``asymmetry`` read 0.0;
    * ``foot_height`` — whether the ankle is at the height the leg's forward
      reach collapses at, and the toe chain runs just above the sole
      (:func:`foot_height`).  The sole-fit rule already asks whether the foot
      reaches the front of the boot; this asks how high off it the chain runs,
      which is where the same werewolf's ankle sat 65 mm up the shin.

    ``render_weights`` additionally writes per-bone weight maps into a folder —
    the maps, looked at, rather than counted.
    """
    started = time.monotonic()
    warnings = []
    rig = _resolve_rig(params)
    mesh = _mesh_for(rig, params)

    poses_param = params.get("poses")
    explicit = None
    if isinstance(poses_param, (list, tuple)) and poses_param:
        explicit = []
        for entry in poses_param:
            if isinstance(entry, (int, float)) and not isinstance(entry, bool):
                explicit.append(("flex %g deg" % float(entry), float(entry), 0.0))
            elif isinstance(entry, dict):
                explicit.append((str(entry.get("label") or "pose"),
                                 float(entry.get("flex_deg") or 0.0),
                                 float(entry.get("twist_deg") or 0.0)))
            else:
                raise ForgeError(
                    "poses must be 'extreme', 'quick', 'full', or a list of angles / "
                    "{flex_deg, twist_deg} objects; got %r." % (entry,))
        pose_set = "explicit"
    else:
        pose_set = str(poses_param or "extreme").lower()
        if pose_set not in POSE_SETS:
            raise ForgeError("Unknown pose set %r. Known: %s (or a list of angles)."
                             % (pose_set, ", ".join(sorted(POSE_SETS))))

    max_poses = get_int(params, "max_poses", 12, minimum=1, maximum=64)
    do_intersections = get_bool(params, "intersections", True)
    face_limit = get_int(params, "intersection_face_limit", INTERSECTION_FACE_LIMIT,
                         minimum=100, maximum=2000000)
    weight_floor = get_float(params, "weight_floor", 0.05, minimum=0.0, maximum=1.0)

    joints = enumerate_joints(rig, params.get("joints"))
    if not joints:
        raise ForgeError(
            "No deform-relevant joints were found on %r. This harness knows the Rigify "
            "limb and spine controls (thigh/shin/upper_arm/forearm FK, hips, chest, "
            "neck, head); a rig with none of them has nothing it can measure." % rig.name)

    snapshot = _capture_pose(rig)
    switches = _ik_switches(rig)
    report_joints = []
    skipped = []
    global_rest_volume = None
    rest_intersections = None
    poses_run = 0
    span = max(max(mesh.dimensions), 1e-6)

    try:
        with object_mode():
            for (bone_name, key) in switches:
                bone = rig.pose.bones.get(bone_name)
                try:
                    bone[key] = 0.0 if key.lower() == "fk_ik" else 1.0
                except (KeyError, TypeError, ValueError):  # pragma: no cover
                    warnings.append("Could not switch %s[%s] to FK; that limb's numbers "
                                    "may be an IK solver's, not the rig's."
                                    % (bone_name, key))
            refresh_view_layer()

            # The rest coordinates come first and the probes are built FROM them:
            # a mesh with shape keys (a corrective at 0, a form key at 1) is
            # evaluated key-mix-then-modifiers, so the raw ``vertices[i].co`` is
            # not the shape the armature deforms and a region built on it would
            # be measuring a body nobody is looking at.
            rest_coords = _vertex_coords(mesh)
            probes = []
            for joint in joints:
                probe = JointProbe(rig, mesh, joint, weight_floor,
                                   rest_world=rest_coords)
                if not probe.usable:
                    skipped.append({"joint": joint["joint"], "label": joint["label"],
                                    "reason": probe.reason})
                    continue
                probes.append(probe)
            if not probes:
                raise ForgeError(
                    "None of the %d joint(s) on %r has skinned geometry around it (%s). "
                    "Is %r actually weighted to this rig?"
                    % (len(joints), rig.name,
                       "; ".join(sorted({s["reason"] for s in skipped})), mesh.name))

            rest = {probe.joint["joint"]: probe.measure(rest_coords) for probe in probes}

            bm = _evaluated_bmesh(mesh)
            try:
                closed = bool(bm.edges) and all(
                    len(edge.link_faces) == 2 for edge in bm.edges)
                global_rest_volume = abs(bm.calc_volume(signed=True)) if closed else None
            finally:
                bm.free()
            if global_rest_volume is None:
                warnings.append(
                    "The mesh is not closed, so whole-body volume was not measured. The "
                    "per-joint hull volumes below need no closed mesh and are the real "
                    "number here.")

            if do_intersections:
                rest_intersections, note = _intersection_count(mesh, face_limit)
                if rest_intersections is None:
                    do_intersections = False
                    warnings.append("Self-intersections were not measured: %s." % note)
                elif rest_intersections:
                    warnings.append(
                        "%r already clips itself at rest (%d face pairs); only *new* "
                        "intersections are reported per pose."
                        % (mesh.name, rest_intersections))

            for probe in probes:
                joint = probe.joint
                sign = _bend_sign(rig, joint)
                control = rig.pose.bones.get(joint["control"])
                rest_hull, rest_section = rest[joint["joint"]]
                if explicit is not None:
                    plan = list(explicit)[:max_poses]
                else:
                    plan = [(label, joint["flex_deg"] * flex, joint["twist_deg"] * twist)
                            for label, flex, twist in POSE_SETS[pose_set]][:max_poses]

                results = []
                drove_geometry = False
                for label, flex_deg, twist_deg in plan:
                    _apply_rotation(control, flex_deg, twist_deg, sign)
                    refresh_view_layer()
                    poses_run += 1
                    coords = _vertex_coords(mesh)
                    movement = probe.moved(rest_coords, coords)
                    if movement > span * MOVEMENT_EPSILON:
                        drove_geometry = True
                    hull, section = probe.measure(coords)
                    entry = {
                        "pose": label,
                        "flex_deg": round(flex_deg * sign, 1),
                        "twist_deg": round(twist_deg, 1),
                        "moved_mm": round(movement * M_TO_MM, 2),
                        "hull_volume_mm3": round(hull * (M_TO_MM ** 3), 1),
                        "volume_loss_pct": (
                            round(100.0 * (rest_hull - hull) / rest_hull, 2)
                            if rest_hull > 1e-12 else None),
                        "section_area_mm2": round(section * (M_TO_MM ** 2), 1),
                        "twist_collapse_pct": (
                            round(100.0 * (rest_section - section) / rest_section, 2)
                            if rest_section > 1e-12 and twist_deg else None),
                    }
                    if do_intersections:
                        count, note = _intersection_count(mesh, face_limit)
                        if count is None:  # pragma: no cover - budget guarded above
                            entry["new_intersections"] = None
                            entry["intersection_note"] = note
                        else:
                            entry["new_intersections"] = max(
                                0, count - (rest_intersections or 0))
                    results.append(entry)
                _apply_rotation(control, 0.0, 0.0, sign)
                refresh_view_layer()

                if not drove_geometry:
                    skipped.append({
                        "joint": joint["joint"], "label": joint["label"],
                        "reason": ("posing %r moved no geometry (largest move %.2f mm) - "
                                   "the control is driven by constraints or the flesh is "
                                   "not weighted to it, so nothing was measured"
                                   % (joint["control"],
                                      max(e["moved_mm"] for e in results))),
                    })
                    warnings.append(
                        "%s was not measured: rotating %r moved nothing."
                        % (joint["label"], joint["control"]))
                    continue

                worst_volume = max((e["volume_loss_pct"] for e in results
                                    if e["volume_loss_pct"] is not None), default=None)
                worst_twist = max((e["twist_collapse_pct"] for e in results
                                   if e["twist_collapse_pct"] is not None), default=None)
                worst_clip = max((e["new_intersections"] for e in results
                                  if e.get("new_intersections") is not None), default=None)
                verdicts = {
                    "volume": _band(worst_volume, "volume_loss_pct"),
                    "intersections": _band(
                        float(worst_clip) if worst_clip is not None else None,
                        "new_intersections"),
                    "twist": _band(worst_twist, "twist_collapse_pct"),
                }
                worst = _worst(verdicts.values())
                report_joints.append({
                    "joint": joint["joint"],
                    "label": joint["label"],
                    "control": joint["control"],
                    "control_constrained": joint["control_constrained"],
                    "deform_bones": joint["deform_bones"],
                    "bend_sign": "positive" if sign > 0 else "negative",
                    "vertices": len(probe.hull_verts),
                    "section_vertices": len(probe.slab_verts),
                    "neighbourhood_radius_mm": round(probe.radius * M_TO_MM, 1),
                    "limb_girth_mm": round(probe.girth * M_TO_MM, 1),
                    "rest_hull_volume_mm3": round(rest_hull * (M_TO_MM ** 3), 1),
                    "rest_section_area_mm2": round(rest_section * (M_TO_MM ** 2), 1),
                    "worst_volume_loss_pct": worst_volume,
                    "worst_twist_collapse_pct": worst_twist,
                    "worst_new_intersections": worst_clip,
                    "verdicts": verdicts,
                    "verdict": worst,
                    "says": _sentence(joint["label"], worst, worst_volume, worst_twist,
                                      worst_clip),
                    "poses": results,
                })
    finally:
        _restore_pose(rig, snapshot, switches)

    # --- the placement gates: always measured, always reported -------------
    #
    # Deformation is what the poses above measure. These eight measure whether
    # the skeleton was ever in the right place to begin with, whether the
    # skin it was bound with is a skin, and whether the limbs fold the way the
    # animal folds -- the questions a live audit had to answer by eye: bones off
    # the limb's centreline, left and right fitted independently (6-24 mm
    # apart), side names mirrored so .L drove the right leg, flesh shared between
    # bones that are nowhere near each other, a weight map so patchy it had holes
    # in the middle of a thigh, knees that bent backwards, hand bones pointing
    # outboard out of the mesh and an ankle 65 mm up the shin. All eight are
    # geometric, so the harness finds them from now on instead of the owner
    # squinting at a render or at a walk cycle.
    placement = {}
    try:
        placement["centering"] = rigforge_landmarks.bone_centering(rig, mesh)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run says so
        placement["centering"] = {"verdict": "unmeasured", "says": str(exc)}
    try:
        placement["asymmetry"] = rigforge_landmarks.bone_asymmetry(rig)
    except Exception as exc:  # noqa: BLE001
        placement["asymmetry"] = {"verdict": "unmeasured", "says": str(exc)}
    try:
        placement["side_naming"] = rigforge_landmarks.side_naming(rig, mesh)
    except Exception as exc:  # noqa: BLE001
        placement["side_naming"] = {"verdict": "unmeasured", "says": str(exc)}
    try:
        placement["overlap"] = rigforge_landmarks.influence_overlap(rig, mesh)
    except Exception as exc:  # noqa: BLE001
        placement["overlap"] = {"verdict": "unmeasured", "says": str(exc)}
    # The fifth gate, added after a live audit: overlap sees flesh shared
    # *between* bones, and nothing saw flesh missing *inside* one. DEF-thigh.L's
    # weight map on the werewolf was patchy — islands with punctures — and no
    # existing number moved at all, because no pair was wrong and no vertex was
    # unweighted. This counts the punctures.
    try:
        placement["continuity"] = rigforge_skin.weight_continuity(rig, mesh)
    except Exception as exc:  # noqa: BLE001
        placement["continuity"] = {"verdict": "unmeasured", "says": str(exc)}
    # The sixth, and the only one that *drives* the rig rather than measuring it
    # at rest: which way does each limb fold. The werewolf's knees bent backwards
    # through five clean placement gates and a 1.1 mm foot slide, because every
    # existing number is about where a bone is and none of them is about what
    # happens when an animator pulls on it.
    try:
        placement["bend_direction"] = bend_direction(rig)
    except Exception as exc:  # noqa: BLE001
        placement["bend_direction"] = {"verdict": "unmeasured", "says": str(exc)}
    # The seventh and eighth, both found the same way the first six were -- by
    # the owner looking at a rest-pose render and seeing a bone outside the
    # silhouette. A hand bone that points outboard out of the mesh and an ankle
    # sitting 65 mm up the shin cleared every gate above, because none of them
    # is about a hand's direction or a foot chain's height: ``centering``
    # deliberately does not judge hands, toes or feet, and both sides were wrong
    # identically so ``asymmetry`` read 0.0.
    try:
        placement["hand_containment"] = hand_containment(rig, mesh)
    except Exception as exc:  # noqa: BLE001
        placement["hand_containment"] = {"verdict": "unmeasured", "says": str(exc)}
    try:
        placement["foot_height"] = foot_height(rig, mesh)
    except Exception as exc:  # noqa: BLE001
        placement["foot_height"] = {"verdict": "unmeasured", "says": str(exc)}

    weight_maps = None
    maps_dir = params.get("render_weights")
    if isinstance(maps_dir, str) and maps_dir.strip():
        try:
            weight_maps = rigforge_landmarks.render_weight_maps(
                rig, mesh, maps_dir.strip(),
                bones=(params.get("weight_map_bones") or None),
                max_bones=get_int(params, "max_weight_maps", 8, minimum=1, maximum=64))
        except Exception as exc:  # noqa: BLE001 - a picture, not the product
            warnings.append("The weight maps could not be rendered (%s: %s)."
                            % (type(exc).__name__, exc))

    attention = [j["label"] for j in report_joints if j["verdict"] == "attention"]
    failed = [j["label"] for j in report_joints if j["verdict"] == "fail"]
    placement_verdicts = [block.get("verdict", "unmeasured")
                          for block in placement.values()]
    gate = "fail" if failed else ("attention" if attention else "pass")
    if "fail" in placement_verdicts:
        gate = "fail"
    elif gate == "pass" and "attention" in placement_verdicts:
        gate = "attention"
    lines = []
    bad_placement = False
    for name in ("side_naming", "bend_direction", "hand_containment", "foot_height",
                 "asymmetry", "centering", "overlap", "continuity"):
        block = placement.get(name) or {}
        if block.get("verdict") in ("fail", "attention") and block.get("says"):
            lines.append(block["says"])
            bad_placement = True
    if not bad_placement:
        # One line, with the number worth quoting in it: a mirrored rig's
        # left/right asymmetry, which a human's X-mirror also puts at 0.0.
        asymmetry = placement.get("asymmetry") or {}
        centering = placement.get("centering") or {}
        continuity = placement.get("continuity") or {}
        bend = placement.get("bend_direction") or {}
        hands = placement.get("hand_containment") or {}
        feet_block = placement.get("foot_height") or {}
        lines.append(
            "Placement is clean: left/right asymmetry %s mm, every sided bone on the "
            "side its name claims, worst bone %s mm off its limb's centreline, no "
            "stray influence between bones, %s%% of the weighted flesh punctured, "
            "every IK limb folds the anatomical way (%d checked), %d hand bone(s) "
            "inside their own palms and %d foot chain(s) at the height the sole puts "
            "them."
            % (asymmetry.get("worst_asymmetry_mm"), centering.get("worst_offset_mm"),
               continuity.get("hole_pct"), len(bend.get("limbs") or []),
               hands.get("measured") or 0, feet_block.get("measured") or 0))
    if failed:
        lines.append("Breaks down: %s." % ", ".join(sorted(failed)))
    if attention:
        lines.append("Worth a look: %s." % ", ".join(sorted(attention)))
    if not failed and not attention and report_joints:
        lines.append("All %d measured joint(s) held their volume, their surface and "
                     "their cross-section at the extremes tested." % len(report_joints))
    if skipped:
        lines.append("%d joint(s) could not be measured." % len(skipped))

    return {
        "rig": rig.name,
        "mesh": mesh.name,
        "pose_set": pose_set,
        "poses_per_joint": len(explicit) if explicit is not None
        else len(POSE_SETS[pose_set]),
        "poses_run": poses_run,
        "joints_measured": len(report_joints),
        "joints_skipped": skipped,
        "shape_keys": shape_key_state(mesh),
        "centering": placement["centering"],
        "asymmetry": placement["asymmetry"],
        "side_naming": placement["side_naming"],
        "overlap": placement["overlap"],
        "continuity": placement["continuity"],
        "bend_direction": placement["bend_direction"],
        "hand_containment": placement["hand_containment"],
        "foot_height": placement["foot_height"],
        "weight_maps": weight_maps,
        "rest_intersections": rest_intersections,
        "rest_volume_mm3": (round(global_rest_volume * (M_TO_MM ** 3), 1)
                            if global_rest_volume is not None else None),
        "thresholds": THRESHOLDS,
        "threshold_tier": (
            "heuristic (proxy tier): visible-artefact bands, not calibrated against "
            "artist accept/reject decisions. Every measurement is reported next to the "
            "band that judged it, so the number outlives the threshold."),
        "gate": gate,
        "says": " ".join(lines),
        "joints": report_joints,
        "pose_restored": True,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# ``animation_check`` — the foot-slide metric
# ---------------------------------------------------------------------------
#
# ``rig_check`` answers *does it deform*.  This answers the other question a
# finished character fails on, and it is the one nobody measures: **do the feet
# stay where they were put**.
#
# Foot slide is not a matter of taste.  A walk cycle whose legs were keyed in
# FK — two rotations per leg per key — has nothing at all holding the foot on
# the ground between those keys: the contact point's world position is whatever
# the rotations happen to multiply out to, and it moves every frame of what is
# supposed to be a *plant*.  In the engine the character skates.  The fix is to
# key the foot's IK target and hold it still, and the difference between the
# two is a **distance in millimetres**, which means it can be gated.
#
# What is measured
# ----------------
# The **ball of the foot** (the toe bone's head, which is the foot bone's tail —
# the point a foot pivots over), tracked in world space for every frame of the
# clip.  Not the ankle: a correct heel-off rolls the whole foot over the ball,
# so the ankle *should* travel while the foot is planted, and measuring it
# would fail the very technique it is supposed to reward.  Measured on Rigify's
# generated foot roll, rolling the heel control moves the ball and toe by
# 0.0 mm, which is exactly why the ball is the right point.
#
# How a stance phase is found
# ---------------------------
# Deterministically, from the track itself, with no tags and no VLM:
#
# 1. **Lowest.** The frames where the ball sits within
#    :data:`CONTACT_BAND` of its own lowest point over the clip.
# 2. **Contiguous.** Runs of at least :data:`MIN_STANCE_FRAMES` such frames.
#    A run is one step.
# 3. **Moving least.** Inside a run, leading and trailing samples travelling
#    more than three times the run's own median speed are trimmed — that is the
#    heel strike arriving and the toe leaving, not the plant.
#
# The drift of a step is the **diameter of the planted point's position cloud**
# over that run, horizontally, in millimetres — not its distance from the first
# sample, because a foot that slides out and back would read zero.
#
# Two modes, because there are two kinds of clip
# ----------------------------------------------
# * ``planted`` — the clip travels (root motion).  The stance foot is world
#   fixed, so the expected drift is **zero** and the measurement is raw.
# * ``in_place`` — the treadmill clip an engine plays while its own controller
#   moves the character.  The feet *must* run backwards during stance; what
#   must not vary is the speed.  One shared velocity — pooled over every stance
#   sample of both feet — is removed first, and the residual is the slide.
#
# ``auto`` picks between them by asking whether the body travelled at all.

#: How high above its lowest sample the ball may sit and still count as down,
#: as a fraction of the ball's whole vertical range over the clip.
CONTACT_BAND = 0.2

#: Shorter than this and a "stance" is one frame of a swing passing through.
MIN_STANCE_FRAMES = 3

#: A sample moving faster than this multiple of its run's median speed is the
#: strike or the toe-off, not the plant.  The floor stops a perfectly planted
#: run (median 0) from trimming itself away over floating-point dust.
STANCE_SPEED_FACTOR = 3.0
STANCE_SPEED_FLOOR_MM = 1.0

#: The verdict bands, in millimetres of drift per step.
#:
#: **Credibility tier: heuristic (proxy).**  Not calibrated against artist
#: accept/reject decisions; they are the scale at which a slide becomes visible
#: on a character a metre and a half tall — a few millimetres is the noise a
#: solver leaves behind, a centimetre reads as skating at walking speed.  The
#: number is always reported next to the band that judged it.
FOOT_SLIDE_THRESHOLDS = {"drift_mm": {"ok": 5.0, "attention": 20.0}}

#: Bones that stand in for "the body", best first, for the travel test.
BODY_CONTROLS = ("root", "torso", "hips", "DEF-spine", "spine_fk")

#: Contact-point candidates per side: ``(bone name pattern, head|tail)``.
#: The ball first, the ankle only when there is no toe at all.
CONTACT_CANDIDATES = (
    ("DEF-toe.%s", "head"),
    ("toe.%s", "head"),
    ("ORG-toe.%s", "head"),
    ("DEF-foot.%s", "tail"),
    ("foot.%s", "tail"),
    ("ORG-foot.%s", "tail"),
    ("DEF-front_toe.%s", "head"),
    ("DEF-front_foot.%s", "tail"),
)


def _slide_band(value):
    if value is None:
        return "unmeasured"
    bands = FOOT_SLIDE_THRESHOLDS["drift_mm"]
    if value <= bands["ok"]:
        return "ok"
    if value <= bands["attention"]:
        return "attention"
    return "fail"


def contact_points(rig, wanted=None):
    """The world point on each foot that a plant is measured at.

    ``wanted`` overrides the search with explicit bone names (``"DEF-toe.L"``,
    or ``"DEF-foot.L:tail"``), for a rig whose feet are not named like a biped's.
    """
    out = []
    if wanted:
        for raw in wanted:
            text = str(raw).strip()
            if not text:
                continue
            point = "head"
            if ":" in text:
                text, point = text.rsplit(":", 1)
                point = point.strip().lower()
                if point not in ("head", "tail"):
                    raise ForgeError(
                        "A foot must be named 'bone' or 'bone:head' / 'bone:tail' "
                        "(got %r)." % raw)
            if text not in rig.pose.bones:
                raise ForgeError("Rig %r has no pose bone %r to measure a plant on."
                                 % (rig.name, text))
            side = "L" if text.endswith(".L") else ("R" if text.endswith(".R") else None)
            out.append({"foot": text, "bone": text, "point": point, "side": side})
        return out
    for side in ("L", "R"):
        for pattern, point in CONTACT_CANDIDATES:
            name = pattern % side
            if name in rig.pose.bones:
                out.append({"foot": "foot.%s" % side, "bone": name, "point": point,
                            "side": side})
                break
    return out


def _point_of(rig, spec):
    bone = rig.pose.bones[spec["bone"]]
    local = bone.tail if spec["point"] == "tail" else bone.head
    return rig.matrix_world @ local


def _horizontal(vector):
    return Vector((vector.x, vector.y, 0.0))


def _diameter(points):
    """The widest distance between any two of them (they are few, and this is exact)."""
    worst = 0.0
    for index, first in enumerate(points):
        for second in points[index + 1:]:
            worst = max(worst, (first - second).length)
    return worst


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def stance_runs(track, band=CONTACT_BAND, minimum=MIN_STANCE_FRAMES):
    """Index runs where the tracked point is down and holding still.

    ``track`` is a list of world positions, one per sampled frame, in order.
    Returns ``[[i, i+1, ...], ...]`` — see the section header for the rule.
    """
    if len(track) < minimum:
        return []
    heights = [point.z for point in track]
    low, high = min(heights), max(heights)
    ceiling = low + max(band * (high - low), 1e-5)
    runs = []
    current = []
    for index, height in enumerate(heights):
        if height <= ceiling:
            current.append(index)
        else:
            if len(current) >= minimum:
                runs.append(current)
            current = []
    if len(current) >= minimum:
        runs.append(current)

    trimmed = []
    for run in runs:
        speeds = [(_horizontal(track[run[i + 1]]) - _horizontal(track[run[i]])).length
                  for i in range(len(run) - 1)]
        if not speeds:
            continue
        limit = max(STANCE_SPEED_FACTOR * _median(speeds),
                    STANCE_SPEED_FLOOR_MM / M_TO_MM)
        start, end = 0, len(run) - 1
        while end - start + 1 > minimum and speeds[start] > limit:
            start += 1
        while end - start + 1 > minimum and speeds[end - 1] > limit:
            end -= 1
        trimmed.append(run[start:end + 1])
    return [run for run in trimmed if len(run) >= minimum]


def _body_travel(rig, tracks):
    """How far "the body" moved horizontally over the clip, and on which bone."""
    for name in BODY_CONTROLS:
        track = tracks.get(name)
        if not track:
            continue
        return name, (_horizontal(track[-1]) - _horizontal(track[0])).length
    return None, 0.0


# ---------------------------------------------------------------------------
# the airborne window — the third kind of clip
# ---------------------------------------------------------------------------
#
# Everything above assumes the feet are *on the floor* and the only question is
# whether they hold still.  A jump breaks that assumption honestly rather than
# accidentally: for a stretch of the clip **neither** foot is on the ground, and
# measuring slide there is measuring nothing.  A metric that scored those frames
# would fail every correct jump ever authored, which is how a gate stops being
# used.
#
# So this section adds a third reading, and the whole design constraint is that
# **a walk and a punch must come through it unchanged**.  They do, because the
# airborne window is not asserted by the caller — it is *detected*, from two
# independent facts that only a jump has:
#
# 1. every foot sits above ``ground + clearance * range`` for a run of frames.
#    A walk never manages it: with double support there is always a foot down,
#    and with the swing foot lifting, the *other* one is by definition at the
#    ground.  A punch never manages it: nothing moves vertically at all.
# 2. **the body itself went up** over that run.  This is the condition that
#    makes the detector safe rather than lucky.  An FK-keyed walk can swing both
#    balls off its own lowest sample at once (the ball's height is whatever two
#    rotations multiply out to); it cannot also lift the root, because nothing
#    keyed it.  The check is "did the body rise", never "did it rise
#    *ballistically*" — that second question is a **gate**, below, and a detector
#    that asked it could never fail.
#
# Why auto-detection rather than a mode the caller passes
# -------------------------------------------------------
# ``mode="jump"`` exists and is honoured (and refused, loudly, on a clip with no
# airborne window).  But the default is auto, because the alternative is a gate
# that reads a correct jump as a catastrophic foot slide unless somebody
# remembered to label the clip — and the person who forgets is the person the
# gate was built for.  ``mode="planted"`` and ``mode="in_place"`` still force the
# old readings on a jump, so nothing was taken away.
#
# What the jump reading measures instead
# --------------------------------------
# * **the plants.**  Every *grounded* run — the takeoff plant and the landing
#   plant, which for a forward jump are in two different places — is measured
#   for drift, raw, with no speed trimming: a jump's plant is not a stance phase
#   passing through, it is the frames the character is standing on the floor,
#   and all of them count.
# * **the parabola.**  A least-squares quadratic through the body's height over
#   the airborne window.  The residual is reported in millimetres; the fitted
#   curvature has to be downward, and it implies a gravity, which is printed.  A
#   floaty arc that ignores gravity fits a quadratic badly and says so.
# * **the landing knee.**  The mid joint of each leg has to travel in the
#   anatomical direction (a knee apexes forward) as the landing absorbs, judged
#   against :func:`rigforge_rig.rig_forward_axis` and
#   :data:`rigforge_rig.POLE_DIRECTION` — the same two facts ``bend_direction``
#   judges a rig's fold by, so the gate and the rig check cannot disagree about
#   which way a knee goes.
# * **hop asymmetry**, off by default: how long the clip spends with exactly one
#   foot down.  On a two-foot jump that is a hop, but plenty of jumps are meant
#   to be hops, so this one is only gated when the caller says what it will
#   tolerate.

#: How far above the clip's lowest contact sample a foot must sit before it
#: counts as off the ground, as a fraction of the contact points' whole vertical
#: range.  One shared ceiling for every foot, because "off the ground" is a
#: statement about the floor and not about each foot's own travel.
#:
#: **Credibility tier: heuristic (proxy).**  Deliberately *narrow*: standing on
#: the floor is an exact condition, not an approximate one, and the band only
#: has to absorb the few millimetres a heel roll lifts a ball by while staying
#: well under one frame of real flight — a 24 fps jump clears ~65 mm in its
#: first airborne frame on a rig whose contact points travel ~290 mm in total,
#: so a tenth of the range (29 mm) separates the two by a factor of two either
#: way.  Narrow is the *safe* direction here, because it is the body-rise test
#: below and not this band that keeps a badly keyed gait from reading as flight.
AIRBORNE_CLEARANCE = 0.1

#: Shorter than this and an "airborne window" is a sampling accident.
MIN_AIRBORNE_FRAMES = 3

#: How far the body control must rise over the candidate window, as a fraction
#: of the contact points' range, before the clip is called a jump.  This is the
#: condition that separates a jump from a badly keyed gait.
AIRBORNE_BODY_RISE = 0.2

#: How far the body's airborne height may sit off its own best-fit parabola, as
#: a fraction of the rise over that window.
#:
#: **Credibility tier: heuristic (proxy).**  Ballistics is exact, so a clip
#: authored from ``v*t - g*t^2/2`` fits to float dust; the tolerance is here for
#: the frame quantisation and the IK solver, not to buy slack.  The measured
#: deviation is always reported next to it, in millimetres.
PARABOLA_TOLERANCE = 0.04

#: How far the knee must travel forward through the landing absorb before the
#: landing counts as one, in millimetres.  Under this the character landed
#: stiff-legged, which is the pose a jump is supposed to not end in.
LANDING_KNEE_TRAVEL_MM = 1.0


def _ground_span(tracks, specs):
    """``(lowest, highest)`` contact sample over every foot and every frame."""
    lows = [min(point.z for point in tracks[spec["bone"]]) for spec in specs]
    highs = [max(point.z for point in tracks[spec["bone"]]) for spec in specs]
    return min(lows), max(highs)


def grounded_flags(tracks, specs, clearance=AIRBORNE_CLEARANCE):
    """Per foot, per sampled frame: is this contact point on the ground?

    Returns ``(ground, high, ceiling, {bone: [bool, ...]})``.  The floor for the
    ceiling stops a clip with no vertical motion at all — a punch — from calling
    float dust a lift-off.
    """
    ground, high = _ground_span(tracks, specs)
    ceiling = ground + max(clearance * (high - ground), 1e-5)
    flags = {spec["bone"]: [point.z <= ceiling for point in tracks[spec["bone"]]]
             for spec in specs}
    return ground, high, ceiling, flags


def _runs_of(values, minimum):
    """Index runs of at least ``minimum`` consecutive true values."""
    runs = []
    current = []
    for index, value in enumerate(values):
        if value:
            current.append(index)
        else:
            if len(current) >= minimum:
                runs.append(current)
            current = []
    if len(current) >= minimum:
        runs.append(current)
    return runs


def airborne_windows(flags, samples, minimum=MIN_AIRBORNE_FRAMES):
    """Index runs where **every** foot is off the ground."""
    if not flags:
        return []
    off = [not any(flags[bone][index] for bone in flags) for index in range(samples)]
    return _runs_of(off, minimum)


def _fit_parabola(values):
    """Least-squares quadratic over the sample index.

    Returns ``(a, b, c, worst residual)`` for ``a*i^2 + b*i + c``, or ``None``
    when there are too few samples for the fit to mean anything.  Three points
    define a parabola exactly, so four is the first count at which a residual is
    evidence rather than arithmetic.
    """
    count = len(values)
    if count < 4:
        return None
    xs = [float(index) for index in range(count)]
    s1 = sum(xs)
    s2 = sum(x * x for x in xs)
    s3 = sum(x * x * x for x in xs)
    s4 = sum(x * x * x * x for x in xs)
    t0 = sum(values)
    t1 = sum(x * y for x, y in zip(xs, values))
    t2 = sum(x * x * y for x, y in zip(xs, values))
    matrix = Matrix(((s4, s3, s2), (s3, s2, s1), (s2, s1, float(count))))
    try:
        solved = matrix.inverted() @ Vector((t2, t1, t0))
    except ValueError:  # pragma: no cover - a degenerate sample spacing
        return None
    a, b, c = float(solved.x), float(solved.y), float(solved.z)
    worst = max(abs(y - (a * x * x + b * x + c)) for x, y in zip(xs, values))
    return a, b, c, worst


def knee_points(rig):
    """The mid joint of each IK leg — what a landing's absorb is measured on.

    Same chain ``bend_direction`` judges the rig's fold on
    (:data:`BEND_CHAIN`), so the two cannot disagree about which bone is the
    knee.  Empty on a rig with no legs, which is not an error here: the clip is
    simply read the old way.
    """
    out = []
    try:
        limbs = rigforge_rig.ik_limbs(rig)
    except (AttributeError, RuntimeError, TypeError, KeyError):  # pragma: no cover
        return out
    for entry in limbs:
        if entry.get("limb") not in ("leg", "front_leg"):
            continue
        chain = BEND_CHAIN.get(entry["limb"])
        if chain is None:
            continue
        mids = _deform_for(rig, chain[1] % entry["side"])
        if mids and mids[0] in rig.pose.bones:
            out.append({"limb": entry["name"], "side": entry["side"],
                        "label": entry.get("label") or entry["name"],
                        "bone": mids[0]})
    return out


@command("animation_check")
def cmd_animation_check(params):
    """Measure foot slide on a clip: per-step drift in millimetres, with a verdict.

    ``animation_check {"rig"?, "action"?, "mode"?: "auto"|"planted"|"in_place"
    |"jump", "frame_step"?, "contact_band"?, "min_stance_frames"?,
    "feet"?: [bones], "airborne_clearance"?, "min_airborne_frames"?,
    "parabola_tolerance"?, "hop_tolerance_frames"?}``

    Deterministic and geometric — no render, no model, nothing judged by eye.
    The rig's pose, action and the scene's frame are all restored.

    A clip with an **airborne window** — a run of frames where every foot is off
    the ground *and* the body rose to put them there — is read as a jump: slide
    is not measured through the flight, the grounded plants either side of it are
    measured raw, and four more things are gated (the parabola, the landing knee,
    the plants, and optionally hop asymmetry).  See the section header above for
    why that detection is automatic and why a walk or a punch cannot trip it.
    ``"airborne"`` in the result is ``null`` on every clip that is not one.
    """
    started = time.monotonic()
    warnings = []
    rig = _resolve_rig_loose(params)
    scene = bpy.context.scene

    wanted_action = params.get("action")
    if isinstance(wanted_action, str) and wanted_action.strip():
        action = bpy.data.actions.get(wanted_action.strip())
        if action is None:
            raise ForgeError(
                "No action called %r. The actions in this file are: %s."
                % (wanted_action.strip(),
                   ", ".join(sorted(a.name for a in bpy.data.actions)) or "none"))
    else:
        action = rig.animation_data.action if rig.animation_data else None
        if action is None:
            raise ForgeError(
                "%r has no action assigned and none was named, so there is no "
                "animation to measure. Pass 'action'." % rig.name)

    mode = get_choice(params, "mode",
                      {"AUTO": "auto", "PLANTED": "planted", "TRAVELLING": "planted",
                       "IN_PLACE": "in_place", "INPLACE": "in_place",
                       "JUMP": "jump", "AIRBORNE": "jump"}, "auto")
    frame_step = get_int(params, "frame_step", 1, minimum=1, maximum=10)
    band = get_float(params, "contact_band", CONTACT_BAND, minimum=0.01, maximum=0.9)
    minimum = get_int(params, "min_stance_frames", MIN_STANCE_FRAMES, minimum=2,
                      maximum=1000)
    clearance = get_float(params, "airborne_clearance", AIRBORNE_CLEARANCE,
                          minimum=0.01, maximum=0.9)
    min_airborne = get_int(params, "min_airborne_frames", MIN_AIRBORNE_FRAMES,
                           minimum=2, maximum=1000)
    parabola_tolerance = get_float(params, "parabola_tolerance", PARABOLA_TOLERANCE,
                                   minimum=0.0005, maximum=1.0)
    hop_tolerance = params.get("hop_tolerance_frames")
    if hop_tolerance is not None:
        hop_tolerance = get_int(params, "hop_tolerance_frames", minimum=0, maximum=1000)

    raw_feet = params.get("feet")
    if isinstance(raw_feet, str):
        raw_feet = [raw_feet]
    specs = contact_points(rig, raw_feet)
    if len(specs) < 1:
        raise ForgeError(
            "No foot contact point was found on %r. This metric measures the ball of "
            "the foot (DEF-toe.<side> head, or DEF-foot.<side> tail); name the bones "
            "with 'feet' if this rig calls them something else." % rig.name)
    if len(specs) < 2:
        warnings.append("Only one foot (%s) was found, so this is half a gait."
                        % specs[0]["bone"])

    span = action.frame_range
    start, end = int(math.floor(span[0])), int(math.ceil(span[1]))
    if end <= start:
        raise ForgeError("Action %r covers a single frame (%d); there is no motion to "
                         "measure." % (action.name, start))
    looping = action.name.endswith(rigforge_rig.LOOP_SUFFIX)
    last = end - frame_step if looping and (end - start) >= 2 * frame_step else end
    if looping and last != end:
        warnings.append(
            "%r is a loop, so its last frame repeats its first; the duplicate was left "
            "out of the sample rather than counted as a second plant." % action.name)
    frames = list(range(start, last + 1, frame_step))

    snapshot = _capture_pose(rig)
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    tracks = {spec["bone"]: [] for spec in specs}
    for name in BODY_CONTROLS:
        if name in rig.pose.bones:
            tracks.setdefault(name, [])
    # The knees ride along in the sample loop whatever the clip turns out to be.
    # Nothing downstream of the jump branch reads them, so a walk and a punch
    # are measured on exactly the numbers they always were; what this buys is
    # that the landing gate never needs a second pass over the action.
    knees = knee_points(rig)
    for entry in knees:
        tracks.setdefault(entry["bone"], [])
    try:
        with object_mode():
            rigforge_rig.assign_action(rig, action)
            for frame in frames:
                scene.frame_set(frame)
                refresh_view_layer()
                for spec in specs:
                    tracks[spec["bone"]].append(_point_of(rig, spec))
                for name in list(tracks):
                    if name in rig.pose.bones and not any(
                            s["bone"] == name for s in specs):
                        bone = rig.pose.bones[name]
                        tracks[name].append(rig.matrix_world @ bone.head)
    finally:
        scene.frame_set(previous_frame)
        try:
            rigforge_rig.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):  # pragma: no cover
            pass
        _restore_pose(rig, snapshot, {})

    body_bone, body_travel = _body_travel(rig, tracks)
    all_runs = {}
    for spec in specs:
        all_runs[spec["bone"]] = stance_runs(tracks[spec["bone"]], band=band,
                                             minimum=minimum)

    excursions = {spec["bone"]: _diameter([_horizontal(p) for p in tracks[spec["bone"]]])
                  for spec in specs}
    biggest = max(excursions.values()) if excursions else 0.0

    # --- is this a jump? --------------------------------------------------
    ground_z, high_z, ceiling_z, foot_grounded = grounded_flags(tracks, specs,
                                                                clearance)
    windows = airborne_windows(foot_grounded, len(frames), min_airborne)
    body_track = tracks.get(body_bone) or []
    foot_range = high_z - ground_z
    body_rise = 0.0
    if windows and body_track:
        grounded_body = [body_track[index].z for index in range(len(frames))
                         if any(foot_grounded[bone][index] for bone in foot_grounded)]
        floor = _median(grounded_body) if grounded_body else body_track[0].z
        body_rise = max(max(body_track[index].z for index in window) - floor
                        for window in windows)
    looks_airborne = bool(windows) and body_rise > AIRBORNE_BODY_RISE * foot_range

    if mode == "jump" and not windows:
        raise ForgeError(
            "mode='jump' was asked for, but %r has no airborne window: no run of %d "
            "frames has every foot above %.1f mm (the lowest contact sample plus %.0f%% "
            "of the %.1f mm the feet travel vertically). Either this clip never leaves "
            "the ground, or the feet do not clear it far enough to be called flight — "
            "lower 'airborne_clearance' or 'min_airborne_frames' if you disagree with "
            "where that line is."
            % (action.name, min_airborne, (ceiling_z - ground_z) * M_TO_MM,
               clearance * 100.0, foot_range * M_TO_MM))
    if mode == "auto" and looks_airborne:
        mode = "jump"
    if mode == "jump":
        reason = (
            "every foot leaves the ground for %d frame(s) across %d window(s) while %s "
            "rises %.0f mm, so this is a jump: slide is not measured through the "
            "flight, and the plants either side of it are"
            % (sum(len(window) for window in windows), len(windows),
               body_bone or "the body", body_rise * M_TO_MM)
            if looks_airborne else "asked for by the caller")
    elif mode == "auto":
        if biggest <= 1e-6:
            mode, reason = "planted", "nothing moved horizontally at all"
        elif body_travel > 0.2 * biggest:
            mode = "planted"
            reason = ("%s travelled %.0f mm across the clip, so this is a travelling "
                      "(root-motion) clip and a planted foot should not move at all"
                      % (body_bone, body_travel * M_TO_MM))
        else:
            mode = "in_place"
            reason = ("%s travelled %.0f mm across the clip against %.0f mm of foot "
                      "excursion, so this is an in-place (treadmill) clip and the feet "
                      "are expected to run backwards at one shared speed"
                      % (body_bone or "the body", body_travel * M_TO_MM,
                         biggest * M_TO_MM))
    else:
        reason = "asked for by the caller"

    treadmill = Vector((0.0, 0.0, 0.0))
    if mode == "in_place":
        # One shared velocity for the whole clip, pooled over every stance
        # sample of both feet. Pooled rather than per-phase on purpose: a
        # per-phase fit would subtract each foot's own slide and pass anything,
        # and pooled rather than a median because the residual it leaves is
        # what the gate is actually about.
        total = Vector((0.0, 0.0, 0.0))
        spans = 0
        for spec in specs:
            track = tracks[spec["bone"]]
            for run in all_runs[spec["bone"]]:
                if len(run) < 2:
                    continue
                total += _horizontal(track[run[-1]]) - _horizontal(track[run[0]])
                spans += len(run) - 1
        if spans:
            treadmill = total / float(spans)

    #: Which sample indices each foot is *on the ground* for, in jump mode.  Not
    #: :func:`stance_runs`: that finds the frames a foot holds still near its own
    #: lowest point and trims the fast samples off each end, which is exactly
    #: right for a gait and exactly wrong here.  A jump's plant is not a phase
    #: passing through — it is every frame the character is standing on the
    #: floor, including the frame it pushes off on and the frame it lands on, and
    #: all of them have to hold.  Two samples is the shortest run a drift exists
    #: for.
    phases = {}
    if mode == "jump":
        after = windows[-1][-1] if windows else -1
        for spec in specs:
            runs = _runs_of(foot_grounded[spec["bone"]], 2)
            all_runs[spec["bone"]] = runs
            phases[spec["bone"]] = [
                "landing" if run[0] > after else
                ("takeoff" if windows and run[-1] < windows[0][0] else "ground")
                for run in runs]

    feet_report = []
    worst_step = None
    for spec in specs:
        track = tracks[spec["bone"]]
        steps = []
        for index, run in enumerate(all_runs[spec["bone"]]):
            points = []
            for offset, sample in enumerate(run):
                point = _horizontal(track[sample])
                if mode == "in_place":
                    point = point - treadmill * float(offset)
                points.append(point)
            drift = _diameter(points)
            heights = [track[sample].z for sample in run]
            entry = {
                "step": index + 1,
                "frames": [frames[run[0]], frames[run[-1]]],
                "samples": len(run),
                "drift_mm": round(drift * M_TO_MM, 2),
                "lift_mm": round((max(heights) - min(heights)) * M_TO_MM, 2),
                "drift_pct_of_excursion": (
                    round(100.0 * drift / excursions[spec["bone"]], 2)
                    if excursions[spec["bone"]] > 1e-9 else None),
                "verdict": _slide_band(drift * M_TO_MM),
            }
            if mode == "jump":
                entry["phase"] = phases[spec["bone"]][index]
            steps.append(entry)
            if worst_step is None or entry["drift_mm"] > worst_step["drift_mm"]:
                worst_step = dict(entry, foot=spec["foot"], bone=spec["bone"])
        worst = max((s["drift_mm"] for s in steps), default=None)
        verdict = _slide_band(worst)
        if not steps:
            verdict = "unmeasured"
        feet_report.append({
            "foot": spec["foot"],
            "bone": spec["bone"],
            "point": spec["point"],
            "samples": len(track),
            "ground_mm": round(min(p.z for p in track) * M_TO_MM, 2),
            "excursion_mm": round(excursions[spec["bone"]] * M_TO_MM, 2),
            "steps_measured": len(steps),
            "steps": steps,
            "worst_drift_mm": worst,
            "verdict": verdict,
            "says": (
                "%s never plants: no run of %d frames sits within %.0f%% of its lowest "
                "point, so there is no stance to measure."
                % (spec["bone"], minimum, band * 100.0) if not steps else
                "%s plants %d time(s); the worst step slides %.1f mm."
                % (spec["bone"], len(steps), worst)),
        })
        if mode == "jump":
            feet_report[-1]["grounded_samples"] = sum(
                1 for value in foot_grounded[spec["bone"]] if value)
            feet_report[-1]["airborne_samples"] = sum(
                1 for value in foot_grounded[spec["bone"]] if not value)

    measured = [foot for foot in feet_report if foot["steps_measured"]]
    worst_overall = max((foot["worst_drift_mm"] for foot in measured), default=None)
    gate = _slide_band(worst_overall) if measured else "unmeasured"
    if not measured:
        warnings.append(
            "No stance phase was found on any foot. Either the clip has no ground "
            "contact at all, or the feet never hold still long enough to be one - "
            "which is itself what a pure-FK walk looks like. Lower "
            "'min_stance_frames', or widen 'contact_band'.")

    if gate == "ok":
        says = ("Feet hold. Worst step slides %.1f mm across %d measured step(s) - "
                "under the %.0f mm this gate calls planted."
                % (worst_overall, sum(f["steps_measured"] for f in measured),
                   FOOT_SLIDE_THRESHOLDS["drift_mm"]["ok"]))
    elif gate == "unmeasured":
        says = ("Nothing could be measured: no foot on %r holds still near the ground "
                "for %d frames in %r." % (rig.name, minimum, action.name))
    else:
        says = ("Feet slide. The worst planted step moves %.1f mm (%s, frames %d-%d) - "
                "%s. An FK-keyed leg is the usual cause; key the feet through their IK "
                "targets (rigforge_walk) and measure again."
                % (worst_step["drift_mm"], worst_step["bone"],
                   worst_step["frames"][0], worst_step["frames"][1],
                   "worth a look" if gate == "attention" else "that reads as skating"))

    # --- the four things only a jump can be asked --------------------------
    airborne_report = None
    if mode == "jump":
        fps = 24.0
        render = getattr(scene, "render", None)
        if render is not None:
            fps = float(getattr(render, "fps", 24) or 24) / float(
                getattr(render, "fps_base", 1.0) or 1.0)
        fps = fps / float(frame_step)

        # (c) the parabola. One least-squares quadratic per window; the fit has
        #     to curve downward and the residual has to be small against the
        #     rise. A clip that goes up and comes down in straight lines fits a
        #     near-zero curvature and is caught by the sign, not by the residual.
        window_rows = []
        worst_deviation = None
        parabola_verdict = "unmeasured"
        for index, window in enumerate(windows):
            heights = [body_track[sample].z for sample in window] if body_track else []
            rise = (max(heights) - min(heights)) if heights else 0.0
            fit = _fit_parabola(heights)
            row = {
                "window": index + 1,
                "frames": [frames[window[0]], frames[window[-1]]],
                "samples": len(window),
                # The *detected* window, which is the frames neither foot is on
                # the floor for - a frame or so shorter than the flight, since
                # the takeoff and landing frames are themselves grounded.
                "airborne_frames": len(window),
                "airborne_s": round(len(window) / fps, 4) if fps > 0 else None,
                "rise_mm": round(rise * M_TO_MM, 2),
                "deviation_mm": None,
                "tolerance_mm": round(
                    max(parabola_tolerance * rise, 1e-4) * M_TO_MM, 3),
                "implied_gravity_m_per_s2": None,
                "verdict": "unmeasured",
            }
            if fit is not None:
                a, _b, _c, residual = fit
                gravity = -2.0 * a * fps * fps
                row["deviation_mm"] = round(residual * M_TO_MM, 3)
                row["implied_gravity_m_per_s2"] = round(gravity, 3)
                allowed = max(parabola_tolerance * rise, 1e-4)
                row["verdict"] = ("ok" if (residual <= allowed and gravity > 0.0)
                                  else "fail")
                worst_deviation = (residual if worst_deviation is None
                                   else max(worst_deviation, residual))
            window_rows.append(row)
        verdicts = [row["verdict"] for row in window_rows]
        if verdicts:
            parabola_verdict = "fail" if "fail" in verdicts else (
                "ok" if "ok" in verdicts else "unmeasured")
        if parabola_verdict == "fail":
            bad = next(row for row in window_rows if row["verdict"] == "fail")
            warnings.append(
                "The airborne body does not follow a ballistic arc: over frames %d-%d "
                "it sits up to %s mm off its own best-fit parabola (tolerance %s mm) "
                "and the fit implies g = %s m/s^2. A jump whose height curve is not "
                "v*t - g*t^2/2 reads as floaty however pretty the keys are."
                % (bad["frames"][0], bad["frames"][1], bad["deviation_mm"],
                   bad["tolerance_mm"], bad["implied_gravity_m_per_s2"]))

        # (b) the landing knee, judged the way `bend_direction` judges a fold:
        #     the rig's own forward axis, and the direction this limb is
        #     supposed to apex in.
        knee_rows = []
        knee_verdict = "unmeasured"
        absorb_bone = None
        if knees and windows:
            # NOT the bone the parabola is measured on. The root is where the
            # ballistic arc lives and it is *flat* once the character is back on
            # the floor by construction, so asking it where the absorb bottoms
            # out returns the contact frame and the knee measures 0.00 mm
            # forward on a perfectly good landing. The absorb is a hip that
            # sinks over planted feet, so the bottom is read off whichever body
            # control actually travels vertically after contact.
            contact = windows[-1][-1] + 1
            tail = list(range(contact, len(frames)))
            for name in BODY_CONTROLS:
                track = tracks.get(name)
                if not tail or not track or len(track) <= tail[-1]:
                    continue
                heights = [track[index].z for index in tail]
                span = max(heights) - min(heights)
                if span > 1e-6 and (absorb_bone is None or span > absorb_bone[1]):
                    absorb_bone = (name, span)
            forward, forward_how = rigforge_rig.rig_forward_axis(rig)
            if tail and absorb_bone is not None:
                sink = tracks[absorb_bone[0]]
                bottom = min(tail, key=lambda i: sink[i].z)
                for entry in knees:
                    track = tracks.get(entry["bone"]) or []
                    if len(track) <= bottom:
                        continue
                    sign = rigforge_rig.POLE_DIRECTION.get(
                        "front_leg" if "front" in entry["limb"] else "leg", 1.0)
                    want = forward * sign
                    travel = track[bottom] - track[contact]
                    along = travel.dot(want)
                    knee_rows.append({
                        "limb": entry["limb"],
                        "joint": entry["bone"],
                        "expected": "forward" if sign > 0 else "backward",
                        "contact_frame": frames[contact],
                        "absorb_frame": frames[bottom],
                        "travel_mm": round(travel.length * M_TO_MM, 2),
                        "travel_along_mm": round(along * M_TO_MM, 2),
                        "required_mm": LANDING_KNEE_TRAVEL_MM,
                        "correct": bool(along * M_TO_MM >= LANDING_KNEE_TRAVEL_MM),
                    })
        if knee_rows:
            knee_verdict = "ok" if all(row["correct"] for row in knee_rows) else "fail"
        if knee_verdict == "fail":
            bad = min(knee_rows, key=lambda row: row["travel_along_mm"])
            warnings.append(
                "The landing does not absorb anatomically: %s travels %+.2f mm %s "
                "between contact (frame %d) and the bottom of the absorb (frame %d), "
                "when a knee has to apex %s by at least %.1f mm. Either the landing is "
                "stiff-legged, or this leg folds the wrong way and rig_check's "
                "bend_direction gate will say so too."
                % (bad["joint"], bad["travel_along_mm"], bad["expected"],
                   bad["contact_frame"], bad["absorb_frame"], bad["expected"],
                   LANDING_KNEE_TRAVEL_MM))

        # (d) hop asymmetry. Off unless the caller says what it will tolerate:
        #     plenty of jumps are *meant* to be hops, and a gate that assumes
        #     otherwise is a gate people switch off.
        single = [sum(1 for bone in foot_grounded if foot_grounded[bone][index]) == 1
                  for index in range(len(frames))]
        hop_runs = _runs_of(single, 1)
        longest_hop = max((len(run) for run in hop_runs), default=0)
        hop_verdict = "unmeasured"
        if hop_tolerance is not None and len(specs) >= 2:
            hop_verdict = "ok" if longest_hop <= hop_tolerance else "fail"
            if hop_verdict == "fail":
                run = max(hop_runs, key=len)
                warnings.append(
                    "This two-foot jump spends %d consecutive frames (%d-%d) with "
                    "exactly one foot on the ground, over the %d frame(s) "
                    "'hop_tolerance_frames' allows. One foot leaving or landing ahead "
                    "of the other is a hop, not a jump."
                    % (longest_hop, frames[run[0]], frames[run[-1]], hop_tolerance))

        airborne_report = {
            "windows": window_rows,
            "airborne_frames": sum(len(window) for window in windows),
            "ground_mm": round(ground_z * M_TO_MM, 2),
            "clearance_mm": round((ceiling_z - ground_z) * M_TO_MM, 2),
            "airborne_clearance": round(clearance, 4),
            "min_airborne_frames": min_airborne,
            "foot_range_mm": round(foot_range * M_TO_MM, 2),
            "body_rise_mm": round(body_rise * M_TO_MM, 2),
            "detected": bool(looks_airborne),
            "fps": round(fps, 4),
            "parabola_tolerance": round(parabola_tolerance, 5),
            "max_parabola_deviation_mm": (round(worst_deviation * M_TO_MM, 3)
                                          if worst_deviation is not None else None),
            "parabola": parabola_verdict,
            "landing_knees": knee_rows,
            "landing_knee": knee_verdict,
            "absorb_bone": absorb_bone[0] if absorb_bone else None,
            "absorb_travel_mm": (round(absorb_bone[1] * M_TO_MM, 2)
                                 if absorb_bone else None),
            "longest_single_foot_run": longest_hop,
            "hop_tolerance_frames": hop_tolerance,
            "hop_asymmetry": hop_verdict,
            "plants_measured": sum(foot["steps_measured"] for foot in feet_report),
            "says": None,
        }

        broken = [name for name, value in (("the ballistic arc", parabola_verdict),
                                           ("the landing knees", knee_verdict),
                                           ("hop asymmetry", hop_verdict))
                  if value == "fail"]
        if broken:
            gate = "fail"
            says = ("The plants hold (worst %s mm) but %s did not: see the warnings."
                    % (worst_overall, " and ".join(broken))
                    if _slide_band(worst_overall) == "ok" and measured else
                    "%s And %s did not hold either." % (says, " and ".join(broken)))
        airborne_report["says"] = (
            "Airborne for %d frame(s) (%s s off the floor): the body rises %.0f mm and "
            "holds its parabola to %s mm (tolerance %s mm, implied g %s m/s^2); the "
            "landing knees travel %s; %d plant(s) measured, worst drift %s mm."
            % (airborne_report["airborne_frames"],
               window_rows[0]["airborne_s"] if window_rows else "?",
               body_rise * M_TO_MM, airborne_report["max_parabola_deviation_mm"],
               window_rows[0]["tolerance_mm"] if window_rows else "?",
               window_rows[0]["implied_gravity_m_per_s2"] if window_rows else "?",
               ", ".join("%s %+.2f mm %s" % (row["joint"], row["travel_along_mm"],
                                             row["expected"])
                         for row in knee_rows) or "unmeasured",
               airborne_report["plants_measured"], worst_overall))
        says = "%s %s" % (says, airborne_report["says"])

    return {
        "rig": rig.name,
        "action": action.name,
        "mode": mode,
        "mode_reason": reason,
        "airborne": airborne_report,
        "frames": [frames[0], frames[-1]],
        "frame_step": frame_step,
        "samples": len(frames),
        "looping": looping,
        "contact_band": round(band, 4),
        "min_stance_frames": minimum,
        "body_bone": body_bone,
        "body_travel_mm": round(body_travel * M_TO_MM, 2),
        "treadmill_mm_per_frame": round(treadmill.length * M_TO_MM, 3),
        "feet": feet_report,
        "steps_measured": sum(foot["steps_measured"] for foot in feet_report),
        "worst_step": worst_step,
        "worst_drift_mm": worst_overall,
        "gate": gate,
        "thresholds": FOOT_SLIDE_THRESHOLDS,
        "threshold_tier": (
            "heuristic (proxy tier): the scale at which a slide becomes visible, not "
            "values calibrated against artist accept/reject decisions. The measurement "
            "is reported next to the band that judged it."),
        "says": says,
        "pose_restored": True,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


def _resolve_rig_loose(params):
    """:func:`_resolve_rig`, but a rig with no mesh on it is still a rig.

    ``rig_check`` needs a skinned mesh because it measures flesh; foot slide is
    measured on the bones, so an unskinned skeleton is a perfectly good subject
    and refusing it would be a rule with no reason behind it.
    """
    try:
        return _resolve_rig(params)
    except ForgeError:
        name = params.get("rig")
        if isinstance(name, str) and name.strip():
            raise
        candidates = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
        if len(candidates) == 1:
            return candidates[0]
        active = bpy.context.view_layer.objects.active
        if active is not None and active.type == "ARMATURE":
            return active
        raise


# ---------------------------------------------------------------------------
# operator (the panel button)
# ---------------------------------------------------------------------------

class FORGE_OT_rig_check(rigforge_rig._RigOperator):
    bl_idname = "forge.rig_check"
    bl_label = "Check Deformation"
    bl_description = ("Pose every limb, spine and neck joint to its extremes and measure "
                      "volume loss, new self-intersections and twist collapse")

    def execute(self, context):
        def work(obj, props):
            rigforge.set_status(props, "Posing the rig and measuring ...")
            result = cmd_rig_check({"mesh": obj.name})
            props.summary = "rig_check: %s (%d joint(s), %.1fs)" % (
                result["gate"], result["joints_measured"], result["seconds"])
            rigforge.set_status(props, result.get("says") or "Rig checked.",
                                error=result["gate"] == "fail")
            return {"FINISHED"}

        return self.guarded(context, work)


_CLASSES = (FORGE_OT_rig_check,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:  # pragma: no cover
            pass
