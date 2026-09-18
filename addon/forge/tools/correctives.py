"""``rigforge_correctives`` — corrective shape keys (JCMs) driven by bend angle.

Why this module exists, measured rather than assumed
----------------------------------------------------
:mod:`forge.tools.rigcheck` measures what a joint does to the flesh at its
extremes.  On a real project it came back with **40-50% volume loss** at the
knees and elbows at 90 degrees of flex, and the obvious first fix was tried and
**failed**: a genuine edge-loop density pass around the joints moved the number
from **50.3% to 51.1%** — nothing, inside the noise.  That was not a bad
retopology pass, it was the ceiling of the technique.

Linear-blend skinning (which is what an armature modifier does, and what every
engine does on the GPU) computes a vertex's posed position as a *weighted
average of rigid transforms*.  Averaging two rotations linearly gives a matrix
that is shorter than either — the further apart the two bones point, the more it
shrinks.  At 90 degrees the shrink is the collapse, and it happens per vertex
regardless of how many vertices there are.  Adding edge loops adds samples of a
function that is wrong at every sample.

The fix every production pipeline uses is a **corrective shape key driven by the
joint's bend angle** — a JCM (joint-corrective morph), also called a PSD or a
"pose-space deformer".  It is not an artistic flourish; it is the standard
correction term for the known error of the standard skinning approximation.

What this module authors
------------------------
For each target joint, at each sampled bend angle:

1. **Pose the limb the way the harness poses it.**  The same control bone, the
   same computed bend sign, the same forced-FK switches — so the angle the
   corrective is authored at is *exactly* the angle the harness reports on, and
   the before/after numbers are comparable by construction.
2. **Measure the collapse, do not guess it.**  Every vertex in the joint's
   neighbourhood (``rigcheck.JointProbe``'s own region, so the correction and
   the measurement agree about what "the joint" is) gets a **radius**: its
   distance to the limb's two-segment skeleton — parent bone head, joint, child
   bone tail.  The radius is measured at rest against the rest skeleton and
   again at the pose against the posed skeleton, and the shortfall
   ``max(0, rest - posed)`` is the pinch, in metres.  It is rotation-invariant
   by construction, which is the whole point: a bent limb is allowed to move,
   it is not allowed to get thinner.
3. **Restore it along the axis it was lost on.**  The displacement is the pinch,
   pushed back out radially from the posed skeleton, scaled by ``strength`` and
   by a **blend-band mask** ``min(1, 2*sqrt(w_parent * w_child))`` — which is
   zero wherever a vertex belongs to one bone alone and peaks exactly where the
   two bones are averaged, because that is where linear blending does its
   damage.  The whole field is then **Laplacian-smoothed over the mesh's edges**
   (:func:`forge.tools.silhouette._smooth_field`, the same smoother the
   silhouette fit uses) so the correction is a fair surface rather than a
   per-vertex reading of the noise.  Nothing here is sculpted or eyeballed.
4. **Write it in the REST frame, which is the part that is easy to get wrong.**

The rest-space math (the inverse transform), stated
---------------------------------------------------
A shape key is a *rest-pose* offset.  The armature deforms the result.  So a
delta that looks right in the posed frame is **not** the delta to store — it has
to be pulled back through the deformation that will be applied to it.

Linear-blend skinning transforms a rest vertex :math:`r_i` to

.. math::  p_i = M_i \\, r_i, \\qquad
           M_i = \\Big(\\sum_b w_{ib}\\Big)^{-1} \\sum_b w_{ib}\\,
                 \\big(P_b \\, A_b^{-1}\\big)

where :math:`P_b` is the bone's posed matrix (``pose_bone.matrix``), :math:`A_b`
its rest matrix (``bone.matrix_local``), both in **armature space**, and
:math:`w_{ib}` the vertex group weight.  The mesh's own object transform is
conjugated in, exactly as Blender's armature modifier does it::

    M_local = mesh.matrix_world^-1 @ rig.matrix_world
              @ M_arm
              @ rig.matrix_world^-1 @ mesh.matrix_world

If the posed position should be :math:`p_i + d_i` instead of :math:`p_i`, then
the rest position must become :math:`r_i + \\Delta r_i` with

.. math::  M_i (r_i + \\Delta r_i) = M_i r_i + d_i
           \\;\\Longrightarrow\\;
           \\Delta r_i = L_i^{-1} d_i

where :math:`L_i` is the **3x3 linear part** of :math:`M_i` (the translation
cancels between the two sides — which is why the pull-back is a linear solve and
not a full inverse-transform of a point).  ``Δr_i`` is what is added to the
Basis key's coordinates to make the corrective key.

That derivation is only worth anything if the :math:`M_i` we build really is the
one Blender applies, so it is **checked against the depsgraph** on every run:
``M_i r_i`` is compared with the evaluated mesh's own vertex positions at the
same pose and the worst disagreement is reported as ``skin_residual_mm``.  A
large residual means the mesh is deformed by something other than plain
vertex-group skinning (dual-quaternion "preserve volume", a shrinkwrap, a
corrective smooth), and the run says so instead of quietly authoring nonsense.

The driver
----------
Each key is driven by the **angle between the two bones that span the joint**, a
``ROTATION_DIFF`` driver variable, valued 0 at the rest angle and ramping to 1
at the sampled bend.  Two deliberate choices:

* **The bones are the deform (or ``ORG-``) bones, not the FK controls.**  A
  Rigify limb can be posed in FK *or* solved in IK, and only the bones at the
  bottom of that stack move in both cases.  The candidates are tried in order
  and the first pair that actually *sweeps* between rest and the sample wins —
  measured, then recorded in the report, so an IK-posed limb drives its
  correctives exactly as an FK-posed one does.
* **The ramp is keyframes on the driver F-curve, not a Python expression.**
  Blender's Python-expression drivers are gated behind the "Auto Run Python
  Scripts" preference; a rig that silently stops correcting itself on someone
  else's machine is worse than no corrective.  ``AVERAGE`` over one variable
  plus keyframe points with ``CONSTANT`` extrapolation needs no Python at all.
  With several samples the ramps are triangular — key *j* peaks at its own angle
  and falls to zero at its neighbours' — so the keys interpolate between each
  other instead of stacking.

Nothing renders, nothing downloads, nothing is written to disk, and the pose is
restored in a ``finally``.
"""

