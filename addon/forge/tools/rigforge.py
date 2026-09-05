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

#: Vertex groups with this prefix are semantic tags. Everything else on the
#: object (deform weights, masks) is left strictly alone.
TAG_PREFIX = "tag_"

ARCHETYPES = ("biped", "quadruped", "custom")

#: Target face counts per platform (plan section 4, stage 2).
PLATFORM_TARGETS = {"desktop": 15000, "mobile": 5000}

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


def _decimate(obj, ratio):
    modifier = obj.modifiers.new(name="Forge Decimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(1e-4, min(1.0, float(ratio)))
    apply_modifier(obj, modifier)


def _ensure_uv_layer(obj, name="UVMap"):
    mesh = obj.data
    if mesh.uv_layers:
        return mesh.uv_layers.active or mesh.uv_layers[0]
    return mesh.uv_layers.new(name=name)


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
    """
    report = {"ok": False, "resolution": int(resolution), "image": None, "path": None,
              "engine": None, "reason": "", "seconds": 0.0}
    scene = get_scene()
    started = time.monotonic()
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


@command("rigforge_retopo")
def cmd_rigforge_retopo(params):
    """Stage 2: a game mesh from the sculpt, tags and all.

    voxel remesh -> Quadriflow -> shrinkwrap back onto the sculpt -> tag
    transfer by proximity -> optional high->low normal bake -> optional
    decimated LODs.  The voxel pass is first for a reason: it is what makes a
    non-manifold sculpt (overlapping blobs, self-intersections, holes) safe for
    Quadriflow, which refuses anything that is not watertight.
    """
    obj = _require_mesh(params)
    if not len(obj.data.polygons):
        raise ForgeError("Object %r has no faces to retopologise." % obj.name)

    target_faces, platform, target_source = _resolve_target_faces(obj, params)
    lods = get_int(params, "lods", 0, minimum=0, maximum=8)
    do_bake = get_bool(params, "bake_normals", False)
    bake_resolution = get_int(params, "bake_resolution", 2048, minimum=16, maximum=8192)
    bake_path = params.get("bake_path")
    keep_original = get_bool(params, "keep_original", True)
    voxel_override = params.get("voxel_size")

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

        # --- 5. LODs
        lod_objects = []
        for level in range(1, lods + 1):
            ratio = 0.5 ** level
            lod_name = "%s_lod%d" % (obj.name, level)
            lod = _duplicate_object(retopo, lod_name, drop_groups=False)
            _decimate(lod, ratio)
            lod_objects.append(lod)
            stages.append({"stage": "lod", "level": level, "ratio": ratio,
                           "object": lod.name, "face_count": len(lod.data.polygons)})

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


@command("rigforge_auto_uv")
def cmd_rigforge_auto_uv(params):
    """Stage 3: seams from the tag boundaries, unwrap, pack."""
    obj = _require_mesh(params)
    if not len(obj.data.polygons):
        raise ForgeError("Object %r has no faces to unwrap." % obj.name)

    seams_from_tags = get_bool(params, "seams_from_tags", True)
    margin = get_float(params, "margin", 0.02, minimum=0.0, maximum=0.5)
    angle_limit = get_float(params, "angle_limit", 66.0, minimum=0.0, maximum=180.0)
    method = get_choice(
        params, "method",
        {"ANGLE_BASED": "ANGLE_BASED", "CONFORMAL": "CONFORMAL",
         "MINIMUM_STRETCH": "MINIMUM_STRETCH"},
        "ANGLE_BASED",
    )

    with object_mode():
        seam_report = mark_tag_seams(obj, angle_limit_deg=angle_limit,
                                     use_tags=seams_from_tags)
        unwrap_method, packed, notes = _unwrap_and_pack(obj, margin, angle_limit, method)
        islands, coverage, faces_with_area = _uv_report(obj)

    layer = obj.data.uv_layers.active or (obj.data.uv_layers[0] if obj.data.uv_layers else None)
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
        description="Extra decimated levels after the retopo mesh (0.5, 0.25, ...)",
        default=2,
        min=0,
        max=8,
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
