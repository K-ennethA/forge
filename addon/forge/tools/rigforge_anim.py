"""RigForge stages 5 and 6: clothing, the action library, keyframing, retargeting.

Phase 5 of the pipeline (``docs/plan.md`` section 4, stages 5 and 6).  Phase 3
tagged the sculpt and retopologised it; Phase 4 rigged it and taught it to leave
as a glTF.  This module is what fills the character out and makes it *move*:

* :func:`cmd_rigforge_cloth` — a garment grown from the tagged faces of the body.
  Duplicate, push out along the normals, thicken, and then either weight it to
  the same rig (``skin_tight``) or run a real Blender cloth sim and bake the
  settled result into a shape key (``shapekeys``).  Godot does not run Blender
  cloth, so **the bake is what ships**: the sim modifiers are removed before the
  command returns and the garment leaves as an ordinary skinned mesh.
* :func:`cmd_rigforge_action` — the Godot action library: ``new`` / ``list`` /
  ``delete`` / ``duplicate`` / ``rename`` / ``push_nla``, with the ``-loop``
  suffix convention enforced in both directions.
* :func:`cmd_rigforge_keyframe` — one structured call for a described-motion
  pass.  This is the command Claude uses when the sculptor says "a heavy
  two-beat hop, ears trail", so a bone it cannot find is answered with the
  closest name it *can* find rather than a KeyError.
* :func:`cmd_rigforge_walk` — the locomotion authoring path.  A walk cycle is
  keyed on the **leg IK targets**, not on the leg's FK rotations, with the
  stance phases world-locked so the planted foot cannot drift; hips, torso,
  arms and the heel/ball foot roll are layered over that.  An FK-keyed walk is
  the foot-slide anti-pattern, and ``animation_check`` measures the difference
  in millimetres.
* :func:`cmd_rigforge_punch` — the combat authoring path, and the same argument
  made twice.  The feet are keyed on the leg IK targets and do not move at all
  (a stance shift is the hips travelling over planted feet, not the feet
  travelling), and the fist is keyed on the **arm** IK target so its drive is a
  straight line to a measured point rather than an FK arc.  Pelvis, chest and
  shoulder carry the same rotation curve offset in time, so the turn travels up
  the body — and the report proves it peaked in that order rather than claiming
  it, alongside peak fist speed and full extension against the arm's own reach.
* :func:`cmd_rigforge_retarget` — a mocap clip the **user supplies** (.bvh or
  .fbx, both read with importers that ship inside Blender) mapped onto the rig's
  FK controls by name heuristics and baked to an action.  Nothing is downloaded,
  ever; the import is deleted in a ``finally``.

Three deliberate positions, all of which the docstrings below argue for at the
point they matter:

* **the sim never ships.**  Cloth, collision and their point caches are torn
  down after the bake, and the settled geometry survives as a shape key.
* **retargeting lands on FK controls**, not on the deform bones, so the result is
  something the sculptor can open and fix.  Rigify limbs default to IK, so the
  rig's ``IK_FK`` switches are moved to FK and keyframed — otherwise the clip
  would look perfect in the FK bones and export as a T-pose.  A **whole-body**
  mocap bake is the one case where switching every limb is right; a keyframe
  pass is not, so :func:`cmd_rigforge_keyframe`'s ``"auto"`` now switches only
  the limbs whose FK controls it is actually keying.  It used to switch all of
  them, which meant keying one arm quietly took both legs off IK and handed the
  next walk cycle a foot slide.
* **rotations are euler.**  ``rotation_euler_deg`` is degrees in the bone's own
  space; a bone in quaternion mode is switched to ``XYZ`` and the switch is
  reported.  Half a keyframing API that silently ignores the mode is worse than
  one that says what it changed.

Everything here runs on Blender's main thread and works under
``blender --background`` (no window, no event loop).
"""

import difflib
import json
import math
import os
import time

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup
from mathutils import Matrix, Vector

from . import rigforge
from . import rigforge_rig
from .common import (
    MM_TO_M,
    M_TO_MM,
    active_only,
    apply_modifier,
    find_object,
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_scene,
    get_str,
    get_view_layer,
    object_mode,
    op_kwargs,
    refresh_view_layer,
    resolve_object,
    resolve_path,
)
from .registry import ForgeError, command
from .rigforge import _prop, _set_prop, tag_display_name, tag_group_name
from .rigforge_rig import LOOP_SUFFIX, PROP_RIG, assign_action

#: The tag every generated garment carries, so a second pass (or a later
#: retopo) can find the clothing without being told which object it is.
GARMENT_TAG = "Garment"

#: Object custom properties written by :func:`cmd_rigforge_cloth`.
PROP_GARMENT_OF = "forge_garment_of"      # garment: the body it was grown from
PROP_GARMENT_INFO = "forge_garment"       # garment: the settings it was made with

# ---------------------------------------------------------------------------
# cloth presets
# ---------------------------------------------------------------------------

#: Physics for the three presets the contract names.  These are Blender's own
#: shipped cloth presets, chosen rather than invented so that a sculptor who
#: opens the Physics tab afterwards sees numbers they recognise:
#:
#: ============ ====== ==== ======= =========== ===== ======= =========
#: preset       Blender mass tension compression shear bending damping
#: ============ ====== ==== ======= =========== ===== ======= =========
#: ``cotton``   Cotton 0.30 15      15          5     0.5     5/5/5/0.5
#: ``leather``  Leather 0.40 80     80          80    150     25/25/25/0.5
#: ``heavy``    Denim  1.00 40      40          40    10      25/25/25/0.5
#: ============ ====== ==== ======= =========== ===== ======= =========
#:
#: ``mass`` is kg per square metre of cloth; the stiffnesses are Blender's own
#: unitless spring constants.  ``quality`` is solver substeps per frame — the
#: single most effective knob against an exploding sim, so the stiffer presets
#: get more of it.  ``air_damping`` stays at 1.0 for all three: a garment is
#: being settled onto a body, not flapping in wind.
CLOTH_PRESETS = {
    "cotton": {
        "quality": 5,
        "mass": 0.30,
        "tension_stiffness": 15.0,
        "compression_stiffness": 15.0,
        "shear_stiffness": 5.0,
        "bending_stiffness": 0.5,
        "tension_damping": 5.0,
        "compression_damping": 5.0,
        "shear_damping": 5.0,
        "bending_damping": 0.5,
        "air_damping": 1.0,
    },
    "leather": {
        "quality": 7,
        "mass": 0.40,
        "tension_stiffness": 80.0,
        "compression_stiffness": 80.0,
        "shear_stiffness": 80.0,
        "bending_stiffness": 150.0,
        "tension_damping": 25.0,
        "compression_damping": 25.0,
        "shear_damping": 25.0,
        "bending_damping": 0.5,
        "air_damping": 1.0,
    },
    "heavy": {
        "quality": 8,
        "mass": 1.00,
        "tension_stiffness": 40.0,
        "compression_stiffness": 40.0,
        "shear_stiffness": 40.0,
        "bending_stiffness": 10.0,
        "tension_damping": 25.0,
        "compression_damping": 25.0,
        "shear_damping": 25.0,
        "bending_damping": 0.5,
        "air_damping": 1.0,
    },
}

DEFAULT_OFFSET_MM = 5.0
DEFAULT_THICKNESS_MM = 2.0
DEFAULT_FRAMES = 30

#: A garment coarser than this is subdivided once before it is thickened, so the
#: cloth solver has springs to work with.  (A 200-face patch has no bend modes at
#: all; it behaves like sheet metal and reads as a bug.)
SUBDIVIDE_BELOW_FACES = 800

#: How far the settled sim is allowed to stray before we call it an explosion:
#: three times the body's bounding box on any axis.  A blown-up cloth sim does
#: not fail, it produces coordinates in the thousands, so a size check is the
#: only honest test.
EXPLOSION_FACTOR = 3.0

PIN_GROUP = "Forge Pin"
CLOTH_MODIFIER = "Forge Cloth"
COLLISION_MODIFIER = "Forge Collision"
SETTLED_KEY = "Settled"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _mesh_from(params, key="object"):
    obj = resolve_object(params, key=key, mesh_only=True)
    if obj.data is None or not len(obj.data.polygons):
        raise ForgeError("Object %r has no faces to work from." % obj.name)
    return obj


def _armature(name):
    obj = find_object(name)
    if obj.type != "ARMATURE":
        raise ForgeError("Object %r is a %s, not an armature." % (obj.name, obj.type))
    return obj


def _rig_for(obj, params, key="rig", required=False):
    """The armature a mesh (or the caller) means.

    Explicit ``rig`` wins; then the mesh's own Armature modifier; then the
    ``forge_rig`` property Phase 4 wrote onto it; then, if there is exactly one
    armature in the file, that one.
    """
    name = params.get(key)
    if isinstance(name, str) and name.strip():
        return _armature(name.strip())
    if obj is not None:
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is not None:
                return modifier.object
        stored = str(_prop(obj, PROP_RIG, "") or "")
        candidate = bpy.data.objects.get(stored)
        if candidate is not None and candidate.type == "ARMATURE":
            return candidate
    active = getattr(get_view_layer().objects, "active", None)
    if active is not None and active.type == "ARMATURE":
        return active
    rigs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if len(rigs) == 1:
        return rigs[0]
    if not required:
        return None
    raise ForgeError(
        "No rig given and none could be guessed; pass %r. Armatures in this file: %s."
        % (key, ", ".join(sorted(o.name for o in rigs)) or "none")
    )


def _world_bbox(obj, coords=None):
    """``(low, high)`` of ``obj`` in world space, optionally over given local coords."""
    matrix = obj.matrix_world
    points = ([matrix @ Vector(co) for co in coords] if coords is not None
              else [matrix @ vertex.co for vertex in obj.data.vertices])
    if not points:
        return None
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    return low, high


def _delete_objects(objects):
    """Remove objects (and their orphaned data), tolerating already-dead handles.

    A datablock removed through one list leaves every other Python reference to
    it a dangling ``StructRNA``: touching even ``.name`` raises ``ReferenceError``,
    so the ``finally`` blocks that call this have to survive being handed the
    same object twice.
    """
    for obj in objects:
        if obj is None:
            continue
        try:
            data = obj.data
        except (ReferenceError, AttributeError):
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError, TypeError):
            continue
        if data is not None and getattr(data, "users", 1) == 0:
            for library in (bpy.data.meshes, bpy.data.armatures):
                try:
                    library.remove(data)
                    break
                except (ReferenceError, RuntimeError, TypeError):
                    continue


# ---------------------------------------------------------------------------
# rigforge_cloth
# ---------------------------------------------------------------------------

def _garment_faces(obj, params):
    """The polygon indices a garment is grown from, and where they came from."""
    raw = params.get("tags")
    use_selection = params.get("use_selection")
    if raw is not None and use_selection:
        raise ForgeError("Pass either 'tags' or 'use_selection', not both.")

    if raw is not None:
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ForgeError("'tags' must be a non-empty list of tag names, e.g. "
                             '["Torso", "Arm.L"].')
        wanted = {}
        for entry in raw:
            group = rigforge.find_tag_group(obj, entry)
            wanted[group.index] = tag_display_name(group.name)
        face_map = rigforge._face_tag_map(obj)
        faces = sorted(index for index, marks in face_map.items()
                       if marks & set(wanted))
        if not faces:
            raise ForgeError(
                "No face on %r carries %s. A face belongs to a tag only when every "
                "one of its vertices does; check the tag with rigforge_list_tags."
                % (obj.name, ", ".join(sorted(wanted.values()))))
        return faces, "tags", sorted(wanted.values())

    if use_selection:
        if not get_bool(params, "use_selection", False):
            raise ForgeError("'use_selection' was false; pass 'tags' instead.")
        faces = rigforge.selected_faces(obj)
        if not faces:
            raise ForgeError("The selection is empty - select the faces the garment "
                             "should cover, or pass 'tags'.")
        return sorted(set(faces)), "selection", []

    known = ", ".join(tag_display_name(g.name) for g in rigforge.tag_groups(obj)) or "none"
    raise ForgeError(
        "Nothing to make a garment from: pass 'tags' (a list of tag names) or "
        "'use_selection': true. Tags on %r: %s." % (obj.name, known))


def _boundary_vertices(bm):
    """Vertices on an open edge of the patch — the seam a garment hangs from."""
    out = set()
    for edge in bm.edges:
        if len(edge.link_faces) < 2:
            out.update(vertex.index for vertex in edge.verts)
    return out