import math
import time

import bpy
from mathutils import Vector

from . import rigcheck
from . import rigforge_rig
from . import silhouette
from .common import (
    M_TO_MM,
    get_bool,
    get_float,
    object_mode,
    refresh_view_layer,
)
from .registry import ForgeError, command

try:  # Blender ships numpy; the guard keeps this honest if a build ever does not.
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is part of Blender
    _np = None

__all__ = [
    "CORRECTIVE_PREFIX",
    "DEFAULT_SAMPLES",
    "PUSH_DIRECTION",
    "corrective_keys",
    "key_name_for",
    "skin_matrices",
    "cmd_rigforge_correctives",
]


# ---------------------------------------------------------------------------
# conventions
# ---------------------------------------------------------------------------

#: Every key this module writes starts with this, so ``report`` and ``clear``
#: can find their own work without a manifest and without touching an artist's
#: hand-sculpted keys.
CORRECTIVE_PREFIX = "corr_"

#: Fractions of the harness's own extreme for this joint.  Two samples, not a
#: sweep, for the same reason ``rig_check`` runs three poses and not thirty: the
#: failure lives at the extreme, and one intermediate keeps the ramp from being
#: a straight line through a curved error.
DEFAULT_SAMPLES = (0.5, 1.0)

#: The push is **radial** — outward from the limb's own posed skeleton — and
#: there is no option, which is a decision that was measured rather than assumed.
#:
#: Pushing along the posed surface normal is the more familiar description of
#: "restore the volume", and it was implemented, run against the same synthetic
#: limb, and **thrown away**: it took the knee's volume loss at 90 degrees from
#: 29.7% to **39.4%**, worse than doing nothing, where radial takes it to 13.6%.
#: The reason is not that normals are a bad idea, it is that the combination is
#: incoherent — the *magnitude* is measured as a loss of distance to the
#: skeleton, so spending it along a different axis over- and under-shoots
#: everywhere, and in the crease of a fold the two facing walls' normals point
#: at each other, so the correction closes the fold it was meant to pad.
#: Measurement and restoration have to share an axis.
PUSH_DIRECTION = "radial"

#: A driver bone pair has to sweep at least this many degrees between rest and
#: the widest sample, or it is not measuring the bend and the next candidate is
#: tried.
MIN_DRIVER_SWEEP_DEG = 5.0

#: Above this the skinning model and the depsgraph disagree enough that the
#: pull-back is not trustworthy, and the run says so.
SKIN_RESIDUAL_WARN_MM = 0.5

#: Laplacian smoothing of the displacement field, 0-1.  The mesh is never
#: smoothed; only the correction is.
DEFAULT_SMOOTH = 0.5

#: A vertex must own at least this much weight on a joint's bones to be part of
#: its neighbourhood — the same floor ``rig_check`` uses.
DEFAULT_WEIGHT_FLOOR = 0.05

#: Displacements below this are not worth a key's storage.
MIN_PUSH_MM = 0.01


def key_name_for(joint, flex_deg):
    """``knee.L`` at 90 degrees -> ``corr_knee_L_090``."""
    stem = str(joint).replace(".", "_").replace(" ", "_")
    return "%s%s_%03d" % (CORRECTIVE_PREFIX, stem, int(round(abs(flex_deg))))


def corrective_keys(mesh):
    """Every key block on ``mesh`` this module wrote, in file order."""
    keys = getattr(mesh.data, "shape_keys", None)
    if keys is None:
        return []
    return [block for block in keys.key_blocks
            if block.name.startswith(CORRECTIVE_PREFIX)]


def _require_numpy():
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so rigforge_correctives cannot run — "
            "the pull-back from posed space to rest space is a per-vertex linear "
            "solve and there is no sane pure-Python version of it.")


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _closest_on_polyline(point, points):
    """Nearest point on a polyline, and the distance to it."""
    best = None
    best_distance = None
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        span = b - a
        length_sq = span.length_squared
        if length_sq < 1e-18:
            candidate = a.copy()
        else:
            t = (point - a).dot(span) / length_sq
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            candidate = a + span * t
        distance = (point - candidate).length
        if best_distance is None or distance < best_distance:
            best, best_distance = candidate, distance
    if best is None:  # pragma: no cover - a one-point polyline
        return points[0].copy(), (point - points[0]).length
    return best, best_distance


def _local_coords(mesh_obj):
    """The mesh's own (undeformed, pre-modifier) vertex coordinates."""
    count = len(mesh_obj.data.vertices)
    flat = _np.empty(count * 3, dtype="f8")
    mesh_obj.data.vertices.foreach_get("co", flat)
    return flat.reshape(count, 3)


def _basis_coords(mesh_obj):
    """The coordinates the armature actually deforms: the Basis key, or the mesh.

    When a mesh carries shape keys, Blender evaluates the key mix *before* the
    modifier stack and the mesh's own ``vertices[i].co`` is not what gets
    deformed at all.  A corrective written against the wrong base is off by the
    difference between the two, everywhere, silently.
    """
    keys = getattr(mesh_obj.data, "shape_keys", None)
    if keys is None or not len(keys.key_blocks):
        return _local_coords(mesh_obj)
    basis = keys.key_blocks[0]
    count = len(basis.data)
    flat = _np.empty(count * 3, dtype="f8")
    basis.data.foreach_get("co", flat)
    return flat.reshape(count, 3)


def _evaluated_local_coords(mesh_obj):
    """Vertex positions of the **evaluated** mesh, in the object's own space.

    Shape keys and drivers are part of that evaluation; this is the same mesh
    ``rig_check`` measures, read in local rather than world space because the
    skinning model is checked against it.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = mesh_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        count = len(mesh.vertices)
        flat = _np.empty(count * 3, dtype="f8")
        mesh.vertices.foreach_get("co", flat)
        return flat.reshape(count, 3)
    finally:
        evaluated.to_mesh_clear()


def _edge_pairs(mesh_obj):
    count = len(mesh_obj.data.edges)
    if not count:
        return _np.zeros((0, 2), dtype="i4")
    flat = _np.empty(count * 2, dtype="i4")
    mesh_obj.data.edges.foreach_get("vertices", flat)
    return flat.reshape(count, 2)


def _group_weights(mesh_obj, names):
    """``(n,)`` array of each vertex's summed weight over a set of vertex groups."""
    indices = {group.index for group in mesh_obj.vertex_groups if group.name in names}
    out = _np.zeros(len(mesh_obj.data.vertices), dtype="f8")
    if not indices:
        return out
    for vertex in mesh_obj.data.vertices:
        total = 0.0
        for entry in vertex.groups:
            if entry.group in indices:
                total += entry.weight
        out[vertex.index] = total
    return out


