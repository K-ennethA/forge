"""RigForge stage 4 and stage 7: metarig, Rigify generation, weights, Godot export.

Phase 4 of the pipeline (``docs/plan.md`` section 4, stages 4 and 7).  Phase 3
put *tags* on the sculpt and a retopologised game mesh next to it; this module
turns that into an animatable rig and a glTF a Godot project can import.

The four commands, in the order you run them:

* :func:`cmd_rigforge_metarig` — build a Rigify metarig and **fit it to the
  tags**.  Not "scale it to the bounding box": each tagged region is measured
  (head top, chin, shoulder, elbow, wrist, hip, knee, ankle) and the matching
  metarig bones are snapped onto those landmarks.  Ear/tail tags become extra
  bone chains, flagged for secondary motion.
* :func:`cmd_rigforge_generate_rig` — Rigify's own generate, then parent the
  game mesh with automatic weights and run the per-tag cleanup rules ("no head
  weights below the neck").
* :func:`cmd_rigforge_weights` — report / cleanup / normalize on their own.
* :func:`cmd_rigforge_export_godot` — bake every action onto a **deform-only**
  copy of the rig, strip the control bones, export glTF with Godot's
  conventions, and write a Godot ``EditorScenePostImport`` helper next to it.

Two things this module deliberately does *not* do:

* **no external add-ons.**  The plan named Game Rig Tools for the deform-rig
  conversion; that is a download, so the conversion is implemented here instead
  (duplicate the rig, keep the ``DEF-`` bones, re-parent them into the clean
  anatomical hierarchy they imply, bake the control rig onto them with visual
  keying, strip everything else).  Rigify itself is not a download — it ships
  with Blender — so it is enabled in-session the same way Phase 3 enables
  Cycles for the normal bake.
* **no simulation.**  Blender has no spring-bone primitive.  "Secondary motion
  v1" is a deterministic constraint rig (see :func:`add_spring_chains`) that
  bakes cleanly into an exported action; it approximates lag, it does not
  simulate it.

Everything here runs on Blender's main thread and works under
``blender --background``.
"""

import json
import math
import os
import re
import time

import bpy
from mathutils import Matrix, Vector

from . import rigforge
from . import rigforge_joints
from . import rigforge_landmarks
from . import rigforge_autotag
from .common import (
    M_TO_MM,
    active_only,
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_scene,
    get_str,
    get_view_layer,
    find_object,
    object_mode,
    op_kwargs,
    refresh_view_layer,
    resolve_object,
    resolve_path,
    selection,
)
from .registry import ForgeError, command
from .rigforge import (
    PROP_ARCHETYPE,
    PROP_LOD,
    PROP_MOTION_NOTES,
    _prop,
    _set_prop,
    tag_display_name,
    tag_group_name,
    tag_groups,
)

#: Rigify names every deforming bone it generates with this prefix; everything
#: else in a generated rig is a control (bare name), an original (``ORG-``), a
#: mechanism (``MCH-``) or a widget object (``WGT-``).
DEF_PREFIX = "DEF-"
CONTROL_PREFIXES = ("ORG-", "MCH-", "WGT-", "VIS-")

#: Bones this module adds after generation for secondary motion. ``MCH-`` so
#: Rigify's own conventions (and the export stripper) treat them as mechanism.
LAG_PREFIX = "MCH-lag-"
LAGMIX_PREFIX = "MCH-lagmix-"

ROOT_BONE = "root"

#: Object custom properties written by :func:`cmd_rigforge_metarig` so that
#: generate/export can work without being told everything again.
PROP_TAG_BONES = "forge_tag_bones"        # metarig: {tag: [bone names]}
PROP_CHAINS = "forge_spring_chains"       # metarig: [chain dicts]
PROP_RIG_MESH = "forge_rig_mesh"          # metarig/rig: the mesh it was fitted to
PROP_METARIG = "forge_metarig"            # mesh: name of its metarig
PROP_RIG = "forge_rig"                    # mesh/metarig: name of the generated rig
PROP_PRESET = "forge_metarig_preset"

#: Tag names that mean "a floppy chain", not "a body part with a limb rig".
CHAIN_TAG_RE = re.compile(
    r"^(ear|tail|antenna|whisker|fin|tentacle|horn|braid|frond|wing_tip)(\b|[._])",
    re.IGNORECASE,
)

DEFAULT_CHAIN_BONES = 3

#: Motion-note words -> how much of the parent's motion the chain follows.
#: Lower = more lag. Read out of the manifest's plain-language notes.
FOLLOW_WORDS = (
    (("floppy", "flop", "lags", "lag ", "trails", "trail", "drags", "drag", "loose",
      "heavy"), 0.45),
    (("bouncy", "bounce", "jiggle", "jiggly", "springy", "wobble"), 0.60),
    (("stiff", "rigid", "firm", "held"), 0.88),
)
DEFAULT_FOLLOW = 0.70

#: Archetype -> the operator that builds its metarig, in preference order.
METARIG_OPS = {
    "human": ("armature_human_metarig_add",),
    "basic_human": ("armature_basic_human_metarig_add", "armature_human_metarig_add"),
    "quadruped": ("armature_basic_quadruped_metarig_add",),
    "wolf": ("armature_wolf_metarig_add", "armature_basic_quadruped_metarig_add"),
    "cat": ("armature_cat_metarig_add", "armature_basic_quadruped_metarig_add"),
    "horse": ("armature_horse_metarig_add", "armature_basic_quadruped_metarig_add"),
    "bird": ("armature_bird_metarig_add", "armature_basic_quadruped_metarig_add"),
    "shark": ("armature_shark_metarig_add",),
}

#: Tags whose presence means the *full* human metarig (face + fingers) is worth
#: its 160 deform bones. Without them the 29-bone basic human is the game rig.
FACE_TAGS = ("face", "jaw", "eye", "eyelid", "brow", "lip", "mouth", "nose", "tongue",
             "teeth", "cheek", "hand", "finger", "thumb", "palm")

PRESETS = ("auto", "human", "basic_human")

#: Suffix conventions Godot's glTF importer understands.
LOOP_SUFFIX = "-loop"
COLLISION_SUFFIXES = ("-col", "-colonly", "-convcol", "-convcolonly")


# ---------------------------------------------------------------------------
# Rigify
# ---------------------------------------------------------------------------

def ensure_rigify():
    """Enable the bundled Rigify add-on, and say so. Raises on real failure.

    Rigify ships *with* Blender (``scripts/addons_core/rigify``); a
    ``--factory-startup`` session simply has it switched off, which is not the
    same thing as not having it.  Unlike Cycles it must be enabled with
    ``default_set=True``: Rigify's own ``register()`` reads
    ``preferences.addons["rigify"].preferences`` and raises a ``KeyError`` if
    the add-on was not written into the preferences first.
    """
    info = {"module": None, "enabled": False, "already": False}
    addons = bpy.context.preferences.addons
    for module in ("rigify", "bl_ext.blender_org.rigify", "bl_ext.user_default.rigify"):
        if module in addons:
            info.update(module=module, enabled=True, already=True)
            break
    if not info["enabled"]:
        import addon_utils

        problems = []
        for module in ("rigify", "bl_ext.blender_org.rigify"):
            try:
                loaded = addon_utils.enable(module, default_set=True, persistent=False)
            except Exception as exc:  # noqa: BLE001 - report, try the next spelling
                problems.append("%s: %s" % (module, exc))
                continue
            if loaded is not None:
                info.update(module=module, enabled=True)
                break
            problems.append("%s: enable() returned None" % module)
        if not info["enabled"]:
            raise ForgeError(
                "Rigify is not available in this Blender session (%s). Rigify ships with "
                "Blender - switch it on in Preferences > Add-ons > Rigging: Rigify, then "
                "run this again." % ("; ".join(problems) or "no reason given")
            )
    if not hasattr(bpy.ops.pose, "rigify_generate"):
        raise ForgeError(
            "Rigify reports itself enabled (%s) but bpy.ops.pose.rigify_generate is "
            "missing, so this Blender build's Rigify is not usable." % info["module"]
        )
    return info


def _metarig_operator(preset):
    """The ``bpy.ops.object.*`` callable that builds ``preset``'s metarig."""
    for name in METARIG_OPS.get(preset, ()):
        operator = getattr(bpy.ops.object, name, None)
        if operator is not None and name in dir(bpy.ops.object):
            return operator, name
    raise ForgeError(
        "This Blender's Rigify offers no metarig operator for preset %r (tried %s)."
        % (preset, ", ".join(METARIG_OPS.get(preset, ())) or "nothing")
    )


# ---------------------------------------------------------------------------
# measuring the tags
# ---------------------------------------------------------------------------

def tag_points(obj, group):
    """World-space positions of every vertex assigned to a tag group."""
    matrix = obj.matrix_world
    index = group.index
    points = []
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.group == index and entry.weight > 0.0:
                points.append(matrix @ vertex.co)
                break
    return points


def _bounds(points):
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _centroid(points):
    total = Vector((0.0, 0.0, 0.0))
    for point in points:
        total += point
    return total / float(len(points))


def _principal_axis(points, centre, seed=None):
    """Dominant direction of a point cloud (power iteration on its covariance).

    No numpy: the add-on is stdlib + bpy only, and a 3x3 covariance with two
    dozen iterations is exact enough to tell an arm's length from its girth.
    """
    cov = [[0.0] * 3 for _ in range(3)]
    for point in points:
        d = point - centre
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    vector = Vector(seed) if seed is not None else Vector((0.577, 0.577, 0.577))
    if vector.length < 1e-9:
        vector = Vector((0.0, 0.0, 1.0))
    vector.normalize()
    for _ in range(32):
        nxt = Vector((
            cov[0][0] * vector.x + cov[0][1] * vector.y + cov[0][2] * vector.z,
            cov[1][0] * vector.x + cov[1][1] * vector.y + cov[1][2] * vector.z,
            cov[2][0] * vector.x + cov[2][1] * vector.y + cov[2][2] * vector.z,
        ))
        if nxt.length < 1e-12:
            break
        nxt.normalize()
        if (nxt - vector).length < 1e-9:
            vector = nxt
            break
        vector = nxt
    return vector


