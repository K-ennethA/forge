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

#: Foot-roll shape, as fractions of the cycle.
ROLL_FLAT_AT = 0.12           #: heel strike is over and the foot is flat
ROLL_LIFT_FOR = 0.18          #: how long the heel-off roll takes, before toe-off

DEFAULT_CYCLE_FRAMES = 32
DEFAULT_ARM_SWING_DEG = 26.0
DEFAULT_ELBOW_BEND_DEG = 14.0
DEFAULT_FOOT_ROLL_DEG = 22.0
DEFAULT_HIP_TWIST_DEG = 6.0

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


def _reach_limit(frame_info, stance_fraction, hip_low, margin):
    """The longest step these legs can take without Rigify stretching them.

    A target the leg cannot reach is worse than a short stride: the foot never
    arrives where it was keyed, so it slides on the deform bones while the
    control sits perfectly still, and the metric blames the animation for a
    reach problem.  Solved in the triangle: the hip is ``drop`` above the ankle
    at its lowest, the leg may span ``margin * leg_length``, so the horizontal
    offset is bounded by the remaining side.  ``None`` when no leg has a hip to
    measure from.
    """
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
                / max(stance_fraction, 1e-6)
        longest = limit if longest is None else min(longest, limit)
    return longest


def _crouch_for(frame_info, step_length, stance_fraction, hip_drop, hip_lower,
                margin, max_lower):
    """Bend the knees as much as the asked-for stride needs, then clamp.

    A rig at rest stands with its legs all but straight, so *every* stride
    longer than a shuffle needs the hips lowered — a real walker's do the same
    thing, which is why the knee is never locked through stance.  So the order
    of preference is: deepen the crouch (up to ``max_lower``), and only then
    shorten the step.  Returns ``(step_length, hip_lower, clamped, deepened)``.
    """
    limit = _reach_limit(frame_info, stance_fraction, hip_lower + hip_drop, margin)
    if limit is None or step_length <= limit:
        return step_length, hip_lower, False, False

    deepened = False
    needed = step_length * stance_fraction
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

    limit = _reach_limit(frame_info, stance_fraction, hip_lower + hip_drop, margin)
    if limit is None:
        return step_length, hip_lower, False, deepened
    # A hair under, after the crouch was solved for this very step, is the
    # solution landing on its own boundary - not a clamp worth a warning.
    if step_length > limit * (1.0 + 1e-6):
        return limit, hip_lower, True, deepened
    return min(step_length, limit), hip_lower, False, deepened