# ---------------------------------------------------------------------------
# the skinning model, and the proof that it is Blender's
# ---------------------------------------------------------------------------

def skin_matrices(mesh_obj, rig, indices):
    """``{vertex index: 4x4 numpy array}`` — the blended skinning transform now.

    Built exactly the way Blender's armature modifier builds it: per deform
    bone, ``pose_bone.matrix @ bone.matrix_local.inverted()`` in armature space,
    averaged by the vertex's group weights and normalised by their sum, then
    conjugated into the mesh object's own space.  See the module docstring for
    the derivation this feeds.
    """
    deform = {}
    for bone in rig.data.bones:
        if not bone.use_deform:
            continue
        pose_bone = rig.pose.bones.get(bone.name)
        if pose_bone is None:  # pragma: no cover - defensive
            continue
        try:
            rest_inverse = bone.matrix_local.inverted()
        except ValueError:  # pragma: no cover - a degenerate bone
            continue
        deform[bone.name] = _np.array(pose_bone.matrix @ rest_inverse, dtype="f8")

    by_index = {}
    for group in mesh_obj.vertex_groups:
        if group.name in deform:
            by_index[group.index] = deform[group.name]

    pre = _np.array(rig.matrix_world.inverted_safe() @ mesh_obj.matrix_world, dtype="f8")
    post = _np.array(mesh_obj.matrix_world.inverted_safe() @ rig.matrix_world, dtype="f8")
    identity = _np.eye(4, dtype="f8")

    out = {}
    vertices = mesh_obj.data.vertices
    for index in indices:
        total = 0.0
        accumulated = _np.zeros((4, 4), dtype="f8")
        for entry in vertices[index].groups:
            matrix = by_index.get(entry.group)
            if matrix is None or entry.weight <= 0.0:
                continue
            accumulated += matrix * float(entry.weight)
            total += float(entry.weight)
        if total <= 1e-12:
            out[index] = identity.copy()
            continue
        out[index] = post @ (accumulated / total) @ pre
    return out


def _skin_residual_mm(matrices, base_coords, evaluated_coords, indices):
    """Worst disagreement between our skinning model and the depsgraph, in mm."""
    worst = 0.0
    for index in indices:
        matrix = matrices[index]
        rest = base_coords[index]
        predicted = matrix[:3, :3] @ rest + matrix[:3, 3]
        error = float(_np.linalg.norm(predicted - evaluated_coords[index]))
        if error > worst:
            worst = error
    return worst * M_TO_MM


# ---------------------------------------------------------------------------
# the joint's skeleton, at rest and posed
# ---------------------------------------------------------------------------

def _joint_bones(rig, joint):
    """``(parent deform names, child deform names)`` for one harness joint."""
    child = [name for name in joint["deform_bones"] if name in rig.data.bones]
    parent = sorted(rigcheck._parent_deform_names(rig, joint["deform_bones"]))
    parent = [name for name in parent if name in rig.data.bones]
    return parent, child


def _skeleton(rig, parent_names, child_names, posed):
    """The limb's three points — proximal, joint, distal — in mesh-independent world space.

    ``posed`` reads the pose bones (where the limb is now); otherwise the rest
    bone matrices are used.  Both are world space so the two are comparable.
    """
    matrix = rig.matrix_world
    if posed:
        def head(name):
            return matrix @ rig.pose.bones[name].head

        def tail(name):
            return matrix @ rig.pose.bones[name].tail
    else:
        def head(name):
            return matrix @ rig.data.bones[name].head_local

        def tail(name):
            return matrix @ rig.data.bones[name].tail_local

    child_sorted = sorted(child_names)
    joint_point = head(child_sorted[0])
    distal = tail(child_sorted[-1])
    if parent_names:
        proximal = head(sorted(parent_names)[0])
    else:
        # No flesh above the joint: fall back to mirroring the child segment, so
        # the polyline still has two limbs and the radius still means something.
        proximal = joint_point + (joint_point - distal)
    return [proximal, joint_point, distal]


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------

def _org_name(name):
    if name.startswith(rigforge_rig.DEF_PREFIX):
        return "ORG-" + name[len(rigforge_rig.DEF_PREFIX):]
    return None


def _driver_candidates(rig, joint, parent_names, child_names):
    """Bone pairs that might measure this joint's bend, best first."""
    pairs = []
    if parent_names and child_names:
        pairs.append((sorted(parent_names)[-1], sorted(child_names)[0]))
        org_parent = _org_name(sorted(parent_names)[-1])
        org_child = _org_name(sorted(child_names)[0])
        if org_parent and org_child:
            pairs.append((org_parent, org_child))
    control = rig.pose.bones.get(joint["control"])
    if control is not None and control.parent is not None:
        pairs.append((control.parent.name, control.name))
    seen = set()
    out = []
    for a, b in pairs:
        if a == b or (a, b) in seen:
            continue
        if a not in rig.pose.bones or b not in rig.pose.bones:
            continue
        seen.add((a, b))
        out.append((a, b))
    return out


def _rotation_difference(rig, a, b):
    """The ``ROTATION_DIFF`` driver's own quantity, in radians, read directly."""
    qa = (rig.matrix_world @ rig.pose.bones[a].matrix).to_quaternion()
    qb = (rig.matrix_world @ rig.pose.bones[b].matrix).to_quaternion()
    return qa.rotation_difference(qb).angle


