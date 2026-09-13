"""The cheap head of the pipeline on its own, so N seeds can be compared.

TRELLIS.2 / Pixal3D run in two halves that meet at one narrow place:

    KSampler(structure) -> VaeDecodeStructureTrellis2 -> VOXEL -> Trellis2ShapeStage

``VaeDecodeStructureTrellis2`` emits a **plain dense boolean occupancy grid**,
``[B, R, R, R]`` at R = 32 or 64 (core: ``decode_structure(...) > 0``, optionally
max-pooled down, ``.squeeze(1).float()``), and ``Trellis2ShapeStage`` consumes it
with ``torch.argwhere(decoded.bool())``.  Everything before that seam is seconds;
everything after it (shape cascade, upsample, texture, remesh, unwrap, bake) is
the two-to-four minute tail.

So the whole point of this module: run **only the head**, N times at N seeds, get
the N grids out, and pay for the tail once.

Why the winner is replayed by SEED rather than re-injected as a grid
-------------------------------------------------------------------
The obvious design is to submit the head as one prompt, capture the VOXEL, and
feed it back into a second prompt that starts at ``Trellis2ShapeStage``.  **Core
cannot express that, and meshgen may not add a node that can.**  Searched across
the whole ComfyUI install: exactly four nodes touch the ``VOXEL`` type -
``VaeDecodeTextureTrellis`` and ``VaeDecodeStructureTrellis2`` produce one,
``Trellis2ShapeStage``/``VoxelToMesh*`` and ``BakeTextureFromVoxel`` consume one.
There is no save node and no load node for it, and writing one would be a custom
node, which meshgen forbids outright (see the nvdiffrast trap in
meshgen/README.md - the standing law that keeps ``custom_nodes/`` empty is what
keeps the licence audit clean).

What core *does* have is a lossless way **out**: ``VoxelToMeshBasic`` emits one
axis-aligned unit cube face per exposed voxel face at integer lattice positions,
and ``SaveGLB`` writes those vertices through untouched (no node matrix, no
rotation - verified in core's ``save_glb``).  That mesh is the grid, exactly, and
:func:`meshgen.ensemble.occupancy_from_cube_mesh` inverts it with no tolerance
fudge.  So the head's output is readable; only the re-entry is impossible.

Hence: the head is a **probe**, and the winner is re-run as part of one ordinary
full generation at the winning seed.  That costs one extra head execution (the
winner's head runs twice) and buys a design that needs nothing core does not
already ship.  It rests on one assumption, which is **measured rather than
asserted** - that the same graph at the same seed decodes the same grid.
``tools/ab_ensemble.py repeat`` is the check, and the number it produced is in
meshgen/README.md.

How the probe graph is built
----------------------------
Not a second committed template: a **pruning** of whichever graph the backend
would have run anyway (single-view or multi-view), by reachability from the
structure decode node, plus a four-node tail.  A template refresh therefore
flows into the probe automatically and the two can never drift, which is a
stronger guarantee than the derived-and-compared one ``multiview_to_3d.json``
gets.  Reachability is the same rule ``tools/build_workflow.py`` uses, and for
the same reason: this template wires ``PreviewImage`` and ``MaskPreview`` as
pass-through DATA nodes, so pruning by "looks like a preview" guts the graph.
"""

from __future__ import annotations

import copy

from .base import BackendError

#: The seam.  ``VaeDecodeStructureTrellis2`` in workflows/image_to_3d.json and in
#: its derived multi-view sibling - the node whose output is the dense grid.
NODE_STRUCTURE_DECODE = "119"
STRUCTURE_DECODE_CLASS = "VaeDecodeStructureTrellis2"

#: The front view's prepared mask, and the crop node whose framing the model
#: actually sees.  Both are already reachable from the structure decode (the
#: conditioning is fed by the cropped image), so the probe gets them for free.
NODE_FRONT_MASK = "303"
NODE_MASK_CROP = "312"

#: Ids for the tail.  Far outside the template's range (its highest is 322) and
#: checked for collision anyway, because a silent overwrite of a template node
#: would produce a graph that runs and means something else.
NODE_VOXEL_TO_MESH = "9001"
NODE_VOXEL_SAVE = "9002"
NODE_MASK_TO_IMAGE = "9003"
NODE_MASK_CROP_CLONE = "9004"
NODE_MASK_SAVE = "9005"

TAIL_IDS = (NODE_VOXEL_TO_MESH, NODE_VOXEL_SAVE,
            NODE_MASK_TO_IMAGE, NODE_MASK_CROP_CLONE, NODE_MASK_SAVE)

#: core's ``VaeDecodeStructureTrellis2.resolution`` combo
GRID_RESOLUTIONS = ("32", "64")

#: ``VoxelToMeshBasic.threshold``.  The grid is exactly 0.0 / 1.0 (core threshold
#: at ``> 0`` and casts to float), so anything strictly inside the interval is
#: the same answer; 0.5 is the value that says that out loud.
VOXEL_THRESHOLD = 0.5


def is_link(value):
    """True for a ComfyUI API-format link, ``["<node id>", <output index>]``."""
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], str))


def reachable(graph: dict, roots) -> set:
    """Every node ``roots`` depends on, transitively, including the roots.

    Reachability from the node that matters is the ONLY safe pruning rule for
    this template - see the module docstring.
    """
    seen = set()
    stack = [str(r) for r in roots]
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in graph:
            continue
        seen.add(node_id)
        for value in (graph[node_id].get("inputs") or {}).values():
            if is_link(value):
                stack.append(value[0])
    return seen


def has_mask_chain(full_graph: dict) -> bool:
    return NODE_FRONT_MASK in full_graph and NODE_MASK_CROP in full_graph


