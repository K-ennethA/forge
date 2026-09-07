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
* **They only have a picture.** ``import_generated`` brings a ``.glb`` from the
  meshgen service (Phase 7, port 8902) into the scene and voxel-repairs it on
  the way in, because raw image-to-3D output is never manifold — paper-thin
  walls, boundary edges, inconsistent winding.  The Generate 3D from Picture
  button drives the whole job: post the image, follow the stages, import.

A mesh that is not watertight cannot be sewn into a solid, so the service
refuses it and says to repair it first.  That refusal is passed through word for
word and the box grows a one-click **Voxel Repair** button (a voxel remesh,
which is what closes those holes), so the fix is a button rather than a
tutorial.

Panel work is async through the house pattern (worker thread + timer); the two
socket commands are synchronous, because a socket command IS the main thread and
its caller is waiting on the reply.
"""

import json
import math
import os
import threading
import time
import traceback
import urllib.error
import urllib.request

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..prefs import meshgen_url, service_url
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

# --- meshgen (Phase 7: a picture becomes a mesh) ----------------------------

#: What Blender's glTF importer opens, and all meshgen ever writes.
GLTF_EXTENSIONS = (".glb", ".gltf")

#: What meshgen will accept as an input picture.
MESHGEN_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


#: A real generation is minutes, not seconds, on this hardware: measured 304 s
#: (trellis2) and 249 s (pixal3d) on the 12 GB card, plus ComfyUI's cold start.
#: Generous on purpose, and tunable for a slower machine or a bigger model.
GENERATE_TIMEOUT = _env_float("FORGE_MESHGEN_JOB_TIMEOUT", 900.0)
#: /generate3d answers 202 immediately; only the job takes minutes.
MESHGEN_REQUEST_TIMEOUT = 30.0
#: How often the panel asks meshgen where the job has got to.
MESHGEN_POLL_INTERVAL = 2.0

#: Raw image-to-3D output is organic soup by every downstream standard: paper
#: walls, boundary edges, inconsistent winding.  Repair is the default, not an
#: option, and this is the density it aims for when nothing says otherwise.
REPAIR_TARGET_FACES = 200000
REPAIR_MIN_FACES = 5000


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
    #: A picture to turn into a mesh.  The Assistant box's attachment wins when
    #: there is one, so an artist who dragged their photo in up there does not
    #: have to find it twice.
    image_path: StringProperty(
        name="Picture",
        description=(
            "A photo or sketch to turn into a 3D shape. Leave this empty to use "
            "the picture attached in the Assistant box above"
        ),
        default="",
        subtype="FILE_PATH",
    )
    #: Live from meshgen's /job while a generation runs: the node doing the
    #: work.  The bar is per-stage, never overall - that is the service's own
    #: contract and the panel says so.
    gen_stage: StringProperty(default="")
    gen_progress: FloatProperty(default=0.0, min=0.0, max=1.0)
    gen_job_id: StringProperty(default="")
    gen_seconds: FloatProperty(default=0.0)


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
# meshgen: the picture-to-mesh service on 8902
# ---------------------------------------------------------------------------

def meshgen_request(path, payload=None, timeout=MESHGEN_REQUEST_TIMEOUT, method=None):
    """One JSON call to meshgen. Worker-thread safe; never touches ``bpy``.

    Its failures are the artist's, not a stack trace: a closed port means the
    picture service is not running, and the fix is the Start services button in
    the Forge Status box at the top of the panel.
    """
    url = meshgen_url(path)
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        detail = body
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                detail = str(parsed["error"])
        except ValueError:
            pass
        raise ForgeError("The picture service refused this (HTTP %s): %s"
                         % (exc.code, detail or exc.reason))
    except urllib.error.URLError as exc:
        raise ForgeError(
            "Could not reach the picture-to-3D service at %s (%s). Press Start "
            "services in the Forge Status box at the top of this panel."
            % (url, getattr(exc, "reason", exc)))
    except OSError as exc:
        raise ForgeError("The picture service did not answer %s: %s" % (url, exc))

    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ForgeError("The picture service answered with something that is "
                         "not JSON (%s): %s" % (exc, text[:200]))
    if not isinstance(parsed, dict):
        raise ForgeError("The picture service answered with a %s, not an object."
                         % type(parsed).__name__)
    return parsed


def resolve_image(path):
    """An attached picture as an absolute path, checked the way the artist reads."""
    text = str(path or "").strip().strip('"')
    if not text:
        raise ForgeError(
            "Attach a picture first: the Picture field in this box, or the one "
            "in the Assistant box above.")
    resolved = common.resolve_path(text)
    if os.path.isdir(resolved):
        raise ForgeError("%s is a folder, not a picture."
                         % os.path.basename(resolved.rstrip("\\/")))
    if os.path.splitext(resolved)[1].lower() not in MESHGEN_IMAGE_EXTENSIONS:
        raise ForgeError(
            "%s is not a picture Forge can send (%s)."
            % (os.path.basename(resolved) or resolved,
               ", ".join(MESHGEN_IMAGE_EXTENSIONS)))
    if not os.path.isfile(resolved):
        raise ForgeError("There is no file at %s." % resolved)
    return resolved


def generate_and_wait(image_path, backend="", on_progress=None,
                      timeout=GENERATE_TIMEOUT, interval=MESHGEN_POLL_INTERVAL,
                      should_stop=None):
    """POST /generate3d, follow /job to the end, return the finished job.

    Worker-thread only.  ``on_progress(job_dict)`` is called after every poll
    with meshgen's own answer, so the caller can mirror the stage name into the
    panel; it must not touch ``bpy`` either.  Raises :class:`ForgeError` for a
    failed, cancelled or overdue job.
    """
    body = {"image_path": image_path}
    if str(backend or "").strip():
        body["backend"] = str(backend).strip()
    submitted = meshgen_request("/generate3d", body)
    job_id = str(submitted.get("job_id") or "").strip()
    if not job_id:
        raise ForgeError("The picture service accepted the job but named no job "
                         "id, so there is nothing to follow.")
    if on_progress is not None:
        on_progress(dict(submitted, job_id=job_id, state=submitted.get("state") or "queued"))

    deadline = time.monotonic() + float(timeout)
    while True:
        if should_stop is not None and should_stop():
            raise ForgeError("Stopped waiting for the picture job (%s)." % job_id)
        job = meshgen_request("/job/%s" % job_id)
        state = str(job.get("state") or "").lower()
        if on_progress is not None:
            on_progress(job)
        if state == "done":
            return job
        if state == "error":
            raise ForgeError("The picture service could not make a model: %s"
                             % (job.get("error") or "no reason given"))
        if state == "cancelled":
            raise ForgeError("The picture job was cancelled.")
        if time.monotonic() > deadline:
            raise ForgeError(
                "The picture job %s is still running after %.0f minutes (stage: "
                "%s). It has not failed - watch it with meshgen_status, or raise "
                "FORGE_MESHGEN_JOB_TIMEOUT."
                % (job_id, float(timeout) / 60.0, job.get("stage") or "unknown"))
        time.sleep(interval)


# ---------------------------------------------------------------------------
# importing a generated mesh (socket command: import_generated)
# ---------------------------------------------------------------------------

def import_gltf(path):
    """Import one .glb/.gltf; returns ``(new object names, operator used)``."""
    before = set(bpy.data.objects.keys())
    attempts = [(bpy.ops.import_scene, "gltf", {"filepath": path}),
                (bpy.ops.wm, "gltf_import", {"filepath": path})]
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
            added = [n for n in bpy.data.objects.keys() if n not in before]
            return added, name
        problems.append("%s returned %s" % (name, ", ".join(sorted(status)) or "nothing"))
    raise ForgeError(
        "Could not open %s — %s"
        % (os.path.basename(path),
           "; ".join(problems) or "this Blender has no glTF importer."))


def _consolidate_import(added_names, source):
    """One mesh object out of whatever the file's scene graph contained.

    A generated ``.glb`` is one mesh, but the file still carries the scene
    graph it was written with: an empty for the glTF root, a Y-up rotation on
    it.  The artist wants an object, not a hierarchy, so the meshes are joined,
    the transform is baked in and the scaffolding is deleted.
    """
    objects = [bpy.data.objects[n] for n in added_names if n in bpy.data.objects]
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes:
        raise ForgeError(
            "%s opened, but there is no mesh in it (%d object(s): %s)."
            % (os.path.basename(source), len(objects),
               ", ".join(obj.type.lower() for obj in objects) or "none"))

    primary = max(meshes, key=lambda obj: len(obj.data.polygons))
    if len(meshes) > 1:
        with common.selection(meshes, primary):
            try:
                bpy.ops.object.join()
            except RuntimeError as exc:
                raise ForgeError("Could not join the %d parts of %s: %s"
                                 % (len(meshes), os.path.basename(source), exc))

    # Bake the glTF root's Y-up rotation into the mesh, then cut it loose, so
    # the object's own numbers are the truth for every downstream measurement.
    matrix = primary.matrix_world.copy()
    if primary.parent is not None:
        primary.parent = None
        primary.matrix_world = matrix
    try:
        with common.active_only(primary):
            bpy.ops.object.transform_apply(
                **common.op_kwargs(bpy.ops.object.transform_apply,
                                   {"location": False, "rotation": True,
                                    "scale": True}))
    except RuntimeError:
        pass  # a transform that will not apply is cosmetic, not fatal

    for name in added_names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj is primary:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    return primary


def _move_to_collection(obj, name):
    target = common._resolve_collection(name)
    for collection in list(obj.users_collection):
        if collection is not target:
            try:
                collection.objects.unlink(obj)
            except (RuntimeError, ReferenceError):
                pass
    if obj.name not in target.objects:
        target.objects.link(obj)
    common.refresh_view_layer()


def repair_voxel_size(obj, target_faces=REPAIR_TARGET_FACES):
    """Voxel size for the repair pass, from the mesh's own size and density.

    The same reasoning as RigForge's retopo: solve ``area / v^2`` for the face
    count we want to land on rather than picking a millimetre value that only
    suits one scale.  The target defaults to the mesh's OWN density (clamped),
    so repairing a generated mesh keeps roughly the detail it arrived with —
    a fixed voxel size would gut a small model or explode a large one.
    """
    from . import rigforge

    faces = len(obj.data.polygons) if obj.type == "MESH" and obj.data else 0
    target = min(max(faces or target_faces, REPAIR_MIN_FACES), target_faces)
    return rigforge.adaptive_voxel_size(obj, target, oversample=1.0)


@command("import_generated")
def cmd_import_generated(params):
    """Bring a generated ``.glb`` into the scene, repaired.

    params: ``path`` (absolute .glb/.gltf), ``name?``, ``repair?`` (default
    true), ``voxel_size?`` (scene metres; omitted = adaptive), ``collection?``.

    Repair is on by default because raw image-to-3D output is never manifold —
    it has paper-thin walls, boundary edges and inconsistent winding, and every
    downstream Forge step (print checks, segmenting, retopo) needs one closed
    shell.  ``repair: false`` is for looking at exactly what the model made.
    """
    path = common.resolve_path(common.get_str(params, "path"))
    if os.path.isdir(path):
        raise ForgeError("%r is a folder, not a model file." % path)
    if os.path.splitext(path)[1].lower() not in GLTF_EXTENSIONS:
        raise ForgeError(
            "import_generated opens %s files (what meshgen writes). %s is a %s "
            "file — use the Model box's Import Model for STL/OBJ/PLY."
            % (" and ".join(GLTF_EXTENSIONS), os.path.basename(path),
               os.path.splitext(path)[1].lower() or "no-extension"))
    if not os.path.isfile(path):
        raise ForgeError("There is no file at %r." % path)

    name = str(params.get("name") or "").strip()
    repair = common.get_bool(params, "repair", True)
    collection = params.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise ForgeError("'collection' must be a collection name.")
    voxel_override = params.get("voxel_size")

    with common.object_mode():
        added, importer = import_gltf(path)
        if not added:
            raise ForgeError(
                "%s imported nothing — the file may be empty or unreadable."
                % os.path.basename(path))
        obj = _consolidate_import(added, path)
        if name:
            obj.name = name
        if collection is not None and collection.strip():
            _move_to_collection(obj, collection.strip())
        common.refresh_view_layer()

        before = common.mesh_stats(obj)
        voxel_size = None
        method = None
        if repair:
            if voxel_override is not None:
                voxel_size = common.get_float(params, "voxel_size", minimum=1e-9)
            else:
                voxel_size = repair_voxel_size(obj)
            method = common._voxel_remesh(obj, voxel_size, 0.0)
            if not len(obj.data.polygons):
                raise ForgeError(
                    "The repair of %r left no faces (voxel size %.6g was too "
                    "coarse for this mesh). Pass a smaller 'voxel_size', or "
                    "'repair': false to see the raw generated mesh."
                    % (obj.name, voxel_size))
        common.refresh_view_layer()

    result = common.mesh_stats(obj)
    result.update({
        "object": obj.name,
        "repaired": bool(repair),
        "path": path,
        "importer": importer,
        "imported_objects": len(added),
        "dimensions_mm": [round(float(v) * common.M_TO_MM, 3) for v in obj.dimensions],
        "before": before,
    })
    if repair:
        result["voxel_size"] = round(float(voxel_size), 8)
        result["voxel_size_mm"] = round(float(voxel_size) * common.M_TO_MM, 4)
        result["repair_method"] = method

    props = get_props()
    if props is not None:
        props.object_name = obj.name
        props.face_count = int(result.get("face_count") or 0)
        props.needs_repair = not repair
        props.summary = ("Generated mesh %s: %d faces%s"
                         % (obj.name, result.get("face_count") or 0,
                            "" if repair else " (NOT repaired)"))
        set_status(props, props.summary)
    _tag_redraw()
    return result


# ---------------------------------------------------------------------------
# merge_for_print (Phase 11) — many chosen pieces become one printable shell
# ---------------------------------------------------------------------------
#
# A design arrives as a component tree: the core, and the proposals the artist
# kept.  Every one of those is its own watertight solid, which is exactly right
# while they are deciding — and exactly wrong at the slicer, where two solids
# that merely overlap are two objects with a seam between them.
#
# Merging is a voxel remesh of the union, and the voxel size is the whole
# argument.  It is the one number that trades detail against file size, and the
# honest default comes from the machine rather than from taste:
#
#   voxel = nozzle / 2  (0.4 / 2 = 0.2 mm on the Centauri Carbon)
#
# Half the nozzle is two voxels across the narrowest bead the printer can
# actually lay down, which is the Nyquist argument in millimetres: anything the
# grid loses at that size is something the printer could not have printed
# anyway.  Going finer buys nothing on the plate and costs quadratically —
# triangle count grows as 1/voxel^2, so 0.1 mm is four times the file for detail
# that ends up inside a single extrusion.  Going coarser is a real choice the
# artist can make (a 0.4 mm voxel halves the file again) and it is theirs to
# make, not ours to make quietly.
#
# The cap exists because that default does not scale: a 100 mm sphere has about
# 31 000 mm2 of surface, which at 0.2 mm is roughly 780 000 quads — near the
# ceiling already — and a 200 mm one is four times that.  So the size is
# predicted from the joined mesh's own surface area before anything is remeshed,
# and coarsened until the prediction fits under the cap, with the trade said out
# loud in `notes` rather than discovered as a five-minute freeze.

#: Polygons the merged shell is allowed to come back with.  A million-face
#: object is already a slow .blend and a ~50 MB STL; past that Blender's own
#: viewport is what breaks first.
MERGE_FACE_CAP = 1000000

#: Voxels per extrusion width.  Two: one to say "material here", one to say
#: "and not here".
MERGE_VOXELS_PER_NOZZLE = 2.0

#: The nozzle assumed when there is no printer profile to read.
MERGE_DEFAULT_NOZZLE_MM = 0.4

#: Never finer than this, whatever the profile says — a 0.02 mm voxel on
#: anything bigger than a thimble is an out-of-memory error, not a detail level.
MERGE_MIN_VOXEL_MM = 0.05

#: A voxel bigger than this fraction of the smallest dimension eats the object.
MERGE_MAX_VOXEL_FRACTION = 0.25


def merge_nozzle_mm():
    """``(nozzle_mm, source)`` from the printer preference, or the default.

    A broken printer path must not fail a merge: the profile is being consulted
    for one number with a sane fallback, not for permission.
    """
    try:
        profile, source = partforge.load_printer()
    except ForgeError:
        return MERGE_DEFAULT_NOZZLE_MM, "default (the printer profile could not be read)"
    if not profile:
        return MERGE_DEFAULT_NOZZLE_MM, "default profile"
    try:
        nozzle = float(profile.get("nozzle_diameter"))
    except (TypeError, ValueError):
        return MERGE_DEFAULT_NOZZLE_MM, "%s (no nozzle_diameter in it)" % source
    if nozzle <= 0.0:
        return MERGE_DEFAULT_NOZZLE_MM, "%s (nozzle_diameter was not a size)" % source
    return nozzle, source


def _polygon_area_mm2(vertices, faces):
    """Surface area of a millimetre mesh, by fan-triangulating every face."""
    total = 0.0
    for face in faces:
        if len(face) < 3:
            continue
        ax, ay, az = vertices[face[0]]
        for index in range(1, len(face) - 1):
            bx, by, bz = vertices[face[index]]
            cx, cy, cz = vertices[face[index + 1]]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            total += 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
    return total


def predicted_faces(area_mm2, voxel_mm):
    """Roughly how many polygons a voxel remesh of that area will produce.

    One quad per voxel of surface, which is what OpenVDB's dual-contouring mesh
    comes out at within a factor the size of the shape's own curvature.  It is
    an estimate and it is used as one: it picks the voxel size, and the real
    count is reported afterwards.
    """
    if voxel_mm <= 0.0:
        return 0
    return int(area_mm2 / (voxel_mm * voxel_mm))


def merge_voxel_size(area_mm2, smallest_dim_mm, requested=None):
    """The resolution argument, decided in one place.

    ``(voxel_mm, source, notes, predicted_faces, nozzle_mm, printer_source)`` —
    ``source`` is ``"nozzle"`` (the default), ``"given"``, ``"clamped"`` or
    ``"coarsened"``, and every clamp writes its reason into ``notes`` rather
    than quietly moving the number.
    """
    notes = []
    nozzle, profile_source = merge_nozzle_mm()
    auto = max(nozzle / MERGE_VOXELS_PER_NOZZLE, MERGE_MIN_VOXEL_MM)

    if requested is None:
        voxel = auto
        source = "nozzle"
    else:
        voxel = float(requested)
        source = "given"

    if voxel < MERGE_MIN_VOXEL_MM:
        notes.append(
            "A %.3f mm voxel is finer than Forge will build (%.2f mm); at that "
            "size the grid costs memory for detail no nozzle can print."
            % (voxel, MERGE_MIN_VOXEL_MM))
        voxel = MERGE_MIN_VOXEL_MM
        source = "clamped"

    ceiling = smallest_dim_mm * MERGE_MAX_VOXEL_FRACTION
    if ceiling > 0.0 and voxel > ceiling:
        notes.append(
            "A %.2f mm voxel is too coarse for something %.1f mm across — it "
            "would leave little or nothing behind — so it was taken down to "
            "%.2f mm." % (voxel, smallest_dim_mm, ceiling))
        voxel = ceiling
        source = "clamped"

    estimate = predicted_faces(area_mm2, voxel)
    if estimate > MERGE_FACE_CAP and area_mm2 > 0.0:
        coarser = voxel * math.sqrt(float(estimate) / float(MERGE_FACE_CAP))
        coarser = math.ceil(coarser * 100.0) / 100.0
        notes.append(
            "At %.2f mm this merge would come back with about %s polygons, over "
            "the %s cap, so the voxel was coarsened to %.2f mm. That is the "
            "trade: the file stays workable, and detail finer than %.2f mm is "
            "rounded off. Ask for a smaller voxel_size_mm if you want it back "
            "and can live with the file."
            % (voxel, "{:,}".format(estimate), "{:,}".format(MERGE_FACE_CAP),
               coarser, coarser))
        voxel = coarser
        source = "coarsened"
        estimate = predicted_faces(area_mm2, voxel)

    return voxel, source, notes, estimate, nozzle, profile_source


def _topology_watertight(faces):
    """True when every edge in this face list is shared by exactly two faces."""
    if not faces:
        return False
    edges = {}
    for face in faces:
        count = len(face)
        if count < 3:
            return False
        for index in range(count):
            a = face[index]
            b = face[(index + 1) % count]
            key = (a, b) if a < b else (b, a)
            edges[key] = edges.get(key, 0) + 1
    return all(count == 2 for count in edges.values())


def _visible(obj):
    try:
        return bool(obj.visible_get())
    except (RuntimeError, ReferenceError, AttributeError):
        return not bool(getattr(obj, "hide_viewport", False))


def merge_sources(params):
    """``(objects, how, collection_name, skipped)`` — what is being merged.

    Resolution order, and it is deliberate: an explicit list wins, then a named
    collection (every **visible** mesh in it — which is what makes "scrap the
    collar" and "merge what's left" the same two words), then the selection,
    then the active object.
    """
    skipped = []
    names = params.get("objects")
    collection_name = params.get("collection")
    if collection_name is not None and not isinstance(collection_name, str):
        raise ForgeError("'collection' must be a collection name.")
    collection_name = (collection_name or "").strip()

    if names is not None and not isinstance(names, list):
        raise ForgeError("'objects' must be a list of object names.")
    if names:
        objects = []
        for entry in names:
            if not isinstance(entry, str) or not entry.strip():
                raise ForgeError("'objects' must be a list of object names.")
            objects.append(common.find_object(entry.strip(), mesh_only=True))
        return objects, "objects", collection_name, skipped

    if collection_name:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise ForgeError(
                "There is no collection called %r. Collections are the folders "
                "in the list at the top right." % collection_name)
        objects = []
        for obj in collection.all_objects:
            if obj.type != "MESH" or not len(obj.data.polygons):
                skipped.append("%s (%s)" % (obj.name, obj.type.lower()))
                continue
            if not _visible(obj):
                skipped.append("%s (hidden)" % obj.name)
                continue
            objects.append(obj)
        if not objects:
            raise ForgeError(
                "Nothing in the collection %r is a visible mesh, so there is "
                "nothing to merge." % collection_name)
        return objects, "collection", collection_name, skipped

    selected = []
    try:
        view_layer = common.get_view_layer()
        selected = [obj for obj in view_layer.objects
                    if obj.select_get() and obj.type == "MESH"]
    except (ForgeError, RuntimeError, AttributeError):
        selected = []
    if selected:
        return selected, "selection", collection_name, skipped

    active = common.get_active_object()
    if active is not None and active.type == "MESH":
        return [active], "active", collection_name, skipped
    raise ForgeError(
        "Nothing to merge. Name the pieces in 'objects', name the project's "
        "'collection', or select them in the viewport first.")


def _merged_name(params, objects, collection_name):
    """``<project>-merged`` by the component convention, unless told otherwise."""
    given = str(params.get("name") or "").strip()
    if given:
        return given
    names = [obj.name for obj in objects]
    if collection_name:
        stem = collection_name          # a project collection IS the project
    else:
        stem = common.common_project(names) if len(names) > 1 else ""
        if not stem:
            stem = common.project_of(names[0], [c.name for c in bpy.data.collections])
    return common.component_name(stem or names[0], "merged")


@command("merge_for_print")
def cmd_merge_for_print(params):
    """Join the chosen pieces and voxel-remesh them into ONE watertight shell.

    params: ``objects?`` (names), ``collection?``, ``voxel_size_mm?`` (omit for
    nozzle/2), ``name?`` (default ``<project>-merged``), ``keep_originals?``
    (default true — the originals are **hidden, never deleted**).

    This is the last step before the slicer, and it is destructive-ish by
    nature, so it pushes an undo checkpoint like every other building command
    and the pieces it consumed are still there behind the eye icon.

    The natural next call is ``check_model`` on what comes back: a merged shell
    is a new mesh, and whether it still fits the bed and still has walls thick
    enough to print is a question the merge cannot answer.
    """
    objects, how, collection_name, skipped = merge_sources(params)
    keep_originals = common.get_bool(params, "keep_originals", True)
    requested = params.get("voxel_size_mm")
    if requested is not None:
        # Zero means "you choose" rather than an error: it is what a flow's
        # numeric parameter has to say when its honest default is the nozzle.
        requested = common.get_float(params, "voxel_size_mm", minimum=0.0)
        if requested <= 0.0:
            requested = None

    name = _merged_name(params, objects, collection_name)
    if any(obj.name == name for obj in objects):
        raise ForgeError(
            "%r is one of the pieces being merged, so the merged shell cannot "
            "be called that too. Pass a different 'name'." % name)

    notes = []
    if skipped:
        notes.append("Skipped: %s." % ", ".join(skipped[:8]))
    vertices = []
    faces = []
    watertight_inputs = 0
    sources = []

    with common.object_mode():
        for obj in objects:
            piece_vertices, piece_faces = common.evaluated_mesh_mm(obj)
            offset = len(vertices)
            vertices.extend(piece_vertices)
            faces.extend([[index + offset for index in face] for face in piece_faces])
            sealed = _topology_watertight(piece_faces)
            watertight_inputs += 1 if sealed else 0
            sources.append({
                "object": obj.name,
                "vertex_count": len(piece_vertices),
                "face_count": len(piece_faces),
                "watertight": sealed,
            })

        if not faces:
            raise ForgeError(
                "The pieces have no faces between them, so there is nothing to "
                "merge.")

        area = _polygon_area_mm2(vertices, faces)
        low = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        high = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        size = [high[axis] - low[axis] for axis in range(3)]
        voxel_mm, voxel_source, voxel_notes, estimate, nozzle, profile_source = \
            merge_voxel_size(area, min(size), requested)
        notes.extend(voxel_notes)

        target_collection = collection_name or _first_collection(objects[0])
        obj, _ = common.build_mesh_object(
            name, vertices, faces, replace=True, collection=target_collection)
        method = common._voxel_remesh(obj, voxel_mm * common.MM_TO_M, 0.0)
        common.refresh_view_layer()

        if not len(obj.data.polygons):
            raise ForgeError(
                "The merge left no faces: a %.2f mm voxel was too coarse for "
                "these pieces. Pass a smaller 'voxel_size_mm'." % voxel_mm)

        hidden = []
        removed = []
        for source in objects:
            if keep_originals:
                if _hide(source):
                    hidden.append(source.name)
            else:
                removed.append(source.name)
                try:
                    bpy.data.objects.remove(source, do_unlink=True)
                except (ReferenceError, RuntimeError):
                    pass
        common.refresh_view_layer()
        _make_active(obj)

    bad_edges, loose, face_count = common._mesh_health(obj)
    watertight = bad_edges == 0 and face_count > 0
    if not watertight:
        notes.append(
            "The merged shell still has %d unsealed or non-manifold edge(s). "
            "Run mesh_diagnose to see where, or merge again at a coarser voxel."
            % bad_edges)
    if watertight_inputs < len(objects):
        notes.append(
            "%d of the %d pieces were not sealed on their own; the voxel remesh "
            "closes that, which is half the reason this step exists."
            % (len(objects) - watertight_inputs, len(objects)))
    if keep_originals:
        notes.append(
            "The %d original piece(s) are hidden, not deleted — click the eye "
            "next to them in the list at the top right to bring one back."
            % len(hidden))

    result = common.mesh_stats(obj)
    result.update({
        "object": obj.name,
        "voxel_size_mm": round(voxel_mm, 4),
        "voxel_size_requested_mm": (round(float(requested), 4)
                                    if requested is not None else None),
        "voxel_source": voxel_source,
        "nozzle_mm": round(float(nozzle), 3),
        "printer_source": profile_source,
        "predicted_face_count": estimate,
        "surface_area_mm2": round(area, 1),
        "watertight_input_count": watertight_inputs,
        "watertight": watertight,
        "loose_vertices": loose,
        "sources": sources,
        "source_count": len(sources),
        "resolved_by": how,
        "collection": collection_name or target_collection or "",
        "kept_originals": bool(keep_originals),
        "hidden": hidden,
        "deleted": removed,
        "dimensions_mm": [round(value, 3) for value in size],
        "remesh_method": method,
        "next": "check_model",
        "notes": notes,
    })

    props = get_props()
    if props is not None:
        props.object_name = obj.name
        props.face_count = int(result.get("face_count") or 0)
        props.needs_repair = not watertight
        props.summary = ("Merged %d piece(s) into %s: %d faces at %.2f mm voxel"
                         % (len(sources), obj.name,
                            result.get("face_count") or 0, voxel_mm))
        set_status(props, props.summary)
    _tag_redraw()
    return result


def _first_collection(obj):
    """The name of the collection this object lives in, or "" for the scene's."""
    try:
        scene_collection = common.get_scene().collection
    except ForgeError:
        scene_collection = None
    for collection in obj.users_collection:
        if collection is not scene_collection:
            return collection.name
    return ""


def _hide(obj):
    try:
        obj.hide_set(True)
        return True
    except (RuntimeError, ReferenceError):
        pass
    try:
        obj.hide_viewport = True
        return True
    except (AttributeError, ReferenceError):
        return False


def _make_active(obj):
    """Leave the merged shell selected, because it is what they work on next."""
    try:
        view_layer = common.get_view_layer()
        for other in view_layer.objects:
            if other.select_get():
                other.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
    except (ForgeError, RuntimeError, ReferenceError, AttributeError):
        pass


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


class FORGE_OT_model_merge(_ModelOperator):
    """Everything selected becomes one printable shell."""

    bl_idname = "forge.model_merge"
    bl_label = "Merge for Print"
    bl_description = (
        "Join everything you have selected into ONE sealed shell for the "
        "slicer. The originals are hidden, not deleted, and the voxel size "
        "comes from the printer's nozzle"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_props(context)
        selected = [obj for obj in (getattr(context, "selected_objects", None) or [])
                    if obj.type == "MESH"]
        if not selected:
            set_status(props,
                       "Select the pieces first — click one, then Shift+click "
                       "the rest — and press Merge for Print again.", error=True)
            return {"CANCELLED"}

        from . import registry

        status, result, message = registry.dispatch("merge_for_print", {
            "objects": [obj.name for obj in selected],
        })
        if status != "success":
            set_status(props, message or "The merge failed.", error=True)
            return {"CANCELLED"}

        result = result or {}
        set_status(props, "Merged %d piece(s) into %s at a %.2f mm voxel. Now "
                          "press Check imported model."
                   % (result.get("source_count") or len(selected),
                      result.get("object") or "",
                      float(result.get("voxel_size_mm") or 0.0)))
        _tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Generate 3D from Picture
# ---------------------------------------------------------------------------

def picture_for(props, context=None):
    """``(path, where it came from)`` — this box's picture, or the chat box's.

    One button, two places the artist might have put the file.  The Model box's
    own field wins when it is filled in; otherwise the picture attached in the
    Assistant box is used, because an artist who dragged their photo in up there
    should not have to find it a second time.
    """
    own = str(getattr(props, "image_path", "") or "").strip()
    if own:
        return resolve_image(own), "the Picture field"
    try:
        from . import assistant

        chat = assistant.get_props(context)
    except Exception:  # noqa: BLE001
        chat = None
    attached = str(getattr(chat, "image_path", "") or "").strip()
    if attached:
        return resolve_image(attached), "the Assistant box"
    raise ForgeError(
        "Attach a picture first — the Picture field in this box, or the one in "
        "the Assistant box above.")


def stage_sentence(box):
    """The live line under the button: which step, and how long so far."""
    stage = str(box.get("stage") or "").strip()
    state = str(box.get("state") or "").strip() or "working"
    seconds = box.get("seconds") or 0.0
    minutes = "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)
    if state == "queued":
        return "Waiting for the picture service (%s) ..." % minutes
    if not stage:
        return "Making your model (%s) ..." % minutes
    return "%s (%s) ..." % (stage, minutes)


class FORGE_OT_model_generate3d(_ModelOperator):
    """A picture in, a repaired mesh in the viewport out. Takes about 5 minutes."""

    bl_idname = "forge.model_generate3d"
    bl_label = "Generate 3D from Picture"
    bl_description = (
        "Turn the attached picture into a 3D shape with the picture service, "
        "then bring it in repaired. Takes about five minutes on this machine"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = get_props(context)
        try:
            image_path, source = picture_for(props, context)
        except ForgeError as exc:
            set_status(props, str(exc), error=True)
            return {"CANCELLED"}

        props.busy = True
        props.gen_stage = ""
        props.gen_progress = 0.0
        props.gen_job_id = ""
        props.gen_seconds = 0.0
        props.needs_repair = False
        set_status(props, "Sending %s from %s — this takes about five minutes."
                   % (os.path.basename(image_path), source))

        box = {"stage": "", "progress": 0.0, "job_id": "", "state": "queued",
               "seconds": 0.0, "started": time.monotonic()}

        def on_progress(job):
            box["job_id"] = str(job.get("job_id") or box["job_id"])
            state = str(job.get("state") or "").strip()
            if state:
                box["state"] = state
            stage = job.get("stage")
            if stage:
                box["stage"] = str(stage)
            progress = job.get("progress")
            if isinstance(progress, (int, float)):
                box["progress"] = float(progress)
            box["seconds"] = time.monotonic() - box["started"]

        def work():
            return generate_and_wait(image_path, on_progress=on_progress)

        def done(value, error):
            if not _alive(props):
                return
            props.busy = False
            props.gen_stage = ""
            if error is not None:
                set_status(props, str(error), error=True)
                return
            mesh_path = str((value or {}).get("mesh_path") or "")
            if not mesh_path:
                set_status(props, "The picture service finished but named no "
                                  "file to import.", error=True)
                return
            seconds = float((value or {}).get("duration_ms") or 0.0) / 1000.0
            from . import registry

            status, result, message = registry.dispatch("import_generated", {
                "path": mesh_path, "repair": True})
            if status != "success":
                set_status(props, "Made %s but could not import it: %s"
                           % (os.path.basename(mesh_path), message), error=True)
                return
            result = result or {}
            set_status(props,
                       "Made %s in %d min %02d s — %d faces, repaired. Press "
                       "Check imported model to see whether it will print."
                       % (result.get("object"), int(seconds) // 60,
                          int(seconds) % 60, result.get("face_count") or 0))

        _run_generation_async(work, done, props, box)
        return {"FINISHED"}


def _run_generation_async(work, done, props, box):
    """The house async pattern, plus a live mirror of the job's stage.

    ``run_async`` only speaks once, at the end; a five-minute job that says
    nothing for five minutes looks broken.  The worker writes into ``box`` and
    a timer copies it onto the props, so the panel shows the stage name meshgen
    is actually on.
    """
    if bpy.app.background:
        try:
            done(work(), None)
        except Exception as exc:  # noqa: BLE001
            if _alive(props):
                props.busy = False
            done(None, exc)
        return

    state = {"done": False, "value": None, "error": None}

    def worker():
        try:
            state["value"] = work()
        except Exception as exc:  # noqa: BLE001
            state["error"] = exc
        finally:
            state["done"] = True

    threading.Thread(target=worker, name="ForgeMeshgen", daemon=True).start()

    def poll():
        if not _alive(props):
            return None
        if not state["done"]:
            props.gen_stage = stage_sentence(box)
            props.gen_progress = max(0.0, min(1.0, float(box.get("progress") or 0.0)))
            props.gen_job_id = str(box.get("job_id") or "")
            props.gen_seconds = float(box.get("seconds") or 0.0)
            _tag_redraw()
            return 0.5
        try:
            done(state["value"], state["error"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tag_redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.5)


_CLASSES = (
    ForgeModelProps,
    FORGE_OT_model_import,
    FORGE_OT_model_check,
    FORGE_OT_model_segment,
    FORGE_OT_model_repair,
    FORGE_OT_model_merge,
    FORGE_OT_model_generate3d,
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