def _make_driver(shape_keys, key_name, rig, bone_a, bone_b, ramp):
    """A ``ROTATION_DIFF`` driver on one key's value, ramped by keyframes.

    ``ramp`` is ``[(angle_radians, value), ...]`` in ascending angle.  No Python
    expression is used anywhere: expression drivers are gated behind the "Auto
    Run Python Scripts" preference, and a corrective that silently stops firing
    on the next machine is worse than no corrective at all.
    """
    path = 'key_blocks["%s"].value' % key_name
    try:
        shape_keys.driver_remove(path)
    except (TypeError, RuntimeError):  # pragma: no cover - nothing to remove
        pass
    fcurve = shape_keys.driver_add(path)
    driver = fcurve.driver
    driver.type = "AVERAGE"
    for variable in list(driver.variables):
        driver.variables.remove(variable)
    variable = driver.variables.new()
    variable.name = "bend"
    variable.type = "ROTATION_DIFF"
    variable.targets[0].id = rig
    variable.targets[0].bone_target = bone_a
    variable.targets[1].id = rig
    variable.targets[1].bone_target = bone_b

    # A fresh driver F-curve comes with a GENERATOR modifier that outputs the
    # variable unchanged and overrides any keyframes; it has to go first.
    for modifier in list(fcurve.modifiers):
        fcurve.modifiers.remove(modifier)
    # Backwards by index, not over a cached list: removing a keyframe reshuffles
    # the collection and the stale references that come back are not "in" it any
    # more, which Blender reports as ``Keyframe not in F-Curve``.
    while len(fcurve.keyframe_points):
        fcurve.keyframe_points.remove(fcurve.keyframe_points[-1])
    for angle, value in ramp:
        point = fcurve.keyframe_points.insert(float(angle), float(value))
        point.interpolation = "LINEAR"
    fcurve.extrapolation = "CONSTANT"
    fcurve.update()
    return fcurve


def _build_ramps(angles):
    """Triangular ramps for a sorted list of ``(rest_angle, sample angles...)``.

    Key *j* is 0 at its lower neighbour, 1 at its own angle and 0 again at its
    upper neighbour, so several correctives on one joint interpolate between
    each other instead of stacking into a balloon.  The last key holds at 1
    beyond its own angle (``CONSTANT`` extrapolation), because a joint bent
    further than the sample needs *more* correction, not less.
    """
    rest, samples = angles[0], list(angles[1:])
    ramps = []
    for index, angle in enumerate(samples):
        lower = samples[index - 1] if index else rest
        points = [(lower, 0.0), (angle, 1.0)]
        if index + 1 < len(samples):
            points.append((samples[index + 1], 0.0))
        ramps.append(points)
    return ramps


# ---------------------------------------------------------------------------
# authoring one joint
# ---------------------------------------------------------------------------

def _shape_keys_for(mesh_obj, notes):
    """The mesh's ``Key`` datablock, created (with a Basis) if it has none."""
    if mesh_obj.data.shape_keys is None:
        mesh_obj.shape_key_add(name="Basis", from_mix=False)
        notes.append(
            "%r had no shape keys, so a 'Basis' key was created from its own "
            "untouched vertices first — a mesh cannot hold a morph target "
            "without the shape it morphs from" % mesh_obj.name)
    return mesh_obj.data.shape_keys


def _write_key(mesh_obj, name, coords):
    """Write absolute rest-space coordinates into a named key, base untouched.

    The key is left **muted** — the caller unmutes every corrective once the
    whole run is authored, so no key can be measured through another one.
    """
    blocks = mesh_obj.data.shape_keys.key_blocks
    existing = blocks.get(name)
    overwritten = existing is not None
    if overwritten:
        key = existing
    else:
        key = mesh_obj.shape_key_add(name=name, from_mix=False)
        try:
            key.slider_min = 0.0
            key.slider_max = 1.0
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            pass
    key.data.foreach_set("co", coords.astype("f8").reshape(-1))
    key.value = 0.0
    key.mute = True
    mesh_obj.data.update()
    return key, overwritten


def _measure_collapse(rig, mesh_obj, indices, parent_names, child_names,
                      rest_skeleton, rest_local):
    """One posed sample: the pinch per vertex, and where to push it back.

    Returns ``(displacement, matrices, stats)``.  ``displacement`` is an
    ``(n, 3)`` array in the mesh's own space, zero outside the joint's
    neighbourhood; ``matrices`` are this pose's per-vertex skinning transforms.
    """
    posed_local = _evaluated_local_coords(mesh_obj)
    matrices = skin_matrices(mesh_obj, rig, indices)
    residual_mm = _skin_residual_mm(matrices, rest_local, posed_local, indices)

    posed_skeleton = _skeleton(rig, parent_names, child_names, posed=True)
    world = mesh_obj.matrix_world
    inverse_world = mesh_obj.matrix_world.inverted_safe().to_3x3()

    displacement = _np.zeros_like(rest_local)
    collapse_total = 0.0
    worst_collapse = 0.0
    touched = 0
    for index in indices:
        rest_point = world @ Vector(rest_local[index])
        posed_point = world @ Vector(posed_local[index])
        _, rest_radius = _closest_on_polyline(rest_point, rest_skeleton)
        nearest, posed_radius = _closest_on_polyline(posed_point, posed_skeleton)
        pinch = rest_radius - posed_radius
        if pinch <= 0.0:
            continue
        # Outward from the posed skeleton: the same axis the pinch was measured
        # along, so the restoration is the measurement's inverse rather than a
        # second guess. See PUSH_DIRECTION for the alternative that was tried.
        push = posed_point - nearest
        if push.length < 1e-9:
            continue
        push.normalize()
        # Back into the mesh's own space: the pull-back below is a local-space
        # linear solve and mixing frames here is the classic silent error.
        local_push = inverse_world @ push
        if local_push.length < 1e-12:  # pragma: no cover - a degenerate transform
            continue
        local_push.normalize()
        displacement[index] = _np.array(local_push * pinch, dtype="f8")
        collapse_total += pinch
        worst_collapse = max(worst_collapse, pinch)
        touched += 1

    stats = {
        "region_vertices": len(indices),
        "pinched_vertices": touched,
        "worst_pinch_mm": round(worst_collapse * M_TO_MM, 3),
        "mean_pinch_mm": round((collapse_total / touched if touched else 0.0) * M_TO_MM, 3),
        "skin_residual_mm": round(residual_mm, 4),
    }
    return displacement, matrices, stats


