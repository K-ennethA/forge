"""Every command in the Forge Blender socket protocol.

All handlers run on Blender's main thread (the socket server queues them and a
``bpy.app.timers`` pump executes them), so touching ``bpy`` here is safe.

Conventions used throughout:

* Parameters are validated before any bpy call; bad input raises
  :class:`~..tools.registry.ForgeError` with a message that says what was wrong.
* Object-targeting commands take ``"object"`` (a name); omitted means the active
  object of the current view layer.
* Handlers force OBJECT mode, operate, and restore the previous mode, selection,
  active object and visibility flags.
* Nothing assumes a VIEW_3D area exists, so the same code paths work in
  ``blender --background``.
"""

import ast
import contextlib
import io
import math
import os

import bmesh
import bpy
from mathutils import Vector

from .registry import ForgeError, command

# Millimetres -> metres. The geometry service works in mm, Blender in m.
MM_TO_M = 0.001
#: The other direction, for handing a Blender mesh back to the service.
M_TO_MM = 1000.0

#: Above this the mesh is not worth streaming to the service: the request would
#: take longer than the artist's patience and the answer would be no different.
#: Said out loud with the fix attached rather than silently truncated.
MAX_MESH_FACES = 500000

_MISSING = object()


# ---------------------------------------------------------------------------
# parameter helpers
# ---------------------------------------------------------------------------

def _raw(params, key, default=_MISSING):
    value = params.get(key, _MISSING)
    if value is _MISSING or value is None:
        if default is _MISSING:
            raise ForgeError("Missing required parameter %r." % key)
        return default
    return value


def get_str(params, key, default=_MISSING, allow_empty=False):
    value = _raw(params, key, default)
    if value is default and not isinstance(value, str):
        return value
    if not isinstance(value, str):
        raise ForgeError("Parameter %r must be a string, got %s." % (key, type(value).__name__))
    if not allow_empty and not value.strip():
        if default is _MISSING:
            raise ForgeError("Parameter %r must not be empty." % key)
        return default
    return value


