"""RigForge: semantic tagging, the character manifest, retopology and auto-UV.

Phase 3 of the pipeline (``docs/plan.md`` section 4, stages 1-3).  The whole
thing hangs off one idea: **tags**.  A tag is a vertex group named
``tag_<Name>`` (``tag_Head``, ``tag_Arm.L``) on the sculpt.  Every later stage —
retopology, UV seams, metarig placement, weight cleanup, clothing — reads the
tags, so getting them onto the mesh is the only manual step that matters.

The manifest (``templates/character.json``) is the durable half of the same
information: the tag list, the archetype and the plain-language motion notes.
Vertex groups are the truth for *where* a tag is; the manifest is the truth for
*what a character is*, and it survives outside the .blend.

Layout of this module:

* tag helpers + the four tag/manifest commands (cheap, no operators);
* :func:`cmd_rigforge_retopo` — the stage 2 pipeline (voxel remesh, Quadriflow,
  shrinkwrap, tag transfer, optional normal bake, optional LODs);
* :func:`cmd_rigforge_auto_uv` — stage 3 (seams from tag boundaries, unwrap,
  pack);
* the scene PropertyGroup and the operators the RigForge panel drives.  Those
  operators call the same functions the socket commands do, so the panel and
  Claude cannot drift apart.

Everything here runs on Blender's main thread and works under
``blender --background`` (no window, no 3D area, no event loop).
"""

import json
import math
import os
import time
from array import array

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
from mathutils.kdtree import KDTree

from . import common
from .common import (
    active_only,
    apply_modifier,
    get_scene,
    get_view_layer,
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_str,
    mesh_stats,
    object_mode,
    op_kwargs,
    refresh_view_layer,
    resolve_object,
    resolve_path,
    selection,
)
from .registry import ForgeError, command
# The UV-aware simplifier. Optional by construction: when the DLL has not been
# built, every call here still imports and the LOD stage falls back to Decimate
# with the reason in the report. Nothing in meshopt.py touches bpy.
from . import meshopt

#: Vertex groups with this prefix are semantic tags. Everything else on the
#: object (deform weights, masks) is left strictly alone.
TAG_PREFIX = "tag_"

ARCHETYPES = ("biped", "quadruped", "custom")

#: Target face counts per platform (plan section 4, stage 2).
PLATFORM_TARGETS = {"desktop": 15000, "mobile": 5000}

#: Per-platform **game budgets** for the finished asset: the face count a
#: shipping character of this class is allowed to cost on screen. These are not
#: the retopo targets above (which are what Quadriflow is aimed at); they are
#: what LOD0 is measured against in the result report, and what the default LOD
#: chain is derived from. Overridable per call with ``lod_budgets``.
LOD_BUDGETS = {"desktop": 50000, "mobile": 10000}

#: Each LOD level targets this fraction of the level above it. A quarter per
#: level is the usual game step: it is a visible halving of the silhouette
#: sample rate in each direction, so the switch distance roughly doubles.
LOD_STEP = 0.25

#: Below this a LOD is not worth generating - the draw call costs more than the
#: triangles it saves.
LOD_MIN_FACES = 64

#: Screen-error model for the visibility ranges written into the Godot import
#: script. A LOD whose measured geometric error is ``e`` world units is
#: acceptable from distance ``d`` when ``e / d * PIXELS_PER_RADIAN <= error_px``.
#: 1080p at a 75 deg vertical FOV: 1080 / (2 * tan(37.5 deg)) ~= 704 px/radian.
LOD_PIXELS_PER_RADIAN = 703.7
LOD_ERROR_PIXELS = 1.0

DEFAULT_RETOPO = {"target_faces_desktop": 15000, "target_faces_mobile": 5000, "lods": 2}
DEFAULT_ACTIONS = ["idle-loop", "walk-loop", "run-loop", "jump", "attack"]
DEFAULT_GODOT = {"targets": ["desktop", "mobile"], "root_motion": False, "y_up": True,
                 "unit_scale": 1.0}

#: Key order of templates/character.json, so a saved manifest is diffable.
MANIFEST_KEY_ORDER = (
    "name", "archetype", "custom_modules", "tags", "motion_notes",
    "retopo", "actions", "godot",
)

#: Object custom properties. The manifest is the portable mirror of these.
PROP_ARCHETYPE = "forge_archetype"
PROP_MOTION_NOTES = "forge_motion_notes"
PROP_MANIFEST_PATH = "forge_manifest_path"
PROP_CHARACTER_NAME = "forge_character_name"
PROP_MANIFEST_EXTRA = "forge_manifest_extra"
PROP_RETOPO = "forge_retopo"
PROP_RETOPO_SOURCE = "forge_retopo_source"
#: Name of the UV layer a *real* unwrap wrote. Its absence is what tells the
#: normal bake that the only UV layer on the mesh is the throwaway one
#: ``_ensure_uv_layer`` makes, which the next unwrap will overwrite - and a bake
#: into a layer that is about to be replaced is a bake that silently never
#: happened. See :func:`bake_normals`.
PROP_UV_UNWRAPPED = "forge_uv_unwrapped"
#: Per-LOD record written onto each generated level: the budget it was cut to,
#: the face count it achieved and the geometric error that came out of it. The
#: Godot exporter reads it to write ``visibility_range_*`` on the manual chain,
#: so the numbers the switch distances rest on are measured, not guessed.
PROP_LOD = "forge_lod"


# ---------------------------------------------------------------------------
# tag naming
# ---------------------------------------------------------------------------

def tag_group_name(tag):
    """``"Head"`` or ``"tag_Head"`` -> ``"tag_Head"`` (validated)."""
    if not isinstance(tag, str):
        raise ForgeError("A tag name must be a string, got %s." % type(tag).__name__)
    name = tag.strip()
    if not name:
        raise ForgeError("A tag name must not be empty.")
    if name.startswith(TAG_PREFIX):
        name = name[len(TAG_PREFIX):].strip()
    if not name:
        raise ForgeError("A tag name must be more than just the %r prefix." % TAG_PREFIX)
    if len(name) > 60:
        raise ForgeError("Tag name %r is too long (max 60 characters)." % name)
    for bad in "\n\r\t":
        if bad in name:
            raise ForgeError("Tag name %r contains a control character." % name)
    return TAG_PREFIX + name


def tag_display_name(group_name):
    """``"tag_Head"`` -> ``"Head"``."""
    text = str(group_name)
    return text[len(TAG_PREFIX):] if text.startswith(TAG_PREFIX) else text


def is_tag_group(group):
    return str(getattr(group, "name", "")).startswith(TAG_PREFIX)


def tag_groups(obj):
    """Every ``tag_*`` vertex group on ``obj``, in the object's own order."""
    return [g for g in obj.vertex_groups if is_tag_group(g)]


def find_tag_group(obj, tag, required=True):
    name = tag_group_name(tag)
    group = obj.vertex_groups.get(name)
    if group is None and required:
        known = ", ".join(tag_display_name(g.name) for g in tag_groups(obj)) or "none"
        raise ForgeError(
            "Object %r has no tag %r (vertex group %r). Tags on this object: %s."
            % (obj.name, tag_display_name(name), name, known)
        )
    return group


def _require_mesh(params, key="object"):
    obj = resolve_object(params, key=key, mesh_only=True)
    if obj.data is None:
        raise ForgeError("Object %r has no mesh data." % obj.name)
    return obj


# ---------------------------------------------------------------------------
# membership helpers
# ---------------------------------------------------------------------------

def _group_vertices(obj, group_index):
    """Indices of the vertices assigned to ``group_index`` with weight > 0."""
    out = []
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.group == group_index and entry.weight > 0.0:
                out.append(vertex.index)
                break
    return out


def _vertex_tag_map(obj):
    """``vertex index -> set(tag group indices)`` for every ``tag_*`` group."""
    wanted = {g.index for g in tag_groups(obj)}
    mapping = {}
    if not wanted:
        return mapping
    for vertex in obj.data.vertices:
        marks = None
        for entry in vertex.groups:
            if entry.group in wanted and entry.weight > 0.0:
                if marks is None:
                    marks = set()
                marks.add(entry.group)
        if marks:
            mapping[vertex.index] = marks
    return mapping


def _face_tag_map(obj, vertex_map=None):
    """``polygon index -> frozenset(tag group indices)``.

    A face carries a tag when *every* one of its vertices does, which is the
    same rule the tagging commands write with, so tagging faces and reading
    faces back round-trips exactly.
    """
    vertex_map = _vertex_tag_map(obj) if vertex_map is None else vertex_map
    faces = {}
    for polygon in obj.data.polygons:
        marks = None
        for vertex_index in polygon.vertices:
            owned = vertex_map.get(vertex_index)
            if not owned:
                marks = None
                break
            marks = set(owned) if marks is None else (marks & owned)
            if not marks:
                break
        faces[polygon.index] = frozenset(marks or ())
    return faces


def _tag_face_count(obj, group_index, face_map=None):
    face_map = _face_tag_map(obj) if face_map is None else face_map
    return sum(1 for marks in face_map.values() if group_index in marks)


def _validate_face_indices(obj, faces):
    if not isinstance(faces, (list, tuple)):
        raise ForgeError("'faces' must be a list of polygon indices.")
    count = len(obj.data.polygons)
    clean = []
    for position, raw in enumerate(faces):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForgeError("faces[%d] must be an integer polygon index, got %r."
                             % (position, raw))
        if isinstance(raw, float) and not float(raw).is_integer():
            raise ForgeError("faces[%d] must be a whole number, got %r." % (position, raw))
        index = int(raw)
        if index < 0 or index >= count:
            raise ForgeError(
                "faces[%d] is %d but %r has %d polygon(s) (0..%d)."
                % (position, index, obj.name, count, count - 1)
            )
        clean.append(index)
    return sorted(set(clean))


def selected_faces(obj):
    """Selected polygon indices, whichever mode the sculptor is actually in.

    Face-select mode keeps its selection in the edit-mode bmesh, and the base
    mesh's ``polygon.select`` flags only catch up when Blender leaves Edit
    Mode — so read the live bmesh when there is one.
    """
    mesh = obj.data
    if obj.mode == "EDIT":
        try:
            bm = bmesh.from_edit_mesh(mesh)
        except (ValueError, TypeError):
            bm = None
        if bm is not None:
            bm.faces.ensure_lookup_table()
            return [face.index for face in bm.faces if face.select]
    return [polygon.index for polygon in mesh.polygons if polygon.select]


def _faces_from_params(obj, params, allow_empty=False):
    """The face list a tag command should act on: explicit, or the selection."""
    raw = params.get("faces")
    use_selection = params.get("use_selection")
    if raw is not None and use_selection:
        raise ForgeError("Pass either 'faces' or 'use_selection', not both.")
    if raw is not None:
        faces = _validate_face_indices(obj, raw)
        source = "faces"
    elif use_selection:
        if not get_bool(params, "use_selection", False):
            raise ForgeError("'use_selection' was false; pass 'faces' instead.")
        faces = selected_faces(obj)
        source = "selection"
    else:
        if allow_empty:
            return None, "none"
        raise ForgeError(
            "Nothing to tag: pass 'faces' (a list of polygon indices) or "
            "'use_selection': true."
        )
    if not faces:
        raise ForgeError(
            "No faces to work on (%s)." % (
                "the selection is empty - select faces in the viewport first"
                if source == "selection" else "'faces' was an empty list"
            )
        )
    return faces, source