def _pull_back(displacement, matrices, indices):
    """``Δr = L^-1 d`` per vertex — posed-space push to rest-space offset."""
    out = _np.zeros_like(displacement)
    singular = 0
    for index in indices:
        wanted = displacement[index]
        if not _np.any(wanted):
            continue
        linear = matrices[index][:3, :3]
        try:
            out[index] = _np.linalg.solve(linear, wanted)
        except _np.linalg.LinAlgError:  # pragma: no cover - a collapsed blend
            singular += 1
            continue
    return out, singular


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def _sample_angles(params, joint):
    """The bend angles to correct at, in degrees, ascending.

    Bare numbers are **fractions** of the harness's own extreme for that joint
    (so ``[0.5, 1.0]`` on a 140-degree knee is 70 and 140 degrees, and the
    samples track the joint rather than needing a table per limb).  A
    ``{"flex_deg": 90}`` object is that angle, verbatim — the same shape
    ``rig_check``'s ``poses`` takes.
    """
    raw = params.get("angle_samples")
    extreme = float(joint["flex_deg"])
    if raw is None:
        entries = list(DEFAULT_SAMPLES)
    elif isinstance(raw, (list, tuple)) and raw:
        entries = list(raw)
    else:
        raise ForgeError(
            "'angle_samples' is a list of bend angles to correct at — fractions of "
            "this joint's own extreme (%.0f degrees here), like [0.5, 1.0], or "
            "{\"flex_deg\": 90} objects for an exact angle. Got %r."
            % (extreme, raw))

    angles = []
    for entry in entries:
        if isinstance(entry, dict):
            try:
                angles.append(float(entry.get("flex_deg")))
            except (TypeError, ValueError):
                raise ForgeError(
                    "An 'angle_samples' object needs a numeric 'flex_deg'; got %r."
                    % (entry,))
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            fraction = float(entry)
            if not 0.0 < fraction <= 1.0:
                raise ForgeError(
                    "An 'angle_samples' number is a FRACTION of the joint's extreme, "
                    "so it has to be above 0 and at most 1 (got %g). For an exact "
                    "angle in degrees, pass {\"flex_deg\": %g}." % (fraction, fraction))
            angles.append(fraction * extreme)
        else:
            raise ForgeError(
                "'angle_samples' takes numbers (fractions of the joint's extreme) or "
                "{\"flex_deg\": N} objects; got %r." % (entry,))
    angles = sorted({round(angle, 4) for angle in angles if abs(angle) > 1e-6})
    if not angles:
        raise ForgeError("'angle_samples' resolved to no usable angle for %s."
                         % joint["joint"])
    return angles


def _worst_joints(rig, mesh, params, warnings):
    """The joints the harness says are actually losing volume, worst first."""
    report = rigcheck.cmd_rig_check({
        "rig": rig.name, "mesh": mesh.name, "poses": "quick",
        "intersections": False,
    })
    scored = [entry for entry in report["joints"]
              if entry.get("worst_volume_loss_pct") is not None]
    failing = [entry for entry in scored
               if entry["verdicts"]["volume"] != "ok"]
    failing.sort(key=lambda entry: -entry["worst_volume_loss_pct"])
    if not failing:
        best = max((entry["worst_volume_loss_pct"] for entry in scored), default=None)
        raise ForgeError(
            "No joint on %r loses enough volume at full flex to be worth a "
            "corrective: the worst is %s against a %.0f%% band, so the harness "
            "already passes it. Name the joints explicitly in 'joints' if you want "
            "correctives anyway."
            % (rig.name,
               ("%.1f%%" % best) if best is not None else "unmeasured",
               rigcheck.THRESHOLDS["volume_loss_pct"]["ok"]))
    warnings.append(
        "'joints' was not given, so the harness picked the joints it measures as "
        "failing on volume at full flex: %s."
        % ", ".join("%s (%.1f%%)" % (entry["joint"], entry["worst_volume_loss_pct"])
                    for entry in failing))
    return [entry["joint"] for entry in failing]


def _harness(rig, mesh, joint_names, plan, weight_floor):
    """One ``rig_check`` run over exactly the joints and angles in play."""
    poses = [{"label": "flex %.0f deg" % angle, "flex_deg": angle, "twist_deg": 0.0}
             for angle in plan]
    report = rigcheck.cmd_rig_check({
        "rig": rig.name, "mesh": mesh.name, "joints": list(joint_names),
        "poses": poses, "intersections": False, "weight_floor": weight_floor,
    })
    out = {}
    for entry in report["joints"]:
        by_angle = {}
        for pose in entry["poses"]:
            by_angle[round(abs(pose["flex_deg"]), 0)] = pose["volume_loss_pct"]
        out[entry["joint"]] = {
            "worst": entry["worst_volume_loss_pct"],
            "verdict": entry["verdicts"]["volume"],
            "by_angle": by_angle,
        }
    return out, report


