"""Multi-view conditioning for Pixal3D: the request surface, the camera rig,
and an honest availability probe.

Phase 18(a).  The intent is "front + side (+ back) images condition one
generation" instead of a single photo guessing the other three sides.

WHAT WORKS TODAY AND WHAT DOES NOT
----------------------------------
Everything in this module is real, tested and verified against upstream *except*
the last mile: **ComfyUI core v0.34.0 cannot run a multi-view Pixal3D graph.**
Two independent things are missing, and only one of them is a download:

1.  The weights.  ``pixal3d_multiview_int8_convrot.safetensors`` is a separate
    5.20 GB checkpoint (:data:`MULTIVIEW_WEIGHT`).  It is NOT on this machine and
    meshgen never fetches it unasked - see the module's readiness reporting.

2.  A node that can express a per-view camera.  This is the real blocker, and a
    download does not fix it.  ``comfy_extras/nodes_trellis2.py`` exposes exactly
    one Pixal3D conditioning node, ``Pixal3DConditioning``, whose only camera
    input is a single scalar ``camera_angle_x``.  The extrinsics it feeds the
    model come from ``comfy/ldm/trellis2/model.py``::

        _PROJ_FRONT_VIEW_TRANSFORM = [[1, 0,  0,  0],
                                      [0, 0, -1, -2],
                                      [0, 1,  0,  0],
                                      [0, 0,  0,  1]]

        def build_proj_transform_matrix(distance, batch_size, ...):
            T = _PROJ_FRONT_VIEW_TRANSFORM.expand(batch_size, -1, -1).clone()
            T[:, 1, 3] = -distance
            return T

    That is a module-level constant with no rotation parameter anywhere.  Every
    element of the batch gets the SAME front-view camera.  Handing the node a
    batch of four images therefore does not fuse four views of one object - it
    generates four independent objects, each wrongly assumed to be a front view
    (``Trellis2ShapeStage`` allocates ``torch.zeros(batch_size, 32, ...)``: one
    latent per batch element, and ``_back_project_to_tokens`` loops over the
    batch writing into disjoint slices).  There is no view-aggregation step in
    core at all.

So this module ships the half that can be built correctly and refuses the half
that cannot, by name.  When ComfyUI core gains a multi-view conditioning node,
:func:`core_multiview_support` finds it and the rest of this file is ready.

THE CAMERA RIG IS VERIFIED, NOT GUESSED
---------------------------------------
:func:`transform_matrix` is derived from core's own ``_PROJ_FRONT_VIEW_TRANSFORM``
by orbiting it about the world up axis, and it reproduces the rig upstream ships
in ``assets/mv_images/example/transforms.json`` **exactly**, to float precision,
at all four azimuths - including the camera distance 3.1192049980163574 at a 20
degree FOV.  ``test_multiview.py`` pins that against the shipped values as a
golden fixture, so this stays true without a GPU, without the weights, and
without network access.
"""

from __future__ import annotations

import math
from pathlib import Path

HF = "https://huggingface.co"

#: The multi-view checkpoint.  ``(folder, filename, bytes, url)`` - the same
#: shape ``comfyui_base`` uses for every other weight, so readiness reporting,
#: /health and the "here is what is missing" message all work unchanged.
#:
#: Size and sha256 read from the Hugging Face API on 2026-09-08.  Note it is
#: byte-for-byte the same SIZE as the single-view int8 checkpoint
#: (5 584 555 824) - the two are the same architecture repacked, which is why
#: core would happily LOAD this file and then silently condition every view as
#: if it were the front.  Downloading it without a per-view camera node would
#: produce confident garbage, not multi-view.
MULTIVIEW_WEIGHT = (
    "diffusion_models",
    "pixal3d_multiview_int8_convrot.safetensors",
    5584555824,
    f"{HF}/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_multiview_int8_convrot.safetensors",
)

#: sha256 of the file above, for the ledger at C:\forge-models\downloads\sha256.txt
MULTIVIEW_WEIGHT_SHA256 = "6b1eb3328930d921d3b57508d9c46cceec1c174dcedeebc14f176ab67d49f477"

#: Node ids that would mean "core can express a per-view camera".  None of these
#: exist in v0.34.0; :func:`core_multiview_support` looks for any of them so the
#: day one lands, meshgen notices instead of needing a code change.
MULTIVIEW_NODE_CANDIDATES = (
    "Pixal3DMultiviewConditioning",
    "Pixal3DMultiViewConditioning",
    "Pixal3DConditioningMultiview",
    "Trellis2MultiviewConditioning",
    "Trellis2MultiViewConditioning",
)

#: View name -> azimuth in degrees around the world up axis (+Z), matching the
#: names upstream gives the four frames of the shipped rig
#: (``azim000`` / ``azim090`` / ``azim180`` / ``azim270``).
#:
#: "side" and "right" are the same camera: upstream has no "side", it has 90
#: degrees, and the request surface in the Phase 18 sketch says ``side``.
VIEW_AZIMUTHS = {
    "front": 0.0,
    "side": 90.0,
    "right": 90.0,
    "back": 180.0,
    "left": 270.0,
}

#: Order views are staged in.  The FIRST frame is load-bearing: upstream's
#: README says "the first frame is the main view and should be the canonical
#: front view", so front is never merely present, it is always index 0.
VIEW_ORDER = ("front", "side", "right", "back", "left")

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

#: Configurations upstream documents.  ``inference_mv.py --num_views N`` takes
#: the first N frames of transforms.json, so any prefix of the 4-view orbit is a
#: supported run; 4 is the only one upstream actually ships an example for.
#: Nothing here has been executed - no weights, no node.  See the README.
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
            "(side and right are the same 90 degree camera)"
        )

    if "front" not in views:
        raise MultiviewError(
            "views.front is required - Pixal3D's rig treats the first frame as "
            "the canonical front view and orbits the others from it"
        )
    if "side" in views and "right" in views:
        raise MultiviewError(
            "views.side and views.right are the same 90 degree camera - give one"
        )

    records = []
    for name in VIEW_ORDER:
        if name not in views:
            continue
        records.append({
            "name": name,
            "azimuth_deg": VIEW_AZIMUTHS[name],
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
    """The deterministic, checkable half of multi-view graph construction.

    Every input a multi-view graph needs that does NOT depend on the shape of a
    node core has not shipped: which files get staged, in what order, which
    camera each one is, and the rig they share.  A future
    ``Pixal3DMultiviewConditioning`` consumes exactly this.

    Deliberately NOT built here: an actual ComfyUI graph.  Wiring N LoadImage
    nodes into a conditioning node whose input names nobody has seen would be
    inventing an interface and calling it verified.
    """
    rig = camera_rig(records, fov_deg=fov_deg)
    return {
        "view_count": len(records),
        "views": [
            {
                "index": index,
                "name": record["name"],
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
            f"{source.name} declares no multi-view conditioning node - "
            "Pixal3DConditioning takes one scalar FOV and core's "
            "_PROJ_FRONT_VIEW_TRANSFORM is a fixed front-view camera with no "
            "rotation parameter, so a batch of views generates that many "
            "separate objects rather than one fused mesh"
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
            "source": "https://github.com/comfyanonymous/ComfyUI - not released as of core v0.34.0",
            "bytes": 0,
            "blocker": True,
            "note": support["detail"] + ". Downloading the weights does not fix "
                    "this; core has nowhere to put the camera.",
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
