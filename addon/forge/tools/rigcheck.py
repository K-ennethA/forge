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
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import diagnose
from . import rigforge
from . import rigforge_rig
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

    def __init__(self, rig, mesh, joint, weight_floor=0.05):
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
        own = _weighted_vertices(
            mesh, _group_indices(mesh, set(joint["deform_bones"])), weight_floor)
        # How thick the limb is, not just how long the bone is. A blob leg is
        # wider than 60% of its own shin, and a neighbourhood measured only in
        # bone lengths would sit entirely inside the flesh and catch no surface
        # at all — which is exactly how a joint gets silently skipped.
        girth = 0.0
        if own:
            distances = sorted(
                rigforge_rig._point_segment_distance(world @ mesh.data.vertices[i].co,
                                                     self.head, self.tail)
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
            point = world @ mesh.data.vertices[index].co
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
    "joints"?: [names], "max_poses"?, "intersections"?, "weight_floor"?}``

    Returns a per-joint report — worst volume loss, new self-intersections and
    twist collapse, each with a band — plus an overall gate.  The pose is always
    restored.
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

            probes = []
            for joint in joints:
                probe = JointProbe(rig, mesh, joint, weight_floor)
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

            rest_coords = _vertex_coords(mesh)
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

    attention = [j["label"] for j in report_joints if j["verdict"] == "attention"]
    failed = [j["label"] for j in report_joints if j["verdict"] == "fail"]
    gate = "fail" if failed else ("attention" if attention else "pass")
    lines = []
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


@command("animation_check")
def cmd_animation_check(params):
    """Measure foot slide on a clip: per-step drift in millimetres, with a verdict.

    ``animation_check {"rig"?, "action"?, "mode"?: "auto"|"planted"|"in_place",
    "frame_step"?, "contact_band"?, "min_stance_frames"?, "feet"?: [bones]}``

    Deterministic and geometric — no render, no model, nothing judged by eye.
    The rig's pose, action and the scene's frame are all restored.
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
                       "IN_PLACE": "in_place", "INPLACE": "in_place"}, "auto")
    frame_step = get_int(params, "frame_step", 1, minimum=1, maximum=10)
    band = get_float(params, "contact_band", CONTACT_BAND, minimum=0.01, maximum=0.9)
    minimum = get_int(params, "min_stance_frames", MIN_STANCE_FRAMES, minimum=2,
                      maximum=1000)

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
    if mode == "auto":
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

    return {
        "rig": rig.name,
        "action": action.name,
        "mode": mode,
        "mode_reason": reason,
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