@command("rigforge_correctives")
def cmd_rigforge_correctives(params):
    """Author bend-angle-driven corrective shape keys and measure what they fixed.

    ``rigforge_correctives {"rig"?, "mesh"?, "action"?: "author"|"report"|"clear",
    "joints"?: ["knee.L", ...], "angle_samples"?: [0.5, 1.0], "strength"?: 0-1,
    "smooth"?: 0-1, "weight_floor"?, "verify"?}``

    ``joints`` defaults to the set ``rig_check`` measures as failing on volume at
    full flex — the harness picks its own worst, so the tool corrects what is
    actually broken rather than what someone guessed.  The result carries the
    before/after volume-loss table for every joint and angle it touched, taken
    with the harness itself, drivers live.
    """
    started = time.monotonic()
    _require_numpy()
    warnings = []
    notes = []
    rig = rigcheck._resolve_rig(params)
    mesh = rigcheck._mesh_for(rig, params)

    action = str(params.get("action") or "author").strip().lower()
    if action not in ("author", "report", "clear"):
        raise ForgeError("Unknown action %r for rigforge_correctives. Known: author "
                         "(the default), report, clear." % action)

    if action == "report":
        return _report(rig, mesh, started)
    if action == "clear":
        return _clear(rig, mesh, params, started)

    strength = get_float(params, "strength", 1.0, minimum=0.0, maximum=1.0)
    smooth = get_float(params, "smooth", DEFAULT_SMOOTH, minimum=0.0, maximum=1.0)
    weight_floor = get_float(params, "weight_floor", DEFAULT_WEIGHT_FLOOR,
                             minimum=0.0, maximum=1.0)
    if params.get("direction") is not None:
        raise ForgeError(
            "There is no 'direction' parameter: the restoring push is always radial, "
            "outward from the limb's own posed skeleton. Pushing along the posed "
            "surface normal was implemented and measured instead, and it made the "
            "knee WORSE - 29.7%% volume loss at 90 degrees became 39.4%%, against "
            "13.6%% for radial - because the magnitude is measured as a loss of "
            "distance to the skeleton, and in the crease of a fold the two facing "
            "walls' normals point at each other. Measurement and restoration have to "
            "share an axis. (Got direction=%r.)" % (params["direction"],))
    do_verify = get_bool(params, "verify", True)

    wanted = params.get("joints")
    if wanted is None:
        wanted = _worst_joints(rig, mesh, params, warnings)
    joints = rigcheck.enumerate_joints(rig, wanted)
    if not joints:
        available = [entry["joint"] for entry in rigcheck.enumerate_joints(rig)]
        raise ForgeError(
            "None of the joint(s) %s exist on %r. This rig's measurable joints are: "
            "%s." % (", ".join(repr(str(name)) for name in wanted), rig.name,
                     ", ".join(available) or "none"))

    edges = _edge_pairs(mesh)
    base_coords = _basis_coords(mesh)
    snapshot = rigcheck._capture_pose(rig)
    switches = rigcheck._ik_switches(rig)

    plan = {}
    for joint in joints:
        plan[joint["joint"]] = _sample_angles(params, joint)
    all_angles = sorted({angle for angles in plan.values() for angle in angles})

    muted = []
    before = {}
    joint_reports = []
    written = []

    try:
        with object_mode():
            # Every corrective on this mesh — the ones already here and the ones
            # written during this run — stays MUTED until the last joint is
            # authored. Both halves matter: the baseline has to be plain
            # linear-blend skinning rather than "whatever the last run left", and
            # a key measured through an earlier key's correction would be a
            # correction of a correction.
            for block in corrective_keys(mesh):
                if not block.mute:
                    block.mute = True
                    muted.append(block)
            refresh_view_layer()
            if do_verify:
                before, _ = _harness(rig, mesh, [j["joint"] for j in joints],
                                     all_angles, weight_floor)

            # --- force full FK for the duration, exactly as the harness does:
            #     an FK rotation under a live IK solver moves nothing.
            for (bone_name, key) in switches:
                bone = rig.pose.bones.get(bone_name)
                try:
                    bone[key] = 0.0 if key.lower() == "fk_ik" else 1.0
                except (KeyError, TypeError, ValueError):  # pragma: no cover
                    warnings.append("Could not switch %s[%s] to FK; that joint's "
                                    "corrective may have been authored against an IK "
                                    "solve." % (bone_name, key))
            refresh_view_layer()

            rest_local = _evaluated_local_coords(mesh)
            rest_world = rigcheck.evaluated_vertex_coords(mesh)
            for joint in joints:
                report = _author_joint(
                    rig, mesh, joint, plan[joint["joint"]], base_coords, rest_local,
                    rest_world, edges, strength, smooth, weight_floor,
                    muted, warnings, notes)
                joint_reports.append(report)
                written.extend(entry["name"] for entry in report["keys"]
                               if entry.get("written"))
    finally:
        for block in muted:
            try:
                block.mute = False
            except ReferenceError:  # pragma: no cover - the key was removed
                continue
        rigcheck._restore_pose(rig, snapshot, switches)

    after = {}
    if do_verify and written:
        after, _ = _harness(rig, mesh, [j["joint"] for j in joints],
                            all_angles, weight_floor)

    table = _table(joint_reports, before, after)
    for report in joint_reports:
        for entry in report["keys"]:
            row = next((r for r in table
                        if r["joint"] == report["joint"]
                        and abs(r["flex_deg"] - entry["flex_deg"]) < 0.5), None)
            if row is not None:
                entry["volume_loss_before_pct"] = row["volume_loss_before_pct"]
                entry["volume_loss_after_pct"] = row["volume_loss_after_pct"]
                entry["improvement_pct"] = row["improvement_pct"]

    return {
        "rig": rig.name,
        "mesh": mesh.name,
        "action": "author",
        "strength": strength,
        "smooth": smooth,
        "direction": PUSH_DIRECTION,
        "rest_space": (
            "Each key stores rest-pose coordinates. The wanted posed-space push d was "
            "pulled back through this pose's own blended skinning matrix as "
            "delta_rest = L^-1 d (L = the 3x3 linear part of the blend), and that "
            "skinning model was checked against the depsgraph at the same pose — see "
            "skin_residual_mm per joint."),
        "shape_keys": written,
        "joints": joint_reports,
        "table": table,
        "verified": bool(after),
        "gate_before": _gate(before),
        "gate_after": _gate(after) if after else None,
        "thresholds": {"volume_loss_pct": rigcheck.THRESHOLDS["volume_loss_pct"]},
        "threshold_tier": rigcheck.cmd_rig_check.__doc__ and (
            "heuristic (proxy tier): the same visible-artefact bands rig_check uses, "
            "uncalibrated. The before/after millimetres and percentages are the "
            "measurement; the band is only how it is coloured in."),
        "says": _says(joint_reports, table, written),
        "notes": notes,
        "warnings": warnings,
        "pose_restored": True,
        "seconds": round(time.monotonic() - started, 3),
    }