def get_float(params, key, default=_MISSING, minimum=None, maximum=None):
    value = _raw(params, key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r must be a number, got %r." % (key, value))
    value = float(value)
    if not math.isfinite(value):
        raise ForgeError("Parameter %r must be a finite number, got %r." % (key, value))
    if minimum is not None and value < minimum:
        raise ForgeError("Parameter %r must be >= %g (got %g)." % (key, minimum, value))
    if maximum is not None and value > maximum:
        raise ForgeError("Parameter %r must be <= %g (got %g)." % (key, maximum, value))
    return value


def get_int(params, key, default=_MISSING, minimum=None, maximum=None):
    value = _raw(params, key, default)
    if isinstance(value, bool):
        raise ForgeError("Parameter %r must be an integer, got a boolean." % key)
    if isinstance(value, float):
        if not value.is_integer():
            raise ForgeError("Parameter %r must be a whole number, got %r." % (key, value))
        value = int(value)
    if not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r must be an integer, got %r." % (key, value))
    if minimum is not None and value < minimum:
        raise ForgeError("Parameter %r must be >= %d (got %d)." % (key, minimum, value))
    if maximum is not None and value > maximum:
        raise ForgeError("Parameter %r must be <= %d (got %d)." % (key, maximum, value))
    return value


def get_bool(params, key, default=_MISSING):
    value = _raw(params, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    raise ForgeError("Parameter %r must be a boolean, got %r." % (key, value))


def get_choice(params, key, choices, default=_MISSING):
    """Case-insensitive mapping of a string parameter onto ``choices`` (a dict)."""
    value = _raw(params, key, default)
    if value is default and not isinstance(value, str):
        return value
    if not isinstance(value, str):
        raise ForgeError("Parameter %r must be a string, got %s." % (key, type(value).__name__))
    key_norm = value.strip().upper()
    if key_norm in choices:
        return choices[key_norm]
    raise ForgeError(
        "Parameter %r must be one of %s (got %r)."
        % (key, ", ".join(sorted(set(choices))), value)
    )


def resolve_path(path, make_parents=False, default_ext=None):
    """Absolute, normalised filesystem path. Handles ``//`` blend-relative,
    ``~``, ``%VAR%``/``$VAR``, spaces and mixed slashes (Windows-safe)."""
    if not isinstance(path, str) or not path.strip():
        raise ForgeError("A non-empty 'path' is required.")
    text = path.strip().strip('"')
    try:
        text = bpy.path.abspath(text)
    except Exception:  # noqa: BLE001 - bpy.path can complain about odd input
        pass
    text = os.path.expandvars(os.path.expanduser(text))
    text = os.path.abspath(os.path.normpath(text))
    if default_ext and not os.path.splitext(text)[1]:
        text += default_ext
    if make_parents:
        parent = os.path.dirname(text)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                raise ForgeError("Could not create output folder %r: %s" % (parent, exc))
    return text


# ---------------------------------------------------------------------------
# scene / object helpers
# ---------------------------------------------------------------------------

def get_scene():
    scene = getattr(bpy.context, "scene", None)
    if scene is None and bpy.data.scenes:
        scene = bpy.data.scenes[0]
    if scene is None:
        raise ForgeError("No scene available.")
    return scene


def get_view_layer():
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        return view_layer
    scene = get_scene()
    if scene.view_layers:
        return scene.view_layers[0]
    raise ForgeError("No view layer available.")


def get_active_object():
    try:
        return get_view_layer().objects.active
    except ForgeError:
        return None


def _object_list_hint(limit=25):
    names = [o.name for o in bpy.data.objects]
    if not names:
        return "the file contains no objects"
    shown = ", ".join(repr(n) for n in names[:limit])
    if len(names) > limit:
        shown += ", ... (%d total)" % len(names)
    return "available objects: " + shown


def find_object(name, mesh_only=False):
    if not isinstance(name, str) or not name.strip():
        raise ForgeError("Object name must be a non-empty string.")
    obj = bpy.data.objects.get(name.strip())
    if obj is None:
        raise ForgeError("No object named %r; %s." % (name, _object_list_hint()))
    if mesh_only and obj.type != "MESH":
        raise ForgeError("Object %r is a %s, this command needs a MESH." % (obj.name, obj.type))
    return obj


def resolve_object(params, key="object", mesh_only=False):
    """Object named by ``params[key]``, or the active object when omitted."""
    name = params.get(key)
    if isinstance(name, str) and name.strip():
        return find_object(name, mesh_only=mesh_only)
    obj = get_active_object()
    if obj is None:
        raise ForgeError(
            "No %r parameter was given and there is no active object; %s." % (key, _object_list_hint())
        )
    if mesh_only and obj.type != "MESH":
        raise ForgeError(
            "The active object %r is a %s, this command needs a MESH. Pass %r explicitly."
            % (obj.name, obj.type, key)
        )
    return obj


def require_in_view_layer(obj):
    view_layer = get_view_layer()
    if obj.name not in view_layer.objects:
        raise ForgeError(
            "Object %r is not linked into the active view layer (its collection may be "
            "excluded or it may live in another scene), so Blender operators cannot touch it."
            % obj.name
        )
    return view_layer


@contextlib.contextmanager
def object_mode():
    """Force OBJECT mode for the duration of the block, restoring the old mode."""
    previous = None
    obj = getattr(bpy.context, "object", None)
    if obj is not None:
        try:
            previous = obj.mode
        except (AttributeError, ReferenceError):
            previous = None
    if previous not in (None, "OBJECT"):
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            previous = None
    try:
        yield
    finally:
        if previous not in (None, "OBJECT"):
            try:
                if getattr(bpy.context, "object", None) is obj:
                    bpy.ops.object.mode_set(mode=previous)
            except (RuntimeError, ReferenceError):
                pass


@contextlib.contextmanager
def selection(objects, active=None):
    """Select exactly ``objects`` (making ``active`` active), then restore.

    Temporarily clears viewport-hide flags on the targets, because Blender
    operators skip hidden objects.
    """
    objects = [o for o in objects if o is not None]
    if not objects:
        raise ForgeError("No objects to operate on.")
    view_layer = None
    for obj in objects:
        view_layer = require_in_view_layer(obj)
    if active is None:
        active = objects[0]

    previous_active = view_layer.objects.active
    previous_selected = [o for o in view_layer.objects if o.select_get()]
    hidden_state = []
    try:
        for obj in previous_selected:
            try:
                obj.select_set(False)
            except (RuntimeError, ReferenceError):
                pass
        for obj in objects:
            hidden_state.append((obj, obj.hide_viewport, obj.hide_get()))
            obj.hide_viewport = False
            obj.hide_set(False)
            obj.select_set(True)
        view_layer.objects.active = active
        yield objects
    finally:
        for obj, hide_viewport, hide in hidden_state:
            try:
                obj.hide_viewport = hide_viewport
                obj.hide_set(hide)
                obj.select_set(False)
            except (RuntimeError, ReferenceError):
                pass
        for obj in previous_selected:
            try:
                obj.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        try:
            if previous_active is not None and previous_active.name in view_layer.objects:
                view_layer.objects.active = previous_active
        except (RuntimeError, ReferenceError):
            pass


def active_only(obj):
    return selection([obj], obj)


def apply_modifier(obj, modifier):
    """Apply ``modifier`` on ``obj``, with a clear error when Blender refuses."""
    name = modifier.name
    with active_only(obj):
        try:
            status = bpy.ops.object.modifier_apply(
                **op_kwargs(
                    bpy.ops.object.modifier_apply,
                    {"modifier": name, "single_user": True},
                )
            )
        except RuntimeError as exc:
            # Leave the modifier in place so the caller can inspect it.
            raise ForgeError(
                "Could not apply modifier %r on %r: %s" % (name, obj.name, exc)
            )
        # Operators report refusals by returning CANCELLED, not by raising.
        if "FINISHED" not in status:
            raise ForgeError(
                "Blender refused to apply modifier %r on %r (returned %s); the object may "
                "be linked/library data or the modifier may be disabled."
                % (name, obj.name, ", ".join(sorted(status)) or "nothing")
            )


def mesh_stats(obj):
    data = obj.data
    if obj.type != "MESH" or data is None:
        return {"vertex_count": 0, "face_count": 0, "edge_count": 0}
    return {
        "vertex_count": len(data.vertices),
        "face_count": len(data.polygons),
        "edge_count": len(data.edges),
    }


def enum_items(rna_owner, prop_name):
    """Identifiers of an enum property on a live RNA struct (version-proofing)."""
    try:
        return set(rna_owner.bl_rna.properties[prop_name].enum_items.keys())
    except Exception:  # noqa: BLE001
        return set()


def _op_exists(module, name):
    try:
        return name in dir(module)
    except Exception:  # noqa: BLE001
        return False


def op_kwargs(operator, kwargs):
    """Drop keywords this Blender build's operator does not define.

    Operator signatures drift between Blender releases; asking the RNA which
    properties exist keeps a renamed or removed option from turning into a hard
    failure of an otherwise fine command.
    """
    try:
        allowed = set(operator.get_rna_type().properties.keys())
    except Exception:  # noqa: BLE001
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in allowed}


# ---------------------------------------------------------------------------
# ping / scene info
# ---------------------------------------------------------------------------

@command("ping")
def cmd_ping(params):
    return {
        "pong": True,
        "blender_version": bpy.app.version_string,
        "blender_version_tuple": list(bpy.app.version),
        "background": bool(bpy.app.background),
    }


@command("get_scene_info")
def cmd_get_scene_info(params):
    # ``dimensions`` comes from the evaluated object, so make sure any pending
    # depsgraph change (a mesh swapped by load_mesh, an edit made through
    # execute_python) is evaluated before we report sizes.
    refresh_view_layer()
    scene = get_scene()
    try:
        layer_objects = get_view_layer().objects
    except ForgeError:
        layer_objects = {}
    objects = []
    for obj in scene.objects:
        data = obj.data
        vertex_count = 0
        face_count = 0
        if obj.type == "MESH" and data is not None:
            vertex_count = len(data.vertices)
            face_count = len(data.polygons)
        elif hasattr(data, "vertices"):
            try:
                vertex_count = len(data.vertices)
            except (TypeError, AttributeError):
                vertex_count = 0
        objects.append(
            {
                "name": obj.name,
                "type": obj.type,
                "location": [float(v) for v in obj.location],
                "dimensions": [float(v) for v in obj.dimensions],
                "vertex_count": vertex_count,
                "face_count": face_count,
                "modifiers": [m.name for m in obj.modifiers],
                "visible": bool(obj.visible_get()) if obj.name in layer_objects else False,
            }
        )
    active = get_active_object()
    return {
        "objects": objects,
        "active": active.name if active is not None else None,
        "scene": scene.name,
        "unit_scale": float(scene.unit_settings.scale_length),
    }


# ---------------------------------------------------------------------------
# execute_python
# ---------------------------------------------------------------------------

_EXEC_NAMESPACE = {}


def _fresh_namespace():
    import bmesh as _bmesh
    import mathutils as _mathutils

    return {
        "__name__": "forge_exec",
        "__builtins__": __builtins__,
        "bpy": bpy,
        "bmesh": _bmesh,
        "mathutils": _mathutils,
        "math": math,
        "Vector": Vector,
    }


@command("execute_python")
def cmd_execute_python(params):
    code = get_str(params, "code")
    reset = get_bool(params, "reset", False)

    global _EXEC_NAMESPACE
    if reset or not _EXEC_NAMESPACE:
        _EXEC_NAMESPACE = _fresh_namespace()
    namespace = _EXEC_NAMESPACE

    try:
        tree = ast.parse(code, filename="<forge>", mode="exec")
    except SyntaxError as exc:
        raise ForgeError("Syntax error in code (line %s): %s" % (exc.lineno, exc.msg))

    tail = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tail = tree.body.pop()

    buffer = io.StringIO()
    value = None
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            if tree.body:
                exec(compile(tree, "<forge>", "exec"), namespace)  # noqa: S102
            if tail is not None:
                expression = ast.Expression(body=tail.value)
                ast.fix_missing_locations(expression)
                value = eval(compile(expression, "<forge>", "eval"), namespace)  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        import traceback as _traceback

        raise ForgeError(
            "%s: %s\n%s\n--- captured output ---\n%s"
            % (type(exc).__name__, exc, _traceback.format_exc(), buffer.getvalue())
        )

    try:
        rendered = None if value is None else repr(value)
    except Exception as exc:  # noqa: BLE001
        rendered = "<unrepresentable %s: %s>" % (type(value).__name__, exc)

    return {"output": buffer.getvalue(), "result": rendered}


# ---------------------------------------------------------------------------
# symmetrize / mirror
# ---------------------------------------------------------------------------

# Contract direction -> (bmesh.ops enum candidates, bpy.ops.mesh.symmetrize enum).
# The named side is the SOURCE: the half that survives and is copied across.
#
# bmesh.ops.symmetrize spells its enum differently from the operator: on 4.x/5.0
# the accepted identifiers are exactly ('-X', '-Y', '-Z', 'X', 'Y', 'Z') -- the
# positive directions have NO leading '+'. Each entry lists candidates in
# preference order so an older or newer spelling still resolves.
_SYMMETRIZE_DIRECTIONS = {
    "+X": (("X", "+X", "POSITIVE_X"), "POSITIVE_X"),
    "-X": (("-X", "NEGATIVE_X"), "NEGATIVE_X"),
    "+Y": (("Y", "+Y", "POSITIVE_Y"), "POSITIVE_Y"),
    "-Y": (("-Y", "NEGATIVE_Y"), "NEGATIVE_Y"),
    "+Z": (("Z", "+Z", "POSITIVE_Z"), "POSITIVE_Z"),
    "-Z": (("-Z", "NEGATIVE_Z"), "NEGATIVE_Z"),
}
# Aliases so callers can also send Blender's own identifiers or a bare axis.
for _axis in ("X", "Y", "Z"):
    _SYMMETRIZE_DIRECTIONS[_axis] = _SYMMETRIZE_DIRECTIONS["+" + _axis]
    _SYMMETRIZE_DIRECTIONS["POSITIVE_" + _axis] = _SYMMETRIZE_DIRECTIONS["+" + _axis]
    _SYMMETRIZE_DIRECTIONS["NEGATIVE_" + _axis] = _SYMMETRIZE_DIRECTIONS["-" + _axis]
del _axis


def _symmetrize_with_ops(obj, op_direction, threshold):
    with active_only(obj):
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except RuntimeError as exc:
            raise ForgeError("Could not enter Edit Mode on %r to symmetrize: %s" % (obj.name, exc))
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            status = bpy.ops.mesh.symmetrize(
                **op_kwargs(
                    bpy.ops.mesh.symmetrize,
                    {"direction": op_direction, "threshold": threshold},
                )
            )
        finally:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
    if "FINISHED" not in status:
        raise ForgeError(
            "Symmetrize was refused by Blender on %r (direction %s, returned %s)."
            % (obj.name, op_direction, ", ".join(sorted(status)) or "nothing")
        )


@command("symmetrize")
def cmd_symmetrize(params):
    obj = resolve_object(params, mesh_only=True)
    bm_candidates, op_direction = get_choice(
        params, "direction", _SYMMETRIZE_DIRECTIONS, (("-X", "NEGATIVE_X"), "NEGATIVE_X")
    )
    threshold = get_float(params, "threshold", 0.0001, minimum=0.0)

    with object_mode():
        mesh = obj.data
        used = None
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            geometry = list(bm.verts) + list(bm.edges) + list(bm.faces)
            for candidate in bm_candidates:
                try:
                    bmesh.ops.symmetrize(bm, input=geometry, direction=candidate, dist=threshold)
                except (TypeError, ValueError):
                    # Not this build's spelling for the enum - try the next one.
                    continue
                used = "bmesh:" + candidate
                bm.to_mesh(mesh)
                mesh.update()
                break
        finally:
            bm.free()
        if used is None:
            # No bmesh spelling matched on this build - fall back to the operator.
            _symmetrize_with_ops(obj, op_direction, threshold)
            used = "operator:" + op_direction

    stats = mesh_stats(obj)
    stats["object"] = obj.name
    stats["direction"] = op_direction
    stats["method"] = used
    return stats


@command("mirror")
def cmd_mirror(params):
    obj = resolve_object(params, mesh_only=True)
    axis = get_choice(params, "axis", {"X": 0, "Y": 1, "Z": 2}, 0)
    use_clip = get_bool(params, "use_clip", True)
    do_apply = get_bool(params, "apply", False)
    bisect = get_bool(params, "bisect", False)
    flip = get_bool(params, "flip", False)
    merge_threshold = get_float(params, "merge_threshold", 0.001, minimum=0.0)

    mirror_object = None
    if isinstance(params.get("mirror_object"), str) and params["mirror_object"].strip():
        mirror_object = find_object(params["mirror_object"])

    with object_mode():
        modifier = obj.modifiers.new(name="Forge Mirror", type="MIRROR")
        modifier.use_axis = tuple(i == axis for i in range(3))
        modifier.use_clip = use_clip
        modifier.merge_threshold = merge_threshold
        if bisect:
            modifier.use_bisect_axis = tuple(i == axis for i in range(3))
        if flip:
            modifier.use_bisect_flip_axis = tuple(i == axis for i in range(3))
        if mirror_object is not None:
            modifier.mirror_object = mirror_object
        name = modifier.name
        if do_apply:
            apply_modifier(obj, modifier)

    return {
        "object": obj.name,
        "modifier": None if do_apply else name,
        "applied": do_apply,
        "axis": "XYZ"[axis],
    }


# ---------------------------------------------------------------------------
# remesh / decimate
# ---------------------------------------------------------------------------

def _voxel_remesh(obj, voxel_size, adaptivity):
    mesh = obj.data
    try:
        mesh.remesh_voxel_size = voxel_size
        mesh.remesh_voxel_adaptivity = adaptivity
    except AttributeError:
        pass
    status = set()
    try:
        with active_only(obj):
            status = bpy.ops.object.voxel_remesh()
    except RuntimeError:
        status = set()
    if "FINISHED" in status:
        return "operator"
    # The operator either raised or returned CANCELLED - do it with a modifier.
    modifier = obj.modifiers.new(name="Forge Remesh", type="REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = voxel_size
    modifier.adaptivity = adaptivity
    apply_modifier(obj, modifier)
    return "modifier"


def _mesh_health(obj):
    """(non-manifold edge count, loose vertex count, face count) for ``obj``.

    ``edge.is_manifold`` is False for boundary edges (one face) as well as for
    edges shared by three or more faces, which is exactly the set Quadriflow
    cannot cope with.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bad_edges = sum(1 for edge in bm.edges if not edge.is_manifold)
        loose_verts = sum(1 for vert in bm.verts if not vert.link_faces)
        return bad_edges, loose_verts, len(bm.faces)
    finally:
        bm.free()


def _quad_remesh(obj, target_faces, use_symmetry, preserve_sharp, preserve_boundary, seed):
    bad_edges, loose_verts, face_count = _mesh_health(obj)
    if face_count == 0:
        raise ForgeError(
            "Quadriflow needs a surface: %r has no faces." % obj.name
        )
    if bad_edges or loose_verts:
        raise ForgeError(
            "Quadriflow remesh cannot run on %r: the mesh is not manifold "
            "(%d non-manifold or boundary edge(s), %d loose vertex/vertices). "
            "Quadriflow needs a watertight mesh with consistent normals - run a "
            "voxel remesh first, or fix the holes, then try again."
            % (obj.name, bad_edges, loose_verts)
        )

    kwargs = {
        "mode": "FACES",
        "target_faces": max(4, int(target_faces)),
        "use_mesh_symmetry": use_symmetry,
        "use_preserve_sharp": preserve_sharp,
        "use_preserve_boundary": preserve_boundary,
        "smooth_normals": False,
        "seed": seed,
    }
    with active_only(obj):
        try:
            try:
                status = bpy.ops.object.quadriflow_remesh(
                    **op_kwargs(bpy.ops.object.quadriflow_remesh, kwargs)
                )
            except TypeError:
                # Older/newer builds may not expose every keyword.
                status = bpy.ops.object.quadriflow_remesh(
                    mode="FACES", target_faces=kwargs["target_faces"]
                )
        except RuntimeError as exc:
            raise ForgeError(
                "Quadriflow remesh failed on %r: %s. Quadriflow needs a manifold, "
                "non-degenerate mesh - try a voxel remesh first." % (obj.name, exc)
            )
    # Quadriflow reports a bad input by warning and returning CANCELLED rather
    # than by raising, so the return value is the only reliable signal.
    if "FINISHED" not in status:
        raise ForgeError(
            "Quadriflow remesh was cancelled on %r (returned %s). Quadriflow needs a "
            "manifold, non-degenerate mesh with consistent face normals - try a voxel "
            "remesh first." % (obj.name, ", ".join(sorted(status)) or "nothing")
        )


@command("remesh")
def cmd_remesh(params):
    obj = resolve_object(params, mesh_only=True)
    mode = get_choice(params, "mode", {"VOXEL": "voxel", "QUAD": "quad", "QUADRIFLOW": "quad"}, "voxel")

    with object_mode():
        if mode == "voxel":
            voxel_size = get_float(params, "voxel_size", 0.01, minimum=1e-6)
            adaptivity = get_float(params, "adaptivity", 0.0, minimum=0.0)
            how = _voxel_remesh(obj, voxel_size, adaptivity)
        else:
            target_faces = get_int(params, "target_faces", 5000, minimum=4)
            _quad_remesh(
                obj,
                target_faces,
                get_bool(params, "use_symmetry", False),
                get_bool(params, "preserve_sharp", False),
                get_bool(params, "preserve_boundary", False),
                get_int(params, "seed", 0, minimum=0),
            )
            how = "quadriflow"

    result = mesh_stats(obj)
    result["object"] = obj.name
    result["mode"] = mode
    result["method"] = how
    return result


@command("decimate")
def cmd_decimate(params):
    obj = resolve_object(params, mesh_only=True)
    ratio = get_float(params, "ratio", 0.5, minimum=0.0, maximum=1.0)
    if ratio <= 0.0:
        raise ForgeError("Parameter 'ratio' must be greater than 0.")

    with object_mode():
        modifier = obj.modifiers.new(name="Forge Decimate", type="DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        modifier.use_collapse_triangulate = get_bool(params, "triangulate", False)
        apply_modifier(obj, modifier)

    result = mesh_stats(obj)
    result["object"] = obj.name
    result["ratio"] = ratio
    return result


# ---------------------------------------------------------------------------
# shading
# ---------------------------------------------------------------------------

def _set_polygon_smooth(mesh, smooth):
    count = len(mesh.polygons)
    if count:
        mesh.polygons.foreach_set("use_smooth", [smooth] * count)
    mesh.update()


def _remove_auto_smooth_modifiers(obj):
    """Drop Blender 4.1+ "Smooth by Angle" geometry-node modifiers.

    ``shade`` sets ``polygons.use_smooth`` directly instead of going through
    ``bpy.ops.object.shade_smooth``/``shade_flat``, so nothing else removes the
    modifier those operators manage.  Without this, ``shade flat`` after
    ``shade auto`` would leave the object visibly auto-smoothed, and repeated
    ``shade auto`` calls would stack modifiers.
    """
    removed = 0
    for modifier in list(obj.modifiers):
        if getattr(modifier, "type", "") != "NODES":
            continue
        group = getattr(modifier, "node_group", None)
        label = (getattr(group, "name", "") or modifier.name or "")
        if "smooth by angle" in label.lower():
            try:
                obj.modifiers.remove(modifier)
                removed += 1
            except (RuntimeError, ReferenceError):
                pass
    return removed


def _shade_auto(obj, angle_degrees):
    angle = math.radians(angle_degrees)
    mesh = obj.data
    _set_polygon_smooth(mesh, True)
    # Start from a clean slate so repeated calls do not stack modifiers.
    _remove_auto_smooth_modifiers(obj)
    # Blender 4.1+ : operator adds a "Smooth by Angle" node group modifier.
    if _op_exists(bpy.ops.object, "shade_auto_smooth"):
        operator = bpy.ops.object.shade_auto_smooth
        kwargs = op_kwargs(operator, {"angle": angle, "use_auto_smooth": True})
        with active_only(obj):
            for attempt in (kwargs, {}):
                try:
                    status = operator(**attempt)
                except (TypeError, RuntimeError):
                    continue
                if "FINISHED" in status:
                    return (
                        "shade_auto_smooth(%s)" % ", ".join(sorted(attempt))
                        if attempt
                        else "shade_auto_smooth(no-args)"
                    )
    # Blender <= 4.0 : mesh level auto smooth.
    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle
        return "mesh.use_auto_smooth"
    raise ForgeError(
        "This Blender build offers neither a working bpy.ops.object.shade_auto_smooth "
        "nor mesh.use_auto_smooth; use mode 'smooth' or 'flat' instead."
    )


@command("shade")
def cmd_shade(params):
    obj = resolve_object(params, mesh_only=True)
    mode = get_choice(params, "mode", {"SMOOTH": "smooth", "FLAT": "flat", "AUTO": "auto"}, "smooth")
    angle = get_float(params, "angle", 30.0, minimum=0.0, maximum=180.0)

    removed = 0
    with object_mode():
        if mode in ("smooth", "flat"):
            removed = _remove_auto_smooth_modifiers(obj)
            _set_polygon_smooth(obj.data, mode == "smooth")
            method = "polygons.use_smooth"
        else:
            method = _shade_auto(obj, angle)

    return {
        "object": obj.name,
        "mode": mode,
        "angle": angle,
        "method": method,
        "auto_smooth_modifiers_removed": removed,
    }


# ---------------------------------------------------------------------------
# transforms / origin
# ---------------------------------------------------------------------------

@command("apply_transforms")
def cmd_apply_transforms(params):
    obj = resolve_object(params)
    has_any = any(k in params for k in ("location", "rotation", "scale"))
    location = get_bool(params, "location", True if not has_any else False)
    rotation = get_bool(params, "rotation", True if not has_any else False)
    scale = get_bool(params, "scale", True if not has_any else False)
    if not (location or rotation or scale):
        raise ForgeError("Nothing to apply: set at least one of location/rotation/scale to true.")

    if obj.data is not None and getattr(obj.data, "users", 1) > 1:
        raise ForgeError(
            "Object %r shares its mesh data with %d other objects; applying transforms "
            "would change them too. Make it single-user first."
            % (obj.name, obj.data.users - 1)
        )

    with object_mode(), active_only(obj):
        try:
            bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)
        except RuntimeError as exc:
            raise ForgeError("Could not apply transforms on %r: %s" % (obj.name, exc))

    return {
        "object": obj.name,
        "location": location,
        "rotation": rotation,
        "scale": scale,
        "matrix_world": [list(row) for row in obj.matrix_world],
    }


def _world_bounds(obj):
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    if not corners:
        raise ForgeError("Object %r has no bounding box." % obj.name)
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


@command("set_origin")
def cmd_set_origin(params):
    obj = resolve_object(params)
    kind = get_choice(
        params,
        "type",
        {
            "GEOMETRY": "geometry",
            "ORIGIN_GEOMETRY": "geometry",
            "BOTTOM": "bottom",
            "CURSOR": "cursor",
            "ORIGIN_CURSOR": "cursor",
        },
        "geometry",
    )
    center = get_choice(params, "center", {"MEDIAN": "MEDIAN", "BOUNDS": "BOUNDS"}, "MEDIAN")
    scene = get_scene()

    with object_mode():
        if kind == "geometry":
            with active_only(obj):
                bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center=center)
        elif kind == "cursor":
            with active_only(obj):
                bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        else:
            low, high = _world_bounds(obj)
            target = Vector(((low[0] + high[0]) * 0.5, (low[1] + high[1]) * 0.5, low[2]))
            cursor = scene.cursor
            saved_location = cursor.location.copy()
            saved_rotation = tuple(cursor.rotation_euler)
            try:
                cursor.location = target
                with active_only(obj):
                    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
            finally:
                cursor.location = saved_location
                cursor.rotation_euler = saved_rotation

    return {"object": obj.name, "type": kind, "location": [float(v) for v in obj.location]}


# ---------------------------------------------------------------------------
# boolean / cleanup
# ---------------------------------------------------------------------------

@command("boolean")
def cmd_boolean(params):
    obj = resolve_object(params, mesh_only=True)
    operand_name = get_str(params, "operand")
    operand = find_object(operand_name, mesh_only=True)
    if operand is obj:
        raise ForgeError("The operand must be a different object from the target.")
    operation = get_choice(
        params,
        "operation",
        {"UNION": "UNION", "DIFFERENCE": "DIFFERENCE", "INTERSECT": "INTERSECT"},
        "DIFFERENCE",
    )
    do_apply = get_bool(params, "apply", True)
    delete_operand = get_bool(params, "delete_operand", False)
    if delete_operand and not do_apply:
        raise ForgeError(
            "delete_operand=true requires apply=true; an unapplied Boolean modifier "
            "still needs its operand object."
        )

    with object_mode():
        modifier = obj.modifiers.new(name="Forge Boolean", type="BOOLEAN")
        modifier.object = operand
        modifier.operation = operation
        solver = params.get("solver")
        if isinstance(solver, str) and solver.strip():
            available = enum_items(modifier, "solver")
            want = solver.strip().upper()
            if want not in available:
                raise ForgeError(
                    "Boolean solver %r is not available on this Blender build (have: %s)."
                    % (solver, ", ".join(sorted(available)) or "unknown")
                )
            modifier.solver = want
        name = modifier.name
        if do_apply:
            apply_modifier(obj, modifier)
        if delete_operand:
            data = operand.data
            bpy.data.objects.remove(operand, do_unlink=True)
            if data is not None and data.users == 0:
                try:
                    bpy.data.meshes.remove(data)
                except (ReferenceError, RuntimeError):
                    pass

    result = mesh_stats(obj)
    result.update(
        {
            "object": obj.name,
            "operation": operation,
            "applied": do_apply,
            "modifier": None if do_apply else name,
            "operand_deleted": delete_operand,
        }
    )
    return result


@command("merge_by_distance")
def cmd_merge_by_distance(params):
    obj = resolve_object(params, mesh_only=True)
    distance = get_float(params, "distance", 0.0001, minimum=0.0)

    with object_mode():
        mesh = obj.data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            before = len(bm.verts)
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=distance)
            removed = before - len(bm.verts)
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()

    result = mesh_stats(obj)
    result["object"] = obj.name
    result["removed"] = removed
    result["distance"] = distance
    return result


@command("separate_loose")
def cmd_separate_loose(params):
    obj = resolve_object(params, mesh_only=True)
    original_name = obj.name

    with object_mode():
        before = set(bpy.data.objects.keys())
        with active_only(obj):
            try:
                bpy.ops.object.mode_set(mode="EDIT")
            except RuntimeError as exc:
                raise ForgeError("Could not enter Edit Mode on %r: %s" % (original_name, exc))
            try:
                bpy.ops.mesh.select_all(action="SELECT")
                try:
                    bpy.ops.mesh.separate(type="LOOSE")
                except RuntimeError as exc:
                    raise ForgeError("Separate by loose parts failed on %r: %s" % (original_name, exc))
            finally:
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except RuntimeError:
                    pass
        created = sorted(name for name in bpy.data.objects.keys() if name not in before)

    names = [original_name] + created
    return {"objects": names, "created": created, "count": len(names)}


# ---------------------------------------------------------------------------
# object bookkeeping
# ---------------------------------------------------------------------------

@command("select_object")
def cmd_select_object(params):
    name = params.get("name") or params.get("object")
    obj = find_object(name if isinstance(name, str) else "")
    view_layer = require_in_view_layer(obj)
    extend = get_bool(params, "extend", False)

    with object_mode():
        if not extend:
            for other in view_layer.objects:
                try:
                    other.select_set(False)
                except (RuntimeError, ReferenceError):
                    pass
        try:
            obj.select_set(True)
        except RuntimeError as exc:
            raise ForgeError("Could not select %r: %s" % (obj.name, exc))
        view_layer.objects.active = obj

    return {"name": obj.name, "selected": [o.name for o in view_layer.objects if o.select_get()]}


@command("rename_object")
def cmd_rename_object(params):
    name = params.get("name") or params.get("object")
    obj = find_object(name if isinstance(name, str) else "")
    new_name = get_str(params, "new_name")
    rename_data = get_bool(params, "rename_data", False)

    obj.name = new_name.strip()
    if rename_data and obj.data is not None:
        try:
            obj.data.name = obj.name
        except (AttributeError, RuntimeError):
            pass
    return {"name": obj.name, "requested": new_name.strip(), "renamed": obj.name != name}


@command("delete_object")
def cmd_delete_object(params):
    name = params.get("name") or params.get("object")
    obj = find_object(name if isinstance(name, str) else "")
    actual = obj.name
    data = obj.data
    purge = get_bool(params, "purge_data", True)

    with object_mode():
        bpy.data.objects.remove(obj, do_unlink=True)
        purged = False
        if purge and data is not None:
            try:
                if data.users == 0:
                    collection = getattr(bpy.data, _DATA_COLLECTIONS.get(type(data).__name__, ""), None)
                    if collection is not None:
                        collection.remove(data)
                        purged = True
            except (ReferenceError, RuntimeError, AttributeError, TypeError):
                purged = False

    return {"name": actual, "deleted": True, "data_purged": purged}


_DATA_COLLECTIONS = {
    "Mesh": "meshes",
    "Curve": "curves",
    "TextCurve": "curves",
    "SurfaceCurve": "curves",
    "MetaBall": "metaballs",
    "Armature": "armatures",
    "Lattice": "lattices",
    "Light": "lights",
    "Camera": "cameras",
}


# ---------------------------------------------------------------------------
# load_mesh (PartForge regeneration path)
# ---------------------------------------------------------------------------

def _validate_mesh_payload(vertices, faces, scale):
    if not isinstance(vertices, (list, tuple)):
        raise ForgeError("'vertices' must be a list of [x, y, z] triples.")
    if not isinstance(faces, (list, tuple)):
        raise ForgeError("'faces' must be a list of index lists.")

    clean_verts = []
    for index, vertex in enumerate(vertices):
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            raise ForgeError("vertices[%d] must be [x, y, z], got %r." % (index, vertex))
        try:
            x, y, z = (float(vertex[0]), float(vertex[1]), float(vertex[2]))
        except (TypeError, ValueError):
            raise ForgeError("vertices[%d] contains a non-numeric value: %r." % (index, vertex))
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise ForgeError("vertices[%d] contains a non-finite value: %r." % (index, vertex))
        clean_verts.append((x * scale, y * scale, z * scale))

    vertex_count = len(clean_verts)
    clean_faces = []
    skipped = 0
    for index, face in enumerate(faces):
        if not isinstance(face, (list, tuple)):
            raise ForgeError("faces[%d] must be a list of vertex indices, got %r." % (index, face))
        try:
            indices = [int(i) for i in face]
        except (TypeError, ValueError):
            raise ForgeError("faces[%d] contains a non-integer index: %r." % (index, face))
        if len(indices) < 3:
            skipped += 1
            continue
        for i in indices:
            if i < 0 or i >= vertex_count:
                raise ForgeError(
                    "faces[%d] references vertex %d but there are only %d vertices."
                    % (index, i, vertex_count)
                )
        if len(set(indices)) != len(indices):
            skipped += 1
            continue
        clean_faces.append(tuple(indices))

    return clean_verts, clean_faces, skipped


def _resolve_collection(name):
    scene = get_scene()
    if not isinstance(name, str) or not name.strip():
        return scene.collection
    name = name.strip()
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
    elif name not in {c.name for c in _iter_collections(scene.collection)}:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    return collection


def _iter_collections(root):
    yield root
    for child in root.children:
        for nested in _iter_collections(child):
            yield nested


def refresh_view_layer():
    """Re-evaluate the view layer so freshly linked objects are visible to it.

    ``collection.objects.link()`` only tags the depsgraph; until it is
    evaluated, the new object is absent from ``view_layer.objects``, so
    selection, ``hide_set`` and every operator-based command would refuse to
    touch an object that was created moments earlier in the same command.
    """
    try:
        get_view_layer().update()
    except (ForgeError, AttributeError, RuntimeError):
        pass


def build_mesh_object(name, vertices, faces, replace=True, collection=None, scale=MM_TO_M):
    """Create or replace a mesh object from raw (millimetre) geometry.

    When ``replace`` is true and an object of that name already exists, its mesh
    datablock is swapped for a freshly built one: the object, its transforms,
    parenting, modifiers, materials and custom properties all survive, which is
    what keeps the PartForge panel state alive across regenerations.
    """
    clean_verts, clean_faces, skipped = _validate_mesh_payload(vertices, faces, scale)

    existing = bpy.data.objects.get(name)
    replaced = False
    if replace and existing is not None and existing.type == "MESH":
        obj = existing
        old_mesh = obj.data
        mesh = bpy.data.meshes.new(name=name + "_forge_tmp")
        mesh.from_pydata(clean_verts, [], clean_faces)
        mesh.update()
        mesh.validate(verbose=False)
        if old_mesh is not None:
            for material in old_mesh.materials:
                mesh.materials.append(material)
            for key in list(old_mesh.keys()):
                if key.startswith("_"):
                    continue
                try:
                    mesh[key] = old_mesh[key]
                except (TypeError, ValueError, KeyError):
                    pass
        obj.data = mesh
        replaced = True
        if old_mesh is not None and old_mesh.users == 0:
            old_name = old_mesh.name
            try:
                bpy.data.meshes.remove(old_mesh)
                mesh.name = old_name
            except (ReferenceError, RuntimeError):
                pass
        else:
            mesh.name = name
    else:
        mesh = bpy.data.meshes.new(name=name)
        mesh.from_pydata(clean_verts, [], clean_faces)
        mesh.update()
        mesh.validate(verbose=False)
        obj = bpy.data.objects.new(name, mesh)
        target = _resolve_collection(collection)
        target.objects.link(obj)

    # Both branches need this, for two different reasons:
    #  * new object - until the view layer is re-evaluated it is absent from
    #    ``view_layer.objects``, so the select branch and the very next command
    #    would not find it;
    #  * replaced mesh - ``obj.data = mesh`` only tags the depsgraph.  Anything
    #    read from the evaluated object (``obj.dimensions``, ``bound_box``, and
    #    therefore ``get_scene_info``) keeps reporting the OLD mesh's size until
    #    it is evaluated, which in ``--background`` never happens on its own.
    refresh_view_layer()

    result = {
        "object": obj.name,
        "vertex_count": len(obj.data.vertices),
        "face_count": len(obj.data.polygons),
        "replaced": replaced,
        "skipped_faces": skipped,
    }
    return obj, result


def _plate_angle(plate):
    """Total spin about Z for a plate item, in radians.

    The geometry service reports the rotation in two halves — ``pre_rotate_deg``
    is the segment's own tightest-footprint orientation, ``rotate_deg`` the extra
    quarter turn the packer gave it — and says the total is their sum.
    """
    degrees = 0.0
    for key in ("pre_rotate_deg", "rotate_deg"):
        value = plate.get(key)
        if value is None:
            continue
        try:
            degrees += float(value)
        except (TypeError, ValueError):
            raise ForgeError("plate.%s must be a number, got %r." % (key, value))
    return math.radians(degrees)


def apply_plate_placement(obj, plate, scale=MM_TO_M):
    """Move an object to where ``/segment``'s plate packing put its segment.

    ``plate`` is one entry of the service's ``plate.items`` list, forwarded
    verbatim: ``{"position_mm": [x, y, z], "pre_rotate_deg": a, "rotate_deg": b}``.
    ``position_mm`` is where that segment's *rotated* bounding-box minimum corner
    goes, so the object is spun about Z first and then offset by however far the
    rotated mesh's minimum corner sits from the origin.  The mesh data itself is
    left in assembly coordinates; only the object transform changes, which is
    what keeps a later Regenerate/replace cheap.
    """
    if not isinstance(plate, dict):
        raise ForgeError("'plate' must be an object with 'position_mm'; got %s."
                         % type(plate).__name__)
    position = plate.get("position_mm")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise ForgeError("plate.position_mm must be [x, y, z] in millimetres.")
    try:
        target = [float(v) * scale for v in position]
    except (TypeError, ValueError):
        raise ForgeError("plate.position_mm must be three numbers, got %r." % (position,))

    angle = _plate_angle(plate)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    mesh = obj.data
    count = len(mesh.vertices) if mesh is not None else 0
    if count:
        flat = [0.0] * (count * 3)
        mesh.vertices.foreach_get("co", flat)
        low = [float("inf"), float("inf"), float("inf")]
        for index in range(0, count * 3, 3):
            x = flat[index]
            y = flat[index + 1]
            z = flat[index + 2]
            rx = x * cos_a - y * sin_a
            ry = x * sin_a + y * cos_a
            if rx < low[0]:
                low[0] = rx
            if ry < low[1]:
                low[1] = ry
            if z < low[2]:
                low[2] = z
    else:
        low = [0.0, 0.0, 0.0]

    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.0, 0.0, angle)
    obj.location = (target[0] - low[0], target[1] - low[1], target[2] - low[2])
    return {
        "location": [round(v, 6) for v in obj.location],
        "rotation_z_deg": round(math.degrees(angle), 6),
    }


def _load_one_mesh(spec, replace, collection, scale, select=False):
    """Shared body of ``load_mesh`` and one entry of ``load_meshes``."""
    name = get_str(spec, "name", "ForgePart")
    vertices = spec.get("vertices")
    faces = spec.get("faces")
    if vertices is None:
        raise ForgeError("Missing required parameter 'vertices' for mesh %r." % name)
    if faces is None:
        faces = []

    obj, result = build_mesh_object(
        name, vertices, faces, replace=replace, collection=collection, scale=scale
    )

    plate = spec.get("plate")
    if plate is not None:
        result.update(apply_plate_placement(obj, plate, scale=scale))

    if select:
        result["selected"] = False
        try:
            view_layer = get_view_layer()
            if obj.name not in view_layer.objects:
                refresh_view_layer()
            if obj.name in view_layer.objects:
                obj.select_set(True)
                view_layer.objects.active = obj
                result["selected"] = True
        except (ForgeError, RuntimeError):
            pass
    return obj, result


@command("load_mesh")
def cmd_load_mesh(params):
    replace = get_bool(params, "replace", True)
    scale = get_float(params, "scale", MM_TO_M, minimum=0.0)
    collection = params.get("collection")

    with object_mode():
        _obj, result = _load_one_mesh(
            params,
            replace=replace,
            collection=collection,
            scale=scale,
            select=get_bool(params, "select", False),
        )

    result["scale"] = scale
    return result


def _plate_items(plate):
    """``/segment``'s ``plate`` object as ``{segment name: plate item}``."""
    if not isinstance(plate, dict):
        return {}
    out = {}
    for item in plate.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            out[item["name"]] = item
    return out


def _as_mesh_entries(meshes, plate):
    """Accept either load_mesh specs or ``/segment`` segments (Phase 6b, additive).

    A segment carries its geometry under ``mesh`` and its plate position in the
    reply's separate ``plate.items`` list, so chaining ``/segment`` into
    ``load_meshes`` used to mean a caller reshaping both by hand (which is
    exactly what the MCP tool ``partforge_load_segments`` does).  Accepting the
    service's own shape here is what lets a saved flow wire the two together
    with a plain reference and no code in between.  Entries that already look
    like ``load_mesh`` specs are passed through untouched.
    """
    placements = _plate_items(plate)
    out = []
    without_mesh = []
    for index, spec in enumerate(meshes):
        if not isinstance(spec, dict):
            raise ForgeError(
                "meshes[%d] must be an object, got %s." % (index, type(spec).__name__)
            )
        if spec.get("vertices") is not None:
            out.append(spec)
            continue
        mesh = spec.get("mesh")
        if not isinstance(mesh, dict) or mesh.get("vertices") is None:
            without_mesh.append(str(spec.get("name") or "meshes[%d]" % index))
            continue
        entry = {
            "name": spec.get("name", "ForgePart"),
            "vertices": mesh.get("vertices"),
            "faces": mesh.get("faces") or [],
        }
        if spec.get("plate") is not None:
            entry["plate"] = spec["plate"]
        elif entry["name"] in placements:
            entry["plate"] = placements[entry["name"]]
        if spec.get("collection") is not None:
            entry["collection"] = spec["collection"]
        out.append(entry)

    if without_mesh and not out:
        raise ForgeError(
            "None of these entries carry geometry (%s). If they came from "
            "/segment, ask for it with \"include_mesh\": true — the planning "
            "call returns sizes and joints but no meshes."
            % ", ".join(without_mesh[:6]))
    if without_mesh:
        raise ForgeError(
            "No geometry for: %s. Every entry needs 'vertices' (or a 'mesh' "
            "object from /segment with include_mesh true)."
            % ", ".join(without_mesh[:6]))
    return out


@command("load_meshes")
def cmd_load_meshes(params):
    """Load many meshes in one round trip (PartForge segmentation).

    Additive extension to the protocol: ``load_mesh`` for a list.  A cut part
    arrives as N segments plus any printed pins, and doing that as N round trips
    means N main-thread hops and N depsgraph refreshes for one logical action.

    ``meshes`` entries may also be ``/segment`` segments verbatim (geometry under
    ``mesh``), in which case a top-level ``plate`` — the service's own plate
    object — supplies each one's position by name.
    """
    meshes = params.get("meshes")
    if not isinstance(meshes, list):
        raise ForgeError("'meshes' must be a list of {name, vertices, faces} objects.")
    if not meshes:
        raise ForgeError("'meshes' is empty; nothing to load.")
    meshes = _as_mesh_entries(meshes, params.get("plate"))

    replace = get_bool(params, "replace", True)
    scale = get_float(params, "scale", MM_TO_M, minimum=0.0)
    collection = params.get("collection")
    select = get_bool(params, "select", False)

    loaded = []
    with object_mode():
        for index, spec in enumerate(meshes):
            if not isinstance(spec, dict):
                raise ForgeError(
                    "meshes[%d] must be an object, got %s." % (index, type(spec).__name__)
                )
            _obj, result = _load_one_mesh(
                spec,
                replace=replace,
                collection=spec.get("collection", collection),
                scale=scale,
                select=select and index == len(meshes) - 1,
            )
            loaded.append(result)

    return {
        "objects": loaded,
        "count": len(loaded),
        "scale": scale,
        "names": [entry["object"] for entry in loaded],
    }


# ---------------------------------------------------------------------------
# load_reference (Phase 6c — the artist's sketch in the viewport)
# ---------------------------------------------------------------------------

#: What Blender will open AND the assistant's Read tool will render.  Kept
#: identical to the bridge's and the MCP tool's list on purpose: a file the
#: panel accepted as an attachment is a file this command accepts as a
#: reference, with no "well, that one only works over there".
REFERENCE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

#: view -> (rotation about X, Y, Z in radians, offset direction).
#:
#: An IMAGE empty draws its picture in its own local XY plane facing local +Z,
#: so each entry is "spin local +Z until it points back at whoever is looking
#: from that orthographic view", with local +Y (the top of the picture) kept
#: upright:
#:
#: * ``front`` — Numpad 1 looks along +Y, so the picture faces -Y: +90° about X.
#: * ``side``  — Numpad 3 looks along -X, so the picture faces +X: the same
#:   +90° about X, then +90° about Z.
#: * ``top``   — Numpad 7 looks down, so the picture faces +Z: no rotation, the
#:   empty's own default.
#:
#: The offset pushes the plane a little way AWAY from the viewer so it never
#: shares a plane with geometry sitting on the origin (docs/architecture.md says
#: "front: +Y, side: -X, top: -Z" and that is exactly this).
REFERENCE_VIEWS = {
    "FRONT": ((math.pi / 2.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "SIDE": ((math.pi / 2.0, 0.0, math.pi / 2.0), (-1.0, 0.0, 0.0)),
    "TOP": ((0.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
}

#: Default height/width of a reference in millimetres, per the contract.
REFERENCE_SIZE_MM = 200.0
#: How far behind the origin the plane sits, in millimetres.
REFERENCE_OFFSET_MM = 1.0
#: Image empties take their opacity from the object colour's alpha.
REFERENCE_ALPHA = 0.5


def _load_reference_image(path):
    """``bpy.data.images.load(check_existing=True)`` with honest failures."""
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        raise ForgeError(
            "Blender could not open %r as an image: %s" % (path, exc)
        )
    size = list(getattr(image, "size", (0, 0)))
    width = int(size[0]) if size else 0
    height = int(size[1]) if len(size) > 1 else 0
    if width <= 0 or height <= 0:
        # A file with the right extension and the wrong insides: Blender loads
        # the datablock happily and only then has nothing in it.  Take the
        # empty datablock back out so a retry after fixing the file is clean.
        try:
            if image.users == 0:
                bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass
        raise ForgeError(
            "%r is not readable as an image (Blender opened it but found no "
            "pixels). Re-save it as a PNG or JPEG and try again." % path
        )
    return image, width, height


def _reference_empty(name, image):
    """The image empty called ``name``, reusing one that is already there.

    Loading the same reference twice must not leave ``Ref-front.001`` behind:
    the artist asked for *the* front reference, and a second copy stacked on the
    first is just a thing to delete.
    """
    existing = bpy.data.objects.get(name)
    if existing is not None:
        if existing.type != "EMPTY":
            raise ForgeError(
                "There is already a %s called %r in this file, so the reference "
                "cannot take that name. Pass a different 'name'."
                % (existing.type.lower(), name)
            )
        existing.empty_display_type = "IMAGE"
        existing.data = image
        return existing, True

    obj = None
    try:
        # Empties carry their image in `data`; passing it straight to new()
        # makes the object and the link in one step.
        obj = bpy.data.objects.new(name, image)
    except (TypeError, RuntimeError):
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        try:
            obj.data = image
        except (AttributeError, TypeError) as exc:
            raise ForgeError(
                "This Blender build would not attach an image to an empty (%s)." % exc
            )
    obj.empty_display_type = "IMAGE"
    return obj, False


@command("load_reference")
def cmd_load_reference(params):
    """Put a sketch or photo in the viewport to model against.

    Additive protocol extension (Phase 6c).  The image becomes an EMPTY of type
    IMAGE — not geometry, not a material — so it can never end up in an export,
    and the artist can move, scale or hide it like any other object.

    ``size_mm`` sets the picture's LONGER side in millimetres (Blender's
    ``empty_display_size`` is the plane's maximum dimension), and the shorter
    side follows the file's own pixel aspect, so the reference is never
    stretched.  Both are reported back in millimetres.
    """
    path = resolve_path(get_str(params, "path"))
    # Folder first: a directory is a directory whatever it is called, and being
    # told it has the wrong extension would send the artist hunting a typo.
    if os.path.isdir(path):
        raise ForgeError("%r is a folder, not an image file." % path)
    extension = os.path.splitext(path)[1].lower()
    if extension not in REFERENCE_EXTENSIONS:
        raise ForgeError(
            "%r is not an image Forge can load (%s). Save the reference as one "
            "of those and try again."
            % (os.path.basename(path) or path, ", ".join(REFERENCE_EXTENSIONS))
        )
    if not os.path.isfile(path):
        raise ForgeError("There is no file at %r." % path)

    view = get_choice(params, "view", {name: name for name in REFERENCE_VIEWS}, "FRONT")
    rotation, direction = REFERENCE_VIEWS[view]
    size_mm = get_float(params, "size_mm", REFERENCE_SIZE_MM, minimum=0.001)
    offset_mm = get_float(params, "offset_mm", REFERENCE_OFFSET_MM, minimum=0.0)
    name = get_str(params, "name", "Ref-%s" % view.lower()).strip()

    image, pixels_x, pixels_y = _load_reference_image(path)

    longest = float(max(pixels_x, pixels_y))
    width_mm = size_mm * (pixels_x / longest)
    height_mm = size_mm * (pixels_y / longest)

    with object_mode():
        obj, replaced = _reference_empty(name, image)
        obj.empty_display_size = size_mm * MM_TO_M
        obj.rotation_mode = "XYZ"
        obj.rotation_euler = rotation
        obj.location = tuple(component * offset_mm * MM_TO_M for component in direction)
        # Opacity for an image empty is the object colour's alpha, gated by
        # use_empty_image_alpha - half transparent so the model shows through.
        obj.use_empty_image_alpha = True
        obj.color = (obj.color[0], obj.color[1], obj.color[2], REFERENCE_ALPHA)
        # Visible in both projections: an artist who orbits away from the
        # orthographic view should not watch their reference vanish.
        obj.show_empty_image_orthographic = True
        obj.show_empty_image_perspective = True

        if obj.name not in {o.name for o in get_scene().objects}:
            _resolve_collection(params.get("collection")).objects.link(obj)
        refresh_view_layer()

    return {
        "object": obj.name,
        "width_mm": round(width_mm, 3),
        "height_mm": round(height_mm, 3),
        "view": view.lower(),
        "size_mm": size_mm,
        "path": path,
        "image": image.name,
        "pixels": [pixels_x, pixels_y],
        "replaced": replaced,
        "location": [round(v, 6) for v in obj.location],
        "rotation_deg": [round(math.degrees(v), 3) for v in obj.rotation_euler],
        "opacity": REFERENCE_ALPHA,
    }


# ---------------------------------------------------------------------------
# reading a mesh back OUT of Blender (Phase 6d: downloaded models)
# ---------------------------------------------------------------------------

def evaluated_mesh_mm(obj, apply_modifiers=True, scale=M_TO_MM):
    """``(vertices_mm, faces)`` for ``obj`` — world space, millimetres.

    The mirror image of :func:`build_mesh_object`, and the whole reason a
    downloaded STL can be print-checked: the geometry service speaks millimetre
    meshes, Blender holds metres, and this is the one place that conversion
    happens on the way out.

    Modifiers are applied by default (what you see is what is checked), the
    object's world matrix is baked in (a scaled or rotated object is measured as
    it sits, not as it was authored) and n-gons are passed through untouched —
    the service sews the shell itself, so triangulating here would only make the
    payload bigger.
    """
    if obj is None:
        raise ForgeError("No object to read a mesh from.")
    if obj.type not in _MESHABLE_TYPES:
        raise ForgeError(
            "%s is a %s, and only meshes (and curves/text that can become one) "
            "can be checked or cut up. Select the imported model itself."
            % (obj.name, obj.type.lower()))

    depsgraph = None
    if apply_modifiers:
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        except (AttributeError, RuntimeError):
            depsgraph = None
    source = obj.evaluated_get(depsgraph) if depsgraph is not None else obj

    try:
        mesh = source.to_mesh()
    except RuntimeError as exc:
        raise ForgeError("Could not read %s as a mesh: %s" % (obj.name, exc))
    if mesh is None:
        raise ForgeError("%s has no geometry to check." % obj.name)

    try:
        face_count = len(mesh.polygons)
        if face_count > MAX_MESH_FACES:
            raise ForgeError(
                "%s has %d faces, which is more than Forge will send over at "
                "once (%d). Simplify it first — the Decimate modifier, or ask "
                "the assistant to simplify it — then try again."
                % (obj.name, face_count, MAX_MESH_FACES))
        if not face_count:
            raise ForgeError(
                "%s has no faces. A print check needs a surface, not just "
                "points or edges." % obj.name)
        matrix = obj.matrix_world
        vertices = []
        for vertex in mesh.vertices:
            point = matrix @ vertex.co
            vertices.append([point.x * scale, point.y * scale, point.z * scale])
        faces = [list(polygon.vertices) for polygon in mesh.polygons]
    finally:
        try:
            source.to_mesh_clear()
        except (AttributeError, RuntimeError):
            pass

    return vertices, faces


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

_EXPORTABLE_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
#: What ``to_mesh()`` will actually give us geometry for.
_MESHABLE_TYPES = _EXPORTABLE_TYPES


@command("export_stl")
def cmd_export_stl(params):
    raw = params.get("objects")
    if isinstance(raw, str):
        raw = [raw]
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raise ForgeError("'objects' must be a list of object names.")

    objects = []
    if raw:
        for entry in raw:
            objects.append(find_object(entry if isinstance(entry, str) else ""))
    else:
        view_layer = get_view_layer()
        objects = [o for o in view_layer.objects if o.select_get() and o.type in _EXPORTABLE_TYPES]
        if not objects:
            active = get_active_object()
            if active is not None and active.type in _EXPORTABLE_TYPES:
                objects = [active]
    if not objects:
        raise ForgeError("No objects to export: pass 'objects' or select something first.")

    bad = [o.name for o in objects if o.type not in _EXPORTABLE_TYPES]
    if bad:
        raise ForgeError("These objects cannot be exported to STL: %s." % ", ".join(bad))

    path = resolve_path(get_str(params, "path"), make_parents=True, default_ext=".stl")
    if os.path.splitext(path)[1].lower() != ".stl":
        path += ".stl"
    # Blender works in metres, slicers read STL as millimetres: scale up by
    # default so a 100 mm part exports as 100 mm. Override with "scale".
    scale = get_float(params, "scale", 1000.0, minimum=1e-9)
    ascii_format = get_bool(params, "ascii", False)
    apply_modifiers = get_bool(params, "apply_modifiers", True)

    with object_mode(), selection(objects, objects[0]):
        exporter = None
        problems = []
        if _op_exists(bpy.ops.wm, "stl_export"):
            try:
                status = bpy.ops.wm.stl_export(
                    **op_kwargs(
                        bpy.ops.wm.stl_export,
                        {
                            "filepath": path,
                            "ascii_format": ascii_format,
                            "export_selected_objects": True,
                            "global_scale": scale,
                            "use_scene_unit": False,
                            "apply_modifiers": apply_modifiers,
                            # Never let the exporter try to raise an overwrite
                            # confirmation - there is no user at this end.
                            "check_existing": False,
                        },
                    )
                )
                if "FINISHED" in status:
                    exporter = "wm.stl_export"
                else:
                    problems.append(
                        "wm.stl_export returned %s" % (", ".join(sorted(status)) or "nothing")
                    )
            except (TypeError, RuntimeError) as exc:
                problems.append("wm.stl_export: %s" % exc)
        if exporter is None:
            if not _op_exists(bpy.ops, "export_mesh"):
                raise ForgeError(
                    "No STL exporter available: the legacy io_mesh_stl add-on is not enabled "
                    "and %s" % ("; ".join(problems) or "bpy.ops.wm.stl_export is missing.")
                )
            try:
                status = bpy.ops.export_mesh.stl(
                    **op_kwargs(
                        bpy.ops.export_mesh.stl,
                        {
                            "filepath": path,
                            "use_selection": True,
                            "ascii": ascii_format,
                            "global_scale": scale,
                            "use_scene_unit": False,
                            "use_mesh_modifiers": apply_modifiers,
                            "check_existing": False,
                        },
                    )
                )
                if "FINISHED" not in status:
                    raise RuntimeError("returned %s" % (", ".join(sorted(status)) or "nothing"))
                exporter = "export_mesh.stl"
            except (TypeError, RuntimeError, AttributeError) as exc:
                problems.append("export_mesh.stl: %s" % exc)
                raise ForgeError("STL export failed - %s" % "; ".join(problems))

    if not os.path.exists(path):
        raise ForgeError("STL export reported success but %r does not exist." % path)

    return {
        "path": path,
        "objects": [o.name for o in objects],
        "scale": scale,
        "exporter": exporter,
        "size_bytes": os.path.getsize(path),
    }
