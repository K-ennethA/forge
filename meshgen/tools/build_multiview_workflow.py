"""Derive workflows/multiview_to_3d.json from workflows/image_to_3d.json.

Phase 18(a).  The multi-view graph is NOT a second hand-maintained template - it
is the official single-view template with the per-view preparation chain cloned
three more times and ``Pixal3DConditioning`` swapped for core's
``Pixal3DMultiViewConditioning`` (ComfyUI core v0.35.0+).  Deriving it means a
template refresh only has to be done once, and ``test_multiview.py`` regenerates
it in-memory and compares, so the two can never drift.

What the transform does, and why each step is needed:

1.  **Clone the preparation chain per view.**  One view's preparation in the
    official template is::

        LoadImage -> RemoveBackground -> ComfySwitchNode(mask)
                  -> MaskPreview -> ImageCropToMask(1024, pad_factor=1.1)
                  -> PreviewImage -> conditioning

    ``MaskPreview`` and ``PreviewImage`` are wired as pass-through DATA nodes in
    this template, not as previews (see meshgen/README.md) - cloning must keep
    them.  ``LoadBackgroundRemovalModel`` is shared by all four chains; the
    model is loaded once.

    The crop is already 1024 square at ``pad_factor=1.1``, which is exactly the
    framing ``Pixal3DMultiViewConditioning`` documents ("the object spans about
    1/1.1 of the frame at its widest") and the padding its own ``_VIEW_PAD``
    constant uses.  So the existing single-view preparation feeds all four
    sockets unchanged - nothing about the crop contract had to move.

2.  **Swap the conditioning node.**  ``Pixal3DConditioning(clip_vision, image,
    camera_angle_x)`` becomes ``Pixal3DMultiViewConditioning(clip_vision, fov,
    front?, left?, back?, right?)``.  Same two outputs (positive, negative), so
    the switch nodes downstream are untouched.

3.  **Drop MoGe.**  Its only consumer was ``Pixal3DConditioning``'s per-image FOV
    estimate.  Multi-view rig renders have a known FOV (20 degrees), so the
    ``LoadMoGeModel -> MoGeInference -> MoGeGeometryToFOV`` branch becomes
    unreachable.  Deleting it is what lets a multi-view run skip the 0.62 GB
    MoGe weight and its VRAM entirely.

4.  **Point the Pixal3D UNETLoader at the multi-view checkpoint.**  This is the
    step that must never be forgotten: the two checkpoints are byte-for-byte the
    same size and the same architecture repacked, so the single-view one loads
    without complaint and then conditions every view as if it were the front.

Regenerate after a template refresh::

    service\\.venv\\Scripts\\python.exe -m meshgen.tools.build_multiview_workflow
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meshgen.backends import multiview  # noqa: E402

#: The single-view ids of one preparation chain, in the order they must be
#: cloned.  Keys are the template's ids; the values in VIEW_CHAIN_NODES supply
#: the clone's ids.  ``122`` is the LoadImage; the rest is ``chain``.
_TEMPLATE_CHAIN = ("122", "192", "248", "303", "312", "302")

#: (id, class_type) pairs the transform depends on.  If a template rebuild moves
#: any of them this refuses to write rather than producing a plausible graph.
_EXPECTED = {
    "122": "LoadImage",
    "192": "RemoveBackground",
    "193": "LoadBackgroundRemovalModel",
    "248": "ComfySwitchNode",
    "303": "MaskPreview",
    "312": "ImageCropToMask",
    "302": "PreviewImage",
    "298": "Pixal3DConditioning",
    "319": "UNETLoader",
    "55": "LoadMoGeModel",
    "56": "MoGeInference",
    "242": "MoGeGeometryToFOV",
}

#: Nodes that exist only to feed Pixal3DConditioning's camera_angle_x.
_MOGE_NODES = ("55", "56", "242")


class BuildError(RuntimeError):
    pass


def _check(graph):
    for node_id, class_type in _EXPECTED.items():
        got = (graph.get(node_id) or {}).get("class_type")
        if got != class_type:
            raise BuildError(
                f"node {node_id} is {got!r}, expected {class_type!r} - "
                "image_to_3d.json was rebuilt and its node ids moved; update "
                "_EXPECTED here and VIEW_CHAIN_NODES in backends/multiview.py"
            )
    taken = set(graph)
    for socket, nodes in multiview.VIEW_CHAIN_NODES.items():
        if socket == "front":
            continue
        for new_id in (nodes["load"],) + tuple(nodes["chain"]):
            if new_id in taken:
                raise BuildError(f"clone id {new_id} collides with a template node")


def _clone_chain(graph, socket):
    """Clone one preparation chain under the ids VIEW_CHAIN_NODES reserves."""
    nodes = multiview.VIEW_CHAIN_NODES[socket]
    remap = dict(zip(_TEMPLATE_CHAIN, (nodes["load"],) + tuple(nodes["chain"])))
    for old_id, new_id in remap.items():
        clone = copy.deepcopy(graph[old_id])
        for key, value in clone.get("inputs", {}).items():
            # a link is ["<source node id>", <output index>]
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                clone["inputs"][key] = [remap.get(value[0], value[0]), value[1]]
        meta = clone.get("_meta") or {}
        if meta.get("title"):
            meta = dict(meta, title=f"{meta['title']} ({socket})")
            clone["_meta"] = meta
        graph[new_id] = clone


def build(single_view: dict) -> dict:
    """The whole transform, as a pure function so a test can re-run it."""
    graph = copy.deepcopy(single_view)
    _check(graph)

    for socket in multiview.VIEW_CHAIN_NODES:
        if socket != "front":
            _clone_chain(graph, socket)

    clip_vision = graph["298"]["inputs"]["clip_vision_model"]
    graph[multiview.NODE_MULTIVIEW_CONDITIONING] = {
        "class_type": multiview.MULTIVIEW_NODE,
        "_meta": {"title": "Pixal3D Multi-View Conditioning"},
        "inputs": {
            "clip_vision_model": clip_vision,
            "fov": multiview.DEFAULT_FOV_DEG,
            **{socket: [multiview.VIEW_CHAIN_OUTPUT[socket], 0]
               for socket in multiview.CORE_VIEW_AZIMUTHS},
        },
    }

    for node_id in _MOGE_NODES:
        graph.pop(node_id, None)

    graph[multiview.NODE_PIXAL3D_UNET]["inputs"]["unet_name"] = multiview.multiview_unet_name()
    return graph


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    workflows = _ROOT / "meshgen" / "workflows"
    source = Path(argv[0]) if argv else workflows / "image_to_3d.json"
    target = Path(argv[1]) if len(argv) > 1 else workflows / multiview.MULTIVIEW_WORKFLOW

    single_view = json.loads(source.read_text(encoding="utf-8"))
    graph = build(single_view)
    target.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"{target}: {len(graph)} nodes "
          f"({len(single_view)} in {source.name}, "
          f"{len(multiview.CORE_VIEW_AZIMUTHS)} views)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