def _author_joint(rig, mesh, joint, angles, base_coords, rest_local, rest_world,
                  edges, strength, smooth, weight_floor, muted,
                  warnings, notes):
    """Measure the collapse at every sample, then write the keys and the drivers."""
    name = joint["joint"]
    control = rig.pose.bones.get(joint["control"])
    if control is None:  # pragma: no cover - enumerate_joints proved it exists
        raise ForgeError("%s's control bone %r vanished between enumeration and "
                         "authoring." % (name, joint["control"]))
    sign = rigcheck._bend_sign(rig, joint)
    probe = rigcheck.JointProbe(rig, mesh, joint, weight_floor, rest_world=rest_world)
    if not probe.usable:
        warnings.append("%s was skipped: %s." % (joint["label"], probe.reason))
        return {"joint": name, "label": joint["label"], "skipped": probe.reason,
                "keys": []}

    parent_names, child_names = _joint_bones(rig, joint)
    if not child_names:  # pragma: no cover - JointProbe would have refused first
        warnings.append("%s was skipped: it has no deform bone below the joint."
                        % joint["label"])
        return {"joint": name, "label": joint["label"],
                "skipped": "no deform bone below the joint", "keys": []}

    parent_weight = _group_weights(mesh, set(parent_names))
    child_weight = _group_weights(mesh, set(child_names))
    total = parent_weight + child_weight
    safe = _np.where(total > 1e-12, total, 1.0)
    band = _np.minimum(1.0, 2.0 * _np.sqrt(
        (parent_weight / safe) * (child_weight / safe)))
    band = _np.where(total > 1e-12, band, 0.0)

    rest_skeleton = _skeleton(rig, parent_names, child_names, posed=False)

    # The vertices to correct: the harness's own neighbourhood, WIDENED to the
    # whole blend band.  The harness's region is a sphere around the joint and
    # therefore has a hard edge, and the pinch at that edge is not zero — a
    # correction cut off there would leave a visible step in the surface.  The
    # blend band is the physically right boundary because the artefact itself
    # goes to zero exactly where one bone stops sharing the vertex with the
    # other.  The measured region is still reported, so the two numbers stay
    # comparable.
    indices = sorted(set(probe.hull_verts) | set(_np.nonzero(band > 0.02)[0].tolist()))
    gate = _np.zeros(len(band), dtype="f8")
    gate[indices] = 1.0

    # --- phase A: measure every sample before writing anything, so no key's own
    #     driver can contaminate the pose the next key is measured at.
    samples = []
    rest_angle = None
    driver_pair = None
    candidates = _driver_candidates(rig, joint, parent_names, child_names)

    rigcheck._apply_rotation(control, 0.0, 0.0, sign)
    refresh_view_layer()
    rest_angles = {pair: _rotation_difference(rig, *pair) for pair in candidates}

    for angle in angles:
        rigcheck._apply_rotation(control, angle, 0.0, sign)
        refresh_view_layer()
        displacement, matrices, stats = _measure_collapse(
            rig, mesh, indices, parent_names, child_names, rest_skeleton,
            rest_local)
        displacement = displacement * band[:, None] * strength
        displacement = silhouette._smooth_field(displacement, edges, smooth)
        # Smoothing bleeds the field past the band; the correction is confined
        # back to the vertices this joint owns, but with a BINARY gate rather
        # than the falloff mask a second time — applying the mask twice would
        # square it and quietly give away a quarter of the correction where the
        # weights are lopsided, which is most of the band.
        displacement = displacement * gate[:, None]
        delta, singular = _pull_back(displacement, matrices, indices)
        if singular:
            warnings.append(
                "%d vertex/vertices around %s had a singular blend matrix at %.0f "
                "degrees and were left alone (their weights average to a collapsed "
                "transform)." % (singular, joint["label"], angle))
        pair_angles = {pair: _rotation_difference(rig, *pair) for pair in candidates}
        samples.append({
            "angle": angle, "delta": delta, "stats": stats,
            "pair_angles": pair_angles,
        })

    rigcheck._apply_rotation(control, 0.0, 0.0, sign)
    refresh_view_layer()

    # --- the driver pair: the first candidate that actually sweeps.
    widest = samples[-1]["pair_angles"]
    for pair in candidates:
        sweep = abs(math.degrees(widest[pair] - rest_angles[pair]))
        if sweep >= MIN_DRIVER_SWEEP_DEG:
            driver_pair = pair
            break
    if driver_pair is None:
        tried = ", ".join("%s/%s (%.1f deg)"
                          % (a, b, abs(math.degrees(widest[(a, b)] - rest_angles[(a, b)])))
                          for a, b in candidates) or "none"
        raise ForgeError(
            "No bone pair on %r measures %s bending: every candidate's rotation "
            "difference moved less than %.0f degrees between rest and %.0f degrees of "
            "flex, so a driver built on it would never fire. Tried %s."
            % (rig.name, joint["label"], MIN_DRIVER_SWEEP_DEG, angles[-1], tried))
    rest_angle = rest_angles[driver_pair]
    sample_angles = [sample["pair_angles"][driver_pair] for sample in samples]
    if any(b <= a for a, b in zip([rest_angle] + sample_angles, sample_angles)):
        warnings.append(
            "%s's driver angle (%s/%s) is not strictly increasing across the samples "
            "%s — ROTATION_DIFF is unsigned, so a joint that bends through straight "
            "will read the same angle twice. The ramps were built anyway; check the "
            "key values at the extremes."
            % (joint["label"], driver_pair[0], driver_pair[1],
               ", ".join("%.1f deg" % math.degrees(a) for a in sample_angles)))

    # --- phase B: write the keys and their ramps.
    shape_keys = _shape_keys_for(mesh, notes)
    ramps = _build_ramps([rest_angle] + sample_angles)
    keys = []
    worst_residual = max(sample["stats"]["skin_residual_mm"] for sample in samples)
    if worst_residual > SKIN_RESIDUAL_WARN_MM:
        warnings.append(
            "The skinning model and Blender's own evaluation disagree by %.2f mm "
            "around %s, so the rest-space pull-back is approximate here. Something "
            "other than plain vertex-group skinning is deforming %r — a "
            "'Preserve Volume' armature modifier, a corrective smooth, a shrinkwrap."
            % (worst_residual, joint["label"], mesh.name))

    for sample, ramp in zip(samples, ramps):
        angle = sample["angle"]
        delta = sample["delta"]
        moved = int(_np.count_nonzero(_np.linalg.norm(delta, axis=1) > 1e-9))
        push_mm = float(_np.max(_np.linalg.norm(delta, axis=1))) * M_TO_MM if moved else 0.0
        entry = {
            "name": key_name_for(name, angle),
            "flex_deg": round(angle, 1),
            "driver_angle_deg": round(math.degrees(sample["pair_angles"][driver_pair]), 2),
            "vertices_moved": moved,
            "max_push_mm": round(push_mm, 3),
            "written": False,
        }
        entry.update(sample["stats"])
        if strength <= 0.0:
            entry["skipped"] = ("strength is 0, so this run measured the collapse and "
                                "wrote nothing")
            keys.append(entry)
            continue
        if push_mm < MIN_PUSH_MM:
            entry["skipped"] = (
                "the largest correction was %.4f mm, below the %.2f mm worth storing — "
                "this joint does not collapse at %.0f degrees"
                % (push_mm, MIN_PUSH_MM, angle))
            keys.append(entry)
            continue
        key, overwritten = _write_key(mesh, entry["name"], base_coords + delta)
        if key not in muted:
            muted.append(key)
        entry["name"] = key.name
        entry["written"] = True
        entry["overwritten"] = overwritten
        _make_driver(shape_keys, key.name, rig, driver_pair[0], driver_pair[1], ramp)
        entry["driver_ramp"] = [[round(math.degrees(a), 2), round(v, 3)]
                                for a, v in ramp]
        keys.append(entry)

    refresh_view_layer()
    return {
        "joint": name,
        "label": joint["label"],
        "control": joint["control"],
        "bend_sign": "positive" if sign > 0 else "negative",
        "deform_bones": joint["deform_bones"],
        "parent_deform_bones": parent_names,
        "driver_bones": list(driver_pair),
        "driver_variable": "rotation_difference",
        "driver_rest_angle_deg": round(math.degrees(rest_angle), 2),
        "region_vertices": len(indices),
        "band_vertices": int(_np.count_nonzero(band > 1e-6)),
        "skin_residual_mm": worst_residual,
        "keys": keys,
    }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _table(joint_reports, before, after):
    """The before/after volume-loss table this command's own report carries."""
    rows = []
    for report in joint_reports:
        for entry in report.get("keys", ()):
            angle = round(entry["flex_deg"], 0)
            was = (before.get(report["joint"]) or {}).get("by_angle", {}).get(angle)
            now = (after.get(report["joint"]) or {}).get("by_angle", {}).get(angle)
            improvement = None
            if was is not None and now is not None and abs(was) > 1e-9:
                improvement = round(100.0 * (was - now) / abs(was), 1)
            rows.append({
                "joint": report["joint"],
                "label": report["label"],
                "flex_deg": entry["flex_deg"],
                "shape_key": entry["name"] if entry.get("written") else None,
                "volume_loss_before_pct": was,
                "volume_loss_after_pct": now,
                "improvement_pct": improvement,
                "max_push_mm": entry.get("max_push_mm"),
            })
    return rows