def _slab_centroid(points, projections, target, half_width):
    """Centroid of the points whose projection sits within ``half_width`` of ``target``.

    Averaging a slab rather than picking the single extreme vertex is what makes
    a landmark survive one stray vertex on a noisy sculpt.
    """
    chosen = [points[i] for i, t in enumerate(projections) if abs(t - target) <= half_width]
    if not chosen:
        # widen until something lands in the slab
        order = sorted(range(len(points)), key=lambda i: abs(projections[i] - target))
        chosen = [points[i] for i in order[: max(1, len(points) // 20)]]
    return _centroid(chosen)


class Region(object):
    """One tagged region, measured: bounds, axis and the joints along it."""

    def __init__(self, tag, points, axis_hint=None, body_centre=None):
        self.tag = tag
        self.count = len(points)
        self.low, self.high = _bounds(points)
        self.centre = _centroid(points)
        self.size = self.high - self.low
        span = sorted(self.size, reverse=True)
        # An almost-round region (a blob arm, a ball head) has no meaningful
        # principal axis, so trust the caller's anatomical hint instead.
        isotropic = span[0] <= 1.5 * max(span[2], 1e-9)
        if axis_hint is not None and isotropic:
            axis = Vector(axis_hint).normalized()
        else:
            axis = _principal_axis(points, self.centre, seed=axis_hint)
        if axis_hint is not None and axis.dot(Vector(axis_hint)) < 0.0:
            axis = -axis
        elif axis_hint is None and body_centre is not None:
            if axis.dot(self.centre - body_centre) < 0.0:
                axis = -axis
        self.axis = axis
        self.isotropic = isotropic

        projections = [(point - self.centre).dot(axis) for point in points]
        lo = min(projections)
        hi = max(projections)
        self.length = hi - lo
        half = max(self.length * 0.08, 1e-6)
        self._points = points
        self._projections = projections
        self.start = _slab_centroid(points, projections, lo, half)
        self.mid = _slab_centroid(points, projections, (lo + hi) * 0.5, half)
        self.end = _slab_centroid(points, projections, hi, half)

    def at(self, fraction):
        """A landmark ``fraction`` of the way along the region's own axis."""
        lo = min(self._projections)
        hi = max(self._projections)
        target = lo + (hi - lo) * float(fraction)
        return _slab_centroid(self._points, self._projections, target,
                              max((hi - lo) * 0.08, 1e-6))

    def slice_centre(self, z_low, z_high):
        """Centroid of the slab between two heights (a spine that follows a belly)."""
        chosen = [p for p in self._points if z_low - 1e-9 <= p.z <= z_high + 1e-9]
        if not chosen:
            return Vector((self.centre.x, self.centre.y, (z_low + z_high) * 0.5))
        centre = _centroid(chosen)
        centre.z = (z_low + z_high) * 0.5
        return centre

    def contains(self, point, band=0.0):
        for i in range(3):
            if point[i] < self.low[i] - band or point[i] > self.high[i] + band:
                return False
        return True

    def distance_to(self, point):
        """Distance from a point to this region's bounding box (0 inside)."""
        total = 0.0
        for i in range(3):
            if point[i] < self.low[i]:
                total += (self.low[i] - point[i]) ** 2
            elif point[i] > self.high[i]:
                total += (point[i] - self.high[i]) ** 2
        return math.sqrt(total)

    def as_dict(self):
        return {
            "tag": self.tag,
            "vertices": self.count,
            "min": [round(v, 6) for v in self.low],
            "max": [round(v, 6) for v in self.high],
            "centre": [round(v, 6) for v in self.centre],
            "axis": [round(v, 6) for v in self.axis],
            "length": round(self.length, 6),
            "isotropic": self.isotropic,
        }


def measure_tags(obj, axis_hints=None):
    """``{tag: Region}`` for every tag on ``obj`` that actually has vertices."""
    hints = axis_hints or {}
    regions = {}
    empty = []
    matrix = obj.matrix_world
    body_centre = matrix @ (sum((Vector(c) for c in obj.bound_box), Vector()) / 8.0)
    for group in tag_groups(obj):
        name = tag_display_name(group.name)
        points = tag_points(obj, group)
        if not points:
            empty.append(name)
            continue
        regions[name] = Region(name, points, axis_hint=hints.get(name),
                               body_centre=body_centre)
    return regions, empty


# ---------------------------------------------------------------------------
# edit-bone surgery
# ---------------------------------------------------------------------------

def _drag_subtree(bone, delta, frozen):
    """Move every descendant of ``bone`` by ``delta`` (connected heads follow)."""
    moved = 0
    for child in bone.children:
        if child.name in frozen:
            continue
        if not child.use_connect:
            child.head = child.head + delta
        child.tail = child.tail + delta
        moved += 1 + _drag_subtree(child, delta, frozen)
    return moved


def _safe_roll(bone, reference):
    """Keep a bone's twist sane after its direction changed."""
    direction = (bone.tail - bone.head)
    if direction.length < 1e-9:
        return
    direction.normalize()
    candidates = [reference, Vector((0.0, 0.0, 1.0)), Vector((0.0, -1.0, 0.0)),
                  Vector((1.0, 0.0, 0.0))]
    for candidate in candidates:
        if candidate is None or candidate.length < 1e-9:
            continue
        vector = candidate.normalized()
        if abs(vector.dot(direction)) < 0.95:
            try:
                bone.align_roll(vector)
            except (AttributeError, ValueError, RuntimeError):
                pass
            return


def fit_chain(edit_bones, names, points, frozen, report=None, bend=None):
    """Snap a chain of bones onto ``len(names) + 1`` landmark points.

    Bones are placed parent first.  Any child that is *not* itself being fitted
    is dragged along by the same delta its parent's tail moved, which is what
    keeps a hand (and its fingers) on the end of a re-fitted forearm, and the
    whole face on top of a re-fitted head.
    """
    placed = []
    if len(points) != len(names) + 1:
        raise ForgeError("fit_chain: %d bones need %d points, got %d."
                         % (len(names), len(names) + 1, len(points)))
    for index, name in enumerate(names):
        bone = edit_bones.get(name)
        if bone is None:
            continue
        reference = (bone.z_axis.copy() if hasattr(bone, "z_axis") else None)
        old_tail = bone.tail.copy()
        head = Vector(points[index])
        tail = Vector(points[index + 1])
        if (tail - head).length < 1e-6:
            tail = head + Vector((0.0, 0.0, 1e-3))
        if index == 0 or not bone.use_connect:
            bone.head = head
        bone.tail = tail
        _safe_roll(bone, reference)
        delta = bone.tail - old_tail
        if delta.length > 1e-12:
            _drag_subtree(bone, delta, frozen)
        placed.append(name)
    if report is not None and placed:
        report.extend(placed)
    return placed


def _bend(a, b, c, direction, minimum=0.03):
    """Push a three-point limb into its **anatomical** pre-bend.

    Rigify's arm/leg rigs derive the IK pole from the plane of the limb; a
    perfectly straight limb (which is exactly what a blob sculpt measures as)
    leaves that plane undefined and the generated IK snaps unpredictably.

    **The measurement is signed, along ``direction``, and that is the fix.**
    This function used to ask only *how far from straight* the joint was, and
    return it untouched whenever the answer cleared ``minimum``.  Measured on the
    live werewolf: the knee sat **25.9 mm off the hip-to-ankle line — 4.2% of a
    620 mm span, comfortably past the 3% test — pointing backwards**, and the
    elbow's 31.6 mm was almost entirely *sideways* (0.08 mm of it front-to-back).
    Both passed the old test, both left the IK plane wrong, and lifting
    ``foot_ik.L`` by 350 mm drove the knee the wrong way.  A magnitude cannot
    tell a knee from a knee bent backwards; only the component along the
    direction the joint is *supposed* to apex can.
    """
    span = (c - a)
    if span.length < 1e-9:
        return b
    axis = span.normalized()
    push = Vector(direction)
    push -= axis * push.dot(axis)
    if push.length < 1e-9:
        push = Vector((0.0, -1.0, 0.0))
        push -= axis * push.dot(axis)
    if push.length < 1e-9:
        return b
    push.normalize()
    offset = (b - a) - axis * (b - a).dot(axis)
    signed = offset.dot(push)
    need = minimum * span.length
    if signed >= need:
        return b
    return b + push * (need - signed)


# ---------------------------------------------------------------------------
# archetype fitting
# ---------------------------------------------------------------------------

#: Bones Rigify's human template carries that most characters do not want, with
#: the switch that keeps each one.  ``breast.L``/``breast.R`` are the whole list
#: today and they earn their place on it: they are a **female torso** feature,
#: they are not part of any limb chain, and on a figure without one they
#: generate ``DEF-breast.L/R`` that stand out of the chest as prongs in the
#: skeleton echo and then sit inside the torso collecting stray influence
#: (measured on the werewolf: ``DEF-breast.L`` sharing vertices with
#: ``DEF-forearm.R.001``, two bones nowhere near each other).
OPTIONAL_BONE_GROUPS = {
    "breast_bones": ("breast.L", "breast.R"),
}


def drop_optional_bones(edit_target, keep):
    """Remove the optional template bones ``keep`` does not ask for. Names dropped.

    Runs on the **metarig, in edit mode, before generation**, which is the only
    place it can run: Rigify decides what to generate from the metarig's bones,
    so a bone deleted here never becomes a ``DEF-`` bone, never enters the deform
    set, never gets a vertex group and never appears in a weight map. Deleting
    the generated ``DEF-`` bone afterwards would leave its vertex group on the
    mesh and its widget in the file.

    Children come with it — a bone hanging off a bone nobody wants is nobody's
    either — and the removal is reported rather than silent.
    """
    bones = edit_target.data.edit_bones
    dropped = []
    for switch, names in sorted(OPTIONAL_BONE_GROUPS.items()):
        if keep.get(switch):
            continue
        for bone_name in names:
            bone = bones.get(bone_name)
            if bone is None:
                continue
            family = [bone]
            frontier = [bone]
            while frontier:
                nxt = []
                for parent in frontier:
                    for child in parent.children:
                        family.append(child)
                        nxt.append(child)
                frontier = nxt
            for doomed in reversed(family):
                try:
                    dropped.append(doomed.name)
                    bones.remove(doomed)
                except (ReferenceError, RuntimeError):  # pragma: no cover
                    dropped.pop()
    return dropped


def _pick_tag(regions, *candidates):
    lowered = {name.lower(): name for name in regions}
    for candidate in candidates:
        key = candidate.lower()
        if key in lowered:
            return lowered[key]
    return None


def _side_tags(regions, prefix):
    """Every tag that looks like ``<prefix>.<side>`` -> {"L": tag, "R": tag}."""
    out = {}
    for name in regions:
        match = re.match(r"^(%s)[._ ]?(L|R|Left|Right)$" % prefix, name, re.IGNORECASE)
        if match:
            out["L" if match.group(2).upper().startswith("L") else "R"] = name
    return out


def fit_biped(meta, regions, warnings, mapping, hints=None):
    """Snap a human metarig onto the tagged landmarks. Returns fitted bone names.

    ``hints`` is an optional :class:`~.rigforge_joints.JointHints` — a neural
    detector's second opinion.  Every landmark below is computed from the tags
    exactly as before and then passed through ``refine``, which moves it part of
    the way towards a believable prediction, overrules an unbelievable one, and
    records both.  With no hints the ``refine`` below is the identity function
    and this fit is bit-for-bit the tag-only fit.
    """
    edit_bones = meta.data.edit_bones
    fitted = []
    frozen = set()
    refine = hints.refine if hints is not None else (lambda role, point: point)

    torso_tag = _pick_tag(regions, "Torso", "Body", "Chest", "Spine")
    head_tag = _pick_tag(regions, "Head", "Skull")
    arms = _side_tags(regions, "Arm|UpperArm|Foreleg")
    legs = _side_tags(regions, "Leg|Thigh|Hindleg")

    spine_names = ["spine", "spine.001", "spine.002", "spine.003"]
    neck_names = ["spine.004", "spine.005", "spine.006"]
    frozen.update(spine_names + neck_names)
    for side in ("L", "R"):
        frozen.update(["shoulder.%s" % side, "upper_arm.%s" % side, "forearm.%s" % side,
                       "thigh.%s" % side, "shin.%s" % side])

    torso = regions.get(torso_tag) if torso_tag else None
    head = regions.get(head_tag) if head_tag else None

    # Where the spine stops and the neck starts, decided once for both chains:
    # Rigify's spine rigs refuse to generate when the head of a chain does not
    # sit exactly on the tail of the chain above it ("bone position is
    # disjoint"), so the two fits have to share this point, not each compute
    # their own idea of it.
    neck_base = None
    if head is not None:
        chest_z = torso.high.z if torso is not None else head.low.z
        neck_base_z = 0.5 * (chest_z + head.low.z)
        base_xy = torso.centre if torso is not None else head.centre
        if torso is not None:
            half = max(torso.size.z * 0.06, 1e-6)
            base_xy = torso.slice_centre(neck_base_z - half, neck_base_z + half)
        neck_base = refine("neck_base", Vector((base_xy.x, base_xy.y, neck_base_z)))

    if torso is None:
        warnings.append(
            "No Torso tag: the spine keeps its scaled default. Tag the body "
            "'Torso' and place the metarig again for a fitted spine.")
    else:
        hips_z = torso.low.z
        chest_z = neck_base.z if neck_base is not None else torso.high.z
        points = []
        for step in range(5):
            fraction = step / 4.0
            z = hips_z + (chest_z - hips_z) * fraction
            half = max((chest_z - hips_z) * 0.12, 1e-6)
            centre = torso.slice_centre(z - half, z + half)
            centre.z = z
            if step == 4 and neck_base is not None:
                # the shared junction: refined once, above, for both chains
                points.append(neck_base.copy())
            else:
                points.append(refine("hips" if step == 0 else "spine_%02d" % step, centre))
        fit_chain(edit_bones, spine_names, points, frozen, fitted)
        mapping.setdefault(torso.tag, []).extend(spine_names)

    if head is None:
        warnings.append(
            "No Head tag: the neck and head bones keep their scaled default.")
    else:
        head_bottom = head.low.z
        head_top = head.high.z
        neck_top_z = head_bottom + (head_top - head_bottom) * 0.15
        if neck_top_z <= neck_base.z:
            neck_top_z = neck_base.z + max((head_top - neck_base.z) * 0.25, 1e-4)
        neck_top = refine("neck_top", Vector((head.centre.x, head.centre.y, neck_top_z)))
        neck_mid = refine("neck_mid", (neck_base + neck_top) * 0.5)
        top = refine("head_top", Vector((head.centre.x, head.centre.y, head_top)))
        fit_chain(edit_bones, neck_names, [neck_base, neck_mid, neck_top, top],
                  frozen, fitted)
        mapping.setdefault(head.tag, []).extend(neck_names[-1:])
        if torso is not None:
            mapping.setdefault(torso.tag, []).extend(neck_names[:-1])

    for side in ("L", "R"):
        tag = arms.get(side)
        if tag is None:
            warnings.append("No Arm.%s tag: that arm keeps its scaled default." % side)
            continue
        arm = regions[tag]
        body = torso.centre if torso is not None else Vector((0.0, 0.0, arm.centre.z))
        outward = arm.centre - body
        outward.z = 0.0
        if outward.length < 1e-9:
            outward = Vector((1.0 if side == "L" else -1.0, 0.0, 0.0))
        outward.normalize()
        axis = arm.axis.copy()
        if axis.dot(outward) < 0.0:
            axis = -axis
        # Re-measure the arm along the outward direction so "start" really is
        # the shoulder end, whatever the blob's own principal axis said.
        arm = Region(tag, arm._points, axis_hint=axis)
        shoulder = refine("shoulder.%s" % side, arm.at(0.06))
        elbow = refine("elbow.%s" % side, arm.at(0.5))
        wrist = refine("wrist.%s" % side, arm.at(0.96))
        elbow = _bend(shoulder, elbow, wrist, Vector((0.0, 1.0, 0.0)))
        if torso is not None:
            root = Vector((body.x + outward.x * torso.size.x * 0.12,
                           body.y + outward.y * torso.size.y * 0.12,
                           torso.high.z - torso.size.z * 0.06))
        else:
            root = shoulder - outward * (arm.length * 0.4)
        root = refine("clavicle.%s" % side, root)
        names = ["shoulder.%s" % side, "upper_arm.%s" % side, "forearm.%s" % side]
        fit_chain(edit_bones, names, [root, shoulder, elbow, wrist], frozen, fitted)
        mapping.setdefault(tag, []).extend(names[1:] + ["hand.%s" % side])

    for side in ("L", "R"):
        tag = legs.get(side)
        if tag is None:
            warnings.append("No Leg.%s tag: that leg keeps its scaled default." % side)
            continue
        leg = Region(tag, regions[tag]._points, axis_hint=Vector((0.0, 0.0, -1.0)))
        hip = leg.at(0.04)
        if torso is not None:
            hip = Vector((hip.x, torso.centre.y, min(torso.low.z, hip.z)))
        hip = refine("hip.%s" % side, hip)
        knee = refine("knee.%s" % side, leg.at(0.5))
        ankle = refine("ankle.%s" % side, leg.at(0.94))
        knee = _bend(hip, knee, ankle, Vector((0.0, -1.0, 0.0)))
        names = ["thigh.%s" % side, "shin.%s" % side]
        fit_chain(edit_bones, names, [hip, knee, ankle], frozen, fitted)
        mapping.setdefault(tag, []).extend(names + ["foot.%s" % side, "toe.%s" % side])

    return fitted


#: How far a landmark-fitted limb is pushed out of straight when its own
#: geometry gives no bend, as a fraction of the limb's length.  It is the same
#: 3% the tag fit uses, and it was **measured back up to that** after being
#: lowered: at 0.5% ``headless_rigik``'s pole test found the knee moving
#: **0.02 mm** over a 25% pole sweep, because Rigify derives a limb's pole angle
#: from its *rest* plane and a limb that is straight at rest has none worth
#: having.  The price is that a correct rig's knee really does sit a little off
#: the line through its own flesh, which is why ``bone_centering`` gates the
#: shaft rather than the joint and allows a quarter of the section's radius.
LANDMARK_BEND = 0.03


def _fit_heel_pivot(edit_bones, side, landmarks):
    """Put ``heel.02.<side>`` on the back of the measured sole. Names placed.

    Rigify turns this one bone into ``foot_heel_ik``, the pivot the whole foot
    roll rotates about at heel strike.  Left where the template put it — which
    is where the *template's* heel was — a correctly fitted foot rolls about a
    point behind or inside its own shoe.  Head and tail span the sole's own
    measured width at its rearmost cross-section, keeping whichever way round
    the template had them so the roll's sign does not flip.
    """
    foot = (landmarks.get("feet") or {}).get("leg.%s" % side)
    bone = None
    for pattern in ("heel.02.%s", "heel.%s", "heel.01.%s"):
        bone = edit_bones.get(pattern % side)
        if bone is not None:
            break
    if foot is None or bone is None:
        return []
    right = Vector(foot["right"])
    half = max(float(foot["heel_width"]), 1e-4) * 0.5
    centre = Vector(foot["heel"])
    # Keep the template's own left-to-right orientation: Rigify reads the
    # direction of this bone, and reversing it reverses the roll.
    sign = 1.0 if (bone.tail - bone.head).dot(right) >= 0.0 else -1.0
    bone.head = centre - right * (half * sign)
    bone.tail = centre + right * (half * sign)
    return [bone.name]


def fit_biped_landmarks(meta, landmarks, warnings, mapping, mirror=True,
                        midplane_local=0.0):
    """The human workflow's steps 3 and 4: place one side, then X-mirror it.

    Every point here came off the mesh as a **cross-section centroid**
    (:mod:`~forge.tools.rigforge_landmarks`), so the only thing this function
    does is snap bones onto measurements and then reflect them.  Compare
    :func:`fit_biped`, which places both sides independently from fractions
    along a tag's bounding blob — that is the path that produced 6-24 mm of
    left/right asymmetry on a live character, and it is now the fallback rather
    than the default.

    Returns ``(fitted bone names, mirror report)``.
    """
    edit_bones = meta.data.edit_bones
    points = landmarks["points"]
    fitted = []
    frozen = set()
    # The same forward the landmark pass pre-bent against, not a second opinion:
    # ``_bend`` below is a backstop for a chain the landmarks could not nudge
    # (a clamped one, a fallback midpoint), and a backstop that disagreed with
    # the thing it backs up would undo it.
    forward = rigforge_landmarks.facing_vector(landmarks.get("facing") or "-Y")
    backward = -forward

    spine_names = ["spine", "spine.001", "spine.002", "spine.003"]
    neck_names = ["spine.004", "spine.005", "spine.006"]
    frozen.update(spine_names + neck_names)
    for side in ("L", "R"):
        frozen.update(["shoulder.%s" % side, "upper_arm.%s" % side, "forearm.%s" % side,
                       "thigh.%s" % side, "shin.%s" % side])

    torso_tag = landmarks.get("torso_tag")
    head_tag = landmarks.get("head_tag")
    spine_points = [points.get("hips"), points.get("spine_01"), points.get("spine_02"),
                    points.get("spine_03"), points.get("neck_base")]
    if all(point is not None for point in spine_points):
        fit_chain(edit_bones, spine_names, [Vector(p) for p in spine_points],
                  frozen, fitted)
        if torso_tag:
            mapping.setdefault(torso_tag, []).extend(spine_names)
    neck_points = [points.get("neck_base"), points.get("neck_mid"),
                   points.get("neck_top"), points.get("head_top")]
    if all(point is not None for point in neck_points):
        fit_chain(edit_bones, neck_names, [Vector(p) for p in neck_points],
                  frozen, fitted)
        if head_tag:
            mapping.setdefault(head_tag, []).extend(neck_names[-1:])
        if torso_tag:
            mapping.setdefault(torso_tag, []).extend(neck_names[:-1])

    for side in landmarks.get("sides", ("L",)):
        tags = landmarks.get("tags") or {}
        arm_tag = tags.get("arm.%s" % side)
        leg_tag = tags.get("leg.%s" % side)
        root = points.get("clavicle.%s" % side)
        shoulder = points.get("shoulder.%s" % side)
        elbow = points.get("elbow.%s" % side)
        wrist = points.get("wrist.%s" % side)
        if None not in (root, shoulder, elbow, wrist):
            bent = _bend(Vector(shoulder), Vector(elbow), Vector(wrist),
                         backward, minimum=LANDMARK_BEND)
            names = ["shoulder.%s" % side, "upper_arm.%s" % side, "forearm.%s" % side]
            chain = [Vector(root), Vector(shoulder), bent, Vector(wrist)]
            # The hand joins the chain when the mesh had one to measure, for the
            # same reason the foot does: dragged from the template it ended at
            # the fingertips, and a hand bone with no knuckle in it hinges from
            # nowhere.
            knuckle = points.get("knuckle.%s" % side)
            if knuckle is not None:
                names.append("hand.%s" % side)
                chain.append(Vector(knuckle))
            fit_chain(edit_bones, names, chain, frozen, fitted)
            if arm_tag:
                mapping.setdefault(arm_tag, []).extend(names[1:] + ["hand.%s" % side])

        hip = points.get("hip.%s" % side)
        knee = points.get("knee.%s" % side)
        ankle = points.get("ankle.%s" % side)
        if None not in (hip, knee, ankle):
            bent = _bend(Vector(hip), Vector(knee), Vector(ankle),
                         forward, minimum=LANDMARK_BEND)
            names = ["thigh.%s" % side, "shin.%s" % side]
            chain = [Vector(hip), bent, Vector(ankle)]
            # The foot is part of the same chain, when the mesh had one to
            # measure. It used to be *dragged* by whatever the shin's tail did,
            # which is how the werewolf ended up with DEF-toe.L's tail 80.1 mm
            # short of its own boot: a foot roll that pivots about a point
            # inside the shoe rolls the shoe, not the character.
            ball = points.get("ball.%s" % side)
            tip = points.get("toe_tip.%s" % side)
            if ball is not None and tip is not None:
                names.extend(["foot.%s" % side, "toe.%s" % side])
                chain.extend([Vector(ball), Vector(tip)])
            fit_chain(edit_bones, names, chain, frozen, fitted)
            fitted.extend(_fit_heel_pivot(edit_bones, side, landmarks))
            if leg_tag:
                mapping.setdefault(leg_tag, []).extend(
                    names + ["foot.%s" % side, "toe.%s" % side])

    mirror_report = None
    if mirror:
        mirror_report = rigforge_landmarks.mirror_edit_bones(
            meta, source="L", midplane_local=midplane_local)
        fitted.extend(name for name in mirror_report["mirrored_bones"]
                      if name not in fitted)
        # The mirrored bones belong to the *other* side's tag. Without this a
        # perfectly rigged right arm reports as a tag nothing was fitted to, and
        # the weight cleanup — which is driven by this map — has no rules for it.
        all_tags = landmarks.get("all_tags") or {}
        for what, names in (("arm", ["upper_arm.%s", "forearm.%s", "hand.%s"]),
                            ("leg", ["thigh.%s", "shin.%s", "foot.%s", "toe.%s"])):
            for side in ("L", "R"):
                if side in landmarks.get("sides", ("L",)):
                    continue
                tag = all_tags.get("%s.%s" % (what, side))
                if not tag:
                    continue
                mapping.setdefault(tag, []).extend(name % side for name in names)
    return fitted, mirror_report


def fit_best_effort(meta, hints, fitted, warnings):
    """Place bones the tags could never reach, from *named* predictions alone.

    The tag vocabulary stops at ``Arm``/``Leg``: nothing an artist paints says
    where an index finger's first knuckle is.  A detector that names its joints
    can say, so this walks the short table of past-the-wrist / past-the-ankle
    bones (:data:`~.rigforge_joints.BEST_EFFORT_BONES`) and moves each one onto
    its named prediction.

    Two rules keep it from fighting the fit above it:

    * only bones the tag fitter did **not** place are eligible, and
    * a *connected* bone's head belongs to its parent's tail, so it is moved
      only when that parent was not itself fitted from tags. Where it was, the
      tags win and the skip is recorded with its reason.

    Returns the bone names actually moved; everything else lands in the report.
    """
    if hints is None or not hints.enabled or not hints.named:
        return []
    edit_bones = meta.data.edit_bones
    fitted_set = set(fitted)
    frozen = set(fitted_set)
    placed = []
    for template, role in rigforge_joints.BEST_EFFORT_BONES:
        for side in ("L", "R"):
            name = template % side
            bone = edit_bones.get(name)
            if bone is None or name in fitted_set:
                continue
            match = hints.named_only("%s.%s" % (role, side), bone.head.copy())
            if match is None:
                continue
            index, point, distance = match
            delta = point - bone.head
            if delta.length < 1e-6:
                continue
            parent = bone.parent
            if bone.use_connect and parent is not None:
                if parent.name in fitted_set:
                    hints.best_effort.append({
                        "bone": name, "role": role, "joint": index,
                        "joint_name": hints.names[index],
                        "skipped": "the tags own this joint: %s was fitted from them"
                                   % parent.name,
                    })
                    continue
                parent.tail = point
            bone.head = point
            bone.tail = bone.tail + delta
            _safe_roll(bone, None)
            _drag_subtree(bone, delta, frozen)
            hints.record_best_effort(name, role, index, distance, delta.length)
            placed.append(name)
    return placed


def fit_quadruped(meta, regions, warnings, mapping):
    """Snap the basic quadruped metarig onto the tags.

    Same landmark machinery as the biped; only the bone table differs.  The
    quadruped's spine runs *forwards* (hips at ``spine.004``, head at
    ``spine.009``) and its tail is the ``spine.003 .. spine`` run.
    """
    edit_bones = meta.data.edit_bones
    fitted = []
    body_names = ["spine.004", "spine.005", "spine.006", "spine.007", "spine.008"]
    neck_names = ["spine.009", "spine.010", "spine.011"]
    tail_names = ["spine.003", "spine.002", "spine.001", "spine"]
    frozen = set(body_names + neck_names + tail_names)
    for side in ("L", "R"):
        frozen.update(["thigh.%s" % side, "shin.%s" % side,
                       "front_thigh.%s" % side, "front_shin.%s" % side,
                       "shoulder.%s" % side])

    torso_tag = _pick_tag(regions, "Torso", "Body", "Chest", "Spine")
    head_tag = _pick_tag(regions, "Head", "Skull")
    tail_tag = _pick_tag(regions, "Tail")
    rear = _side_tags(regions, "Leg|HindLeg|RearLeg|Leg.B")
    front = _side_tags(regions, "Arm|Foreleg|FrontLeg|Leg.F")

    torso = regions.get(torso_tag) if torso_tag else None
    head = regions.get(head_tag) if head_tag else None
    body = None

    if torso is None:
        warnings.append("No Torso tag: the quadruped spine keeps its scaled default.")
    else:
        # forwards = towards the head if we know where that is, else -Y
        forward = Vector((0.0, -1.0, 0.0))
        if head is not None:
            candidate = head.centre - torso.centre
            candidate.z = 0.0
            if candidate.length > 1e-6:
                forward = candidate.normalized()
        body = Region(torso.tag, torso._points, axis_hint=forward)
        points = [body.at(step / 5.0) for step in range(6)]
        fit_chain(edit_bones, body_names, points, frozen, fitted)
        mapping.setdefault(torso.tag, []).extend(body_names)

    if head is not None:
        # the chains have to meet exactly or Rigify calls them disjoint
        base = body.at(1.0) if body is not None else head.centre
        tip = head.at(1.0)
        mid = (base + tip) * 0.5
        near = (base + mid) * 0.5
        fit_chain(edit_bones, neck_names, [base, near, mid, tip], frozen, fitted)
        mapping.setdefault(head.tag, []).extend(neck_names)
    else:
        warnings.append("No Head tag: the quadruped neck and head keep their default.")

    if tail_tag is not None and body is not None:
        tail = Region(tail_tag, regions[tail_tag]._points,
                      axis_hint=(regions[tail_tag].centre - torso.centre))
        points = [body.at(0.0)] + [tail.at(step / 3.0) for step in range(1, 4)]
        fit_chain(edit_bones, tail_names, points, frozen, fitted)
        mapping.setdefault(tail_tag, []).extend(tail_names)

    for side in ("L", "R"):
        for tags, names, label in (
            (rear, ["thigh.%s" % side, "shin.%s" % side], "rear"),
            (front, ["front_thigh.%s" % side, "front_shin.%s" % side], "front"),
        ):
            tag = tags.get(side)
            if tag is None:
                warnings.append("No %s leg tag for side %s; it keeps its scaled default."
                                % (label, side))
                continue
            leg = Region(tag, regions[tag]._points, axis_hint=Vector((0.0, 0.0, -1.0)))
            hip = leg.at(0.04)
            knee = _bend(hip, leg.at(0.5), leg.at(0.94), Vector((0.0, -1.0, 0.0)))
            ankle = leg.at(0.94)
            fit_chain(edit_bones, names, [hip, knee, ankle], frozen, fitted)
            mapping.setdefault(tag, []).extend(names)

    return fitted


# ---------------------------------------------------------------------------
# chains (ears, tails, anything floppy)
# ---------------------------------------------------------------------------

def follow_from_notes(notes):
    """Turn plain-language motion notes into a follow factor (1 = rigid)."""
    text = (notes or "").lower()
    for words, value in FOLLOW_WORDS:
        for word in words:
            if word in text:
                return value
    return DEFAULT_FOLLOW


def chain_tags(regions, modules, explicit, archetype):
    """Which tags become bone chains rather than limbs.

    A tag is a chain when it is named like one (``Ear.L``, ``Tail``), when the
    caller listed it in ``modules`` as a chain/tail, or when ``spring_chains``
    names it outright.  The quadruped rig already has a tail spine, so its Tail
    tag is fitted there instead of becoming a loose chain.
    """
    wanted = {}
    for name in regions:
        if CHAIN_TAG_RE.match(name):
            if archetype == "quadruped" and name.lower() == "tail":
                continue
            wanted[name] = {"bones": DEFAULT_CHAIN_BONES, "source": "name"}
    for entry in modules or ():
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        tag = entry.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            continue
        tag = tag_display_name(tag.strip())
        if kind in ("chain", "tail", "spring", "jiggle"):
            wanted[tag] = {"bones": int(entry.get("bones") or DEFAULT_CHAIN_BONES),
                           "source": "modules"}
        elif kind in ("limb", "spine") and tag in wanted:
            wanted.pop(tag, None)
    if isinstance(explicit, (list, tuple)):
        for raw in explicit:
            if isinstance(raw, str) and raw.strip():
                wanted[tag_display_name(raw.strip())] = {"bones": DEFAULT_CHAIN_BONES,
                                                         "source": "spring_chains"}
    return {tag: spec for tag, spec in wanted.items() if tag in regions}


def _nearest_bone(edit_bones, point, exclude=()):
    """The existing bone whose body is closest to ``point`` (a chain's anchor)."""
    best = None
    best_distance = None
    for bone in edit_bones:
        if bone.name in exclude:
            continue
        segment = bone.tail - bone.head
        length = segment.length
        if length < 1e-9:
            closest = bone.head
        else:
            t = max(0.0, min(1.0, (point - bone.head).dot(segment) / (length * length)))
            closest = bone.head + segment * t
        distance = (closest - point).length
        if best_distance is None or distance < best_distance:
            best = bone
            best_distance = distance
    return best


def add_chain(meta, region, count, parent_hint=None):
    """Append a straight bone chain along a tag's own axis. Returns bone names."""
    edit_bones = meta.data.edit_bones
    count = max(1, min(12, int(count)))
    base = region.at(0.0)
    tip = region.at(1.0)
    if (tip - base).length < 1e-6:
        tip = base + Vector((0.0, 0.0, max(region.length, 1e-3)))

    made = []
    previous = None
    parent = None
    if parent_hint is not None:
        parent = edit_bones.get(parent_hint)
    if parent is None:
        parent = _nearest_bone(edit_bones, base)
    for index in range(count):
        name = region.tag if index == 0 else "%s.%03d" % (region.tag, index)
        existing = edit_bones.get(name)
        if existing is not None:
            edit_bones.remove(existing)
        bone = edit_bones.new(name)
        bone.head = base + (tip - base) * (index / float(count))
        bone.tail = base + (tip - base) * ((index + 1) / float(count))
        bone.parent = previous if previous is not None else parent
        bone.use_connect = previous is not None
        bone.use_deform = True
        _safe_roll(bone, Vector((0.0, 0.0, 1.0)))
        previous = bone
        made.append(name)
    return made, (parent.name if parent is not None else None)


# ---------------------------------------------------------------------------
# rigforge_metarig
# ---------------------------------------------------------------------------

def _resolve_preset(obj, archetype, requested, regions, warnings):
    """Which metarig template to build, and why."""
    if requested not in (None, "", "auto"):
        return requested, "explicit"
    if archetype == "quadruped":
        return "quadruped", "archetype"
    if archetype in METARIG_OPS and archetype not in ("human", "basic_human"):
        return archetype, "archetype"
    detail = [name for name in regions
              if any(word in name.lower() for word in FACE_TAGS)]
    if detail:
        warnings.append(
            "Detail tags %s found, so the full Rigify human metarig (face and "
            "fingers, ~160 deform bones) was used. Pass preset='basic_human' for a "
            "game-weight rig." % ", ".join(sorted(detail)[:4]))
        return "human", "face/hand tags"
    return "basic_human", "no face or hand tags"


def _scale_metarig_to(meta, obj, regions):
    """Uniformly scale and seat the metarig on the mesh before any fitting.

    Every bone this module does not explicitly fit (feet, fingers, the whole
    face) rides on this transform, which is why it happens first and is applied
    into the armature data.
    """
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    mesh_low = Vector((min(c.x for c in corners), min(c.y for c in corners),
                       min(c.z for c in corners)))
    mesh_high = Vector((max(c.x for c in corners), max(c.y for c in corners),
                        max(c.z for c in corners)))

    heads = [b.head_local for b in meta.data.bones] + [b.tail_local for b in meta.data.bones]
    if not heads:
        raise ForgeError("The metarig has no bones to scale.")
    rig_low = Vector((min(p.x for p in heads), min(p.y for p in heads),
                      min(p.z for p in heads)))
    rig_high = Vector((max(p.x for p in heads), max(p.y for p in heads),
                       max(p.z for p in heads)))

    mesh_span = mesh_high - mesh_low
    rig_span = rig_high - rig_low
    axis = 2 if rig_span.z >= max(rig_span.x, rig_span.y) else (
        0 if rig_span.x >= rig_span.y else 1)
    if rig_span[axis] < 1e-9 or mesh_span[axis] < 1e-9:
        scale = 1.0
    else:
        scale = mesh_span[axis] / rig_span[axis]

    meta.scale = (scale, scale, scale)
    meta.rotation_euler = (0.0, 0.0, 0.0)
    scaled_low = rig_low * scale
    scaled_high = rig_high * scale
    offset = Vector((
        (mesh_low.x + mesh_high.x) * 0.5 - (scaled_low.x + scaled_high.x) * 0.5,
        (mesh_low.y + mesh_high.y) * 0.5 - (scaled_low.y + scaled_high.y) * 0.5,
        mesh_low.z - scaled_low.z,
    ))
    meta.location = offset
    refresh_view_layer()
    with active_only(meta):
        try:
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        except RuntimeError as exc:
            raise ForgeError("Could not apply the metarig's fitting transform: %s" % exc)
    return scale


def _enter_edit(obj):
    try:
        bpy.ops.object.mode_set(mode="EDIT")
    except RuntimeError as exc:
        raise ForgeError("Could not enter Edit Mode on %r: %s" % (obj.name, exc))


def _leave_edit():
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        pass


@command("rigforge_metarig")
def cmd_rigforge_metarig(params):
    """Stage 4a: a Rigify metarig, fitted to the tags rather than merely scaled.

    ``archetype: "auto"`` reads the manifest (the object's stored archetype).
    Missing tags are warnings, never errors: a quadruped with no arms, or a
    sculpt tagged only Head and Torso, still gets a usable best-effort rig.

    ``joints_file`` adds a **third landmark source** — a neural joint detector's
    predictions, written by ``rigbridge/detect_joints.py`` in the
    ``forge.joints/1`` schema.  Predictions never replace the tags: each
    tag-derived landmark moves ``joints_weight`` (0.5) of the way towards a
    prediction that lands within ``joints_tolerance`` (12% of the mesh's span)
    of it, a prediction beyond that but inside ``joints_disagree_band`` (3x) is
    reported as a **disagreement** and overruled, and roles the tags cannot
    cover (fingers, toes) are placed from *named* predictions as best-effort.
    Without ``joints_file`` this command behaves exactly as it always did.

    **The default path is the human rigger's workflow** (``method: "landmarks"``,
    :mod:`~forge.tools.rigforge_landmarks`): the orientation gate first, then
    symmetrize, then joints snapped onto cross-section centroids on the
    character's **left only**, then an exact X-mirror onto the right — which is
    why ``mirror.residual_asymmetry_mm`` is 0.0 and not the 6-24 mm a
    two-sided fit produces.  ``method: "tags"`` is the old fraction-of-the-blob
    fit, and it is also where a mesh the landmarks cannot read falls back to,
    saying so in ``warnings`` and in ``fit_method``.

    ``tags`` decides where the tags the fit slices come from.  ``"auto"`` (the
    default) rebuilds each limb's tag along that limb's **own detected axis**
    when a joint detector is installed, which is what stops a limb that lies
    along the body from being tagged as a box band and then refused; a machine
    without the detector keeps whatever tags the mesh has, exactly as before.
    ``"keep"`` never touches them and ``"detector"`` refuses rather than falling
    back.  ``auto_tags`` in the result names the source of every tag.

    ``breast_bones`` (default **false**) keeps Rigify's ``breast.L/R``.  They are
    off by default because they are a *female torso* feature this pipeline's
    characters mostly do not have, and on one that does not they generate
    ``DEF-`` bones that stand out of the chest as prongs and then collect stray
    influence inside the torso.  Dropped on the **metarig**, before generation,
    so they never reach the deform set at all.  ``dropped_bones`` says what went.
    """
    obj = resolve_object(params, mesh_only=True)
    started = time.monotonic()
    warnings = []
    rigify_info = ensure_rigify()
    keep_breasts = {"breast_bones": get_bool(params, "breast_bones", False)}
    dropped_bones = []

    archetype = get_choice(
        params, "archetype",
        {"AUTO": "auto", "BIPED": "biped", "QUADRUPED": "quadruped", "CUSTOM": "custom"},
        "auto",
    )
    if archetype == "auto":
        stored = rigforge._normalise_archetype(_prop(obj, PROP_ARCHETYPE, "") or "")
        archetype = stored if stored in rigforge.ARCHETYPES else "biped"
        if not stored:
            warnings.append("No archetype on %r or in its manifest; assumed 'biped'."
                            % obj.name)
    preset_param = params.get("preset")
    if preset_param is not None:
        preset_param = get_choice(
            params, "preset",
            {name.upper(): name for name in
             tuple(PRESETS) + tuple(METARIG_OPS)},
            "auto",
        )

    # --- steps 1 and 2 of the human workflow, before a bone exists ---------
    method = get_choice(params, "method",
                        {"LANDMARKS": "landmarks", "TAGS": "tags", "AUTO": "landmarks"},
                        "landmarks")
    if params.get("joints_file") and params.get("method") is None:
        # A joints file is a *refinement of the tag fit*: its predictions are
        # blended into tag-derived landmarks and overruled by them. The landmark
        # workflow takes every joint off the mesh's own cross-sections instead,
        # so the two cannot both be in charge — and quietly ignoring the file
        # the caller went to the trouble of producing is the worse of the two
        # ways to resolve that.
        method = "tags"
        warnings.append(
            "A joints file was passed, so the tag fit ran and the predictions were "
            "blended into it. The landmark workflow measures every joint off the mesh "
            "itself and has no use for a second opinion; pass method='landmarks' to use "
            "it and ignore the file.")
    prepared = None
    if method == "landmarks" and archetype != "quadruped":
        prepared = rigforge_landmarks.prepare_for_rigging(obj, params, warnings)
    elif method == "landmarks":
        method = "tags"
        warnings.append(
            "The landmark workflow is wired into the biped fit only (a quadruped's "
            "limbs, spine and junctions have no agreed roles yet), so this quadruped "
            "was fitted the old way: fractions along each tag.")

    # --- tags that follow each limb's own axis, before the tags are read ---
    # The landmark fitter can only be as good as the tags it is handed: a tag
    # that is a box band rather than a tube has no centreline to slice, and the
    # fitter refuses it (measured on the werewolf: "the tag 'Arm.L' is not shaped
    # like a limb"). Detecting a skeleton and tagging along it is what turns that
    # refusal into a fit, so it runs by default when the detector is installed —
    # after the orientation gate and the symmetrize, because both move the flesh
    # the tags describe.
    autotag_report, axis_hints = _auto_tags(obj, params, method, archetype, prepared,
                                            warnings)

    regions, empty = measure_tags(obj)
    if not regions:
        raise ForgeError(
            "Object %r has no tagged geometry to fit a metarig to. Tag it first "
            "(RigForge panel, or rigforge_tag) - at least Head and Torso." % obj.name)
    if empty:
        warnings.append("Tags with no geometry were ignored: %s." % ", ".join(sorted(empty)))

    preset, preset_reason = _resolve_preset(obj, archetype, preset_param, regions, warnings)
    operator, operator_name = _metarig_operator(preset)

    # --- the third landmark source: a detector's predicted joints
    hints = None
    joints_path = params.get("joints_file")
    if isinstance(joints_path, str) and joints_path.strip():
        joints_path = resolve_path(joints_path.strip())
        data = rigforge_joints.load_joints(joints_path)
        hints = rigforge_joints.JointHints(
            data, obj,
            weight=get_float(params, "joints_weight", rigforge_joints.DEFAULT_WEIGHT,
                             minimum=0.0, maximum=1.0),
            tolerance=get_float(params, "joints_tolerance",
                                rigforge_joints.DEFAULT_TOLERANCE,
                                minimum=0.0, maximum=1.0),
            disagree=get_float(params, "joints_disagree_band",
                               rigforge_joints.DEFAULT_DISAGREE,
                               minimum=1.0, maximum=20.0),
            axis_up=(params.get("joints_axis_up") or None),
            path=joints_path, warnings=warnings)
        if not hints.enabled:
            guess = rigforge_joints.sanity_axis_guess(data, obj)
            if guess:
                warnings.append(
                    "That joints file would land inside the mesh if it were read as "
                    "unit=%s, axis_up=%s (%d of %d joints). Fix the producer, or pass "
                    "joints_axis_up to override."
                    % (guess["unit"], guess["axis_up"], guess["inside"], guess["of"]))
        if archetype == "quadruped" and hints.enabled:
            hints.enabled = False
            warnings.append(
                "Predicted joints are wired into the biped fit only; the quadruped "
                "template's roles (front vs rear limbs, a spine that runs forwards) have "
                "no agreed role names yet, so the joints file was read and not used.")

    name = "%s_metarig" % obj.name
    mapping = {}
    chains_meta = []

    with object_mode():
        existing = bpy.data.objects.get(name)
        if existing is not None:
            data = existing.data
            bpy.data.objects.remove(existing, do_unlink=True)
            if data is not None and getattr(data, "users", 1) == 0:
                try:
                    bpy.data.armatures.remove(data)
                except (ReferenceError, RuntimeError, TypeError):
                    pass

        scene = get_scene()
        cursor = scene.cursor.location.copy()
        try:
            scene.cursor.location = (0.0, 0.0, 0.0)
            status = operator()
        finally:
            scene.cursor.location = cursor
        if "FINISHED" not in status:
            raise ForgeError("Rigify's %s returned %s."
                             % (operator_name, ", ".join(sorted(status)) or "nothing"))
        meta = get_view_layer().objects.active
        if meta is None or meta.type != "ARMATURE":
            raise ForgeError("Rigify's %s did not leave an armature active." % operator_name)
        meta.name = name
        try:
            meta.data.name = name
        except (AttributeError, RuntimeError):
            pass

        scale = _scale_metarig_to(meta, obj, regions)

        landmarks = None
        landmark_report = None
        mirror_report = None
        fit_method = method
        if prepared is not None:
            sides = ("L", "R") if prepared["symmetry_requested"] is False else ("L",)
            try:
                landmarks = rigforge_landmarks.biped_landmarks(
                    obj, midplane=prepared["midplane"],
                    character_left=prepared["character_left"],
                    warnings=warnings, sides=sides, axis_hints=axis_hints,
                    facing=prepared["orientation"]["faces"])
            except ForgeError as exc:
                fit_method = "tags"
                warnings.append(
                    "The landmark fit could not read this mesh, so the old tag-position "
                    "fit ran instead: %s" % exc)

        with active_only(meta):
            _enter_edit(meta)
            try:
                dropped_bones = drop_optional_bones(meta, keep_breasts)
            finally:
                _leave_edit()
        if dropped_bones:
            warnings.append(
                "Removed %d optional bone(s) from the metarig before generation: %s. "
                "Rigify's human template carries them for a female torso; on a figure "
                "that does not have one they generate DEF- bones that stick out of the "
                "chest and then collect stray influence inside the torso. Pass "
                "breast_bones=true to keep them."
                % (len(dropped_bones), ", ".join(dropped_bones)))

        with active_only(meta):
            _enter_edit(meta)
            try:
                if archetype == "quadruped":
                    fitted = fit_quadruped(meta, regions, warnings, mapping)
                elif landmarks is not None:
                    midplane_local = (meta.matrix_world.inverted()
                                      @ Vector((prepared["midplane"], 0.0, 0.0))).x
                    fitted, mirror_report = fit_biped_landmarks(
                        meta, landmarks, warnings, mapping,
                        mirror=(prepared["symmetry_requested"] is not False),
                        midplane_local=midplane_local)
                    if hints is not None:
                        warnings.append(
                            "method='landmarks' was asked for explicitly, so the joints "
                            "file was read and not used: every joint here came off the "
                            "mesh's own cross-sections.")
                else:
                    fitted = fit_biped(meta, regions, warnings, mapping, hints=hints)
                    if hints is not None:
                        fit_best_effort(meta, hints, fitted, warnings)
                    # The fallback keeps the rest of the human workflow. Only the
                    # *joints* come from tag fractions instead of cross-sections;
                    # the mesh was still oriented, symmetrized and re-sided, and
                    # the two halves are still one mirror of the other, which is
                    # the 0.0 mm that no two-sided fit can promise.
                    if prepared is not None and prepared["symmetry_requested"] is not False:
                        midplane_local = (meta.matrix_world.inverted()
                                          @ Vector((prepared["midplane"], 0.0, 0.0))).x
                        mirror_report = rigforge_landmarks.mirror_edit_bones(
                            meta, source="L", midplane_local=midplane_local)
                        fit_method = "tags+mirror"
                        warnings.append(
                            "The joints came from tag positions, but the rest of the "
                            "workflow still ran: the character's left was fitted and "
                            "mirrored onto its right, so the two sides differ by %.4f mm "
                            "rather than by whatever two independent fits happened to "
                            "produce." % mirror_report["residual_asymmetry_mm"])

                specs = chain_tags(regions, params.get("modules"),
                                   params.get("spring_chains"), archetype)
                notes = str(_prop(obj, PROP_MOTION_NOTES, "") or "")
                follow = follow_from_notes(notes)
                for tag in sorted(specs):
                    bones, parent = add_chain(meta, regions[tag], specs[tag]["bones"])
                    mapping.setdefault(tag, []).extend(bones)
                    chains_meta.append({
                        "tag": tag, "bones": bones, "parent": parent,
                        "follow": follow, "source": specs[tag]["source"],
                    })
            finally:
                _leave_edit()

        for chain in chains_meta:
            pose_bone = meta.pose.bones.get(chain["bones"][0])
            if pose_bone is not None and hasattr(pose_bone, "rigify_type"):
                try:
                    pose_bone.rigify_type = "basic.copy_chain"
                    chain["rigify_type"] = "basic.copy_chain"
                except (AttributeError, TypeError, ValueError):
                    chain["rigify_type"] = ""
                    warnings.append(
                        "Could not set a Rigify type on chain %r; its bones will still "
                        "be generated as plain deform bones." % chain["tag"])

        # unfitted regions are the honest half of the report
        unfitted = sorted(set(regions) - set(mapping))
        if unfitted:
            warnings.append(
                "These tags had no place in the %s template, so nothing was fitted to "
                "them: %s. List them in 'modules' as chains if they should move."
                % (preset, ", ".join(unfitted)))

        if hints is not None:
            warnings.extend(hints.summary_warnings())

        _set_prop(meta, PROP_TAG_BONES, json.dumps(mapping))
        _set_prop(meta, PROP_CHAINS, json.dumps(chains_meta))
        _set_prop(meta, PROP_RIG_MESH, obj.name)
        _set_prop(meta, PROP_PRESET, preset)
        _set_prop(obj, PROP_METARIG, meta.name)
        refresh_view_layer()

    # --- the echo-back: the skeleton over the ghosted body, before skinning.
    # A rigger looks at this before they bind anything, because this is the
    # moment a mis-placed bone is still free. Never fatal: a render that cannot
    # run must not cost the artist their metarig.
    echo = None
    if get_bool(params, "echo", True):
        try:
            echo = rigforge_landmarks.render_skeleton_echo(
                meta, obj, _echo_directory(params, obj),
                resolution=get_int(params, "echo_resolution", 768,
                                   minimum=128, maximum=2048))
        except Exception as exc:  # noqa: BLE001 - an artifact, not the product
            warnings.append("The skeleton echo could not be rendered (%s: %s), so this "
                            "fit has numbers but no picture."
                            % (type(exc).__name__, exc))

    joint_landmarks = {}
    if landmarks is not None:
        for role, point in sorted(landmarks["points"].items()):
            info = dict(landmarks["detail"].get(role) or {})
            info.pop("point", None)
            info["mm"] = [round(v * 1000.0, 2) for v in point]
            joint_landmarks[role] = info

    return {
        "object": obj.name,
        "metarig": meta.name,
        "archetype": archetype,
        "preset": preset,
        "preset_reason": preset_reason,
        "metarig_operator": operator_name,
        "bone_count": len(meta.data.bones),
        "fitted_bones": sorted(fitted),
        "mapping": {tag: sorted(set(bones)) for tag, bones in mapping.items()},
        "chains": chains_meta,
        "landmarks": {tag: region.as_dict() for tag, region in sorted(regions.items())},
        "joints": hints.report() if hints is not None else None,
        "fit_method": fit_method,
        "orientation": (prepared["orientation"] if prepared else None),
        "symmetry": (prepared["symmetry"] if prepared else None),
        "side_tags": (prepared["retag"] if prepared else None),
        "midplane_mm": (round(prepared["midplane"] * 1000.0, 3) if prepared else None),
        "joint_landmarks": joint_landmarks,
        # The anatomical pre-bend, per joint, before the mirror: what was nudged,
        # by how many millimetres, and why. A chain already bent the right way
        # reports `nudged: false` and the measurement that earned it.
        "prebend": (landmarks.get("prebend") if landmarks else []),
        # Optional template bones removed before generation, so they never
        # become DEF- bones, vertex groups or weight maps.
        "dropped_bones": dropped_bones,
        "auto_tags": autotag_report,
        "mirror": mirror_report,
        "skeleton_echo": echo,
        "scale": round(scale, 6),
        "rigify": rigify_info,
        "says": _metarig_sentence(obj, fit_method, prepared, mirror_report, echo),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


def _auto_tags(obj, params, method, archetype, prepared, warnings):
    """Rebuild the limb tags along their own detected axes.  ``(report, hints)``.

    ``tags`` is the switch, and its default is ``"auto"``, which means **repair,
    not rewrite**.  Auto-tagging runs only when the tags on the mesh cannot
    already be read as limbs — the landmark fit is attempted first, with
    whatever tags and hints are there, and a mesh it can read keeps every tag it
    has.  That ordering is not a nicety:

    * a tag rebuild is a *mesh edit*, and editing an artist's painted tags
      because a GPU was available would be the tool overreaching;
    * the tags this fixes are exactly the ones the fitter refuses, so the
      refusal is the signal, and there is no better one available;
    * and it costs nothing when it is not needed — the trial fit is pure
      geometry, so a mesh that is already fine never pays for a detection.

    Two more gates, for the same reason:

    * only the biped landmark path, because the quadruped fit has no agreed limb
      roles and is measuring blob fractions either way;
    * a limb whose detected chain is implausible keeps the tag it already had.

    ``tags: "keep"`` turns it off; ``tags: "detector"`` rebuilds regardless of
    whether the current tags would have done, and refuses if it cannot.

    The hints that come back are each limb's measured direction, which is the
    other half of the fix: a perfect cylindrical tag around a *hanging* arm is
    still refused by a fitter that assumes arms run sideways.
    """
    mode = get_choice(params, "tags",
                      {"AUTO": "auto", "DETECTOR": "detector", "UNIRIG": "detector",
                       "KEEP": "keep", "NONE": "keep", "OFF": "keep"}, "auto")
    stored_hints = rigforge_autotag.stored_axis_hints(obj)[0]
    if mode == "keep":
        return {"mode": mode, "used": False,
                "why": "tags='keep': the tags already on the mesh were used as they are."}, \
               stored_hints
    if method != "landmarks" or archetype == "quadruped":
        return {"mode": mode, "used": False,
                "why": ("auto-tagging feeds the biped landmark fit; this run used the "
                        "%s fit on a %s, so the tags were left alone."
                        % (method, archetype))}, None

    complaint = None
    snapshot = None
    if mode == "auto":
        if not rigforge.tag_groups(obj):
            # Repair needs something to repair. A mesh with no tags at all keeps
            # the refusal it has always had ("tag it first"), which is a better
            # answer than inventing a body plan for whatever it happens to be —
            # and it costs no detection. ``tags: "detector"`` still tags from
            # nothing, for the generated-organic lane.
            return {"mode": mode, "used": False,
                    "why": ("%r has no tags to repair. Auto-tagging rebuilds tags the "
                            "landmark fit cannot read; pass tags='detector' to tag an "
                            "untagged mesh from scratch." % obj.name)}, None
        readable, complaint = _tags_already_measure(obj, prepared, stored_hints)
        if readable:
            return {"mode": mode, "used": False,
                    "why": ("the tags already on %r measure as limbs, so they were left "
                            "alone. Auto-tagging repairs tags the landmark fit cannot "
                            "read; it does not overwrite tags that work."
                            % obj.name)}, stored_hints
        snapshot = rigforge_autotag.snapshot_tags(obj)

    status = rigforge_autotag.detector_status(params.get("rigbridge"))
    if not status.get("installed"):
        if mode == "detector":
            raise ForgeError(
                "tags='detector' was asked for and joint detection is not available "
                "here: %s" % status.get("says"))
        # Not a warning: an optional GPU tool that is not installed is not a
        # defect in this run, and a warning per metarig would train the artist
        # to skim them.
        return {"mode": mode, "used": False, "detector": status,
                "why": status.get("says") or "the joint detector is not installed"}, \
               rigforge_autotag.stored_axis_hints(obj)[0]

    joints, detector = _detected_joints(obj, params, warnings)
    if joints is None and mode == "detector":
        raise ForgeError(
            "tags='detector' was asked for and the detection did not produce joints; "
            "see the warnings for what the runner said.")
    report = rigforge_autotag.auto_tag(
        obj, joints=joints, detector=detector,
        midplane=(prepared or {}).get("midplane"),
        character_left=(prepared or {}).get("character_left"),
        radius_factor=get_float(params, "tag_radius_factor",
                                rigforge_autotag.DEFAULT_RADIUS_FACTOR,
                                minimum=1.0, maximum=6.0),
        apply=True, replace=True, warnings=warnings)
    report["mode"] = mode
    hints = {}
    for role, vector in (report.get("axis_hints") or {}).items():
        hints[role] = Vector(vector)
    hints = hints or None

    # The repair has to prove it repaired something. A rebuild that leaves the
    # landmark fit still refusing has not helped — and it is not neutral, because
    # the tag fit downstream will build a rig from the new tags without comment.
    # So `auto` is allowed to move a mesh from "refused" to "fitted" and nothing
    # else; anything else is put back exactly as it was.
    if snapshot is not None:
        readable, still = _tags_already_measure(obj, prepared, hints)
        if not readable:
            rigforge_autotag.restore_tags(obj, snapshot)
            warnings.append(
                "The joint detector rebuilt %r's tags and the landmark fit still could "
                "not read them (%s), so the original tags were put back and nothing "
                "about this fit changed. The detector is a repair, and this one did not "
                "take." % (obj.name, still))
            return {"mode": mode, "used": False, "reverted": True,
                    "attempted": report.get("sources"),
                    "why_refused_before": complaint,
                    "why_refused_after": still,
                    "why": ("the rebuilt tags were refused by the landmark fit too, so "
                            "they were reverted")}, stored_hints
        warnings.append(
            "The tags on %r could not be read as limbs, so the joint detector rebuilt "
            "them and the landmark fit now reads them. The refusal was: %s"
            % (obj.name, complaint))

    report["used"] = True
    return report, hints


def _tags_already_measure(obj, prepared, hints):
    """``(True, None)`` when the tags on ``obj`` can already be read as limbs.

    The trial is the real thing — :func:`rigforge_landmarks.biped_landmarks`
    with the tags and hints this fit would actually use — because any cheaper
    proxy would eventually disagree with it, and the whole point is to predict
    *its* verdict.  It touches nothing: the landmark pass is a measurement.
    """
    prepared = prepared or {}
    try:
        rigforge_landmarks.biped_landmarks(
            obj, midplane=prepared.get("midplane", 0.0),
            character_left=prepared.get("character_left", 1.0),
            warnings=[],
            sides=(("L", "R") if prepared.get("symmetry_requested") is False else ("L",)),
            axis_hints=hints)
    except ForgeError as exc:
        return False, str(exc)
    return True, None


def _detected_joints(obj, params, warnings):
    """``(joints document | None, provenance)`` — the detection, and where it came from.

    The provenance travels with the report whether or not the detection worked,
    because "the tags came from a cache hit of a run on this exact mesh" and
    "the tags came from the box band because the GPU was busy" are the two
    things a reader of the result most needs to tell apart.
    """
    result = rigforge_autotag.detect_joints_for(
        obj, seed=get_int(params, "tag_seed", 12345, minimum=0, maximum=2 ** 31 - 1),
        refresh=get_bool(params, "tag_refresh", False),
        rigbridge=params.get("rigbridge"))
    provenance = {key: value for key, value in result.items() if key != "joints"}
    if result.get("ok"):
        return result["joints"], provenance
    warnings.append(
        "Joint detection did not run for the tags (%s), so the tags on the mesh were "
        "kept: %s" % (result.get("reason"), result.get("says") or ""))
    return None, provenance


def _echo_directory(params, obj):
    """Where the skeleton echo lands when the caller did not say."""
    wanted = params.get("echo_dir") or params.get("echo_path")
    if isinstance(wanted, str) and wanted.strip():
        return wanted.strip()
    base = None
    try:
        if bpy.data.filepath:
            base = os.path.join(os.path.dirname(bpy.data.filepath), "forge_echo")
    except (AttributeError, OSError):
        base = None
    if base is None:
        import tempfile

        base = os.path.join(tempfile.gettempdir(), "forge_echo")
    return os.path.join(base, re.sub(r"[^A-Za-z0-9_.-]", "_", obj.name))


def _metarig_sentence(obj, fit_method, prepared, mirror_report, echo):
    lines = []
    if fit_method == "landmarks":
        lines.append(
            "%s was rigged the way a person does it: oriented, symmetrized, joints "
            "snapped onto the mesh's own cross-section centroids on the character's "
            "left, then X-mirrored." % obj.name)
    else:
        lines.append("%s was fitted by tag position (the fallback path): joints are "
                     "fractions along each tagged blob, not landmarks." % obj.name)
    if prepared:
        orientation = prepared["orientation"]
        lines.append("It faces %s%s." % (orientation["faces"],
                                         "" if orientation.get("rotated_deg") in (0.0, None)
                                         else " after a %+.0f degree fix"
                                         % orientation["rotated_deg"]))
        symmetry = prepared["symmetry"]
        if symmetry.get("after_mm") is not None:
            lines.append("Mesh asymmetry %.2f mm, symmetrized to %.3f mm."
                         % (symmetry["removed_mm"], symmetry["after_mm"]))
        elif symmetry.get("measured_mm") is not None:
            lines.append("Mesh asymmetry %.2f mm." % symmetry["measured_mm"])
        if (prepared.get("retag") or {}).get("swapped_tags"):
            lines.append("Sided tags %s were swapped to match the geometry."
                         % ", ".join(prepared["retag"]["swapped_tags"]))
    if mirror_report is not None:
        lines.append("Left/right asymmetry after the mirror: %.4f mm."
                     % mirror_report["residual_asymmetry_mm"])
    if echo and echo.get("images"):
        lines.append("Look at the skeleton before binding: %s."
                     % ", ".join(entry["path"] for entry in echo["images"]))
    return " ".join(lines)


# ---------------------------------------------------------------------------
# generate: rigify, weights, cleanup
# ---------------------------------------------------------------------------

def _stored_json(obj, key, fallback):
    raw = _prop(obj, key, "")
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return fallback
    return parsed


def deform_bones(rig):
    """Names of the bones a mesh may actually be weighted to."""
    return [bone.name for bone in rig.data.bones if bone.use_deform]


def def_bones_for(rig, metarig_bone):
    """Every ``DEF-`` bone Rigify generated for one metarig bone.

    Rigify subdivides some deform bones (``upper_arm.L`` becomes
    ``DEF-upper_arm.L`` plus ``DEF-upper_arm.L.001``), so the mapping is
    one-to-many and has to be resolved against the rig that actually exists.
    """
    out = []
    exact = DEF_PREFIX + metarig_bone
    for bone in rig.data.bones:
        name = bone.name
        if name == exact:
            out.append(name)
        elif name.startswith(exact + "."):
            suffix = name[len(exact) + 1:]
            if suffix.isdigit():
                out.append(name)
    return out


def tag_bone_map(rig, metarig, regions=None, band=0.0):
    """``{tag: set(deform bone names)}`` — from the metarig mapping, then geometry.

    The mapping :func:`cmd_rigforge_metarig` recorded is the truth for the bones
    it placed; anything else Rigify generated (pelvis, breast, an ear chain the
    user added by hand) is assigned to whichever tagged region its middle sits
    in.  That fallback is also the whole answer for a rig this add-on did not
    build.
    """
    mapping = {}
    claimed = set()
    if metarig is not None:
        stored = _stored_json(metarig, PROP_TAG_BONES, {})
        if isinstance(stored, dict):
            for tag, bones in stored.items():
                if not isinstance(bones, (list, tuple)):
                    continue
                for bone in bones:
                    for name in def_bones_for(rig, str(bone)):
                        mapping.setdefault(tag, set()).add(name)
                        claimed.add(name)
    if regions:
        matrix = rig.matrix_world
        for bone in rig.data.bones:
            if not bone.use_deform or bone.name in claimed:
                continue
            middle = matrix @ ((bone.head_local + bone.tail_local) * 0.5)
            best = None
            best_distance = None
            for tag, region in regions.items():
                distance = region.distance_to(middle)
                if best_distance is None or distance < best_distance:
                    best, best_distance = tag, distance
            if best is not None and best_distance is not None and best_distance <= band:
                mapping.setdefault(best, set()).add(bone.name)
    return mapping


def _point_segment_distance(point, a, b):
    segment = b - a
    length_sq = segment.length_squared
    if length_sq < 1e-18:
        return (point - a).length
    t = max(0.0, min(1.0, (point - a).dot(segment) / length_sq))
    return (point - (a + segment * t)).length


def distance_weights(obj, rig, vertices=None, influences=4):
    """Weight vertices by distance to each deform bone. The auto-weights fallback.

    Blender's automatic weights are a heat-diffusion solve that flatly refuses
    some meshes ("Bone Heat Weighting: failed to find solution for one or more
    bones"), usually because of interior geometry or a bone outside the surface.
    Inverse-square distance to the bone *segment* is cruder but never fails and
    never leaves a vertex unweighted, which is what matters at this point in the
    pipeline: the sculptor can polish, but only if there is something to polish.
    """
    bones = [bone for bone in rig.data.bones if bone.use_deform]
    if not bones:
        return 0
    rig_matrix = rig.matrix_world
    segments = [(bone.name,
                 rig_matrix @ bone.head_local,
                 rig_matrix @ bone.tail_local) for bone in bones]
    groups = {}
    for name, _a, _b in segments:
        group = obj.vertex_groups.get(name)
        groups[name] = group if group is not None else obj.vertex_groups.new(name=name)

    matrix = obj.matrix_world
    targets = obj.data.vertices if vertices is None else [obj.data.vertices[i] for i in vertices]
    buckets = {name: [] for name, _a, _b in segments}
    for vertex in targets:
        point = matrix @ vertex.co
        scored = []
        for name, a, b in segments:
            distance = _point_segment_distance(point, a, b)
            scored.append((distance, name))
        scored.sort()
        chosen = scored[: max(1, int(influences))]
        weights = []
        for distance, name in chosen:
            weights.append((name, 1.0 / max(distance, 1e-4) ** 2))
        total = sum(weight for _name, weight in weights) or 1.0
        for name, weight in weights:
            buckets[name].append((vertex.index, weight / total))
    written = 0
    for name, entries in buckets.items():
        if not entries:
            continue
        group = groups[name]
        for index, weight in entries:
            group.add([index], weight, "REPLACE")
            written += 1
    obj.data.update()
    return written


def transfer_deform_weights(source, target, rig, vertices=None):
    """Copy deform weights from a skinned mesh onto another by nearest vertex.

    Used for LODs, which are decimated *before* skinning and therefore arrive at
    export time with tags but no weights.  Nearest-vertex transfer keeps an LOD
    deforming like the mesh it was decimated from; distance-to-bone (the other
    fallback) does not know about the shoulder the artist just fixed by hand.
    """
    from mathutils.kdtree import KDTree

    names = set(deform_bones(rig))
    source_groups = {group.index: group.name for group in source.vertex_groups
                     if group.name in names}
    if not source_groups or not len(source.data.vertices):
        return 0
    tree = KDTree(len(source.data.vertices))
    for vertex in source.data.vertices:
        tree.insert(source.matrix_world @ vertex.co, vertex.index)
    tree.balance()

    weights = {}
    for vertex in source.data.vertices:
        entries = [(source_groups[element.group], float(element.weight))
                   for element in vertex.groups
                   if element.group in source_groups and element.weight > 0.0]
        if entries:
            weights[vertex.index] = entries

    groups = {}
    matrix = target.matrix_world
    targets = (target.data.vertices if vertices is None
               else [target.data.vertices[i] for i in vertices])
    written = 0
    for vertex in targets:
        _co, index, _distance = tree.find(matrix @ vertex.co)
        for name, weight in weights.get(index, ()):
            group = groups.get(name)
            if group is None:
                group = target.vertex_groups.get(name) or target.vertex_groups.new(name=name)
                groups[name] = group
            group.add([vertex.index], weight, "REPLACE")
            written += 1
    target.data.update()
    return written


def _unweighted_vertices(obj, names):
    indices = {group.index for group in obj.vertex_groups if group.name in names}
    out = []
    for vertex in obj.data.vertices:
        if not any(entry.group in indices and entry.weight > 0.0 for entry in vertex.groups):
            out.append(vertex.index)
    return out


def parent_with_weights(obj, rig, warnings):
    """Parent ``obj`` to ``rig`` with automatic weights, repairing what fails."""
    report = {"method": "auto_weights", "unweighted_before": None,
              "unweighted_after": 0, "repaired": 0, "status": ""}
    names = set(deform_bones(rig))
    if not names:
        raise ForgeError("Rig %r has no deforming bones to weight to." % rig.name)

    refresh_view_layer()
    failure = ""
    with selection([obj, rig], rig):
        try:
            status = bpy.ops.object.parent_set(
                **op_kwargs(bpy.ops.object.parent_set,
                            {"type": "ARMATURE_AUTO", "keep_transform": True}))
            report["status"] = ", ".join(sorted(status)) or "nothing"
            if "FINISHED" not in status:
                failure = "parent_set returned %s" % report["status"]
        except RuntimeError as exc:
            failure = str(exc)

    if failure:
        warnings.append(
            "Automatic (bone heat) weights failed on %r: %s. Fell back to "
            "distance weights - check the shoulders and hips before animating."
            % (obj.name, failure))
        report["method"] = "distance_weights"
        obj.parent = rig
        obj.matrix_parent_inverse = rig.matrix_world.inverted_safe()
        modifier = None
        for existing in obj.modifiers:
            if existing.type == "ARMATURE":
                modifier = existing
                break
        if modifier is None:
            modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = rig
        report["repaired"] = distance_weights(obj, rig)
    else:
        missing = _unweighted_vertices(obj, names)
        report["unweighted_before"] = len(missing)
        if missing:
            warnings.append(
                "Bone heat left %d vertex/vertices of %r with no weight at all; they "
                "were filled in with distance weights." % (len(missing), obj.name))
            report["method"] = "auto_weights+distance_repair"
            report["repaired"] = distance_weights(obj, rig, vertices=missing)

    report["unweighted_after"] = len(_unweighted_vertices(obj, names))
    return report


# --- weight rules ----------------------------------------------------------

def _deform_group_indices(obj, rig):
    names = set(deform_bones(rig))
    return {group.index: group.name for group in obj.vertex_groups if group.name in names}


def cleanup_weights(obj, rig, regions, bones_by_tag, band):
    """Zero weights a tag has no business carrying. Returns the per-tag report.

    The rule from the plan, stated exactly: *no head weights below the neck
    tag*.  Generalised: a vertex inside tag **T** may only be weighted to a bone
    that belongs to another tag **O** when the vertex is within ``band`` of
    O's own region.  That band is what keeps the shoulder and the neck blending
    instead of shearing.
    """
    bone_owner = {}
    for tag, names in bones_by_tag.items():
        for name in names:
            bone_owner[name] = tag
    group_names = _deform_group_indices(obj, rig)
    name_to_index = {name: index for index, name in group_names.items()}

    entries = []
    total_zeroed = 0
    removals = {}
    for tag, region in sorted(regions.items()):
        group = obj.vertex_groups.get(tag_group_name(tag))
        if group is None:
            continue
        matrix = obj.matrix_world
        touched = 0
        zeroed = 0
        removed_weight = 0.0
        offenders = {}
        for vertex in obj.data.vertices:
            in_tag = False
            for element in vertex.groups:
                if element.group == group.index and element.weight > 0.0:
                    in_tag = True
                    break
            if not in_tag:
                continue
            touched += 1
            point = matrix @ vertex.co
            for element in vertex.groups:
                name = group_names.get(element.group)
                if name is None or element.weight <= 0.0:
                    continue
                owner = bone_owner.get(name)
                if owner is None or owner == tag:
                    continue
                other = regions.get(owner)
                if other is not None and other.distance_to(point) <= band:
                    continue
                removed_weight += float(element.weight)
                element.weight = 0.0
                removals.setdefault(name, []).append(vertex.index)
                offenders[name] = offenders.get(name, 0) + 1
                zeroed += 1
        total_zeroed += zeroed
        entries.append({
            "tag": tag,
            "vertices": touched,
            "weights_zeroed": zeroed,
            "weight_removed": round(removed_weight, 6),
            "bones": sorted(offenders),
            "per_bone": dict(sorted(offenders.items())),
        })

    for name, indices in removals.items():
        index = name_to_index.get(name)
        group = obj.vertex_groups.get(name)
        if group is not None:
            group.remove(sorted(set(indices)))
    obj.data.update()
    return {"tags": entries, "weights_zeroed": total_zeroed, "band": round(band, 6)}


def limit_and_normalize(obj, rig, max_influences=4):
    """Keep the N strongest deform weights per vertex and normalise them to 1.

    Done in Python rather than through ``vertex_group_limit_total``: tags live
    in the same vertex-group namespace as deform weights, and the operator's
    group filters are not a safe way to protect ``tag_*`` groups from being
    normalised into nonsense.
    """
    group_names = _deform_group_indices(obj, rig)
    if not group_names:
        return {"limited": 0, "normalized": 0, "max_influences": int(max_influences),
                "deform_groups": 0}
    limit = max(1, int(max_influences))
    removals = {}
    limited = 0
    normalized = 0
    for vertex in obj.data.vertices:
        entries = [element for element in vertex.groups
                   if element.group in group_names and element.weight > 0.0]
        if not entries:
            continue
        if len(entries) > limit:
            entries.sort(key=lambda element: element.weight, reverse=True)
            for element in entries[limit:]:
                removals.setdefault(group_names[element.group], []).append(vertex.index)
                element.weight = 0.0
            entries = entries[:limit]
            limited += 1
        total = sum(float(element.weight) for element in entries)
        if total > 0.0 and abs(total - 1.0) > 1e-5:
            for element in entries:
                element.weight = float(element.weight) / total
            normalized += 1
    for name, indices in removals.items():
        group = obj.vertex_groups.get(name)
        if group is not None:
            group.remove(sorted(set(indices)))
    obj.data.update()
    return {"limited": limited, "normalized": normalized,
            "max_influences": limit, "deform_groups": len(group_names)}


def weight_report(obj, rig, overlap=True):
    """Per-bone influence counts, the two numbers that mean trouble, and the matrix.

    The counts answer *how much does each bone move*.  The **overlap matrix**
    (:func:`~forge.tools.rigforge_landmarks.influence_overlap`) answers the
    question the owner actually asked — *what does one movement do to another* —
    by naming every pair of bones that share influence over the same vertices,
    with the mass they share and the distance between them.  It rides along here
    because a weight report without it is a list of numbers nobody can act on.
    """
    group_names = _deform_group_indices(obj, rig)
    counts = {name: 0 for name in group_names.values()}
    unnormalized = 0
    over = 0
    unweighted = 0
    worst = 0
    for vertex in obj.data.vertices:
        entries = [element for element in vertex.groups
                   if element.group in group_names and element.weight > 0.0]
        if not entries:
            unweighted += 1
            continue
        if len(entries) > 4:
            over += 1
        worst = max(worst, len(entries))
        total = sum(float(element.weight) for element in entries)
        if abs(total - 1.0) > 1e-3:
            unnormalized += 1
        for element in entries:
            counts[group_names[element.group]] += 1
    matrix = None
    continuity = None
    if overlap:
        try:
            matrix = rigforge_landmarks.influence_overlap(rig, obj)
        except Exception as exc:  # noqa: BLE001 - a report that cannot run says so
            matrix = {"verdict": "unmeasured", "says": str(exc)}
        # The overlap matrix answers "does this bone move flesh that is not
        # its own"; it cannot see the other half of the live defect, which was
        # a map that is patchy *inside* one limb. Imported here rather than at
        # module scope because rigforge_skin is built on this module.
        try:
            from . import rigforge_skin

            continuity = rigforge_skin.weight_continuity(rig, obj)
        except Exception as exc:  # noqa: BLE001
            continuity = {"verdict": "unmeasured", "says": str(exc)}
    return {
        "object": obj.name,
        "rig": rig.name,
        "overlap": matrix,
        "continuity": continuity,
        "deform_groups": len(group_names),
        "total_vertices": len(obj.data.vertices),
        "unweighted_vertices": unweighted,
        "unnormalized_vertices": unnormalized,
        "over_influenced_vertices": over,
        "max_influences_found": worst,
        "bones": {name: count for name, count in sorted(counts.items()) if count},
        "unused_bones": sorted(name for name, count in counts.items() if not count),
    }


# --- secondary motion ------------------------------------------------------

def add_spring_chains(rig, chains, warnings, influence=0.6):
    """Secondary motion v1: deterministic lag, no simulation.

    Blender has no spring-bone primitive, and every real one (Wiggle Bones and
    friends) is a download.  What this builds instead, per chain:

    * ``MCH-lag-<tag>`` — a bone parented to the chain's parent, sitting at the
      chain's tip.  It follows the head exactly.
    * ``MCH-lagmix-<tag>`` — a bone parented to ``root`` at the same place, with
      a Copy Transforms constraint onto ``MCH-lag-<tag>`` at influence
      ``follow``.  At influence 1 it tracks the head perfectly; below 1 it
      blends between the head's motion and its own rest pose, so it arrives
      *late* and short.
    * every ``DEF-`` bone of the chain gets a Damped Track onto that mixer,
      influence rising towards the tip.

    So the ears swing towards where the head *was*, the effect is a pure
    function of the pose (no state, no frame order, no cache), and it bakes
    into an exported action like any other constraint.  It is an approximation
    of inertia, not a simulation of it — hence "v1".
    """
    if not chains:
        return []
    made = []
    with active_only(rig):
        _enter_edit(rig)
        try:
            edit_bones = rig.data.edit_bones
            root = edit_bones.get(ROOT_BONE)
            for chain in chains:
                bones = [name for name in chain.get("bones") or []]
                def_names = []
                for name in bones:
                    def_names.extend(def_bones_for_edit(edit_bones, name))
                if not def_names:
                    warnings.append(
                        "Chain %r produced no deform bones in the generated rig, so it "
                        "got no secondary motion." % chain.get("tag"))
                    continue
                tip = edit_bones[def_names[-1]].tail.copy()
                length = max((edit_bones[def_names[-1]].tail
                              - edit_bones[def_names[0]].head).length, 1e-3)
                parent_name = chain.get("parent")
                anchor = None
                for candidate in (DEF_PREFIX + str(parent_name), str(parent_name),
                                  "ORG-" + str(parent_name)):
                    if candidate in edit_bones:
                        anchor = edit_bones[candidate]
                        break
                if anchor is None:
                    anchor = root
                lag_name = LAG_PREFIX + chain["tag"]
                mix_name = LAGMIX_PREFIX + chain["tag"]
                for name, parent in ((lag_name, anchor), (mix_name, root)):
                    existing = edit_bones.get(name)
                    if existing is not None:
                        edit_bones.remove(existing)
                    bone = edit_bones.new(name)
                    bone.head = tip
                    bone.tail = tip + Vector((0.0, 0.0, length * 0.25))
                    bone.parent = parent
                    bone.use_connect = False
                    bone.use_deform = False
                made.append({"tag": chain["tag"], "lag": lag_name, "mixer": mix_name,
                             "bones": def_names, "follow": chain.get("follow",
                                                                     DEFAULT_FOLLOW)})
        finally:
            _leave_edit()

    for entry in made:
        mixer = rig.pose.bones.get(entry["mixer"])
        if mixer is not None:
            constraint = mixer.constraints.new("COPY_TRANSFORMS")
            constraint.name = "Forge Lag"
            constraint.target = rig
            constraint.subtarget = entry["lag"]
            constraint.influence = max(0.0, min(1.0, float(entry["follow"])))
        count = len(entry["bones"])
        for index, name in enumerate(entry["bones"]):
            pose_bone = rig.pose.bones.get(name)
            if pose_bone is None:
                continue
            constraint = pose_bone.constraints.new("DAMPED_TRACK")
            constraint.name = "Forge Secondary Motion"
            constraint.target = rig
            constraint.subtarget = entry["mixer"]
            try:
                constraint.track_axis = "TRACK_Y"
            except (AttributeError, TypeError, ValueError):
                pass
            constraint.influence = float(influence) * ((index + 1) / float(count))
    return made


def def_bones_for_edit(edit_bones, metarig_bone):
    exact = DEF_PREFIX + metarig_bone
    out = []
    for bone in edit_bones:
        if bone.name == exact:
            out.append(bone.name)
        elif bone.name.startswith(exact + ".") and bone.name[len(exact) + 1:].isdigit():
            out.append(bone.name)
    return sorted(out)


# ---------------------------------------------------------------------------
# the IK control layer
# ---------------------------------------------------------------------------
#
# The 2026-09-17 audit asked whether this pipeline does "best practices on
# rigging like inverse kinematics for legs".  The answer the code gives, and
# the reason this section exists, is in three parts:
#
# 1. **The rig builder is Rigify, not a custom skeleton.**  The 29 bones an
#    audit sees are the *metarig* (``basic_human``, 29 bones).  Rigify's
#    generate turns those 29 into a 222-bone control rig that already ships
#    leg IK (``foot_ik``, ``thigh_ik_target`` pole, and the classic three
#    pivots ``foot_heel_ik`` / ``foot_spin_ik`` / ``toe_ik``), arm IK
#    (``hand_ik``, ``upper_arm_ik_target``) and a per-limb ``IK_FK`` blend on
#    the limb's parent control.  35 of those bones deform.  So the IK was
#    never missing from the *rig* — importing Rigify complexity is not the
#    work, because the complexity is already generated.
# 2. **Forge's own tooling was switching it off.**  ``rigforge_keyframe`` and
#    ``rigforge_retarget`` called ``set_fk`` on the *whole rig* whenever any
#    FK bone was keyed, so keying one arm silently moved both legs to FK.  An
#    FK-keyed leg is the foot-slide anti-pattern: nothing holds the foot on
#    the ground between keys.  That is fixed in :mod:`forge.tools.rigforge_anim`
#    (the switch is now per limb) and this section is what makes "per limb"
#    expressible.
# 3. **The convention has to be stated, defaulted and reported.**  Game
#    animation keys legs through IK targets (planted feet) and arms in FK
#    (arcs), so a generated rig leaves this module with legs on IK, arms on
#    FK, and both poles live — and says so in the generate report.
#
# Nothing here adds, removes or renames a deform bone.  Every function in this
# section writes custom properties on control bones only, which is why
# skinning, ``rig_check`` and the Godot export path cannot notice it.

#: Rigify's per-limb IK/FK blend.  It lives on the limb's **parent** control
#: (``thigh_parent.L``, ``upper_arm_parent.L``) and runs **0.0 = full IK,
#: 1.0 = full FK** - the opposite way round from how it reads out loud, which
#: is exactly the kind of thing that is worth a named constant.
IK_FK_PROP = "IK_FK"

#: Rigify's "use a real pole target instead of the IK-root's roll" switch, on
#: the same parent control.  With it off, ``thigh_ik_target.L`` exists, is
#: hidden, and moving it does nothing at all (measured: 0.0 mm of knee travel).
POLE_PROP = "pole_vector"

IK_MODE_IK = "ik"
IK_MODE_FK = "fk"
IK_MODE_BLEND = "blend"

#: The game-animation convention, and the default a generated rig leaves with.
#: Legs animate through their IK targets so the feet can be planted; arms
#: animate in FK so the hands swing on arcs instead of being nailed in space.
IK_CONVENTION = {"leg": IK_MODE_IK, "arm": IK_MODE_FK}

#: One row per limb kind Rigify's biped generates.  ``%s`` is the side.
#: A row whose switch or IK target is missing is skipped, so a quadruped, a
#: half-rigged metarig or a future Rigify rename degrades to "fewer limbs
#: reported" rather than to an exception.
IK_LIMB_TABLE = (
    {"limb": "leg", "label": "leg", "switch": "thigh_parent.%s",
     "ik_target": "foot_ik.%s", "pole": "thigh_ik_target.%s",
     "ik_root": "thigh_ik.%s", "solver": "MCH-shin_ik.%s",
     "fk_chain": ("thigh_fk.%s", "shin_fk.%s", "foot_fk.%s", "toe_fk.%s"),
     # The classic three-pivot foot roll. ``foot_heel_ik`` carries the roll
     # itself (+X rolls over the ball and lifts the heel - measured, the ball
     # and toe stay put to 0.0 mm; -X pivots about the heel for the strike),
     # ``foot_spin_ik`` is the spin pivot under it, ``toe_ik`` the toe.
     "roll": (("heel", "foot_heel_ik.%s"), ("spin", "foot_spin_ik.%s"),
              ("toe", "toe_ik.%s")),
     "deform": ("thigh.%s", "shin.%s", "foot.%s", "toe.%s"),
     "tip": ("toe.%s", "foot.%s")},
    {"limb": "arm", "label": "arm", "switch": "upper_arm_parent.%s",
     "ik_target": "hand_ik.%s", "pole": "upper_arm_ik_target.%s",
     "ik_root": "upper_arm_ik.%s", "solver": "MCH-forearm_ik.%s",
     "fk_chain": ("upper_arm_fk.%s", "forearm_fk.%s", "hand_fk.%s"),
     "roll": (),
     "deform": ("upper_arm.%s", "forearm.%s", "hand.%s"),
     "tip": ("hand.%s",)},
)

#: Control-name fragments -> the limb a bone belongs to, so a *keyed* bone can
#: say which limb's switch it needs.  Ordered, first match wins, and the order
#: is the whole design: "upper_arm" and "forearm" both contain "arm", and
#: "front_thigh" contains "thigh", so the specific spellings are tested first.
LIMB_FRAGMENTS = (
    ("front_thigh", "front_leg"), ("front_shin", "front_leg"),
    ("front_foot", "front_leg"), ("front_paw", "front_leg"),
    ("front_toe", "front_leg"),
    ("upper_arm", "arm"), ("forearm", "arm"), ("hand", "arm"), ("palm", "arm"),
    ("thigh", "leg"), ("shin", "leg"), ("foot", "leg"), ("toe", "leg"),
    ("heel", "leg"),
)

#: Below/above these the blend is called ik/fk rather than a blend.
_IK_EPSILON = 1e-3


def limb_of_bone(name):
    """``('leg', 'L')`` for a control bone name, or ``None`` if it is not a limb.

    Used to scope an FK switch to the limbs that were actually keyed.  The
    prefixes come off first so ``DEF-thigh.L`` and ``ORG-thigh.L`` classify the
    same way as ``thigh_fk.L``.
    """
    text = str(name or "").strip()
    for prefix in CONTROL_PREFIXES + (DEF_PREFIX,):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    lowered = text.lower()
    side = None
    for marker in (".l", "_l"):
        if lowered.endswith(marker) or (marker + ".") in lowered:
            side = "L"
            break
    if side is None:
        for marker in (".r", "_r"):
            if lowered.endswith(marker) or (marker + ".") in lowered:
                side = "R"
                break
    for fragment, limb in LIMB_FRAGMENTS:
        if fragment in lowered:
            return (limb, side)
    return None


def limb_name(limb, side):
    """``leg`` + ``L`` -> ``"leg.L"``; a sideless limb keeps its bare name."""
    return "%s.%s" % (limb, side) if side else str(limb)


def _mode_of(value):
    if value <= _IK_EPSILON:
        return IK_MODE_IK
    if value >= 1.0 - _IK_EPSILON:
        return IK_MODE_FK
    return IK_MODE_BLEND


def _ik_constraints_on(rig, bone_name):
    """Every real ``IK`` constraint on one bone, described.

    The ``muted`` flag is driver-driven (Rigify mutes the pole variant or the
    poleless one depending on :data:`POLE_PROP`) and is only true after a
    depsgraph evaluation, so it is *reported* and never asserted on.
    """
    out = []
    bone = rig.pose.bones.get(bone_name or "")
    if bone is None:
        return out
    for constraint in bone.constraints:
        if constraint.type != "IK":
            continue
        out.append({
            "bone": bone.name,
            "constraint": constraint.name,
            "target": getattr(getattr(constraint, "target", None), "name", None),
            "subtarget": getattr(constraint, "subtarget", "") or None,
            "pole_subtarget": getattr(constraint, "pole_subtarget", "") or None,
            "chain_count": int(getattr(constraint, "chain_count", 0) or 0),
            "muted": bool(getattr(constraint, "mute", False)),
        })
    return out


def ik_limbs(rig, limbs=None):
    """Every IK-capable limb on ``rig``: target, pole, switch, roll pivots.

    ``limbs`` filters by name (``"leg.L"``, or ``"leg"`` for both sides).
    Missing bones are skipped rather than invented: what comes back is what
    this rig actually has.
    """
    wanted = None
    if limbs:
        wanted = {str(name).strip().lower() for name in limbs if str(name).strip()}
    out = []
    for row in IK_LIMB_TABLE:
        for side in ("L", "R"):
            def named(pattern):
                return pattern % side if pattern and "%s" in pattern else pattern

            switch = named(row["switch"])
            target = named(row["ik_target"])
            switch_bone = rig.pose.bones.get(switch)
            if switch_bone is None or target not in rig.pose.bones:
                continue
            try:
                blend = float(switch_bone[IK_FK_PROP])
            except (KeyError, TypeError, ValueError):
                continue
            name = limb_name(row["limb"], side)
            if wanted is not None and name.lower() not in wanted \
                    and row["limb"].lower() not in wanted:
                continue
            pole = named(row["pole"])
            pole_on = None
            if POLE_PROP in switch_bone.keys():
                try:
                    pole_on = bool(switch_bone[POLE_PROP])
                except (TypeError, ValueError):  # pragma: no cover - odd ID prop
                    pole_on = None
            solver = named(row["solver"])
            constraints = _ik_constraints_on(rig, solver)
            deform = []
            for pattern in row["deform"]:
                deform.extend(def_bones_for(rig, named(pattern)))
            roll = {}
            for label, pattern in row["roll"]:
                bone_name = named(pattern)
                if bone_name in rig.pose.bones:
                    roll[label] = bone_name
            tip = None
            for pattern in row["tip"]:
                candidates = def_bones_for(rig, named(pattern))
                if candidates:
                    tip = candidates[0]
                    break
            out.append({
                "name": name,
                "limb": row["limb"],
                "label": "%s %s" % ({"L": "left", "R": "right"}[side], row["label"]),
                "side": side,
                "switch_bone": switch,
                "switch_prop": IK_FK_PROP,
                "ik_fk": round(blend, 4),
                "mode": _mode_of(blend),
                "default_mode": IK_CONVENTION.get(row["limb"], IK_MODE_FK),
                "ik_target": target,
                "ik_root": named(row["ik_root"]) if named(row["ik_root"]) in rig.pose.bones
                else None,
                "pole_target": pole if pole in rig.pose.bones else None,
                "pole_enabled": pole_on,
                "solver_bone": solver if solver in rig.pose.bones else None,
                "ik_constraints": constraints,
                "chain_count": max([c["chain_count"] for c in constraints] or [0]),
                "fk_chain": [named(b) for b in row["fk_chain"]
                             if named(b) in rig.pose.bones],
                "roll_pivots": roll,
                "deform_bones": sorted(set(deform)),
                "tip_bone": tip,
            })
    return out


def _retag(rig):
    """Tell the depsgraph a custom property changed.

    Rigify's whole IK/FK blend is **drivers reading a custom property**: the
    ``IK_FK`` float mutes IK constraints and drives the ``ORG-`` bones' Copy
    Transforms influences.  Writing the property does not by itself tag
    anything for re-evaluation, so without this the value reads back correctly
    and the rig keeps posing the old way — the most convincing kind of silent
    no-op there is.  Measured: switching a leg to FK moved the deform bones by
    0.0 mm until this call existed.
    """
    try:
        rig.update_tag(refresh={"DATA"})
    except (AttributeError, TypeError, RuntimeError):  # pragma: no cover
        try:
            rig.update_tag()
        except (AttributeError, RuntimeError):
            pass
    refresh_view_layer()


def set_limb_mode(rig, entry, mode, keyframe_at=None):
    """Move one limb's ``IK_FK`` blend, optionally keyframing it.

    Returns the value it set, or ``None`` when the property refused it.
    """
    bone = rig.pose.bones.get(entry["switch_bone"])
    if bone is None:
        return None
    value = 1.0 if mode == IK_MODE_FK else 0.0
    prop = entry.get("switch_prop") or IK_FK_PROP
    try:
        bone[prop] = value
    except (KeyError, TypeError, ValueError, RuntimeError):
        return None
    _retag(rig)
    if keyframe_at is not None:
        try:
            bone.keyframe_insert('["%s"]' % prop, frame=int(keyframe_at))
        except (RuntimeError, TypeError):  # pragma: no cover - no action yet
            pass
    entry["ik_fk"] = value
    entry["mode"] = _mode_of(value)
    return value


def set_pole_vector(rig, entry, enabled):
    """Switch one limb's explicit pole target on or off. ``None`` if it has none."""
    bone = rig.pose.bones.get(entry["switch_bone"])
    if bone is None or POLE_PROP not in bone.keys():
        return None
    try:
        bone[POLE_PROP] = bool(enabled)
    except (KeyError, TypeError, ValueError, RuntimeError):  # pragma: no cover
        return None
    _retag(rig)
    entry["pole_enabled"] = bool(enabled)
    return bool(enabled)


def apply_ik_convention(rig, legs=None, arms=None, poles=True, keyframe_at=None,
                        limbs=None):
    """Put the rig on the game convention: legs IK, arms FK, poles live.

    Every argument is a deliberate default rather than a rule: ``legs`` and
    ``arms`` take ``"ik"``/``"fk"``, ``poles`` takes ``False`` for rigs whose
    animator prefers Rigify's roll-driven knee.  Nothing here touches a deform
    bone, so a rig that has already been skinned and checked stays valid.
    """
    wanted = {"leg": legs or IK_CONVENTION["leg"], "arm": arms or IK_CONVENTION["arm"]}
    found = ik_limbs(rig, limbs)
    changed = []
    poled = []
    for entry in found:
        mode = wanted.get(entry["limb"], entry["default_mode"])
        before = entry["mode"]
        if set_limb_mode(rig, entry, mode, keyframe_at=keyframe_at) is not None \
                and before != entry["mode"]:
            changed.append("%s %s -> %s" % (entry["name"], before, entry["mode"]))
        if poles is not None and entry["pole_target"]:
            if set_pole_vector(rig, entry, poles) is not None and poles:
                poled.append(entry["pole_target"])
    return {
        "convention": dict(wanted),
        "poles": sorted(poled),
        "changed": changed,
        "limbs": found,
    }


#: Which way each limb's joint (and therefore its pole target) must sit relative
#: to the direction the rig faces.  The same anatomy
#: :data:`~.rigforge_landmarks.PREBEND_DIRECTION` states for the landmarks; it
#: is repeated here against the *generated* rig because the metarig and the rig
#: are two different objects and only one of them is what an animator poses.
POLE_DIRECTION = {"leg": 1.0, "front_leg": 1.0, "arm": -1.0}

#: The chain a pole is judged against: (root deform tag, mid deform tag, end
#: deform tag), ``%s`` the side.
POLE_CHAIN = {
    "leg": ("thigh.%s", "shin.%s", "foot.%s"),
    "front_leg": ("front_thigh.%s", "front_shin.%s", "front_foot.%s"),
    "arm": ("upper_arm.%s", "forearm.%s", "hand.%s"),
}


def _rest_head(rig, bone_name):
    bone = rig.data.bones.get(bone_name)
    return None if bone is None else rig.matrix_world @ bone.head_local


def _rest_tail(rig, bone_name):
    bone = rig.data.bones.get(bone_name)
    return None if bone is None else rig.matrix_world @ bone.tail_local


def rig_forward_axis(rig, limbs=None):
    """Which way this rig faces, taken from **its own toes**.

    Ankle to toe tip, flattened onto the ground plane, averaged over the legs —
    the same rule ``rigforge_walk`` uses to decide which way a character walks
    (:func:`forge.tools.rigforge_anim.locomotion_frame`), so a gate built on this
    and the walk it protects cannot disagree about which way is forward.  It is
    deliberately *not* ``detect_orientation``: that measures the mesh's
    lopsidedness and it can be wrong about a digitigrade, tailed, snouted
    character (measured on the werewolf: it reads ``+Y`` while the rig's own feet
    run heel ``+80 mm`` to toe tip ``-87 mm``, which is ``-Y``).  A rig's feet
    are not a statistic.

    Returns ``(vector, how)``; ``how`` is ``"toes"`` or ``"convention"``.
    """
    limbs = limbs if limbs is not None else ik_limbs(rig)
    total = Vector((0.0, 0.0, 0.0))
    used = 0
    for entry in limbs:
        if entry["limb"] not in ("leg", "front_leg"):
            continue
        side = entry["side"]
        toe_tag = "toe.%s" % side if entry["limb"] == "leg" else "front_toe.%s" % side
        foot_tag = "foot.%s" % side if entry["limb"] == "leg" else "front_foot.%s" % side
        feet = def_bones_for(rig, foot_tag)
        toes = def_bones_for(rig, toe_tag)
        ankle = _rest_head(rig, feet[0]) if feet else None
        tip = _rest_tail(rig, toes[-1]) if toes else (
            _rest_tail(rig, feet[-1]) if feet else None)
        if ankle is None or tip is None:
            continue
        direction = Vector((tip.x - ankle.x, tip.y - ankle.y, 0.0))
        if direction.length > 1e-6:
            total += direction.normalized()
            used += 1
    if used and total.length > 1e-6:
        return total.normalized(), "toes"
    return Vector((0.0, -1.0, 0.0)), "convention"


def pole_side_check(rig, limbs=None, forward=None):
    """Does every IK pole sit on the side its limb is pre-bent towards?

    A pole target is not decoration and it is not free: Rigify derives a limb's
    pole angle from the limb's **rest plane**, so the pole lands wherever the
    rest pose put the joint.  Pre-bend the knee forward and the pole is in front
    of it; leave the knee sitting backwards and the pole goes behind it and then
    *holds it there* through every solve.  That is a pole fighting the anatomy,
    and it is invisible until an animator lifts a foot.

    There is nothing to fix on this side of the generate: moving the generated
    pole bone would leave the IK constraint's ``pole_angle`` pointing at where
    the bone used to be, which is worse than the fault.  The fix is upstream —
    pre-bend the metarig and generate again — so a wrong pole is reported as a
    **fail with that sentence in it**, never quietly.
    """
    limbs = limbs if limbs is not None else ik_limbs(rig)
    if forward is None:
        forward, forward_how = rig_forward_axis(rig, limbs)
    else:
        forward, forward_how = Vector(forward).normalized(), "given"
    rows = []
    for entry in limbs:
        pole = entry.get("pole_target")
        chain = POLE_CHAIN.get(entry["limb"])
        if not pole or chain is None:
            continue
        side = entry["side"]
        root_bones = def_bones_for(rig, chain[0] % side)
        mid_bones = def_bones_for(rig, chain[1] % side)
        end_bones = def_bones_for(rig, chain[2] % side)
        if not root_bones or not mid_bones:
            continue
        root = _rest_head(rig, root_bones[0])
        joint = _rest_head(rig, mid_bones[0])
        end = (_rest_head(rig, end_bones[0]) if end_bones
               else _rest_tail(rig, mid_bones[-1]))
        pole_point = _rest_head(rig, pole)
        if root is None or joint is None or end is None or pole_point is None:
            continue
        span = end - root
        if span.length < 1e-9:
            continue
        axis = span.normalized()
        want = forward * POLE_DIRECTION.get(entry["limb"], 1.0)
        want = want - axis * want.dot(axis)
        if want.length < 1e-9:
            continue
        want.normalize()
        joint_off = (joint - root) - axis * (joint - root).dot(axis)
        pole_off = (pole_point - root) - axis * (pole_point - root).dot(axis)
        joint_side = joint_off.dot(want)
        pole_side = pole_off.dot(want)
        agrees = (pole_side > 0.0) and (joint_side > 0.0)
        rows.append({
            "limb": entry["name"],
            "pole": pole,
            "pole_enabled": entry.get("pole_enabled"),
            "expected": "forward" if POLE_DIRECTION.get(entry["limb"], 1.0) > 0
            else "backward",
            "joint_offset_mm": round(joint_side * M_TO_MM, 2),
            "pole_offset_mm": round(pole_side * M_TO_MM, 2),
            "span_mm": round(span.length * M_TO_MM, 2),
            "joint_pct_of_span": round(100.0 * joint_side / span.length, 2),
            "correct": bool(agrees),
        })
    wrong = [row for row in rows if not row["correct"]]
    if not rows:
        verdict = "unmeasured"
        says = "This rig has no IK pole target to check."
    elif not wrong:
        verdict = "ok"
        worst = min(rows, key=lambda row: row["pole_offset_mm"])
        says = ("Every one of the %d IK pole targets sits on the side its limb bends "
                "towards (closest: %s, %.1f mm %s of the chain, with its joint %.1f mm "
                "the same way)."
                % (len(rows), worst["limb"], worst["pole_offset_mm"], worst["expected"],
                   worst["joint_offset_mm"]))
    else:
        verdict = "fail"
        worst = min(wrong, key=lambda row: row["pole_offset_mm"])
        says = ("IK POLES FIGHT THE ANATOMY: %d of %d. %s's pole %r sits %.1f mm on the "
                "%s side of the chain when it must be %s, and its joint sits %.1f mm "
                "(%.1f%% of the limb's span) the same wrong way. Rigify derives the pole "
                "angle from the limb's REST plane, so this is not fixable on the "
                "generated rig — moving the pole bone would leave the IK constraint's "
                "pole_angle aimed where the bone used to be. Pre-bend the metarig (the "
                "landmark pass does it: a knee apexes forward, an elbow backward) and "
                "generate again."
                % (len(wrong), len(rows), worst["limb"], worst["pole"],
                   abs(worst["pole_offset_mm"]),
                   "wrong" if worst["pole_offset_mm"] < 0 else "right",
                   worst["expected"], worst["joint_offset_mm"],
                   worst["joint_pct_of_span"]))
    return {
        "forward": [round(v, 4) for v in forward],
        "forward_from": forward_how,
        "poles": rows,
        "wrong": [row["limb"] for row in wrong],
        "verdict": verdict,
        "says": says,
    }


def ik_summary(limbs):
    """One sentence about what the IK layer is, for a report's ``says``."""
    if not limbs:
        return ("No IK limb was found on this rig: nothing here has a foot or hand IK "
                "target, so every limb can only be animated in FK.")
    by_mode = {}
    for entry in limbs:
        by_mode.setdefault(entry["mode"], []).append(entry["name"])
    parts = ["%s on %s" % (mode.upper(), ", ".join(sorted(names)))
             for mode, names in sorted(by_mode.items())]
    rolls = sum(len(entry["roll_pivots"]) for entry in limbs)
    poles = [entry["name"] for entry in limbs if entry.get("pole_enabled")]
    text = "%d IK limb(s): %s." % (len(limbs), "; ".join(parts))
    if poles:
        text += " Pole targets live on %s." % ", ".join(sorted(poles))
    if rolls:
        text += " %d foot-roll pivot(s) available." % rolls
    return text


@command("rigforge_ik")
def cmd_rigforge_ik(params):
    """Report or set the rig's FK/IK layer, per limb.

    ``rigforge_ik {"rig"?, "action"?: "report"|"set", "legs"?: "ik"|"fk",
    "arms"?: "ik"|"fk", "limbs"?: [names], "mode"?: "ik"|"fk", "poles"?: bool,
    "frame"?: int}``

    ``report`` (the default) changes nothing.  ``set`` moves the blend and, with
    ``frame``, keyframes it — which is what makes a clip that switches mid-shot
    survive the export bake.
    """
    started = time.monotonic()
    warnings = []
    rig = _rig_from_params(params)

    action = get_choice(params, "action", {"REPORT": "report", "SET": "set"}, "report")
    wanted_limbs = params.get("limbs")
    if isinstance(wanted_limbs, str):
        wanted_limbs = [wanted_limbs]
    if wanted_limbs is not None and not isinstance(wanted_limbs, (list, tuple)):
        raise ForgeError("'limbs' must be a list of limb names like [\"leg.L\", \"arm\"].")

    modes = {"IK": IK_MODE_IK, "FK": IK_MODE_FK}
    mode = get_choice(params, "mode", modes, None) if params.get("mode") else None
    legs = get_choice(params, "legs", modes, None) if params.get("legs") else None
    arms = get_choice(params, "arms", modes, None) if params.get("arms") else None
    poles = get_bool(params, "poles", True) if params.get("poles") is not None else None
    frame = get_int(params, "frame", None, minimum=-1_000_000, maximum=1_000_000) \
        if params.get("frame") is not None else None

    limbs = ik_limbs(rig, wanted_limbs)
    if wanted_limbs and not limbs:
        raise ForgeError(
            "No limb on %r matches %s. This rig's IK limbs are: %s."
            % (rig.name, ", ".join(repr(str(n)) for n in wanted_limbs),
               ", ".join(entry["name"] for entry in ik_limbs(rig)) or "none"))
    changed = []
    convention = None
    if action == "set":
        if mode is not None:
            with object_mode():
                for entry in limbs:
                    before = entry["mode"]
                    if set_limb_mode(rig, entry, mode, keyframe_at=frame) is not None \
                            and before != entry["mode"]:
                        changed.append("%s %s -> %s" % (entry["name"], before,
                                                        entry["mode"]))
                    if poles is not None and entry["pole_target"]:
                        set_pole_vector(rig, entry, poles)
            convention = {"all": mode}
        else:
            with object_mode():
                report = apply_ik_convention(
                    rig, legs=legs, arms=arms,
                    poles=True if poles is None else poles,
                    keyframe_at=frame, limbs=wanted_limbs)
            changed = report["changed"]
            convention = report["convention"]
            limbs = report["limbs"]
        refresh_view_layer()
        limbs = ik_limbs(rig, wanted_limbs)
    if not limbs:
        warnings.append(
            "No IK limb was found on %r. Rigify's biped generates foot_ik/hand_ik and "
            "an IK_FK switch on thigh_parent/upper_arm_parent; a rig without them was "
            "not generated by rigforge_generate_rig, or is not a biped." % rig.name)

    # Whoever is asking about the IK layer is the person who most needs to know
    # a pole is on the wrong side of its limb.
    pole_sides = pole_side_check(rig, limbs)
    if pole_sides["verdict"] == "fail":
        warnings.append(pole_sides["says"])

    return {
        "rig": rig.name,
        "action": action,
        "limbs": limbs,
        "limb_names": [entry["name"] for entry in limbs],
        "convention": convention,
        "changed": changed,
        "frame": frame,
        "pole_sides": pole_sides,
        "says": "%s %s" % (ik_summary(limbs), pole_sides["says"]),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


def _rig_from_params(params, key="rig"):
    """The armature a rig-level command is about: named, active, or the only one."""
    name = params.get(key)
    if isinstance(name, str) and name.strip():
        rig = find_object(name.strip())
        if rig.type != "ARMATURE":
            raise ForgeError("Object %r is a %s, not an armature." % (rig.name, rig.type))
        return rig
    active = get_view_layer().objects.active
    if active is not None:
        if active.type == "ARMATURE":
            return active
        for modifier in active.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is not None:
                return modifier.object
        stored = str(_prop(active, PROP_RIG, "") or "")
        if stored and bpy.data.objects.get(stored) is not None:
            return bpy.data.objects[stored]
    candidates = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if len(candidates) == 1:
        return candidates[0]
    raise ForgeError(
        "No rig given and none could be guessed (%d armature(s) in the file). Pass "
        "'rig', or select the character." % len(candidates))


# ---------------------------------------------------------------------------
# rigforge_generate_rig
# ---------------------------------------------------------------------------

def _default_mesh_for(metarig, params):
    name = params.get("mesh")
    if isinstance(name, str) and name.strip():
        return find_object(name.strip(), mesh_only=True)
    stored = str(_prop(metarig, PROP_RIG_MESH, "") or "")
    if stored:
        for candidate in ("%s_retopo" % stored, stored):
            obj = bpy.data.objects.get(candidate)
            if obj is not None and obj.type == "MESH":
                return obj
    active = get_view_layer().objects.active
    if active is not None and active.type == "MESH":
        return active
    raise ForgeError(
        "No mesh to skin: pass 'mesh', or run rigforge_metarig on the sculpt first "
        "so the metarig remembers which mesh it belongs to.")


@command("rigforge_generate_rig")
def cmd_rigforge_generate_rig(params):
    """Stage 4b: Rigify generate, automatic weights, per-tag cleanup, tag contract.

    The last stage is :func:`forge.tools.rigforge_skin.constrain_weights` — the
    rigger's mask-then-blend over the bind bone heat produced, because bone heat
    diffuses across the armpit of a clothed figure whose arms hang down and puts
    the arm's weight on the chest.  ``tag_constrained: false`` reproduces the
    old, unconstrained bind.
    """
    started = time.monotonic()
    warnings = []
    ensure_rigify()

    name = params.get("metarig")
    if isinstance(name, str) and name.strip():
        metarig = find_object(name.strip())
    else:
        metarig = None
        active = get_view_layer().objects.active
        if active is not None and active.type == "ARMATURE" and \
                _prop(active, PROP_TAG_BONES, ""):
            metarig = active
        else:
            candidates = [obj for obj in bpy.data.objects
                          if obj.type == "ARMATURE" and _prop(obj, PROP_TAG_BONES, "")]
            if len(candidates) == 1:
                metarig = candidates[0]
        if metarig is None:
            raise ForgeError(
                "No metarig given and none could be guessed. Pass 'metarig', or run "
                "rigforge_metarig first.")
    if metarig.type != "ARMATURE":
        raise ForgeError("Object %r is a %s, not the metarig armature."
                         % (metarig.name, metarig.type))
    if not len(metarig.data.bones):
        raise ForgeError("Metarig %r has no bones." % metarig.name)

    mesh = _default_mesh_for(metarig, params)
    do_parent = get_bool(params, "parent_with_weights", True)
    do_cleanup = get_bool(params, "cleanup", True)
    # The tag contract. On by default because the defect it fixes was measured
    # on a live character and bone heat produces it on every clothed figure
    # whose arms hang down; off is for reproducing the old bind on purpose.
    do_constrain = get_bool(params, "tag_constrained", True)
    max_influences = get_int(params, "max_influences", 4, minimum=1, maximum=12)
    band_ratio = get_float(params, "band", 0.06, minimum=0.0, maximum=1.0)
    do_springs = get_bool(params, "spring_chains", True)

    with object_mode():
        refresh_view_layer()
        with active_only(metarig):
            try:
                status = bpy.ops.pose.rigify_generate()
            except RuntimeError as exc:
                raise ForgeError(_rigify_error(str(exc), metarig))
            if "FINISHED" not in status:
                raise ForgeError(
                    "Rigify's generate returned %s on %r. Check the metarig's bone "
                    "types in the Bone properties tab."
                    % (", ".join(sorted(status)) or "nothing", metarig.name))
        rig = getattr(metarig.data, "rigify_target_rig", None)
        if rig is None:
            raise ForgeError(
                "Rigify reported success but left no target rig on %r." % metarig.name)
        refresh_view_layer()

        chains = _stored_json(metarig, PROP_CHAINS, [])
        springs = []
        if do_springs and chains:
            springs = add_spring_chains(rig, chains, warnings)

        # The IK layer Rigify already generated, put on the game convention and
        # reported. Control properties only - not one deform bone moves, which
        # is why this is safe to do to a rig that is about to be skinned.
        ik = apply_ik_convention(
            rig,
            legs=get_choice(params, "ik_legs", {"IK": "ik", "FK": "fk"}, None)
            if params.get("ik_legs") else None,
            arms=get_choice(params, "ik_arms", {"IK": "ik", "FK": "fk"}, None)
            if params.get("ik_arms") else None,
            poles=get_bool(params, "ik_poles", True))
        if not ik["limbs"]:
            warnings.append(
                "This rig generated no IK limb (no foot_ik/hand_ik with an IK_FK "
                "switch), so every limb can only be animated in FK. A pure-FK leg is "
                "the foot-slide anti-pattern - check the metarig's limb types.")
        refresh_view_layer()

        # Straight after the convention, while the rig is still this function's
        # business: a pole on the wrong side of its limb is a rig that bends
        # backwards, and it is generated silently from a rest plane nobody
        # looked at. Never fatal - the rig is still a rig, and refusing to
        # deliver it would throw away the skinning too - but it is a fail in
        # the report and a warning in the sentence.
        pole_sides = pole_side_check(rig, ik["limbs"])
        if pole_sides["verdict"] == "fail":
            warnings.append(pole_sides["says"])

        regions, _empty = measure_tags(mesh)
        span = max(max(mesh.dimensions), 1e-6)
        band = band_ratio * span

        weights = {"method": "skipped", "unweighted_after": None}
        cleanup = None
        limits = None
        constrained = None
        if do_parent:
            for group in list(mesh.vertex_groups):
                if group.name.startswith(DEF_PREFIX):
                    mesh.vertex_groups.remove(group)
            weights = parent_with_weights(mesh, rig, warnings)
            if do_cleanup:
                bones_by_tag = tag_bone_map(rig, metarig, regions, band=band)
                cleanup = cleanup_weights(mesh, rig, regions, bones_by_tag, band)
                cleanup["bones_by_tag"] = {tag: sorted(names)
                                           for tag, names in sorted(bones_by_tag.items())}
                limits = limit_and_normalize(mesh, rig, max_influences)
                cleanup.update(limits)
                left = _unweighted_vertices(mesh, set(deform_bones(rig)))
                if left:
                    warnings.append(
                        "Cleanup left %d vertex/vertices of %r with no weight; they were "
                        "re-filled from the nearest bones." % (len(left), mesh.name))
                    distance_weights(mesh, rig, vertices=left, influences=max_influences)
                    limit_and_normalize(mesh, rig, max_influences)
            # Last, and on top of whatever the bind and the cleanup produced:
            # the tag contract. `cleanup_weights` above is a *bounding box*
            # rule and catches the gross case; this is the tag's own per-vertex
            # membership, which is the thing that knows a rib from an arm.
            if do_constrain and regions:
                from . import rigforge_skin

                try:
                    constrained = rigforge_skin.constrain_weights(
                        mesh, rig, metarig, regions, max_influences=max_influences,
                        warnings=warnings)
                except ForgeError as exc:
                    constrained = {"skipped": True, "says": str(exc)}
                    warnings.append(
                        "Tag-constrained skinning did not run (%s), so the weights are "
                        "bone heat's as-is: check the chest for arm influence before "
                        "animating." % exc)

        _set_prop(rig, PROP_RIG_MESH, mesh.name)
        _set_prop(rig, PROP_TAG_BONES, _prop(metarig, PROP_TAG_BONES, ""))
        _set_prop(metarig, PROP_RIG, rig.name)
        _set_prop(mesh, PROP_RIG, rig.name)
        _set_prop(mesh, PROP_METARIG, metarig.name)
        refresh_view_layer()

    deform = deform_bones(rig)
    return {
        "rig": rig.name,
        "metarig": metarig.name,
        "mesh": mesh.name,
        "weighted": bool(do_parent),
        "weights": weights,
        "cleanup_report": cleanup or {"tags": [], "weights_zeroed": 0,
                                      "skipped": not do_cleanup},
        "tag_constrained": constrained or {"skipped": True,
                                           "says": "tag_constrained was off"
                                           if not do_constrain else
                                           "nothing was skinned, so there was "
                                           "nothing to constrain"},
        "spring_chains": springs,
        "deform_bones": len(deform),
        "bone_count": len(rig.data.bones),
        "control_bones": len(rig.data.bones) - len(deform),
        "ik": {
            "convention": ik["convention"],
            "limbs": ik["limbs"],
            "limb_names": [entry["name"] for entry in ik["limbs"]],
            "poles": ik["poles"],
            "pole_sides": pole_sides,
            "changed": ik["changed"],
            "says": "%s %s" % (ik_summary(ik["limbs"]), pole_sides["says"]),
        },
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


def _rigify_error(message, metarig):
    """Rigify names the offending bone in its exception; keep that in the error."""
    bone = ""
    match = re.search(r"bone '([^']+)'", message, re.IGNORECASE)
    if match:
        bone = match.group(1)
    text = "Rigify could not generate a rig from %r: %s" % (metarig.name, message.strip())
    if bone:
        text += (" The bone it names is %r - check its Rigify type and its parent in "
                 "the metarig." % bone)
    return text


# ---------------------------------------------------------------------------
# rigforge_weights
# ---------------------------------------------------------------------------

def _rig_for_mesh(obj, params):
    name = params.get("rig")
    if isinstance(name, str) and name.strip():
        rig = find_object(name.strip())
        if rig.type != "ARMATURE":
            raise ForgeError("Object %r is a %s, not an armature." % (rig.name, rig.type))
        return rig
    for modifier in obj.modifiers:
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    stored = str(_prop(obj, PROP_RIG, "") or "")
    if stored:
        rig = bpy.data.objects.get(stored)
        if rig is not None and rig.type == "ARMATURE":
            return rig
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    raise ForgeError(
        "Mesh %r is not bound to an armature; pass 'rig', or run "
        "rigforge_generate_rig first." % obj.name)


@command("rigforge_weights")
def cmd_rigforge_weights(params):
    """Report, clean up, or just limit-and-normalize a skinned mesh's weights."""
    obj = resolve_object(params, mesh_only=True)
    rig = _rig_for_mesh(obj, params)
    action = get_choice(
        params, "action",
        {"REPORT": "report", "CLEANUP": "cleanup", "NORMALIZE": "normalize"},
        "report",
    )
    max_influences = get_int(params, "max_influences", 4, minimum=1, maximum=12)
    band_ratio = get_float(params, "band", 0.06, minimum=0.0, maximum=1.0)
    warnings = []

    if action == "report":
        return {"object": obj.name, "rig": rig.name, "action": action,
                "report": weight_report(obj, rig), "changed": 0, "warnings": warnings}

    with object_mode():
        if action == "normalize":
            result = limit_and_normalize(obj, rig, max_influences)
            changed = result["limited"] + result["normalized"]
        else:
            metarig = None
            stored = str(_prop(obj, PROP_METARIG, "") or "")
            if stored:
                metarig = bpy.data.objects.get(stored)
            regions, _empty = measure_tags(obj)
            if not regions:
                raise ForgeError(
                    "Cleanup needs tags: %r has no tagged geometry, so there are no "
                    "regions to keep weights inside of." % obj.name)
            band = band_ratio * max(max(obj.dimensions), 1e-6)
            bones_by_tag = tag_bone_map(rig, metarig, regions, band=band)
            result = cleanup_weights(obj, rig, regions, bones_by_tag, band)
            result.update(limit_and_normalize(obj, rig, max_influences))
            left = _unweighted_vertices(obj, set(deform_bones(rig)))
            if left:
                distance_weights(obj, rig, vertices=left, influences=max_influences)
                limit_and_normalize(obj, rig, max_influences)
                warnings.append("%d vertex/vertices lost every weight to the cleanup "
                                "rules and were re-filled from the nearest bones."
                                % len(left))
            changed = result["weights_zeroed"] + result["limited"] + result["normalized"]

    return {
        "object": obj.name,
        "rig": rig.name,
        "action": action,
        "changed": changed,
        "cleanup_report": result,
        "report": weight_report(obj, rig),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def action_fcurves(action):
    """Every F-curve of an action, on both the legacy and the slotted API.

    Blender 5.0 moved F-curves into layers/strips/channelbags per action *slot*;
    ``action.fcurves`` no longer exists there.
    """
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        return list(curves)
    out = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for slot in getattr(action, "slots", ()):
                try:
                    bag = strip.channelbag(slot)
                except (AttributeError, TypeError, RuntimeError):
                    bag = None
                if bag is not None:
                    out.extend(bag.fcurves)
    return out


def action_bones(action):
    """Names of the pose bones an action animates."""
    names = set()
    for curve in action_fcurves(action):
        path = getattr(curve, "data_path", "") or ""
        if path.startswith('pose.bones["'):
            try:
                names.add(path.split('"')[1])
            except IndexError:
                continue
    return names


def assign_action(obj, action):
    """Assign an action, picking a slot when the Blender build wants one."""
    if obj.animation_data is None:
        obj.animation_data_create()
    data = obj.animation_data
    data.action = action
    if action is None or not hasattr(data, "action_slot"):
        return
    if data.action_slot is not None:
        return
    slots = list(getattr(action, "slots", ()))
    suitable = list(getattr(data, "action_suitable_slots", ())) or slots
    for slot in suitable:
        try:
            data.action_slot = slot
            return
        except (TypeError, ValueError, RuntimeError):
            continue
    if not slots:
        try:
            slot = action.slots.new(id_type="OBJECT", name=obj.name)
            data.action_slot = slot
        except (AttributeError, TypeError, RuntimeError):
            pass


def rig_actions(rig, requested):
    """The actions to bake: named ones, or everything that fits this rig."""
    bones = {bone.name for bone in rig.pose.bones}
    if isinstance(requested, (list, tuple)):
        out = []
        missing = []
        for raw in requested:
            action = bpy.data.actions.get(str(raw))
            if action is None:
                missing.append(str(raw))
            else:
                out.append(action)
        if missing:
            raise ForgeError("No action(s) named %s in this file. Actions here: %s."
                             % (", ".join(repr(n) for n in missing),
                                ", ".join(sorted(a.name for a in bpy.data.actions))
                                or "none"))
        return out, []
    out = []
    skipped = []
    for action in bpy.data.actions:
        animated = action_bones(action)
        if not animated:
            skipped.append(action.name)
            continue
        if animated & bones:
            out.append(action)
        else:
            skipped.append(action.name)
    out.sort(key=lambda action: action.name.lower())
    return out, skipped


# ---------------------------------------------------------------------------
# export: the deform rig
# ---------------------------------------------------------------------------

def _apply_object_transform(obj, warnings, location=False):
    """Bake an export copy's rotation and scale into its data.

    Godot reads a glTF node's transform verbatim; a character that arrives
    rotated 90 degrees or at scale 0.01 because that is how it sat in the .blend
    is the classic first-import surprise.
    """
    refresh_view_layer()
    with active_only(obj):
        try:
            bpy.ops.object.transform_apply(location=location, rotation=True, scale=True)
        except RuntimeError as exc:
            warnings.append("Could not apply transforms on %r: %s" % (obj.name, exc))
            return False
    return True


def _duplicate(obj, name, collection):
    copy = obj.copy()
    copy.data = obj.data.copy() if obj.data is not None else None
    copy.name = name
    copy.animation_data_clear()
    collection.objects.link(copy)
    return copy


def _rename_export_object(copy, wanted, renamed, source=None):
    """Name an export copy (and its data) the way Godot should see it.

    Object names are unique per file, so a copy can never take its source's
    name while the source still holds it: the source is renamed out of the way
    and restored by the caller's ``finally``.  The *data* name matters too — the
    glTF exporter names its meshes after the datablock, not the object.
    """
    for block in (source, getattr(source, "data", None)):
        if block is None:
            continue
        try:
            if block.name == wanted:
                renamed.append((block, block.name))
                block.name = wanted + "_forge_src"
        except (AttributeError, RuntimeError):
            continue
    copy.name = wanted
    if copy.data is not None:
        try:
            copy.data.name = copy.name
        except (AttributeError, RuntimeError):
            pass
    return copy.name


def clean_def_parent(rig, bone_name, keep):
    """The nearest ancestor of a DEF bone that survives the strip.

    Rigify parents deform bones to ``ORG-``/``MCH-`` bones, so simply deleting
    the control rig would leave every limb an orphan.  Walking the original
    hierarchy and mapping each ancestor back to its ``DEF-`` counterpart
    reconstructs the anatomical parenting the metarig described.
    """
    bone = rig.data.bones.get(bone_name)
    if bone is None:
        return None
    current = bone.parent
    while current is not None:
        name = current.name
        if name in keep and name != bone_name:
            return name
        for prefix in CONTROL_PREFIXES:
            if name.startswith(prefix):
                candidate = DEF_PREFIX + name[len(prefix):]
                if candidate in keep and candidate != bone_name:
                    return candidate
                break
        else:
            candidate = DEF_PREFIX + name
            if candidate in keep and candidate != bone_name:
                return candidate
        current = current.parent
    return None


def build_deform_rig(rig, name, collection, warnings, want_root=True):
    """A deform-only copy of a Rigify rig, hierarchy intact. No add-on required.

    This is the job the plan gave to Game Rig Tools.  It is four steps: copy the
    armature, work out where each ``DEF-`` bone's parent went, delete everything
    that is not a deform bone (plus a root), and re-apply the parenting.
    """
    keep = {bone.name for bone in rig.data.bones if bone.use_deform}
    if not keep:
        raise ForgeError("Rig %r has no deforming bones, so there is nothing to export."
                         % rig.name)
    parents = {bone_name: clean_def_parent(rig, bone_name, keep) for bone_name in keep}

    copy = _duplicate(rig, name, collection)
    copy.hide_viewport = False
    # Strip the rig logic *before* the bones it points at are deleted: a
    # constraint whose subtarget has just been removed makes the depsgraph
    # shout about relations it cannot build, on every evaluation, for the rest
    # of the export.
    for pose_bone in copy.pose.bones:
        for constraint in list(pose_bone.constraints):
            pose_bone.constraints.remove(constraint)
        try:
            pose_bone.custom_shape = None
        except (AttributeError, TypeError):
            pass
    refresh_view_layer()

    with active_only(copy):
        _enter_edit(copy)
        try:
            edit_bones = copy.data.edit_bones
            root = edit_bones.get(ROOT_BONE)
            if want_root and root is None:
                root = edit_bones.new(ROOT_BONE)
                size = max(max(rig.dimensions), 1.0) * 0.25
                root.head = Vector((0.0, 0.0, 0.0))
                root.tail = Vector((0.0, size, 0.0))
                root.use_deform = False
            keep_names = set(keep)
            if root is not None:
                keep_names.add(root.name)
            for bone in [b for b in edit_bones if b.name not in keep_names]:
                edit_bones.remove(bone)
            for bone_name in sorted(keep):
                bone = edit_bones.get(bone_name)
                if bone is None:
                    continue
                parent_name = parents.get(bone_name)
                parent = edit_bones.get(parent_name) if parent_name else None
                if parent is None:
                    parent = root
                if parent is not None and parent.name != bone.name:
                    bone.use_connect = False
                    bone.parent = parent
        finally:
            _leave_edit()

    # a deform rig carries no rig logic: no constraints, no drivers, no custom shapes
    try:
        copy.animation_data_clear()
    except AttributeError:
        pass
    if copy.data.animation_data is not None:
        copy.data.animation_data_clear()
    refresh_view_layer()
    return copy


def bake_action_onto(source, target, action, frame_start, frame_end, step=1):
    """Bake one action from the control rig onto the deform rig, visually.

    Copy Transforms on every deform bone plus ``nla.bake(visual_keying=True)``
    is the whole trick: whatever the control rig's IK, drivers and secondary
    motion resolve to on a frame is what lands on the deform bone for that
    frame, so the exported clip needs none of that machinery.
    """
    with active_only(target):
        try:
            bpy.ops.object.mode_set(mode="POSE")
        except RuntimeError as exc:
            raise ForgeError("Could not enter Pose Mode on %r to bake: %s"
                             % (target.name, exc))
        try:
            if target.animation_data is not None:
                target.animation_data.action = None
            baked = bpy.ops.nla.bake(
                **op_kwargs(bpy.ops.nla.bake, {
                    "frame_start": int(frame_start),
                    "frame_end": int(frame_end),
                    "step": max(1, int(step)),
                    "only_selected": False,
                    "visual_keying": True,
                    "clear_constraints": False,
                    "clear_parents": False,
                    "use_current_action": False,
                    "bake_types": {"POSE"},
                    "clean_curves": False,
                }))
        finally:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
    if "FINISHED" not in baked:
        raise ForgeError("Baking %r onto %r returned %s."
                         % (action.name, target.name,
                            ", ".join(sorted(baked)) or "nothing"))
    result = target.animation_data.action if target.animation_data else None
    if result is None:
        raise ForgeError("Baking %r onto %r produced no action." % (action.name, target.name))
    return result


# ---------------------------------------------------------------------------
# shape-key weights: the half of a corrective that glTF will not carry by itself
# ---------------------------------------------------------------------------
#
# A corrective shape key is two things: a morph target and a driver that says
# how much of it to use.  **glTF has the first and not the second.**  Morph
# targets are a first-class part of the format; drivers are a Blender concept
# with no glTF counterpart at all, so a rig whose knees are fixed by a
# bend-angle driver exports with the fix present and never applied — the model
# arrives in Godot with a ``corr_knee_L_090`` morph sitting at weight 0 for
# ever, and nothing errors.
#
# The fix is the same one the bone bake uses: **sample it**.  For every frame of
# every exported clip, the driver is evaluated (through the depsgraph, on the
# source mesh, while the source rig plays that action) and the resulting weight
# is written as an ordinary keyframe on the export copy's key block.  Those
# keyframes go into an action pushed onto an NLA track **named after the clip**,
# which is what makes Khronos' exporter merge them into the same glTF animation
# as the armature's track rather than emitting a second, nameless one.
#
# Khronos' exporter does have a driver path of its own (``get_sk_drivers``): in
# ``NLA_TRACKS`` mode it will find drivers on an exported armature's child
# meshes and sample them.  It is deliberately **not** relied on here, and the
# copy's inherited drivers are removed before export, because that path samples
# whatever the *original* control rig happens to be posed at — and by export
# time the original holds one arbitrary action for every clip being written, so
# all of them would receive the same weight curve.  Sampling per action, against
# the action, is the only version that is right for more than one clip.

def driven_shape_keys(mesh_obj):
    """Names of the key blocks on a mesh whose value is driven rather than set."""
    keys = getattr(getattr(mesh_obj, "data", None), "shape_keys", None)
    if keys is None or keys.animation_data is None:
        return []
    out = []
    for fcurve in keys.animation_data.drivers:
        path = fcurve.data_path or ""
        if '"' in path and path.endswith(".value"):
            name = path.split('"')[1]
            if name in keys.key_blocks:
                out.append(name)
    return sorted(set(out))


def sample_shape_key_values(mesh_obj, names, frames):
    """``{key name: [value per frame]}`` — the drivers evaluated, not guessed.

    Read off the **depsgraph-evaluated** mesh: a driver writes its result into
    the evaluated copy and never into the datablock, so reading
    ``key_blocks[name].value`` on the original returns whatever it was authored
    at (0) at every frame.  That is the silent-zero bug this function exists to
    avoid.
    """
    scene = get_scene()
    samples = {name: [] for name in names}
    for frame in frames:
        scene.frame_set(int(frame))
        refresh_view_layer()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = mesh_obj.evaluated_get(depsgraph)
        blocks = getattr(getattr(evaluated.data, "shape_keys", None), "key_blocks", None)
        for name in names:
            block = blocks.get(name) if blocks is not None else None
            samples[name].append(float(block.value) if block is not None else 0.0)
    return samples


def bake_shape_key_values(export_mesh, samples, frames, clip_name, warnings):
    """Key the sampled weights onto the export copy and stash them in an NLA track.

    Returns ``(names actually keyed, the action holding them)``.  The copy's
    inherited drivers are removed first: keyframes and a driver on the same
    property is a fight the driver wins, so leaving them would make the bake a
    no-op.
    """
    keys = getattr(export_mesh.data, "shape_keys", None)
    if keys is None or not samples:
        return [], None
    if keys.animation_data is None:
        keys.animation_data_create()
    for fcurve in list(keys.animation_data.drivers):
        try:
            keys.animation_data.drivers.remove(fcurve)
        except (RuntimeError, TypeError):  # pragma: no cover - defensive
            continue
    keys.animation_data.action = None

    keyed = []
    for index, frame in enumerate(frames):
        for name, values in samples.items():
            block = keys.key_blocks.get(name)
            if block is None:
                continue
            block.value = values[index]
            try:
                block.keyframe_insert(data_path="value", frame=int(frame))
            except (RuntimeError, TypeError) as exc:  # pragma: no cover - defensive
                warnings.append("Could not key shape key %r for %r: %s"
                                % (name, clip_name, exc))
                continue
            if name not in keyed:
                keyed.append(name)
    if not keyed:
        return [], None

    action = keys.animation_data.action
    if action is None:  # pragma: no cover - keyframe_insert always makes one
        return [], None
    # Sampled data wants sampled interpolation: a Bezier handle between two
    # samples of a ramp overshoots past 1, and a morph weight above 1 is a
    # corrective that inflates the limb it was meant to rescue.
    for curve in action_fcurves(action):
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"
        curve.update()
    action.name = "%s_forge_sk" % clip_name
    action.use_fake_user = True

    track = keys.animation_data.nla_tracks.new()
    # The name is the contract: Khronos' exporter merges animations whose NLA
    # tracks share a name, which is how the weights end up in the same glTF clip
    # as the bones instead of a second animation nobody plays.
    track.name = clip_name
    strip = track.strips.new(clip_name, int(frames[0]), action)
    strip.name = clip_name
    try:
        for slot in getattr(action, "slots", ()):
            strip.action_slot = slot
            break
    except (AttributeError, TypeError, RuntimeError):  # pragma: no cover
        pass
    keys.animation_data.action = None
    return keyed, action


def constrain_to(source, target):
    """Copy Transforms on every bone of ``target`` from the same-named bone of ``source``."""
    made = 0
    for pose_bone in target.pose.bones:
        for constraint in list(pose_bone.constraints):
            pose_bone.constraints.remove(constraint)
        if pose_bone.name not in source.pose.bones:
            continue
        constraint = pose_bone.constraints.new("COPY_TRANSFORMS")
        constraint.name = "Forge Bake"
        constraint.target = source
        constraint.subtarget = pose_bone.name
        made += 1
    return made


def strip_constraints(rig):
    for pose_bone in rig.pose.bones:
        for constraint in list(pose_bone.constraints):
            pose_bone.constraints.remove(constraint)


def apply_root_motion(rig, action, hip_bone, frames):
    """Move the horizontal travel of the hips onto the root bone.

    Godot's root-motion track wants the character's travel on a bone that the
    skeleton itself does not deform with.  Both passes work in object space and
    keyframe what they set, so the visual result is identical: the root carries
    the travel, the hips keep everything else.
    """
    scene = get_scene()
    root = rig.pose.bones.get(ROOT_BONE)
    hip = rig.pose.bones.get(hip_bone)
    if root is None or hip is None:
        return {"applied": False, "reason": "no %s bone" % (ROOT_BONE if root is None
                                                            else hip_bone)}
    assign_action(rig, action)
    sampled = {}
    for frame in frames:
        scene.frame_set(frame)
        sampled[frame] = hip.matrix.copy()

    root.rotation_mode = "QUATERNION"
    hip.rotation_mode = hip.rotation_mode if hip.rotation_mode != "AXIS_ANGLE" else "QUATERNION"
    moved = 0.0
    for frame in frames:
        scene.frame_set(frame)
        matrix = sampled[frame]
        offset = Vector((matrix.translation.x, matrix.translation.y, 0.0))
        moved = max(moved, offset.length)
        root.matrix = Matrix.Translation(offset) @ root.bone.matrix_local
        root.keyframe_insert("location", frame=frame)
        root.keyframe_insert("rotation_quaternion", frame=frame)
        refresh_view_layer()
        hip.matrix = Matrix.Translation(-offset) @ matrix
        hip.keyframe_insert("location", frame=frame)
        if hip.rotation_mode == "QUATERNION":
            hip.keyframe_insert("rotation_quaternion", frame=frame)
        else:
            hip.keyframe_insert("rotation_euler", frame=frame)
        hip.keyframe_insert("scale", frame=frame)
    return {"applied": True, "bone": ROOT_BONE, "from": hip_bone,
            "travel": round(moved, 6)}


# ---------------------------------------------------------------------------
# export: the Godot import helper
# ---------------------------------------------------------------------------

#: Godot 4.x ``EditorScenePostImport`` companion. ``{...}`` placeholders only —
#: GDScript is full of ``%`` format operators, so ``str.format`` keeps the two
#: languages' formatting from fighting.
GODOT_IMPORT_SCRIPT = '''@tool
extends EditorScenePostImport
# Written by Forge (RigForge stage 7) next to {basename}.
#
# Use it: select {basename} in Godot's FileSystem dock, open the Import tab, set
# "Import Script" to this file and press Reimport.
#
# What it does:
#   * every animation whose name ends in "{loop}" is set to loop, the rest are
#     explicitly set not to;
#   * it prints the animations it found, and warns about any this export was
#     supposed to contain but Godot did not see;
#   * meshes suffixed {collision} keep Godot's own collision-shape behaviour;
#     nothing here has to do that, the suffix does it.
#   * LOD_CHAIN (manual LOD exports only) writes visibility_range_begin/end onto
#     the sibling meshes, so the chain actually SWITCHES. Godot has no "-lodN"
#     import suffix - -col, -convcol, -occ, -navmesh and friends are the whole
#     list - so without these ranges every level renders at once, on top of
#     each other. It is empty on a default export, where the only mesh shipped
#     is LOD0 and Godot's own importer (meshoptimizer) generates the levels.

const LOOP_SUFFIX := "{loop}"
const EXPECTED_ACTIONS: Array[String] = [{actions}]
const EXPECTED_COLLISION: Array[String] = [{collision_meshes}]
const LOD_CHAIN: Array = [{lod_chain}]


func _post_import(scene: Node) -> Node:
	_apply_lod_chain(scene)
	var player := _find_player(scene)
	if player == null:
		if not EXPECTED_ACTIONS.is_empty():
			push_warning("Forge: no AnimationPlayer in {basename}")
		return scene
	var found: Array[String] = []
	for library_name in player.get_animation_library_list():
		var library := player.get_animation_library(library_name)
		for animation_name in library.get_animation_list():
			var animation := library.get_animation(animation_name)
			found.append(animation_name)
			if animation_name.ends_with(LOOP_SUFFIX):
				animation.loop_mode = Animation.LOOP_LINEAR
			else:
				animation.loop_mode = Animation.LOOP_NONE
	print("Forge: {basename} imported with %d animation(s): %s"
		% [found.size(), ", ".join(found)])
	for expected in EXPECTED_ACTIONS:
		if not found.has(expected):
			push_warning("Forge: expected animation '%s' is missing" % expected)
	return scene


func _apply_lod_chain(scene: Node) -> void:
	if LOD_CHAIN.is_empty():
		return
	for entry in LOD_CHAIN:
		var mesh := _find_mesh(scene, entry["mesh"])
		if mesh == null:
			push_warning("Forge: LOD mesh '%s' is not in {basename}" % entry["mesh"])
			continue
		mesh.visibility_range_begin = entry["begin"]
		mesh.visibility_range_end = entry["end"]
		mesh.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_DISABLED
	print("Forge: {basename} LOD chain wired over %d mesh(es)" % LOD_CHAIN.size())


func _find_mesh(node: Node, mesh_name: String) -> MeshInstance3D:
	if node is MeshInstance3D and node.name == mesh_name:
		return node
	for child in node.get_children():
		var found := _find_mesh(child, mesh_name)
		if found != null:
			return found
	return null


func _find_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node
	for child in node.get_children():
		var found := _find_player(child)
		if found != null:
			return found
	return null
'''


def _gd_string_list(names):
    return ", ".join('"%s"' % str(name).replace('\\', '\\\\').replace('"', '\\"')
                     for name in names)


def _gd_lod_chain(chain):
    """``[{"mesh": name, "begin": f, "end": f}, ...]`` as a GDScript literal."""
    parts = []
    for entry in chain or ():
        parts.append('{"mesh": "%s", "begin": %.4f, "end": %.4f}'
                     % (str(entry["mesh"]).replace('\\', '\\\\').replace('"', '\\"'),
                        float(entry["begin"]), float(entry["end"])))
    return ", ".join(parts)


def write_godot_import_script(path, actions, collision, lod_chain=()):
    """Emit the ``EditorScenePostImport`` companion next to the glTF."""
    target = os.path.splitext(path)[0] + "_import.gd"
    body = GODOT_IMPORT_SCRIPT.format(
        basename=os.path.basename(path),
        loop=LOOP_SUFFIX,
        collision="/".join(COLLISION_SUFFIXES[:2]),
        actions=_gd_string_list(actions),
        collision_meshes=_gd_string_list(collision or ()),
        lod_chain=_gd_lod_chain(lod_chain),
    )
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return target


# ---------------------------------------------------------------------------
# rigforge_export_godot
# ---------------------------------------------------------------------------

TEMP_COLLECTION = "FORGE_EXPORT_TEMP"


def _meshes_for_rig(rig, params):
    raw = params.get("meshes")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, (list, tuple)) and raw:
        return [find_object(str(name), mesh_only=True) for name in raw]
    out = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is rig:
                out.append(obj)
                break
    if not out:
        stored = str(_prop(rig, PROP_RIG_MESH, "") or "")
        obj = bpy.data.objects.get(stored)
        if obj is not None and obj.type == "MESH":
            out.append(obj)
    return out


def _lod_siblings(mesh):
    out = []
    base = mesh.name
    if base.endswith("_retopo"):
        base = base[: -len("_retopo")]
    for level in range(1, 9):
        candidate = bpy.data.objects.get("%s_lod%d" % (base, level))
        if candidate is not None and candidate.type == "MESH":
            out.append((candidate, level))
    return out


#: How ``lods`` is read. Godot 4 generates LODs on import with meshoptimizer,
#: and has no ``-lodN`` import suffix to recognise ours by (its list is ``-col``,
#: ``-convcol``, ``-occ``, ``-navmesh`` and friends), so the default ships LOD0
#: alone and lets the importer do the work. ``manual`` is for the cases where
#: the artist wants their own chain - hand-authored silhouettes, a level that
#: must not be regenerated - and it is only honest if the levels actually
#: switch, so it writes ``visibility_range_*`` into the import script.
LOD_MODES = {"AUTO": "auto", "GODOT": "auto", "NONE": "auto", "MANUAL": "manual"}


def _lod_mode(params):
    raw = params.get("lods")
    if isinstance(raw, bool):
        # Legacy callers said lods=true/false before there was a choice. True
        # meant "send my levels", which is what manual means now.
        return "manual" if raw else "auto"
    if raw is None:
        return "auto"
    return get_choice(params, "lods", LOD_MODES, "auto")


def _lod_record(obj):
    """The measured LOD record rigforge wrote, or ``{}``."""
    try:
        raw = obj.get(PROP_LOD)
    except (AttributeError, TypeError):
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(value, dict):
            return value
    return {}


def _measure_begin(lod0, lod):
    """Switch distance for a level with no stored record, measured now."""
    try:
        error = rigforge.measure_lod_error(lod0, lod, samples=500)["max"]
        return rigforge.visibility_distance(error)
    except Exception:  # noqa: BLE001 - a missing range is better than a lost export
        return 0.0


def build_lod_chain(entries):
    """``[(object, level, lod0), ...]`` -> visibility ranges, LOD0 included.

    One rule makes the chain usable: the distances must strictly increase, or
    two levels claim the same band and Godot draws both.  A level whose
    measured error lands at or below the level above it is nudged past it
    rather than dropped, because a level that exists and never shows is the
    bug this whole function is here to prevent.
    """
    by_lod0 = {}
    for lod, level, lod0 in entries:
        by_lod0.setdefault(id(lod0), (lod0, []))[1].append((level, lod))
    chain = []
    for lod0, levels in by_lod0.values():
        levels.sort()
        begins = []
        previous = 0.0
        for _level, lod in levels:
            record = _lod_record(lod)
            begin = record.get("visibility_begin")
            if not isinstance(begin, (int, float)) or isinstance(begin, bool) or begin <= 0.0:
                begin = _measure_begin(lod0, lod)
            begin = float(begin)
            if begin <= previous:
                begin = previous * 1.5 if previous > 0.0 else 1.0
            begins.append(begin)
            previous = begin
        objects = [lod0] + [lod for _level, lod in levels]
        for index, obj in enumerate(objects):
            begin = 0.0 if index == 0 else begins[index - 1]
            # 0.0 is Godot's "no end": the last level is visible for ever.
            end = begins[index] if index < len(begins) else 0.0
            chain.append({"object": obj, "begin": round(begin, 4), "end": round(end, 4)})
    return chain


@command("rigforge_export_godot")
def cmd_rigforge_export_godot(params):
    """Stage 7: deform-only bake, glTF with Godot conventions, import helper.

    Every temporary object lives in one collection that is removed in a
    ``finally``: a failed export must leave the scene exactly as it found it.
    """
    started = time.monotonic()
    warnings = []
    scene = get_scene()

    name = params.get("rig")
    if isinstance(name, str) and name.strip():
        rig = find_object(name.strip())
    else:
        rig = get_view_layer().objects.active
        if rig is None or rig.type != "ARMATURE":
            candidates = [obj for obj in bpy.data.objects
                          if obj.type == "ARMATURE" and _prop(obj, PROP_RIG_MESH, "")]
            rig = candidates[0] if len(candidates) == 1 else None
        if rig is None:
            raise ForgeError("No rig given and none could be guessed; pass 'rig'.")
    if rig.type != "ARMATURE":
        raise ForgeError("Object %r is a %s, not an armature." % (rig.name, rig.type))

    path = resolve_path(get_str(params, "path"), make_parents=True, default_ext=".glb")
    extension = os.path.splitext(path)[1].lower()
    if extension not in (".glb", ".gltf"):
        path += ".glb"
        extension = ".glb"
    export_format = "GLB" if extension == ".glb" else "GLTF_SEPARATE"

    meshes = _meshes_for_rig(rig, params)
    if not meshes:
        warnings.append("No mesh is bound to %r; exporting the skeleton alone." % rig.name)
    lod_mode = _lod_mode(params)
    include_lods = lod_mode == "manual"
    root_motion = get_bool(params, "root_motion", False)
    deform_only = get_bool(params, "deform_only", True)
    want_script = get_bool(params, "godot_import_script", True)
    step = get_int(params, "frame_step", 1, minimum=1, maximum=10)
    unit_scale = get_float(params, "unit_scale", 1.0, minimum=1e-6)
    if not deform_only:
        warnings.append("deform_only=false is not supported in v1: a control rig's "
                        "MCH/ORG bones would ship as skeleton joints. Exported "
                        "deform-only anyway.")

    requested = params.get("actions")
    if isinstance(requested, str) and requested.strip().lower() in ("all", "*", ""):
        requested = None
    actions, skipped = rig_actions(rig, requested)
    if not actions:
        warnings.append("No action animates %r, so the glTF has geometry but no "
                        "animation." % rig.name)

    previous_frame = scene.frame_current
    previous_range = (scene.frame_start, scene.frame_end)
    previous_action = None
    if rig.animation_data is not None:
        previous_action = rig.animation_data.action
    collection = None
    renamed = []
    renamed_actions = []
    baked_actions = []
    key_actions = []
    morph_report = {"targets": [], "driven": [], "animated_clips": {}}
    files = []
    baked_names = []
    collision = []
    lod_chain = []
    script_path = None
    root_report = {"applied": False, "reason": "not requested"}

    try:
        with object_mode():
            collection = bpy.data.collections.new(TEMP_COLLECTION)
            scene.collection.children.link(collection)
            refresh_view_layer()

            export_rig = build_deform_rig(rig, "%s_forge_export" % rig.name,
                                          collection, warnings)
            _rename_export_object(export_rig, rig.name, renamed, source=rig)
            _apply_object_transform(export_rig, warnings)
            deform = [bone.name for bone in export_rig.data.bones
                      if bone.name != ROOT_BONE]

            export_meshes = []
            origins = {}
            sources_by_copy = {}
            sources = [(mesh, None, None) for mesh in meshes]
            lod_entries = []
            if include_lods:
                for mesh in meshes:
                    for lod, level in _lod_siblings(mesh):
                        if lod not in meshes:
                            sources.append((lod, level, mesh))
                            lod_entries.append((lod, level, mesh))
                if not lod_entries:
                    warnings.append(
                        "lods=\"manual\" but no <mesh>_lodN sibling exists; only LOD0 "
                        "was exported. Generate levels with rigforge_retopo's 'lods'.")
            else:
                skipped_levels = [lod.name for mesh in meshes
                                  for lod, _level in _lod_siblings(mesh)]
                if skipped_levels:
                    warnings.append(
                        "Godot 4 generates its own LODs on import (meshoptimizer) and "
                        "has no -lodN import suffix, so %s were left out of the glTF. "
                        "Pass lods=\"manual\" to ship them with visibility ranges "
                        "instead." % ", ".join(sorted(skipped_levels)))
            # names captured before any source is renamed out of the way
            display = {id(entry[0]): entry[0].name for entry in sources}
            for mesh, level, origin in sources:
                # The export copy takes the source's own name (the source is
                # renamed out of the way and put back in the ``finally``), so the
                # glTF node names match the objects the sculptor sees - LODs
                # included. They used to be renamed to a `-lodN` suffix, which
                # looked like a Godot import convention and is not one: Godot's
                # suffixes are -col/-convcol/-occ/-navmesh and there is no -lod
                # among them. The switching is done by the visibility ranges the
                # import script writes, keyed on exactly these names.
                base = mesh.name
                copy = _duplicate(mesh, base + "_forge_export", collection)
                _apply_object_transform(copy, warnings)
                _rename_export_object(copy, base, renamed, source=mesh)
                world = copy.matrix_world.copy()
                copy.parent = export_rig
                copy.matrix_parent_inverse = export_rig.matrix_world.inverted_safe()
                copy.matrix_world = world
                bound = False
                for modifier in copy.modifiers:
                    if modifier.type == "ARMATURE":
                        modifier.object = export_rig
                        bound = True
                if not bound:
                    modifier = copy.modifiers.new(name="Armature", type="ARMATURE")
                    modifier.object = export_rig
                origins[copy.name] = origin
                sources_by_copy[copy.name] = mesh
                export_meshes.append(copy)
            refresh_view_layer()

            # What morph targets are going out, and which of them are driven and
            # would therefore arrive inert without the per-frame weight bake.
            for copy in export_meshes:
                keys = getattr(copy.data, "shape_keys", None)
                if keys is None:
                    continue
                morph_report["targets"].extend(
                    "%s/%s" % (copy.name, block.name)
                    for block in list(keys.key_blocks)[1:] if not block.mute)
            for copy in export_meshes:
                source = sources_by_copy.get(copy.name)
                if source is None:
                    continue
                morph_report["driven"].extend(
                    "%s/%s" % (copy.name, name) for name in driven_shape_keys(source))

            # An unweighted vertex makes the glTF exporter invent a
            # ``neutral_bone`` joint and hang the geometry off it, which is how
            # an LOD generated before skinning quietly ships un-animated.
            bone_names = set(deform)
            for copy in export_meshes:
                missing = _unweighted_vertices(copy, bone_names)
                if not missing:
                    continue
                origin = origins.get(copy.name)
                how = "the nearest bones"
                if origin is not None and not _unweighted_vertices(origin, bone_names):
                    transfer_deform_weights(origin, copy, export_rig, vertices=missing)
                    how = "%s's weights, by nearest vertex" % display.get(id(origin),
                                                                          origin.name)
                    missing = _unweighted_vertices(copy, bone_names)
                if missing:
                    distance_weights(copy, export_rig, vertices=missing)
                limit_and_normalize(copy, export_rig)
                warnings.append(
                    "%r had vertices with no deform weight (an LOD decimated before "
                    "skinning, usually); they were weighted from %s for this export."
                    % (copy.name, how))

            # --- bake every action onto the deform bones
            hip = None
            for candidate in ("DEF-spine", "DEF-spine.001", "DEF-hips"):
                if candidate in export_rig.pose.bones:
                    hip = candidate
                    break
            if hip is None and deform:
                hip = sorted(deform)[0]

            if actions:
                export_rig.animation_data_create()
                for action in actions:
                    constrain_to(rig, export_rig)
                    assign_action(rig, action)
                    frame_range = action.frame_range
                    start = int(math.floor(frame_range[0]))
                    end = int(math.ceil(frame_range[1]))
                    if end <= start:
                        end = start + 1
                    scene.frame_start = start
                    scene.frame_end = end
                    baked = bake_action_onto(rig, export_rig, action, start, end, step)
                    strip_constraints(export_rig)
                    frames = list(range(start, end + 1, step))

                    # The other half of the bake. Bones become keys through
                    # nla.bake; driven morph weights have no such operator, so
                    # they are sampled here, per clip, while the source rig is
                    # still holding this action.
                    for copy in export_meshes:
                        source = sources_by_copy.get(copy.name)
                        if source is None:
                            continue
                        driven = driven_shape_keys(source)
                        if not driven:
                            continue
                        samples = sample_shape_key_values(source, driven, frames)
                        keyed, key_action = bake_shape_key_values(
                            copy, samples, frames, action.name, warnings)
                        if key_action is not None:
                            key_actions.append(key_action)
                        if keyed:
                            morph_report["animated_clips"].setdefault(
                                action.name, []).extend(
                                    "%s/%s" % (copy.name, name) for name in keyed)
                    if root_motion:
                        try:
                            root_report = apply_root_motion(export_rig, baked, hip, frames)
                        except Exception as exc:  # noqa: BLE001 - never lose the export
                            root_report = {"applied": False, "reason": "%s: %s"
                                           % (type(exc).__name__, exc)}
                            warnings.append(
                                "Root motion could not be baked for %r (%s); the clip "
                                "was exported with the travel still on the hips."
                                % (action.name, exc))
                    # The clip has to reach Godot under the animator's own name,
                    # and Blender will not hand out a name the source action is
                    # still holding - so the source steps aside for the export
                    # and is put back in the ``finally``.
                    clip_name = action.name
                    renamed_actions.append((action, clip_name))
                    action.name = clip_name + "_forge_src"
                    baked.name = clip_name
                    baked.use_fake_user = True
                    baked_actions.append(baked)
                    baked_names.append(baked.name)

                    track = export_rig.animation_data.nla_tracks.new()
                    track.name = clip_name
                    strip = track.strips.new(clip_name, start, baked)
                    strip.name = clip_name
                    try:
                        for slot in getattr(baked, "slots", ()):
                            strip.action_slot = slot
                            break
                    except (AttributeError, TypeError, RuntimeError):
                        pass
                    export_rig.animation_data.action = None
                strip_constraints(export_rig)

            # --- the manual LOD chain, keyed on the names the glTF will carry
            lod_chain = []
            if lod_entries:
                for entry in build_lod_chain(lod_entries):
                    obj = entry["object"]
                    lod_chain.append({
                        "mesh": display.get(id(obj), obj.name),
                        "begin": entry["begin"],
                        "end": entry["end"],
                    })

            # --- glTF
            collision = [mesh.name for mesh in export_meshes
                         if mesh.name.lower().endswith(COLLISION_SUFFIXES)]
            targets = [export_rig] + export_meshes
            refresh_view_layer()
            previous_unit = scene.unit_settings.scale_length
            try:
                scene.unit_settings.scale_length = unit_scale
                with selection(targets, export_rig):
                    status = bpy.ops.export_scene.gltf(
                        **op_kwargs(bpy.ops.export_scene.gltf, {
                            "filepath": path,
                            "export_format": export_format,
                            "use_selection": True,
                            "export_yup": True,
                            "export_apply": False,
                            # Khronos' exporter defaults export_tangents to
                            # FALSE, and a tangent-less asset makes every engine
                            # regenerate the basis with its own algorithm - which
                            # is not the basis the normal map was baked against.
                            # The map then decodes slightly wrong, everywhere, and
                            # nothing errors. (gltf_tangents is ANDed with
                            # gltf_normals inside the exporter, so normals are
                            # asked for explicitly rather than relied on.)
                            "export_normals": True,
                            "export_tangents": True,
                            "export_texcoords": True,
                            "export_materials": "EXPORT",
                            "export_image_format": "AUTO",
                            "export_animations": bool(baked_names),
                            "export_animation_mode": "NLA_TRACKS",
                            "export_nla_strips": True,
                            "export_force_sampling": True,
                            "export_skins": True,
                            "export_def_bones": False,
                            # Morph targets, and the weight curves that make
                            # them mean anything. export_morph_animation
                            # defaults True but is passed explicitly because a
                            # corrective that ships at weight 0 for ever is
                            # exactly the kind of failure nobody sees; the reset
                            # keeps one clip's weights out of the next clip when
                            # only some of them are keyed.
                            "export_morph": True,
                            "export_morph_animation": True,
                            "export_morph_reset_sk_data": True,
                            "export_extras": True,
                            "export_leaf_bone": False,
                            "export_optimize_animation_size": False,
                            "check_existing": False,
                        }))
            finally:
                scene.unit_settings.scale_length = previous_unit
            if "FINISHED" not in status:
                raise ForgeError("The glTF exporter returned %s for %s."
                                 % (", ".join(sorted(status)) or "nothing", path))
            if not os.path.exists(path):
                raise ForgeError("The glTF exporter reported success but %s does not "
                                 "exist." % path)
            files.append(path)
            if export_format == "GLTF_SEPARATE":
                companion = os.path.splitext(path)[0] + ".bin"
                if os.path.exists(companion):
                    files.append(companion)

            script_path = None
            if want_script:
                script_path = write_godot_import_script(path, baked_names, collision,
                                                        lod_chain)
                files.append(script_path)
    finally:
        scene.frame_current = previous_frame
        scene.frame_start, scene.frame_end = previous_range
        try:
            if rig.animation_data is not None:
                assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        if collection is not None:
            for obj in list(collection.objects):
                data = obj.data
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except (ReferenceError, RuntimeError):
                    continue
                if data is not None and getattr(data, "users", 1) == 0:
                    for library in (bpy.data.meshes, bpy.data.armatures):
                        try:
                            library.remove(data)
                            break
                        except (ReferenceError, RuntimeError, TypeError):
                            continue
            try:
                bpy.data.collections.remove(collection)
            except (ReferenceError, RuntimeError):
                pass
        # baked clips belong to a rig that no longer exists; drop them before
        # the originals take their names back
        for action in baked_actions + key_actions:
            try:
                action.use_fake_user = False
                bpy.data.actions.remove(action)
            except (ReferenceError, RuntimeError, TypeError):
                pass
        for action, old_name in reversed(renamed_actions):
            try:
                action.name = old_name
            except (ReferenceError, RuntimeError):
                pass
        for obj, old_name in reversed(renamed):
            try:
                obj.name = old_name
            except (ReferenceError, RuntimeError):
                pass
        refresh_view_layer()

    return {
        "path": path,
        "format": export_format,
        "rig": rig.name,
        "actions": baked_names,
        "skipped_actions": skipped,
        "deform_bones": sorted(deform),
        "deform_bone_count": len(deform),
        "meshes": [mesh.name for mesh in meshes],
        "collision_meshes": collision,
        "lods": lod_mode,
        "lod_chain": lod_chain,
        "tangents": True,
        "morph_targets": sorted(set(morph_report["targets"])),
        "driven_morph_targets": sorted(set(morph_report["driven"])),
        "morph_animation": {clip: sorted(set(names))
                            for clip, names in morph_report["animated_clips"].items()},
        "morph_animation_note": (
            "glTF carries morph targets but has no concept of a driver, so a driven "
            "corrective would arrive at weight 0 for ever. Any driven key's weight is "
            "sampled per frame per clip and keyed on the export copy, in an NLA track "
            "named after the clip so the exporter merges it into the same animation as "
            "the bones."),
        "files": files,
        "import_script": script_path,
        "root_motion": root_report,
        "unit_scale": unit_scale,
        "y_up": True,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# panel operators
# ---------------------------------------------------------------------------

class _RigOperator(rigforge._RigForgeOperator):
    """Same guarded-status behaviour as the Phase 3 operators."""


class FORGE_OT_rf_metarig(_RigOperator):
    bl_idname = "forge.rf_metarig"
    bl_label = "Place Metarig"
    bl_description = ("Build a Rigify metarig and fit it to this mesh's tags "
                      "(head top, chin, shoulder, elbow, wrist, hip, knee, ankle)")

    def execute(self, context):
        def work(obj, props):
            rigforge._push_meta(obj, props)
            payload = {"object": obj.name, "archetype": props.archetype}
            if props.rig_preset != "auto":
                payload["preset"] = props.rig_preset
            rigforge.set_status(props, "Fitting a metarig to %s ..." % obj.name)
            result = cmd_rigforge_metarig(payload)
            warnings = result.get("warnings") or []
            props.summary = "%s   %d bones   %d tag(s) mapped" % (
                result["metarig"], result["bone_count"], len(result["mapping"]))
            rigforge.set_status(
                props,
                "Metarig %s (%s, %d bones)%s" % (
                    result["metarig"], result["preset"], result["bone_count"],
                    "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_generate_rig(_RigOperator):
    bl_idname = "forge.rf_generate_rig"
    bl_label = "Generate Rig"
    bl_description = ("Run Rigify's generate on the metarig, skin this mesh with "
                      "automatic weights and apply the per-tag cleanup rules")

    def execute(self, context):
        def work(obj, props):
            metarig = str(rigforge._prop(obj, PROP_METARIG, "") or "")
            payload = {"mesh": obj.name, "max_influences": int(props.max_influences)}
            if metarig:
                payload["metarig"] = metarig
            rigforge.set_status(props, "Generating the rig ...")
            result = cmd_rigforge_generate_rig(payload)
            report = result.get("cleanup_report") or {}
            props.summary = "%s   %d deform bones   %d weight(s) cleaned" % (
                result["rig"], result["deform_bones"], report.get("weights_zeroed", 0))
            warnings = result.get("warnings") or []
            rigforge.set_status(
                props,
                "Rig %s: %d deform bones, weights %s%s" % (
                    result["rig"], result["deform_bones"],
                    (result.get("weights") or {}).get("method", "?"),
                    "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_weights(_RigOperator):
    bl_idname = "forge.rf_weights"
    bl_label = "Weights"
    bl_description = "Report, clean up or normalise this mesh's deform weights"

    action: bpy.props.EnumProperty(
        name="Action",
        items=(("report", "Report", "Count influences and find problem vertices"),
               ("cleanup", "Cleanup", "Re-apply the per-tag rules, then limit and normalise"),
               ("normalize", "Normalize", "Limit influences and normalise only")),
        default="report",
    )

    def execute(self, context):
        def work(obj, props):
            result = cmd_rigforge_weights({
                "object": obj.name, "action": self.action,
                "max_influences": int(props.max_influences)})
            report = result["report"]
            props.summary = "%d bone(s)   %d over-influenced   %d un-normalised" % (
                report["deform_groups"], report["over_influenced_vertices"],
                report["unnormalized_vertices"])
            rigforge.set_status(props, "Weights %s: %d change(s), %d bone(s)"
                                % (self.action, result["changed"], report["deform_groups"]))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_export_godot(_RigOperator):
    bl_idname = "forge.rf_export_godot"
    bl_label = "Export to Godot"
    bl_description = ("Bake every action onto the deform bones, strip the control "
                      "rig and write a glTF plus its Godot import script")

    def execute(self, context):
        def work(obj, props):
            if not props.export_path.strip():
                return self.fail(props, "Set an export path first (.glb or .gltf).")
            rig_name = str(rigforge._prop(obj, PROP_RIG, "") or "")
            payload = {"path": props.export_path,
                       "root_motion": bool(props.root_motion),
                       "lods": str(props.export_lods)}
            if rig_name:
                payload["rig"] = rig_name
            if props.export_actions == "selected" and props.export_action_names.strip():
                payload["actions"] = [name.strip() for name
                                      in props.export_action_names.split(",")
                                      if name.strip()]
            rigforge.set_status(props, "Exporting to Godot ...")
            result = cmd_rigforge_export_godot(payload)
            props.summary = "%s   %d action(s)   %d deform bones" % (
                os.path.basename(result["path"]), len(result["actions"]),
                result["deform_bone_count"])
            warnings = result.get("warnings") or []
            rigforge.set_status(
                props,
                "Exported %s (%d action(s))%s" % (
                    os.path.basename(result["path"]), len(result["actions"]),
                    "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings))
            return {"FINISHED"}

        return self.guarded(context, work)


_CLASSES = (
    FORGE_OT_rf_metarig,
    FORGE_OT_rf_generate_rig,
    FORGE_OT_rf_weights,
    FORGE_OT_rf_export_godot,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