def _build_garment(body, faces, name, offset, thickness, subdivide, warnings):
    """Duplicate the tagged patch, push it off the body, thicken it.

    The duplicate keeps the body's vertex groups (so the deform weights come
    across for free, before any transfer runs) and its material slot layout, and
    the offset is a per-vertex push along the vertex normal rather than a
    Displace modifier, so what the cloth solver sees is real geometry with no
    modifier stack underneath it.
    """
    existing = bpy.data.objects.get(name)
    if existing is not None:
        _delete_objects([existing])

    copy = body.copy()
    copy.data = body.data.copy()
    copy.name = name
    try:
        copy.data.name = name
    except (AttributeError, RuntimeError):
        pass
    targets = list(body.users_collection) or [get_scene().collection]
    linked = False
    for collection in targets:
        try:
            collection.objects.link(copy)
            linked = True
        except RuntimeError:
            continue
    if not linked:
        get_scene().collection.objects.link(copy)
    for modifier in list(copy.modifiers):
        try:
            copy.modifiers.remove(modifier)
        except (RuntimeError, ReferenceError):
            pass
    try:
        copy.animation_data_clear()
    except AttributeError:
        pass
    if copy.data.shape_keys is not None:
        # A body mid-sculpt can carry shape keys; a garment starts from the
        # geometry as it stands, and stacking someone else's morphs under a
        # cloth sim is not a thing anyone asked for.
        with active_only(copy):
            try:
                bpy.ops.object.shape_key_remove(all=True)
            except RuntimeError:
                pass
        warnings.append("The body's shape keys were not carried onto the garment.")
    refresh_view_layer()

    keep = set(faces)
    bm = bmesh.new()
    try:
        bm.from_mesh(copy.data)
        bm.faces.ensure_lookup_table()
        doomed = [face for face in bm.faces if face.index not in keep]
        if doomed:
            bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bm.verts.ensure_lookup_table()
        loose = [vertex for vertex in bm.verts if not vertex.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.normal_update()
        bm.verts.ensure_lookup_table()
        if not bm.faces:
            bm.free()
            _delete_objects([copy])
            raise ForgeError("Those faces left no geometry to make a garment from.")
        if offset:
            for vertex in bm.verts:
                vertex.co = vertex.co + vertex.normal * offset
        bm.normal_update()
        boundary = _boundary_vertices(bm)
        bm.to_mesh(copy.data)
    finally:
        try:
            bm.free()
        except (ReferenceError, RuntimeError):
            pass
    copy.data.update()

    # The seam has to be pinned or a shirt slides off the shoulders on frame 2.
    pin = copy.vertex_groups.get(PIN_GROUP) or copy.vertex_groups.new(name=PIN_GROUP)
    if boundary:
        pin.add(sorted(boundary), 1.0, "REPLACE")
    pinned = len(boundary)

    sheet_faces = len(copy.data.polygons)
    levels = 0
    if subdivide == "auto":
        levels = 1 if sheet_faces < SUBDIVIDE_BELOW_FACES else 0
    else:
        levels = int(subdivide)
    if levels > 0:
        modifier = copy.modifiers.new(name="Forge Subdivide", type="SUBSURF")
        modifier.subdivision_type = "SIMPLE"
        modifier.levels = levels
        modifier.render_levels = levels
        apply_modifier(copy, modifier)

    if thickness > 0.0:
        modifier = copy.modifiers.new(name="Forge Solidify", type="SOLIDIFY")
        modifier.thickness = thickness
        modifier.offset = 0.0
        try:
            modifier.use_rim = True
            modifier.use_rim_only = False
        except (AttributeError, TypeError):
            pass
        apply_modifier(copy, modifier)

    group = copy.vertex_groups.get(tag_group_name(GARMENT_TAG))
    if group is None:
        group = copy.vertex_groups.new(name=tag_group_name(GARMENT_TAG))
    if len(copy.data.vertices):
        group.add([v.index for v in copy.data.vertices], 1.0, "REPLACE")

    _set_prop(copy, PROP_GARMENT_OF, body.name)
    refresh_view_layer()
    return copy, {"sheet_faces": sheet_faces, "subdivide_levels": levels,
                  "pinned_vertices": pinned}


def _skin_garment(garment, body, rig, warnings):
    """Give the garment the body's deform weights and an armature modifier.

    Duplicating the body's faces already brought the weights along verbatim,
    which is the best possible transfer; Solidify and the subdivision carry them
    through their own interpolation.  What can still arrive unweighted is a
    vertex the body itself never weighted, so anything left over is filled in by
    nearest-vertex data transfer from the body, and the whole mesh is then
    limited to four influences and normalised the way a game engine wants.
    """
    report = {"method": "inherited", "rig": None, "transferred": 0,
              "unweighted_before": 0, "unweighted_after": 0, "armature_modifier": False}
    if rig is None:
        warnings.append(
            "No rig is bound to %r, so the garment was left unskinned. Run "
            "rigforge_generate_rig first, or pass 'rig'." % body.name)
        return report
    report["rig"] = rig.name

    names = set(rigforge_rig.deform_bones(rig))
    if not names:
        warnings.append("Rig %r has no deforming bones; the garment was left unskinned."
                        % rig.name)
        return report

    missing = rigforge_rig._unweighted_vertices(garment, names)
    report["unweighted_before"] = len(missing)
    if missing:
        report["transferred"] = rigforge_rig.transfer_deform_weights(
            body, garment, rig, vertices=missing)
        report["method"] = "inherited+nearest_vertex_transfer"
        missing = rigforge_rig._unweighted_vertices(garment, names)
        if missing:
            rigforge_rig.distance_weights(garment, rig, vertices=missing)
            report["method"] = "inherited+nearest_vertex_transfer+distance"
    rigforge_rig.limit_and_normalize(garment, rig)
    report["unweighted_after"] = len(rigforge_rig._unweighted_vertices(garment, names))

    modifier = None
    for existing in garment.modifiers:
        if existing.type == "ARMATURE":
            modifier = existing
            break
    if modifier is None:
        modifier = garment.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = rig
    report["armature_modifier"] = True

    if garment.parent is not rig:
        world = garment.matrix_world.copy()
        garment.parent = rig
        garment.matrix_parent_inverse = rig.matrix_world.inverted_safe()
        garment.matrix_world = world
    refresh_view_layer()
    return report


def _apply_cloth_preset(settings, preset):
    values = CLOTH_PRESETS[preset]
    applied = {}
    for key, value in values.items():
        try:
            setattr(settings, key, value)
            applied[key] = value
        except (AttributeError, TypeError, ValueError):
            continue
    try:
        settings.effector_weights.gravity = 1.0
    except (AttributeError, TypeError):
        pass
    return applied


def _run_cloth(garment, body, preset, frames, collide, self_collision, thickness,
               pinned, warnings):
    """Settle the garment onto the body, then hand back the settled coordinates.

    Frame stepping is the whole simulation: Blender's cloth solver advances one
    frame per depsgraph evaluation and refuses to skip, so the loop below *is*
    the bake.  Nothing about it needs a window, which is the only reason a
    ``--background`` run can do this at all.
    """
    scene = get_scene()
    report = {"ok": False, "frames": int(frames), "preset": preset, "physics": {},
              "reason": "", "seconds": 0.0, "self_collision": bool(self_collision),
              "collision": bool(collide), "pinned_vertices": int(pinned)}
    started = time.monotonic()

    span = max(list(body.dimensions) + [1e-3])
    distance = max(1e-4, min(thickness * 0.5 if thickness > 0 else span * 0.01,
                             span * 0.01))

    cloth = garment.modifiers.new(name=CLOTH_MODIFIER, type="CLOTH")
    report["physics"] = _apply_cloth_preset(cloth.settings, preset)
    settings = cloth.settings
    collision = cloth.collision_settings
    try:
        collision.use_collision = bool(collide)
        # Quality is substeps of the collision solver: cheap insurance against a
        # garment eating its way through the body it is supposed to sit on.
        collision.collision_quality = 5
        collision.distance_min = distance
        collision.self_distance_min = distance
        # Self collision is off by default on purpose: a duplicated patch of a
        # sculpt is very often self-intersecting before the sim starts, and a
        # self-collision solver handed an already-tangled mesh is the single
        # most reliable way to make a cloth sim explode.
        collision.use_self_collision = bool(self_collision)
    except (AttributeError, TypeError, ValueError) as exc:
        warnings.append("Some cloth collision settings were refused (%s)." % exc)
    if pinned:
        try:
            settings.vertex_group_mass = PIN_GROUP
            settings.pin_stiffness = 1.0
        except (AttributeError, TypeError, ValueError):
            warnings.append("The seam could not be pinned; the garment may slide.")
    try:
        cloth.point_cache.frame_start = 1
        cloth.point_cache.frame_end = max(2, int(frames))
    except (AttributeError, TypeError, ValueError):
        pass

    collider = None
    if collide:
        collider = body.modifiers.get(COLLISION_MODIFIER)
        if collider is None:
            collider = body.modifiers.new(name=COLLISION_MODIFIER, type="COLLISION")
        try:
            body.collision.thickness_outer = distance
            body.collision.damping = 0.2
        except (AttributeError, TypeError, ValueError):
            pass

    previous_frame = scene.frame_current
    previous_range = (scene.frame_start, scene.frame_end)
    coords = None
    try:
        scene.frame_start = 1
        scene.frame_end = max(2, int(frames))
        for frame in range(1, max(2, int(frames)) + 1):
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            depsgraph.update()
            garment.evaluated_get(depsgraph)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = garment.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            coords = [vertex.co.copy() for vertex in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    except Exception as exc:  # noqa: BLE001 - a failed sim must not lose the garment
        report["reason"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        scene.frame_start, scene.frame_end = previous_range
        try:
            scene.frame_set(previous_frame)
        except (RuntimeError, TypeError):
            pass
        # The sim never ships: modifiers and point caches come off before the
        # command returns, whether it worked or not.
        try:
            garment.modifiers.remove(cloth)
        except (RuntimeError, ReferenceError):
            pass
        if collider is not None:
            try:
                body.modifiers.remove(collider)
            except (RuntimeError, ReferenceError):
                pass
        refresh_view_layer()
        report["seconds"] = round(time.monotonic() - started, 3)

    if coords is None:
        return report, None
    if len(coords) != len(garment.data.vertices):
        report["reason"] = ("the solver returned %d vertices for a %d vertex garment"
                            % (len(coords), len(garment.data.vertices)))
        return report, None
    report["ok"] = True
    return report, coords


def _sim_is_sane(garment, body, coords):
    """Did the sim settle, or did it explode? Returns ``(ok, reason, size)``."""
    for co in coords:
        for value in co:
            if not math.isfinite(value):
                return False, "the solver produced non-finite coordinates", None
    bounds = _world_bbox(garment, coords)
    if bounds is None:
        return False, "the settled garment has no vertices", None
    body_bounds = _world_bbox(body)
    if body_bounds is None:
        return True, "", [round(v, 6) for v in (bounds[1] - bounds[0])]
    size = bounds[1] - bounds[0]
    body_size = body_bounds[1] - body_bounds[0]
    limits = [max(body_size[i], 1e-4) * EXPLOSION_FACTOR for i in range(3)]
    for axis in range(3):
        if size[axis] > limits[axis]:
            return (False,
                    "the settled garment is %.3f across on %s, more than %.0fx the "
                    "body's %.3f" % (size[axis], "XYZ"[axis], EXPLOSION_FACTOR,
                                     body_size[axis]),
                    [round(v, 6) for v in size])
    return True, "", [round(v, 6) for v in size]


def _bake_shape_key(garment, coords, name=SETTLED_KEY):
    mesh = garment.data
    if mesh.shape_keys is None:
        garment.shape_key_add(name="Basis", from_mix=False)
    existing = mesh.shape_keys.key_blocks.get(name) if mesh.shape_keys else None
    if existing is not None:
        garment.shape_key_remove(existing)
    key = garment.shape_key_add(name=name, from_mix=False)
    for index, co in enumerate(coords):
        key.data[index].co = co
    key.value = 1.0
    try:
        key.slider_min = 0.0
        key.slider_max = 1.0
    except (AttributeError, TypeError):
        pass
    mesh.update()
    return key


@command("rigforge_cloth")
def cmd_rigforge_cloth(params):
    """Stage 5: a garment from the tagged faces of the body.

    ``skin_tight`` is the one that always works: no simulation at all, the
    garment simply wears the body's own deform weights.  ``shapekeys`` adds the
    cloth pass and bakes the settled result into a ``Settled`` shape key, then
    removes the cloth and collision modifiers so what is left is an ordinary
    skinned mesh Godot can import.  ``bones`` is not implemented in v1 and warns
    loudly before falling back to ``skin_tight``.
    """
    started = time.monotonic()
    warnings = []
    body = _mesh_from(params)

    output = get_choice(
        params, "output",
        {"SKIN_TIGHT": "skin_tight", "SKINTIGHT": "skin_tight", "SKIN": "skin_tight",
         "SHAPEKEYS": "shapekeys", "SHAPE_KEYS": "shapekeys", "SHAPEKEY": "shapekeys",
         "BONES": "bones"},
        "skin_tight",
    )
    preset = get_choice(
        params, "preset",
        {"COTTON": "cotton", "LEATHER": "leather", "HEAVY": "heavy", "DENIM": "heavy"},
        "cotton",
    )
    offset_mm = get_float(params, "offset_mm", DEFAULT_OFFSET_MM, minimum=0.0,
                          maximum=1000.0)
    thickness_mm = get_float(params, "thickness_mm", DEFAULT_THICKNESS_MM, minimum=0.0,
                             maximum=1000.0)
    frames = get_int(params, "frames", DEFAULT_FRAMES, minimum=1, maximum=2000)
    collide = get_bool(params, "collision", True)
    self_collision = get_bool(params, "self_collision", False)
    raw_subdivide = params.get("subdivide", "auto")
    if isinstance(raw_subdivide, str):
        if raw_subdivide.strip().lower() != "auto":
            raise ForgeError("'subdivide' must be \"auto\" or a level count 0-3.")
        subdivide = "auto"
    elif raw_subdivide is None:
        subdivide = "auto"
    else:
        subdivide = get_int(params, "subdivide", 0, minimum=0, maximum=3)

    requested_output = output
    if output == "bones":
        warnings.append(
            "output='bones' is not implemented in v1: converting a cloth sim into a "
            "bone chain needs a solver this add-on does not have. The garment was "
            "built with skin_tight behaviour instead - it is weighted to the same "
            "rig and ships as-is. Use output='shapekeys' if you want the simulated "
            "silhouette baked in.")
        output = "skin_tight"

    faces, source, tags = _garment_faces(body, params)
    name = params.get("name")
    if isinstance(name, str) and name.strip():
        name = name.strip()
    else:
        name = "%s_Garment" % body.name

    rig = _rig_for(body, params)
    offset = offset_mm * MM_TO_M
    thickness = thickness_mm * MM_TO_M

    with object_mode():
        garment, build = _build_garment(body, faces, name, offset, thickness,
                                        subdivide, warnings)

    sim = {"ok": False, "reason": "not requested", "frames": 0, "preset": preset}
    shape_keys = []
    settled_size = None
    if output == "shapekeys":
        with object_mode():
            sim, coords = _run_cloth(garment, body, preset, frames, collide,
                                     self_collision, thickness,
                                     build["pinned_vertices"], warnings)
            if coords is not None:
                ok, reason, settled_size = _sim_is_sane(garment, body, coords)
                sim["bbox"] = settled_size
                if ok:
                    key = _bake_shape_key(garment, coords)
                    shape_keys.append(key.name)
                else:
                    sim["ok"] = False
                    sim["reason"] = reason
                    warnings.append(
                        "The cloth sim did not settle (%s), so no shape key was baked; "
                        "the un-simulated garment was delivered instead. Try a stiffer "
                        "preset, fewer frames, or a larger offset_mm." % reason)
            else:
                warnings.append(
                    "The cloth sim did not run (%s); the un-simulated garment was "
                    "delivered instead." % (sim.get("reason") or "no reason given"))

    with object_mode():
        weights = _skin_garment(garment, body, rig, warnings)

    _set_prop(garment, PROP_GARMENT_INFO, json.dumps({
        "body": body.name, "output": output, "requested_output": requested_output,
        "preset": preset, "offset_mm": offset_mm, "thickness_mm": thickness_mm,
        "tags": tags, "source": source, "shape_keys": shape_keys,
    }))
    refresh_view_layer()

    bounds = _world_bbox(garment)
    return {
        "garment": garment.name,
        "object": body.name,
        "output": output,
        "requested_output": requested_output,
        "preset": preset,
        "physics": sim.get("physics") or CLOTH_PRESETS[preset],
        "shape_keys": shape_keys,
        "tags": tags,
        "source": source,
        "faces_used": len(faces),
        "face_count": len(garment.data.polygons),
        "vertex_count": len(garment.data.vertices),
        "sheet_faces": build["sheet_faces"],
        "subdivide_levels": build["subdivide_levels"],
        "pinned_vertices": build["pinned_vertices"],
        "offset_mm": offset_mm,
        "thickness_mm": thickness_mm,
        "tag": GARMENT_TAG,
        "rig": weights.get("rig"),
        "weights": weights,
        "sim": sim,
        "size": [round(v, 6) for v in (bounds[1] - bounds[0])] if bounds else None,
        "modifiers": [modifier.type for modifier in garment.modifiers],
        "material_slots": [slot.name for slot in garment.material_slots],
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# the action library
# ---------------------------------------------------------------------------

def loop_name(name, loop):
    """Enforce Godot's ``-loop`` convention. ``loop=None`` leaves the name alone."""
    text = str(name).strip()
    if not text:
        raise ForgeError("An action name must not be empty.")
    if loop is None:
        return text
    has = text.endswith(LOOP_SUFFIX)
    if loop and not has:
        return text + LOOP_SUFFIX
    if not loop and has:
        stripped = text[: -len(LOOP_SUFFIX)].strip()
        if not stripped:
            raise ForgeError("Stripping %r from %r leaves nothing to name the action."
                             % (LOOP_SUFFIX, text))
        return stripped
    return text


def is_loop(name):
    return str(name).endswith(LOOP_SUFFIX)


def action_channel_containers(action):
    """Every F-curve collection of an action, legacy or slotted.

    Blender 5.0 moved F-curves into per-slot channelbags inside layer strips, so
    ``action.fcurves`` is simply gone; anything that wants to *remove* a curve
    needs the collection it lives in, not just the curve.
    """
    containers = []
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        containers.append(curves)
        return containers
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for slot in getattr(action, "slots", ()):
                try:
                    bag = strip.channelbag(slot)
                except (AttributeError, TypeError, RuntimeError):
                    bag = None
                if bag is not None:
                    containers.append(bag.fcurves)
    return containers


def clear_action(action):
    """Remove every F-curve from an action. Returns how many went away."""
    removed = 0
    for container in action_channel_containers(action):
        for curve in list(container):
            try:
                container.remove(curve)
                removed += 1
            except (RuntimeError, ReferenceError, TypeError):
                continue
    return removed


def find_fcurve(action, data_path, index):
    for curve in rigforge_rig.action_fcurves(action):
        if curve.data_path == data_path and curve.array_index == index:
            return curve
    return None


def nla_usage(action):
    """``[{object, track, strip}]`` — every NLA strip in the file using ``action``."""
    out = []
    for obj in bpy.data.objects:
        data = getattr(obj, "animation_data", None)
        if data is None:
            continue
        for track in data.nla_tracks:
            for strip in track.strips:
                if getattr(strip, "action", None) is action:
                    out.append({"object": obj.name, "track": track.name,
                                "strip": strip.name})
    return out


def action_entry(action, rig=None):
    frame_range = tuple(action.frame_range)
    curves = rigforge_rig.action_fcurves(action)
    bones = sorted(rigforge_rig.action_bones(action))
    tracks = nla_usage(action)
    entry = {
        "name": action.name,
        "loop": is_loop(action.name),
        "frame_range": [round(float(frame_range[0]), 4), round(float(frame_range[1]), 4)],
        "frames": max(0, int(round(float(frame_range[1]) - float(frame_range[0])))),
        "fcurves": len(curves),
        "bones": bones,
        "bone_count": len(bones),
        "nla": bool(tracks),
        "nla_tracks": tracks,
        "use_fake_user": bool(action.use_fake_user),
        "users": int(action.users),
    }
    if rig is not None:
        rig_bones = {bone.name for bone in rig.pose.bones}
        entry["fits_rig"] = bool(set(bones) & rig_bones)
        current = rig.animation_data.action if rig.animation_data else None
        entry["assigned"] = current is action
    return entry


def _action_list(rig=None):
    return [action_entry(action, rig)
            for action in sorted(bpy.data.actions, key=lambda a: a.name.lower())]


def _require_action(name):
    action = bpy.data.actions.get(name)
    if action is None:
        known = ", ".join(sorted(a.name for a in bpy.data.actions)) or "none"
        suggestion = difflib.get_close_matches(name, [a.name for a in bpy.data.actions],
                                               n=1, cutoff=0.6)
        hint = " Did you mean %r?" % suggestion[0] if suggestion else ""
        raise ForgeError("No action named %r in this file.%s Actions here: %s."
                         % (name, hint, known))
    return action


@command("rigforge_action")
def cmd_rigforge_action(params):
    """Manage the Godot action library.

    ``new`` / ``list`` / ``delete`` / ``duplicate`` / ``rename`` / ``push_nla``.
    Every one of them returns the whole library in ``actions``, because the
    caller almost always wants to see the result of what it just did next to
    everything else.

    The ``-loop`` convention is enforced in *both* directions: ``loop: true``
    appends the suffix if it is missing, ``loop: false`` strips it if it is
    there, and omitting ``loop`` leaves the name exactly as given.
    """
    action_name = get_choice(
        params, "action",
        {"NEW": "new", "CREATE": "new", "LIST": "list", "DELETE": "delete",
         "REMOVE": "delete", "DUPLICATE": "duplicate", "COPY": "duplicate",
         "RENAME": "rename", "PUSH_NLA": "push_nla", "PUSHNLA": "push_nla",
         "PUSH": "push_nla"},
        "list",
    )
    loop = None
    if params.get("loop") is not None:
        loop = get_bool(params, "loop", False)
    rig = None
    if params.get("rig") is not None or action_name in ("new", "push_nla"):
        rig = _rig_for(None, params, required=(action_name == "push_nla"))

    warnings = []
    result = {"action": action_name, "loop": loop,
              "rig": rig.name if rig is not None else None}

    if action_name == "list":
        result["actions"] = _action_list(rig)
        result["count"] = len(result["actions"])
        result["loop_actions"] = [entry["name"] for entry in result["actions"]
                                  if entry["loop"]]
        result["warnings"] = warnings
        return result

    if action_name == "new":
        wanted = loop_name(get_str(params, "name"), loop)
        if bpy.data.actions.get(wanted) is not None:
            raise ForgeError(
                "An action named %r already exists. Use action='duplicate' to branch "
                "from it, or action='rename' to move it out of the way." % wanted)
        action = bpy.data.actions.new(wanted)
        action.use_fake_user = True
        assigned = False
        if rig is not None:
            assign_action(rig, action)
            assigned = True
        result.update({"name": action.name, "created": True, "assigned": assigned,
                       "entry": action_entry(action, rig)})

    elif action_name == "delete":
        action = _require_action(get_str(params, "name"))
        name = action.name
        used = nla_usage(action)
        for entry in used:
            warnings.append("%r was still in NLA track %r on %r; the strip went with it."
                            % (name, entry["track"], entry["object"]))
        for obj in bpy.data.objects:
            data = getattr(obj, "animation_data", None)
            if data is not None and data.action is action:
                data.action = None
        action.use_fake_user = False
        bpy.data.actions.remove(action)
        result.update({"name": name, "deleted": True, "was_in_nla": used})

    elif action_name == "duplicate":
        source = _require_action(get_str(params, "source"))
        raw = params.get("name")
        wanted = str(raw).strip() if isinstance(raw, str) and raw.strip() else \
            "%s_copy" % loop_name(source.name, False)
        wanted = loop_name(wanted, loop if loop is not None else is_loop(source.name))
        if bpy.data.actions.get(wanted) is not None:
            raise ForgeError("An action named %r already exists; pick another 'name'."
                             % wanted)
        copy = source.copy()
        copy.name = wanted
        copy.use_fake_user = True
        if copy.name != wanted:
            warnings.append("Blender named the copy %r." % copy.name)
        assigned = False
        if rig is not None:
            assign_action(rig, copy)
            assigned = True
        result.update({"name": copy.name, "source": source.name, "duplicated": True,
                       "assigned": assigned, "entry": action_entry(copy, rig)})

    elif action_name == "rename":
        # Two shapes: {"source": old, "name": new} renames; {"name": x, "loop": b}
        # on its own just applies (or removes) the -loop suffix in place.
        raw_source = params.get("source")
        raw_new = params.get("new_name") if params.get("new_name") is not None \
            else params.get("name")
        if isinstance(raw_source, str) and raw_source.strip():
            action = _require_action(raw_source.strip())
            if not isinstance(raw_new, str) or not raw_new.strip():
                raise ForgeError("Renaming needs a new 'name' as well as 'source'.")
            wanted = loop_name(raw_new.strip(), loop)
        else:
            action = _require_action(get_str(params, "name"))
            if loop is None:
                raise ForgeError(
                    "Rename needs either 'source' (the action) plus 'name' (its new "
                    "name), or 'name' plus 'loop' to just apply the -loop convention.")
            wanted = loop_name(action.name, loop)
        old = action.name
        if wanted != old and bpy.data.actions.get(wanted) is not None:
            raise ForgeError("An action named %r already exists." % wanted)
        action.name = wanted
        if action.name != wanted:
            warnings.append("Blender named it %r." % action.name)
        result.update({"name": action.name, "previous_name": old, "renamed": True,
                       "entry": action_entry(action, rig)})

    else:  # push_nla
        action = _require_action(get_str(params, "name"))
        if rig.animation_data is None:
            rig.animation_data_create()
        data = rig.animation_data
        existing = [track for track in data.nla_tracks if track.name == action.name]
        if existing:
            warnings.append("An NLA track named %r was already on %r; a second strip "
                            "was pushed onto a new track." % (action.name, rig.name))
        start = int(math.floor(float(action.frame_range[0])))
        track = data.nla_tracks.new()
        track.name = action.name
        strip = track.strips.new(action.name, start, action)
        strip.name = action.name
        try:
            for slot in getattr(action, "slots", ()):
                strip.action_slot = slot
                break
        except (AttributeError, TypeError, RuntimeError):
            pass
        if data.action is action:
            data.action = None
        result.update({"name": action.name, "pushed": True, "track": track.name,
                       "strip": strip.name, "entry": action_entry(action, rig)})

    result["actions"] = _action_list(rig)
    result["count"] = len(result["actions"])
    result["warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# rigforge_keyframe
# ---------------------------------------------------------------------------

EULER_MODES = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
INTERPOLATIONS = ("BEZIER", "LINEAR", "CONSTANT", "SINE", "QUAD", "CUBIC", "BACK",
                  "BOUNCE", "ELASTIC")

#: Names that are rig machinery rather than something an animator poses.
_MACHINERY = ("DEF-", "ORG-", "MCH-", "WGT-", "VIS-")

#: Rigify's per-limb IK/FK blend property, in the spellings it has shipped under.
IK_FK_KEYS = ("IK_FK", "ik_fk", "IK/FK", "IK-FK")


def control_bones(rig):
    """Pose bone names an animator would actually key, best first."""
    controls = []
    machinery = []
    for bone in rig.pose.bones:
        (machinery if bone.name.startswith(_MACHINERY) else controls).append(bone.name)
    controls.sort(key=str.lower)
    machinery.sort(key=str.lower)
    return controls, machinery


def _bone_hint(rig, wanted, limit=30):
    controls, machinery = control_bones(rig)
    ordered = controls + machinery
    close = difflib.get_close_matches(wanted, ordered, n=3, cutoff=0.5)
    if not close:
        lowered = wanted.lower()
        close = [name for name in ordered if lowered in name.lower()][:3]
    hint = ""
    if close:
        hint = " Did you mean %s?" % ", ".join(repr(name) for name in close)
    shown = controls[:limit] if controls else machinery[:limit]
    label = "Control bones" if controls else "Bones"
    more = ""
    total = len(controls) if controls else len(machinery)
    if total > len(shown):
        more = ", ... (%d in all)" % total
    return "%s %s on this rig: %s%s." % (hint, label, ", ".join(shown) or "none", more)


def resolve_pose_bone(rig, name):
    if not isinstance(name, str) or not name.strip():
        raise ForgeError("Every key needs a 'bone' name.")
    bone = rig.pose.bones.get(name.strip())
    if bone is None:
        raise ForgeError("Rig %r has no pose bone %r.%s"
                         % (rig.name, name.strip(), _bone_hint(rig, name.strip())))
    return bone


def _vector3(value, label, key):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)] * 3
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ForgeError("keys[%d].%s must be three numbers [x, y, z], got %r."
                         % (key, label, value))
    out = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ForgeError("keys[%d].%s must be three numbers, got %r." % (key, label, value))
        number = float(component)
        if not math.isfinite(number):
            raise ForgeError("keys[%d].%s must be finite, got %r." % (key, label, value))
        out.append(number)
    return out


def limbs_in_plan(rig, bone_names):
    """The limb names (``leg.L``, ``arm.R``) the keyed bones belong to.

    This is what makes the FK switch *scoped*.  Before it existed, keying one
    arm in FK moved every ``IK_FK`` on the rig — including both legs — which
    quietly turned a planted-feet leg rig into a pure-FK one and gave the clip
    the foot slide it was rigged to avoid.
    """
    found = []
    for name in bone_names:
        classified = rigforge_rig.limb_of_bone(name)
        if classified is None:
            continue
        label = rigforge_rig.limb_name(*classified)
        if label not in found:
            found.append(label)
    return found


def set_fk(rig, keyframe_at=None, limbs=None):
    """Push Rigify IK/FK blends to full FK, optionally keyframing them.

    Rigify limbs ship with the blend on IK.  An FK pose — which is what both
    ``rigforge_keyframe`` and ``rigforge_retarget`` produce — is then completely
    invisible in the deform bones, so the clip looks right in the viewport's FK
    controls and exports as a T-pose.  Switching the blend is not a nicety.

    ``limbs`` restricts the switch to those limb names (``["arm.L"]``).  Left
    ``None`` it does what it always did and switches the whole rig, because
    that is still the right answer for a full-body mocap bake.  What changed is
    who asks for ``None``: :func:`cmd_rigforge_keyframe`'s ``"auto"`` now names
    the limbs it keyed, so a leg on IK survives an arm being keyed in FK.
    """
    allowed = None
    if limbs is not None:
        allowed = {str(name).strip().lower() for name in limbs if str(name).strip()}
    switched = []
    for bone in rig.pose.bones:
        if allowed is not None:
            classified = rigforge_rig.limb_of_bone(bone.name)
            if classified is None:
                continue
            limb, side = classified
            if rigforge_rig.limb_name(limb, side).lower() not in allowed \
                    and str(limb).lower() not in allowed:
                continue
        for key in IK_FK_KEYS:
            try:
                if key not in bone.keys():
                    continue
                current = bone[key]
            except (TypeError, AttributeError, KeyError):
                continue
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                continue
            try:
                bone[key] = 1.0
            except (TypeError, ValueError, RuntimeError):
                continue
            switched.append("%s[%s]" % (bone.name, key))
            if keyframe_at is not None:
                try:
                    bone.keyframe_insert('["%s"]' % key, frame=keyframe_at)
                except (RuntimeError, TypeError):
                    pass
            break
    return switched


#: Rigify's per-limb stretch blend, on the same switch bone as ``IK_FK``.
IK_STRETCH_PROP = "IK_Stretch"

#: What a planted-foot clip keys it to, for the whole clip.
PLANTED_IK_STRETCH = 0.0


def plant_ik_stretch(rig, limbs, frames, kinds=("leg", "front_leg"),
                     value=PLANTED_IK_STRETCH):
    """Key ``IK_Stretch = 0`` across a clip, and hand back what to restore.

    **Why this is not optional.**  Rigify ships ``IK_Stretch = 1.0`` on every
    limb, which means an IK target the chain cannot reach does not clamp at full
    extension — the chain *grows*.  On a rig standing at 99.8% of its own leg
    length that turns any disagreement between where the body is and where the
    foot is planted into literal stretching of the leg mesh, and it does it
    silently: the control sits exactly where it was keyed, the deform bones are
    the ones that lie.  Measured on ``werewolf-wip-14``'s walk, the left leg
    chain grew **+261.7 mm = +32.6% of its own length** at frame 33; the same
    switch squashed the jump's legs -4.8% at the apex.

    Stretch is a cinematic effect — squash-and-stretch on a cartoon leap — and
    an effect belongs in the clip that asks for it, never in the default.  So
    every command here that plants a foot keys the property to 0 for the whole
    clip: two keys, first frame and last, which with any interpolation is a
    constant channel, and with the export bake is a constant track.

    The **live** property is put back afterwards by the caller (pass the
    returned ``restore`` list to :func:`restore_ik_stretch`), so clearing or
    unassigning the action leaves the rig exactly as the animator had it rather
    than silently re-authoring their rig defaults.

    Returns ``{"bones", "keys", "value", "restore", "missing"}``.
    """
    bones = []
    restore = []
    missing = []
    keys = 0
    wanted = tuple(kinds)
    for entry in limbs:
        if entry.get("limb") not in wanted:
            continue
        bone = rig.pose.bones.get(entry.get("switch_bone") or "")
        if bone is None:
            continue
        if IK_STRETCH_PROP not in bone.keys():
            missing.append(bone.name)
            continue
        try:
            prior = float(bone[IK_STRETCH_PROP])
        except (TypeError, ValueError):  # pragma: no cover - odd ID property
            missing.append(bone.name)
            continue
        try:
            bone[IK_STRETCH_PROP] = float(value)
        except (KeyError, TypeError, ValueError, RuntimeError):  # pragma: no cover
            missing.append(bone.name)
            continue
        restore.append((bone.name, prior))
        for frame in frames:
            try:
                bone.keyframe_insert('["%s"]' % IK_STRETCH_PROP, frame=int(frame))
                keys += 1
            except (RuntimeError, TypeError):  # pragma: no cover - no action yet
                break
        bones.append(bone.name)
    return {"bones": bones, "keys": keys, "value": float(value),
            "restore": restore, "missing": sorted(set(missing))}


def restore_ik_stretch(rig, restore):
    """Put the live ``IK_Stretch`` values back where :func:`plant_ik_stretch` found them."""
    put_back = {}
    for name, prior in restore or ():
        bone = rig.pose.bones.get(name)
        if bone is None or IK_STRETCH_PROP not in bone.keys():
            continue
        try:
            bone[IK_STRETCH_PROP] = float(prior)
        except (KeyError, TypeError, ValueError, RuntimeError):  # pragma: no cover
            continue
        put_back[name] = round(float(prior), 6)
    return put_back


def ik_stretch_report(planted, put_back):
    """The block every planted-foot command returns, so a gate can read it."""
    return {
        "property": IK_STRETCH_PROP,
        "keyed_to": planted["value"],
        "bones": list(planted["bones"]),
        "keys": planted["keys"],
        "restored_to": put_back,
        "without_the_property": planted["missing"],
    }


@command("rigforge_keyframe")
def cmd_rigforge_keyframe(params):
    """Batch keyframing on the control rig — the described-motion command.

    A ``keys`` entry is ``{"bone", "frame", "rotation_euler_deg"?, "location"?,
    "scale"?}``; at least one channel per entry.  Rotations are **degrees** in the
    bone's own local space and force the bone into ``XYZ`` euler mode (reported
    in ``rotation_modes``), because a Rigify control defaults to quaternion and
    describing a pose in quaternions is not a thing anybody does out loud.
    """
    started = time.monotonic()
    warnings = []
    rig = _rig_for(None, params, key="rig", required=True)

    raw_keys = params.get("keys")
    if not isinstance(raw_keys, (list, tuple)) or not raw_keys:
        raise ForgeError(
            "'keys' must be a non-empty list of "
            '{"bone", "frame", "rotation_euler_deg"/"location"/"scale"} objects.')
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS}, "BEZIER")
    clear = get_bool(params, "clear", False)
    fk_raw = params.get("fk_switch", "auto")
    if isinstance(fk_raw, str):
        if fk_raw.strip().lower() != "auto":
            raise ForgeError("'fk_switch' must be true, false or \"auto\".")
        fk_switch = "auto"
    elif fk_raw is None:
        fk_switch = "auto"
    else:
        fk_switch = get_bool(params, "fk_switch", True)

    wanted_action = get_str(params, "action")
    loop = None
    if params.get("loop") is not None:
        loop = get_bool(params, "loop", False)
    wanted_action = loop_name(wanted_action, loop)

    action = bpy.data.actions.get(wanted_action)
    created = False
    if action is None:
        action = bpy.data.actions.new(wanted_action)
        action.use_fake_user = True
        created = True
    cleared = 0
    if clear:
        cleared = clear_action(action)

    frames = []
    bones_touched = []
    modes = {}
    fk_switched = []
    fk_limbs = None
    keys_set = 0
    touched_curves = {}

    with object_mode():
        assign_action(rig, action)
        # Parse and validate everything before touching a single channel: a
        # typo in keys[7] must not leave keys[0..6] half applied.
        plan = []
        for index, entry in enumerate(raw_keys):
            if not isinstance(entry, dict):
                raise ForgeError("keys[%d] must be an object, got %s."
                                 % (index, type(entry).__name__))
            bone = resolve_pose_bone(rig, entry.get("bone"))
            if entry.get("frame") is None:
                raise ForgeError("keys[%d] is missing 'frame'." % index)
            frame = get_int(entry, "frame", minimum=-1_000_000, maximum=1_000_000)
            channels = {}
            if entry.get("rotation_euler_deg") is not None:
                channels["rotation_euler"] = [math.radians(v) for v in _vector3(
                    entry["rotation_euler_deg"], "rotation_euler_deg", index)]
            if entry.get("location") is not None:
                channels["location"] = _vector3(entry["location"], "location", index)
            if entry.get("scale") is not None:
                channels["scale"] = _vector3(entry["scale"], "scale", index)
            if not channels:
                raise ForgeError(
                    "keys[%d] (bone %r, frame %d) sets nothing: give it at least one of "
                    "rotation_euler_deg, location or scale."
                    % (index, bone.name, frame))
            plan.append((bone, frame, channels))

        fk_bones = [bone.name for bone, _f, _c in plan if "_fk" in bone.name.lower()]
        scoped = None
        if fk_switch is True:
            # Explicit: the caller asked for the whole rig and gets it.
            scoped = None
        elif fk_switch == "auto" and fk_bones:
            # Scoped: only the limbs whose FK controls are actually in this
            # pass. Switching the rest would silently take a leg off IK, and a
            # leg keyed in FK is the foot-slide anti-pattern (animation_check
            # measures it in millimetres).
            scoped = limbs_in_plan(rig, fk_bones)
        fk_limbs = scoped
        if fk_switch is True or (fk_switch == "auto" and fk_bones):
            at = min(frame for _bone, frame, _c in plan)
            fk_switched = set_fk(rig, keyframe_at=at, limbs=scoped)
            if fk_switched:
                warnings.append(
                    "Rigify's IK/FK blend was moved to full FK on %d limb(s) and "
                    "keyframed, so these FK poses actually reach the deform bones: %s."
                    % (len(fk_switched), ", ".join(fk_switched))
                    + ("" if scoped is None else
                       " Only %s was switched; every other limb kept its mode, because "
                       "an FK-keyed leg cannot plant a foot."
                       % ", ".join(scoped)))
            left = [entry["name"] for entry in rigforge_rig.ik_limbs(rig)
                    if entry["mode"] == "ik"]
            if scoped is not None and left and any(
                    name.startswith("leg") for name in left):
                warnings.append(
                    "%s stayed on IK. Key the feet through their IK targets "
                    "(foot_ik.L/R) or use rigforge_walk; an FK-keyed leg slides."
                    % ", ".join(sorted(left)))

        for bone, frame, channels in plan:
            if "rotation_euler" in channels and bone.rotation_mode not in EULER_MODES:
                modes[bone.name] = bone.rotation_mode
                bone.rotation_mode = "XYZ"
            for path, value in channels.items():
                setattr(bone, path, value)
                data_path = 'pose.bones["%s"].%s' % (bone.name, path)
                if not bone.keyframe_insert(path, frame=frame):
                    raise ForgeError(
                        "Blender refused a %s key on %r at frame %d."
                        % (path, bone.name, frame))
                keys_set += 1
                for axis in range(3):
                    touched_curves.setdefault((data_path, axis), set()).add(frame)
            frames.append(frame)
            if bone.name not in bones_touched:
                bones_touched.append(bone.name)

        # interpolation is per keyframe point, and only on the points we made
        applied = 0
        for (data_path, axis), at in touched_curves.items():
            curve = find_fcurve(action, data_path, axis)
            if curve is None:
                continue
            for point in curve.keyframe_points:
                if int(round(point.co.x)) in at:
                    point.interpolation = interpolation
                    applied += 1
            try:
                curve.update()
            except (AttributeError, RuntimeError):
                pass

    if modes:
        warnings.append(
            "Rotation mode changed to XYZ euler on %s (they were %s); "
            "rotation_euler_deg is euler by contract."
            % (", ".join(sorted(modes)),
               ", ".join(sorted(set(modes.values())))))

    span = tuple(action.frame_range)
    return {
        "rig": rig.name,
        "action": action.name,
        "created": created,
        "loop": is_loop(action.name),
        "keys_set": keys_set,
        "keys": len(raw_keys),
        "bones": bones_touched,
        "frame_range": [min(frames), max(frames)],
        "action_frame_range": [round(float(span[0]), 4), round(float(span[1]), 4)],
        "interpolation": interpolation,
        "interpolated_points": applied,
        "cleared_fcurves": cleared,
        "fcurves": len(rigforge_rig.action_fcurves(action)),
        "rotation_modes": modes,
        "fk_switched": fk_switched,
        "fk_limbs": fk_limbs,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# rigforge_walk — locomotion on the IK targets
# ---------------------------------------------------------------------------
#
# The walk cycle is where FK keyframing stops being a stylistic choice and
# starts being a bug.  A leg keyed on ``thigh_fk`` / ``shin_fk`` has nothing
# holding the foot on the ground between keys: the foot's world position is
# whatever the two rotations happen to multiply out to, it drifts every frame
# of "stance", and the result is the oldest artefact in game animation — the
# character moon-walking through its own footsteps.  The fix is not better
# keys, it is a different channel: **key the foot IK target's position, and
# hold it still while the foot is down.**
#
# So this command authors the four classic keys of a walk — contact, down,
# pass, up — as *foot target positions* rather than leg rotations, with the
# stance phase world-locked by construction, and layers the parts that really
# are rotations (hip bob and sway, torso counter-rotation, arm swing, the
# heel/ball foot roll) on top.
#
# Two modes, one geometry:
#
# * ``travel: true`` (the default) advances the ``root`` bone by one stride per
#   cycle and leaves each planted foot at a **fixed world position**.  That is
#   a root-motion clip: ``rigforge_export_godot`` with ``root_motion: true``
#   ships the travel on the root track, and ``animation_check`` measures stance
#   drift straight against zero.
# * ``travel: false`` is the same cycle with the body's travel subtracted — the
#   treadmill clip an engine plays while its own controller moves the
#   character.  The feet must move backwards during stance; what must not
#   happen is that they move at a *different* speed from each other or from the
#   character, so ``animation_check``'s ``in_place`` mode measures the residual
#   after a single shared velocity is removed.
#
# Both loop seamlessly: the last frame repeats the first, one stride along.

#: How much of the cycle each foot spends on the ground.  0.62 is a walk (the
#: two stance phases overlap, which is what double support *is*); below 0.5 the
#: gait is a run and both feet leave the ground.
DEFAULT_STANCE_FRACTION = 0.62

#: Defaults as fractions of the leg's length, so the same numbers fit a
#: 30 cm figurine and a 2 m ogre.
STEP_LENGTH_RATIO = 0.40      #: body travel per step (half a stride)
STEP_HEIGHT_RATIO = 0.10      #: how high the swinging foot lifts
HIP_DROP_RATIO = 0.035        #: the two-per-cycle vertical bob
HIP_SWAY_RATIO = 0.030        #: the one-per-cycle weight shift onto the stance leg

#: How far below standing height the hips sit for the whole cycle.  Not a
#: stylistic crouch: a rig at rest has its legs all but straight, so a hip left
#: at standing height has no bend to spend and the leg cannot reach a stride's
#: worth of forward step without Rigify stretching it — which reads on the
#: deform bones as the exact foot slide this command exists to avoid.  Walking
#: humans flex the knee through stance for the same reason.
HIP_LOWER_RATIO = 0.045

#: ...and how far the hips may be lowered *automatically* to buy a longer
#: stride before the stride is shortened instead.  Past this it stops reading
#: as a walk and starts reading as a crouch, which is a different clip.
MAX_HIP_LOWER_RATIO = 0.20

#: The leg may reach this fraction of its own length before Rigify's stretch
#: starts making up the difference — and a stretched leg does not reach its
#: target, which reads as foot slide on the deform bones.
DEFAULT_REACH_MARGIN = 0.97

#: How much of the leg's own measured limit the reach clamp keeps in hand when
#: it fits the ask inside it, so the correction lands inside the reach rather
#: than on its exact edge - where the next frame's bob puts it back outside.
WALK_REACH_MARGIN = 0.01

#: Foot-roll shape, as fractions of the cycle.
ROLL_FLAT_AT = 0.12           #: heel strike is over and the foot is flat
ROLL_LIFT_FOR = 0.18          #: how long the heel-off roll takes, before toe-off

DEFAULT_CYCLE_FRAMES = 32
DEFAULT_ARM_SWING_DEG = 26.0
DEFAULT_ELBOW_BEND_DEG = 14.0
DEFAULT_FOOT_ROLL_DEG = 22.0
DEFAULT_HIP_TWIST_DEG = 6.0

#: Where each arm's **forward** peak sits relative to its own side's foot
#: strike, in degrees of the cycle.  180 is the whole of classical gait: the
#: left arm is forward when the left foot is *back*, which is to say when the
#: RIGHT foot strikes.  It is not a style setting — an arm swinging with the leg
#: on its own side (0 here) is the oldest amateur tell in walk animation, and it
#: is what this rig authored until wip-15.  Exposed as a parameter only so the
#: defect can be reconstructed on purpose: ``animation_check``'s
#: ``gait_opposition`` gate is built against it.
DEFAULT_ARM_PHASE_DEG = 180.0

#: How far **ahead of the hip joint** the striking heel lands, as a fraction of
#: the stride.
#:
#: **Credibility tier: derived from the classical walk-cycle reference.**  A
#: walker's heel contacts 25–35% of a stride in front of the pelvis and the
#: torso then passes over the planted foot through mid-stance; a foot that lands
#: under or behind the body is the character walking into its own feet.  0.30 is
#: the middle of that band, and it is measured back out by
#: ``animation_check``'s ``strike_lead`` gate rather than asserted.
DEFAULT_STRIKE_LEAD = 0.30

#: The test rotation the arm's forward direction is **asked of the rig** with.
#: Which way a positive rotation about the walk frame's ``right`` axis carries
#: the hand depends on the arm's rest pose, and a first-order cross product gets
#: it wrong on a rig whose arms are not a plumb hang — so it is probed, once,
#: the same way ``max_span`` probes the leg rather than summing a rest chain.
ARM_SIGN_PROBE_DEG = 10.0

#: The torso control, best first.
TORSO_CONTROLS = ("torso", "hips", "chest", "spine_fk")
ROOT_CONTROLS = ("root", "root.001")


def _smoothstep(value):
    value = min(1.0, max(0.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def _world_matrix(rig, pose_bone):
    return rig.matrix_world @ pose_bone.matrix


def _rest_world(rig, pose_bone):
    """The bone's **rest** matrix in world space — pose-independent."""
    return rig.matrix_world @ pose_bone.bone.matrix_local


def _set_world(rig, pose_bone, matrix):
    """Put a pose bone at a world matrix, whatever its parent chain is doing.

    ``PoseBone.matrix`` is armature space and its setter resolves the parent
    for us, which is the only reason this command can key a foot target that
    hangs off a driven ``SWITCH_PARENT`` mechanism without knowing it exists.
    """
    pose_bone.matrix = rig.matrix_world.inverted_safe() @ matrix


def _key_transform(pose_bone, frame, rotation=True, scale=False):
    """Keyframe location (+ the rotation channel that matches the bone's mode)."""
    count = 0
    pose_bone.keyframe_insert("location", frame=frame)
    count += 3
    if rotation:
        if pose_bone.rotation_mode == "QUATERNION":
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
            count += 4
        elif pose_bone.rotation_mode == "AXIS_ANGLE":
            pose_bone.keyframe_insert("rotation_axis_angle", frame=frame)
            count += 4
        else:
            pose_bone.keyframe_insert("rotation_euler", frame=frame)
            count += 3
    if scale:
        pose_bone.keyframe_insert("scale", frame=frame)
        count += 3
    return count


def _rotate_about(matrix, axis, angle, pivot):
    """``matrix`` rotated by ``angle`` about the world ``axis`` through ``pivot``."""
    rotation = Matrix.Rotation(angle, 4, axis)
    return (Matrix.Translation(pivot) @ rotation @ Matrix.Translation(-pivot)) @ matrix


def locomotion_frame(rig, limbs):
    """The rig's own walking axes and measurements, derived rather than assumed.

    ``forward`` is the direction the **toes** point (ankle to toe tip, flattened
    onto the ground plane), so a character built facing any way walks the way it
    faces.  ``leg_length`` is hip to ankle at rest.  Nothing here is a constant
    in metres: every default downstream is a fraction of these.
    """
    up = Vector((0.0, 0.0, 1.0))
    forwards = []
    feet = {}
    leg_lengths = []
    for entry in limbs:
        if entry["limb"] != "leg":
            continue
        target = rig.pose.bones.get(entry["ik_target"])
        if target is None:
            continue
        ankle = _rest_world(rig, target).translation.copy()
        ball = None
        tip = None
        toe = rig.pose.bones.get(entry["roll_pivots"].get("toe") or "")
        if toe is not None:
            rest = _rest_world(rig, toe)
            ball = rest.translation.copy()
            tip = (rest @ Matrix.Translation((0.0, toe.bone.length, 0.0))).translation
        if ball is None and entry["tip_bone"]:
            bone = rig.pose.bones.get(entry["tip_bone"])
            if bone is not None:
                rest = _rest_world(rig, bone)
                ball = rest.translation.copy()
                tip = (rest @ Matrix.Translation((0.0, bone.bone.length, 0.0))).translation
        if ball is None:
            ball = ankle.copy()
        if tip is None:
            tip = ball.copy()
        direction = Vector((tip.x - ankle.x, tip.y - ankle.y, 0.0))
        if direction.length > 1e-6:
            forwards.append(direction.normalized())
        hip = None
        for name in entry["fk_chain"]:
            bone = rig.pose.bones.get(name)
            if bone is not None:
                hip = _rest_world(rig, bone).translation.copy()
                break
        if hip is not None:
            leg_lengths.append((hip - ankle).length)
        feet[entry["name"]] = {
            "limb": entry,
            "side": entry["side"],
            "target": entry["ik_target"],
            "ankle": ankle,
            "ball": ball,
            "hip": hip,
            "rest": _rest_world(rig, target),
            "heel_pivot": entry["roll_pivots"].get("heel"),
            # The toe control rotates about the ball, so it is the one pivot on
            # the foot that can do ankle/toe work without moving the point the
            # foot-slide gate measures. See JUMP_LOAD_TOE_LIFT_DEG.
            "toe_pivot": entry["roll_pivots"].get("toe"),
        }
    if not forwards:
        forward = Vector((0.0, -1.0, 0.0))
    else:
        forward = Vector((0.0, 0.0, 0.0))
        for vector in forwards:
            forward += vector
        forward = forward.normalized() if forward.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    right = forward.cross(up)
    right = right.normalized() if right.length > 1e-6 else Vector((1.0, 0.0, 0.0))
    leg_length = sum(leg_lengths) / len(leg_lengths) if leg_lengths else 1.0
    return {"forward": forward, "right": right, "up": up,
            "leg_length": leg_length, "feet": feet}


def stance_reach_factor(stance_fraction, strike_lead):
    """How far the ankle swings from the hip during stance, per metre of step.

    The foot is planted ``strike_lead`` of a stride in front of the hip joint at
    the strike and the body then walks over it, so by toe-off it is
    ``stance_fraction - strike_lead`` of a stride behind.  The leg has to make
    whichever of those two is longer, and both are fractions of the **stride**,
    which is two steps — hence the 2.

    Before the strike lead was authored this was simply ``stance_fraction``,
    which is the same number only when the strike lands at exactly half the
    stance (``strike_lead == stance_fraction / 2``).
    """
    return 2.0 * max(float(strike_lead),
                     max(0.0, float(stance_fraction) - float(strike_lead)))


def _reach_limit(frame_info, stance_fraction, hip_low, margin,
                 reach_factor=None):
    """The longest step these legs can take without Rigify stretching them.

    A target the leg cannot reach is worse than a short stride: the foot never
    arrives where it was keyed, so it slides on the deform bones while the
    control sits perfectly still, and the metric blames the animation for a
    reach problem.  Solved in the triangle: the hip is ``drop`` above the ankle
    at its lowest, the leg may span ``margin * leg_length``, so the horizontal
    offset is bounded by the remaining side.  ``None`` when no leg has a hip to
    measure from.
    """
    factor = (stance_fraction if reach_factor is None else reach_factor)
    longest = None
    for foot in frame_info["feet"].values():
        if foot["hip"] is None:
            continue
        drop = (foot["hip"].z - hip_low) - foot["ankle"].z
        span = margin * frame_info["leg_length"]
        if drop >= span:
            limit = 0.0
        else:
            limit = math.sqrt(max(0.0, span * span - drop * drop)) \
                / max(factor, 1e-6)
        longest = limit if longest is None else min(longest, limit)
    return longest


def _crouch_for(frame_info, step_length, stance_fraction, hip_drop, hip_lower,
                margin, max_lower, reach_factor=None):
    """Bend the knees as much as the asked-for stride needs, then clamp.

    A rig at rest stands with its legs all but straight, so *every* stride
    longer than a shuffle needs the hips lowered — a real walker's do the same
    thing, which is why the knee is never locked through stance.  So the order
    of preference is: deepen the crouch (up to ``max_lower``), and only then
    shorten the step.  Returns ``(step_length, hip_lower, clamped, deepened)``.
    """
    factor = (stance_fraction if reach_factor is None else reach_factor)
    limit = _reach_limit(frame_info, stance_fraction, hip_lower + hip_drop, margin,
                         factor)
    if limit is None or step_length <= limit:
        return step_length, hip_lower, False, False

    deepened = False
    needed = step_length * factor
    for foot in frame_info["feet"].values():
        if foot["hip"] is None:
            continue
        span = margin * frame_info["leg_length"]
        if needed >= span:
            # Further than the leg is long, however deep the crouch. Take every
            # millimetre the cap allows and let the clamp below say the rest.
            wanted = max_lower
        else:
            standing = foot["hip"].z - foot["ankle"].z - hip_drop
            wanted = standing - math.sqrt(max(0.0, span * span - needed * needed))
        if wanted > hip_lower:
            hip_lower = min(max_lower, wanted)
            deepened = True

    limit = _reach_limit(frame_info, stance_fraction, hip_lower + hip_drop, margin,
                         factor)
    if limit is None:
        return step_length, hip_lower, False, deepened
    # A hair under, after the crouch was solved for this very step, is the
    # solution landing on its own boundary - not a clamp worth a warning.
    if step_length > limit * (1.0 + 1e-6):
        return limit, hip_lower, True, deepened
    return min(step_length, limit), hip_lower, False, deepened


def _foot_offset(u, stance_fraction, stride, step_height, plant):
    """One foot's ground-plane offset and lift at cycle phase ``u``.

    Returns ``(along_forward, lift)`` relative to the foot's rest position,
    before the body's own travel is added.  ``plant`` is where this foot stands
    for *this* step — computed by the caller from the strike lead, because where
    a foot lands relative to the body is the gait, not a detail of the curve.

    Stance is a **constant**, which is the entire point: between contact and
    toe-off this function returns the same number every frame, so the key it
    produces is the same key, so the foot cannot drift.  One swing carries the
    foot forward by a whole stride, which is what puts the next plant one stride
    down the floor from this one.
    """
    if u <= stance_fraction:
        return plant, 0.0
    progress = (u - stance_fraction) / max(1e-6, 1.0 - stance_fraction)
    along = plant + stride * _smoothstep(progress)
    lift = step_height * math.sin(math.pi * progress)
    return along, lift


def _foot_roll(u, stance_fraction, roll_deg):
    """Heel-control X rotation, in degrees, at cycle phase ``u``.

    Positive rolls over the **ball** (heel-off, the ball and toe stay planted
    to the millimetre); negative pivots about the **heel** (the strike, toe
    up).  Zero through the middle of stance, which is the window the foot-slide
    metric finds.
    """
    strike = -roll_deg * 0.6
    if u < ROLL_FLAT_AT:
        return strike * (1.0 - u / ROLL_FLAT_AT)
    lift_from = max(ROLL_FLAT_AT, stance_fraction - ROLL_LIFT_FOR)
    if u <= lift_from:
        return 0.0
    if u <= stance_fraction:
        return roll_deg * (u - lift_from) / max(1e-6, stance_fraction - lift_from)
    swing = (u - stance_fraction) / max(1e-6, 1.0 - stance_fraction)
    # roll_deg at toe-off -> back through zero -> the strike angle at contact
    return roll_deg + (strike - roll_deg) * _smoothstep(swing)


@command("rigforge_walk")
def cmd_rigforge_walk(params):
    """Author a walk cycle on the leg IK targets, with the stance feet planted.

    ``rigforge_walk {"rig"?, "action"?, "cycle_frames"?, "step_length"?,
    "step_height"?, "stance_fraction"?, "hip_drop"?, "hip_sway"?,
    "hip_twist_deg"?, "arm_swing_deg"?, "arm_phase_deg"?, "elbow_bend_deg"?,
    "foot_roll_deg"?, "strike_lead"?, "travel"?, "loop"?, "clear"?,
    "interpolation"?, "stride_width"?, "reach_margin"?}``

    Every length parameter is metres and every one of them defaults to a
    fraction of *this* rig's leg, so the command works on a figurine and an
    ogre without being told which it is.

    Two of the parameters are gait rather than styling, and both have a gate in
    ``animation_check`` that measures them back off the result:

    * ``arm_phase_deg`` (default 180) — where each arm's forward peak sits
      relative to its **own** side's foot strike.  180 is contralateral swing:
      left arm forward on the right foot's contact.  0 authors the amateur
      same-side swing on purpose, which is how ``gait_opposition`` is proved
      red;
    * ``strike_lead`` (default 0.30) — how far in front of the hip joint the
      **heel** lands, as a fraction of the stride.  0 lands it under the body,
      which is how ``strike_lead`` is proved red.
    """
    started = time.monotonic()
    warnings = []
    rig = _rig_for(None, params, key="rig", required=True)
    scene = get_scene()

    cycle_frames = get_int(params, "cycle_frames", DEFAULT_CYCLE_FRAMES,
                           minimum=4, maximum=600)
    stance_fraction = get_float(params, "stance_fraction", DEFAULT_STANCE_FRACTION,
                                minimum=0.2, maximum=0.95)
    travel = get_bool(params, "travel", True)
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS}, "LINEAR")
    clear = get_bool(params, "clear", True)
    reach_margin = get_float(params, "reach_margin", DEFAULT_REACH_MARGIN,
                             minimum=0.5, maximum=1.2)
    arm_swing = math.radians(get_float(params, "arm_swing_deg", DEFAULT_ARM_SWING_DEG,
                                       minimum=0.0, maximum=90.0))
    arm_phase_deg = get_float(params, "arm_phase_deg", DEFAULT_ARM_PHASE_DEG,
                              minimum=0.0, maximum=360.0)
    arm_phase = math.radians(arm_phase_deg)
    elbow_bend = math.radians(get_float(params, "elbow_bend_deg", DEFAULT_ELBOW_BEND_DEG,
                                        minimum=0.0, maximum=120.0))
    # 0.45 rather than 0.5: past half the stance the foot is behind the body for
    # most of the plant instead of in front of it for most of it, and the leg
    # runs out of reach forward before it runs out backward.
    strike_lead = get_float(params, "strike_lead", DEFAULT_STRIKE_LEAD,
                            minimum=0.0, maximum=0.45)
    roll_deg = get_float(params, "foot_roll_deg", DEFAULT_FOOT_ROLL_DEG,
                         minimum=0.0, maximum=60.0)
    hip_twist = math.radians(get_float(params, "hip_twist_deg", DEFAULT_HIP_TWIST_DEG,
                                       minimum=0.0, maximum=45.0))

    limbs = rigforge_rig.ik_limbs(rig)
    legs = [entry for entry in limbs if entry["limb"] == "leg"]
    if len(legs) < 2:
        raise ForgeError(
            "rigforge_walk keys the legs through their IK targets, and %r has %d of "
            "them (it needs foot_ik.L and foot_ik.R with an IK_FK switch on "
            "thigh_parent.L/R). Generate the rig with rigforge_generate_rig, or run "
            "rigforge_ik to see what this rig actually has. Keying a walk on "
            "thigh_fk/shin_fk instead is the foot-slide anti-pattern this command "
            "exists to replace." % (rig.name, len(legs)))

    info = locomotion_frame(rig, limbs)
    leg_length = info["leg_length"]
    forward, right, up = info["forward"], info["right"], info["up"]

    step_length = get_float(params, "step_length", STEP_LENGTH_RATIO * leg_length,
                            minimum=1e-4)
    step_height = get_float(params, "step_height", STEP_HEIGHT_RATIO * leg_length,
                            minimum=0.0)
    hip_drop = get_float(params, "hip_drop", HIP_DROP_RATIO * leg_length, minimum=0.0)
    hip_sway = get_float(params, "hip_sway", HIP_SWAY_RATIO * leg_length, minimum=0.0)
    hip_lower = get_float(params, "hip_lower", HIP_LOWER_RATIO * leg_length, minimum=0.0)
    stride_width = get_float(params, "stride_width", 0.0)

    max_lower = get_float(params, "max_hip_lower", MAX_HIP_LOWER_RATIO * leg_length,
                          minimum=0.0)
    if params.get("hip_lower") is not None:
        max_lower = hip_lower  # asked for explicitly: the stride gives way instead
    reach_factor = stance_reach_factor(stance_fraction, strike_lead)
    step_length, hip_lower, clamped, deepened = _crouch_for(
        info, step_length, stance_fraction, hip_drop, hip_lower, reach_margin,
        max_lower, reach_factor)
    if deepened:
        warnings.append(
            "The hips were lowered to %.3f m below standing height so the legs can "
            "reach a %.3f m step without stretching. A rig at rest stands with its "
            "legs straight; a walker's knees are not locked, and a stretched leg does "
            "not arrive where it was keyed."
            % (hip_lower, step_length))
    if clamped:
        warnings.append(
            "step_length was shortened to %.3f m: any longer and the leg cannot reach "
            "its own IK target at contact even with the hips at %.3f m, so Rigify's "
            "stretch makes up the difference and the foot slides on the deform bones "
            "while the control sits still." % (step_length, hip_lower))
    stride = 2.0 * step_length

    wanted_action = get_str(params, "action", "walk")
    loop = get_bool(params, "loop", True) if params.get("loop") is not None else True
    wanted_action = loop_name(wanted_action, loop)

    action = bpy.data.actions.get(wanted_action)
    created = False
    if action is None:
        action = bpy.data.actions.new(wanted_action)
        action.use_fake_user = True
        created = True
    cleared = 0

    frames = list(range(1, cycle_frames + 2))
    keys_set = 0
    #: ``(frame, limb, hip-to-ankle span, rest reach, in stance)`` — the walk's
    #: own version of the jump's extension track, and the thing the reach clamp
    #: below closes the loop on.
    span_track = []
    #: The deform hip/ankle the span is measured between, and their rest chain.
    probe_legs = jump_legs(rig, limbs, info)
    #: Where the ankle sits relative to its own IK target at rest.  Rigify's
    #: ``foot_ik`` head and ``DEF-foot`` head are **not** the same point - on
    #: the synthetic rig they are 34 mm apart - so "did the foot arrive?" has
    #: to be asked about the ankle's *intended* position, not the control's.
    #: Without this the miss reads a constant 34 mm at every frame of every
    #: clip and a clamp driving it grinds the stride away for nothing.
    rest_gap = {}
    for _name, _leg in probe_legs.items():
        _target = rig.pose.bones.get(_leg["foot"]["target"])
        if _target is None:
            continue
        rest_gap[_name] = (_rest_world(rig, rig.pose.bones[_leg["ankle_bone"]])
                           .translation
                           - _rest_world(rig, _target).translation)
    bones_touched = []
    modes = {}
    previous_frame = scene.frame_current

    def touched(name):
        if name not in bones_touched:
            bones_touched.append(name)

    with object_mode():
        assign_action(rig, action)
        if clear:
            cleared = clear_action(action)

        # Legs on IK, arms on FK, poles live - and keyframed at frame 1, so the
        # export bake resolves the same rig the animator saw.
        convention = rigforge_rig.apply_ik_convention(
            rig, poles=get_bool(params, "poles", True), keyframe_at=frames[0])
        for entry in convention["limbs"]:
            touched(entry["switch_bone"])

        # Every stance phase of a walk is a planted foot, so the legs may not
        # stretch to reach a target: see plant_ik_stretch.
        planted_stretch = plant_ik_stretch(rig, convention["limbs"],
                                           (frames[0], frames[-1]))
        keys_set += planted_stretch["keys"]
        for name in planted_stretch["bones"]:
            touched(name)

        root = None
        for name in ROOT_CONTROLS:
            if name in rig.pose.bones:
                root = rig.pose.bones[name]
                break
        if root is None and travel:
            travel = False
            warnings.append(
                "This rig has no 'root' bone, so the cycle was authored in place "
                "(travel=false). Godot's root-motion track wants a bone the skeleton "
                "does not deform with; without one there is nothing to put it on.")
        torso = None
        for name in TORSO_CONTROLS:
            if name in rig.pose.bones:
                torso = rig.pose.bones[name]
                break
        if torso is None:
            warnings.append("No torso control (torso/hips/chest) — the hips were not "
                            "keyed, so the walk has no weight shift.")

        arms = []
        for entry in limbs:
            if entry["limb"] != "arm":
                continue
            upper = next((rig.pose.bones[n] for n in entry["fk_chain"]
                          if "upper_arm" in n and n in rig.pose.bones), None)
            fore = next((rig.pose.bones[n] for n in entry["fk_chain"]
                         if "forearm" in n and n in rig.pose.bones), None)
            if upper is not None:
                arms.append({"side": entry["side"], "upper": upper, "fore": fore,
                             "upper_rest": _rest_world(rig, upper),
                             "fore_rest": _rest_world(rig, fore) if fore else None,
                             # Which way a positive rotation about `right` takes
                             # the hand. Probed below, not assumed.
                             "forward_sign": 1.0, "forward_probe_mm": None})
        if not arms:
            warnings.append("No FK arm control was found, so the arms do not swing.")

        rest_root = _rest_world(rig, root) if root is not None else None
        rest_torso = _rest_world(rig, torso) if torso is not None else None
        heels = {}
        for name, foot in info["feet"].items():
            pivot = foot.get("heel_pivot")
            bone = rig.pose.bones.get(pivot or "")
            if bone is None:
                continue
            if bone.rotation_mode == "QUATERNION":
                modes[bone.name] = bone.rotation_mode
                bone.rotation_mode = "XYZ"
            heels[name] = bone

        # --- how far this leg actually goes, asked of the rig itself ---------
        #
        # Not the rest chain: `jump_legs` sums hip-to-knee-to-ankle along the
        # pre-bent rest pose, and the IK straightens past that - measured on
        # the synthetic rig, 528.0 mm delivered against a 510.3 mm summed
        # chain, a 3.5% gap that a clamp built on the chain can never close.
        # So the leg is asked: the target is pushed a long way down, the
        # solver does what it does with IK_Stretch keyed to 0, and the
        # hip-to-ankle distance that comes back IS the limit. Two evaluations,
        # once, before anything is keyed.
        max_span = {}
        for name in sorted(probe_legs):
            leg = probe_legs[name]
            target = rig.pose.bones.get(leg["foot"]["target"])
            if target is None:
                continue
            rest = _rest_world(rig, target)
            far = rest.copy()
            far.translation = rest.translation - up * (2.0 * leg["reach"])
            _set_world(rig, target, far)
            refresh_view_layer()
            hip = rig.matrix_world @ rig.pose.bones[leg["hip_bone"]].head
            ankle = rig.matrix_world @ rig.pose.bones[leg["ankle_bone"]].head
            max_span[name] = (hip - ankle).length
            target.matrix_basis.identity()
        refresh_view_layer()

        # --- which way a positive rotation swings the hand, asked of the rig --
        #
        # The swing is a rotation about the walk frame's `right` axis, and
        # whether +10 degrees about it carries the hand forward or backward
        # depends on the arm's rest pose. A first-order cross product answers
        # that wrongly on any arm that is not a plumb hang (measured on the
        # synthetic biped: the cross product says forward, the rig delivers
        # backward), and an arm swinging the wrong way is an arm swinging with
        # the leg on its own side no matter what the phase says. So the rig is
        # asked, once, exactly as `max_span` asks the leg.
        for arm in arms:
            upper = arm["upper"]
            tip = arm["fore"] if arm["fore"] is not None else upper
            upper.matrix_basis.identity()
            if arm["fore"] is not None:
                arm["fore"].matrix_basis.identity()
            refresh_view_layer()
            before = ((rig.matrix_world @ tip.tail)
                      - (rig.matrix_world @ upper.head)).dot(forward)
            probe = _rotate_about(arm["upper_rest"], right,
                                  math.radians(ARM_SIGN_PROBE_DEG),
                                  arm["upper_rest"].translation)
            _set_world(rig, upper, probe)
            upper.location = (0.0, 0.0, 0.0)
            refresh_view_layer()
            after = ((rig.matrix_world @ tip.tail)
                     - (rig.matrix_world @ upper.head)).dot(forward)
            moved = after - before
            arm["forward_sign"] = -1.0 if moved < 0.0 else 1.0
            arm["forward_probe_mm"] = round(moved * M_TO_MM, 3)
            upper.matrix_basis.identity()
        if arms and all(abs(arm["forward_probe_mm"] or 0.0) < 0.5 for arm in arms):
            warnings.append(
                "A %.0f degree test rotation moved no hand more than half a "
                "millimetre along the walk's forward axis, so which way these arms "
                "swing could not be measured and the swing was authored on the "
                "positive rotation. Arms posed along the swing axis (a flat T-pose) "
                "have no forward component to rotate."
                % ARM_SIGN_PROBE_DEG)
        refresh_view_layer()

        # Phase offset: the left foot contacts at the top of the cycle, the
        # right half a cycle later. That half-cycle IS the gait.
        offsets = {}
        for index, name in enumerate(sorted(info["feet"])):
            offsets[name] = 0.0 if info["feet"][name]["side"] == "L" else 0.5
        #: The same half-cycle, by side, because it is what the ARMS are keyed
        #: against: each arm's phase is its own leg's phase plus `arm_phase`.
        side_offset = {}
        for name, value in offsets.items():
            side_offset.setdefault(info["feet"][name]["side"], value)

        # --- where each foot lands, relative to the BODY ---------------------
        #
        # Defect #2 of the wip-15 review, in the artist's words: "the foot
        # should land in front of the center of the model". It did not. The
        # plant used to be `step_length * stance_fraction` ahead of the foot's
        # own REST position for both feet, which is a position on the floor and
        # says nothing about where the body is when the foot gets there. The
        # left foot contacts at the top of the cycle, when the body has not
        # travelled yet, so it read about right by accident; the right foot
        # contacts half a cycle later, by which time the body has walked half a
        # stride past it, and it landed 0.19 of a stride BEHIND the hip.
        #
        # So the plant is solved from what it actually means: at this foot's
        # contact the body has travelled `stride * offset`, and the HEEL - the
        # part that strikes - is to be `strike_lead` of a stride in front of the
        # hip joint. The heel rides the target, so:
        #
        #     plant = stride * offset  +  strike_lead * stride  +  (hip0 - heel0)
        #
        # where the last term is the rest offset between the heel and the hip
        # joint along forward, which is what turns "the target went here" into
        # "the heel landed there". The foot roll pivots about the heel at the
        # strike, so the roll does not move the point this is solved for.
        heel_to_hip = {}
        for name, foot in info["feet"].items():
            hip = (probe_legs[name]["hip"] if name in probe_legs else foot["hip"])
            if hip is None:
                heel_to_hip[name] = 0.0
                continue
            heel_bone = rig.pose.bones.get(foot.get("heel_pivot") or "")
            heel = (_rest_world(rig, heel_bone).translation if heel_bone is not None
                    else foot["rest"].translation)
            heel_to_hip[name] = (hip - heel).dot(forward)
        if any(rig.pose.bones.get(foot.get("heel_pivot") or "") is None
               for foot in info["feet"].values()):
            warnings.append(
                "At least one foot has no heel pivot, so the strike lead was solved "
                "on the IK target instead of the heel. The foot still lands in front "
                "of the hips; where its heel lands depends on the offset between the "
                "two, which this rig does not expose.")

        plants = {name: [] for name in info["feet"]}
        # One pass over the cycle.  A function rather than a bare loop so the
        # reach clamp below can re-author with a deeper crouch: the leg's own
        # reach is *measured on the posed rig* rather than solved off the rest
        # geometry, because the rest solve was out by 4% on the synthetic rig
        # (it models the hips as a plumb drop and ignores the sway, the twist
        # and the offset between the torso control and the hip socket) and 4%
        # of a leg is exactly the over-reach that used to be paid for in
        # stretched deform bones.
        def _author_pass():
            keys = 0
            del span_track[:]
            for name in plants:
                del plants[name][:]
            for frame in frames:
                t = float(frame - frames[0]) / float(cycle_frames)
                scene.frame_set(frame)

                body = stride * t
                # What the body has travelled in world space this frame: the root's
                # own translation when this is a root-motion clip, and nothing at
                # all when it is the treadmill. Every anchor below that belongs to
                # the character rather than to the floor rides it.
                carry = body if travel else 0.0
                if root is not None:
                    matrix = rest_root.copy()
                    if travel:
                        matrix.translation = rest_root.translation + forward * body
                    _set_world(rig, root, matrix)
                    keys += _key_transform(root, frame)
                    touched(root.name)
                    refresh_view_layer()

                stance_now = set()
                for name in sorted(info["feet"]):
                    foot = info["feet"][name]
                    target = rig.pose.bones.get(foot["target"])
                    if target is None:
                        continue
                    u = (t - offsets[name]) % 1.0
                    cycle_offset = stride * math.floor((t - offsets[name]) + 1e-9)
                    plant = (cycle_offset + stride * offsets[name]
                             + stride * strike_lead + heel_to_hip[name])
                    along, lift = _foot_offset(u, stance_fraction, stride,
                                               step_height, plant)
                    if not travel:
                        along -= body
                    lateral = stride_width * (1.0 if foot["side"] == "L" else -1.0)
                    matrix = foot["rest"].copy()
                    matrix.translation = (foot["rest"].translation + forward * along
                                          + right * lateral + up * lift)
                    _set_world(rig, target, matrix)
                    keys += _key_transform(target, frame)
                    touched(target.name)
                    if u <= stance_fraction:
                        plants[name].append(frame)
                        stance_now.add(name)
                    heel = heels.get(name)
                    if heel is not None:
                        heel.rotation_euler = (math.radians(
                            _foot_roll(u, stance_fraction, roll_deg)), 0.0, 0.0)
                        heel.keyframe_insert("rotation_euler", frame=frame)
                        keys += 3
                        touched(heel.name)

                if torso is not None:
                    bob = -hip_lower - hip_drop * math.cos(4.0 * math.pi * t)
                    sway = hip_sway * math.sin(2.0 * math.pi * t)
                    # ``carry`` is the whole of defect #1.  Every world matrix in
                    # this loop is absolute, and the torso's used to be built off
                    # the *rest* position - which, with the root travelling forward
                    # underneath it, keyed a local translation that cancelled the
                    # travel exactly and pinned the hips in world space while the
                    # feet walked away from them.  On a rig with IK_Stretch = 1 the
                    # leg then simply grew (+32.6% at f33 on werewolf-wip-14), and
                    # the cycle could not close: f33's torso sat one stride behind
                    # f1's in root space.  The travel rides the root, so everything
                    # that is supposed to travel with the body adds it here too.
                    anchor = rest_torso.translation + forward * carry
                    matrix = rest_torso.copy()
                    matrix.translation = anchor + up * bob + right * sway
                    if hip_twist:
                        matrix = _rotate_about(matrix, up,
                                               hip_twist * math.sin(2.0 * math.pi * t),
                                               anchor)
                    _set_world(rig, torso, matrix)
                    keys += _key_transform(torso, frame)
                    touched(torso.name)
                    refresh_view_layer()

                for arm in arms:
                    # --- contralateral swing, which is defect #1 of wip-15 ----
                    #
                    # `drive` is +1 when this arm is at its FORWARD peak and -1
                    # at its back peak, and it is phased off this arm's OWN
                    # side's foot: at `arm_phase = pi` the left arm's forward
                    # peak lands exactly on the left foot's *back* peak, which
                    # is the right foot's strike. That is classical gait, and it
                    # is what the code used to get wrong twice over - once in
                    # the quarter-cycle `sin` (the arm peaked at mid-stance) and
                    # once in a per-side `sign` flip that, combined with the
                    # per-side half-cycle offset, cancelled to nothing and swung
                    # BOTH arms in unison (measured: 0.0 degrees of phase
                    # between the two hands).
                    #
                    # The side now lives in the phase, where it belongs, and the
                    # only per-side number left is `forward_sign` - which is
                    # measured off the rig above, not assumed, and is the same
                    # for two mirrored arms rotating about one world axis.
                    #
                    # The swing is authored as a world *rotation* and the location
                    # basis is then zeroed, so the arm rides the spine it hangs off
                    # instead of being re-pinned to a rest position the travelling
                    # body has already left behind - the same trick the punch and
                    # the jump use on the chest, and the other half of the seam fix
                    # above.
                    phase = side_offset.get(arm["side"], 0.0)
                    drive = math.cos(2.0 * math.pi * (t - phase) + arm_phase)
                    sign = arm["forward_sign"]
                    angle = arm_swing * drive
                    upper = arm["upper"]
                    matrix = _rotate_about(arm["upper_rest"], right, angle * sign,
                                           arm["upper_rest"].translation)
                    _set_world(rig, upper, matrix)
                    upper.location = (0.0, 0.0, 0.0)
                    keys += _key_transform(upper, frame)
                    touched(upper.name)
                    if arm["fore"] is not None:
                        refresh_view_layer()
                        # The elbow is most flexed at the arm's forward peak,
                        # and flexion carries the hand the same way the swing
                        # does - so it rides `forward_sign` too. It used to ride
                        # the per-side flip, which hyperextended one elbow
                        # through the whole cycle.
                        bend = elbow_bend * (0.5 + 0.5 * drive)
                        fore_rest = arm["fore_rest"]
                        bent = _rotate_about(fore_rest, right, bend * sign,
                                             fore_rest.translation)
                        carried = _rotate_about(bent, right, angle * sign,
                                                arm["upper_rest"].translation)
                        _set_world(rig, arm["fore"], carried)
                        arm["fore"].location = (0.0, 0.0, 0.0)
                        keys += _key_transform(arm["fore"], frame)
                        touched(arm["fore"].name)

                # --- what the leg was actually asked for, on the posed rig ---
                # Between the same two deform bones the rest chain was summed
                # between, so the ratio is one triangle rather than two. This
                # is the number the whole command turns on: over 1.0 the IK
                # target is further away than the leg is long, and something
                # downstream - stretch, or a foot that never arrives - has to
                # pay for it.
                if probe_legs:
                    refresh_view_layer()
                    for name in sorted(probe_legs):
                        leg = probe_legs[name]
                        target = rig.pose.bones.get(leg["foot"]["target"])
                        if target is None or name not in max_span:
                            continue
                        hip = rig.matrix_world @ rig.pose.bones[leg["hip_bone"]].head
                        ankle = (rig.matrix_world
                                 @ rig.pose.bones[leg["ankle_bone"]].head)
                        # Where the ankle is *asked* to be: the target, plus the
                        # rest offset between the control and the joint.
                        want = (rig.matrix_world @ target.head) + rest_gap[name]
                        asked = hip - want
                        span_track.append((
                            frame, name, asked.length,
                            asked.length / max_span[name], name in stance_now,
                            Vector((asked.x, asked.y, 0.0)).length, asked.z,
                            (hip - ankle).length))
            return keys

        # --- the reach clamp, closed on what the rig actually did ------------
        #
        # `_crouch_for` solves the stride against a model of the leg, and the
        # model is optimistic: it drops the hips down a plumb line from their
        # rest position, ignores the sway, the twist and the gap between the
        # torso control and the hip socket, and takes the *lowest* bob rather
        # than the highest.  Measured on the synthetic rig it cleared a stride
        # the posed leg then over-reached by 0.3%, and on werewolf-wip-14 the
        # same optimism (compounded by the travel bug) asked the left leg for
        # 1.3258 of its own length.  So the solve is now only the opening bid:
        # the clip is authored, the span is measured on the deform bones, and
        # if any frame asked for more leg than there is the hips go down and
        # the cycle is re-keyed.  With IK_Stretch keyed to 0 an over-reach can
        # no longer be paid for in bone length, so this is what pays for it.
        # It is a **solve**, not a ladder, and it is solved on what the leg was
        # ASKED for against what it can actually give.
        #
        # Two wrong metrics were measured and thrown away first, and both are
        # worth recording.  Hip-to-ankle over the leg's *rest chain* SATURATES
        # once ``IK_Stretch`` is keyed to 0 - it sat at exactly 1.0348 for a
        # 279 mm step and for a 387 mm one, so five passes moved it not at all.
        # Straight ankle-to-target distance is a constant 34 mm on this rig,
        # because ``foot_ik``'s head and ``DEF-foot``'s head are different
        # points, and a clamp chasing it ground a 193 mm stride down to 20 mm.
        # What is left is the honest question: is the place the ankle was asked
        # to be further from the hip than the leg can reach?
        #
        # The correction is then arithmetic rather than a guess.  The ask is a
        # right triangle from the hip - the crouch moves its vertical side, the
        # stride scales its horizontal one - and it has to fit inside the leg's
        # measured limit:
        #
        #     S = max_span * (1 - margin),  ask^2 = flat^2 + vert^2
        #     deepen by  d = vert - sqrt(S^2 - flat^2)      (the crouch first)
        #     then scale the step by  sqrt(S^2 - vert^2) / flat
        def _worst_row(stance_only=True):
            rows = [row for row in span_track if not stance_only or row[4]]
            if not rows:
                return None
            return max(rows, key=lambda row: row[3])

        keys_set += _author_pass()
        reach_passes = 1
        over_before = None
        over_after = None
        for _attempt in range(4):
            row = _worst_row()
            if row is None:
                break
            if over_before is None:
                over_before = row[3]
            over_after = row[3]
            if row[3] <= 1.0:
                break
            flat, vert = row[5], row[6]
            # Fit the ask inside the leg's own measured limit, with a hair of
            # margin so the solve lands inside rather than on the edge (where
            # the next frame's bob puts it back outside).
            span_target = max_span[row[1]] * (1.0 - WALK_REACH_MARGIN)
            moved = False
            if hip_lower < max_lower - 1e-9 and span_target > flat:
                wanted = abs(vert) - math.sqrt(max(0.0, span_target * span_target
                                                   - flat * flat))
                if wanted > 1e-6:
                    hip_lower = min(max_lower, hip_lower + wanted)
                    deepened = moved = True
            if not moved:
                # The crouch has nothing left (or the stride alone is longer
                # than the leg however deep the knees go), so the stride is
                # what gives - by exactly the horizontal side the triangle
                # leaves once the vertical one is fixed.
                room = span_target * span_target - vert * vert
                factor = (math.sqrt(room) / flat) if room > 0.0 and flat > 1e-9 else 0.5
                new_step = step_length * max(0.1, min(0.995, factor))
                if new_step < step_length - 1e-9:
                    step_length = new_step
                    stride = 2.0 * step_length
                    clamped = moved = True
            if not moved:
                break
            clear_action(action)
            rigforge_rig.apply_ik_convention(
                rig, poles=get_bool(params, "poles", True), keyframe_at=frames[0])
            again = plant_ik_stretch(rig, convention["limbs"],
                                     (frames[0], frames[-1]))
            keys_set = again["keys"] + _author_pass()
            reach_passes += 1
        row = _worst_row()
        if row is not None:
            over_after = row[3]
        swing_row = _worst_row(stance_only=False)
        swing_over = swing_row[3] if swing_row is not None else 0.0
        if over_after is not None and over_after > 1.0:
            warnings.append(
                "A planted foot is still asked to stand %.4f of the leg's own measured "
                "reach from the hip after %d pass(es), with the hips at %.0f mm of a "
                "%.0f mm ceiling and a %.0f mm step. With %s keyed to 0 an "
                "out-of-reach target cannot be paid for in bone length, so what gives "
                "is the foot: it slides. This rest pose has no knee bend left to spend "
                "- the anatomical pre-bend upstream is the real fix."
                % (over_after, reach_passes, hip_lower * 1000.0,
                   max_lower * 1000.0, step_length * 1000.0, IK_STRETCH_PROP))
        elif reach_passes > 1:
            warnings.append(
                "The cycle was re-authored %d time(s): the stride the rest-pose solve "
                "cleared asked a planted leg for %.4f of its own measured reach, so "
                "the hips went to %.0f mm and the step to %.0f mm. Solved from the "
                "posed rig's own triangle, not modelled."
                % (reach_passes - 1, over_before or 0.0,
                   hip_lower * 1000.0, step_length * 1000.0))
        if swing_over > 1.0 and swing_over > over_after + 1e-9:
            warnings.append(
                "A swinging foot is asked for %.4f of the leg's reach at its worst. "
                "That is not foot slide - the foot is off the ground - but the leg "
                "cannot make the arc, so the lift reads shallower than the %.0f mm it "
                "was given. Lower 'step_height' or shorten the stride if it shows."
                % (swing_over, step_height * 1000.0))

        applied = 0
        for curve in rigforge_rig.action_fcurves(action):
            for point in curve.keyframe_points:
                point.interpolation = interpolation
                applied += 1
            try:
                curve.update()
            except (AttributeError, RuntimeError):  # pragma: no cover
                pass

    scene.frame_set(previous_frame)
    refresh_view_layer()
    # After the frame restore, not before: an animated ID property is stamped
    # back onto the original pose bone every time the scene is evaluated, so a
    # restore that runs before the last frame_set is immediately overwritten by
    # the clip's own 0. The command has to leave the rig as it found it.
    stretch_restored = restore_ik_stretch(rig, planted_stretch["restore"])

    if modes:
        warnings.append(
            "Rotation mode changed to XYZ euler on %s so the foot roll is one readable "
            "channel." % ", ".join(sorted(modes)))
    if planted_stretch["missing"]:
        warnings.append(
            "No %s property on %s, so nothing stops this rig's legs stretching to reach "
            "a target they cannot make. On a Rigify rig that property is how a planted "
            "foot stays planted."
            % (IK_STRETCH_PROP, ", ".join(planted_stretch["missing"])))

    steps = []
    for name in sorted(plants):
        runs = []
        for frame in plants[name]:
            if runs and frame == runs[-1][-1] + 1:
                runs[-1].append(frame)
            else:
                runs.append([frame])
        steps.append({"foot": name, "target": info["feet"][name]["target"],
                      "stance_runs": [[run[0], run[-1]] for run in runs],
                      "stance_frames": sum(len(run) for run in runs)})

    return {
        "rig": rig.name,
        "action": action.name,
        "created": created,
        "loop": is_loop(action.name),
        "travel": travel,
        "cycle_frames": cycle_frames,
        "frame_range": [frames[0], frames[-1]],
        "stance_fraction": round(stance_fraction, 4),
        "step_length_m": round(step_length, 5),
        "stride_m": round(stride, 5),
        "step_height_m": round(step_height, 5),
        "hip_drop_m": round(hip_drop, 5),
        "hip_sway_m": round(hip_sway, 5),
        "hip_lower_m": round(hip_lower, 5),
        # --- the gait, as it was authored -------------------------------------
        # 180 degrees is contralateral swing and 0.30 of a stride is the heel
        # landing in front of the hips; `animation_check`'s `gait_opposition`
        # and `strike_lead` gates measure both back off the result rather than
        # trusting these.
        "arm_phase_deg": round(arm_phase_deg, 3),
        "arm_swing_contralateral": bool(abs(arm_phase_deg - 180.0) <= 45.0),
        "arm_forward_sign": {arm["side"]: arm["forward_sign"] for arm in arms},
        "arm_forward_probe_mm": {arm["side"]: arm["forward_probe_mm"]
                                 for arm in arms},
        "strike_lead": round(strike_lead, 5),
        "strike_lead_mm": round(strike_lead * stride * M_TO_MM, 2),
        "stance_reach_factor": round(reach_factor, 5),
        # How deep the crouch was *allowed* to go before the stride had to give
        # instead. A caller (or a gate) that wants to know whether a shortened
        # step was inevitable on this rig needs both numbers, not just one.
        "max_hip_lower_m": round(max_lower, 5),
        "step_length_requested_m": round(
            get_float(params, "step_length", STEP_LENGTH_RATIO * leg_length,
                      minimum=1e-4), 5),
        "leg_length_m": round(leg_length, 5),
        "step_length_reach_clamped": clamped,
        "hip_lower_deepened": deepened,
        "forward_axis": [round(v, 4) for v in forward],
        "convention": convention["convention"],
        "ik_limbs": [entry["name"] for entry in convention["limbs"]],
        "poles": convention["poles"],
        "ik_stretch": ik_stretch_report(planted_stretch, stretch_restored),
        # --- the reach, against the leg's own MEASURED limit ------------------
        # Over 1.0 the ankle was asked to stand further from the hip than this
        # leg goes, and with IK_Stretch keyed to 0 that is paid for in foot
        # slide rather than in bone length. The denominator is measured off the
        # rig, not summed off the rest chain - see `max_span`.
        "leg_reach_ratio": (round(over_after, 5)
                            if over_after is not None else None),
        "leg_reach_ratio_first_pass": (round(over_before, 5)
                                       if over_before is not None else None),
        "swing_reach_ratio": round(swing_over, 5),
        "leg_max_span_m": ({name: round(value, 5)
                            for name, value in sorted(max_span.items())}
                           if max_span else None),
        "leg_reach_m": (round(min(leg["reach"] for leg in probe_legs.values()), 5)
                        if probe_legs else None),
        "reach_passes": reach_passes,
        # The frame the clamp solved against, with the triangle it solved in.
        "leg_reach_worst": ({
            "frame": _worst_row()[0], "limb": _worst_row()[1],
            "asked_mm": round(_worst_row()[2] * M_TO_MM, 2),
            "ratio": round(_worst_row()[3], 5),
            "in_stance": bool(_worst_row()[4]),
            "flat_mm": round(_worst_row()[5] * M_TO_MM, 2),
            "vert_mm": round(_worst_row()[6] * M_TO_MM, 2),
            "delivered_mm": round(_worst_row()[7] * M_TO_MM, 2),
        } if _worst_row() is not None else None),
        # The seam is closed by construction, not by measurement: at t=1 every
        # channel is its own t=0 value one stride along, and the travel is on
        # the root, so the pose in root space repeats exactly.
        "loop_closes_in": "root space" if travel else "world space",
        "feet": steps,
        "bones": bones_touched,
        "keys_set": keys_set,
        "cleared_fcurves": cleared,
        "fcurves": len(rigforge_rig.action_fcurves(action)),
        "interpolation": interpolation,
        "interpolated_points": applied,
        "rotation_modes": modes,
        "says": (
            "%s: %d-frame cycle, %.0f mm stride, feet keyed on %s with %.0f%% of the "
            "cycle planted. The heel strikes %.0f mm (%.0f%% of the stride) in front "
            "of the hip joint and the arms swing %.0f degrees out of phase with the "
            "leg on their own side. %s"
            % (action.name, cycle_frames, stride * 1000.0,
               " and ".join(step["target"] for step in steps),
               stance_fraction * 100.0, strike_lead * stride * M_TO_MM,
               strike_lead * 100.0, arm_phase_deg,
               "The root carries the travel (export with root_motion)." if travel
               else "In place: the feet run backwards at one shared speed.")),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# rigforge_punch — a jab/cross authored on planted feet
# ---------------------------------------------------------------------------
#
# A walk is a locomotion problem: the feet move and the trick is keeping the
# planted one still.  A punch is the opposite problem and the same answer.  The
# feet do not move **at all** — a boxer's stance shift is weight transfer, the
# hips travelling over stationary feet, not the feet travelling — so this
# command keys both foot IK targets at their rest positions on every single
# frame.  That is not belt-and-braces: an unkeyed foot inherits whatever the
# pelvis does, and a pelvis that rotates 22 degrees and slides 30 mm forward
# would drag an FK leg's foot with it.  Keyed and constant, ``animation_check``
# measures the stance at 0.0 mm by construction.
#
# What the rest of the body does, and why each curve has the shape it has
# -----------------------------------------------------------------------
# A punch is a **kinetic chain**.  It does not start at the arm; it starts at
# the floor and arrives at the arm last.  So every rotation in this command is
# the same curve — rise to a peak, ease out back to guard — offset in *time*:
#
#   pelvis peak  ->  chest peak  ->  shoulder peak  ->  fist at full extension
#      s - 3L          s - 2L           s - 1L                   s
#
# where ``s`` is the strike frame and ``L`` is ``lead_frames``.  Measured on the
# result (world yaw against the rest pose, reported in ``rotation_lead``) the
# peaks land in that order by construction: at the pelvis's own peak the chest
# term is still rising, so the chest's total peaks strictly later, and the same
# argument moves the shoulder later again.  Each segment's *total* rotation is
# the sum of everything below it in the chain, which is what "rotation travels
# up the body" means when you measure it rather than assert it.
#
# The fist is keyed on the **arm IK target**, for the same reason the feet are.
# A jab travels in a straight line; FK rotations travel on an arc.  Keying
# ``hand_ik`` gives the straight line for free and lands the fist on the target
# to the millimetre, so the command can *measure* full extension against the
# arm's own reach instead of hoping.  Both arms go to IK for the clip and the
# switch is keyframed, so the export bake resolves what was authored.
#
# Three numbers this refuses to leave unmeasured, all in the report:
#
# * **peak fist speed**, and the frame it happens on — which is before the
#   strike, not on it, because a fist decelerates into full extension.  A fist
#   still accelerating at the moment of impact is an arm being thrown, not
#   punched.
# * **extension against reach**.  The arm's reach is measured off this rig
#   (upper arm + forearm at rest, shoulder joint to wrist).  A target further
#   away than ``max_extension_ratio`` of it is pulled back in rather than
#   reached for: past roughly 98% the elbow is hyperextended, the IK solver
#   starts stretching, and a stretched arm does not arrive where it was keyed.
# * **peak hip and chest rotation**, in degrees, measured from the authored
#   curves rather than echoed from the parameters.
#
# Everything is a fraction of this rig's own measurements — arm reach, shoulder
# height, shoulder width, leg length — so the same call fits a 6'2" brawler and
# a goblin without being told which it is.

PUNCH_DEFAULT_FRAMES = 24

#: Where in the clip the fist is at full extension, as a fraction of it.  Under
#: half: a punch is a fast strike and a longer, slower recovery.
PUNCH_STRIKE_FRACTION = 0.45

#: Where the chamber (the small draw back before the fist fires) sits, as a
#: fraction of the run-up to the strike.
PUNCH_CHAMBER_FRACTION = 0.30

#: How many frames each link of the kinetic chain leads the next by, as a
#: fraction of the clip.  0.085 of a 24-frame punch is two frames, which is the
#: "a couple of frames" an animator offsets a torso pass by.
PUNCH_LEAD_FRACTION = 0.085

#: Rotation caps, in degrees, for the three links that turn.
DEFAULT_HIP_ROTATION_DEG = 22.0
DEFAULT_CHEST_ROTATION_DEG = 18.0
DEFAULT_SHOULDER_ROTATION_DEG = 10.0

#: The hard ceiling on how far the fist may be from the shoulder at full
#: extension, as a fraction of the arm's own measured reach.  Past this the
#: elbow is hyperextended and Rigify's IK stretch makes up the difference — the
#: arm equivalent of the foot slide ``rigforge_walk`` exists to avoid.
MAX_EXTENSION_RATIO = 0.98

#: How far past that cap the *measured* extension may land before it is worth a
#: warning, as a fraction of reach.  The target is solved against the control
#: rig's shoulder joint; the deform shoulder it is measured on afterwards hangs
#: off Rigify's parent-switch machinery and can sit a millimetre or two away
#: from it.  1% of reach is that gap, not slack in the rule.
EXTENSION_TOLERANCE = 0.01

#: What a target distance *defaults* to, as a fraction of reach: full extension
#: with the elbow still soft.
PUNCH_REACH_MARGIN = 0.95

#: The guard, as fractions of the arm's reach above/in front of the shoulder,
#: and of the shoulder's half-width toward the chest midline.  Together they
#: put the fist by the cheek with the elbow folded at roughly 60% of reach.
GUARD_RISE_RATIO = 0.25
GUARD_FORWARD_RATIO = 0.30
GUARD_INWARD_RATIO = 0.5

#: How far the fist draws back past the guard before it fires, as a fraction of
#: reach.  Small on purpose: a visible wind-up is a telegraph, and a telegraphed
#: punch is a different (slower, heavier) clip.
CHAMBER_DRAW_RATIO = 0.08

#: The weight transfer: how far the hips travel over the planted feet, as a
#: fraction of leg length, and how much of that is lateral (onto the lead foot).
WEIGHT_SHIFT_RATIO = 0.05
WEIGHT_SHIFT_LATERAL = 0.4

#: How far the hips may be lowered to buy the stance the transfer needs before
#: the transfer is shortened instead.  A rig at rest stands with its legs
#: straight, and a straight leg cannot let its hip travel horizontally without
#: the IK stretching — which is foot slide by another name.
PUNCH_MAX_HIP_LOWER_RATIO = 0.18

#: How sharply the drive from chamber to target accelerates.  >1 skews the peak
#: speed late: the fist is fastest at about 60% of the way out and decelerating
#: by the time it arrives.
PUNCH_DRIVE_SKEW = 1.6

#: Controls, best first.  ``pelvis`` turns, ``body`` carries the weight shift;
#: on a rig that has only one of them they are the same bone and it does both.
PELVIS_CONTROLS = ("hips", "torso", "spine_fk")
BODY_CONTROLS = ("torso", "hips", "spine_fk")
CHEST_CONTROLS = ("chest", "spine_fk.002", "spine_fk.001", "spine_fk")
HEAD_CONTROLS = ("head", "neck")


def _ease_out(value):
    """0 -> 1, fast then slow: the shape a body settling back to guard makes."""
    value = min(1.0, max(0.0, float(value)))
    return 1.0 - (1.0 - value) ** 3


def _rise_fall(frame, start, peak, end):
    """One link's rotation at ``frame``: 0 at ``start``, 1 at ``peak``, 0 at ``end``.

    Smoothstep both ways, which is not an aesthetic choice — it is what makes
    the lead *hold*.  The unwind has to leave the peak with zero slope, because
    the next link up is still rising through it: with a decay that drops away
    at full speed the moment the pelvis tops out (a plain cubic ease-out does
    exactly that) the pelvis loses more degrees per frame than the shoulder
    gains, the sum peaks early, and the chain silently stops leading.  Flat off
    the peak, accelerating, then decelerating into guard — so a body eases out
    of its own turn instead of snapping out of it, and each link's *total*
    still peaks strictly after the one below it.

    Deterministic, no noise, evaluated per frame so ``LINEAR`` keyframe handles
    reproduce it exactly: the easing lives in the samples, not in Bezier
    tangents a game engine is going to resample anyway.
    """
    if frame <= start or frame >= end:
        return 0.0
    if frame <= peak:
        return _smoothstep((frame - start) / float(max(1, peak - start)))
    return 1.0 - _smoothstep((frame - peak) / float(max(1, end - peak)))


def _signed_yaw(from_vector, to_vector, up):
    """The signed angle about ``up`` that turns one horizontal vector onto another."""
    a = Vector((from_vector.x, from_vector.y, 0.0))
    b = Vector((to_vector.x, to_vector.y, 0.0))
    if a.length < 1e-9 or b.length < 1e-9:
        return 0.0
    a.normalize()
    b.normalize()
    return math.atan2(a.cross(b).dot(up), a.dot(b))


def _yaw_delta(rest, current):
    """How far ``current`` is turned about the vertical from ``rest``, in radians.

    Quaternion rather than matrix columns so a scaled control bone (Rigify has
    several) does not read as a rotation, and exact for the pure-Z turns this
    command authors.
    """
    delta = current.to_quaternion() @ rest.to_quaternion().inverted()
    if delta.w < 0.0:
        delta.negate()
    return 2.0 * math.atan2(delta.z, delta.w)


def punch_arms(rig, limbs):
    """Each arm's shoulder joint, wrist and **measured** reach, at rest.

    ``reach`` is upper arm + forearm — the shoulder joint to the wrist, summed
    along the rest chain rather than taken as the straight line between the
    ends, because an arm modelled with a soft elbow would otherwise measure
    short and every default derived from it would come out cramped.
    """
    out = {}
    for entry in limbs:
        if entry["limb"] != "arm":
            continue
        chain = [rig.pose.bones.get(name) for name in entry["fk_chain"]]
        chain = [bone for bone in chain if bone is not None]
        if len(chain) < 2:
            continue
        points = [_rest_world(rig, bone).translation.copy() for bone in chain]
        reach = sum((points[index + 1] - points[index]).length
                    for index in range(len(points) - 1))
        target = rig.pose.bones.get(entry["ik_target"])
        if target is None or reach <= 1e-6:
            continue
        out[entry["side"]] = {
            "side": entry["side"],
            "limb": entry,
            "shoulder": points[0],
            "wrist": points[-1],
            "reach": reach,
            "target": entry["ik_target"],
            "rest": _rest_world(rig, target),
            "shoulder_bone": ("shoulder.%s" % entry["side"]
                              if ("shoulder.%s" % entry["side"]) in rig.pose.bones
                              else None),
        }
    return out


def _punch_stance(info, pivot, yaw, shift, margin, hip_lower, max_lower):
    """Bend the knees as much as the weight transfer needs, then clamp it.

    The hips rotate and travel over feet that are nailed down, which means the
    hip socket moves horizontally away from its ankle: ``sqrt(drop^2 + reach^2)``
    has to stay inside the leg.  A rig at rest stands with its legs straight and
    has nothing to spend, so the order of preference is the same as
    :func:`_crouch_for`'s — deepen the stance first, shorten the transfer only
    when the stance runs out.  Returns ``(shift, hip_lower, clamped, deepened)``.
    """
    needed = 0.0
    deepened = False
    clamped = False
    for foot in info["feet"].values():
        if foot["hip"] is None:
            continue
        hip, ankle = foot["hip"], foot["ankle"]
        arm = Vector((hip.x - pivot.x, hip.y - pivot.y, 0.0)).length
        # A rotation of `yaw` about the body axis moves a socket `arm` out from
        # it by a chord of 2*arm*sin(yaw/2).
        rotation = 2.0 * arm * math.sin(abs(yaw) * 0.5)
        flat = Vector((hip.x - ankle.x, hip.y - ankle.y, 0.0)).length
        drop = hip.z - ankle.z
        span = margin * info["leg_length"]
        # Conservative: the rotation and the shift are summed rather than
        # composed, so the answer is never short of what the leg actually needs.
        want = flat + rotation + shift
        needed = max(needed, want)
        room = math.sqrt(max(0.0, span * span - want * want))
        lower = drop - room
        if lower > hip_lower:
            hip_lower = min(max_lower, lower)
            deepened = True

    if not deepened or needed <= 0.0:
        return shift, hip_lower, clamped, deepened

    # Did the stance we were allowed actually buy it? If not, the transfer is
    # what gives way - never the plant.
    allowed = shift
    for foot in info["feet"].values():
        if foot["hip"] is None:
            continue
        hip, ankle = foot["hip"], foot["ankle"]
        span = margin * info["leg_length"]
        drop = (hip.z - ankle.z) - hip_lower
        flat = Vector((hip.x - ankle.x, hip.y - ankle.y, 0.0)).length
        rotation = 2.0 * Vector((hip.x - pivot.x, hip.y - pivot.y, 0.0)).length \
            * math.sin(abs(yaw) * 0.5)
        room = math.sqrt(max(0.0, span * span - drop * drop))
        allowed = min(allowed, max(0.0, room - flat - rotation))
    if allowed < shift * (1.0 - 1e-6):
        clamped = True
        shift = allowed
    return shift, hip_lower, clamped, deepened


@command("rigforge_punch")
def cmd_rigforge_punch(params):
    """Author a jab/cross on the arm IK target, with both feet planted.

    ``rigforge_punch {"rig"?, "action"?, "side"?, "frames"?, "strike_fraction"?,
    "lead_frames"?, "target_distance"?, "target_height"?, "hip_rotation_deg"?,
    "chest_rotation_deg"?, "shoulder_rotation_deg"?, "weight_shift"?,
    "hip_lower"?, "guard_rise"?, "chamber_draw"?, "reach_margin"?,
    "max_extension_ratio"?, "loop"?, "clear"?, "interpolation"?, "poles"?}``

    Every length is metres and every default is a fraction of *this* rig's own
    arm reach, shoulder width or leg length, measured off the rest pose.  See
    the section header above for what each body part's curve is and why.
    """
    started = time.monotonic()
    warnings = []
    rig = _rig_for(None, params, key="rig", required=True)
    scene = get_scene()

    side = get_choice(params, "side",
                      {"L": "L", "LEFT": "L", "R": "R", "RIGHT": "R"}, "R")
    total_frames = get_int(params, "frames", PUNCH_DEFAULT_FRAMES,
                           minimum=8, maximum=600)
    strike_fraction = get_float(params, "strike_fraction", PUNCH_STRIKE_FRACTION,
                                minimum=0.15, maximum=0.85)
    hip_deg = get_float(params, "hip_rotation_deg", DEFAULT_HIP_ROTATION_DEG,
                        minimum=0.0, maximum=60.0)
    chest_deg = get_float(params, "chest_rotation_deg", DEFAULT_CHEST_ROTATION_DEG,
                          minimum=0.0, maximum=60.0)
    shoulder_deg = get_float(params, "shoulder_rotation_deg",
                             DEFAULT_SHOULDER_ROTATION_DEG, minimum=0.0, maximum=45.0)
    max_extension = get_float(params, "max_extension_ratio", MAX_EXTENSION_RATIO,
                              minimum=0.3, maximum=1.0)
    reach_margin = get_float(params, "reach_margin", PUNCH_REACH_MARGIN,
                             minimum=0.2, maximum=max_extension)
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS}, "LINEAR")
    clear = get_bool(params, "clear", True)
    loop = get_bool(params, "loop", False) if params.get("loop") is not None else False

    limbs = rigforge_rig.ik_limbs(rig)
    legs = [entry for entry in limbs if entry["limb"] == "leg"]
    if len(legs) < 2:
        raise ForgeError(
            "rigforge_punch plants the feet through their IK targets, and %r has %d "
            "leg(s) with one (it needs foot_ik.L and foot_ik.R with an IK_FK switch on "
            "thigh_parent.L/R). Generate the rig with rigforge_generate_rig, or run "
            "rigforge_ik to see what this rig actually has. A punch whose legs are "
            "keyed in FK drags its own feet across the floor as the hips turn, which "
            "is the foot-slide anti-pattern animation_check measures in millimetres."
            % (rig.name, len(legs)))

    arms = punch_arms(rig, limbs)
    if side not in arms:
        raise ForgeError(
            "rigforge_punch drives the fist through the arm's IK target, and %r has no "
            "usable %s arm (it needs hand_ik.%s plus upper_arm_fk.%s/forearm_fk.%s to "
            "measure the reach from). Arms found: %s."
            % (rig.name, {"L": "left", "R": "right"}[side], side, side, side,
               ", ".join("arm.%s" % key for key in sorted(arms)) or "none"))

    info = locomotion_frame(rig, limbs)
    forward, right, up = info["forward"], info["right"], info["up"]
    leg_length = info["leg_length"]

    punching = arms[side]
    other_side = "R" if side == "L" else "L"
    off = arms.get(other_side)
    reach = punching["reach"]
    shoulder_rest = punching["shoulder"]
    if off is not None:
        chest_mid = (shoulder_rest + off["shoulder"]) * 0.5
    else:
        chest_mid = shoulder_rest.copy()
        warnings.append(
            "Only the %s arm was found, so the chest midline was taken as that "
            "shoulder and there is no off-hand guard to hold." % side)
    #: The body's own vertical axis, through the chest midline: what the pelvis,
    #: the chest and the shoulder all turn about.
    pivot = Vector((chest_mid.x, chest_mid.y, 0.0))
    half_width = abs((shoulder_rest - chest_mid).dot(right))
    #: +1 when the punching shoulder sits on the rig's right. A turn of +theta
    #: about `up` carries `right` onto `forward` (up x right == forward), so
    #: this sign is what drives the punching shoulder *into* the punch whichever
    #: way the character happens to face.
    yaw_sign = 1.0 if (shoulder_rest - chest_mid).dot(right) >= 0.0 else -1.0

    hip_rad = math.radians(hip_deg)
    chest_rad = math.radians(chest_deg)
    shoulder_rad = math.radians(shoulder_deg)

    weight_shift = get_float(params, "weight_shift", WEIGHT_SHIFT_RATIO * leg_length,
                             minimum=0.0, maximum=leg_length)
    hip_lower = get_float(params, "hip_lower", 0.0, minimum=0.0, maximum=leg_length)
    max_lower = PUNCH_MAX_HIP_LOWER_RATIO * leg_length
    if params.get("hip_lower") is not None:
        max_lower = hip_lower  # asked for explicitly: the transfer gives way instead
    weight_shift, hip_lower, shift_clamped, stance_deepened = _punch_stance(
        info, pivot, hip_rad, weight_shift, DEFAULT_REACH_MARGIN, hip_lower,
        max_lower)
    if stance_deepened:
        warnings.append(
            "The hips were lowered %.3f m into a stance so the legs can carry a "
            "%.0f deg pelvis turn and a %.0f mm weight transfer over planted feet. A "
            "rig at rest stands with its legs straight and has no bend to spend; a "
            "boxer's knees are not locked for the same reason."
            % (hip_lower, hip_deg, weight_shift * 1000.0))
    if shift_clamped:
        warnings.append(
            "weight_shift was shortened to %.0f mm: any further and the hip travels "
            "outside what the leg can reach with the foot nailed down, so the IK "
            "stretches and the foot slides on the deform bones while the target sits "
            "still." % (weight_shift * 1000.0))

    stance_drop = up * hip_lower

    # --- the clock --------------------------------------------------------
    strike_index = 1 + int(round(strike_fraction * (total_frames - 1)))
    strike_index = max(2, min(total_frames - 1, strike_index))
    chamber_index = 1 + int(round(PUNCH_CHAMBER_FRACTION * (strike_index - 1)))
    chamber_index = max(1, min(strike_index - 1, chamber_index))
    lead = get_int(params, "lead_frames",
                   max(1, int(round(PUNCH_LEAD_FRACTION * total_frames))),
                   minimum=0, maximum=200)
    room = max(0, (strike_index - 1) // 3)
    if lead > room:
        warnings.append(
            "lead_frames was cut from %d to %d: the strike lands on frame %d, and the "
            "pelvis has to peak three leads before the fist does. A longer lead needs "
            "more frames or a later strike_fraction." % (lead, room, strike_index))
        lead = room
    if lead == 0:
        warnings.append(
            "lead_frames is 0, so the pelvis, chest, shoulder and fist all peak on the "
            "same frame. That is a body thrown in one piece, not a kinetic chain; give "
            "the clip more frames or a later strike_fraction to buy the offset.")
    peaks = {
        "pelvis": strike_index - 3 * lead,
        "chest": strike_index - 2 * lead,
        "shoulder": strike_index - lead,
        "fist": strike_index,
    }
    # The clip starts and ends on the same settled guard pose, so the last
    # frame already repeats the first: `loop` only decides whether the action
    # carries Godot's `-loop` suffix, it does not change a single key.
    end_frame = total_frames
    frames = list(range(1, total_frames + 1))

    def _curve(frame, key):
        return _rise_fall(min(frame, end_frame), 1, peaks[key], end_frame)

    def _yaw_at(frame):
        """``(pelvis, chest, shoulder)`` world yaw — each the sum of the links below."""
        pelvis = yaw_sign * hip_rad * _curve(frame, "pelvis")
        chest = pelvis + yaw_sign * chest_rad * _curve(frame, "chest")
        shoulder = chest + yaw_sign * shoulder_rad * _curve(frame, "shoulder")
        return pelvis, chest, shoulder

    def _shift_at(frame):
        drive = _curve(frame, "pelvis")
        return (forward * (weight_shift * drive)
                + right * (-yaw_sign * WEIGHT_SHIFT_LATERAL * weight_shift * drive))

    def _body_at(frame):
        """The rigid frame the guard rides: the chest's turn plus the transfer."""
        _pelvis, chest, _shoulder = _yaw_at(frame)
        move = Matrix.Translation(_shift_at(frame) - stance_drop)
        turn = (Matrix.Translation(pivot) @ Matrix.Rotation(chest, 4, up)
                @ Matrix.Translation(-pivot))
        return move @ turn

    # --- the target -------------------------------------------------------
    # Solved against where the shoulder **is at the moment of impact**, not
    # where it sits at rest. The whole point of the kinetic chain is that the
    # hips, chest and clavicle carry the shoulder joint forward into the punch;
    # measuring the reach from the rest shoulder would hand the arm a target it
    # arrives at with the elbow still folded — 64% of reach on the rig this was
    # first measured on, which is a punch that lands short and looks it.
    _pelvis_strike, _chest_strike, yaw_strike = _yaw_at(strike_index)
    shoulder_strike = ((Matrix.Translation(_shift_at(strike_index) - stance_drop)
                        @ Matrix.Translation(pivot)
                        @ Matrix.Rotation(yaw_strike, 4, up)
                        @ Matrix.Translation(-pivot)) @ shoulder_rest)

    # Default: on the chest midline, at that shoulder's height, as far forward
    # as the arm reaches with the elbow still soft. Solved in the triangle
    # rather than guessed, so `target_distance` and the reach agree exactly.
    target_height = get_float(params, "target_height", shoulder_strike.z)
    flat = Vector((shoulder_strike.x, shoulder_strike.y, 0.0))
    along = (pivot - flat).dot(forward)
    lateral = (pivot - flat).dot(right)
    rise = target_height - shoulder_strike.z

    def _distance_for(span):
        room = span * span - lateral * lateral - rise * rise
        if room <= 0.0:
            return None
        return -along + math.sqrt(room)

    derived = _distance_for(reach_margin * reach)
    if derived is None:
        raise ForgeError(
            "A target at z=%.3f is %.0f mm off the %s shoulder's own height and "
            "sideline before it moves forward at all, which is further than %.0f%% of "
            "this arm's %.0f mm reach. Lower 'target_height', or raise 'reach_margin'."
            % (target_height, math.hypot(lateral, rise) * 1000.0, side,
               reach_margin * 100.0, reach * 1000.0))
    target_distance = get_float(params, "target_distance", derived, minimum=0.0)
    target = Vector((pivot.x, pivot.y, target_height)) + forward * target_distance
    extension_planned = (target - shoulder_strike).length
    target_clamped = False
    if extension_planned > max_extension * reach * (1.0 + 1e-9):
        pulled = _distance_for(max_extension * reach)
        if pulled is None:
            raise ForgeError(
                "That target cannot be punched at all: it is %.0f mm off the shoulder "
                "sideways and in height alone, against a %.0f mm arm. Move it onto the "
                "chest midline or closer to shoulder height."
                % (math.hypot(lateral, rise) * 1000.0, reach * 1000.0))
        warnings.append(
            "target_distance was pulled back from %.0f mm to %.0f mm: the fist would "
            "otherwise be %.0f mm from the shoulder against a measured reach of %.0f mm "
            "(%.0f%%). Past %.0f%% the elbow is hyperextended, Rigify's IK stretch "
            "makes up the difference, and a stretched arm does not arrive where it was "
            "keyed."
            % (target_distance * 1000.0, pulled * 1000.0, extension_planned * 1000.0,
               reach * 1000.0, 100.0 * extension_planned / reach,
               max_extension * 100.0))
        target_distance = pulled
        target = Vector((pivot.x, pivot.y, target_height)) + forward * target_distance
        extension_planned = (target - shoulder_strike).length
        target_clamped = True

    # --- guard and chamber, in the rest frame the body carries around -------
    guard_rise = get_float(params, "guard_rise", GUARD_RISE_RATIO * reach, minimum=0.0)
    guard_forward = get_float(params, "guard_forward", GUARD_FORWARD_RATIO * reach,
                              minimum=0.0)
    chamber_draw = get_float(params, "chamber_draw", CHAMBER_DRAW_RATIO * reach,
                             minimum=0.0)

    def _guard_for(entry):
        sign = 1.0 if (entry["shoulder"] - chest_mid).dot(right) >= 0.0 else -1.0
        inward = abs((entry["shoulder"] - chest_mid).dot(right)) * GUARD_INWARD_RATIO
        return (entry["shoulder"] + up * guard_rise + forward * guard_forward
                - right * (sign * inward))

    guard = _guard_for(punching)
    chamber = guard - forward * chamber_draw
    guard_off = _guard_for(off) if off is not None else None

    #: The fist's rest orientation points down the arm, which at rest is out to
    #: the side; at full extension it has to point at the target. This is the
    #: yaw that turns one onto the other, spent over the drive.
    aim_yaw = _signed_yaw(punching["wrist"] - punching["shoulder"], forward, up)

    chamber_world = _body_at(chamber_index) @ chamber

    def _extension_at(frame):
        """0 at guard, 1 with the fist on the target."""
        if frame <= chamber_index:
            return 0.0
        if frame <= strike_index:
            x = (frame - chamber_index) / float(max(1, strike_index - chamber_index))
            return _smoothstep(min(1.0, max(0.0, x)) ** PUNCH_DRIVE_SKEW)
        x = (frame - strike_index) / float(max(1, end_frame - strike_index))
        return 1.0 - _ease_out(min(1.0, max(0.0, x)))

    def _fist_at(frame):
        frame = min(frame, end_frame)
        if frame <= chamber_index:
            # Settling into the chamber, riding the body.
            draw = _smoothstep((frame - 1) / float(max(1, chamber_index - 1)))
            return _body_at(frame) @ guard.lerp(chamber, draw)
        if frame <= strike_index:
            # The drive. A straight world-space line, by construction.
            return chamber_world.lerp(target, _extension_at(frame))
        back = _ease_out((frame - strike_index)
                         / float(max(1, end_frame - strike_index)))
        return target.lerp(_body_at(frame) @ guard, back)

    # --- the action -------------------------------------------------------
    wanted_action = get_str(params, "action", "punch.%s" % side)
    wanted_action = loop_name(wanted_action, loop)
    before_actions = sorted(action.name for action in bpy.data.actions)
    action = bpy.data.actions.get(wanted_action)
    created = False
    if action is None:
        action = bpy.data.actions.new(wanted_action)
        created = True
    # Fake user on every punch action, whether we made it or found it: the next
    # command to assign a different action to this rig must not take the punch
    # with it, and that is exactly what happens to a zero-user action on save.
    action.use_fake_user = True

    keys_set = 0
    cleared = 0
    bones_touched = []
    modes = {}
    previous_frame = scene.frame_current
    previous_action = rig.animation_data.action if rig.animation_data else None

    def touched(name):
        if name and name not in bones_touched:
            bones_touched.append(name)

    fist_track = []
    measured = {"pelvis": [], "chest": [], "shoulder": []}
    strike_measurement = {}

    with object_mode():
        assign_action(rig, action)
        if clear:
            cleared = clear_action(action)

        # Legs IK (the plant) and arms IK (the straight-line fist), keyframed at
        # frame 1 so the export bake resolves the rig that was authored.
        convention = rigforge_rig.apply_ik_convention(
            rig, legs="ik", arms="ik", poles=get_bool(params, "poles", True),
            keyframe_at=frames[0])
        for entry in convention["limbs"]:
            touched(entry["switch_bone"])

        # A punch is the planted-foot clip: the feet never move for the whole
        # 24 frames, so the legs have no business stretching. See
        # plant_ik_stretch.
        planted_stretch = plant_ik_stretch(rig, convention["limbs"],
                                           (frames[0], frames[-1]))
        keys_set += planted_stretch["keys"]
        for name in planted_stretch["bones"]:
            touched(name)

        def _control(names):
            for name in names:
                if name in rig.pose.bones:
                    return rig.pose.bones[name]
            return None

        body = _control(BODY_CONTROLS)
        pelvis_bone = _control(PELVIS_CONTROLS)
        chest_bone = _control(CHEST_CONTROLS)
        head_bone = _control(HEAD_CONTROLS)
        if body is None or pelvis_bone is None:
            raise ForgeError(
                "rigforge_punch turns the hips, and %r has none of %s to turn. Generate "
                "the rig with rigforge_generate_rig."
                % (rig.name, ", ".join(PELVIS_CONTROLS)))
        if chest_bone is pelvis_bone:
            chest_bone = None
            warnings.append(
                "This rig has no separate chest control, so the chest turn was folded "
                "into the pelvis: the rotation cannot lead itself up a spine that is "
                "one bone.")
        if head_bone is None:
            warnings.append("No head control, so the head does not hold the target.")
        shoulder_bone = rig.pose.bones.get(punching["shoulder_bone"] or "")
        if shoulder_bone is None and shoulder_deg > 0.0:
            warnings.append(
                "No %s control on this rig, so the shoulder does not lead the arm; the "
                "chest hands its rotation straight to the fist."
                % (punching["shoulder_bone"] or "clavicle"))

        rest_of = {}
        for bone in (body, pelvis_bone, chest_bone, shoulder_bone, head_bone):
            if bone is not None:
                rest_of[bone.name] = _rest_world(rig, bone)

        heels = {}
        for name, foot in info["feet"].items():
            heel = rig.pose.bones.get(foot.get("heel_pivot") or "")
            if heel is None:
                continue
            if heel.rotation_mode == "QUATERNION":
                modes[heel.name] = heel.rotation_mode
                heel.rotation_mode = "XYZ"
            heels[name] = heel

        for frame in frames:
            scene.frame_set(frame)
            source = min(frame, end_frame)
            yaw_pelvis, yaw_chest, yaw_shoulder = _yaw_at(source)
            shift = _shift_at(source) - stance_drop

            # 1. The feet. Rest position, every frame, unmoved: the plant is a
            #    constant, which is why it cannot drift.
            for name in sorted(info["feet"]):
                foot = info["feet"][name]
                target_bone = rig.pose.bones.get(foot["target"])
                if target_bone is None:
                    continue
                _set_world(rig, target_bone, foot["rest"].copy())
                keys_set += _key_transform(target_bone, frame)
                touched(target_bone.name)
                heel = heels.get(name)
                if heel is not None:
                    heel.rotation_euler = (0.0, 0.0, 0.0)
                    heel.keyframe_insert("rotation_euler", frame=frame)
                    keys_set += 3
                    touched(heel.name)

            # 2. The body: the weight transfer, and the stance it stands in.
            matrix = rest_of[body.name].copy()
            matrix.translation = rest_of[body.name].translation + shift
            if pelvis_bone is body:
                matrix = _rotate_about(matrix, up, yaw_pelvis, pivot)
            _set_world(rig, body, matrix)
            keys_set += _key_transform(body, frame)
            touched(body.name)
            refresh_view_layer()

            # 3. The pelvis, 4. the chest, 5. the shoulder — each set to its own
            #    accumulated world yaw, so what the report measures on the bone
            #    is the sum of every link below it whatever the parenting is.
            for bone, yaw in ((pelvis_bone, yaw_pelvis), (chest_bone, yaw_chest),
                              (shoulder_bone, yaw_shoulder)):
                if bone is None or bone is body:
                    continue
                rest = rest_of[bone.name]
                matrix = rest.copy()
                matrix.translation = rest.translation + shift
                matrix = _rotate_about(matrix, up, yaw, pivot)
                _set_world(rig, bone, matrix)
                keys_set += _key_transform(bone, frame)
                touched(bone.name)
                refresh_view_layer()

            # 6. The hands. The punching fist on its path, the off hand holding
            #    guard as the body carries it.
            fist = _fist_at(source)
            extension = _extension_at(source)
            hand = rig.pose.bones.get(punching["target"])
            if hand is not None:
                matrix = Matrix.Rotation(yaw_chest + aim_yaw * extension, 4, up) \
                    @ punching["rest"].copy()
                matrix.translation = fist
                _set_world(rig, hand, matrix)
                keys_set += _key_transform(hand, frame)
                touched(hand.name)
            if off is not None:
                off_hand = rig.pose.bones.get(off["target"])
                if off_hand is not None:
                    base = off["rest"].copy()
                    base.translation = guard_off
                    _set_world(rig, off_hand, _body_at(source) @ base)
                    keys_set += _key_transform(off_hand, frame)
                    touched(off_hand.name)
            refresh_view_layer()

            # 7. The head holds the target. Its rotation is set in world space,
            #    which is the whole point: the chest turns underneath it and the
            #    eyes stay where they were, instead of being swung off the
            #    target by the very rotation that throws the punch.
            if head_bone is not None:
                # Where the head *is* is the spine's business, so the anchor is
                # computed from the body frame rather than read back off the
                # evaluated pose: a measured position feeds a micron of float
                # dust into the look angle and into the location channel, and
                # "author the same punch twice, get the same keys" is a promise
                # that does not survive micron-sized noise. The location basis
                # is then pinned to exactly zero and keyed there, so the head
                # rides the neck and only its rotation is authored.
                anchor = _body_at(source) @ rest_of[head_bone.name].translation
                look = _signed_yaw(forward, target - anchor, up)
                matrix = _rotate_about(rest_of[head_bone.name], up, look,
                                       rest_of[head_bone.name].translation)
                matrix.translation = anchor
                _set_world(rig, head_bone, matrix)
                head_bone.location = (0.0, 0.0, 0.0)
                keys_set += _key_transform(head_bone, frame)
                touched(head_bone.name)
                refresh_view_layer()

            # --- measurement, on the posed rig rather than on the parameters --
            fist_track.append((frame, fist.copy()))
            for key, bone in (("pelvis", pelvis_bone), ("chest", chest_bone),
                              ("shoulder", shoulder_bone)):
                if bone is None:
                    continue
                measured[key].append(
                    (frame, abs(math.degrees(_yaw_delta(rest_of[bone.name],
                                                        _world_matrix(rig, bone))))))
            if frame == strike_index:
                upper = rigforge_rig.def_bones_for(rig, "upper_arm.%s" % side)
                joint_bone = (upper[0] if upper and upper[0] in rig.pose.bones
                              else punching["limb"]["fk_chain"][0])
                joint = (rig.matrix_world
                         @ rig.pose.bones[joint_bone].head).copy()
                deform = rigforge_rig.def_bones_for(rig, "hand.%s" % side)
                landed = (rig.matrix_world @ rig.pose.bones[deform[0]].head
                          if deform and deform[0] in rig.pose.bones else fist.copy())
                strike_measurement = {
                    "shoulder": joint,
                    "extension_m": (fist - joint).length,
                    "landed_mm": (landed - target).length * M_TO_MM,
                }

        applied = 0
        for curve in rigforge_rig.action_fcurves(action):
            for point in curve.keyframe_points:
                point.interpolation = interpolation
                applied += 1
            try:
                curve.update()
            except (AttributeError, RuntimeError):  # pragma: no cover
                pass

    scene.frame_set(previous_frame)
    refresh_view_layer()
    # After the frame restore, not before: an animated ID property is stamped
    # back onto the original pose bone every time the scene is evaluated, so a
    # restore that runs before the last frame_set is immediately overwritten by
    # the clip's own 0. The command has to leave the rig as it found it.
    stretch_restored = restore_ik_stretch(rig, planted_stretch["restore"])

    if modes:
        warnings.append(
            "Rotation mode changed to XYZ euler on %s so the planted foot roll is one "
            "readable channel." % ", ".join(sorted(modes)))
    if planted_stretch["missing"]:
        warnings.append(
            "No %s property on %s, so nothing stops this rig's legs stretching under a "
            "hip turn the planted feet have to absorb."
            % (IK_STRETCH_PROP, ", ".join(planted_stretch["missing"])))
    if previous_action is not None and previous_action is not action:
        warnings.append(
            "%r was the action on %r and is now %r; %r was left in the file with a fake "
            "user, so nothing was lost."
            % (previous_action.name, rig.name, action.name, previous_action.name))

    # --- the numbers ------------------------------------------------------
    speeds = [(fist_track[index][0],
               (fist_track[index][1] - fist_track[index - 1][1]).length)
              for index in range(1, len(fist_track))]
    peak_speed_frame, peak_speed = max(speeds, key=lambda pair: pair[1]) \
        if speeds else (strike_index, 0.0)
    fps = float(getattr(getattr(scene, "render", None), "fps", 24) or 24)

    def _peak(key):
        if not measured[key]:
            return {"frame": None, "degrees": None}
        frame, degrees = max(measured[key], key=lambda pair: (pair[1], -pair[0]))
        return {"frame": frame, "degrees": round(degrees, 3)}

    rotation_lead = {name: _peak(name) for name in ("pelvis", "chest", "shoulder")}
    rotation_lead["fist"] = {"frame": strike_index, "degrees": None}
    ordered = [entry["frame"] for entry in
               (rotation_lead["pelvis"], rotation_lead["chest"],
                rotation_lead["shoulder"]) if entry["frame"] is not None]
    leads_correctly = ordered == sorted(ordered) and (
        not ordered or ordered[-1] <= strike_index)
    if not leads_correctly:
        warnings.append(
            "The rotation does not travel up the body in order (pelvis %s, chest %s, "
            "shoulder %s, fist %d). Raise 'lead_frames', or give the clip more frames."
            % (rotation_lead["pelvis"]["frame"], rotation_lead["chest"]["frame"],
               rotation_lead["shoulder"]["frame"], strike_index))

    extension_m = strike_measurement.get("extension_m", extension_planned)
    extension_ratio = extension_m / reach if reach > 1e-9 else None
    landed_mm = strike_measurement.get("landed_mm")
    within_cap = (extension_ratio is None
                  or extension_ratio <= max_extension + EXTENSION_TOLERANCE)
    if not within_cap:
        warnings.append(
            "Measured on the posed rig the fist ends %.0f mm from the shoulder, %.1f%% "
            "of its %.0f mm reach — past the %.0f%% this command calls hyperextended. "
            "Shorten 'target_distance'."
            % (extension_m * 1000.0, extension_ratio * 100.0, reach * 1000.0,
               max_extension * 100.0))
    after_actions = sorted(a.name for a in bpy.data.actions)
    lost = sorted(set(before_actions) - set(after_actions))
    if lost:  # pragma: no cover - nothing here removes an action
        warnings.append("Action(s) %s went missing." % ", ".join(lost))

    return {
        "rig": rig.name,
        "action": action.name,
        "created": created,
        "loop": is_loop(action.name),
        "side": side,
        "frames": total_frames,
        "frame_range": [frames[0], frames[-1]],
        "strike_frame": strike_index,
        "chamber_frame": chamber_index,
        "lead_frames": lead,
        "peak_frames": dict(peaks),
        "rotation_lead": rotation_lead,
        "rotation_leads_in_order": leads_correctly,
        "hip_rotation_deg": round(hip_deg, 3),
        "chest_rotation_deg": round(chest_deg, 3),
        "shoulder_rotation_deg": round(shoulder_deg, 3),
        "arm_reach_m": round(reach, 5),
        "extension_m": round(extension_m, 5),
        "extension_planned_m": round(extension_planned, 5),
        "extension_ratio": round(extension_ratio, 5) if extension_ratio else None,
        "max_extension_ratio": round(max_extension, 4),
        "extension_tolerance": EXTENSION_TOLERANCE,
        "extension_within_cap": bool(within_cap),
        "fist_landed_mm": round(landed_mm, 3) if landed_mm is not None else None,
        "target": [round(v, 5) for v in target],
        "target_distance_m": round(target_distance, 5),
        "target_height_m": round(target_height, 5),
        "target_reach_clamped": target_clamped,
        "peak_fist_speed_frame": peak_speed_frame,
        "peak_fist_speed_m_per_frame": round(peak_speed, 5),
        "peak_fist_speed_m_per_s": round(peak_speed * fps, 3),
        "fps": fps,
        "guard": [round(v, 5) for v in guard],
        "guard_rise_m": round(guard_rise, 5),
        "chamber_draw_m": round(chamber_draw, 5),
        "weight_shift_m": round(weight_shift, 5),
        "weight_shift_clamped": shift_clamped,
        "hip_lower_m": round(hip_lower, 5),
        "hip_lower_deepened": stance_deepened,
        "shoulder_half_width_m": round(half_width, 5),
        "leg_length_m": round(leg_length, 5),
        "forward_axis": [round(v, 4) for v in forward],
        "aim_yaw_deg": round(math.degrees(aim_yaw), 3),
        "feet_planted": sorted(info["feet"][name]["target"] for name in info["feet"]),
        "convention": convention["convention"],
        "poles": convention["poles"],
        "ik_stretch": ik_stretch_report(planted_stretch, stretch_restored),
        "bones": bones_touched,
        "keys_set": keys_set,
        "cleared_fcurves": cleared,
        "fcurves": len(rigforge_rig.action_fcurves(action)),
        "interpolation": interpolation,
        "interpolated_points": applied,
        "rotation_modes": modes,
        "actions_in_file": after_actions,
        "says": (
            "%s: %d frames, %s fist fires from the chamber on frame %d and lands on "
            "frame %d. Peak fist speed %.2f m/s on frame %d — before the strike, not "
            "on it. Full extension %.0f mm against a measured reach of %.0f mm "
            "(%.0f%%, cap %.0f%%). Rotation peaks pelvis %s deg (f%s) -> chest %s deg "
            "(f%s) -> shoulder %s deg (f%s) -> fist (f%d). Both feet keyed on %s and "
            "never moved."
            % (action.name, total_frames, {"L": "left", "R": "right"}[side],
               chamber_index, strike_index, peak_speed * fps, peak_speed_frame,
               extension_m * 1000.0, reach * 1000.0,
               (extension_ratio or 0.0) * 100.0, max_extension * 100.0,
               rotation_lead["pelvis"]["degrees"], rotation_lead["pelvis"]["frame"],
               rotation_lead["chest"]["degrees"], rotation_lead["chest"]["frame"],
               rotation_lead["shoulder"]["degrees"], rotation_lead["shoulder"]["frame"],
               strike_index,
               " and ".join(sorted(info["feet"][n]["target"] for n in info["feet"])))),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# rigforge_jump — a standing jump whose clock is gravity
# ---------------------------------------------------------------------------
#
# The walk plants the feet.  The punch plants them harder.  A jump is the first
# clip in this module where the feet are *supposed* to leave the ground, and it
# is therefore the first one that can be wrong in a way neither of those two can
# be: it can **float**.
#
# Floating is not a matter of taste either.  A body in flight is not being
# animated, it is being *integrated*: from the instant the toes leave the floor
# to the instant the heels touch it, the only thing acting on the character is
# gravity, and the height curve is
#
#     z(t) = v*t - g*t^2 / 2,      v = sqrt(2 * g * apex_height)
#
# with airtime ``2*sqrt(2*apex_height/g)``.  So **apex_height is the parameter
# and the airtime falls out of it.**  This command will not accept a frame count
# for the flight, because a frame count for the flight is exactly the mistake:
# key a 12-frame hang on a 5 cm hop and the character is on the moon, key a
# 4-frame hang on a 60 cm leap and it is being yanked down on a string.  The
# report quotes the implied airtime and the maximum deviation from the parabola
# in millimetres, and ``animation_check``'s jump reading gates them.
#
# One honest quantisation, stated rather than hidden
# --------------------------------------------------
# The airtime lands between frames far more often than not.  Rounding it to
# whole frames and keying the *requested* apex anyway would put the landing
# somewhere between two keys, which is a discontinuity right where the impact
# is.  So the airtime is rounded to whole frames and then the apex is **solved
# back out of the rounded airtime** (``apex = g * airtime^2 / 8``): the parabola
# closes exactly on the landing frame, and the report prints apex reached
# against apex requested so the difference is visible instead of silent.
#
# The six phases, and what each one is for
# ----------------------------------------
# * **guard** (frame 1) — standing.  The clip starts and ends here.
# * **anticipation** — the hips drop into a crouch *over stationary feet*, the
#   arms swing back, the chest pitches forward.  This is the only place the
#   energy for the jump can come from, and a jump without it reads as a
#   character being lifted rather than jumping.
# * **launch** — the hips accelerate up and the legs extend, **capped against
#   the legs' own measured reach** the same way the punch caps the fist against
#   the arm's: past ~98% of thigh+shin the knee is hyperextended, Rigify's IK
#   stretch makes up the difference, and a stretched leg does not arrive where
#   it was keyed.  The heel rolls positive over the ball, so the foot leaves the
#   floor heel-first and **toes last**.
# * **airborne** — the root follows the parabola; the legs tuck by a
#   parameterised amount (the feet rise *relative to the root*, which is what a
#   knee bend is when the foot is keyed on an IK target) and the toes point.
# * **landing** — the feet plant heel-first at the takeoff spot, or
#   ``jump_distance`` ahead of it, and the knees flex to absorb.  The absorb is
#   deeper than the anticipation crouch by default, because catching a falling
#   body takes more travel than launching a standing one.
# * **recover** — ease back to the guard the clip started in.
#
# Everything is a fraction of this rig's own leg: apex, crouch, absorb, tuck.
# Nothing here is a constant in metres.

#: Standard gravity.  A parameter, because a low-gravity jump is a real art
#: direction and the point of deriving the timing is that it stays derived.
STANDARD_GRAVITY = 9.80665

JUMP_DEFAULT_FRAMES = 36

#: Defaults as fractions of the leg's measured length.
APEX_HEIGHT_RATIO = 0.35      #: how high the hips rise at the top of the arc
CROUCH_DEPTH_RATIO = 0.16     #: the anticipation crouch
TUCK_HEIGHT_RATIO = 0.30      #: how far the feet rise toward the hips in flight
MAX_CROUCH_RATIO = 0.45       #: no crouch may take the hips lower than this

#: The landing absorb, as a multiple of the anticipation crouch.  Deeper on
#: purpose: the anticipation only has to load a standing body, the absorb has to
#: catch a falling one.
LANDING_DEPTH_FACTOR = 1.5

#: The grounded phases, as fractions of the clip.  The airborne window is *not*
#: in this table — it is derived from the apex — so these four plus the flight
#: are what set the clip's real length.
JUMP_ANTICIPATION_FRACTION = 0.22
JUMP_LAUNCH_FRACTION = 0.08
JUMP_LANDING_FRACTION = 0.12
JUMP_RECOVER_FRACTION = 0.22

#: How far the hips rise above standing height at full extension, as a fraction
#: of leg length — before the reach cap below cuts it down to what the legs
#: actually have.  A rig whose rest pose is already straight has nothing to
#: spend here and is told so.
JUMP_EXTENSION_RISE_RATIO = 0.06

#: The hard ceiling on hip-to-ankle distance at full extension, as a fraction of
#: the leg's own measured reach (thigh + shin along the rest chain).  The exact
#: argument as :data:`MAX_EXTENSION_RATIO` makes for the arm.
MAX_LEG_EXTENSION_RATIO = 0.98
LEG_EXTENSION_TOLERANCE = 0.01

JUMP_CHEST_PITCH_DEG = 14.0
JUMP_ARM_BACK_DEG = 35.0
JUMP_ARM_UP_DEG = 110.0
JUMP_ELBOW_BEND_DEG = 20.0
JUMP_FOOT_ROLL_DEG = 22.0

# --- the countermovement, and why the old crouch did not read --------------
#
# The anticipation used to be a plumb drop: 128 mm of hip travel straight down
# a vertical line, in 0.333 s, with the trunk left vertical, the ankles pinned
# and the feet 11.7 mm through the floor.  Every one of those numbers was
# *delivered to spec* — and the reviewer could not see a crouch, because a
# plumb drop is not what a countermovement jump looks like.  A real load goes
# **down and back**: the hips travel rearward over the heels, the trunk folds
# forward over the knees to keep the centre of mass over the feet, the ankles
# dorsiflex, and the whole thing takes long enough to read.  The drive is then
# the mirror image — **up and forward**.  These four defaults are that shape.

#: How far the hips travel *rearward* at the bottom of the load, as a fraction
#: of the crouch depth.  The gate wants >= 0.3; a real countermovement is
#: nearer half, which is where this sits.  Measured before: 0.00 mm.
JUMP_HIP_SETBACK_RATIO = 0.5

#: ...and how far forward of the guard the hips are driven at takeoff, as a
#: fraction of the same setback.  This is the "and forward" half: a body that
#: loads backwards and launches straight up has thrown its weight nowhere.
JUMP_HIP_DRIVE_RATIO = 0.35

#: How far the **torso control itself** folds forward at the bottom of the
#: load.  Distinct from ``chest_pitch_deg``, which is the upper spine's own
#: lean on top of this: the trunk folding is the hip hinge, and it is the
#: single biggest thing missing from the old crouch's silhouette.
JUMP_TORSO_FOLD_DEG = 20.0

#: Toe lift at the bottom of the load, in degrees, on the toe control.  The
#: ankle work goes here rather than on ``foot_heel_ik`` for a measured reason:
#: the heel control pivots the whole foot, which moves the **ball** — the exact
#: point the foot-slide gate measures — whereas the toe control rotates about
#: the ball and leaves it where it was keyed to 0.0 mm.  Weight back on the
#: heels, toes light, is what a load does; it is also the only version of it
#: that a planted-foot clip can afford.
JUMP_LOAD_TOE_LIFT_DEG = 9.0

#: The shortest anticipation the reviewer could read, in seconds.  The old
#: default was 8 frames = 0.333 s at 24 fps and the note was "it is fast".  The
#: window is lengthened to meet this before the clip is laid out, and the
#: report quotes what it ended up with.
JUMP_MIN_ANTICIPATION_S = 0.35

#: How far the evaluated mesh may sit below the sole plane before the crouch is
#: pulled back, in metres.  Not zero: a skinned surface has float dust on it.
JUMP_FLOOR_TOLERANCE_M = 0.001

#: The heel-strike angle at landing contact, as a fraction of ``foot_roll_deg``.
#: Small on purpose: the roll pivots about the heel, which lifts the **ball** —
#: the very point the foot-slide metric measures — so a theatrical heel strike
#: buys a landing that reads as not-yet-planted for several frames.
JUMP_LANDING_STRIKE_RATIO = 0.25


def _track_at(frame, anchors):
    """Smoothstep between successive ``(frame, value)`` anchors.

    This is the animator's own working method written down: a channel is a
    short table of *key poses* and the frames they land on, and the computer
    fills in between them.  Smoothstep because it leaves and arrives at every
    anchor with zero slope, so a pose the clip passes through **holds** for an
    instant instead of being crossed at speed — the same argument
    :func:`_rise_fall` makes for the punch's kinetic chain, and the reason the
    crouch reads as a crouch rather than as a bounce.

    Evaluated per frame so ``LINEAR`` keyframe handles reproduce it exactly.
    Anchors must be non-decreasing in frame; where two share a frame the later
    one wins, which is how a hold is written.
    """
    if frame <= anchors[0][0]:
        return anchors[0][1]
    for index in range(1, len(anchors)):
        left_frame, left_value = anchors[index - 1]
        right_frame, right_value = anchors[index]
        if frame <= right_frame:
            if right_frame <= left_frame:
                return right_value
            span = _smoothstep((frame - left_frame) / float(right_frame - left_frame))
            return left_value + (right_value - left_value) * span
    return anchors[-1][1]


#: Hip, knee, ankle — as ``(metarig tag, FK control)`` pairs, best first.  The
#: deform bone is preferred for a reason that is not cosmetic: see
#: :func:`jump_legs`.
JUMP_LEG_JOINTS = (("thigh.%s", "thigh_fk.%s"), ("shin.%s", "shin_fk.%s"),
                   ("foot.%s", "foot_fk.%s"))


def jump_legs(rig, limbs, info):
    """Each leg's hip, knee, ankle and **measured** reach, at rest.

    ``reach`` is thigh + shin summed along the rest chain — not the straight
    line from hip to ankle, because a leg with an anatomical pre-bend (which is
    what ``rigforge_landmarks.prebend_joint`` puts there, and what
    ``rig_check``'s ``bend_direction`` gate insists on) measures short that way
    and every default derived from it would come out cramped.

    The three joints are taken from the **deform** chain, and that is the whole
    subtlety of this function.  A jump keys its legs through the IK targets, so
    for the entire clip ``thigh_fk`` and ``foot_fk`` sit exactly where they sit
    at rest — they are not driven, they are the *other* half of the switch.
    Measure an extension ratio on them and every frame of every jump reports the
    rest pose's ratio, which is a number that looks plausible, never moves, and
    means nothing.  The deform bones are what the solver drives, so they are
    what a posed measurement can be taken on; the reach is summed between the
    same three heads, so the ratio is one triangle rather than two.
    """
    out = {}
    for entry in limbs:
        if entry["limb"] != "leg":
            continue
        foot = info["feet"].get(entry["name"])
        if foot is None or foot["hip"] is None:
            continue
        side = entry["side"]
        names = []
        for tag, fallback in JUMP_LEG_JOINTS:
            found = rigforge_rig.def_bones_for(rig, tag % side)
            name = found[0] if found and found[0] in rig.pose.bones else None
            if name is None and (fallback % side) in rig.pose.bones:
                name = fallback % side
            if name is not None:
                names.append(name)
        if len(names) < 3:
            continue
        points = [_rest_world(rig, rig.pose.bones[name]).translation.copy()
                  for name in names]
        reach = sum((points[index + 1] - points[index]).length
                    for index in range(len(points) - 1))
        hip, ankle = points[0], points[-1]
        if reach <= 1e-6:
            reach = (hip - ankle).length
        out[entry["name"]] = {
            "entry": entry, "foot": foot, "reach": reach,
            "hip": hip, "ankle": ankle,
            "hip_bone": names[0], "knee_bone": names[1], "ankle_bone": names[-1],
            "rest_span": (hip - ankle).length,
        }
    return out


def _extension_headroom(legs, cap):
    """How far the hips may rise over planted feet before a knee hyperextends.

    With the foot nailed to the floor the hip socket is ``sqrt(flat^2 + drop^2)``
    from the ankle; raising the hips by ``r`` grows ``drop``.  The cap is
    ``cap * reach``, so ``r <= sqrt((cap*reach)^2 - flat^2) - drop`` and the
    answer is the tightest leg's.  ``None`` when there is no leg to measure.
    """
    limit = None
    for leg in legs.values():
        hip, ankle = leg["hip"], leg["ankle"]
        flat = Vector((hip.x - ankle.x, hip.y - ankle.y, 0.0)).length
        drop = hip.z - ankle.z
        span = cap * leg["reach"]
        room = math.sqrt(max(0.0, span * span - flat * flat))
        headroom = room - drop
        limit = headroom if limit is None else min(limit, headroom)
    return limit


@command("rigforge_jump")
def cmd_rigforge_jump(params):
    """Author a standing jump whose airtime is derived from its apex height.

    ``rigforge_jump {"rig"?, "action"?, "frames"?, "apex_height"?,
    "jump_distance"?, "crouch_depth"?, "landing_depth"?, "tuck_height"?,
    "anticipation_fraction"?, "launch_fraction"?, "landing_fraction"?,
    "recover_fraction"?, "gravity"?, "chest_pitch_deg"?, "arm_swing_back_deg"?,
    "arm_swing_up_deg"?, "elbow_bend_deg"?, "foot_roll_deg"?,
    "landing_strike_ratio"?, "max_extension_ratio"?, "loop"?, "clear"?,
    "interpolation"?, "poles"?}``

    Every length is metres and every default is a fraction of *this* rig's own
    leg, measured off the rest pose.  See the section header above for what each
    phase is and why the airtime is not something the caller gets to pick.
    """
    started = time.monotonic()
    warnings = []
    rig = _rig_for(None, params, key="rig", required=True)
    scene = get_scene()

    total_frames = get_int(params, "frames", JUMP_DEFAULT_FRAMES,
                           minimum=12, maximum=600)
    gravity = get_float(params, "gravity", STANDARD_GRAVITY, minimum=0.1, maximum=100.0)
    anticipation_fraction = get_float(params, "anticipation_fraction",
                                      JUMP_ANTICIPATION_FRACTION,
                                      minimum=0.05, maximum=0.5)
    launch_fraction = get_float(params, "launch_fraction", JUMP_LAUNCH_FRACTION,
                                minimum=0.02, maximum=0.3)
    landing_fraction = get_float(params, "landing_fraction", JUMP_LANDING_FRACTION,
                                 minimum=0.02, maximum=0.4)
    recover_fraction = get_float(params, "recover_fraction", JUMP_RECOVER_FRACTION,
                                 minimum=0.05, maximum=0.5)
    max_extension = get_float(params, "max_extension_ratio", MAX_LEG_EXTENSION_RATIO,
                              minimum=0.3, maximum=1.0)
    chest_pitch = math.radians(get_float(params, "chest_pitch_deg",
                                         JUMP_CHEST_PITCH_DEG, minimum=0.0,
                                         maximum=45.0))
    torso_fold = math.radians(get_float(params, "torso_fold_deg",
                                        JUMP_TORSO_FOLD_DEG, minimum=0.0,
                                        maximum=60.0))
    toe_lift = get_float(params, "load_toe_lift_deg", JUMP_LOAD_TOE_LIFT_DEG,
                         minimum=0.0, maximum=45.0)
    min_anticipation_s = get_float(params, "anticipation_seconds",
                                   JUMP_MIN_ANTICIPATION_S, minimum=0.0, maximum=3.0)
    floor_clamp = get_bool(params, "floor_clamp", True)
    arm_back = math.radians(get_float(params, "arm_swing_back_deg", JUMP_ARM_BACK_DEG,
                                      minimum=0.0, maximum=90.0))
    arm_up = math.radians(get_float(params, "arm_swing_up_deg", JUMP_ARM_UP_DEG,
                                    minimum=0.0, maximum=170.0))
    elbow_bend = math.radians(get_float(params, "elbow_bend_deg", JUMP_ELBOW_BEND_DEG,
                                        minimum=0.0, maximum=120.0))
    roll_deg = get_float(params, "foot_roll_deg", JUMP_FOOT_ROLL_DEG,
                         minimum=0.0, maximum=60.0)
    strike_ratio = get_float(params, "landing_strike_ratio", JUMP_LANDING_STRIKE_RATIO,
                             minimum=0.0, maximum=1.0)
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS}, "LINEAR")
    clear = get_bool(params, "clear", True)
    loop = get_bool(params, "loop", False) if params.get("loop") is not None else False

    limbs = rigforge_rig.ik_limbs(rig)
    leg_entries = [entry for entry in limbs if entry["limb"] == "leg"]
    if len(leg_entries) < 2:
        raise ForgeError(
            "rigforge_jump takes off and lands on the leg IK targets, and %r has %d "
            "leg(s) with one (it needs foot_ik.L and foot_ik.R with an IK_FK switch on "
            "thigh_parent.L/R). Generate the rig with rigforge_generate_rig, or run "
            "rigforge_ik to see what this rig actually has. A jump whose legs are keyed "
            "in FK cannot plant its takeoff or its landing, which is the foot-slide "
            "anti-pattern animation_check measures in millimetres."
            % (rig.name, len(leg_entries)))

    info = locomotion_frame(rig, limbs)
    forward, right, up = info["forward"], info["right"], info["up"]
    leg_length = info["leg_length"]
    legs = jump_legs(rig, limbs, info)
    if not legs:
        raise ForgeError(
            "rigforge_jump measures the legs' reach off their own rest chain, and no leg "
            "on %r has both a hip (thigh_fk) and a foot IK target to measure between."
            % rig.name)
    leg_reach = min(leg["reach"] for leg in legs.values())

    apex_height = get_float(params, "apex_height", APEX_HEIGHT_RATIO * leg_length,
                            minimum=1e-4, maximum=5.0 * leg_length)
    jump_distance = get_float(params, "jump_distance", 0.0,
                              minimum=0.0, maximum=6.0 * leg_length)
    crouch_depth = get_float(params, "crouch_depth", CROUCH_DEPTH_RATIO * leg_length,
                             minimum=0.0, maximum=MAX_CROUCH_RATIO * leg_length)
    landing_depth = get_float(params, "landing_depth",
                              min(LANDING_DEPTH_FACTOR * crouch_depth,
                                  MAX_CROUCH_RATIO * leg_length),
                              minimum=0.0, maximum=MAX_CROUCH_RATIO * leg_length)
    tuck_height = get_float(params, "tuck_height", TUCK_HEIGHT_RATIO * leg_length,
                            minimum=0.0, maximum=leg_length)
    hip_setback = get_float(params, "hip_setback",
                            JUMP_HIP_SETBACK_RATIO * crouch_depth,
                            minimum=0.0, maximum=leg_length)
    if landing_depth <= crouch_depth and params.get("landing_depth") is None:
        warnings.append(
            "The landing absorb (%.0f mm) is no deeper than the anticipation crouch "
            "(%.0f mm) because the crouch cap (%.0f%% of a %.0f mm leg) caught it "
            "first. Catching a falling body wants more travel than launching a "
            "standing one; shorten 'crouch_depth' to get it back."
            % (landing_depth * 1000.0, crouch_depth * 1000.0,
               MAX_CROUCH_RATIO * 100.0, leg_length * 1000.0))

    # --- the clock, and the one place physics wins -------------------------
    fps = 24.0
    render = getattr(scene, "render", None)
    if render is not None:
        fps = (float(getattr(render, "fps", 24) or 24)
               / float(getattr(render, "fps_base", 1.0) or 1.0))
    airtime_requested = 2.0 * math.sqrt(2.0 * apex_height / gravity)
    airborne_frames = max(2, int(round(airtime_requested * fps)))
    # Solve the apex back out of the rounded airtime, so the parabola closes
    # exactly on the landing frame instead of between two keys.
    airtime = airborne_frames / fps
    launch_speed = 0.5 * gravity * airtime
    apex_actual = launch_speed * launch_speed / (2.0 * gravity)
    if abs(apex_actual - apex_height) > 1e-6:
        warnings.append(
            "apex_height was resolved from %.1f mm to %.1f mm: %.3f s of airtime at "
            "%.3g m/s^2 is %.2f frames at %.3g fps, and the flight was rounded to %d "
            "whole frames so the parabola lands on a key instead of between two. The "
            "airtime is what is real here; the apex follows from it."
            % (apex_height * 1000.0, apex_actual * 1000.0, airtime_requested, gravity,
               airtime_requested * fps, fps, airborne_frames))

    anticipation = max(2, int(round(anticipation_fraction * total_frames)))
    # The load has to last long enough to read. `anticipation_fraction` says
    # what share of the clip it wants; this says what it may not go under, in
    # seconds, because 0.333 s of load is the number the review called "fast"
    # and a fraction of a short clip is how it got there. The clip lengthens to
    # hold it - the warning below says so - rather than the load being squeezed.
    anticipation_floor = int(math.ceil(min_anticipation_s * fps - 1e-9))
    anticipation_lengthened = anticipation_floor > anticipation
    if anticipation_lengthened:
        warnings.append(
            "The anticipation was lengthened from %d frames (%.3f s) to %d (%.3f s) to "
            "meet the %.2f s floor: a countermovement that loads faster than that does "
            "not read as a crouch, whatever depth it reaches. The clip grows to hold it."
            % (anticipation, anticipation / fps, anticipation_floor,
               anticipation_floor / fps, min_anticipation_s))
        anticipation = anticipation_floor
    launch = max(2, int(round(launch_fraction * total_frames)))
    landing = max(2, int(round(landing_fraction * total_frames)))
    recover = max(2, int(round(recover_fraction * total_frames)))
    needed = 1 + anticipation + launch + airborne_frames + landing + recover
    if needed > total_frames:
        warnings.append(
            "The clip was lengthened from %d frames to %d: %.1f mm of apex is %d frames "
            "of flight at %.3g fps, and the grounded phases asked for %d more. Airtime "
            "is not a frame budget - compressing it is how a jump starts reading as a "
            "character on a string."
            % (total_frames, needed, apex_actual * 1000.0, airborne_frames, fps,
               needed - 1 - airborne_frames))
        total_frames = needed
    else:
        recover += total_frames - needed  # the settle is the phase that may stretch

    f_crouch = 1 + anticipation
    f_takeoff = f_crouch + launch
    f_land = f_takeoff + airborne_frames
    f_absorb = f_land + landing
    end_frame = f_absorb + recover
    f_apex = f_takeoff + airborne_frames // 2
    frames = list(range(1, end_frame + 1))

    # --- the legs' own ceiling ---------------------------------------------
    headroom = _extension_headroom(legs, max_extension)
    wanted_rise = JUMP_EXTENSION_RISE_RATIO * leg_length
    extension_rise = wanted_rise if headroom is None else min(wanted_rise,
                                                              max(0.0, headroom))
    extension_clamped = headroom is not None and headroom < wanted_rise - 1e-9
    if extension_clamped:
        warnings.append(
            "The launch extension was cut from %.0f mm to %.0f mm: any further and the "
            "hip sits more than %.0f%% of the leg's own %.0f mm reach from a planted "
            "ankle, which is a hyperextended knee. Rigify's IK stretch makes up the "
            "difference and a stretched leg does not arrive where it was keyed."
            % (wanted_rise * 1000.0, extension_rise * 1000.0, max_extension * 100.0,
               leg_reach * 1000.0))
    if extension_rise <= 1e-6:
        rest_span = min(leg["rest_span"] for leg in legs.values())
        warnings.append(
            "There is no extension left to launch with: standing still the hip is "
            "already %.0f mm from the ankle against a %.0f%% cap of %.0f mm, so the "
            "takeoff is carried entirely by the root leaving the ground. That is a rest "
            "pose whose legs are all but straight; rig_check's bend_direction gate is "
            "the one that says so properly, and the fix is the anatomical pre-bend "
            "upstream." % (rest_span * 1000.0, max_extension * 100.0,
                           max_extension * leg_reach * 1000.0))

    # --- the channels, as tables of key poses ------------------------------
    #
    # Rebuilt rather than written once, because the floor clamp below may shrink
    # the crouch and re-key: a table built from the parameters has to be built
    # from the *current* parameters.
    channels = {}

    def _build_channels(crouch, absorb, setback):
        drive = JUMP_HIP_DRIVE_RATIO * setback
        channels["hip"] = [
            (1, 0.0), (f_crouch, -crouch), (f_takeoff, extension_rise),
            (f_apex, 0.0), (f_land, 0.0), (f_absorb, -absorb), (end_frame, 0.0)]
        # Down **and back**, then up **and forward**: the hips travel rearward
        # over the heels through the load and are driven ahead of the guard at
        # takeoff. Before this channel existed the hips dropped a plumb line -
        # hip setback measured 0.00 mm at every frame of the load, which is the
        # single reason the crouch did not read.
        #
        # The **absorb** deliberately carries none of it. A catch is not a
        # load run backwards: the knee has to travel forward over a planted
        # foot to give, and pulling the hips rearward there cancels exactly
        # that travel (measured: the landing knee went from +64.4 mm forward
        # to -45.0 mm the moment the absorb was given a setback of its own).
        channels["setback"] = [
            (1, 0.0), (f_crouch, -setback), (f_takeoff, drive), (f_apex, 0.0),
            (f_land, 0.0), (f_absorb, 0.0), (end_frame, 0.0)]
        # The trunk folds forward over the load and extends through the drive.
        # Negative is forward, the same sign the chest pitch uses.
        #
        # The **catch** gets none of it, for the same reason the setback does
        # not: the fold pivots about the torso's own head, which swings the
        # pelvis - and with it the hip sockets - rearward, and that is exactly
        # the travel a landing knee needs forward. Measured, a fold on the
        # absorb took the landing knee from +64.4 mm forward to -9.2 mm. The
        # lean a landing does have is the chest's, which is where it always
        # was.
        channels["fold"] = [
            (1, 0.0), (f_crouch, -torso_fold), (f_takeoff, 0.15 * torso_fold),
            (f_apex, 0.0), (f_land, 0.0), (f_absorb, 0.0), (end_frame, 0.0)]
        channels["tuck"] = [(f_takeoff, 0.0), (f_apex, tuck_height), (f_land, 0.0)]
        channels["pitch"] = [
            (1, 0.0), (f_crouch, -chest_pitch), (f_takeoff, 0.15 * chest_pitch),
            (f_apex, 0.0), (f_land, -0.4 * chest_pitch), (f_absorb, -chest_pitch),
            (end_frame, 0.0)]
        channels["arm"] = [
            (1, 0.0), (f_crouch, -arm_back), (f_takeoff, arm_up),
            (f_apex, 0.6 * arm_up), (f_land, 0.3 * arm_up),
            (f_absorb, 0.15 * arm_up), (end_frame, 0.0)]
        channels["elbow"] = [
            (1, 0.0), (f_crouch, elbow_bend), (f_takeoff, 0.3 * elbow_bend),
            (f_apex, 0.6 * elbow_bend), (f_land, 0.2 * elbow_bend),
            (f_absorb, elbow_bend), (end_frame, 0.0)]
        # Heel roll: 0 flat, + rolls over the ball (heel off, toes last), -
        # pivots about the heel (the strike, toe up). Measured on Rigify's
        # generated foot roll, + moves the ball by 0.0 mm - which is why the
        # takeoff can roll hard and the landing may not.
        channels["roll"] = [
            (1, 0.0), (f_crouch, 0.0), (f_takeoff, roll_deg), (f_apex, roll_deg),
            (f_land, -strike_ratio * roll_deg),
            (min(f_land + 2, f_absorb), 0.0), (end_frame, 0.0)]
        # Toe work, on the one foot pivot that turns about the ball and so
        # leaves the measured plant point exactly where it was keyed: toes up
        # through the load (weight back on the heels, with the hips), pointed
        # through the flight, up again for the heel-first contact.
        #
        # The toes point in FLIGHT and nowhere else. Pointed on a grounded
        # frame they go through the floor, and the takeoff is the worst of
        # them: the heel is already rolled hard over the ball there, so a
        # pointed toe on top of it drove the sole 8.7 mm under the plane on the
        # synthetic rig.
        channels["toe"] = [
            (1, 0.0), (f_crouch, toe_lift), (f_takeoff, 0.0),
            (f_apex, -0.6 * toe_lift), (f_land, toe_lift),
            (min(f_land + 2, f_absorb), 0.0), (end_frame, 0.0)]

    _build_channels(crouch_depth, landing_depth, hip_setback)

    def _root_offset(frame):
        """The ballistic translation: zero on the ground, a parabola in flight."""
        if frame <= f_takeoff:
            return Vector((0.0, 0.0, 0.0))
        if frame >= f_land:
            return forward * jump_distance
        elapsed = (frame - f_takeoff) / fps
        rise = launch_speed * elapsed - 0.5 * gravity * elapsed * elapsed
        return up * rise + forward * (jump_distance * elapsed / airtime)

    def _ideal_rise(frame):
        if frame <= f_takeoff or frame >= f_land:
            return 0.0
        elapsed = (frame - f_takeoff) / fps
        return launch_speed * elapsed - 0.5 * gravity * elapsed * elapsed

    def _body_offset(frame):
        """Where the hips ride: the root's arc, the crouch/extend curve, the setback."""
        return (_root_offset(frame)
                + up * _track_at(frame, channels["hip"])
                + forward * _track_at(frame, channels["setback"]))

    # --- the action ---------------------------------------------------------
    default_name = "jump-forward" if jump_distance > 1e-6 else "jump"
    wanted_action = get_str(params, "action", default_name)
    wanted_action = loop_name(wanted_action, loop)
    before_actions = sorted(existing.name for existing in bpy.data.actions)
    action = bpy.data.actions.get(wanted_action)
    created = False
    if action is None:
        action = bpy.data.actions.new(wanted_action)
        created = True
    # Fake user whether we made it or found it, for the same reason the punch
    # does: a zero-user action is lost on save the moment something else is
    # assigned to this rig.
    action.use_fake_user = True

    keys_set = 0
    cleared = 0
    bones_touched = []
    modes = {}
    previous_frame = scene.frame_current
    previous_action = rig.animation_data.action if rig.animation_data else None

    def touched(name):
        if name and name not in bones_touched:
            bones_touched.append(name)

    root_track = []
    torso_track = []
    #: ``(frame, along-forward, height)`` for the hips, in the rig's own walking
    #: frame — what the anticipation gate reads the setback and the depth off.
    hip_track = []
    extension_track = []

    with object_mode():
        assign_action(rig, action)
        if clear:
            cleared = clear_action(action)

        # Legs IK (the plants, and the tuck), arms FK (a swing is an arc, which
        # is what FK is for) - keyframed at frame 1 so the export bake resolves
        # the rig that was authored.
        convention = rigforge_rig.apply_ik_convention(
            rig, legs="ik", arms="fk", poles=get_bool(params, "poles", True),
            keyframe_at=frames[0])
        for entry in convention["limbs"]:
            touched(entry["switch_bone"])

        # The takeoff plant, the landing plant and the absorb are all planted
        # feet, and the tuck in between is a fold rather than a stretch - so
        # the whole clip is keyed stretch-free. See plant_ik_stretch: with the
        # Rigify default of 1.0 this jump squashed its own legs -4.8% at the
        # apex instead of folding the knee.
        planted_stretch = plant_ik_stretch(rig, convention["limbs"],
                                           (frames[0], frames[-1]))
        keys_set += planted_stretch["keys"]
        for name in planted_stretch["bones"]:
            touched(name)

        def _control(names):
            for name in names:
                if name in rig.pose.bones:
                    return rig.pose.bones[name]
            return None

        root = _control(ROOT_CONTROLS)
        torso = _control(TORSO_CONTROLS)
        chest = _control(CHEST_CONTROLS)
        # The bone an animator - and every gate that reads this clip - calls
        # "the hips". Distinct from the torso control, which on a Rigify spine
        # sits at the spine's own pivot rather than at the pelvis.
        hips_bone = _control(("hips",)) or torso
        # ...and the hip JOINT, which is what the trunk actually folds about.
        # Averaged over the legs off their own rest chain, so it is the point
        # between the hip sockets rather than a control's origin.
        hip_pivot_rest = Vector((0.0, 0.0, 0.0))
        for leg in legs.values():
            hip_pivot_rest = hip_pivot_rest + leg["hip"]
        hip_pivot_rest = hip_pivot_rest / float(max(1, len(legs)))
        if torso is None:
            raise ForgeError(
                "rigforge_jump drives the body through the torso control, and %r has "
                "none of %s. Generate the rig with rigforge_generate_rig."
                % (rig.name, ", ".join(TORSO_CONTROLS)))
        if root is None:
            raise ForgeError(
                "rigforge_jump puts the ballistic arc on the root bone, and %r has none "
                "of %s. Godot's root-motion track wants a bone the skeleton does not "
                "deform with; without one there is nothing to put a jump's travel on, "
                "and animation_check measures the parabola on that same bone."
                % (rig.name, ", ".join(ROOT_CONTROLS)))
        if chest is torso:
            chest = None
            warnings.append(
                "This rig has no separate chest control, so the chest pitch was folded "
                "into the torso: a spine that is one bone cannot lean and rise "
                "independently.")

        arms = []
        for entry in limbs:
            if entry["limb"] != "arm":
                continue
            upper = next((rig.pose.bones[name] for name in entry["fk_chain"]
                          if "upper_arm" in name and name in rig.pose.bones), None)
            fore = next((rig.pose.bones[name] for name in entry["fk_chain"]
                         if "forearm" in name and name in rig.pose.bones), None)
            if upper is not None:
                arms.append({"side": entry["side"], "upper": upper, "fore": fore,
                             "upper_rest": _rest_world(rig, upper),
                             "fore_rest": _rest_world(rig, fore) if fore else None})
        if not arms:
            warnings.append("No FK arm control was found, so the arms do not swing - "
                            "and an arm swing is where a jump's read of effort lives.")

        rest_root = _rest_world(rig, root)
        rest_torso = _rest_world(rig, torso)
        rest_chest = _rest_world(rig, chest) if chest is not None else None

        heels = {}
        toes = {}
        toe_sign = {}
        for name, foot in info["feet"].items():
            heel = rig.pose.bones.get(foot.get("heel_pivot") or "")
            if heel is not None:
                if heel.rotation_mode == "QUATERNION":
                    modes[heel.name] = heel.rotation_mode
                    heel.rotation_mode = "XYZ"
                heels[name] = heel
            toe = rig.pose.bones.get(foot.get("toe_pivot") or "")
            if toe is not None:
                if toe.rotation_mode == "QUATERNION":
                    modes[toe.name] = toe.rotation_mode
                    toe.rotation_mode = "XYZ"
                toes[name] = toe
                # Which way local +X lifts the toe tip, derived from the bone's
                # own rest orientation rather than assumed: a rotation about
                # local X takes the bone's +Y (the direction it points) toward
                # its local +Z, so the tip rises when that axis points up. A
                # foot built on the other roll reads -1 here and the same
                # authored degrees still lift the toes.
                rest_toe = _rest_world(rig, toe)
                toe_sign[name] = 1.0 if rest_toe.col[2].z >= 0.0 else -1.0

        # The two ends the extension is measured between are the two ends the
        # reach was summed between (see `jump_legs`).
        thigh_probe = {name: leg["hip_bone"] for name, leg in legs.items()}
        ankle_probe = {name: leg["ankle_bone"] for name, leg in legs.items()}

        # One pass over the whole clip.  It is a function rather than a bare
        # loop so the floor clamp below can ask for a second pass with a
        # shallower crouch: everything written here is a pure function of the
        # `channels` table, so re-running it re-authors the clip rather than
        # layering on it (the action is cleared between passes), and the
        # determinism the suite pins is unchanged.
        def _author_pass():
            keys = 0
            del root_track[:]
            del torso_track[:]
            del hip_track[:]
            del extension_track[:]
            for frame in frames:
                scene.frame_set(frame)
                offset = _root_offset(frame)
                body = _body_offset(frame)
                tuck = (_track_at(frame, channels["tuck"])
                        if f_takeoff < frame < f_land else 0.0)

                # 1. The root carries the ballistic arc, and nothing else does. It
                #    is keyed first because every world matrix below is resolved
                #    through it.
                matrix = rest_root.copy()
                matrix.translation = rest_root.translation + offset
                _set_world(rig, root, matrix)
                keys += _key_transform(root, frame)
                touched(root.name)
                refresh_view_layer()

                # 2. The feet. Grounded: the rest position (or the landing spot),
                #    constant, which is why the plants cannot drift. Airborne: the
                #    root's arc plus the tuck, which is what a knee bend is when the
                #    foot is keyed on an IK target.
                for name in sorted(info["feet"]):
                    foot = info["feet"][name]
                    target = rig.pose.bones.get(foot["target"])
                    if target is None:
                        continue
                    matrix = foot["rest"].copy()
                    matrix.translation = foot["rest"].translation + offset + up * tuck
                    _set_world(rig, target, matrix)
                    keys += _key_transform(target, frame)
                    touched(target.name)
                    heel = heels.get(name)
                    if heel is not None:
                        heel.rotation_euler = (
                            math.radians(_track_at(frame, channels["roll"])), 0.0, 0.0)
                        heel.keyframe_insert("rotation_euler", frame=frame)
                        keys += 3
                        touched(heel.name)
                    toe = toes.get(name)
                    if toe is not None:
                        toe.rotation_euler = (
                            toe_sign[name] * math.radians(_track_at(frame, channels["toe"])),
                            0.0, 0.0)
                        toe.keyframe_insert("rotation_euler", frame=frame)
                        keys += 3
                        touched(toe.name)

                # 3. The hips: the crouch, the extension, the absorb - and the
                #    trunk folding forward over them.
                #
                #    The fold pivots about the **hip joint**, not about the
                #    torso control's own head, and that is not a detail. A
                #    countermovement trunk fold is a rotation at the hip; pivot
                #    it at the spine-base control instead and every point above
                #    that control - including Rigify's own `hips` box, whose
                #    head sits above and ahead of it - swings FORWARD while the
                #    torso goes back. Measured on the synthetic rig: torso.head
                #    -38.6 mm (rearward, correct) and hips.head +38.0 mm
                #    (forward), from one authored setback, so the clip read as
                #    a crouch on one bone and as its opposite on another.
                #    Pivoting at the hip socket keeps the whole pelvis with the
                #    setback and leans only what is above it.
                fold = _track_at(frame, channels["fold"])
                hip_pivot = hip_pivot_rest + body
                anchor = rest_torso.translation + body
                matrix = rest_torso.copy()
                matrix.translation = anchor
                if fold:
                    matrix = _rotate_about(matrix, right, fold, hip_pivot)
                _set_world(rig, torso, matrix)
                keys += _key_transform(torso, frame)
                touched(torso.name)
                refresh_view_layer()

                # 4. The chest pitch, set in world space and then pinned back onto
                #    the spine. The world matrix is what makes the lean a *lean*
                #    whatever the hips are doing underneath it; zeroing the location
                #    basis afterwards is the punch's head trick - the bone rides its
                #    parent and only its rotation is authored, so the arc the hips
                #    are on is not keyed into the spine twice.
                if chest is not None:
                    matrix = rest_chest.copy()
                    matrix.translation = rest_chest.translation + body
                    # The trunk's hinge first, about the same hip joint the
                    # torso turned about - the chest matrix is absolute, so
                    # without this the upper spine would stand back up and
                    # quietly cancel the fold - and then the chest's own lean
                    # on top of it, about wherever the hinge left it.
                    if fold:
                        matrix = _rotate_about(matrix, right, fold, hip_pivot)
                    matrix = _rotate_about(matrix, right,
                                           _track_at(frame, channels["pitch"]),
                                           matrix.translation)
                    _set_world(rig, chest, matrix)
                    chest.location = (0.0, 0.0, 0.0)
                    keys += _key_transform(chest, frame)
                    touched(chest.name)
                    refresh_view_layer()

                # 5. The arms. Both the same way, unlike a walk: a jump's arms swing
                #    back together and throw up together, because they are adding
                #    momentum rather than balancing a gait.
                swing = _track_at(frame, channels["arm"])
                bend = _track_at(frame, channels["elbow"])
                #    They ride the trunk's hinge too, for the same reason the
                #    chest does: an absolute world matrix that ignores the fold
                #    leaves the shoulders behind the body they hang off.
                for arm in arms:
                    matrix = arm["upper_rest"].copy()
                    matrix.translation = arm["upper_rest"].translation + body
                    if fold:
                        matrix = _rotate_about(matrix, right, fold, hip_pivot)
                    anchor = matrix.translation.copy()
                    matrix = _rotate_about(matrix, right, swing, anchor)
                    _set_world(rig, arm["upper"], matrix)
                    arm["upper"].location = (0.0, 0.0, 0.0)
                    keys += _key_transform(arm["upper"], frame)
                    touched(arm["upper"].name)
                    if arm["fore"] is not None:
                        refresh_view_layer()
                        bent = arm["fore_rest"].copy()
                        bent.translation = arm["fore_rest"].translation + body
                        if fold:
                            bent = _rotate_about(bent, right, fold, hip_pivot)
                        bent = _rotate_about(bent, right, bend,
                                             bent.translation.copy())
                        carried = _rotate_about(bent, right, swing, anchor)
                        _set_world(rig, arm["fore"], carried)
                        arm["fore"].location = (0.0, 0.0, 0.0)
                        keys += _key_transform(arm["fore"], frame)
                        touched(arm["fore"].name)
                refresh_view_layer()

                # --- measurement, on the posed rig rather than on the parameters --
                root_track.append((frame, (rig.matrix_world @ root.head).z,
                                   _ideal_rise(frame)))
                # SIGN CONVENTION, stated once and used everywhere below:
                # `forward` is the rig's own facing, flattened onto the ground
                # (ankle to toe tip - the same axis `rig_forward_axis`
                # returns).  **Setback is positive rearward**, i.e. along
                # -forward, and it is the hip's offset from the ANKLE LINE
                # rather than its absolute position, so a jump that travels
                # does not read its own travel as setback.
                #
                # And "the hips" is the hip JOINT - the average of the deform
                # thigh heads - not a spine control's origin.  Rigify's `hips`
                # control points downward, so its head sits about a third of
                # the way up the trunk; any forward fold swings that point
                # forward however far back the pelvis goes, which is a fact
                # about where the bone's head is and not about the pose.  The
                # control is measured too, and reported, so the difference is
                # visible rather than argued about.
                flat = Vector((forward.x, forward.y, 0.0))
                flat = flat.normalized() if flat.length > 1e-9 else forward

                def _behind(point, base):
                    offset = point - base
                    return -Vector((offset.x, offset.y, 0.0)).dot(flat)

                ankle_now = Vector((0.0, 0.0, 0.0))
                socket = Vector((0.0, 0.0, 0.0))
                for name in sorted(legs):
                    ankle_now = (ankle_now + rig.matrix_world
                                 @ rig.pose.bones[ankle_probe[name]].head)
                    socket = (socket + rig.matrix_world
                              @ rig.pose.bones[thigh_probe[name]].head)
                ankle_now = ankle_now / float(max(1, len(legs)))
                socket = socket / float(max(1, len(legs)))
                hips_now = rig.matrix_world @ hips_bone.head
                hip_track.append((frame, _behind(socket, ankle_now), socket.z,
                                  _behind(hips_now, ankle_now), hips_now.z))
                # The crouch and the absorb are read on the hip joint for the
                # same reason: it is the point the trunk folds about, so its
                # height is the depth the clip bought rather than the depth
                # plus whatever the lean did to a control above it.
                torso_track.append((frame, socket.z))
                # Every frame, not just the takeoff: the cap is a statement about
                # the whole clip, and the cheapest way to be sure the peak really is
                # at full extension is to look at all of them.
                for name in sorted(legs):
                    hip = rig.matrix_world @ rig.pose.bones[thigh_probe[name]].head
                    ankle = rig.matrix_world @ rig.pose.bones[ankle_probe[name]].head
                    extension_track.append((frame, name, (hip - ankle).length,
                                            legs[name]["reach"]))
            return keys

        # --- the floor clamp ------------------------------------------------
        #
        # The review's fourth complaint about the crouch was that part of the
        # drop was spent *below the floor*: the evaluated mesh's lowest vertex
        # went from +0.7 mm to -11.7 mm at the crouch bottom, so 12 mm of a
        # 130 mm load never appeared in the silhouette at all.  A deeper crouch
        # that sinks is not a deeper crouch.
        #
        # The sole plane is not guessed: it is the lowest point the *same*
        # evaluated mesh reaches on the guard frame, which is the pose the
        # landmarks' ground plane was fitted to.  If any frame goes below it,
        # the crouch and the absorb are pulled back by exactly the overshoot
        # and the clip is re-authored — a fixed-point iteration, capped at
        # three passes and never taking more than half the asked-for depth, so
        # a mesh that intersects the floor for a reason of its own says so in a
        # warning instead of grinding the crouch away to nothing.
        skinned = [obj for obj in bpy.data.objects
                   if obj.type == "MESH" and any(
                       getattr(mod, "type", "") == "ARMATURE"
                       and getattr(mod, "object", None) is rig
                       for mod in obj.modifiers)]

        # Which vertices *are* the sole.  Not the whole mesh: a deep absorb
        # legitimately takes the hips - and on a short-legged character the
        # crotch - below where they stood, and failing a jump for that would be
        # failing it for squatting.  What may never happen is the **foot**
        # going through the plane it is standing on, which is what the review
        # measured (+0.7 mm to -11.7 mm) and what "the feet sink" means.  The
        # sole is every vertex whose dominant deform weight is a foot or toe
        # bone; a mesh with no such weights falls back to all of it, because
        # something is better measured than nothing.
        sole_bones = set()
        for entry in limbs:
            if entry["limb"] not in ("leg", "front_leg"):
                continue
            for name in entry["deform_bones"]:
                if "foot" in name or "toe" in name or "paw" in name:
                    sole_bones.add(name)

        # Dominance is judged among the **deform** groups only.  A generated
        # mesh also carries the region tags the autotagger left on it
        # (``tag_Leg.L`` and friends, weight 1.0 everywhere they apply), and a
        # plain "largest weight wins" scan hands every vertex to a tag group
        # and finds no feet at all - which is how this measurement first came
        # back clamping the crouch against a swinging hand.
        deform_names = {bone.name for bone in rig.data.bones
                        if getattr(bone, "use_deform", False)}

        def _sole_indices(obj):
            groups = {group.index: group.name for group in obj.vertex_groups}
            deform = {index for index, name in groups.items() if name in deform_names}
            wanted = {index for index, name in groups.items() if name in sole_bones}
            if not wanted:
                return None
            out = []
            for vertex in obj.data.vertices:
                best, best_weight = None, 0.0
                for item in vertex.groups:
                    if item.group in deform and item.weight > best_weight:
                        best, best_weight = item.group, item.weight
                if best in wanted:
                    out.append(vertex.index)
            return out or None

        soles = {obj.name: _sole_indices(obj) for obj in skinned}

        def _lowest_z():
            """The lowest evaluated **sole** vertex of every mesh this rig deforms.

            ``None`` when no mesh has foot-weighted geometry: a sole plane that
            cannot be found is not a licence to measure something else.
            """
            depsgraph = bpy.context.evaluated_depsgraph_get()
            lowest = None
            for obj in skinned:
                indices = soles.get(obj.name)
                if not indices:
                    continue
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    matrix = evaluated.matrix_world
                    for index in indices:
                        if index >= len(mesh.vertices):
                            continue
                        z = (matrix @ mesh.vertices[index].co).z
                        if lowest is None or z < lowest:
                            lowest = z
                finally:
                    evaluated.to_mesh_clear()
            return lowest

        def _floor_scan(plane):
            """How far the sole goes below ``plane``, per grounded phase.

            Returns ``{"load": (mm below, frame), "catch": (...)}``.  Split by
            phase because the two are driven by *different* depths - the
            anticipation by ``crouch_depth``, the absorb by ``landing_depth`` -
            and scaling both because one of them sinks throws away depth the
            other never spent.  Only the grounded frames are scanned: between
            takeoff and landing the character is in the air by construction and
            the scan is the expensive half of an authoring pass.
            """
            out = {"load": (0.0, None), "catch": (0.0, None)}
            for frame in frames:
                if f_takeoff < frame < f_land:
                    continue
                phase = "load" if frame <= f_takeoff else "catch"
                scene.frame_set(frame)
                refresh_view_layer()
                low = _lowest_z()
                if low is None:
                    continue
                depth = plane - low
                if depth > out[phase][0]:
                    out[phase] = (depth, frame)
            return out

        def _reauthor(crouch, absorb):
            """Re-key the whole clip at these depths. Returns the key count."""
            _build_channels(crouch, absorb, hip_setback)
            clear_action(action)
            rigforge_rig.apply_ik_convention(
                rig, legs="ik", arms="fk", poles=get_bool(params, "poles", True),
                keyframe_at=frames[0])
            again = plant_ik_stretch(rig, convention["limbs"],
                                     (frames[0], frames[-1]))
            return again["keys"] + _author_pass()

        keys_set += _author_pass()
        floor_plane = None
        floor_passes = 1
        floor_before = None
        floor_after = None
        floor_frame = None
        crouch_clamped = False
        floor_solve = []
        floor_depth_independent = False
        if floor_clamp and skinned:
            scene.frame_set(frames[0])
            refresh_view_layer()
            floor_plane = _lowest_z()
            if floor_plane is None:
                warnings.append(
                    "The sole plane could not be found: no mesh skinned to %r has any "
                    "geometry whose heaviest deform weight is one of %s, so there is "
                    "nothing to measure a floor against and the crouch was not clamped."
                    % (rig.name, ", ".join(sorted(sole_bones)) or "a foot bone"))
        if floor_plane is not None:
            # --- the solve, not an iteration ---------------------------------
            #
            # The sole's dip is a monotone function of how far the hips travel
            # down in that phase, and we know one point of it exactly: at zero
            # depth the pose IS the guard frame, which is the pose the plane was
            # measured on, so ``dip(0) = 0``.  One authored pass gives a second
            # point, ``dip(1) = p``.  A secant through those two solves directly
            # for the scale that puts the sole on the plane - no backoff ladder,
            # and the answer does not depend on how big the first reading was.
            #
            # The first correction is deliberately the *conservative* end of
            # that solve (the chord of a curve that starts flat and steepens
            # lies above it, so it under-shoots the depth it could have kept),
            # so a second secant through the two **measured** points recovers
            # the depth the first one gave away.  At most: correct, recover,
            # verify.
            tolerance = JUMP_FLOOR_TOLERANCE_M
            target = 0.5 * tolerance
            asked = {"load": crouch_depth, "catch": landing_depth}
            scale = {"load": 1.0, "catch": 1.0}
            #: ``(scale, dip)`` samples per phase.  ``(0, 0)`` is not an
            #: assumption: at zero depth the pose is the guard frame, which is
            #: the frame the plane itself was measured on.
            samples = {"load": [(0.0, 0.0)], "catch": [(0.0, 0.0)]}
            floor_scale = 0.25

            def _worst(scan):
                row = max(scan.values(), key=lambda entry: entry[0])
                return row[0], row[1]

            def _solve(phase):
                """Where a secant through the bracketing samples puts ``target``."""
                rows = sorted(set(samples[phase]))
                below = [row for row in rows if row[1] <= target]
                above = [row for row in rows if row[1] > target]
                if not above:
                    return 1.0
                low = below[-1] if below else (0.0, 0.0)
                high = above[0]
                if high[1] - low[1] <= 1e-12:  # pragma: no cover - equal readings
                    return low[0]
                span = (target - low[1]) / (high[1] - low[1])
                return max(floor_scale,
                           min(1.0, low[0] + (high[0] - low[0]) * span))

            scan = _floor_scan(floor_plane)
            floor_before, floor_frame = _worst(scan)
            #: The deepest pose measured clear of the plane, and - for a rig
            #: where no pose is clear - the one that came closest.  A solve
            #: that ends worse than a pose it already measured has to hand
            #: that pose back; shrinking a crouch by 75% and keeping a *bigger*
            #: dip than it started with is the worst of both.
            best_clear = None
            best_effort = (floor_before, crouch_depth, landing_depth, dict(scale))
            if floor_before <= tolerance:
                best_clear = (crouch_depth, landing_depth, dict(scale))
            # The loop does not stop at "clear": a first correction from the
            # chord through (0, 0) is deliberately the conservative end of the
            # solve, and stopping there throws away depth the geometry would
            # have allowed (measured: 77 mm of crouch cut to 19 mm to buy
            # 3.24 mm, when 27 mm was clear).  It stops when the secant has
            # nothing left to say in either direction.
            for _attempt in range(3):
                moved = False
                for phase in ("load", "catch"):
                    samples[phase].append((scale[phase], scan[phase][0]))
                    wanted = _solve(phase)
                    # Shrink only a phase that is actually through the plane;
                    # a phase already clear may only be handed depth *back*.
                    if scan[phase][0] <= tolerance and wanted < scale[phase]:
                        continue
                    if abs(wanted - scale[phase]) > 1e-4:
                        scale[phase] = wanted
                        moved = True
                if not moved:
                    # Nothing left to solve. Either the pose is clear at the
                    # depth it asked for - the ordinary case, one pass, no
                    # re-authoring - or the dip did not move with the depth at
                    # all, in which case the depth is not what is putting the
                    # sole through the plane and backing off further would only
                    # cost the pose for nothing. The warning below tells them
                    # apart.
                    if _worst(scan)[0] > tolerance:
                        floor_depth_independent = True
                    break
                crouch_depth = asked["load"] * scale["load"]
                landing_depth = asked["catch"] * scale["catch"]
                # The absorb has to stay deeper than the crouch whatever the
                # solve does to them separately, and only ever by shrinking.
                ratio = (asked["catch"] / asked["load"]) if asked["load"] > 1e-9 else 1.0
                if ratio > 1.0 and landing_depth <= crouch_depth:
                    crouch_depth = landing_depth / ratio
                keys_set = _reauthor(crouch_depth, landing_depth)
                floor_passes += 1
                crouch_clamped = (crouch_depth < asked["load"] - 1e-9
                                  or landing_depth < asked["catch"] - 1e-9)
                scan = _floor_scan(floor_plane)
                floor_solve.append({
                    "pass": floor_passes,
                    "crouch_mm": round(crouch_depth * 1000.0, 2),
                    "absorb_mm": round(landing_depth * 1000.0, 2),
                    "load_mm": round(scan["load"][0] * M_TO_MM, 4),
                    "catch_mm": round(scan["catch"][0] * M_TO_MM, 4),
                })
                now = _worst(scan)[0]
                if now <= tolerance:
                    if best_clear is None or crouch_depth > best_clear[0]:
                        best_clear = (crouch_depth, landing_depth, dict(scale))
                # A shallower pose has to be better by more than the whole
                # tolerance to be worth having: trading 150 mm of crouch for
                # 0.09 mm of dip is the review's complaint in the other
                # direction.
                if now < best_effort[0] - tolerance:
                    best_effort = (now, crouch_depth, landing_depth, dict(scale))
                elif now > floor_before + 1e-9:
                    # Shallower and *worse*: the response is not monotone in
                    # the depth, so there is nothing here for a secant to
                    # solve. Stop before another pass costs more of the pose.
                    floor_depth_independent = True
                    break
            # A recovery pass is allowed to overshoot - that is what makes it a
            # solve rather than a ratchet - so if the clip ends through the
            # plane, the best pose it actually measured is what gets authored:
            # the deepest clear one, or failing that the shallowest dip.
            if _worst(scan)[0] > tolerance:
                if best_clear is not None:
                    keep = best_clear
                else:
                    keep = best_effort[1:]
                if (abs(keep[0] - crouch_depth) > 1e-9
                        or abs(keep[1] - landing_depth) > 1e-9):
                    crouch_depth, landing_depth, scale = keep
                    keys_set = _reauthor(crouch_depth, landing_depth)
                    floor_passes += 1
                    crouch_clamped = (crouch_depth < asked["load"] - 1e-9
                                      or landing_depth < asked["catch"] - 1e-9)
                    scan = _floor_scan(floor_plane)
                    floor_solve.append({
                        "pass": floor_passes, "restored": True,
                        "crouch_mm": round(crouch_depth * 1000.0, 2),
                        "absorb_mm": round(landing_depth * 1000.0, 2),
                        "load_mm": round(scan["load"][0] * M_TO_MM, 4),
                        "catch_mm": round(scan["catch"][0] * M_TO_MM, 4),
                    })
            floor_after, floor_frame = _worst(scan)
            if floor_after > JUMP_FLOOR_TOLERANCE_M and floor_depth_independent:
                warnings.append(
                    "The sole sits %.2f mm below its plane at frame %s and scaling the "
                    "crouch did not move it (%s). That is not a depth problem: "
                    "something other than how far the hips travel is putting this foot "
                    "through the floor - the heel roll, the landing strike, or geometry "
                    "that already intersects the ground at rest. The crouch was put "
                    "back to the best depth measured (%.0f mm) rather than ground away "
                    "for nothing."
                    % (floor_after * M_TO_MM, floor_frame,
                       "; ".join(
                           "%s %s" % (phase, ", ".join(
                               "%.0f%% -> %.2f mm" % (row[0] * 100.0, row[1] * M_TO_MM)
                               for row in sorted(set(samples[phase])) if row[0] > 0.0))
                           for phase in ("load", "catch")),
                       crouch_depth * 1000.0))
            elif floor_after > JUMP_FLOOR_TOLERANCE_M:
                warnings.append(
                    "The sole still reaches %.2f mm below its plane at frame %s after "
                    "%d pass(es), with the crouch solved down to %.0f mm and the absorb "
                    "to %.0f mm. A crouch that sinks spends its depth under the floor "
                    "instead of in the silhouette."
                    % (floor_after * M_TO_MM, floor_frame, floor_passes,
                       crouch_depth * 1000.0, landing_depth * 1000.0))
            elif crouch_clamped:
                warnings.append(
                    "The crouch was solved back to %.0f mm and the absorb to %.0f mm so "
                    "the sole stays on its plane: at the asked-for %.0f / %.0f mm it "
                    "went %.2f mm through. Solved from the measured dip in %d pass(es), "
                    "not stepped down."
                    % (crouch_depth * 1000.0, landing_depth * 1000.0,
                       asked["load"] * 1000.0, asked["catch"] * 1000.0,
                       floor_before * M_TO_MM, floor_passes))

        applied = 0
        for curve in rigforge_rig.action_fcurves(action):
            for point in curve.keyframe_points:
                point.interpolation = interpolation
                applied += 1
            try:
                curve.update()
            except (AttributeError, RuntimeError):  # pragma: no cover
                pass

    scene.frame_set(previous_frame)
    refresh_view_layer()
    # After the frame restore, not before: an animated ID property is stamped
    # back onto the original pose bone every time the scene is evaluated, so a
    # restore that runs before the last frame_set is immediately overwritten by
    # the clip's own 0. The command has to leave the rig as it found it.
    stretch_restored = restore_ik_stretch(rig, planted_stretch["restore"])

    if modes:
        warnings.append(
            "Rotation mode changed to XYZ euler on %s so the foot roll is one readable "
            "channel." % ", ".join(sorted(modes)))
    if planted_stretch["missing"]:
        warnings.append(
            "No %s property on %s, so nothing stops this rig's legs squashing instead "
            "of folding when the hips drop onto a planted foot."
            % (IK_STRETCH_PROP, ", ".join(planted_stretch["missing"])))
    if previous_action is not None and previous_action is not action:
        warnings.append(
            "%r was the action on %r and is now %r; %r was left in the file with a fake "
            "user, so nothing was lost."
            % (previous_action.name, rig.name, action.name, previous_action.name))

    # --- the numbers --------------------------------------------------------
    ground_z = root_track[0][1] if root_track else 0.0
    airborne_rows = [row for row in root_track if f_takeoff < row[0] < f_land]
    apex_reached = max((row[1] - ground_z for row in airborne_rows), default=0.0)
    parabola_deviation = max((abs((row[1] - ground_z) - row[2])
                              for row in airborne_rows), default=0.0)
    parabola_within = parabola_deviation <= max(1e-4, 0.01 * max(apex_actual, 1e-6))

    stand_z = torso_track[0][1] if torso_track else 0.0
    crouch_measured = stand_z - min((z for frame, z in torso_track
                                     if frame <= f_takeoff), default=stand_z)
    absorb_measured = stand_z - min((z for frame, z in torso_track
                                     if frame >= f_land), default=stand_z)
    absorb_deeper = absorb_measured > crouch_measured

    # --- does the anticipation read? ---------------------------------------
    # All four numbers the review's `anticipation_reads` gate asks for, taken
    # off the posed rig rather than off the parameters that asked for them.
    # ``hip_track`` rows are ``(frame, socket setback, socket height, control
    # setback, control height)`` with **rearward positive** (see the sign
    # convention where the track is filled).  So the setback the load buys is
    # simply how much further behind the ankles the hips finish than they
    # started, and it needs no sign gymnastics here.
    load_rows = [row for row in hip_track if row[0] <= f_crouch]
    stand_back = load_rows[0][1] if load_rows else 0.0
    stand_high = load_rows[0][2] if load_rows else 0.0
    stand_control = load_rows[0][3] if load_rows else 0.0
    bottom = min(load_rows, key=lambda row: row[2]) if load_rows else None
    hip_drop_measured = (stand_high - bottom[2]) if bottom else 0.0
    hip_setback_measured = max((row[1] - stand_back for row in load_rows), default=0.0)
    control_setback_measured = max((row[3] - stand_control for row in load_rows),
                                   default=0.0)
    setback_ratio = (hip_setback_measured / hip_drop_measured
                     if hip_drop_measured > 1e-9 else 0.0)
    anticipation_seconds = (f_crouch - 1) / fps
    # The drive is measured out of the load, not out of the guard: "up and
    # forward" is what the body does from the bottom of the countermovement,
    # and the hips are still behind the guard when the toes leave the floor -
    # which is correct, and would read as a negative drive against the guard.
    drive_rows = [row for row in hip_track if f_crouch <= row[0] <= f_takeoff]
    bottom_back = drive_rows[0][1] if drive_rows else stand_back
    hip_drive_measured = max((bottom_back - row[1] for row in drive_rows), default=0.0)

    extension_rows = [(span / reach, frame, name)
                      for frame, name, span, reach in extension_track if reach > 1e-9]
    extension_ratio = max(extension_rows)[0] if extension_rows else None
    extension_peak_frame = max(extension_rows)[1] if extension_rows else None
    tuck_ratio = min(extension_rows)[0] if extension_rows else None
    tuck_frame = min(extension_rows)[1] if extension_rows else None
    rest_ratio = max((leg["rest_span"] / leg["reach"] for leg in legs.values()
                      if leg["reach"] > 1e-9), default=0.0)
    # The cap governs what *this command adds*, which is why the ceiling is the
    # cap or the rest pose, whichever is already higher. A rig generated with no
    # anatomical pre-bend stands at 99% of its own chain length before anything
    # is keyed; failing the jump for that would be blaming the animation for a
    # rig defect, and the pre-bend warning above already names the real one.
    ceiling_ratio = max(max_extension, rest_ratio)
    extension_within_cap = (extension_ratio is None
                            or extension_ratio <= ceiling_ratio + LEG_EXTENSION_TOLERANCE)
    if not extension_within_cap:
        warnings.append(
            "Measured on the posed rig at takeoff the hip sits %.1f%% of the leg's %.0f "
            "mm reach from the ankle, past the %.1f%% ceiling (a %.0f%% cap, or the "
            "%.1f%% this rest pose already stands at). Lower 'max_extension_ratio', or "
            "shorten the crouch that bought it."
            % (extension_ratio * 100.0, leg_reach * 1000.0, ceiling_ratio * 100.0,
               max_extension * 100.0, rest_ratio * 100.0))
    if not parabola_within:
        warnings.append(
            "The root's measured height wanders %.2f mm from the parabola it was keyed "
            "from. That should be float dust; something else is driving this bone."
            % (parabola_deviation * M_TO_MM))
    if not absorb_deeper:
        warnings.append(
            "The landing absorb (%.0f mm) is not deeper than the anticipation crouch "
            "(%.0f mm), measured on the torso. A landing that does not give is a "
            "character hitting the floor, not catching itself."
            % (absorb_measured * 1000.0, crouch_measured * 1000.0))

    after_actions = sorted(existing.name for existing in bpy.data.actions)
    lost = sorted(set(before_actions) - set(after_actions))
    if lost:  # pragma: no cover - nothing here removes an action
        warnings.append("Action(s) %s went missing." % ", ".join(lost))

    return {
        "rig": rig.name,
        "action": action.name,
        "created": created,
        "loop": is_loop(action.name),
        "frames": total_frames,
        "frame_range": [frames[0], frames[-1]],
        "fps": round(fps, 4),
        "gravity": round(gravity, 5),
        "phases": {
            "guard": [1, 1],
            "anticipation": [1, f_crouch],
            "launch": [f_crouch, f_takeoff],
            "airborne": [f_takeoff + 1, f_land - 1],
            "landing": [f_land, f_absorb],
            "recover": [f_absorb, end_frame],
        },
        "crouch_frame": f_crouch,
        "takeoff_frame": f_takeoff,
        "apex_frame": f_apex,
        "landing_frame": f_land,
        "absorb_frame": f_absorb,
        "airborne_frames": airborne_frames,
        "airborne_keys": len(airborne_rows),
        "airtime_s": round(airtime, 5),
        "airtime_requested_s": round(airtime_requested, 5),
        "launch_speed_m_per_s": round(launch_speed, 5),
        "apex_requested_m": round(apex_height, 5),
        "apex_solved_m": round(apex_actual, 5),
        "apex_reached_m": round(apex_reached, 5),
        "apex_error_mm": round(abs(apex_reached - apex_actual) * M_TO_MM, 3),
        # The true peak is half a frame from the nearest key whenever the flight
        # is an even number of frames, and a body at the top of a parabola falls
        # ``g*dt^2/2`` in that half frame. Reported rather than hidden, because
        # it is the entire difference between the apex solved for and the apex a
        # sampled clip can actually show, and a gate that does not know that
        # number would call a correct jump short.
        "apex_peak_between_keys_mm": round(
            0.5 * gravity * (0.5 / fps) ** 2 * M_TO_MM, 4),
        "parabola_deviation_mm": round(parabola_deviation * M_TO_MM, 4),
        "parabola_within_tolerance": bool(parabola_within),
        "jump_distance_m": round(jump_distance, 5),
        "crouch_depth_m": round(crouch_depth, 5),
        "crouch_measured_m": round(crouch_measured, 5),
        # --- the countermovement, measured on the posed rig ------------------
        "anticipation_frames": anticipation,
        "anticipation_seconds": round(anticipation_seconds, 5),
        "anticipation_min_seconds": round(min_anticipation_s, 5),
        "anticipation_lengthened": bool(anticipation_lengthened),
        "hip_setback_m": round(hip_setback, 5),
        "hip_setback_measured_m": round(hip_setback_measured, 5),
        "hip_control_setback_measured_m": round(control_setback_measured, 5),
        "hip_setback_bone": "+".join(sorted(thigh_probe.values())),
        "hip_control_bone": hips_bone.name,
        "hip_setback_sign": "positive is rearward, along -forward_axis, "
                            "measured from the ankle line, on the hip joint "
                            "(the deform thigh heads)",
        "hip_drop_measured_m": round(hip_drop_measured, 5),
        "hip_setback_ratio": round(setback_ratio, 5),
        "hip_drive_measured_m": round(hip_drive_measured, 5),
        "torso_fold_deg": round(math.degrees(torso_fold), 3),
        "load_toe_lift_deg": round(toe_lift, 3),
        "floor_plane_z_m": (round(floor_plane, 6) if floor_plane is not None else None),
        "floor_penetration_mm": (round(floor_after * M_TO_MM, 4)
                                 if floor_after is not None else None),
        "floor_penetration_before_mm": (round(floor_before * M_TO_MM, 4)
                                        if floor_before is not None else None),
        "floor_penetration_frame": floor_frame,
        "floor_passes": floor_passes,
        "floor_solve": floor_solve,
        "floor_depth_independent": bool(floor_depth_independent),
        "sole_bones": sorted(sole_bones),
        "sole_vertices": {name: (len(value) if value is not None else None)
                          for name, value in sorted(soles.items())},
        "crouch_floor_clamped": bool(crouch_clamped),
        "meshes_measured": sorted(obj.name for obj in skinned),
        "ik_stretch": ik_stretch_report(planted_stretch, stretch_restored),
        "landing_depth_m": round(landing_depth, 5),
        "absorb_measured_m": round(absorb_measured, 5),
        "absorb_deeper_than_crouch": bool(absorb_deeper),
        "tuck_height_m": round(tuck_height, 5),
        "extension_rise_m": round(extension_rise, 5),
        "extension_rise_clamped": bool(extension_clamped),
        "extension_headroom_m": (round(headroom, 5) if headroom is not None else None),
        "leg_rest_span_m": round(min(leg["rest_span"] for leg in legs.values()), 5),
        "leg_reach_m": round(leg_reach, 5),
        "leg_length_m": round(leg_length, 5),
        "extension_ratio": (round(extension_ratio, 5)
                            if extension_ratio is not None else None),
        "extension_peak_frame": extension_peak_frame,
        "tuck_ratio": round(tuck_ratio, 5) if tuck_ratio is not None else None,
        "tuck_frame": tuck_frame,
        "legs": [{"limb": name,
                  "hip_bone": leg["hip_bone"],
                  "knee_bone": leg["knee_bone"],
                  "ankle_bone": leg["ankle_bone"],
                  "reach_m": round(leg["reach"], 5),
                  "rest_span_m": round(leg["rest_span"], 5)}
                 for name, leg in sorted(legs.items())],
        "max_extension_ratio": round(max_extension, 4),
        "rest_extension_ratio": round(rest_ratio, 5),
        "extension_ceiling_ratio": round(ceiling_ratio, 5),
        "extension_tolerance": LEG_EXTENSION_TOLERANCE,
        "extension_within_cap": bool(extension_within_cap),
        "foot_roll_deg": round(roll_deg, 3),
        "landing_strike_deg": round(strike_ratio * roll_deg, 3),
        "chest_pitch_deg": round(math.degrees(chest_pitch), 3),
        "arm_swing_back_deg": round(math.degrees(arm_back), 3),
        "arm_swing_up_deg": round(math.degrees(arm_up), 3),
        "forward_axis": [round(value, 4) for value in forward],
        "feet_planted": sorted(info["feet"][name]["target"] for name in info["feet"]),
        "convention": convention["convention"],
        "poles": convention["poles"],
        "bones": bones_touched,
        "keys_set": keys_set,
        "cleared_fcurves": cleared,
        "fcurves": len(rigforge_rig.action_fcurves(action)),
        "interpolation": interpolation,
        "interpolated_points": applied,
        "rotation_modes": modes,
        "actions_in_file": after_actions,
        "says": (
            "%s: %d frames. Apex %.0f mm reached against %.0f mm requested (solved to "
            "%.0f mm by the frame rounding), %d frames of airtime (%.3f s at %.3g fps, "
            "g = %.3g m/s^2), max parabola deviation %.3f mm. Leg extension peaks at "
            "%.1f%% of a %.0f mm reach (cap %.0f%%). Landing absorbs %.0f mm against a "
            "%.0f mm anticipation crouch. The load takes %.3f s and the hips travel "
            "%.0f mm back for %.0f mm down (setback ratio %.2f) before driving %.0f mm "
            "forward out of it; the sole stays %.1f mm clear of its plane. Feet "
            "planted on %s "
            "through takeoff and landing."
            % (action.name, total_frames, apex_reached * 1000.0, apex_height * 1000.0,
               apex_actual * 1000.0, airborne_frames, airtime, fps, gravity,
               parabola_deviation * M_TO_MM,
               (extension_ratio or 0.0) * 100.0, leg_reach * 1000.0,
               max_extension * 100.0, absorb_measured * 1000.0,
               crouch_measured * 1000.0,
               anticipation_seconds, hip_setback_measured * 1000.0,
               hip_drop_measured * 1000.0, setback_ratio,
               hip_drive_measured * 1000.0,
               -(floor_after or 0.0) * M_TO_MM,
               " and ".join(sorted(info["feet"][name]["target"]
                                   for name in info["feet"])))),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# rigforge_retarget
# ---------------------------------------------------------------------------

TEMP_COLLECTION = "FORGE_RETARGET_TEMP"

#: Source-bone name fragments -> the slot they mean.  Ordered, first match wins,
#: and the order is the whole design: "LeftForeArm" contains "arm", "LeftUpLeg"
#: contains "leg", so the specific spellings have to be tested before the loose
#: ones.  ``sided`` says whether the slot exists once or once per side.
BONE_RULES = (
    (("forearm", "fore_arm", "fore arm", "lowerarm", "lower_arm", "radius", "elbow"),
     "forearm", True),
    (("upperarm", "upper_arm", "upper arm", "humerus", "shldr"), "upperarm", True),
    (("shoulder", "clavicle", "collar"), "shoulder", True),
    (("hand", "wrist"), "hand", True),
    (("upleg", "up_leg", "up leg", "upperleg", "upper_leg", "thigh", "femur"),
     "thigh", True),
    (("lowerleg", "lower_leg", "foreleg", "shin", "calf", "tibia", "knee"),
     "shin", True),
    (("toebase", "toe_base", "toe", "ball"), "toe", True),
    (("foot", "ankle"), "foot", True),
    (("head",), "head", False),
    (("neck",), "neck", False),
    (("upperchest", "upper_chest", "chest", "spine2", "spine3", "spine.002",
      "spine.003", "thorax"), "chest", False),
    (("spine1", "spine.001", "abdomen", "waist", "spine"), "spine", False),
    (("hips", "hip", "pelvis", "root"), "hips", False),
    (("arm",), "upperarm", True),
    (("leg",), "shin", True),
)

#: Fragments that mean "not a bone this retarget can use": fingers (five of them
#: would all land on the hand control), and the zero-length terminators every
#: BVH exporter writes.
SKIP_FRAGMENTS = ("finger", "index", "middle", "thumb", "ring", "pinky",
                  "_end", "end site", "endsite", "nub", "_site", "eye", "jaw",
                  "tongue", "breast", "prop", "camera", "ik")

#: Slot -> the Rigify **control** bones that can receive it, best first.  Every
#: candidate is checked against the target rig, so the same table works on the
#: 29-bone basic human and the full one.  FK is deliberate: a retargeted clip
#: should be something the sculptor can open and fix, and an IK chain cannot
#: take a raw per-bone rotation at all.
#: The order inside a slot matters as much as the slot itself: Rigify's
#: ``basic_spine`` gives one control per *end* of the spine (``hips``, ``chest``,
#: with ``torso`` over both), so a clip's Hips/Spine/Chest has to be spread
#: across all three or the second bone silently loses its target to the first.
SLOT_TARGETS = {
    "hips": ("torso", "hips", "spine_fk", "root"),
    "spine": ("spine_fk.001", "hips", "tweak_spine.001", "chest"),
    "chest": ("chest", "spine_fk.002", "tweak_spine.002"),
    "neck": ("neck", "spine_fk.003", "tweak_spine.004"),
    "head": ("head", "tweak_spine.005"),
    "shoulder": ("shoulder.{S}",),
    "upperarm": ("upper_arm_fk.{S}", "upper_arm_ik.{S}", "upper_arm.{S}"),
    "forearm": ("forearm_fk.{S}", "forearm.{S}"),
    "hand": ("hand_fk.{S}", "hand_ik.{S}", "hand.{S}"),
    "thigh": ("thigh_fk.{S}", "thigh_ik.{S}", "thigh.{S}"),
    "shin": ("shin_fk.{S}", "shin.{S}"),
    "foot": ("foot_fk.{S}", "foot_ik.{S}", "foot.{S}"),
    "toe": ("toe_fk.{S}", "toe.{S}"),
}

#: The slot whose world travel becomes the rig's hip translation.
ROOT_SLOT = "hips"


def _normalise_bone(name):
    text = str(name).strip().lower()
    for prefix in ("mixamorig:", "mixamorig1:", "mixamorig", "bip01 ", "bip01_",
                   "bip001 ", "armature|", "character1_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip()


def _side_of(text):
    """``"L"``, ``"R"`` or ``None`` for a source bone name."""
    if "left" in text:
        return "L"
    if "right" in text:
        return "R"
    for suffix, side in ((".l", "L"), (".r", "R"), ("_l", "L"), ("_r", "R"),
                         ("-l", "L"), ("-r", "R"), (" l", "L"), (" r", "R")):
        if text.endswith(suffix):
            return side
    # CMU-style "lfemur" / "rtibia": a bare leading l/r in front of a word.
    if len(text) > 2 and text[0] in "lr" and text[1].isalpha():
        return "L" if text[0] == "l" else "R"
    return None


def classify_source_bone(name):
    """``(slot, side)`` for one source bone, or ``(None, reason)`` when unusable."""
    text = _normalise_bone(name)
    if not text:
        return None, "empty name"
    for fragment in SKIP_FRAGMENTS:
        if fragment in text:
            return None, "looks like %r, which this mapping skips" % fragment
    for needles, slot, sided in BONE_RULES:
        for needle in needles:
            if needle in text:
                if not sided:
                    return (slot, None), ""
                side = _side_of(text)
                if side is None:
                    return None, "no side (left/right, .L/.R) in a two-sided name"
                return (slot, side), ""
    return None, "no rule matched"


def auto_mapping(source_rig, target_rig):
    """Name-heuristic ``{source bone: target control bone}`` plus a report."""
    target_bones = {bone.name for bone in target_rig.pose.bones}
    mapped = []
    unmapped = []
    taken = {}
    for bone in source_rig.pose.bones:
        slot, reason = classify_source_bone(bone.name)
        if slot is None:
            unmapped.append({"source": bone.name, "reason": reason})
            continue
        slot_name, side = slot
        candidates = SLOT_TARGETS.get(slot_name, ())
        target = None
        for template in candidates:
            candidate = template.format(S=side) if side else template
            if candidate in target_bones:
                target = candidate
                break
        if target is None:
            unmapped.append({
                "source": bone.name,
                "reason": "slot %r has no control bone on this rig (tried %s)"
                          % (slot_name, ", ".join(
                              t.format(S=side) if side else t for t in candidates)
                             or "nothing")})
            continue
        if target in taken:
            unmapped.append({"source": bone.name,
                             "reason": "%r was already taken by %r"
                                       % (target, taken[target])})
            continue
        taken[target] = bone.name
        mapped.append({"source": bone.name, "target": target, "slot": slot_name,
                       "side": side})
    return mapped, unmapped


def explicit_mapping(raw, source_rig, target_rig):
    if not isinstance(raw, dict) or not raw:
        raise ForgeError("'mapping' must be \"auto\" or a non-empty "
                         '{"source bone": "target bone"} object.')
    source_bones = {bone.name for bone in source_rig.pose.bones}
    target_bones = {bone.name for bone in target_rig.pose.bones}
    mapped = []
    unmapped = []
    for source, target in raw.items():
        if not isinstance(target, str) or not target.strip():
            raise ForgeError("mapping[%r] must be a target bone name." % source)
        if source not in source_bones:
            unmapped.append({"source": str(source),
                             "reason": "not a bone of the imported clip"})
            continue
        if target.strip() not in target_bones:
            raise ForgeError("mapping[%r] names %r, which is not a bone of %r.%s"
                             % (source, target, target_rig.name,
                                _bone_hint(target_rig, target.strip())))
        mapped.append({"source": source, "target": target.strip(), "slot": "explicit",
                       "side": None})
    for bone in source_rig.pose.bones:
        if bone.name not in raw:
            unmapped.append({"source": bone.name, "reason": "not in the mapping"})
    return mapped, unmapped


def _armature_height(rig):
    points = []
    for bone in rig.data.bones:
        points.append(rig.matrix_world @ bone.head_local)
        points.append(rig.matrix_world @ bone.tail_local)
    if not points:
        return 0.0
    return max(p.z for p in points) - min(p.z for p in points)


def _import_clip(path, warnings):
    """Import a user-supplied .bvh/.fbx and return the objects it created.

    Nothing here reaches the network: both importers ship inside Blender and the
    file is one the sculptor already had.
    """
    extension = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if extension == ".bvh":
        operator = getattr(bpy.ops.import_anim, "bvh", None)
        if operator is None:
            raise ForgeError("This Blender has no BVH importer (bpy.ops.import_anim.bvh).")
        status = operator(**op_kwargs(operator, {
            "filepath": path,
            "target": "ARMATURE",
            "global_scale": 1.0,
            "use_fps_scale": False,
            "update_scene_fps": False,
            "update_scene_duration": False,
            "use_cyclic": False,
            "rotate_mode": "NATIVE",
        }))
        kind = "bvh"
    elif extension == ".fbx":
        operator = getattr(bpy.ops.import_scene, "fbx", None)
        if operator is None:
            raise ForgeError("This Blender has no FBX importer (bpy.ops.import_scene.fbx).")
        status = operator(**op_kwargs(operator, {
            "filepath": path,
            "use_anim": True,
            "automatic_bone_orientation": True,
            "ignore_leaf_bones": True,
            "global_scale": 1.0,
        }))
        kind = "fbx"
    else:
        raise ForgeError(
            "source_path must be a .bvh or .fbx file (got %r). Both importers ship "
            "with Blender; nothing is downloaded." % (extension or "no extension"))
    if "FINISHED" not in status:
        raise ForgeError("The %s importer returned %s for %s."
                         % (kind.upper(), ", ".join(sorted(status)) or "nothing", path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        raise ForgeError("%s imported without error but produced no objects." % path)
    return imported, kind


def _source_action(source_rig):
    data = getattr(source_rig, "animation_data", None)
    if data is None or data.action is None:
        return None
    return data.action


class _bones_bakeable(object):
    """Make exactly ``names`` selected and visible for a bake, then put it back.

    ``nla.bake(only_selected=True)`` reads ``context.selected_pose_bones``, which
    skips anything hidden — and Rigify parks the FK chains on bone collections
    that are hidden by default.  Baking *everything* instead is not an option
    here: a Rigify control rig is full of constrained MCH/ORG bones, and writing
    their evaluated transform into ``matrix_basis`` while the constraint still
    runs on top applies it twice.

    Where the flags live moved in Blender 5.0 — ``select`` and ``hide`` are on
    ``PoseBone`` now, and ``Bone`` kept only ``hide``/``hide_select`` — so every
    spelling is written when it exists and restored exactly as it was.
    """

    #: ``(holder, attribute, value while baking)``; ``None`` means "the name matches".
    _FLAGS = (("pose", "select", None), ("bone", "select", None),
              ("pose", "hide", False), ("bone", "hide", False),
              ("bone", "hide_select", False))

    def __init__(self, rig, names):
        self.rig = rig
        self.names = set(names)
        self._collections = []
        self._restore = []

    def __enter__(self):
        for collection in getattr(self.rig.data, "collections_all", ()):
            try:
                self._collections.append((collection, collection.is_visible))
                collection.is_visible = True
            except (AttributeError, TypeError):
                continue
        for pose_bone in self.rig.pose.bones:
            holders = {"pose": pose_bone, "bone": pose_bone.bone}
            wanted = pose_bone.name in self.names
            for owner, attribute, value in self._FLAGS:
                holder = holders[owner]
                if not hasattr(holder, attribute):
                    continue
                try:
                    self._restore.append((holder, attribute,
                                          getattr(holder, attribute)))
                    setattr(holder, attribute,
                            wanted if value is None else value)
                except (AttributeError, TypeError, ValueError):
                    continue
        return self

    def __exit__(self, *_exc):
        for holder, attribute, value in reversed(self._restore):
            try:
                setattr(holder, attribute, value)
            except (AttributeError, TypeError, ValueError, ReferenceError):
                continue
        for collection, visible in self._collections:
            try:
                collection.is_visible = visible
            except (AttributeError, TypeError, ReferenceError):
                continue
        return False


@command("rigforge_retarget")
def cmd_rigforge_retarget(params):
    """Map a user-supplied mocap clip onto the rig's FK controls and bake it.

    The transfer is a constraint bake, not matrix arithmetic: a Copy Rotation in
    world space on every mapped control plus one Copy Location for the hips, then
    ``nla.bake(visual_keying=True)``.  That is more robust than solving the local
    rotations by hand because Blender's own evaluator does the rest-orientation
    algebra, and it is the same mechanism Phase 4's export bake already trusts.

    Every imported object is deleted in a ``finally``.  A retarget that fails
    leaves the file exactly as it found it.
    """
    started = time.monotonic()
    warnings = []
    scene = get_scene()

    target_rig = _rig_for(None, params, key="target_rig", required=True)
    path = resolve_path(get_str(params, "source_path"))
    if not os.path.isfile(path):
        raise ForgeError("No such file: %s (retargeting only reads files you supply; "
                         "nothing is downloaded)." % path)

    loop = None
    if params.get("loop") is not None:
        loop = get_bool(params, "loop", False)
    action_name = loop_name(get_str(params, "action_name"), loop)
    replace = get_bool(params, "replace", True)
    step = get_int(params, "frame_step", 1, minimum=1, maximum=10)
    fk_switch = get_bool(params, "fk_switch", True)

    raw_scale = params.get("scale", "auto")
    if isinstance(raw_scale, str):
        if raw_scale.strip().lower() != "auto":
            raise ForgeError("'scale' must be \"auto\" or a number.")
        scale_mode = "auto"
        scale = None
    elif raw_scale is None:
        scale_mode = "auto"
        scale = None
    else:
        scale_mode = "explicit"
        scale = get_float(params, "scale", minimum=1e-6, maximum=1e6)

    raw_mapping = params.get("mapping", "auto")
    if isinstance(raw_mapping, str) and raw_mapping.strip().lower() != "auto":
        raise ForgeError("'mapping' must be \"auto\" or a {source: target} object.")

    existing = bpy.data.actions.get(action_name)
    if existing is not None and not replace:
        raise ForgeError("An action named %r already exists; pass 'replace': true to "
                         "overwrite it, or pick another 'action_name'." % action_name)

    previous_frame = scene.frame_current
    previous_range = (scene.frame_start, scene.frame_end)
    previous_action = None
    if target_rig.animation_data is not None:
        previous_action = target_rig.animation_data.action
    previous_matrices = {bone.name: bone.matrix_basis.copy()
                         for bone in target_rig.pose.bones}

    imported = []
    collection = None
    known_actions = set(bpy.data.actions)
    baked = None
    mapped = []
    unmapped = []
    fk_switched = []
    frame_start = frame_end = 0
    kind = ""
    # captured while the import is still alive: the ``finally`` deletes it, and a
    # removed datablock's Python handle raises on ``.name``.
    source_name = ""
    mapping_mode = "auto"
    replaced = False
    made = 0
    try:
        with object_mode():
            collection = bpy.data.collections.new(TEMP_COLLECTION)
            scene.collection.children.link(collection)
            refresh_view_layer()

            imported, kind = _import_clip(path, warnings)
            for obj in imported:
                for existing_collection in list(obj.users_collection):
                    try:
                        existing_collection.objects.unlink(obj)
                    except RuntimeError:
                        pass
                try:
                    collection.objects.link(obj)
                except RuntimeError:
                    pass
            refresh_view_layer()

            armatures = [obj for obj in imported if obj.type == "ARMATURE"]
            if not armatures:
                raise ForgeError(
                    "%s contains no armature, so there is no skeleton to retarget "
                    "from (it imported %s)."
                    % (path, ", ".join(sorted({obj.type for obj in imported}))))
            source_rig = armatures[0]
            source_name = source_rig.name
            if len(armatures) > 1:
                warnings.append("The clip contained %d armatures; used %r."
                                % (len(armatures), source_rig.name))

            clip = _source_action(source_rig)
            if clip is None:
                raise ForgeError("The imported skeleton %r carries no animation."
                                 % source_rig.name)
            frame_start = int(math.floor(float(clip.frame_range[0])))
            frame_end = int(math.ceil(float(clip.frame_range[1])))
            if frame_end <= frame_start:
                frame_end = frame_start + 1

            # --- scale the source to the rig, then align their hips at rest
            source_height = _armature_height(source_rig)
            target_height = _armature_height(target_rig)
            if scale_mode == "auto":
                if source_height <= 1e-9:
                    scale = 1.0
                    warnings.append("The clip's skeleton has no height; scale 1.0 "
                                    "was used.")
                else:
                    scale = target_height / source_height
            source_rig.scale = (scale, scale, scale)
            refresh_view_layer()

            # --- mapping
            if isinstance(raw_mapping, dict):
                mapped, unmapped = explicit_mapping(raw_mapping, source_rig, target_rig)
                mapping_mode = "explicit"
            else:
                mapped, unmapped = auto_mapping(source_rig, target_rig)
                mapping_mode = "auto"
            if not mapped:
                raise ForgeError(
                    "Nothing in %s could be mapped onto %r. Source bones: %s. Pass an "
                    "explicit 'mapping' if the clip uses names this heuristic does not "
                    "know."
                    % (os.path.basename(path), target_rig.name,
                       ", ".join(sorted(b.name for b in source_rig.pose.bones))[:400]))

            root_pair = next((entry for entry in mapped
                              if entry.get("slot") == ROOT_SLOT), None)
            if root_pair is not None:
                source_bone = source_rig.pose.bones[root_pair["source"]]
                target_bone = target_rig.pose.bones[root_pair["target"]]
                source_rest = source_rig.matrix_world @ source_bone.bone.head_local
                target_rest = target_rig.matrix_world @ target_bone.bone.head_local
                source_rig.location = source_rig.location + (target_rest - source_rest)
                refresh_view_layer()

            if fk_switch:
                fk_switched = set_fk(target_rig, keyframe_at=None)
                if fk_switched:
                    warnings.append(
                        "Rigify's IK/FK blend was moved to full FK on %d limb(s) so the "
                        "retargeted FK rotations reach the deform bones; the switch is "
                        "keyframed into the baked action." % len(fk_switched))

            # --- constraints
            made = 0
            for entry in mapped:
                bone = target_rig.pose.bones[entry["target"]]
                constraint = bone.constraints.new("COPY_ROTATION")
                constraint.name = "Forge Retarget Rot"
                constraint.target = source_rig
                constraint.subtarget = entry["source"]
                constraint.target_space = "WORLD"
                constraint.owner_space = "WORLD"
                constraint.mix_mode = "REPLACE"
                made += 1
                if root_pair is not None and entry is root_pair:
                    location = bone.constraints.new("COPY_LOCATION")
                    location.name = "Forge Retarget Loc"
                    location.target = source_rig
                    location.subtarget = entry["source"]
                    location.target_space = "WORLD"
                    location.owner_space = "WORLD"
                    location.use_offset = False
                    made += 1

            scene.frame_start = frame_start
            scene.frame_end = frame_end
            if target_rig.animation_data is None:
                target_rig.animation_data_create()
            target_rig.animation_data.action = None

            targets = [entry["target"] for entry in mapped]
            with active_only(target_rig), _bones_bakeable(target_rig, targets):
                try:
                    bpy.ops.object.mode_set(mode="POSE")
                except RuntimeError as exc:
                    raise ForgeError("Could not enter Pose Mode on %r to bake: %s"
                                     % (target_rig.name, exc))
                try:
                    selected = {bone.name for bone
                                in (bpy.context.selected_pose_bones or ())}
                    if not selected & set(targets):
                        raise ForgeError(
                            "None of the %d mapped control bones could be selected for "
                            "the bake on %r, so the clip would have gone nowhere. The "
                            "rig's bone collections may be locked."
                            % (len(targets), target_rig.name))
                    if len(selected & set(targets)) < len(targets):
                        warnings.append(
                            "%d of %d mapped control bones could not be selected for "
                            "the bake and were skipped: %s."
                            % (len(targets) - len(selected & set(targets)),
                               len(targets),
                               ", ".join(sorted(set(targets) - selected))))
                    status = bpy.ops.nla.bake(**op_kwargs(bpy.ops.nla.bake, {
                        "frame_start": frame_start,
                        "frame_end": frame_end,
                        "step": step,
                        "only_selected": True,
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
            if "FINISHED" not in status:
                raise ForgeError("Baking the retarget returned %s."
                                 % (", ".join(sorted(status)) or "nothing"))
            baked = (target_rig.animation_data.action
                     if target_rig.animation_data else None)
            if baked is None:
                raise ForgeError("The bake produced no action.")

            # The IK/FK switch has to live *inside* the clip: the Godot export
            # bakes the control rig per action, and a switch that is only a live
            # property would be whatever the last command left it at.
            if fk_switched:
                for frame in (frame_start, frame_end):
                    set_fk(target_rig, keyframe_at=frame)

            replaced = False
            if baked.name != action_name:
                clash = bpy.data.actions.get(action_name)
                if clash is not None and clash is not baked:
                    clash.use_fake_user = False
                    bpy.data.actions.remove(clash)
                    replaced = True
                    warnings.append("An action named %r already existed and was "
                                    "replaced." % action_name)
                baked.name = action_name
            baked.use_fake_user = True
            baked_name = baked.name
    finally:
        # constraints come off the *real* rig whatever happened
        for entry in mapped:
            bone = target_rig.pose.bones.get(entry["target"])
            if bone is None:
                continue
            for constraint in list(bone.constraints):
                if constraint.name.startswith("Forge Retarget"):
                    try:
                        bone.constraints.remove(constraint)
                    except (RuntimeError, ReferenceError):
                        pass
        if collection is not None:
            _delete_objects(list(collection.objects))
            try:
                bpy.data.collections.remove(collection)
            except (ReferenceError, RuntimeError):
                pass
        # anything the importer left outside the temp collection
        _delete_objects(imported)
        # ... and the clip's own action, which outlives the skeleton it came in
        # with. Only actions this command caused to exist are touched.
        for action in [a for a in bpy.data.actions if a not in known_actions]:
            if action is baked:
                continue
            try:
                action.use_fake_user = False
                bpy.data.actions.remove(action)
            except (ReferenceError, RuntimeError, TypeError):
                continue
        scene.frame_start, scene.frame_end = previous_range
        try:
            scene.frame_set(previous_frame)
        except (RuntimeError, TypeError):
            pass
        if baked is None:
            try:
                if target_rig.animation_data is not None:
                    assign_action(target_rig, previous_action)
            except (AttributeError, TypeError, RuntimeError):
                pass
            for name, matrix in previous_matrices.items():
                bone = target_rig.pose.bones.get(name)
                if bone is not None:
                    try:
                        bone.matrix_basis = matrix
                    except (RuntimeError, ValueError):
                        pass
        refresh_view_layer()

    return {
        "action": baked_name,
        "loop": is_loop(baked_name),
        "target_rig": target_rig.name,
        "source_path": path,
        "source_format": kind,
        "source_armature": source_name,
        "mapping": mapping_mode,
        "mapped": mapped,
        "mapped_count": len(mapped),
        "unmapped": [entry["source"] for entry in unmapped],
        "unmapped_detail": unmapped,
        "unmapped_count": len(unmapped),
        "frames": max(0, frame_end - frame_start),
        "frame_range": [frame_start, frame_end],
        "frame_step": step,
        "scale": round(float(scale), 6),
        "scale_mode": scale_mode,
        "fk_switched": fk_switched,
        "replaced": replaced,
        "constraints": made,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# panel state
# ---------------------------------------------------------------------------

CLOTH_PRESET_ITEMS = (
    ("cotton", "Cotton", "Light shirt cloth: mass 0.30, tension 15, bending 0.5"),
    ("leather", "Leather", "Stiff and thick: mass 0.40, tension 80, bending 150"),
    ("heavy", "Heavy", "Denim-weight: mass 1.00, tension 40, bending 10"),
)

CLOTH_OUTPUT_ITEMS = (
    ("skin_tight", "Skin Tight", "No simulation: the garment wears the body's weights"),
    ("shapekeys", "Shape Keys", "Simulate, then bake the settled shape into a shape key"),
    ("bones", "Bones", "Not implemented in v1 - warns and falls back to Skin Tight"),
)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_rigforge_anim", None)


def _selected_tags(props):
    return [name.strip() for name in (props.cloth_tags or "").split(",") if name.strip()]


class ForgeAnimProps(PropertyGroup):
    """Scene-level Phase 5 panel state (cloth + action library)."""

    # --- cloth
    cloth_tags: StringProperty(
        name="Tags",
        description="Comma-separated tag names the garment covers (use the tag "
                    "buttons above to toggle them)",
        default="",
    )
    cloth_use_selection: BoolProperty(
        name="Use Selection",
        description="Grow the garment from the selected faces instead of whole tags",
        default=False,
    )
    cloth_name: StringProperty(
        name="Name",
        description="Name for the garment object (blank = <mesh>_Garment)",
        default="",
    )
    cloth_preset: EnumProperty(
        name="Preset",
        description="Cloth physics used when the output is Shape Keys",
        items=CLOTH_PRESET_ITEMS,
        default="cotton",
    )
    cloth_output: EnumProperty(
        name="Output",
        description="What ships: weights only, or a baked settled shape",
        items=CLOTH_OUTPUT_ITEMS,
        default="skin_tight",
    )
    cloth_offset_mm: FloatProperty(
        name="Offset",
        description="How far the garment is pushed off the body along its normals (mm)",
        default=DEFAULT_OFFSET_MM,
        min=0.0,
        max=1000.0,
    )
    cloth_thickness_mm: FloatProperty(
        name="Thickness",
        description="Solidify thickness of the garment shell (mm)",
        default=DEFAULT_THICKNESS_MM,
        min=0.0,
        max=1000.0,
    )
    cloth_frames: IntProperty(
        name="Frames",
        description="Simulation frames to settle the cloth before baking",
        default=DEFAULT_FRAMES,
        min=1,
        max=2000,
    )
    cloth_self_collision: BoolProperty(
        name="Self Collision",
        description="Let the garment collide with itself. Off by default: a "
                    "duplicated sculpt patch is often already self-intersecting",
        default=False,
    )

    # --- actions
    action_name: StringProperty(
        name="Action",
        description="Name for a new, duplicated or renamed action",
        default="",
    )
    action_loop: BoolProperty(
        name="Loop",
        description="Enforce Godot's -loop suffix on the name",
        default=False,
    )
    action_source: StringProperty(
        name="Source",
        description="Existing action a Duplicate branches from (blank = the rig's current)",
        default="",
    )

    # --- retarget
    retarget_path: StringProperty(
        name="Clip",
        description="A .bvh or .fbx motion clip you supply. Nothing is downloaded",
        default="",
        subtype="FILE_PATH",
    )
    retarget_name: StringProperty(
        name="Action",
        description="Name for the baked action",
        default="",
    )
    retarget_loop: BoolProperty(
        name="Loop",
        description="Enforce Godot's -loop suffix on the baked action",
        default=False,
    )

    status: StringProperty(name="Status", default="")
    status_is_error: BoolProperty(default=False)
    summary: StringProperty(name="Summary", default="")


def set_status(props, message, error=False):
    if props is None:
        return
    props.status = str(message).strip().splitlines()[0][:400] if message else ""
    props.status_is_error = bool(error)
    if error and message:
        print("[Forge/RigForge]", message)


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class _AnimOperator(Operator):
    """Guarded like the Phase 3/4 operators, but not tied to a mesh being active.

    The Actions box is used with the *rig* selected as often as the mesh, so
    ``poll`` cannot demand a mesh the way ``_RigForgeOperator`` does.
    """

    bl_options = {"REGISTER", "UNDO"}

    def fail(self, props, message):
        set_status(props, message, error=True)
        self.report({"ERROR"}, str(message))
        return {"CANCELLED"}

    def guarded(self, context, work):
        props = get_props(context)
        try:
            return work(props)
        except ForgeError as exc:
            return self.fail(props, str(exc))
        except Exception as exc:  # noqa: BLE001 - the panel must never traceback
            import traceback as _traceback

            _traceback.print_exc()
            return self.fail(props, "%s: %s" % (type(exc).__name__, exc))


class FORGE_OT_rf_cloth_tag(_AnimOperator):
    bl_idname = "forge.rf_cloth_tag"
    bl_label = "Toggle Cloth Tag"
    bl_description = "Add or remove this tag from the garment's coverage"

    tag: StringProperty(name="Tag", default="")

    def execute(self, context):
        def work(props):
            if props is None:
                return {"CANCELLED"}
            name = (self.tag or "").strip()
            if not name:
                return self.fail(props, "No tag named.")
            selected = _selected_tags(props)
            if name in selected:
                selected.remove(name)
            else:
                selected.append(name)
            props.cloth_tags = ", ".join(selected)
            set_status(props, "Garment covers: %s" % (props.cloth_tags or "nothing yet"))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_cloth(_AnimOperator):
    bl_idname = "forge.rf_cloth"
    bl_label = "Make Garment"
    bl_description = ("Duplicate the covered faces into a garment, offset and thicken "
                      "it, then weight it to the rig (and optionally settle it with a "
                      "cloth sim baked into a shape key)")

    def execute(self, context):
        def work(props):
            obj = rigforge.active_mesh(context)
            if obj is None:
                return self.fail(props, "Select the body mesh first.")
            payload = {
                "object": obj.name,
                "preset": props.cloth_preset,
                "output": props.cloth_output,
                "offset_mm": float(props.cloth_offset_mm),
                "thickness_mm": float(props.cloth_thickness_mm),
                "frames": int(props.cloth_frames),
                "self_collision": bool(props.cloth_self_collision),
            }
            if props.cloth_name.strip():
                payload["name"] = props.cloth_name.strip()
            if props.cloth_use_selection:
                payload["use_selection"] = True
            else:
                tags = _selected_tags(props)
                if not tags:
                    return self.fail(props, "Pick at least one tag for the garment "
                                            "(or switch on Use Selection).")
                payload["tags"] = tags
            set_status(props, "Making a garment ...")
            result = cmd_rigforge_cloth(payload)
            warnings = result.get("warnings") or []
            props.summary = "%s   %d faces   %s" % (
                result["garment"], result["face_count"],
                ", ".join(result["shape_keys"]) or result["output"])
            set_status(
                props,
                "Garment %s (%s, %d faces)%s" % (
                    result["garment"], result["output"], result["face_count"],
                    "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_action(_AnimOperator):
    bl_idname = "forge.rf_action"
    bl_label = "Action"
    bl_description = "Create, duplicate, delete or push an action to the NLA"

    action: EnumProperty(
        name="Action",
        items=(("new", "New", "Create an empty action and assign it to the rig"),
               ("duplicate", "Duplicate", "Branch a copy of an existing action"),
               ("delete", "Delete", "Remove the named action from the file"),
               ("push_nla", "Push NLA", "Push the action onto its own NLA track")),
        default="new",
    )
    name: StringProperty(name="Name", default="")

    def execute(self, context):
        def work(props):
            if props is None:
                return {"CANCELLED"}
            name = (self.name or props.action_name or "").strip()
            payload = {"action": self.action, "loop": bool(props.action_loop)}
            rig = _rig_for(rigforge.active_mesh(context), {})
            if rig is not None:
                payload["rig"] = rig.name
            if self.action == "duplicate":
                source = (props.action_source or name).strip()
                if not source:
                    return self.fail(props, "Name the action to duplicate in Source.")
                payload["source"] = source
                if props.action_name.strip():
                    payload["name"] = props.action_name.strip()
            else:
                if not name:
                    return self.fail(props, "Type an action name first.")
                payload["name"] = name
            result = cmd_rigforge_action(payload)
            props.summary = "%d action(s) in the library" % result["count"]
            set_status(props, "%s %s" % (self.action.replace("_", " ").title(),
                                         result.get("name", "")))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_action_select(_AnimOperator):
    bl_idname = "forge.rf_action_select"
    bl_label = "Edit Action"
    bl_description = "Assign this action to the rig so it is the one you are editing"

    name: StringProperty(name="Name", default="")

    def execute(self, context):
        def work(props):
            rig = _rig_for(rigforge.active_mesh(context), {}, required=True)
            action = _require_action((self.name or "").strip())
            with object_mode():
                assign_action(rig, action)
            set_status(props, "Editing %s on %s" % (action.name, rig.name))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_retarget(_AnimOperator):
    bl_idname = "forge.rf_retarget"
    bl_label = "Retarget"
    bl_description = ("Map a .bvh/.fbx clip you supply onto this rig's FK controls "
                      "and bake it to an action")

    def execute(self, context):
        def work(props):
            if not (props.retarget_path or "").strip():
                return self.fail(props, "Point Clip at a .bvh or .fbx file first.")
            name = (props.retarget_name or "").strip()
            if not name:
                name = os.path.splitext(os.path.basename(
                    bpy.path.abspath(props.retarget_path)))[0] or "retargeted"
            rig = _rig_for(rigforge.active_mesh(context), {}, required=True)
            set_status(props, "Retargeting ...")
            result = cmd_rigforge_retarget({
                "target_rig": rig.name,
                "source_path": props.retarget_path,
                "action_name": name,
                "loop": bool(props.retarget_loop),
            })
            warnings = result.get("warnings") or []
            props.summary = "%s   %d mapped / %d unmapped   %d frames" % (
                result["action"], result["mapped_count"], result["unmapped_count"],
                result["frames"])
            set_status(
                props,
                "Retargeted %s (%d bone(s), %d frame(s))%s" % (
                    result["action"], result["mapped_count"], result["frames"],
                    "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings))
            return {"FINISHED"}

        return self.guarded(context, work)


_CLASSES = (
    ForgeAnimProps,
    FORGE_OT_rf_cloth_tag,
    FORGE_OT_rf_cloth,
    FORGE_OT_rf_action,
    FORGE_OT_rf_action_select,
    FORGE_OT_rf_retarget,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_rigforge_anim = bpy.props.PointerProperty(type=ForgeAnimProps)


def unregister():
    try:
        del bpy.types.Scene.forge_rigforge_anim
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