def _gate(measured):
    if not measured:
        return None
    return rigcheck._worst([entry["verdict"] for entry in measured.values()])


def _says(joint_reports, table, written):
    if not written:
        skipped = [report.get("skipped") for report in joint_reports
                   if report.get("skipped")]
        if skipped:
            return "Nothing was authored: %s." % "; ".join(sorted(set(skipped)))
        return ("Nothing was authored: no joint's collapse was large enough to be "
                "worth a corrective at the angles sampled.")
    parts = []
    for row in table:
        if row["volume_loss_before_pct"] is None or row["volume_loss_after_pct"] is None:
            continue
        parts.append("%s at %.0f deg: %.1f%% -> %.1f%% volume loss"
                     % (row["label"], row["flex_deg"],
                        row["volume_loss_before_pct"], row["volume_loss_after_pct"]))
    if not parts:
        return ("Wrote %d corrective shape key(s) with bend-angle drivers; the "
                "verification pass was not run, so there is no before/after number "
                "to quote." % len(written))
    return ("Wrote %d corrective shape key(s), each driven by its joint's own bend "
            "angle. Measured with the harness, drivers live: %s."
            % (len(written), "; ".join(parts)))


def _report(rig, mesh, started):
    """What correctives this mesh already carries, and what drives them."""
    shape_keys = getattr(mesh.data, "shape_keys", None)
    drivers = {}
    if shape_keys is not None and shape_keys.animation_data is not None:
        for fcurve in shape_keys.animation_data.drivers:
            path = fcurve.data_path or ""
            if '"' not in path:
                continue
            drivers[path.split('"')[1]] = fcurve
    entries = []
    for block in corrective_keys(mesh):
        fcurve = drivers.get(block.name)
        bones = []
        ramp = []
        if fcurve is not None:
            for variable in fcurve.driver.variables:
                bones.extend(target.bone_target for target in variable.targets
                             if target.bone_target)
            ramp = [[round(math.degrees(point.co.x), 2), round(point.co.y, 3)]
                    for point in fcurve.keyframe_points]
        entries.append({
            "name": block.name,
            "value": round(float(block.value), 4),
            "muted": bool(block.mute),
            "driven": fcurve is not None,
            "driver_bones": bones,
            "driver_ramp": ramp,
        })
    return {
        "rig": rig.name,
        "mesh": mesh.name,
        "action": "report",
        "correctives": entries,
        "count": len(entries),
        "undriven": [entry["name"] for entry in entries if not entry["driven"]],
        "says": ("%r carries no corrective shape keys." % mesh.name if not entries else
                 "%r carries %d corrective shape key(s); %d of them are driven by a "
                 "bend angle." % (mesh.name, len(entries),
                                  sum(1 for e in entries if e["driven"]))),
        "pose_restored": True,
        "seconds": round(time.monotonic() - started, 3),
    }


def _clear(rig, mesh, params, started):
    """Remove the correctives this module wrote, and their drivers."""
    wanted = params.get("joints")
    stems = None
    if wanted:
        stems = tuple("%s%s_" % (CORRECTIVE_PREFIX, str(name).replace(".", "_"))
                      for name in wanted)
    shape_keys = getattr(mesh.data, "shape_keys", None)
    removed = []
    for block in list(corrective_keys(mesh)):
        if stems is not None and not block.name.startswith(stems):
            continue
        name = block.name
        if shape_keys is not None:
            try:
                shape_keys.driver_remove('key_blocks["%s"].value' % name)
            except (TypeError, RuntimeError):  # pragma: no cover - no driver
                pass
        try:
            mesh.shape_key_remove(block)
        except (RuntimeError, ReferenceError, TypeError) as exc:  # pragma: no cover
            raise ForgeError("Could not remove shape key %r from %r: %s"
                             % (name, mesh.name, exc))
        removed.append(name)
    refresh_view_layer()
    return {
        "rig": rig.name,
        "mesh": mesh.name,
        "action": "clear",
        "removed": removed,
        "remaining": [block.name for block in corrective_keys(mesh)],
        "says": ("Nothing to clear: %r carries no corrective shape keys%s."
                 % (mesh.name, " for those joints" if stems else "") if not removed
                 else "Removed %d corrective shape key(s) and their drivers: %s."
                 % (len(removed), ", ".join(removed))),
        "pose_restored": True,
        "seconds": round(time.monotonic() - started, 3),
    }