def build_mask_graph(full_graph: dict, prefix: str) -> dict:
    """Just the prepared front silhouette, with no sampler in the graph at all.

    Background removal plus a crop - a second or two, no diffusion model loaded.
    ``best_of`` needs the target silhouette without needing a structure grid, and
    this is the cheapest honest way to get the SAME mask the model was shown
    rather than a mask meshgen re-derived from the caller's file.
    """
    if not has_mask_chain(full_graph):
        raise BackendError(
            f"the graph has no node {NODE_FRONT_MASK}/{NODE_MASK_CROP} - the "
            "prepared-mask chain moved; update backends/structure_probe.py")
    graph = {node_id: copy.deepcopy(node)
             for node_id, node in full_graph.items()
             if node_id in reachable(full_graph, [NODE_FRONT_MASK])}
    _add_mask_tail(graph, full_graph, prefix)
    return graph


def check_tail_ids(full_graph: dict, ids):
    """A silent overwrite of a template node would produce a graph that runs and
    means something else, so the tail's ids are checked against the FULL graph."""
    for node_id in ids:
        if node_id in full_graph:
            raise BackendError(
                f"probe tail id {node_id} collides with a template node - "
                "renumber TAIL_IDS in backends/structure_probe.py")


def _add_mask_tail(graph: dict, full_graph: dict, prefix: str):
    check_tail_ids(full_graph, (NODE_MASK_TO_IMAGE, NODE_MASK_CROP_CLONE,
                                NODE_MASK_SAVE))
    # The prepared mask, put through the SAME crop the model's input image went
    # through, so the silhouette anything is scored against is framed the way
    # the model framed the picture.  Compositing a mask-as-image against itself
    # is exactly ``img * mask`` on the same bounding box, so nothing here
    # reinvents core's framing - it reuses the node that did it.
    crop = copy.deepcopy(full_graph[NODE_MASK_CROP])
    crop["inputs"] = dict(crop.get("inputs") or {})
    crop["inputs"]["images"] = [NODE_MASK_TO_IMAGE, 0]
    crop["inputs"]["masks"] = [NODE_FRONT_MASK, 0]
    crop["_meta"] = {"title": "Crop mask to mask"}

    graph[NODE_MASK_TO_IMAGE] = {
        "class_type": "MaskToImage",
        "_meta": {"title": "Prepared mask as image"},
        "inputs": {"mask": [NODE_FRONT_MASK, 0]},
    }
    graph[NODE_MASK_CROP_CLONE] = crop
    graph[NODE_MASK_SAVE] = {
        "class_type": "SaveImage",
        "_meta": {"title": "Save prepared silhouette"},
        "inputs": {"images": [NODE_MASK_CROP_CLONE, 0],
                   "filename_prefix": f"{prefix}_mask"},
    }


def build_structure_graph(full_graph: dict, prefix: str, resolution="32",
                          want_mask: bool = True) -> dict:
    """Prune ``full_graph`` to the structure head and give it a save tail.

    ``full_graph`` is a finished graph - the one :meth:`ComfyUIBackend.graph_for`
    just produced, with the image staged, the backend switch set and every caller
    option already applied.  Taking it *after* those means the probe is sampling
    the same structure stage the real run will, not an approximation of it.
    """
    resolution = str(resolution)
    if resolution not in GRID_RESOLUTIONS:
        raise BackendError(
            f"structure grid resolution {resolution!r} is not one of "
            f"{', '.join(GRID_RESOLUTIONS)} (core's own combo)")

    decode = full_graph.get(NODE_STRUCTURE_DECODE)
    if decode is None or decode.get("class_type") != STRUCTURE_DECODE_CLASS:
        raise BackendError(
            f"node {NODE_STRUCTURE_DECODE} is "
            f"{(decode or {}).get('class_type')!r}, expected "
            f"{STRUCTURE_DECODE_CLASS!r} - the workflow template was rebuilt and "
            "its node ids moved; update NODE_STRUCTURE_DECODE in "
            "backends/structure_probe.py"
        )

    roots = [NODE_STRUCTURE_DECODE]
    mask = want_mask and has_mask_chain(full_graph)
    if mask:
        roots.append(NODE_FRONT_MASK)

    keep = reachable(full_graph, roots)
    graph = {node_id: copy.deepcopy(node) for node_id, node in full_graph.items()
             if node_id in keep}
    graph[NODE_STRUCTURE_DECODE]["inputs"]["resolution"] = resolution

    check_tail_ids(full_graph, TAIL_IDS)

    graph[NODE_VOXEL_TO_MESH] = {
        "class_type": "VoxelToMeshBasic",
        "_meta": {"title": "Structure grid to cubes"},
        "inputs": {"voxel": [NODE_STRUCTURE_DECODE, 0],
                   "threshold": VOXEL_THRESHOLD},
    }
    graph[NODE_VOXEL_SAVE] = {
        "class_type": "SaveGLB",
        "_meta": {"title": "Save structure grid"},
        "inputs": {"mesh": [NODE_VOXEL_TO_MESH, 0],
                   "filename_prefix": f"{prefix}_grid"},
    }

    if mask:
        _add_mask_tail(graph, full_graph, prefix)
    return graph


def find_images(entry: dict, node_id: str, config):
    """Absolute paths of the images one node reported in a history entry.

    ``SaveImage`` emits the ``{"filename", "subfolder", "type"}`` dict form; the
    bare-string form ``Save3DAdvanced`` uses never appears here.
    """
    outputs = (entry.get("outputs") or {}).get(str(node_id)) or {}
    found = []
    for items in outputs.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or "filename" not in item:
                continue
            base = config.output_dir
            if item.get("type") == "temp":
                base = config.comfyui_dir / "temp"
            found.append(base / (item.get("subfolder") or "") / str(item["filename"]))
    return found