def _foot_offset(u, stance_fraction, step_length, step_height, cycle_offset):
    """One foot's ground-plane offset and lift at cycle phase ``u``.

    Returns ``(along_forward, lift)`` relative to the foot's rest position,
    before the body's own travel is added.  Stance is a **constant**, which is
    the entire point: between contact and toe-off this function returns the
    same number every frame, so the key it produces is the same key, so the
    foot cannot drift.
    """
    stride = 2.0 * step_length
    plant = cycle_offset + step_length * stance_fraction
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
    "hip_twist_deg"?, "arm_swing_deg"?, "elbow_bend_deg"?, "foot_roll_deg"?,
    "travel"?, "loop"?, "clear"?, "interpolation"?, "stride_width"?,
    "reach_margin"?}``

    Every length parameter is metres and every one of them defaults to a
    fraction of *this* rig's leg, so the command works on a figurine and an
    ogre without being told which it is.
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
    elbow_bend = math.radians(get_float(params, "elbow_bend_deg", DEFAULT_ELBOW_BEND_DEG,
                                        minimum=0.0, maximum=120.0))
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
    step_length, hip_lower, clamped, deepened = _crouch_for(
        info, step_length, stance_fraction, hip_drop, hip_lower, reach_margin,
        max_lower)
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
                             "fore_rest": _rest_world(rig, fore) if fore else None})
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

        # Phase offset: the left foot contacts at the top of the cycle, the
        # right half a cycle later. That half-cycle IS the gait.
        offsets = {}
        for index, name in enumerate(sorted(info["feet"])):
            offsets[name] = 0.0 if info["feet"][name]["side"] == "L" else 0.5

        plants = {name: [] for name in info["feet"]}
        for frame in frames:
            t = float(frame - frames[0]) / float(cycle_frames)
            scene.frame_set(frame)

            body = stride * t
            if root is not None:
                matrix = rest_root.copy()
                if travel:
                    matrix.translation = rest_root.translation + forward * body
                _set_world(rig, root, matrix)
                keys_set += _key_transform(root, frame)
                touched(root.name)
                refresh_view_layer()

            for name in sorted(info["feet"]):
                foot = info["feet"][name]
                target = rig.pose.bones.get(foot["target"])
                if target is None:
                    continue
                u = (t - offsets[name]) % 1.0
                cycle_offset = stride * math.floor((t - offsets[name]) + 1e-9)
                along, lift = _foot_offset(u, stance_fraction, step_length, step_height,
                                           cycle_offset)
                if not travel:
                    along -= body
                lateral = stride_width * (1.0 if foot["side"] == "L" else -1.0)
                matrix = foot["rest"].copy()
                matrix.translation = (foot["rest"].translation + forward * along
                                      + right * lateral + up * lift)
                _set_world(rig, target, matrix)
                keys_set += _key_transform(target, frame)
                touched(target.name)
                if u <= stance_fraction:
                    plants[name].append(frame)
                heel = heels.get(name)
                if heel is not None:
                    heel.rotation_euler = (math.radians(
                        _foot_roll(u, stance_fraction, roll_deg)), 0.0, 0.0)
                    heel.keyframe_insert("rotation_euler", frame=frame)
                    keys_set += 3
                    touched(heel.name)

            if torso is not None:
                bob = -hip_lower - hip_drop * math.cos(4.0 * math.pi * t)
                sway = hip_sway * math.sin(2.0 * math.pi * t)
                matrix = rest_torso.copy()
                matrix.translation = rest_torso.translation + up * bob + right * sway
                if hip_twist:
                    matrix = _rotate_about(matrix, up,
                                           hip_twist * math.sin(2.0 * math.pi * t),
                                           rest_torso.translation)
                _set_world(rig, torso, matrix)
                keys_set += _key_transform(torso, frame)
                touched(torso.name)

            for arm in arms:
                # Opposite the leg of the same side: the left arm goes back as
                # the left leg comes forward.
                phase = 0.0 if arm["side"] == "L" else 0.5
                angle = arm_swing * math.sin(2.0 * math.pi * (t - phase) + math.pi)
                sign = 1.0 if arm["side"] == "L" else -1.0
                upper = arm["upper"]
                matrix = _rotate_about(arm["upper_rest"], right, angle * sign,
                                       arm["upper_rest"].translation)
                _set_world(rig, upper, matrix)
                keys_set += _key_transform(upper, frame)
                touched(upper.name)
                if arm["fore"] is not None:
                    refresh_view_layer()
                    bend = elbow_bend * (0.5 + 0.5 * math.sin(
                        2.0 * math.pi * (t - phase) + math.pi))
                    fore_rest = arm["fore_rest"]
                    bent = _rotate_about(fore_rest, right, bend * sign,
                                         fore_rest.translation)
                    carried = _rotate_about(bent, right, angle * sign,
                                            arm["upper_rest"].translation)
                    _set_world(rig, arm["fore"], carried)
                    keys_set += _key_transform(arm["fore"], frame)
                    touched(arm["fore"].name)

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

    if modes:
        warnings.append(
            "Rotation mode changed to XYZ euler on %s so the foot roll is one readable "
            "channel." % ", ".join(sorted(modes)))

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
        "leg_length_m": round(leg_length, 5),
        "step_length_reach_clamped": clamped,
        "hip_lower_deepened": deepened,
        "forward_axis": [round(v, 4) for v in forward],
        "convention": convention["convention"],
        "ik_limbs": [entry["name"] for entry in convention["limbs"]],
        "poles": convention["poles"],
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
            "cycle planted. %s"
            % (action.name, cycle_frames, stride * 1000.0,
               " and ".join(step["target"] for step in steps),
               stance_fraction * 100.0,
               "The root carries the travel (export with root_motion)." if travel
               else "In place: the feet run backwards at one shared speed.")),
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