def _vertices_of_faces(obj, faces):
    verts = set()
    polygons = obj.data.polygons
    for index in faces:
        verts.update(polygons[index].vertices)
    return verts


# ---------------------------------------------------------------------------
# tag commands
# ---------------------------------------------------------------------------

@command("rigforge_list_tags")
def cmd_rigforge_list_tags(params):
    """Every semantic tag on the object, with vertex and face counts.

    ``name`` is the bare tag ("Head"); ``vertex_group`` is the Blender vertex
    group it lives in ("tag_Head").  The manifest uses the bare form.
    """
    obj = _require_mesh(params)
    face_map = _face_tag_map(obj)
    tags = []
    for group in tag_groups(obj):
        vertices = _group_vertices(obj, group.index)
        tags.append({
            "name": tag_display_name(group.name),
            "vertex_group": group.name,
            "vertex_count": len(vertices),
            "face_count": _tag_face_count(obj, group.index, face_map),
        })
    tags.sort(key=lambda entry: entry["name"].lower())
    return {
        "object": obj.name,
        "tags": tags,
        "count": len(tags),
        "total_vertices": len(obj.data.vertices),
        "total_faces": len(obj.data.polygons),
        "untagged_faces": sum(1 for marks in face_map.values() if not marks),
    }


@command("rigforge_tag")
def cmd_rigforge_tag(params):
    """Assign the vertices of the given faces to ``tag_<name>`` at weight 1.0."""
    obj = _require_mesh(params)
    group_name = tag_group_name(get_str(params, "tag"))
    replace = get_bool(params, "replace", False)

    # Read the selection BEFORE the mode switch: leaving Edit Mode is what
    # flushes the bmesh into the base mesh, and the indices survive it.
    faces, source = _faces_from_params(obj, params)

    with object_mode():
        group = obj.vertex_groups.get(group_name)
        created = group is None
        if group is None:
            group = obj.vertex_groups.new(name=group_name)
        if replace:
            existing = _group_vertices(obj, group.index)
            if existing:
                group.remove(existing)
        vertices = sorted(_vertices_of_faces(obj, faces))
        if vertices:
            group.add(vertices, 1.0, "REPLACE")
        obj.data.update()

    assigned = _group_vertices(obj, group.index)
    return {
        "object": obj.name,
        "tag": tag_display_name(group.name),
        "vertex_group": group.name,
        "vertex_count": len(assigned),
        "face_count": _tag_face_count(obj, group.index),
        "faces_used": len(faces),
        "created": created,
        "replaced": replace,
        "source": source,
    }


@command("rigforge_untag")
def cmd_rigforge_untag(params):
    """Remove faces from a tag, or (with no faces given) remove the tag itself.

    Removing a subset only drops vertices that no *remaining* face of the tag
    still needs, so a shared border between two tagged patches survives and the
    neighbouring faces keep their tag.  Pass ``include_shared: true`` for the
    blunter Blender-style behaviour (remove every vertex of those faces, which
    erodes the tag by one ring of faces around the hole).
    """
    obj = _require_mesh(params)
    group = find_tag_group(obj, get_str(params, "tag"))
    group_name = group.name
    include_shared = get_bool(params, "include_shared", False)
    faces, source = _faces_from_params(obj, params, allow_empty=True)

    if faces is None:
        with object_mode():
            obj.vertex_groups.remove(group)
            obj.data.update()
        return {
            "object": obj.name,
            "tag": tag_display_name(group_name),
            "vertex_group": group_name,
            "removed_group": True,
            "vertex_count": 0,
            "face_count": 0,
            "source": source,
        }

    with object_mode():
        face_map = _face_tag_map(obj)
        dropping = set(faces)
        doomed = _vertices_of_faces(obj, faces)
        keep = set()
        if not include_shared:
            for index, marks in face_map.items():
                if group.index in marks and index not in dropping:
                    keep.update(obj.data.polygons[index].vertices)
        removable = sorted(doomed - keep)
        if removable:
            group.remove(removable)
        obj.data.update()

    remaining = _group_vertices(obj, group.index)
    return {
        "object": obj.name,
        "tag": tag_display_name(group_name),
        "vertex_group": group_name,
        "removed_group": False,
        "removed_vertices": len(removable),
        "vertex_count": len(remaining),
        "face_count": _tag_face_count(obj, group.index),
        "faces_used": len(faces),
        "include_shared": include_shared,
        "source": source,
    }


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def _prop(obj, key, default=""):
    try:
        value = obj.get(key)
    except (AttributeError, TypeError):
        return default
    if value is None:
        return default
    return value


def _set_prop(obj, key, value):
    if value is None:
        try:
            del obj[key]
        except (KeyError, TypeError):
            pass
        return
    obj[key] = value


def _stored_json(obj, key, fallback):
    raw = _prop(obj, key, "")
    if not raw:
        return dict(fallback)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return dict(fallback)
    return parsed if isinstance(parsed, dict) else dict(fallback)


def _retopo_block(obj):
    block = dict(DEFAULT_RETOPO)
    block.update(_stored_json(obj, PROP_RETOPO, {}))
    return block


def build_manifest(obj):
    """The character.json document for ``obj``, built from live state.

    Tags come from the vertex groups (the single source of truth for *where* a
    tag is), everything else from the object's custom properties plus whatever
    unknown keys a previously loaded manifest carried, so a load/save round trip
    does not quietly drop fields this phase does not understand yet.
    """
    extra = _stored_json(obj, PROP_MANIFEST_EXTRA, {})
    doc = {
        "name": str(_prop(obj, PROP_CHARACTER_NAME, "") or obj.name),
        "archetype": str(_prop(obj, PROP_ARCHETYPE, "") or "custom"),
        "custom_modules": list(extra.get("custom_modules") or []),
        "tags": sorted((tag_display_name(g.name) for g in tag_groups(obj)),
                       key=lambda text: text.lower()),
        "motion_notes": str(_prop(obj, PROP_MOTION_NOTES, "") or ""),
        "retopo": _retopo_block(obj),
        "actions": list(extra.get("actions") or DEFAULT_ACTIONS),
        "godot": dict(extra.get("godot") or DEFAULT_GODOT),
    }
    for key, value in extra.items():
        if key not in doc and key not in ("custom_modules", "actions", "godot"):
            doc[key] = value
    ordered = {key: doc[key] for key in MANIFEST_KEY_ORDER if key in doc}
    for key in doc:
        if key not in ordered:
            ordered[key] = doc[key]
    return ordered


def apply_manifest(obj, doc, create_missing_tags=True):
    """Write a manifest document onto ``obj``. Returns the tags it had to create."""
    if not isinstance(doc, dict):
        raise ForgeError("A manifest must be a JSON object, got %s." % type(doc).__name__)

    name = doc.get("name")
    if isinstance(name, str) and name.strip():
        _set_prop(obj, PROP_CHARACTER_NAME, name.strip())
    archetype = doc.get("archetype")
    if isinstance(archetype, str) and archetype.strip():
        _set_prop(obj, PROP_ARCHETYPE, _normalise_archetype(archetype))
    notes = doc.get("motion_notes")
    if isinstance(notes, str):
        _set_prop(obj, PROP_MOTION_NOTES, notes)
    retopo = doc.get("retopo")
    if isinstance(retopo, dict):
        block = dict(DEFAULT_RETOPO)
        for key in ("target_faces_desktop", "target_faces_mobile", "lods"):
            value = retopo.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                block[key] = int(value)
        _set_prop(obj, PROP_RETOPO, json.dumps(block))

    extra = {key: value for key, value in doc.items()
             if key not in ("name", "archetype", "tags", "motion_notes", "retopo")}
    _set_prop(obj, PROP_MANIFEST_EXTRA, json.dumps(extra))

    created = []
    if create_missing_tags:
        for raw in doc.get("tags") or []:
            if not isinstance(raw, str) or not raw.strip():
                continue
            group_name = tag_group_name(raw)
            if obj.vertex_groups.get(group_name) is None:
                obj.vertex_groups.new(name=group_name)
                created.append(tag_display_name(group_name))
    return created


def _normalise_archetype(value):
    text = str(value or "").strip().lower()
    # The template ships the literal "biped | quadruped | custom" as a hint.
    if "|" in text:
        return "custom"
    if text in ARCHETYPES:
        return text
    return text or "custom"


def manifest_path(obj, params=None, required=True):
    raw = ""
    if params is not None:
        candidate = params.get("path")
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.strip()
    if not raw:
        raw = str(_prop(obj, PROP_MANIFEST_PATH, "") or "").strip()
    if not raw:
        if not required:
            return ""
        raise ForgeError(
            "No manifest path: pass 'path', or set one on the object first "
            "(the RigForge panel's Manifest field stores it on the object)."
        )
    return resolve_path(raw, make_parents=True, default_ext=".json")


