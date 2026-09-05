"""The Model box: a model the artist downloaded is a first-class part.

Everything else in Forge starts from a PARAMS script the geometry service can
build.  This module is for the other half of the artist's life: the STL off the
internet, the OBJ a friend sent, their own sculpt — geometry that already exists
and only needs the same two questions answered.

* **Will it print?** ``check_model`` reads the object's evaluated mesh, scales it
  to millimetres and posts it to the service's ``/check_mesh``.  The answers land
  in the very same Print Checks rows a parametric part fills in, because the
  artist should not have to learn that there are two kinds of check.
* **It is too big — cut it up.** ``segment_model`` posts the same mesh to
  ``/segment_mesh`` with a joint and a mode, and loads the pieces back into the
  viewport laid out on the plate, exactly as the PartForge Segment button does.

A mesh that is not watertight cannot be sewn into a solid, so the service
refuses it and says to repair it first.  That refusal is passed through word for
word and the box grows a one-click **Voxel Repair** button (a voxel remesh,
which is what closes those holes), so the fix is a button rather than a
tutorial.

Panel work is async through the house pattern (worker thread + timer); the two
socket commands are synchronous, because a socket command IS the main thread and
its caller is waiting on the reply.
"""

import os

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..prefs import service_url
from . import common, partforge
from .partforge import ServiceError, run_async, _tag_redraw
from .partforge import request_json as service_request
from .registry import ForgeError, command

#: What Blender can bring in without a third-party add-on.
IMPORT_FILTER = "*.stl;*.obj;*.ply"

#: Scale a file's numbers by this on the way in.  Print files are millimetres
#: essentially always; the option exists because OBJ has no unit convention at
#: all and an artist should be able to say so instead of scaling by hand.
IMPORT_UNITS = (
    ("MM", "Millimetres", "The file's numbers are millimetres (nearly always true for print files)"),
    ("M", "Metres", "The file's numbers are already metres"),
)

#: Marks the service's refusal to work on a mesh with holes in it.
_REPAIR_MARKERS = ("watertight", "repair", "not closed", "manifold")

#: Timeouts mirror the parametric side (service: /check 120 s, /segment 300 s).
CHECK_TIMEOUT = 150.0
SEGMENT_TIMEOUT = 330.0


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

class ForgeModelProps(PropertyGroup):
    """Scene state for the Model box."""

    import_path: StringProperty(
        name="Model file",
        description="An STL / OBJ / PLY file to bring into the scene",
        default="",
        subtype="FILE_PATH",
    )
    import_units: EnumProperty(
        name="File units",
        description="What one unit in the file means",
        items=IMPORT_UNITS,
        default="MM",
    )
    object_name: StringProperty(
        name="Model",
        description="The imported object the Check and Segment buttons work on",
        default="",
    )
    status: StringProperty(default="")
    status_is_error: BoolProperty(default=False)
    busy: BoolProperty(default=False)
    summary: StringProperty(default="")
    #: Set when the service said the mesh has holes: the panel then puts the
    #: Voxel Repair button where the artist is already looking.
    needs_repair: BoolProperty(default=False)
    voxel_size_mm: FloatProperty(
        name="Detail",
        description=(
            "Voxel size for the repair, in millimetres. Smaller keeps more "
            "detail and takes longer"
        ),
        default=1.0,
        min=0.05,
        max=50.0,
        precision=2,
    )
    face_count: IntProperty(default=0)


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_model", None)


def set_status(props, message, error=False):
    if props is None:
        return
    text = str(message or "").strip()
    props.status = text.splitlines()[0][:400] if text else ""
    props.status_is_error = bool(error)
    if error and text:
        print("[Forge/Model]", text)


