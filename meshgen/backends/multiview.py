"""Multi-view conditioning for Pixal3D: the request surface, the camera rig,
the graph, and the availability probe.

Phase 18(a).  The intent is "front + side (+ back) images condition one
generation" instead of a single photo guessing the other three sides.

**This runs now.**  ComfyUI core **v0.35.0** (2026-09-09) added
``Pixal3DMultiViewConditioning`` to ``comfy_extras/nodes_trellis2.py``, which is
the per-view camera node whose absence blocked this through v0.34.x.  meshgen's
live install is on **v0.35.1** and the multi-view checkpoint is on disk, so the
two blockers this module used to document are both gone.  What remains here is
the mapping between meshgen's request surface and that node's sockets - and one
bug that mapping used to carry.

THE AZIMUTH SWAP (read this before touching :data:`VIEW_AZIMUTHS`)
------------------------------------------------------------------
Core's own view table, verbatim from ``comfy_extras/nodes_trellis2.py``::

    _VIEW_AZIMUTHS = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}

meshgen originally mapped ``right -> 90`` and ``left -> 270``: **exactly
swapped**, a 180 degree error on the two side cameras.  Feeding a correctly
named socket the other side's image does not fail - it produces a confidently
wrong mesh.  It is fixed here, and the fix is defended three ways:

1.  :data:`CORE_VIEW_AZIMUTHS` is core's dict, copied, and :data:`VIEW_AZIMUTHS`
    is derived from it rather than written out again.
2.  Views are wired to node sockets **by azimuth value**
    (:func:`core_socket_for_azimuth`), never by name, so a name-level mistake
    cannot survive: the socket is whatever core calls 270 degrees.
3.  A real 2-view and 4-view run was checked for orientation - a wide-front /
    narrow-side test object must come out wide on the axis the front view saw.

WHAT ``side`` MEANS, LOUDLY
--------------------------
meshgen's request surface predates core's and calls the one extra view ``side``.
Core has no ``side``.  The choice made here, deliberately:

    **``side`` is the object's RIGHT side, i.e. core's ``right``, azimuth 270.**

So ``{"front": ..., "side": ...}`` is a front + right-side pair.  If you want
the left side, ask for ``left`` by name.  ``side`` and ``right`` are the same
camera and giving both is refused rather than guessed.

THE CAMERA RIG IS VERIFIED, NOT GUESSED
---------------------------------------
:func:`transform_matrix` is core's ``_PROJ_FRONT_VIEW_TRANSFORM`` orbited about
the world up axis, and it agrees with three independent sources at every
azimuth: core's front-view constant at 0 degrees, core's own
``_orbit_camera_to_world`` at all four, and the ``transforms.json`` TencentARC
ships in ``assets/mv_images/example/`` - including the camera distance
3.1192049980163574 at a 20 degree FOV.  ``test_multiview.py`` pins all of them
as golden fixtures, so this stays true without a GPU, without the weights, and
without network access.

Core's distance agrees too, and for the same reason::

    c2w = _orbit_camera_to_world(azimuths, [0.0] * num_views,
                                 _VIEW_PAD * 0.5 / math.tan(fov / 2.0))
    _VIEW_PAD = 1.1

which is :func:`distance_for_fov` with :data:`PAD_FACTOR`.  meshgen's existing
1024 / pad-1.1 crop contract is therefore already the framing this node wants,
and the single-view preparation chain feeds all four sockets unchanged.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

HF = "https://huggingface.co"

#: The multi-view checkpoint.  ``(folder, filename, bytes, url)`` - the same
#: shape ``comfyui_base`` uses for every other weight, so readiness reporting,
#: /health and the "here is what is missing" message all work unchanged.
#:
#: Size and sha256 read from the Hugging Face API on 2026-09-08, and verified
#: against the downloaded file on 2026-09-13.  Note it is byte-for-byte the same
#: SIZE as the single-view int8 checkpoint (5 584 555 824) - the two are the
#: same architecture repacked.  That is why the graph must load THIS file when
#: views are present: the single-view checkpoint would load without complaint
#: and quietly condition every view as if it were the front.
MULTIVIEW_WEIGHT = (
    "diffusion_models",
    "pixal3d_multiview_int8_convrot.safetensors",
    5584555824,
    f"{HF}/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_multiview_int8_convrot.safetensors",
)

#: sha256 of the file above, for the ledger at C:\forge-models\downloads\sha256.txt
MULTIVIEW_WEIGHT_SHA256 = "6b1eb3328930d921d3b57508d9c46cceec1c174dcedeebc14f176ab67d49f477"

#: Node ids that mean "core can express a per-view camera".  The first is the
#: one core actually shipped in v0.35.0; the rest are the spellings that were
#: plausible before it landed and are kept so the probe stays tolerant.
#: :func:`core_multiview_support` looks for any of them.
MULTIVIEW_NODE_CANDIDATES = (
    "Pixal3DMultiViewConditioning",
    "Pixal3DMultiviewConditioning",
    "Pixal3DConditioningMultiview",
    "Trellis2MultiviewConditioning",
    "Trellis2MultiViewConditioning",
)

#: ComfyUI core's ``_VIEW_AZIMUTHS``, copied verbatim from
#: ``comfy_extras/nodes_trellis2.py`` (v0.35.0+).  This is the authority for
#: which socket is which camera; everything else in this module is derived from
#: it.  Insertion order is also core's socket order, which is the order it
#: batches views in and the order its "first connected view is the front" rule
#: walks.
CORE_VIEW_AZIMUTHS = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}

#: Core's crop padding, from the same file: ``_VIEW_PAD = 1.1``.  Kept here so
#: a test can assert meshgen's :data:`PAD_FACTOR` still equals it.
CORE_VIEW_PAD = 1.1

#: meshgen request-surface name -> azimuth in degrees around the world up axis
#: (+Z).  Derived from :data:`CORE_VIEW_AZIMUTHS` plus exactly one alias.
#:
#: ``side`` is NOT a fifth camera.  meshgen's request surface predates core's
#: and says ``side``; core has no such name.  **``side`` means the object's
#: RIGHT side - core's ``right``, azimuth 270.**  Ask for ``left`` by name if
#: you want the other one.  This was previously ``side/right -> 90`` and
#: ``left -> 270``, which is core's mapping with the two sides swapped; see the
#: module docstring.
VIEW_AZIMUTHS = dict(CORE_VIEW_AZIMUTHS)
VIEW_AZIMUTHS["side"] = CORE_VIEW_AZIMUTHS["right"]

#: Aliases in the request surface that are not core socket names.
VIEW_ALIASES = {"side": "right"}

#: Order views are staged in: core's own socket order, with the ``side`` alias
#: sitting where ``right`` sits because it IS ``right``.  The FIRST frame is
#: load-bearing - core poses the mesh to whichever view is connected first, and
#: :func:`normalise_views` requires ``front``, so front is always index 0.
VIEW_ORDER = ("front", "left", "back", "right", "side")

#: azimuth -> the core socket name for that camera.  The wiring in
#: :func:`build_multiview_graph` goes through this, never through the request
#: name, so a naming mistake cannot reach the graph.
_AZIMUTH_TO_SOCKET = {az: name for name, az in CORE_VIEW_AZIMUTHS.items()}


def core_socket_for_azimuth(azimuth_deg: float) -> str:
    """Which ``Pixal3DMultiViewConditioning`` socket is this camera?

    By value, not by name: 270 degrees is ``right`` whatever meshgen chose to
    call it upstream of here.
    """
    key = float(azimuth_deg) % 360.0
    for az, name in _AZIMUTH_TO_SOCKET.items():
        if abs(key - az) < 1e-9:
            return name
    raise MultiviewError(
        f"azimuth {azimuth_deg} is not one of core's four cameras "
        f"({', '.join(f'{n}={a:g}' for n, a in CORE_VIEW_AZIMUTHS.items())})"
    )

#: Suffixes ComfyUI's LoadImage will actually open.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

#: Upstream's shipped multi-view rig uses a 20 degree horizontal FOV for all
#: four frames.  Unlike the single-image path there is no MoGe estimate to make:
#: these are renders/photos on a known orbit, not one found photograph.
DEFAULT_FOV_DEG = 20.0

#: Object half-extent the camera distance is solved for.  Pixal3D pads the
#: subject crop by 1.1 (core's own ``ImageCropToMask`` tooltip: "pad_factor=1.1
#: for Pixal3D"), so the rig frames a half-extent of 0.5 * 1.1 = 0.55.  That
#: constant is not a guess: 0.55 / tan(20 deg / 2) = 3.1192049980163574, which
#: is the camera distance in upstream's shipped transforms.json to the last
#: digit.  Core's SINGLE-view path uses 0.5 (pad 1.0) instead - see
#: Pixal3DConditioning: ``distance = 0.5 / tan(camera_angle_x / 2)``.
PAD_FACTOR = 1.1

#: View counts a multi-view run accepts.  Core's node takes any 1-4 of its
#: optional sockets, but one view is not multi-view: the single-image path
#: estimates the camera FOV with MoGe instead of assuming a 20 degree rig, which
#: is the better answer for one found photograph.  Four is the ceiling because
#: core has four cameras.
#:
#: 2 and 4 are MEASURED on this machine (RTX 5070 12 GB) - see the README's
#: multi-view table.  3 is the same code path with one chain fewer.
SUPPORTED_VIEW_COUNTS = (2, 3, 4)


class MultiviewError(ValueError):
    """A ``views`` request that cannot be honoured as written."""


class MultiviewUnavailable(MultiviewError):
    """The request is well-formed; this machine cannot run it.

    ``.missing`` carries the same records /health uses, so the 400 body can name
    the file, the path, the size and the URL rather than saying "unsupported".
    """

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = list(missing or [])


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------
def normalise_views(views) -> list:
    """Validate a ``views`` request block into an ordered list of view records.

    Returns ``[{"name", "azimuth_deg", "path"}, ...]`` with front at index 0.
    Raises :class:`MultiviewError` with a message the artist can act on.
    """
    if not isinstance(views, dict):
        raise MultiviewError("views must be an object like "
                             '{"front": "C:/a.png", "side": "C:/b.png"}')
    if not views:
        raise MultiviewError("views is empty - give it at least a front image, "
                             "or drop it and use image_path")

    unknown = [k for k in views if k not in VIEW_AZIMUTHS]
    if unknown:
        raise MultiviewError(
            f"unknown view(s): {', '.join(sorted(unknown))}. "
            f"Supported: {', '.join(VIEW_ORDER)} "
            "(side and right are the same 270 degree camera - the object's "
            "right side)"
        )

    if "front" not in views:
        raise MultiviewError(
            "views.front is required - Pixal3D's rig treats the first frame as "
            "the canonical front view and orbits the others from it"
        )
    if "side" in views and "right" in views:
        raise MultiviewError(
            "views.side and views.right are the same 270 degree camera (the "
            "object's right side) - give one"
        )

    records = []
    for name in VIEW_ORDER:
        if name not in views:
            continue
        azimuth = VIEW_AZIMUTHS[name]
        records.append({
            "name": name,
            "azimuth_deg": azimuth,
            "socket": core_socket_for_azimuth(azimuth),
            "path": str(_check_view_path(name, views[name])),
        })
    return records


def _check_view_path(name, raw) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise MultiviewError(f"views.{name} must be an absolute path to an image")
    path = Path(raw)
    if not path.is_absolute():
        raise MultiviewError(f"views.{name} must be absolute, got {raw!r}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise MultiviewError(
            f"views.{name} must be an image ({', '.join(IMAGE_SUFFIXES)}), "
            f"got {path.suffix or 'no extension'}"
        )
    if not path.is_file():
        raise MultiviewError(f"views.{name} not found: {path}")
    return path


# ---------------------------------------------------------------------------
# the camera rig
# ---------------------------------------------------------------------------
def distance_for_fov(fov_deg: float = DEFAULT_FOV_DEG,
                     pad_factor: float = PAD_FACTOR) -> float:
    """Camera distance that frames a unit object at ``fov_deg``.

    ``(0.5 * pad_factor) / tan(fov / 2)``.  At the defaults this is
    3.1192049980163574 - upstream's shipped value, to the last digit.
    """
    return (0.5 * pad_factor) / math.tan(math.radians(fov_deg) / 2.0)


def transform_matrix(azimuth_deg: float, distance: float) -> list:
    """4x4 camera-to-world matrix for one frame of the orbit.

    This is core's ``_PROJ_FRONT_VIEW_TRANSFORM`` rotated about the world up
    axis (+Z) by ``azimuth_deg``.  At azimuth 0 it reproduces that constant
    exactly; at 90/180/270 it reproduces upstream's shipped transforms.json.

    Frame conventions (core's own comment, and upstream's README agree):
    world +Z is up, the front camera sits at ``(0, -d, 0)`` looking at the
    origin, and the camera looks along its local -Z.
    """
    theta = math.radians(azimuth_deg)
    c, s = math.cos(theta), math.sin(theta)
    # Rz(theta) @ FRONT, worked out by hand so this stays stdlib-only.
    return [
        [_z(c), 0.0, _z(s), _z(s * distance)],
        [_z(s), 0.0, _z(-c), _z(-c * distance)],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _z(value: float) -> float:
    """Snap -0.0 and cosine/sine dust to clean values.

    math.cos(math.radians(90)) is 6.1e-17, not 0.  Upstream's transforms.json
    has exact zeros and ones, and a golden-fixture comparison should not have to
    care about the difference.
    """
    if abs(value) < 1e-12:
        return 0.0
    return value


def camera_rig(records, fov_deg: float = DEFAULT_FOV_DEG,
               mesh_scale: float = 1.0) -> dict:
    """Build the ``transforms.json`` upstream's ``inference_mv.py`` reads.

    ``records`` comes from :func:`normalise_views`.  The result is the exact
    schema upstream ships: ``camera_angle_x`` in RADIANS (Blender/NeRF
    convention), ``mesh_scale``, and a ``frames`` list whose first entry is the
    front view.
    """
    distance = distance_for_fov(fov_deg)
    frames = []
    for record in records:
        azimuth = record["azimuth_deg"]
        frames.append({
            "file_path": Path(record["path"]).name,
            "name": f"azim{int(round(azimuth)):03d}",
            "transform_matrix": transform_matrix(azimuth, distance),
        })
    return {
        "camera_angle_x": math.radians(fov_deg),
        "mesh_scale": float(mesh_scale),
        "frames": frames,
    }


def stage_plan(records, fov_deg: float = DEFAULT_FOV_DEG) -> dict:
    """Everything :func:`build_multiview_graph` needs, in a checkable form.

    Which files get staged, in what order, which camera each one is, which core
    socket that camera is, and the rig they share.  Kept separate from the graph
    so the mapping can be asserted without a workflow template, a GPU or weights.

    ``camera_rig`` / ``transform_matrix`` are not sent to ComfyUI - core rebuilds
    the identical rig internally from the connected sockets and ``fov``.  They
    stay because they are the independent check that our socket assignment means
    the camera we think it does, and because they are what upstream's
    ``inference_mv.py`` would consume if the escape hatch is ever needed.
    """
    rig = camera_rig(records, fov_deg=fov_deg)
    return {
        "view_count": len(records),
        "views": [
            {
                "index": index,
                "name": record["name"],
                "socket": record.get("socket") or core_socket_for_azimuth(record["azimuth_deg"]),
                "azimuth_deg": record["azimuth_deg"],
                "path": record["path"],
                "transform_matrix": frame["transform_matrix"],
            }
            for index, (record, frame) in enumerate(zip(records, rig["frames"]))
        ],
        "camera_angle_x_deg": fov_deg,
        "camera_angle_x": rig["camera_angle_x"],
        "camera_distance": distance_for_fov(fov_deg),
        "mesh_scale": rig["mesh_scale"],
        "transforms_json": rig,
    }


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------
#: The sibling of ``image_to_3d.json`` that routes up to four prepared views
#: into ``Pixal3DMultiViewConditioning``.  Generated from the single-view
#: template by ``meshgen/tools/build_multiview_workflow.py`` - never hand-edited,
#: and a test regenerates it and compares.
MULTIVIEW_WORKFLOW = "multiview_to_3d.json"

#: The class name of the conditioning node the multi-view graph is built around.
MULTIVIEW_NODE = "Pixal3DMultiViewConditioning"

#: Node ids inside :data:`MULTIVIEW_WORKFLOW`.  Same rule as the ``NODE_*``
#: constants in ``comfyui_base``: if a template rebuild moves them, one test
#: fails loudly in one place.
NODE_MULTIVIEW_CONDITIONING = "298"   # was Pixal3DConditioning in the single-view graph
NODE_PIXAL3D_UNET = "319"             # must load the MULTI-VIEW checkpoint here

#: core socket -> the nodes that exist only to prepare that socket's image.
#: ``load`` is the ``LoadImage`` whose ``image`` widget gets the staged filename;
#: ``chain`` is the rest of the per-view preparation (background removal, mask,
#: the 1024 / pad-1.1 crop, the pass-through preview ``ImageCropToMask`` feeds).
#: ``front`` reuses the single-view graph's original ids, so the whole
#: single-view contract - including ``NODE_LOAD_IMAGE`` - is untouched.
VIEW_CHAIN_NODES = {
    "front": {"load": "122", "chain": ("192", "248", "303", "312", "302")},
    "left":  {"load": "910", "chain": ("911", "912", "913", "914", "915")},
    "back":  {"load": "920", "chain": ("921", "922", "923", "924", "925")},
    "right": {"load": "930", "chain": ("931", "932", "933", "934", "935")},
}

#: The node in each cloned chain whose output feeds the conditioning socket
#: (the ``PreviewImage`` that ``ImageCropToMask`` passes through - see the
#: README's warning about "preview-looking" nodes being load-bearing here).
VIEW_CHAIN_OUTPUT = {name: nodes["chain"][-1] for name, nodes in VIEW_CHAIN_NODES.items()}


def build_multiview_graph(graph: dict, plan: dict, staged: dict,
                          fov_deg: float = DEFAULT_FOV_DEG) -> dict:
    """Wire a staged multi-view request into the multi-view template.

    ``graph``  - a loaded copy of :data:`MULTIVIEW_WORKFLOW` (all four chains).
    ``plan``   - :func:`stage_plan` output.
    ``staged`` - ``{view name: filename inside ComfyUI's input dir}``.

    Returns the graph with every requested view wired to the socket its AZIMUTH
    says it is, and every chain that was not requested deleted along with its
    socket - core's inputs are optional, and an optional input must be absent
    rather than present-and-dangling.
    """
    graph = copy.deepcopy(graph)
    node = graph.get(NODE_MULTIVIEW_CONDITIONING)
    if node is None or node.get("class_type") != MULTIVIEW_NODE:
        raise MultiviewError(
            f"{MULTIVIEW_WORKFLOW} node {NODE_MULTIVIEW_CONDITIONING} is "
            f"{node and node.get('class_type')!r}, expected {MULTIVIEW_NODE!r} - "
            "regenerate it with meshgen/tools/build_multiview_workflow.py"
        )

    node["inputs"]["fov"] = float(fov_deg)

    wanted = {}
    for view in plan["views"]:
        socket = core_socket_for_azimuth(view["azimuth_deg"])
        if socket in wanted:
            raise MultiviewError(
                f"two views resolve to the same camera ({socket}, "
                f"azimuth {view['azimuth_deg']:g})"
            )
        name = view["name"]
        if name not in staged:
            raise MultiviewError(f"view {name!r} was never staged")
        wanted[socket] = staged[name]

    if "front" not in wanted:
        # normalise_views already guarantees this; assert it at the graph edge
        # too, because core silently re-poses the mesh onto whatever came first.
        raise MultiviewError("the multi-view graph must have a front view")

    for socket, chain in VIEW_CHAIN_NODES.items():
        if socket in wanted:
            graph[chain["load"]]["inputs"]["image"] = wanted[socket]
            node["inputs"][socket] = [VIEW_CHAIN_OUTPUT[socket], 0]
        else:
            node["inputs"].pop(socket, None)
            for node_id in (chain["load"],) + tuple(chain["chain"]):
                graph.pop(node_id, None)
    return graph


def multiview_unet_name() -> str:
    """The checkpoint the multi-view graph must load - not the single-view one."""
    return MULTIVIEW_WEIGHT[1]


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------
def core_multiview_support(config, client=None) -> dict:
    """Does the installed ComfyUI expose a per-view camera node?

    Prefers ``/object_info`` from a running host (the authority); falls back to
    reading the node source, which is how this answers with nothing running and
    no GPU touched.  Returns ``{"node", "checked", "detail"}`` with ``node``
    None when there is no such node.
    """
    if client is not None:
        try:
            if client.is_running():
                info = client.object_info() or {}
                for candidate in MULTIVIEW_NODE_CANDIDATES:
                    if candidate in info:
                        return {"node": candidate, "checked": "object_info",
                                "detail": f"{candidate} is registered on the running ComfyUI"}
                return {"node": None, "checked": "object_info",
                        "detail": "the running ComfyUI registers no multi-view "
                                  "Pixal3D conditioning node"}
        except Exception:
            pass  # a probe must never be the reason a request fails

    source = Path(config.comfyui_root) / "comfy_extras" / "nodes_trellis2.py"
    if not source.is_file():
        return {"node": None, "checked": "unknown",
                "detail": f"could not read {source} to check for a multi-view node"}
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"node": None, "checked": "unknown",
                "detail": f"could not read {source}: {exc}"}
    for candidate in MULTIVIEW_NODE_CANDIDATES:
        if f'node_id="{candidate}"' in text or f"node_id='{candidate}'" in text:
            return {"node": candidate, "checked": "source",
                    "detail": f"{candidate} found in {source.name}"}
    return {
        "node": None,
        "checked": "source",
        "detail": (
            f"{source.name} declares no multi-view conditioning node. Core "
            "gained Pixal3DMultiViewConditioning in v0.35.0 - this install is "
            "older. Before that, Pixal3DConditioning took one scalar FOV and "
            "core's _PROJ_FRONT_VIEW_TRANSFORM was a fixed front-view camera "
            "with no rotation parameter, so a batch of views generated that "
            "many separate objects rather than one fused mesh"
        ),
    }


def multiview_missing(config, client=None) -> list:
    """What stands between this machine and a real multi-view run.

    Entries use the same ``{"what", "path", "source", "bytes"}`` shape as every
    other missing-weight record, so /health and the NotReady message render them
    with no special casing.
    """
    missing = []

    folder, filename, size, url = MULTIVIEW_WEIGHT
    weight_path = Path(config.models_root) / folder / filename
    if not weight_path.is_file():
        missing.append({
            "what": f"{filename} ({size / 2**30:.2f} GB) - multi-view Pixal3D checkpoint",
            "path": str(weight_path),
            "source": url,
            "bytes": size,
            "sha256": MULTIVIEW_WEIGHT_SHA256,
            "needs_approval": True,
            "note": (
                f"NOT downloaded automatically. A separate {size:,} byte file, "
                "and on its own it is not enough - see the node entry."
            ),
        })
    elif weight_path.stat().st_size != size:
        missing.append({
            "what": f"{filename} is {weight_path.stat().st_size} bytes on disk, "
                    f"expected {size} - probably a truncated download",
            "path": str(weight_path),
            "source": url,
            "bytes": size,
            "sha256": MULTIVIEW_WEIGHT_SHA256,
        })

    support = core_multiview_support(config, client)
    if support["node"] is None:
        missing.append({
            "what": (
                "a ComfyUI core node that can express a per-view camera "
                "(none of: " + ", ".join(MULTIVIEW_NODE_CANDIDATES) + ")"
            ),
            "path": str(Path(config.comfyui_root) / "comfy_extras" / "nodes_trellis2.py"),
            "source": "https://github.com/Comfy-Org/ComfyUI - Pixal3DMultiViewConditioning landed in core v0.35.0",
            "bytes": 0,
            "blocker": True,
            "note": support["detail"] + ". Downloading the weights does not fix "
                    "this; update ComfyUI to v0.35.0 or newer.",
        })
    return missing


def unavailable_message(missing) -> str:
    """The 400 body: what is missing, where it goes, and what to do about it."""
    lines = [
        "multi-view generation is not available on this machine.",
        "",
    ]
    for item in missing:
        lines.append(f"  - {item['what']}")
        lines.append(f"      expected at: {item['path']}")
        lines.append(f"      get it from: {item['source']}")
        if item.get("sha256"):
            lines.append(f"      sha256:      {item['sha256']}")
        if item.get("note"):
            lines.append(f"      note:        {item['note']}")
    lines += [
        "",
        "meshgen never downloads weights on its own. If you want the multi-view "
        "checkpoint fetched, say so and approve the size above.",
        "",
        'Meanwhile: send the front image as "image_path" for a normal '
        'single-image generation, or add {"on_unavailable": "front_only"} to '
        "options to have this request fall back to the front view "
        "automatically (the reply says when it did).",
    ]
    return "\n".join(lines)