@command("rigforge_manifest")
def cmd_rigforge_manifest(params):
    """``save`` / ``load`` / ``get`` the character.json manifest for an object."""
    obj = _require_mesh(params)
    action = get_choice(
        params, "action",
        {"SAVE": "save", "LOAD": "load", "GET": "get", "READ": "get"},
        "get",
    )

    # archetype / motion_notes / name are accepted on every action: they are the
    # panel's edit buffer being pushed down before a save.
    archetype = params.get("archetype")
    if isinstance(archetype, str) and archetype.strip():
        _set_prop(obj, PROP_ARCHETYPE, _normalise_archetype(archetype))
    notes = params.get("motion_notes")
    if isinstance(notes, str):
        _set_prop(obj, PROP_MOTION_NOTES, notes)
    character_name = params.get("name")
    if isinstance(character_name, str) and character_name.strip():
        _set_prop(obj, PROP_CHARACTER_NAME, character_name.strip())

    if action == "get":
        return {
            "object": obj.name,
            "action": action,
            "manifest": build_manifest(obj),
            "path": manifest_path(obj, params, required=False),
        }

    if action == "save":
        path = manifest_path(obj, params)
        doc = build_manifest(obj)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(doc, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError as exc:
            raise ForgeError("Could not write the manifest to %s: %s" % (path, exc))
        _set_prop(obj, PROP_MANIFEST_PATH, path)
        return {"object": obj.name, "action": action, "manifest": doc, "path": path,
                "written": True}

    path = manifest_path(obj, params)
    if not os.path.isfile(path):
        raise ForgeError("Manifest not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ForgeError("Could not read the manifest %s: %s" % (path, exc))

    create = get_bool(params, "create_missing_tags", True)
    with object_mode():
        created = apply_manifest(obj, doc, create_missing_tags=create)
    _set_prop(obj, PROP_MANIFEST_PATH, path)
    return {
        "object": obj.name,
        "action": action,
        "manifest": build_manifest(obj),
        "loaded": doc,
        "path": path,
        "created_tags": created,
    }


@command("rigforge_status")
def cmd_rigforge_status(params):
    """One-call overview of a character: tags, manifest state, derived meshes.

    Additive to the Phase 3 sketch (the sketch names ``rigforge_status`` on the
    MCP side only); having it on the socket means the MCP tool is one round trip
    instead of three.
    """
    obj = _require_mesh(params)
    tags = cmd_rigforge_list_tags({"object": obj.name})
    derived = []
    for suffix in ("_retopo", "_lod1", "_lod2", "_lod3", "_lod4"):
        other = bpy.data.objects.get(obj.name + suffix)
        if other is not None and other.type == "MESH":
            derived.append({
                "name": other.name,
                "face_count": len(other.data.polygons),
                "vertex_count": len(other.data.vertices),
                "tags": len(tag_groups(other)),
                "uv_layers": [layer.name for layer in other.data.uv_layers],
                "uv_unwrapped": has_real_unwrap(other),
            })
    return {
        "object": obj.name,
        "archetype": str(_prop(obj, PROP_ARCHETYPE, "") or ""),
        "motion_notes": str(_prop(obj, PROP_MOTION_NOTES, "") or ""),
        "manifest_path": str(_prop(obj, PROP_MANIFEST_PATH, "") or ""),
        "character_name": str(_prop(obj, PROP_CHARACTER_NAME, "") or obj.name),
        "tags": tags["tags"],
        "untagged_faces": tags["untagged_faces"],
        "face_count": len(obj.data.polygons),
        "vertex_count": len(obj.data.vertices),
        "uv_layers": [layer.name for layer in obj.data.uv_layers],
        "uv_unwrapped": has_real_unwrap(obj),
        "retopo": _retopo_block(obj),
        "derived": derived,
        "retopo_source": str(_prop(obj, PROP_RETOPO_SOURCE, "") or ""),
    }


# ---------------------------------------------------------------------------
# retopology (stage 2)
# ---------------------------------------------------------------------------

def _surface_area(obj):
    return float(sum(polygon.area for polygon in obj.data.polygons))


def _max_dimension(obj):
    try:
        return max(float(v) for v in obj.dimensions)
    except (TypeError, ValueError):
        return 0.0


def adaptive_voxel_size(obj, target_faces, oversample=4.0):
    """Voxel size that leaves Quadriflow roughly ``oversample`` x its target.

    A voxel remesh of a surface of area *A* at size *v* lands around ``A / v^2``
    quads, so solving for the face count we want is a better starting point than
    any fixed millimetre value: the same call works on a 3 cm trinket and a 3 m
    creature.  Clamped against the object's own size so a pathological area (a
    sculpt full of interior geometry) cannot ask for a billion voxels.
    """
    target = max(int(target_faces), 16)
    area = _surface_area(obj)
    span = _max_dimension(obj)
    if span <= 0.0:
        raise ForgeError("Object %r has no size to remesh." % obj.name)
    if area <= 0.0:
        area = span * span
    size = math.sqrt(area / (target * float(oversample)))
    low = span / 400.0    # never finer than ~400 voxels across the longest axis
    high = span / 16.0    # never coarser than 16, or the silhouette dissolves
    return max(low, min(high, size))


def _delete_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and getattr(data, "users", 1) == 0:
        try:
            bpy.data.meshes.remove(data)
        except (ReferenceError, RuntimeError, TypeError):
            pass
    return True


def _duplicate_object(obj, name, drop_groups=True):
    """A real copy of ``obj`` named ``name``, in the same collection(s)."""
    _delete_object(name)
    new = obj.copy()
    new.data = obj.data.copy()
    new.name = name
    try:
        new.data.name = name
    except (AttributeError, RuntimeError):
        pass
    targets = list(obj.users_collection) or [get_scene().collection]
    linked = False
    for collection in targets:
        try:
            collection.objects.link(new)
            linked = True
        except RuntimeError:
            pass
    if not linked:
        get_scene().collection.objects.link(new)
    for modifier in list(new.modifiers):
        try:
            new.modifiers.remove(modifier)
        except (RuntimeError, ReferenceError):
            pass
    if drop_groups:
        try:
            new.vertex_groups.clear()
        except (AttributeError, RuntimeError):
            for group in list(new.vertex_groups):
                new.vertex_groups.remove(group)
    # A freshly linked object is absent from view_layer.objects until the
    # depsgraph is evaluated, and every operator below needs to find it.
    refresh_view_layer()
    return new


def transfer_tags(source, target, samples=3):
    """Copy ``tag_*`` groups from ``source`` onto ``target`` by proximity.

    Nearest-vertex lookup through a KD-tree rather than a Data Transfer
    modifier: it is deterministic, needs no modifier stack or depsgraph
    evaluation, and votes over the ``samples`` nearest source vertices so a
    single stray vertex on a tag border cannot claim a whole region.  Both
    objects share a transform (the retopo mesh is a copy), so local coordinates
    compare directly.
    """
    groups = tag_groups(source)
    if not groups:
        return []
    source_mesh = source.data
    if not len(source_mesh.vertices) or not len(target.data.vertices):
        return []

    tree = KDTree(len(source_mesh.vertices))
    for vertex in source_mesh.vertices:
        tree.insert(vertex.co, vertex.index)
    tree.balance()

    membership = _vertex_tag_map(source)
    wanted = {group.index: group.name for group in groups}
    buckets = {index: [] for index in wanted}

    count = max(1, int(samples))
    for vertex in target.data.vertices:
        votes = {}
        for _co, index, _dist in tree.find_n(vertex.co, count):
            for group_index in membership.get(index, ()):
                votes[group_index] = votes.get(group_index, 0) + 1
        if not votes:
            continue
        best = max(votes.values())
        for group_index, tally in votes.items():
            if tally == best and group_index in buckets:
                buckets[group_index].append(vertex.index)

    transferred = []
    for group_index, name in wanted.items():
        vertices = buckets[group_index]
        group = target.vertex_groups.get(name)
        if group is None:
            group = target.vertex_groups.new(name=name)
        if vertices:
            group.add(vertices, 1.0, "REPLACE")
        transferred.append({
            "name": tag_display_name(name),
            "vertex_group": name,
            "vertex_count": len(vertices),
        })
    target.data.update()
    return transferred


def _shrinkwrap(target, source):
    modifier = target.modifiers.new(name="Forge Shrinkwrap", type="SHRINKWRAP")
    modifier.target = source
    try:
        modifier.wrap_method = "NEAREST_SURFACEPOINT"
        modifier.offset = 0.0
    except (AttributeError, TypeError):
        pass
    apply_modifier(target, modifier)


#: Vertex group the LOD pass builds to hold the UV seam (and open boundary)
#: vertices when ``protect_seams`` is on. Removed again as soon as the modifier
#: is applied.
SEAM_PROTECT_GROUP = "forge_lod_seam_protect"


def _seam_vertices(obj):
    """Vertex indices on a UV seam or an open boundary."""
    mesh = obj.data
    out = set()
    for edge in mesh.edges:
        if edge.use_seam:
            out.update(edge.vertices)
    counts = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if count != 2:
            out.update(key)
    return sorted(out)


def _protect_seams(obj):
    """Lock the seam vertices in a vertex group; returns its name or ``None``.

    Blender's Decimate **Collapse** is a position-only quadric: it has no UV
    term at all (the modifier's ``delimit`` option belongs to Planar/Dissolve),
    so a naive decimate is free to walk a vertex across a UV seam and smear the
    atlas.  The only lever Collapse exposes is its vertex group, and measured on
    Blender 5.0.1 that group is a **hard lock, not a soft cost**: a vertex in it
    is never collapsed, whatever its weight (1.0, 0.5, 0.05) and whatever
    ``vertex_group_factor`` says (0.5 through 100 all behave identically).

    So this is not free.  On an unwrapped character the seam vertices *are* most
    of the budget, and locking them floors the reduction well above any LOD
    target - which is why ``protect_seams`` is off by default and the budget
    wins.  Turn it on when an exact atlas matters more than the face count.

    **Only the Decimate fallback needs any of this.**  Under meshoptimizer
    (``meshopt.simplify_lod``, the default whenever the DLL is built) a UV seam
    is an attribute discontinuity the quadric already prices, the survivors keep
    their original UVs bit for bit, and ``protect_seams`` is simply unnecessary -
    it is ignored on that path.  This function is what runs when the DLL is not
    there; see ``native/meshopt/README.md``.
    """
    indices = _seam_vertices(obj)
    if not indices:
        return None
    existing = obj.vertex_groups.get(SEAM_PROTECT_GROUP)
    if existing is not None:
        obj.vertex_groups.remove(existing)
    group = obj.vertex_groups.new(name=SEAM_PROTECT_GROUP)
    group.add(indices, 1.0, "REPLACE")
    return group.name


def _decimate(obj, ratio, protect_seams=False):
    protect = _protect_seams(obj) if protect_seams else None
    modifier = obj.modifiers.new(name="Forge Decimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(1e-4, min(1.0, float(ratio)))
    if protect:
        try:
            modifier.vertex_group = protect
            modifier.invert_vertex_group = False
        except (AttributeError, TypeError, ValueError):
            protect = None
    try:
        apply_modifier(obj, modifier)
    finally:
        group = obj.vertex_groups.get(SEAM_PROTECT_GROUP)
        if group is not None:
            try:
                obj.vertex_groups.remove(group)
            except (RuntimeError, ReferenceError):
                pass
    return protect is not None


def triangle_count(obj):
    """Triangles, not polygons.

    Decimate's ratio is a *triangle* budget and its output is triangles, so a
    face budget expressed against a quad mesh has to be converted before it can
    become a ratio.  Counted from the polygon loops rather than
    ``loop_triangles`` so no depsgraph evaluation is needed.
    """
    return sum(max(0, len(polygon.vertices) - 2) for polygon in obj.data.polygons)


def _decimate_to_budget(obj, budget, protect_seams=False):
    """Collapse ``obj`` towards ``budget`` **triangles**. Returns a report.

    Budgets are counted in triangles throughout, because that is the unit a
    game budget is quoted in and the unit Decimate's ratio is expressed in.
    The polygon count is reported too and is always the smaller of the two:
    Collapse triangulates, then rejoins coplanar pairs back into quads unless
    ``use_collapse_triangulate`` is set, so a 1592-triangle result can show as
    1068 polygons.
    """
    before = triangle_count(obj)
    budget = max(int(LOD_MIN_FACES), int(budget))
    ratio = 1.0 if before <= budget else float(budget) / float(before)
    protected = _decimate(obj, ratio, protect_seams=protect_seams)
    triangles = triangle_count(obj)
    return {
        "budget": budget,
        "ratio": round(ratio, 6),
        "triangles": triangles,
        "face_count": len(obj.data.polygons),
        "triangles_before": before,
        "within_budget": triangles <= budget,
        "seams_protected": bool(protected),
    }


def _mesh_to_meshopt_buffers(obj):
    """Flat split-vertex buffers for :mod:`meshopt`, plus the way back.

    UVs live on *loops*, not vertices, so a UV-aware simplifier needs one vertex
    per distinct ``(vertex, uv)`` corner — the same split a glTF export makes.
    Those splits share the **exact position floats** of the Blender vertex they
    came from, which is precisely what meshoptimizer's position hash needs to
    stitch them back into one topological vertex with a UV discontinuity across
    it.  That is the whole trick: the seam stays a seam for the metric, and stays
    a single vertex for the topology.

    The split key is ``(vertex_index, u, v)`` and *not* the normal: keying on the
    normal too would split every flat-shaded corner as well, tripling the vertex
    count to protect a channel that only weights the metric.  The normal of the
    first corner that mints a split is the one used.
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    uv_layer = mesh.uv_layers.active
    uv_data = uv_layer.data if uv_layer is not None else None

    loop_normals = None
    if len(mesh.loops):
        try:
            buffer = array("f", bytes(4 * 3 * len(mesh.loops)))
            mesh.corner_normals.foreach_get("vector", buffer)
            loop_normals = buffer
        except (AttributeError, RuntimeError, TypeError, ValueError):
            loop_normals = None

    positions = array("f")
    uvs = array("f")
    normals = array("f")
    indices = array("I")
    split_of = {}
    split_to_vertex = []
    split_material = []
    split_smooth = []

    for triangle in mesh.loop_triangles:
        polygon = mesh.polygons[triangle.polygon_index]
        for loop_index, vertex_index in zip(triangle.loops, triangle.vertices):
            if uv_data is not None:
                uv = uv_data[loop_index].uv
                key = (vertex_index, uv[0], uv[1])
            else:
                uv = None
                key = (vertex_index, 0.0, 0.0)
            split = split_of.get(key)
            if split is None:
                split = len(split_to_vertex)
                split_of[key] = split
                co = mesh.vertices[vertex_index].co
                positions.extend((co[0], co[1], co[2]))
                if uv is not None:
                    uvs.extend((uv[0], uv[1]))
                if loop_normals is not None:
                    base = loop_index * 3
                    normals.extend((loop_normals[base], loop_normals[base + 1],
                                    loop_normals[base + 2]))
                else:
                    normal = mesh.vertices[vertex_index].normal
                    normals.extend((normal[0], normal[1], normal[2]))
                split_to_vertex.append(vertex_index)
                split_material.append(polygon.material_index)
                split_smooth.append(bool(polygon.use_smooth))
            indices.append(split)

    return {
        "positions": positions,
        "uvs": uvs if uv_data is not None else None,
        "normals": normals,
        "indices": indices,
        "split_to_vertex": split_to_vertex,
        "split_material": split_material,
        "split_smooth": split_smooth,
        "uv_name": uv_layer.name if uv_layer is not None else None,
        "triangles": len(indices) // 3,
    }


def _rebuild_mesh_from_indices(obj, buffers, new_indices):
    """Replace ``obj``'s geometry with the triangles meshopt returned.

    The index buffer references the *original* split vertices, so every surviving
    vertex is written back with its original position and its original UV — no
    interpolation, no re-projection, bit-identical to LOD0's atlas.  That is the
    property the whole lane exists for, and it is why the LODs can share one
    baked map.

    Vertex groups ride along (the tags the rig and the cloth pass read, and any
    deform weights), copied per surviving vertex through a bmesh deform layer
    rather than ``vertex_groups.add`` per vertex, which is thousands of RNA calls
    for the same result.  Materials and the smooth flag are carried per vertex
    and voted per new triangle: a collapsed triangle has no single source face,
    so a vote is the honest answer rather than a fabricated one.
    """
    mesh = obj.data
    positions = buffers["positions"]
    uvs = buffers["uvs"]
    split_to_vertex = buffers["split_to_vertex"]

    order = []
    new_of_split = {}
    for split in new_indices:
        if split not in new_of_split:
            new_of_split[split] = len(order)
            order.append(split)

    weights = {}
    for split in order:
        vertex_index = split_to_vertex[split]
        if vertex_index not in weights:
            weights[vertex_index] = [(group.group, group.weight)
                                     for group in mesh.vertices[vertex_index].groups]

    bm = bmesh.new()
    deform = bm.verts.layers.deform.verify()
    uv_out = bm.loops.layers.uv.new(buffers["uv_name"]) if buffers["uv_name"] else None

    bm_verts = []
    for split in order:
        base = split * 3
        vert = bm.verts.new((positions[base], positions[base + 1], positions[base + 2]))
        for group_index, weight in weights[split_to_vertex[split]]:
            vert[deform][group_index] = weight
        bm_verts.append(vert)
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()

    materials = buffers["split_material"]
    smooth = buffers["split_smooth"]
    for triangle in range(len(new_indices) // 3):
        corner = triangle * 3
        a, b, c = (new_indices[corner], new_indices[corner + 1], new_indices[corner + 2])
        if a == b or b == c or a == c:
            continue
        try:
            face = bm.faces.new((bm_verts[new_of_split[a]], bm_verts[new_of_split[b]],
                                 bm_verts[new_of_split[c]]))
        except ValueError:
            # Duplicate face: meshopt can emit one when two collapses meet.
            continue
        face.material_index = max(set((materials[a], materials[b], materials[c])),
                                  key=(materials[a], materials[b], materials[c]).count)
        face.smooth = smooth[a] and smooth[b] and smooth[c]
        if uv_out is not None and uvs is not None:
            for loop, split in zip(face.loops, (a, b, c)):
                loop[uv_out].uv = (uvs[split * 2], uvs[split * 2 + 1])

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def _meshopt_to_budget(obj, budget):
    """Collapse ``obj`` towards ``budget`` **triangles** with meshoptimizer.

    Raises :class:`meshopt.MeshoptError` when the library is missing or the mesh
    is not simplifiable, which is the caller's cue to fall back to Decimate.
    """
    before = triangle_count(obj)
    budget = max(int(LOD_MIN_FACES), int(budget))
    buffers = _mesh_to_meshopt_buffers(obj)
    detail = {}
    new_indices, result_error = meshopt.simplify_lod(
        buffers["positions"], buffers["indices"],
        uvs=buffers["uvs"], normals=buffers["normals"],
        target_index_count=budget * 3, report=detail)
    _rebuild_mesh_from_indices(obj, buffers, new_indices)
    triangles = triangle_count(obj)
    return {
        "budget": budget,
        # Reported for symmetry with the Decimate path; meshopt is driven by an
        # index target, so this is the ratio it achieved, not one it was given.
        "ratio": round(float(triangles) / float(before), 6) if before else 1.0,
        "triangles": triangles,
        "face_count": len(obj.data.polygons),
        "triangles_before": before,
        "within_budget": triangles <= budget,
        # protect_seams is a Decimate-only lever and meshopt does not need it:
        # a UV seam is an attribute discontinuity the metric already prices, and
        # the survivors keep their exact UVs, so there is nothing to lock.
        "seams_protected": False,
        "result_error": detail.get("result_error"),
        "achieved_index_count": detail.get("achieved_index_count"),
        "target_index_count": detail.get("target_index_count"),
        "attribute_weights": detail.get("attribute_weights"),
        "split_vertices": len(buffers["split_to_vertex"]),
        "wedges": detail.get("weld", {}).get("wedges"),
    }


def _simplify_to_budget(obj, budget, protect_seams=False):
    """LOD one level down, by meshopt when it is available and Decimate when not.

    Returns ``(report, simplifier)``.  A meshopt failure is never fatal: the
    Decimate path is still correct, only UV-blind, so the fallback happens with
    the reason recorded in ``simplifier`` rather than as an error.
    """
    if meshopt.available():
        try:
            report = _meshopt_to_budget(obj, budget)
            report["simplifier"] = meshopt.simplifier_name()
            return report, report["simplifier"]
        except meshopt.MeshoptError as exc:
            simplifier = "blender-decimate (meshopt failed: %s)" % exc
    else:
        simplifier = meshopt.simplifier_name()
    report = _decimate_to_budget(obj, budget, protect_seams=protect_seams)
    report["simplifier"] = simplifier
    return report, simplifier


def measure_lod_error(reference, lod, samples=2000):
    """Max/mean distance from ``reference``'s vertices to ``lod``'s surface.

    This is the *achieved* geometric error of a level - the number the
    visibility range is computed from.  Blender's Decimate reports nothing of
    the kind, so it is measured after the fact with
    ``Object.closest_point_on_mesh``, which is an exact point-to-surface
    distance rather than a vertex-to-vertex approximation.
    """
    vertices = reference.data.vertices
    total = len(vertices)
    if not total or not len(lod.data.polygons):
        return {"max": 0.0, "mean": 0.0, "samples": 0}
    step = max(1, total // max(1, int(samples)))
    worst = 0.0
    accumulated = 0.0
    taken = 0
    for index in range(0, total, step):
        co = vertices[index].co
        try:
            hit, location, _normal, _face = lod.closest_point_on_mesh(co)
        except (RuntimeError, ValueError):
            continue
        if not hit:
            continue
        distance = (location - co).length
        worst = max(worst, distance)
        accumulated += distance
        taken += 1
    return {
        "max": round(worst, 9),
        "mean": round(accumulated / taken, 9) if taken else 0.0,
        "samples": taken,
    }


def visibility_distance(error, error_pixels=LOD_ERROR_PIXELS):
    """Distance (scene units) at which ``error`` shrinks below ``error_pixels``.

    A world-space error *e* seen from distance *d* subtends ``e / d`` radians;
    multiplied by :data:`LOD_PIXELS_PER_RADIAN` that is its size in pixels.
    Solving for *d* gives the nearest distance at which this level is honest -
    which is exactly Godot's ``visibility_range_begin`` for it.
    """
    error = max(0.0, float(error))
    if error <= 0.0:
        return 0.0
    return error * LOD_PIXELS_PER_RADIAN / max(1e-6, float(error_pixels))


def _ensure_uv_layer(obj, name="UVMap"):
    mesh = obj.data
    if mesh.uv_layers:
        return mesh.uv_layers.active or mesh.uv_layers[0]
    return mesh.uv_layers.new(name=name)


def has_real_unwrap(obj):
    """True when a real unwrap (not ``_ensure_uv_layer``'s filler) ran here."""
    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "uv_layers", None):
        return False
    stored = str(_prop(obj, PROP_UV_UNWRAPPED, "") or "")
    return bool(stored) and stored in mesh.uv_layers


# ---------------------------------------------------------------------------
# normal bake (best effort - Cycles only)
# ---------------------------------------------------------------------------

def _bake_material(obj, image):
    """Make sure every material slot has ``image`` as its active texture node."""
    mesh = obj.data
    if not mesh.materials:
        material = bpy.data.materials.new(name=obj.name + "_bake")
        material.use_nodes = True
        mesh.materials.append(material)
    touched = []
    for material in mesh.materials:
        if material is None:
            continue
        if not material.use_nodes:
            material.use_nodes = True
        tree = material.node_tree
        node = None
        for existing in tree.nodes:
            if existing.type == "TEX_IMAGE" and existing.image is image:
                node = existing
                break
        if node is None:
            node = tree.nodes.new("ShaderNodeTexImage")
            node.location = (-400.0, 300.0)
        node.image = image
        for other in tree.nodes:
            other.select = False
        node.select = True
        tree.nodes.active = node
        touched.append(material.name)
    return touched


def bake_normals(high, low, resolution=2048, path=None, margin=8):
    """Bake a high->low normal map. Returns a report; never raises.

    Baking is the one step of the retopo pipeline that is allowed to fail: it
    needs Cycles, a render context and real time, and a ``--background`` run on a
    machine without a GPU may simply refuse.  Everything else in the pipeline is
    already on disk when this runs, so a failure is reported, not fatal.

    It refuses outright on a mesh that has never been unwrapped.  A bake needs
    somewhere to write, and the obvious "somewhere" - an empty default UV layer -
    is a trap: the real unwrap that follows replaces every coordinate in it, so
    the baked map decodes against a layout that no longer exists and the whole
    bake is silently worthless.  Unwrap first (``rigforge_auto_uv``, or leave
    ``unwrap`` on in ``rigforge_retopo``, which does it in the right order).
    """
    report = {"ok": False, "resolution": int(resolution), "image": None, "path": None,
              "engine": None, "reason": "", "seconds": 0.0}
    scene = get_scene()
    started = time.monotonic()
    if not has_real_unwrap(low):
        report["reason"] = (
            "%r has no real UV unwrap yet, so there is no atlas to bake into; "
            "unwrap it first (rigforge_auto_uv) or the bake would be thrown away "
            "by the unwrap that follows." % low.name
        )
        return report
    previous_engine = scene.render.engine
    previous_bake = {}
    image = None
    try:
        # Cycles is a shipped add-on that `--factory-startup` (or a user who
        # turned it off) leaves disabled, which is not the same thing as not
        # having it. Ask for it, then let the assignment be the real test:
        # scene.render.engine is a dynamic enum, so its static RNA item list is
        # not a reliable way to ask what is registered.
        try:
            if "cycles" not in bpy.context.preferences.addons:
                import addon_utils

                addon_utils.enable("cycles", default_set=False, persistent=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            scene.render.engine = "CYCLES"
        except (TypeError, ValueError) as exc:
            report["reason"] = ("Cycles is not available in this Blender session (%s). "
                                "Enable the Cycles add-on, then bake again." % exc)
            return report
        if scene.render.engine != "CYCLES":
            report["reason"] = (
                "Blender would not switch to Cycles (still %r); the Cycles add-on is "
                "probably disabled." % scene.render.engine
            )
            return report
        report["engine"] = "CYCLES"

        _ensure_uv_layer(low)
        if not low.data.uv_layers:
            report["reason"] = "The retopo mesh has no UV layer to bake into."
            return report

        name = "%s_normal" % low.name
        existing = bpy.data.images.get(name)
        if existing is not None:
            bpy.data.images.remove(existing)
        image = bpy.data.images.new(name, int(resolution), int(resolution),
                                    alpha=False, float_buffer=False)
        image.generated_color = (0.5, 0.5, 1.0, 1.0)
        image.colorspace_settings.name = "Non-Color"
        _bake_material(low, image)

        scene.render.engine = "CYCLES"
        report["engine"] = "CYCLES"
        cycles = getattr(scene, "cycles", None)
        if cycles is not None:
            try:
                cycles.device = "CPU"
                cycles.samples = 1
                cycles.use_denoising = False
            except (AttributeError, TypeError):
                pass
        bake = scene.render.bake
        for key in ("use_selected_to_active", "cage_extrusion", "margin", "use_clear",
                    "max_ray_distance"):
            if hasattr(bake, key):
                previous_bake[key] = getattr(bake, key)
        extrusion = max(1e-4, _max_dimension(low) * 0.02)
        if hasattr(bake, "use_selected_to_active"):
            bake.use_selected_to_active = True
        if hasattr(bake, "cage_extrusion"):
            bake.cage_extrusion = extrusion
        if hasattr(bake, "max_ray_distance"):
            bake.max_ray_distance = extrusion * 2.0
        if hasattr(bake, "margin"):
            bake.margin = int(margin)
        if hasattr(bake, "use_clear"):
            bake.use_clear = True

        with selection([high, low], low):
            status = bpy.ops.object.bake(
                **op_kwargs(bpy.ops.object.bake,
                            {"type": "NORMAL", "use_clear": True, "margin": int(margin),
                             "use_selected_to_active": True,
                             "cage_extrusion": extrusion})
            )
        if "FINISHED" not in status:
            report["reason"] = ("Cycles returned %s from object.bake."
                                % (", ".join(sorted(status)) or "nothing"))
            return report

        report["ok"] = True
        report["image"] = image.name
        if path:
            target = resolve_path(path, make_parents=True, default_ext=".png")
            image.filepath_raw = target
            image.file_format = "PNG"
            image.save()
            report["path"] = target
    except Exception as exc:  # noqa: BLE001 - bake is best effort, by contract
        report["reason"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        try:
            scene.render.engine = previous_engine
        except (TypeError, ValueError):
            pass
        for key, value in previous_bake.items():
            try:
                setattr(scene.render.bake, key, value)
            except (AttributeError, TypeError, ValueError):
                pass
        if not report["ok"] and image is not None:
            try:
                bpy.data.images.remove(image)
            except (ReferenceError, RuntimeError):
                pass
    return report


# ---------------------------------------------------------------------------
# rigforge_retopo
# ---------------------------------------------------------------------------

def _resolve_target_faces(obj, params):
    platform = get_choice(
        params, "platform",
        {"DESKTOP": "desktop", "MOBILE": "mobile"},
        "desktop",
    )
    explicit = params.get("target_faces")
    if explicit is not None:
        return get_int(params, "target_faces", minimum=16, maximum=10_000_000), platform, "param"
    stored = _retopo_block(obj)
    key = "target_faces_%s" % platform
    value = stored.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 16:
        return int(value), platform, "manifest"
    return PLATFORM_TARGETS[platform], platform, "platform preset"


def _resolve_lod_budgets(params, platform, lods, lod0_triangles):
    """Absolute **triangle** budgets per LOD level, from level 1 downwards.

    Fixed ``0.5 ** level`` ratios say nothing about what the asset may cost:
    half of a 200 000-triangle mesh is still 100 000 triangles and still
    unshippable.  A budget does say it.  The default chain starts at the
    platform's game budget (or LOD0's own count, whichever is smaller - there
    is no point "reducing" to more triangles than LOD0 has) and takes a quarter
    per level.
    """
    explicit = params.get("lod_budgets")
    if explicit is not None:
        if not isinstance(explicit, (list, tuple)) or not explicit:
            raise ForgeError("'lod_budgets' must be a non-empty list of face counts.")
        budgets = []
        for entry in explicit:
            if isinstance(entry, bool) or not isinstance(entry, (int, float)):
                raise ForgeError("Every 'lod_budgets' entry must be a face count, got %r."
                                 % (entry,))
            budgets.append(max(LOD_MIN_FACES, int(entry)))
        return budgets[:lods] if lods else budgets, "param"

    ceiling = min(int(lod0_triangles), LOD_BUDGETS.get(platform, LOD_BUDGETS["desktop"]))
    budgets = []
    current = float(ceiling)
    for _level in range(lods):
        current *= LOD_STEP
        budgets.append(max(LOD_MIN_FACES, int(round(current))))
    return budgets, "platform budget"


@command("rigforge_retopo")
def cmd_rigforge_retopo(params):
    """Stage 2: a game mesh from the sculpt, tags and all.

    The canonical finishing order, and the order this runs in:

    ``repair -> retopo -> UV unwrap -> bake high->low -> tangents -> LOD``

    So: voxel remesh -> Quadriflow -> shrinkwrap back onto the sculpt -> tag
    transfer by proximity -> **unwrap LOD0** -> optional high->low normal bake
    into that atlas -> LODs decimated **from the unwrapped LOD0**.  The voxel
    pass is first for a reason: it is what makes a non-manifold sculpt
    (overlapping blobs, self-intersections, holes) safe for Quadriflow, which
    refuses anything that is not watertight.

    The three orderings that are not negotiable:

    * the unwrap comes **before** the bake, or the bake writes into the
      throwaway UV layer a later unwrap replaces and is silently worthless;
    * the LODs come **from** the unwrapped LOD0, so every level carries LOD0's
      atlas and one set of baked maps serves the whole chain;
    * tangents are exported (``rigforge_export_godot``), not generated by the
      engine, or the normal map decodes against a basis it was not baked in.
    """
    obj = _require_mesh(params)
    if not len(obj.data.polygons):
        raise ForgeError("Object %r has no faces to retopologise." % obj.name)

    target_faces, platform, target_source = _resolve_target_faces(obj, params)
    lods = get_int(params, "lods", 0, minimum=0, maximum=8)
    do_unwrap = get_bool(params, "unwrap", True)
    do_bake = get_bool(params, "bake_normals", False)
    bake_resolution = get_int(params, "bake_resolution", 2048, minimum=16, maximum=8192)
    bake_path = params.get("bake_path")
    keep_original = get_bool(params, "keep_original", True)
    voxel_override = params.get("voxel_size")
    uv_margin = get_float(params, "margin", 0.02, minimum=0.0, maximum=0.5)
    uv_angle_limit = get_float(params, "angle_limit", 66.0, minimum=0.0, maximum=180.0)
    seams_from_tags = get_bool(params, "seams_from_tags", True)
    # Off by default, and the docstring of _protect_seams says why: Blender's
    # Decimate vertex group is a hard lock, so protecting the seams of an
    # unwrapped mesh costs more budget than the tearing it prevents. Under
    # meshopt (the default when the DLL is built) it is not merely off, it is
    # unnecessary and ignored - seams survive as attribute discontinuities.
    protect_seams = get_bool(params, "protect_seams", False)

    if do_bake and not do_unwrap:
        raise ForgeError(
            "bake_normals with unwrap=false would bake into a throwaway UV layer "
            "that the next unwrap replaces, so the bake would be worthless. Leave "
            "'unwrap' on, or unwrap first with rigforge_auto_uv and bake after."
        )

    warnings = []
    if not keep_original:
        warnings.append(
            "keep_original=false is not supported in v1; the sculpt is never "
            "destroyed. Delete it yourself once you are happy with the retopo."
        )

    retopo_name = "%s_retopo" % obj.name
    before = mesh_stats(obj)
    stages = []
    started = time.monotonic()

    with object_mode():
        retopo = _duplicate_object(obj, retopo_name)
        # The copy inherits the sculpt's custom properties, and the remesh below
        # throws away whatever UVs came with them: this mesh has not been
        # unwrapped until stage 5 says so.
        _set_prop(retopo, PROP_UV_UNWRAPPED, "")

        # --- 1. voxel remesh: makes a non-manifold sculpt safe for Quadriflow
        if voxel_override is not None:
            voxel_size = get_float(params, "voxel_size", minimum=1e-9)
        else:
            voxel_size = adaptive_voxel_size(retopo, target_faces)
        method = common._voxel_remesh(retopo, voxel_size, 0.0)
        stages.append({"stage": "voxel_remesh", "voxel_size": round(voxel_size, 8),
                       "method": method, "face_count": len(retopo.data.polygons)})
        if not len(retopo.data.polygons):
            raise ForgeError(
                "The voxel remesh of %r produced no faces (voxel size %.6g was too "
                "coarse for this sculpt). Pass a smaller 'voxel_size'."
                % (obj.name, voxel_size)
            )

        # --- 2. Quadriflow to the target, with a decimate fallback
        quad_method = "quadriflow"
        try:
            common._quad_remesh(retopo, target_faces, False, False, False, 0)
        except ForgeError as exc:
            quad_method = "decimate_fallback"
            warnings.append("Quadriflow failed after the voxel pass (%s); fell back to "
                            "a collapse decimate to the same target." % exc)
            current = len(retopo.data.polygons)
            if current > target_faces:
                _decimate(retopo, float(target_faces) / float(current))
        stages.append({"stage": "quad_remesh", "method": quad_method,
                       "target_faces": target_faces,
                       "face_count": len(retopo.data.polygons)})

        # --- 3. shrinkwrap back onto the sculpt (applied)
        _shrinkwrap(retopo, obj)
        stages.append({"stage": "shrinkwrap", "target": obj.name,
                       "method": "NEAREST_SURFACEPOINT", "applied": True})

        # --- 4. tags, by proximity
        transferred = transfer_tags(obj, retopo)
        stages.append({"stage": "tag_transfer", "method": "kdtree_nearest_vote",
                       "tags": len(transferred)})

        # carry the character metadata onto the retopo mesh so the manifest,
        # the UV pass and stage 4 all work off it directly.
        _set_prop(retopo, PROP_RETOPO_SOURCE, obj.name)
        _set_prop(retopo, PROP_RETOPO, json.dumps(dict(
            _retopo_block(obj), **{"target_faces_%s" % platform: int(target_faces),
                                   "lods": int(lods)})))
        _set_prop(obj, PROP_RETOPO, json.dumps(dict(
            _retopo_block(obj), **{"target_faces_%s" % platform: int(target_faces),
                                   "lods": int(lods)})))

        # --- 5. UV unwrap of LOD0, BEFORE the bake and before the LODs.
        uv_report = None
        if do_unwrap:
            uv_report = auto_uv(retopo, seams_from_tags=seams_from_tags,
                                margin=uv_margin, angle_limit=uv_angle_limit)
            stages.append({"stage": "auto_uv", "object": retopo.name,
                           "uv_layer": uv_report["uv_layer"],
                           "islands": uv_report["islands"],
                           "uv_coverage": uv_report["uv_coverage"]})
        else:
            warnings.append(
                "unwrap=false: this mesh has no atlas, so it cannot be baked into "
                "and its LODs have no UVs to share. Run rigforge_auto_uv before "
                "baking or exporting."
            )

        refresh_view_layer()

    # --- 6. bake (outside object_mode(): it drives its own selection)
    bake_report = None
    if do_bake:
        bake_report = bake_normals(obj, retopo, resolution=bake_resolution,
                                   path=bake_path if isinstance(bake_path, str) else None)
        stages.append({"stage": "bake_normals", "ok": bake_report["ok"],
                       "reason": bake_report["reason"]})
        if not bake_report["ok"]:
            warnings.append("Normal bake did not run: %s" % bake_report["reason"])

    # --- 7. LODs, decimated FROM the unwrapped, baked LOD0 so every level
    #        carries LOD0's atlas and one set of maps serves the whole chain.
    lod_objects = []
    lod_reports = []
    lod0_faces = len(retopo.data.polygons)
    lod0_triangles = triangle_count(retopo)
    budget_ceiling = LOD_BUDGETS.get(platform, LOD_BUDGETS["desktop"])
    budgets, budget_source = _resolve_lod_budgets(params, platform, lods, lod0_triangles)
    radius = max(1e-9, _max_dimension(retopo) * 0.5)
    with object_mode():
        for level in range(1, lods + 1):
            budget = budgets[level - 1] if level - 1 < len(budgets) else budgets[-1]
            lod_name = "%s_lod%d" % (obj.name, level)
            # Every level is decimated from LOD0, never from the level above:
            # chaining would compound the UV drift instead of measuring it once.
            lod = _duplicate_object(retopo, lod_name, drop_groups=False)
            report, _simplifier = _simplify_to_budget(lod, budget,
                                                      protect_seams=protect_seams)
            error = measure_lod_error(retopo, lod)
            report.update({
                "level": level,
                "object": lod.name,
                "error": error["max"],
                "error_mean": error["mean"],
                "error_relative": round(error["max"] / radius, 6),
                "visibility_begin": round(visibility_distance(error["max"]), 4),
                "uv_layer": (lod.data.uv_layers.active.name
                             if lod.data.uv_layers and lod.data.uv_layers.active else None),
            })
            _set_prop(lod, PROP_LOD, json.dumps({
                "level": level, "budget": report["budget"],
                "face_count": report["face_count"], "error": report["error"],
                "error_relative": report["error_relative"],
                "visibility_begin": report["visibility_begin"],
                "simplifier": report["simplifier"],
                "source": retopo.name,
            }))
            if not report["within_budget"]:
                if report["seams_protected"]:
                    why = ("; protect_seams locks every seam vertex, which is what "
                           "is holding it up")
                elif report["simplifier"].startswith("meshopt"):
                    why = ("; meshopt stopped at %s indices - topology or attribute "
                           "discontinuities blocked the rest"
                           % report.get("achieved_index_count"))
                else:
                    why = "; Decimate could collapse no further"
                warnings.append(
                    "%s came out at %d triangles against a %d-triangle budget%s."
                    % (lod.name, report["triangles"], report["budget"], why))
            if do_unwrap and not report["uv_layer"]:
                warnings.append("%s lost its UV layer during decimation." % lod.name)
            lod_objects.append(lod)
            lod_reports.append(report)
            stages.append({"stage": "lod", "level": level, "ratio": report["ratio"],
                           "budget": report["budget"], "object": lod.name,
                           "face_count": report["face_count"],
                           "triangles": report["triangles"],
                           "simplifier": report["simplifier"],
                           "error": report["error"]})
        refresh_view_layer()

    if lod0_triangles > budget_ceiling:
        warnings.append(
            "LOD0 is %d triangles against the %s game budget of %d; pass a smaller "
            "'target_faces' or accept that this asset is over budget."
            % (lod0_triangles, platform, budget_ceiling))

    after = mesh_stats(obj)
    if (before["face_count"], before["vertex_count"]) != (after["face_count"],
                                                          after["vertex_count"]):
        warnings.append("The sculpt changed during retopology; that is a bug, please report it.")

    objects = [retopo] + lod_objects
    face_counts = {o.name: len(o.data.polygons) for o in objects}
    return {
        "source": obj.name,
        "object": retopo.name,
        "objects": [o.name for o in objects],
        "face_counts": face_counts,
        "vertex_counts": {o.name: len(o.data.vertices) for o in objects},
        "target_faces": target_faces,
        "target_source": target_source,
        "platform": platform,
        "voxel_size": round(voxel_size, 8),
        "quad_method": quad_method,
        "lods": lods,
        "lod_reports": lod_reports,
        # The simplifier the chain actually ran on, named once at the top so a
        # reader does not have to open a per-level report to find out whether the
        # UV-aware path was in play. Same string the per-level reports carry.
        "simplifier": (lod_reports[0]["simplifier"] if lod_reports
                       else meshopt.simplifier_name()),
        "lod_budgets": budgets,
        "lod_budget_source": budget_source,
        "budget": {
            "platform": platform,
            "lod0": budget_ceiling,
            "lod0_faces": lod0_faces,
            "lod0_triangles": lod0_triangles,
            "unit": "triangles",
            "within_budget": lod0_triangles <= budget_ceiling,
        },
        "unwrapped": bool(do_unwrap),
        "uv": uv_report,
        "tags": transferred,
        "tags_transferred": len([t for t in transferred if t["vertex_count"]]),
        "keep_original": True,
        "source_face_count": after["face_count"],
        "stages": stages,
        "baked": bake_report,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# auto UV (stage 3)
# ---------------------------------------------------------------------------

def mark_tag_seams(obj, angle_limit_deg=66.0, use_tags=True):
    """Seam every edge where the tag changes; fall back to sharp angles.

    Tag boundaries are exactly the seams a character wants — the plan calls them
    out by name: neck, shoulders, wrists — because that is where one named
    region stops and the next begins.  A mesh with no tags (or one big one) has
    no boundaries to use, so it falls back to Blender's own smart-project style
    angle threshold.  Open boundaries are always seams; a hole cannot be
    unwrapped through.
    """
    mesh = obj.data
    face_map = _face_tag_map(obj) if use_tags else {}
    distinct = {marks for marks in face_map.values() if marks}
    from_tags = use_tags and len(distinct) >= 2
    limit = math.radians(max(0.0, min(180.0, float(angle_limit_deg))))

    seams = 0
    tag_seams = 0
    angle_seams = 0
    boundary_seams = 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for edge in bm.edges:
            edge.seam = False
        for edge in bm.edges:
            faces = edge.link_faces
            if len(faces) != 2:
                edge.seam = True
                boundary_seams += 1
                seams += 1
                continue
            if from_tags:
                if face_map.get(faces[0].index) != face_map.get(faces[1].index):
                    edge.seam = True
                    tag_seams += 1
                    seams += 1
                continue
            try:
                angle = edge.calc_face_angle(0.0)
            except (ValueError, RuntimeError):
                angle = 0.0
            if angle > limit:
                edge.seam = True
                angle_seams += 1
                seams += 1
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()

    return {
        "seams": seams,
        "seam_source": "tags" if from_tags else ("angle" if use_tags else "angle"),
        "tag_seams": tag_seams,
        "angle_seams": angle_seams,
        "boundary_seams": boundary_seams,
        "distinct_tag_regions": len(distinct),
        "angle_limit_deg": angle_limit_deg,
    }


def _uv_report(obj):
    """``(islands, coverage, faces_with_uv_area)`` from the active UV layer."""
    mesh = obj.data
    if not mesh.uv_layers:
        return 0, 0.0, 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        layer = bm.loops.layers.uv.active
        if layer is None:
            return 0, 0.0, 0
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        # union-find over faces joined by an edge whose two UV pairs coincide
        parent = list(range(len(bm.faces)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        def uv_at(face, vert):
            for loop in face.loops:
                if loop.vert is vert:
                    return loop[layer].uv
            return None

        epsilon = 1e-6
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            first, second = edge.link_faces
            matched = True
            for vert in edge.verts:
                a = uv_at(first, vert)
                b = uv_at(second, vert)
                if a is None or b is None or (a - b).length > epsilon:
                    matched = False
                    break
            if matched:
                union(first.index, second.index)

        coverage = 0.0
        with_area = 0
        used = set()
        for face in bm.faces:
            points = [loop[layer].uv for loop in face.loops]
            area = 0.0
            for index in range(len(points)):
                current = points[index]
                nxt = points[(index + 1) % len(points)]
                area += current.x * nxt.y - nxt.x * current.y
            area = abs(area) * 0.5
            coverage += area
            if area > 1e-12:
                with_area += 1
            used.add(find(face.index))
        return len(used), coverage, with_area
    finally:
        bm.free()


def _unwrap_and_pack(obj, margin, angle_limit_deg, method="ANGLE_BASED"):
    """Angle-based unwrap along the marked seams, then pack the islands."""
    scene = get_scene()
    tool_settings = scene.tool_settings
    previous_sync = getattr(tool_settings, "use_uv_select_sync", None)
    notes = []
    try:
        if previous_sync is not None:
            # With sync on, "all faces selected" means "all UVs selected", which
            # is the only way to tell pack_islands to pack everything without a
            # UV editor to select in.
            tool_settings.use_uv_select_sync = True
        with active_only(obj):
            _ensure_uv_layer(obj)
            try:
                bpy.ops.object.mode_set(mode="EDIT")
            except RuntimeError as exc:
                raise ForgeError("Could not enter Edit Mode on %r to unwrap: %s"
                                 % (obj.name, exc))
            try:
                bpy.ops.mesh.select_all(action="SELECT")
                status = set()
                try:
                    status = bpy.ops.uv.unwrap(
                        **op_kwargs(bpy.ops.uv.unwrap,
                                    {"method": method, "fill_holes": True,
                                     "correct_aspect": True, "margin": margin})
                    )
                except (RuntimeError, TypeError) as exc:
                    notes.append("uv.unwrap failed (%s)" % exc)
                if "FINISHED" not in status:
                    if not notes:
                        notes.append("uv.unwrap returned %s"
                                     % (", ".join(sorted(status)) or "nothing"))
                    try:
                        status = bpy.ops.uv.smart_project(
                            **op_kwargs(bpy.ops.uv.smart_project,
                                        {"angle_limit": math.radians(angle_limit_deg),
                                         "island_margin": margin,
                                         "correct_aspect": True, "scale_to_bounds": False})
                        )
                    except (RuntimeError, TypeError) as exc:
                        raise ForgeError(
                            "Neither uv.unwrap nor uv.smart_project could unwrap %r: %s"
                            % (obj.name, exc))
                    if "FINISHED" not in status:
                        raise ForgeError("Unwrapping %r failed: %s" % (obj.name, "; ".join(notes)))
                    unwrap_method = "smart_project"
                else:
                    unwrap_method = "unwrap:%s" % method

                packed = False
                try:
                    # Leave udim_source alone: its default packs into the 0..1
                    # tile, while ORIGINAL_AABB would pack into whatever box the
                    # unwrap happened to produce and quietly cost ~10% coverage.
                    pack = bpy.ops.uv.pack_islands(
                        **op_kwargs(bpy.ops.uv.pack_islands,
                                    {"rotate": True, "scale": True, "margin": margin,
                                     "margin_method": "SCALED",
                                     "shape_method": "CONCAVE"})
                    )
                    packed = "FINISHED" in pack
                    if not packed:
                        notes.append("uv.pack_islands returned %s"
                                     % (", ".join(sorted(pack)) or "nothing"))
                except (RuntimeError, TypeError) as exc:
                    notes.append("uv.pack_islands failed (%s)" % exc)
            finally:
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except RuntimeError:
                    pass
    finally:
        if previous_sync is not None:
            try:
                tool_settings.use_uv_select_sync = previous_sync
            except (AttributeError, TypeError):
                pass
    return unwrap_method, packed, notes


def auto_uv(obj, seams_from_tags=True, margin=0.02, angle_limit=66.0,
            method="ANGLE_BASED"):
    """Seams from the tag boundaries, unwrap, pack. The stage-3 body.

    Factored out of :func:`cmd_rigforge_auto_uv` so the retopo pipeline can run
    it **in the right place** - after the tag transfer, before the bake - rather
    than leaving the artist to call it afterwards and invalidate their own bake.
    """
    if not len(obj.data.polygons):
        raise ForgeError("Object %r has no faces to unwrap." % obj.name)

    with object_mode():
        seam_report = mark_tag_seams(obj, angle_limit_deg=angle_limit,
                                     use_tags=seams_from_tags)
        unwrap_method, packed, notes = _unwrap_and_pack(obj, margin, angle_limit, method)
        islands, coverage, faces_with_area = _uv_report(obj)

    layer = obj.data.uv_layers.active or (obj.data.uv_layers[0] if obj.data.uv_layers else None)
    if layer is not None:
        # The mark that turns a UV layer from "somewhere to put coordinates"
        # into "the atlas this asset's maps are baked against".
        _set_prop(obj, PROP_UV_UNWRAPPED, layer.name)
    result = {
        "object": obj.name,
        "islands": islands,
        "uv_coverage": round(float(coverage), 6),
        "uv_layer": layer.name if layer is not None else None,
        "face_count": len(obj.data.polygons),
        "faces_with_uv_area": faces_with_area,
        "margin": margin,
        "method": unwrap_method,
        "packed": packed,
        "notes": notes,
    }
    result.update(seam_report)
    return result


@command("rigforge_auto_uv")
def cmd_rigforge_auto_uv(params):
    """Stage 3: seams from the tag boundaries, unwrap, pack."""
    obj = _require_mesh(params)
    seams_from_tags = get_bool(params, "seams_from_tags", True)
    margin = get_float(params, "margin", 0.02, minimum=0.0, maximum=0.5)
    angle_limit = get_float(params, "angle_limit", 66.0, minimum=0.0, maximum=180.0)
    method = get_choice(
        params, "method",
        {"ANGLE_BASED": "ANGLE_BASED", "CONFORMAL": "CONFORMAL",
         "MINIMUM_STRETCH": "MINIMUM_STRETCH"},
        "ANGLE_BASED",
    )
    return auto_uv(obj, seams_from_tags=seams_from_tags, margin=margin,
                   angle_limit=angle_limit, method=method)


# ---------------------------------------------------------------------------
# panel state
# ---------------------------------------------------------------------------

ARCHETYPE_ITEMS = (
    ("biped", "Biped", "Two legs, two arms, one head - the Rigify human template"),
    ("quadruped", "Quadruped", "Four legs - the Rigify quadruped template"),
    ("custom", "Custom", "Assembled from limb, spine and tail modules"),
)

PLATFORM_ITEMS = (
    ("desktop", "Desktop", "Higher polycount budget (15000 faces by default)"),
    ("mobile", "Mobile", "Lower polycount budget (5000 faces by default)"),
)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_rigforge", None)


def set_status(props, message, error=False):
    if props is None:
        return
    props.status = str(message).strip().splitlines()[0][:400] if message else ""
    props.status_is_error = bool(error)
    if error and message:
        print("[Forge/RigForge]", message)


def active_mesh(context=None):
    """The active object if it is a usable mesh, else ``None``."""
    context = context or bpy.context
    obj = getattr(context, "object", None)
    if obj is None:
        try:
            obj = get_view_layer().objects.active
        except ForgeError:
            obj = None
    if obj is None or obj.type != "MESH" or obj.data is None:
        return None
    return obj


def _push_meta(obj, props):
    """Panel edit buffer -> object custom properties."""
    if obj is None or props is None:
        return
    _set_prop(obj, PROP_ARCHETYPE, props.archetype)
    _set_prop(obj, PROP_MOTION_NOTES, props.motion_notes)
    if props.character_name.strip():
        _set_prop(obj, PROP_CHARACTER_NAME, props.character_name.strip())
    if props.manifest_path.strip():
        _set_prop(obj, PROP_MANIFEST_PATH, props.manifest_path.strip())


#: Set while :func:`pull_meta` writes, so the ``update=`` callbacks below do not
#: push a half-filled buffer back over the object we are reading from.
_SUPPRESS_META_UPDATE = False


def pull_meta(obj, props):
    """Object custom properties -> panel edit buffer.

    Every value is snapshotted before the first assignment: each ``props``
    write fires ``_meta_update``, and a push mid-read would write the *old*
    buffer over the object's stored notes before we had read them.
    """
    global _SUPPRESS_META_UPDATE
    if obj is None or props is None:
        return
    archetype = _normalise_archetype(_prop(obj, PROP_ARCHETYPE, "custom"))
    snapshot = {
        "archetype": archetype if archetype in ARCHETYPES else "custom",
        "motion_notes": str(_prop(obj, PROP_MOTION_NOTES, "") or ""),
        "character_name": str(_prop(obj, PROP_CHARACTER_NAME, "") or obj.name),
        "manifest_path": str(_prop(obj, PROP_MANIFEST_PATH, "") or ""),
    }
    block = _retopo_block(obj)

    _SUPPRESS_META_UPDATE = True
    try:
        props.archetype = snapshot["archetype"]
        props.motion_notes = snapshot["motion_notes"]
        props.character_name = snapshot["character_name"]
        if snapshot["manifest_path"]:
            props.manifest_path = snapshot["manifest_path"]
        lods = block.get("lods")
        if isinstance(lods, int) and 0 <= lods <= 8:
            props.lods = lods
    finally:
        _SUPPRESS_META_UPDATE = False


def _meta_update(self, context):
    """Keep the active object in step while the panel fields are edited."""
    if _SUPPRESS_META_UPDATE:
        return
    obj = active_mesh(context)
    if obj is not None:
        _push_meta(obj, self)


class ForgeRigForgeProps(PropertyGroup):
    """Scene-level RigForge panel state (the object holds the real values)."""

    new_tag_name: StringProperty(
        name="Tag",
        description="Name for a new semantic tag, e.g. Head, Arm.L, Ear.R, Tail",
        default="",
    )
    archetype: EnumProperty(
        name="Archetype",
        description="What kind of creature this is; picks the rig template in stage 4",
        items=ARCHETYPE_ITEMS,
        default="biped",
        update=_meta_update,
    )
    motion_notes: StringProperty(
        name="Motion Notes",
        description=(
            "Plain language, e.g. 'ears are floppy and lag behind the head', "
            "'tail drags on the ground', 'hops rather than walks'"
        ),
        default="",
        update=_meta_update,
    )
    character_name: StringProperty(
        name="Character",
        description="Name written into the manifest (blank = the object's name)",
        default="",
        update=_meta_update,
    )
    manifest_path: StringProperty(
        name="Manifest",
        description="character.json for this sculpt",
        default="",
        subtype="FILE_PATH",
        update=_meta_update,
    )

    platform: EnumProperty(
        name="Platform",
        description="Polycount budget for the retopologised game mesh",
        items=PLATFORM_ITEMS,
        default="desktop",
    )
    target_faces: IntProperty(
        name="Target Faces",
        description="Quadriflow target. 0 = the platform preset (desktop 15000, mobile 5000)",
        default=0,
        min=0,
        max=1_000_000,
    )
    lods: IntProperty(
        name="LODs",
        description=(
            "Extra decimated levels below the retopo mesh. Each level targets a "
            "quarter of the level above it, capped by the platform game budget "
            "(desktop 50000, mobile 10000 faces)"
        ),
        default=2,
        min=0,
        max=8,
    )
    retopo_unwrap: BoolProperty(
        name="Unwrap",
        description=(
            "Unwrap the retopo mesh before baking and before the LODs are cut, "
            "so the bake lands in the real atlas and every LOD shares it"
        ),
        default=True,
    )
    bake_normals: BoolProperty(
        name="Bake Normals",
        description="Bake a high-to-low normal map with Cycles (best effort)",
        default=False,
    )
    bake_resolution: IntProperty(
        name="Bake Size",
        description="Normal map resolution in pixels, square",
        default=2048,
        min=16,
        max=8192,
    )
    uv_margin: FloatProperty(
        name="Margin",
        description="Gap left between packed UV islands",
        default=0.02,
        min=0.0,
        max=0.5,
        precision=3,
    )
    uv_angle_limit: FloatProperty(
        name="Angle",
        description="Fallback sharp-edge seam angle, used when the mesh has fewer than two tags",
        default=66.0,
        min=0.0,
        max=180.0,
    )
    uv_seams_from_tags: BoolProperty(
        name="Seams From Tags",
        description="Mark a seam wherever one tagged region meets another (neck, shoulders, wrists)",
        default=True,
    )

    # --- Phase 4: rig + Godot export ---------------------------------------
    rig_preset: EnumProperty(
        name="Template",
        description=(
            "Which Rigify metarig to fit. Auto picks the 29-bone basic human "
            "unless the sculpt has face or hand tags, which earn the full one"
        ),
        items=(
            ("auto", "Auto", "Choose from the tags on the mesh"),
            ("basic_human", "Basic Human", "29 bones: spine, arms, legs - the game rig"),
            ("human", "Human", "The full Rigify human: face and fingers too"),
            ("quadruped", "Quadruped", "Rigify's basic quadruped"),
        ),
        default="auto",
    )
    max_influences: IntProperty(
        name="Max Influences",
        description="Bones allowed to move one vertex (4 is what game engines expect)",
        default=4,
        min=1,
        max=12,
    )
    export_path: StringProperty(
        name="Export",
        description="Where to write the glTF (.glb or .gltf); the Godot import script goes beside it",
        default="",
        subtype="FILE_PATH",
    )
    export_actions: EnumProperty(
        name="Actions",
        description="Which actions to bake onto the deform bones",
        items=(
            ("all", "All", "Every action that animates this rig"),
            ("selected", "Named", "Only the actions listed below"),
        ),
        default="all",
    )
    export_action_names: StringProperty(
        name="Names",
        description="Comma-separated action names, e.g. idle-loop, walk-loop, jump",
        default="",
    )
    export_lods: EnumProperty(
        name="LODs",
        description=(
            "Godot 4 generates LODs on import with meshoptimizer and has no "
            "-lodN suffix to recognise ours by, so the default ships LOD0 alone "
            "and lets the importer do it"
        ),
        items=(
            ("auto", "Godot", "Export LOD0 only; Godot's importer generates the levels"),
            ("manual", "Manual",
             "Also export <mesh>_lod1/_lod2, wired with visibility ranges so they switch"),
        ),
        default="auto",
    )
    root_motion: BoolProperty(
        name="Root Motion",
        description="Move each clip's horizontal hip travel onto the root bone",
        default=False,
    )

    status: StringProperty(name="Status", default="Idle")
    status_is_error: BoolProperty(default=False)
    summary: StringProperty(name="Summary", default="")


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class _RigForgeOperator(Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_mesh(context) is not None

    def fail(self, props, message):
        set_status(props, message, error=True)
        self.report({"ERROR"}, str(message))
        return {"CANCELLED"}

    def guarded(self, context, work):
        """Run ``work(obj, props)``, funnelling every failure into the status line."""
        props = get_props(context)
        obj = active_mesh(context)
        if obj is None:
            return self.fail(props, "Select a mesh object first.")
        try:
            return work(obj, props)
        except ForgeError as exc:
            return self.fail(props, str(exc))
        except Exception as exc:  # noqa: BLE001 - the panel must never traceback
            import traceback as _traceback

            _traceback.print_exc()
            return self.fail(props, "%s: %s" % (type(exc).__name__, exc))


class FORGE_OT_rf_new_tag(_RigForgeOperator):
    bl_idname = "forge.rf_new_tag"
    bl_label = "New Tag"
    bl_description = (
        "Create a tag from the name field. Any selected faces are assigned to it "
        "straight away; with nothing selected the tag is created empty"
    )

    def execute(self, context):
        def work(obj, props):
            name = (props.new_tag_name or "").strip()
            if not name:
                return self.fail(props, "Type a tag name first (Head, Arm.L, Tail ...).")
            group_name = tag_group_name(name)
            faces = selected_faces(obj)
            if faces:
                result = cmd_rigforge_tag({"object": obj.name, "tag": name,
                                           "faces": faces, "replace": True})
                set_status(props, "Tag %s: %d face(s), %d vertices"
                           % (result["tag"], result["face_count"], result["vertex_count"]))
            else:
                with object_mode():
                    if obj.vertex_groups.get(group_name) is None:
                        obj.vertex_groups.new(name=group_name)
                set_status(props, "Created empty tag %s (select faces, then Assign)"
                           % tag_display_name(group_name))
            props.new_tag_name = ""
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_assign_tag(_RigForgeOperator):
    bl_idname = "forge.rf_assign_tag"
    bl_label = "Assign From Selection"
    bl_description = "Assign the selected faces to this tag"

    tag: StringProperty(name="Tag", default="")
    replace: BoolProperty(
        name="Replace",
        description="Clear the tag first instead of adding to it",
        default=False,
    )

    def execute(self, context):
        def work(obj, props):
            name = (self.tag or props.new_tag_name or "").strip()
            if not name:
                return self.fail(props, "No tag named; type one or use a tag row's button.")
            result = cmd_rigforge_tag({"object": obj.name, "tag": name,
                                       "use_selection": True, "replace": self.replace})
            set_status(props, "%s %s: %d face(s), %d vertices"
                       % ("Replaced" if self.replace else "Assigned to",
                          result["tag"], result["face_count"], result["vertex_count"]))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_select_tag(_RigForgeOperator):
    bl_idname = "forge.rf_select_tag"
    bl_label = "Select Tag"
    bl_description = "Select this tag's geometry in Edit Mode"

    tag: StringProperty(name="Tag", default="")

    def execute(self, context):
        def work(obj, props):
            group = find_tag_group(obj, self.tag)
            with active_only(obj):
                previous_mode = obj.mode
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except RuntimeError as exc:
                    return self.fail(props, "Could not enter Edit Mode: %s" % exc)
                try:
                    obj.vertex_groups.active_index = group.index
                    bpy.ops.mesh.select_all(action="DESELECT")
                    bpy.ops.object.vertex_group_select()
                finally:
                    if previous_mode == "OBJECT":
                        try:
                            bpy.ops.object.mode_set(mode="EDIT")
                        except RuntimeError:
                            pass
            set_status(props, "Selected %s" % tag_display_name(group.name))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_remove_tag(_RigForgeOperator):
    bl_idname = "forge.rf_remove_tag"
    bl_label = "Remove Tag"
    bl_description = "Delete this tag entirely (the geometry is untouched)"

    tag: StringProperty(name="Tag", default="")

    def execute(self, context):
        def work(obj, props):
            result = cmd_rigforge_untag({"object": obj.name, "tag": self.tag})
            set_status(props, "Removed tag %s" % result["tag"])
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_sync(_RigForgeOperator):
    bl_idname = "forge.rf_sync"
    bl_label = "Read From Object"
    bl_description = "Load the archetype, motion notes and manifest path stored on the active object"

    def execute(self, context):
        def work(obj, props):
            pull_meta(obj, props)
            set_status(props, "Read %s (%d tag(s))" % (obj.name, len(tag_groups(obj))))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_manifest_save(_RigForgeOperator):
    bl_idname = "forge.rf_manifest_save"
    bl_label = "Save Manifest"
    bl_description = "Write character.json: tags, archetype and motion notes"

    def execute(self, context):
        def work(obj, props):
            _push_meta(obj, props)
            payload = {"object": obj.name, "action": "save"}
            if props.manifest_path.strip():
                payload["path"] = props.manifest_path
            result = cmd_rigforge_manifest(payload)
            props.manifest_path = result["path"]
            set_status(props, "Saved %s (%d tag(s))"
                       % (os.path.basename(result["path"]), len(result["manifest"]["tags"])))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_manifest_load(_RigForgeOperator):
    bl_idname = "forge.rf_manifest_load"
    bl_label = "Load Manifest"
    bl_description = (
        "Read character.json onto this object: archetype, motion notes, and empty "
        "vertex groups for any tag it lists that the mesh does not have yet"
    )

    def execute(self, context):
        def work(obj, props):
            payload = {"object": obj.name, "action": "load"}
            if props.manifest_path.strip():
                payload["path"] = props.manifest_path
            result = cmd_rigforge_manifest(payload)
            pull_meta(obj, props)
            props.manifest_path = result["path"]
            created = result.get("created_tags") or []
            set_status(props, "Loaded %s (%d tag(s)%s)"
                       % (os.path.basename(result["path"]),
                          len(result["manifest"]["tags"]),
                          ", %d created" % len(created) if created else ""))
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_retopo(_RigForgeOperator):
    bl_idname = "forge.rf_retopo"
    bl_label = "Retopologise"
    bl_description = (
        "One click from sculpt to game mesh: voxel remesh, Quadriflow to the "
        "target, shrinkwrap back, transfer tags, optional bake and LODs"
    )

    def execute(self, context):
        def work(obj, props):
            payload = {
                "object": obj.name,
                "platform": props.platform,
                "lods": int(props.lods),
                "unwrap": bool(props.retopo_unwrap),
                "seams_from_tags": bool(props.uv_seams_from_tags),
                "margin": float(props.uv_margin),
                "angle_limit": float(props.uv_angle_limit),
                "bake_normals": bool(props.bake_normals),
                "bake_resolution": int(props.bake_resolution),
                "keep_original": True,
            }
            if props.target_faces > 0:
                payload["target_faces"] = int(props.target_faces)
            set_status(props, "Retopologising %s ..." % obj.name)
            result = cmd_rigforge_retopo(payload)
            counts = result["face_counts"]
            props.summary = "   ".join("%s %d" % (name, counts[name])
                                       for name in result["objects"])
            warnings = result.get("warnings") or []
            set_status(
                props,
                "Retopo: %s (%d faces, %d tag(s))%s"
                % (result["object"], counts[result["object"]], result["tags_transferred"],
                   "  -  " + warnings[0] if warnings else ""),
                error=bool(warnings),
            )
            return {"FINISHED"}

        return self.guarded(context, work)


class FORGE_OT_rf_auto_uv(_RigForgeOperator):
    bl_idname = "forge.rf_auto_uv"
    bl_label = "Auto UV"
    bl_description = "Seam the tag boundaries, unwrap and pack the islands"

    def execute(self, context):
        def work(obj, props):
            result = cmd_rigforge_auto_uv({
                "object": obj.name,
                "seams_from_tags": bool(props.uv_seams_from_tags),
                "margin": float(props.uv_margin),
                "angle_limit": float(props.uv_angle_limit),
            })
            props.summary = "%d island(s)   %.0f%% coverage   %d seam(s) from %s" % (
                result["islands"], result["uv_coverage"] * 100.0,
                result["seams"], result["seam_source"])
            set_status(props, "UV: %d island(s), %.0f%% coverage"
                       % (result["islands"], result["uv_coverage"] * 100.0))
            return {"FINISHED"}

        return self.guarded(context, work)


_CLASSES = (
    ForgeRigForgeProps,
    FORGE_OT_rf_new_tag,
    FORGE_OT_rf_assign_tag,
    FORGE_OT_rf_select_tag,
    FORGE_OT_rf_remove_tag,
    FORGE_OT_rf_sync,
    FORGE_OT_rf_manifest_save,
    FORGE_OT_rf_manifest_load,
    FORGE_OT_rf_retopo,
    FORGE_OT_rf_auto_uv,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_rigforge = bpy.props.PointerProperty(type=ForgeRigForgeProps)


def unregister():
    try:
        del bpy.types.Scene.forge_rigforge
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