def _alive(props):
    try:
        return props is not None and props.busy in (True, False)
    except (ReferenceError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# which object the box is pointed at
# ---------------------------------------------------------------------------

def target_object(props=None, name="", context=None):
    """The object to work on: an explicit name, the box's, or the active one."""
    context = context or bpy.context
    name = str(name or "").strip()
    if not name and props is not None:
        name = str(props.object_name or "").strip()
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ForgeError(
                "There is no object called %r in this file. Select the model in "
                "the viewport and try again." % name)
        return obj
    obj = common.get_active_object()
    if obj is None:
        raise ForgeError(
            "Nothing is selected. Click the imported model in the viewport "
            "first, so Forge knows which one you mean.")
    return obj


def looks_like_repair_problem(message):
    text = str(message or "").lower()
    return any(marker in text for marker in _REPAIR_MARKERS)


# ---------------------------------------------------------------------------
# the two service calls, as plain functions
# ---------------------------------------------------------------------------

def mesh_body(obj):
    """``({"mesh": ...}, counts)`` — the shared half of both requests."""
    vertices, faces = common.evaluated_mesh_mm(obj)
    body = {"mesh": {"vertices": vertices, "faces": faces}}
    counts = {"vertex_count": len(vertices), "face_count": len(faces),
              "scale": common.M_TO_MM}
    return body, counts


def check_body(obj, printer=None):
    body, counts = mesh_body(obj)
    if printer:
        body["printer"] = printer
    return body, counts


def segment_body(obj, joint=None, mode="auto", printer=None, include_mesh=True):
    body, counts = mesh_body(obj)
    body["joint"] = joint or {"type": "dovetail"}
    body["mode"] = mode if mode is not None else "auto"
    body["include_mesh"] = bool(include_mesh)
    if printer:
        body["printer"] = printer
    return body, counts


def post(endpoint, body, timeout):
    """POST to the geometry service, ``ServiceError`` -> ``ForgeError``.

    Kept deliberately liberal about the response: the service half of Phase 6d
    is being written alongside this one, and a field arriving late must cost a
    line of the report, never the whole call.
    """
    try:
        return service_request(service_url(endpoint), body, timeout=timeout)
    except ServiceError as exc:
        message = str(exc)
        if looks_like_repair_problem(message):
            raise ForgeError(
                "%s\n\nPress Voxel Repair in the Model box (or ask the "
                "assistant to repair it) and check again." % message)
        raise ForgeError(message)


def store_check_results(payload, obj_name=""):
    """Write a ``/check_mesh`` answer into the Print Checks rows. Main thread."""
    pf = partforge.get_props()
    if pf is None:
        return False
    partforge.store_checks(pf, payload)
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    if obj_name:
        # The Print Checks box normally says which script it checked; for an
        # imported model the object name is that answer.
        pf.check_summary = ("%s   %s" % (obj_name, pf.check_summary)).strip()
    if stats:
        pf.stats = partforge._format_stats(stats, {})
    return True


def summarize_check(payload, counts):
    overall = str(payload.get("overall") or "?").upper()
    rows = [row for row in (payload.get("checks") or []) if isinstance(row, dict)]
    failed = [str(row.get("name")) for row in rows if row.get("status") == "fail"]
    bits = ["Print checks: %s" % overall, "%d check(s)" % len(rows)]
    if failed:
        bits.append("failed: %s" % ", ".join(failed))
    bits.append("%s faces" % counts.get("face_count", "?"))
    return "   ".join(bits)


# ---------------------------------------------------------------------------
# socket commands (Phase 6d mirrors: check_model / segment_model)
# ---------------------------------------------------------------------------

def _printer_param(params):
    """An explicit ``printer`` object, else the add-on's printer preference."""
    printer = params.get("printer")
    if printer is not None:
        if not isinstance(printer, dict):
            raise ForgeError("'printer' must be a printer.json object.")
        return printer, "supplied"
    profile, source = partforge.load_printer()
    return profile, source


@command("check_model")
def cmd_check_model(params):
    """Print-check a mesh that already exists in the scene.

    params: ``object?`` (name; omitted = the active object), ``printer?``.
    The evaluated mesh goes to the service's ``/check_mesh`` in millimetres and
    the answer is written into the Print Checks panel as well as returned.
    """
    obj = target_object(props=get_props(), name=params.get("object"))
    printer, printer_source = _printer_param(params)
    body, counts = check_body(obj, printer)
    payload = post("/check_mesh", body, CHECK_TIMEOUT)

    store_check_results(payload, obj.name)
    props = get_props()
    if props is not None:
        props.object_name = obj.name
        props.face_count = int(counts.get("face_count") or 0)
        props.needs_repair = False
        props.summary = summarize_check(payload, counts)
        set_status(props, props.summary,
                   error=str(payload.get("overall") or "").lower() == "fail")
    _tag_redraw()

    result = {
        "object": obj.name,
        "overall": payload.get("overall"),
        "checks": payload.get("checks") or [],
        "mesh": counts,
        "printer_source": printer_source,
        "panel": True,
    }
    for key in ("printer", "stats", "params", "timings"):
        if payload.get(key) is not None:
            result[key] = payload[key]
    return result


@command("segment_model")
def cmd_segment_model(params):
    """Cut a mesh that already exists in the scene into printable pieces.

    params: ``object?``, ``printer?``, ``joint?`` (``{"type", "tolerance"?}``),
    ``mode?`` (``"auto"`` | ``{"radial": N}`` | ``{"planar": [z,...]}``),
    ``collection?``.  The pieces come back as objects positioned on the plate,
    the same as the PartForge Segment button.
    """
    obj = target_object(props=get_props(), name=params.get("object"))
    printer, printer_source = _printer_param(params)

    joint = params.get("joint")
    if joint is None:
        joint = {"type": "dovetail"}
    if not isinstance(joint, dict):
        raise ForgeError("'joint' must be an object like {\"type\": \"dovetail\"}.")

    mode = params.get("mode", "auto")
    if mode in (None, ""):
        mode = "auto"
    if not isinstance(mode, (str, dict, int, list)):
        raise ForgeError(
            "'mode' must be \"auto\", {\"radial\": N} or {\"planar\": [z, ...]}.")

    collection = params.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise ForgeError("'collection' must be a collection name.")

    body, counts = segment_body(obj, joint=joint, mode=mode, printer=printer)
    payload = post("/segment_mesh", body, SEGMENT_TIMEOUT)

    names = partforge.load_segment_objects(payload, collection=(collection or "").strip() or None)

    props = get_props()
    if props is not None:
        props.object_name = obj.name
        props.face_count = int(counts.get("face_count") or 0)
        props.needs_repair = False
        props.summary = partforge.format_segment_summary(payload, names)
        set_status(props, "Cut %s into %d piece(s)." % (obj.name, len(names)))
    pf = partforge.get_props()
    if pf is not None:
        pf.segment_summary = partforge.format_segment_summary(payload, names)
    _tag_redraw()

    segments = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        entry = {"name": segment.get("name"), "kind": segment.get("kind", "segment")}
        for key in ("stats", "orient_deg", "oriented_bbox_mm"):
            if segment.get(key) is not None:
                entry[key] = segment[key]
        segments.append(entry)

    result = {
        "object": obj.name,
        "objects": names,
        "count": len(names),
        "segments": segments,
        "mesh": counts,
        "printer_source": printer_source,
    }
    for key in ("mode", "joint", "plate", "cuts"):
        if payload.get(key) is not None:
            result[key] = payload[key]
    return result


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class FORGE_OT_model_import(Operator):
    """Bring a downloaded model into the scene, at the right size."""

    bl_idname = "forge.model_import"
    bl_label = "Import Model"
    bl_description = (
        "Open an STL / OBJ / PLY file you downloaded or exported, scaled so its "
        "millimetres are Forge's millimetres"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", default="")
    filter_glob: StringProperty(default=IMPORT_FILTER, options={"HIDDEN"})

    def invoke(self, context, event):
        props = get_props(context)
        stored = str(getattr(props, "import_path", "") or "").strip()
        if stored:
            self.filepath = bpy.path.abspath(stored)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = get_props(context)
        path = str(self.filepath or getattr(props, "import_path", "") or "").strip()
        if not path:
            set_status(props, "Pick a file first.", error=True)
            return {"CANCELLED"}
        path = common.resolve_path(path)
        if not os.path.isfile(path):
            set_status(props, "There is no file at %s." % path, error=True)
            return {"CANCELLED"}

        scale = 0.001 if getattr(props, "import_units", "MM") == "MM" else 1.0
        before = set(bpy.data.objects.keys())
        try:
            importer = import_file(path, scale)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        added = [name for name in bpy.data.objects.keys() if name not in before]
        if not added:
            set_status(props, "%s imported nothing — the file may be empty."
                       % os.path.basename(path), error=True)
            return {"CANCELLED"}

        obj = bpy.data.objects.get(added[-1])
        props.import_path = path
        props.object_name = obj.name if obj is not None else added[-1]
        props.needs_repair = False
        props.summary = ""
        size = ""
        if obj is not None:
            try:
                size = "%.0f x %.0f x %.0f mm" % tuple(
                    float(v) * common.M_TO_MM for v in obj.dimensions)
                props.face_count = len(obj.data.polygons) if obj.type == "MESH" else 0
            except (TypeError, ValueError, AttributeError):
                size = ""
        set_status(props,
                   "Imported %s (%s%s). Press Check imported model to see whether "
                   "it will print."
                   % (props.object_name, size + ", " if size else "",
                      "%d faces" % props.face_count if props.face_count else "no faces"))
        self.report({"INFO"}, "Imported %s with %s" % (props.object_name, importer))
        _tag_redraw()
        return {"FINISHED"}


def import_file(path, scale):
    """Import one mesh file with whichever importer this Blender has.

    Returns the operator name that worked; raises :class:`ForgeError` naming
    every attempt when none does.
    """
    extension = os.path.splitext(path)[1].lower()
    attempts = []
    if extension == ".stl":
        attempts = [(bpy.ops.wm, "stl_import",
                     {"filepath": path, "global_scale": scale, "use_scene_unit": False}),
                    (bpy.ops.import_mesh, "stl",
                     {"filepath": path, "global_scale": scale, "use_scene_unit": False})]
    elif extension == ".obj":
        attempts = [(bpy.ops.wm, "obj_import",
                     {"filepath": path, "global_scale": scale}),
                    (bpy.ops.import_scene, "obj",
                     {"filepath": path, "global_scale": scale})]
    elif extension == ".ply":
        attempts = [(bpy.ops.wm, "ply_import",
                     {"filepath": path, "global_scale": scale}),
                    (bpy.ops.import_mesh, "ply", {"filepath": path})]
    else:
        raise ForgeError(
            "Forge can open .stl, .obj and .ply files. %s is a %s file — convert "
            "it first, or ask the assistant how."
            % (os.path.basename(path), extension or "no-extension"))

    problems = []
    for module, name, kwargs in attempts:
        if not common._op_exists(module, name):
            continue
        operator = getattr(module, name)
        try:
            status = operator(**common.op_kwargs(operator, kwargs))
        except (TypeError, RuntimeError, AttributeError) as exc:
            problems.append("%s: %s" % (name, exc))
            continue
        if "FINISHED" in status:
            return name
        problems.append("%s returned %s" % (name, ", ".join(sorted(status)) or "nothing"))

    raise ForgeError(
        "Could not open %s — %s"
        % (os.path.basename(path),
           "; ".join(problems) or "this Blender has no importer for that format."))


class _ModelOperator(Operator):
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        props = get_props(context)
        return props is not None and not props.busy


class FORGE_OT_model_check(_ModelOperator):
    bl_idname = "forge.model_check"
    bl_label = "Check imported model"
    bl_description = (
        "Ask the shape service whether this model can be printed: bed fit, wall "
        "thickness, overhangs and whether it is watertight"
    )

    def execute(self, context):
        props = get_props(context)
        try:
            obj = target_object(props, context=context)
            printer, printer_source = partforge.load_printer()
            body, counts = check_body(obj, printer)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        name = obj.name
        props.object_name = name
        props.face_count = int(counts.get("face_count") or 0)
        props.busy = True
        props.needs_repair = False
        set_status(props, "Checking %s against %s ..." % (name, printer_source))

        def work():
            return post("/check_mesh", body, CHECK_TIMEOUT)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                props.needs_repair = looks_like_repair_problem(str(error))
                set_status(props, str(error), error=True)
                return
            store_check_results(value, name)
            props.summary = summarize_check(value, counts)
            watertight = _watertight_row(value)
            props.needs_repair = watertight is False
            set_status(props, props.summary,
                       error=str(value.get("overall") or "").lower() == "fail")

        run_async(work, done)
        return {"FINISHED"}


def _watertight_row(payload):
    """``True``/``False``/``None`` — did the watertight check pass?"""
    for row in (payload.get("checks") or []):
        if isinstance(row, dict) and str(row.get("name")) == "watertight":
            return str(row.get("status")) == "pass"
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    value = stats.get("watertight")
    return bool(value) if isinstance(value, bool) else None


class FORGE_OT_model_segment(_ModelOperator):
    bl_idname = "forge.model_segment"
    bl_label = "Segment imported model"
    bl_description = (
        "Cut this model into pieces that fit the bed, with joints, and lay them "
        "out in the viewport the way they will sit on the plate"
    )

    def execute(self, context):
        props = get_props(context)
        pf = partforge.get_props(context)
        try:
            obj = target_object(props, context=context)
            printer, printer_source = partforge.load_printer()
            mode = partforge.segment_mode(pf) if pf is not None else "auto"
            joint = partforge.joint_spec(pf) if pf is not None else {"type": "dovetail"}
            body, counts = segment_body(obj, joint=joint, mode=mode, printer=printer)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        name = obj.name
        collection = str(getattr(pf, "segment_collection", "") or "").strip()
        props.object_name = name
        props.face_count = int(counts.get("face_count") or 0)
        props.busy = True
        props.needs_repair = False
        set_status(props, "Cutting %s (%s) ..." % (name, printer_source))

        def work():
            return post("/segment_mesh", body, SEGMENT_TIMEOUT)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            if error is not None:
                props.needs_repair = looks_like_repair_problem(str(error))
                set_status(props, str(error), error=True)
                return
            try:
                names = partforge.load_segment_objects(value, collection=collection or None)
            except ForgeError as exc:
                set_status(props, str(exc), error=True)
                return
            props.summary = partforge.format_segment_summary(value, names)
            if pf is not None:
                pf.segment_summary = props.summary
            if not names:
                set_status(props, "The service returned no pieces to load.", error=True)
                return
            set_status(props, "Cut %s into %d piece(s): %s"
                       % (name, len(names),
                          ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")))

        run_async(work, done)
        return {"FINISHED"}


class FORGE_OT_model_repair(_ModelOperator):
    """Close the holes with a voxel remesh, which is the repair that works."""

    bl_idname = "forge.model_repair"
    bl_label = "Voxel Repair"
    bl_description = (
        "Rebuild the model as one closed surface so it can be checked and cut "
        "up. Fine detail smaller than the voxel size is lost"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_props(context)
        try:
            obj = target_object(props, context=context)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        # Straight through the command registry: same code path the assistant
        # uses, same named undo checkpoint, one implementation of remeshing.
        from . import registry

        status, result, message = registry.dispatch("remesh", {
            "object": obj.name,
            "mode": "voxel",
            "voxel_size": float(props.voxel_size_mm) * common.MM_TO_M,
        })
        if status != "success":
            set_status(props, message or "The repair failed.", error=True)
            return {"CANCELLED"}
        props.needs_repair = False
        props.face_count = int((result or {}).get("face_count") or 0)
        set_status(props, "Repaired %s (%s faces now). Check it again."
                   % (obj.name, props.face_count))
        _tag_redraw()
        return {"FINISHED"}


_CLASSES = (
    ForgeModelProps,
    FORGE_OT_model_import,
    FORGE_OT_model_check,
    FORGE_OT_model_segment,
    FORGE_OT_model_repair,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_model = bpy.props.PointerProperty(type=ForgeModelProps)


def unregister():
    try:
        del bpy.types.Scene.forge_model
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
