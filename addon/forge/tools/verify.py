"""``verify_design`` and ``turntable`` — the geometric half of the judgement.

Why this file exists
--------------------
``render_preview`` and ``capture_viewport`` are the assistant's eyes, and
``mesh_diagnose`` counts the defects behind them.  Together they were still only
half a gate, and the measured half was the smaller one: 123 000 human votes say
render-based judging systematically rewards visual impact over downstream
utility (+144 ELO for textured over untextured; *the same model* scores 78 ELO
higher presented as a splat than as a mesh), and ~26% of VLM A/B judgements
reverse when you swap which candidate is shown first.  A loop that only looks is
a loop that can be fooled by prettiness.

So: **renders judge beauty, ``verify_design`` judges truth, and both have to
pass.**  This module is the second gate — one scored report over the things a
picture cannot tell you:

* every defect ``mesh_diagnose`` already finds (it is composed in verbatim, not
  reimplemented — one measurement, one implementation);
* **poly budget** against a target, so "game-ready" is a number and not a mood;
* **UV metrics** when the mesh has UVs — island count, area distortion, flipped
  faces, out-of-bounds loops, an overlap estimate;
* **symmetry residual** — how far the mesh is from its own mirror, in
  millimetres, *reported and never judged*: asymmetry is usually a decision;
* **edge-loop density in deformation zones** — how many loops cross each joint,
  read off an armature when there is one and off the tag boundaries when there
  is not, because RigForge already knows where the joints are;
* **silhouette IoU against a reference image**, which is the only measurement in
  here that answers "is it the shape of the thing they asked for".

Credibility tiering
-------------------
Every claim in the report carries a ``tier``.  Two values, and the distinction is
load-bearing rather than decorative:

* ``measured`` — a computed number with a definition. Face count, island count,
  the count of flipped UV faces, the mirror distance in millimetres. If it is
  wrong, the code is wrong.
* ``heuristic`` — a number that required a judgement call to compute: which
  pixels of a photograph are "the object", whether a cluster of vertices is an
  "edge loop", whether summed UV area exceeding rasterised coverage means the
  islands overlap. Real information, wrong to quote as fact.

The pattern is stolen verbatim from the competitive sweep (`finding < prediction
< proxy < executed-solver`), collapsed to the two tiers this module can actually
distinguish.  A report that presented the silhouette IoU of a background-removed
photograph with the same confidence as a face count would be a worse report than
one that omitted it.

Read-only, always
-----------------
``verify_design`` measures and ``turntable`` renders; neither changes the
.blend.  Both borrow render settings and a camera exactly the way
``render_preview`` does and put every one of them back, so both are in
``READ_ONLY_COMMANDS`` and neither costs the artist an undo step.
"""

import math
import os
import tempfile
import time

import bpy

from . import common
from . import diagnose
from .registry import ForgeError, command

try:  # Blender ships numpy; the guards keep this honest if a build ever does not.
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is part of Blender
    _np = None

__all__ = [
    "MEASURED",
    "HEURISTIC",
    "PROFILES",
    "TURNTABLE_VIEWS",
    "claim",
    "uv_metrics",
    "symmetry_residual",
    "loop_density",
    "silhouette_iou",
    "verdict_lines",
]


# ---------------------------------------------------------------------------
# credibility tiers
# ---------------------------------------------------------------------------

#: A computed number with a definition behind it.
MEASURED = "measured"

#: A number that took a judgement call to compute. Real information; not a fact.
HEURISTIC = "heuristic"


def claim(value, tier, note=None, **extra):
    """One reportable number wearing its credibility tier.

    Every leaf in the report goes through here, so there is no way to add a
    finding that forgot to say how much it should be trusted.
    """
    entry = {"value": value, "tier": tier}
    if note:
        entry["note"] = note
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# profiles — what "good" means depends on where the mesh is going
# ---------------------------------------------------------------------------
#
# `for` is not a filter over the same report: it decides which axes are GATED
# (they can say "attention") and which are merely reported.  A print does not
# care how many UV islands it has; a game character does not care whether it is
# watertight in the printing sense, and it certainly does not care about wall
# thickness.
#
# The print profile deliberately gates almost nothing here.  Print readiness is
# already a solved, deterministic pipeline — `partforge_check` / `check_model`
# against the real printer profile — and a second, weaker opinion about wall
# thickness computed from a bounding box would be worse than no opinion.  So the
# print profile measures what it can measure honestly and POINTS at the tool
# that actually answers the question.

PROFILES = {
    "game": {
        "gates": ("defects", "poly_budget", "uv", "loops", "silhouette"),
        "poly_budget": 15000,  # rigforge PLATFORM_TARGETS["desktop"]
        "note": ("Game profile: edge loops, UVs and the polygon budget are "
                 "gated; symmetry is reported, never judged."),
    },
    "print": {
        "gates": ("defects", "silhouette"),
        "poly_budget": 0,
        "note": ("Print profile: only defects and silhouette are gated here. "
                 "Bed fit, wall thickness and overhangs are partforge_check / "
                 "check_model's question against the real printer profile — "
                 "run one of those, this does not duplicate them."),
    },
    "any": {
        "gates": ("defects", "silhouette"),
        "poly_budget": 0,
        "note": ("No target declared, so only the universal axes are gated: "
                 "defects, and the silhouette when a reference was given. Pass "
                 "for='game' or for='print' for the rest."),
    },
}

#: Below this many faces a "game asset" is more likely a blockout than a
#: finished mesh, and quoting a budget verdict on it would be noise.
POLY_FLOOR = 24

#: A mesh is called "near-symmetric" when its mirror residual is under this
#: fraction of its own bounding diagonal.  Above it, symmetry was a decision
#: rather than an accident and the residual is a fact, not a fault.
SYMMETRY_NEAR = 0.02

#: Deformation zones want at least this many edge loops crossing them. Three is
#: the artist's own rule of thumb (one at the joint, one either side), and it is
#: the number below which a bend creases instead of rolling.
LOOPS_WANTED = 3

#: UV area within this factor of an even distribution is not worth mentioning.
#: 2x is roughly the point at which a checker pattern visibly changes size.
UV_DISTORTION_OK = 2.0

#: How many points the symmetry KD-tree holds before it starts subsampling.
#: One Python insert per point is the cost, and a 500k-vertex sculpt would spend
#: seconds on a number that does not change in the fourth decimal place.
SYMMETRY_TREE_LIMIT = 120000


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------

def _round(value, places=4):
    try:
        return round(float(value), places)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _local_coords(mesh):
    """``(n, 3)`` array of the mesh's own vertex positions."""
    count = len(mesh.vertices)
    if _np is not None:
        flat = _np.empty(count * 3, dtype="f8")
        mesh.vertices.foreach_get("co", flat)
        return flat.reshape(count, 3)
    return [tuple(v.co) for v in mesh.vertices]  # pragma: no cover


def _to_world(coords, matrix):
    """Local coordinates as world coordinates, vectorised where numpy exists."""
    if _np is None:  # pragma: no cover - numpy is part of Blender
        return [matrix @ common.Vector(tuple(c)) for c in coords]
    rotation = _np.array([[matrix[r][c] for c in range(3)] for r in range(3)],
                         dtype="f8")
    translation = _np.array([matrix[r][3] for r in range(3)], dtype="f8")
    return coords @ rotation.T + translation


# ---------------------------------------------------------------------------
# UV metrics
# ---------------------------------------------------------------------------

#: Above this many faces the UV island walk (a Python pass over every loop) costs
#: more than it is worth on a timer, so it is skipped and SAYS so — the same
#: contract mesh_diagnose's self-intersection cap signs. Every other UV number is
#: vectorised and runs regardless.
UV_FACE_LIMIT = 200000


def _uv_array(mesh, layer, loop_count):
    """``(loops, 2)`` UV array, whichever API this Blender exposes.

    Blender 4.1 moved UVs onto a float2 attribute (``layer.uv[i].vector``) while
    keeping the older loop collection (``layer.data[i].uv``) alive beside it, and
    which one is present has changed twice. Both are tried, cheapest first.
    """
    flat = _np.empty(loop_count * 2, dtype="f8")
    for owner, prop in ((getattr(layer, "uv", None), "vector"),
                        (getattr(layer, "data", None), "uv")):
        if owner is None:
            continue
        try:
            if len(owner) != loop_count:
                continue
            owner.foreach_get(prop, flat)
            return flat.reshape(loop_count, 2)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    data = getattr(layer, "data", None)  # pragma: no cover - last resort
    if data is None:
        raise ForgeError("Could not read the UV layer %r off this mesh."
                         % getattr(layer, "name", "?"))
    return _np.array([list(entry.uv) for entry in data], dtype="f8")


def uv_metrics(mesh):
    """Island count, area distortion, flips, out-of-bounds loops, overlap.

    Everything here comes off the mesh's own UV loop layer, so it needs no
    unwrap, no bake and no operator — it is a read of numbers that are already
    there.  Returns ``None`` when the mesh has no UVs at all, which is a
    different answer from "the UVs are bad" and is reported as such.

    The measurements, and what each one is worth:

    * **islands** — connected components over faces that share an edge whose two
      loop UVs agree on both sides.  That is the same definition Blender's own
      packer uses, so the count matches what the artist sees in the UV editor.
      ``measured``.
    * **flipped faces** — faces whose UV winding is opposite the majority.  A
      flipped face renders its texture mirrored, and it is a real defect rather
      than a preference.  ``measured``.
    * **out-of-bounds loops** — UVs outside the 0..1 square.  Legal (UDIM, tiled
      trims) and therefore reported rather than failed, but a surprise if it was
      not intended.  ``measured``.
    * **area distortion** — per face, the ratio of its share of UV area to its
      share of surface area.  1.0 is perfect; the p95 of the ratio is the number
      worth quoting ("some faces get 4x the texture density of others").
      ``measured``.
    * **overlap** — summed UV face area against rasterised coverage on a fixed
      grid.  Overlapping islands count their area twice while covering it once,
      so the excess is the estimate.  A raster is an approximation and small
      excesses are quantisation, so this one is ``heuristic`` and it says so.
    """
    if _np is None:  # pragma: no cover - numpy is part of Blender
        return None
    layers = getattr(mesh, "uv_layers", None)
    if not layers or not len(layers):
        return None
    layer = layers.active or layers[0]
    loop_count = len(mesh.loops)
    face_count = len(mesh.polygons)
    if not loop_count or not face_count:
        return None

    uvs = _uv_array(mesh, layer, loop_count)

    def uv_at(index):
        return (float(uvs[index][0]), float(uvs[index][1]))

    starts = _np.empty(face_count, dtype="i4")
    totals = _np.empty(face_count, dtype="i4")
    areas = _np.empty(face_count, dtype="f8")
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    mesh.polygons.foreach_get("area", areas)
    loop_vertices = _np.empty(loop_count, dtype="i4")
    mesh.loops.foreach_get("vertex_index", loop_vertices)

    # --- per-face signed UV area (the shoelace formula) -------------------
    signed_areas = []
    for index in range(face_count):
        start = int(starts[index])
        total = int(totals[index])
        area = 0.0
        for i in range(total):
            ax, ay = uv_at(start + i)
            bx, by = uv_at(start + (i + 1) % total)
            area += ax * by - bx * ay
        signed_areas.append(area / 2.0)
    face_areas = [float(v) for v in areas]

    if not signed_areas:
        return None

    positive = sum(1 for a in signed_areas if a > 0.0)
    negative = sum(1 for a in signed_areas if a < 0.0)
    # The minority winding is the flipped one. A mesh whose UVs are ALL wound
    # the other way is not flipped, it is a convention — so the count is the
    # smaller of the two, never "everything negative".
    flipped = min(positive, negative)
    degenerate = sum(1 for a in signed_areas if a == 0.0)

    uv_total = sum(abs(a) for a in signed_areas)
    mesh_total = sum(face_areas)

    # --- area distortion --------------------------------------------------
    ratios = []
    if uv_total > 0.0 and mesh_total > 0.0:
        for uv_area, face_area in zip(signed_areas, face_areas):
            if face_area <= 0.0:
                continue
            share_uv = abs(uv_area) / uv_total
            share_mesh = face_area / mesh_total
            if share_mesh <= 0.0:
                continue
            ratios.append(share_uv / share_mesh)

    distortion = {}
    if ratios:
        ordered = sorted(ratios)
        median = ordered[len(ordered) // 2]
        p05 = ordered[max(0, int(0.05 * (len(ordered) - 1)))]
        p95 = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))]
        worst = max(p95, (1.0 / p05) if p05 > 1e-9 else p95)
        distortion = {
            "median": _round(median, 3),
            "p05": _round(p05, 3),
            "p95": _round(p95, 3),
            "worst_factor": _round(worst, 2),
        }

    # --- out of the 0..1 square ------------------------------------------
    outside = (uvs < -1e-6) | (uvs > 1.0 + 1e-6)
    out_of_bounds = int(_np.count_nonzero(outside.any(axis=1)))

    # --- islands ----------------------------------------------------------
    if face_count > UV_FACE_LIMIT:
        islands, island_note = None, (
            "not counted: %d faces is above the %d-face budget for the island "
            "walk. Every other UV number here still ran."
            % (face_count, UV_FACE_LIMIT)
        )
    else:
        islands, island_note = _uv_islands(starts, totals, loop_vertices, uv_at,
                                           face_count)

    # --- overlap estimate -------------------------------------------------
    overlap, overlap_note = _uv_overlap(starts, totals, uv_at, face_count,
                                        uv_total)

    result = {
        "layer": layer.name,
        "layers": [entry.name for entry in layers],
        "islands": claim(islands, MEASURED,
                         island_note or "connected faces sharing matched loop UVs"),
        "flipped_faces": claim(flipped, MEASURED,
                               "faces wound against the majority — their texture "
                               "renders mirrored"),
        "degenerate_faces": claim(degenerate, MEASURED,
                                  "faces with no UV area at all"),
        "out_of_bounds_loops": claim(out_of_bounds, MEASURED,
                                     "UVs outside the 0..1 square — legal for "
                                     "UDIM/tiled work, a surprise otherwise"),
        "area_distortion": claim(distortion.get("worst_factor"), MEASURED,
                                 "worst texture-density ratio against an even "
                                 "layout; 1.0 is perfect",
                                 detail=distortion),
        "overlap": claim(overlap, HEURISTIC, overlap_note),
        "coverage": claim(_round(uv_total, 4), MEASURED,
                          "total UV area (1.0 fills the square exactly once)"),
    }
    return result


def _uv_islands(starts, totals, loop_vertices, uv_at, face_count):
    """Connected components of faces joined across UV-matched edges.

    Two faces are in the same island when they share a mesh edge AND both
    faces' UVs agree at both of its ends — which is exactly Blender's own
    definition, so the count matches what the artist sees in the UV editor. A
    disagreement at either end is a seam.
    """
    if not face_count:
        return 0, None

    # edge key -> list of (face index, uv at a, uv at b, walked forwards?)
    edge_loops = {}
    for index in range(face_count):
        start = int(starts[index])
        total = int(totals[index])
        for i in range(total):
            loop_index = start + i
            next_index = start + (i + 1) % total
            a = int(loop_vertices[loop_index])
            b = int(loop_vertices[next_index])
            key = (a, b) if a < b else (b, a)
            edge_loops.setdefault(key, []).append(
                (index, uv_at(loop_index), uv_at(next_index), a < b)
            )

    parent = list(range(face_count))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    tolerance = 1e-6
    for entries in edge_loops.values():
        if len(entries) < 2:
            continue
        for i in range(len(entries)):
            face_a, a0, a1, forward_a = entries[i]
            # Orient both edges the same way round before comparing, or every
            # shared edge looks like a seam because the two faces walk it in
            # opposite directions.
            pa = (a0, a1) if forward_a else (a1, a0)
            for j in range(i + 1, len(entries)):
                face_b, b0, b1, forward_b = entries[j]
                pb = (b0, b1) if forward_b else (b1, b0)
                if (abs(pa[0][0] - pb[0][0]) < tolerance
                        and abs(pa[0][1] - pb[0][1]) < tolerance
                        and abs(pa[1][0] - pb[1][0]) < tolerance
                        and abs(pa[1][1] - pb[1][1]) < tolerance):
                    union(face_a, face_b)

    return len({find(i) for i in range(face_count)}), None


#: Coverage raster for the overlap estimate. 512 is fine enough that an ordinary
#: island's quantisation error stays under a percent and coarse enough to build
#: in milliseconds.
UV_RASTER = 512

#: Summed UV area has to exceed rasterised coverage by more than this fraction
#: before overlap is called. Below it the excess is the raster's own edge
#: quantisation, and saying "overlap" would be crying wolf on every clean unwrap.
UV_OVERLAP_SLACK = 0.08


def _uv_overlap(starts, totals, uv_at, face_count, uv_total):
    """Do the islands sit on top of each other? Summed area vs covered area.

    Overlapping islands count their area twice and cover it once, so an excess
    of summed-over-covered is the estimate.  It is an estimate: a raster
    quantises every island's edge, so a clean unwrap comes out a few percent
    over and the slack absorbs that.  ``heuristic``, and the note says why.
    """
    if uv_total <= 0.0 or _np is None:
        return None, ("not estimated: this build has no numpy, so there is no "
                      "coverage raster to compare against")

    grid = _np.zeros((UV_RASTER, UV_RASTER), dtype=bool)
    cell = 1.0 / UV_RASTER
    seen_any = False
    for index in range(face_count):
        start = int(starts[index])
        total = int(totals[index])
        us = []
        vs = []
        for i in range(total):
            u, v = uv_at(start + i)
            us.append(u)
            vs.append(v)
        # Bounding-box fill per face. It over-counts a triangle's corner cells,
        # which pushes the estimate toward "overlap" rather than away from it —
        # the wrong direction for a false negative, so the slack is generous.
        lo_u = max(0, min(UV_RASTER - 1, int(math.floor(min(us) / cell))))
        hi_u = max(0, min(UV_RASTER - 1, int(math.ceil(max(us) / cell)) - 1))
        lo_v = max(0, min(UV_RASTER - 1, int(math.floor(min(vs) / cell))))
        hi_v = max(0, min(UV_RASTER - 1, int(math.ceil(max(vs) / cell)) - 1))
        if hi_u < lo_u or hi_v < lo_v:
            continue
        grid[lo_v:hi_v + 1, lo_u:hi_u + 1] = True
        seen_any = True

    if not seen_any:
        return None, "not estimated: no face landed inside the 0..1 square"

    covered = float(_np.count_nonzero(grid)) * cell * cell
    if covered <= 0.0:
        return None, "not estimated: nothing covered on the raster"
    excess = (uv_total / covered) - 1.0
    overlapping = excess > UV_OVERLAP_SLACK
    note = ("summed UV area is %.0f%% %s the rasterised coverage at %dx%d — "
            "%s. A raster quantises every island edge, so anything under %.0f%% "
            "is counted as clean."
            % (abs(excess) * 100.0,
               "above" if excess >= 0 else "below",
               UV_RASTER, UV_RASTER,
               "the islands very likely overlap" if overlapping
               else "consistent with no overlap",
               UV_OVERLAP_SLACK * 100.0))
    return bool(overlapping), note


# ---------------------------------------------------------------------------
# symmetry residual — REPORTED, never judged
# ---------------------------------------------------------------------------

def symmetry_residual(mesh, axis="X", samples=4000):
    """How far this mesh is from its own mirror, in millimetres.

    For each sampled vertex, mirror it across the axis plane through the mesh's
    own bounding-box centre and measure the distance to the nearest real vertex.
    Mean, p95 and max come back, plus the same numbers as a fraction of the
    bounding diagonal so "is this near-symmetric" is answerable on a model of any
    size.

    **This is reported and never judged.** A gesture, a swept tail, a hand on a
    hip and a deliberately asymmetric silhouette all produce a large residual and
    all of them are correct.  What the number is *for* is the other case: a mesh
    the artist believes is mirrored, where a residual of 4 mm on one ear is a
    symmetrize that did not take.  Only they can tell those apart, so the report
    hands over the number and the comparison and stops.

    ``measured`` — a nearest-neighbour distance is a distance.  The sampling
    cap is stated in the result rather than applied silently.
    """
    count = len(mesh.vertices)
    if count < 4:
        return None

    index = {"X": 0, "Y": 1, "Z": 2}.get(str(axis).upper().lstrip("+-"), 0)
    coords = _local_coords(mesh)

    from mathutils.kdtree import KDTree

    # The tree is the expensive half (one Python insert per point), so a very
    # dense sculpt is subsampled into it — uniformly, and the result says how
    # many points it actually held. A subsampled tree can only OVERSTATE the
    # residual (the true mirror partner may not be in it), never understate it,
    # which is the safe direction for a number nobody is allowed to fail on.
    tree_step = max(1, count // SYMMETRY_TREE_LIMIT)
    tree_points = list(range(0, count, tree_step))
    tree = KDTree(len(tree_points))
    for slot, i in enumerate(tree_points):
        tree.insert(common.Vector((float(coords[i][0]), float(coords[i][1]),
                                   float(coords[i][2]))), slot)
    tree.balance()

    if _np is not None:
        low = coords.min(axis=0)
        high = coords.max(axis=0)
    else:  # pragma: no cover
        low = [min(c[i] for c in coords) for i in range(3)]
        high = [max(c[i] for c in coords) for i in range(3)]
    centre = [(float(low[i]) + float(high[i])) / 2.0 for i in range(3)]
    diagonal = math.sqrt(sum((float(high[i]) - float(low[i])) ** 2
                             for i in range(3))) or 1e-9

    step = max(1, count // max(1, int(samples)))
    sampled = 0
    distances = []
    for i in range(0, count, step):
        point = [float(coords[i][0]), float(coords[i][1]), float(coords[i][2])]
        point[index] = 2.0 * centre[index] - point[index]
        _, _, distance = tree.find(common.Vector(point))
        if distance is None:
            continue
        distances.append(float(distance))
        sampled += 1

    if not distances:
        return None

    distances.sort()
    mean = sum(distances) / len(distances)
    p95 = distances[min(len(distances) - 1, int(0.95 * (len(distances) - 1)))]
    worst = distances[-1]
    ratio = mean / diagonal

    return {
        "axis": str(axis).upper().lstrip("+-"),
        "mean_mm": claim(_round(mean * common.M_TO_MM, 3), MEASURED,
                         "average distance from a mirrored vertex to the "
                         "nearest real one"),
        "p95_mm": claim(_round(p95 * common.M_TO_MM, 3), MEASURED),
        "max_mm": claim(_round(worst * common.M_TO_MM, 3), MEASURED),
        "fraction_of_size": claim(_round(ratio, 5), MEASURED,
                                  "mean residual as a fraction of the bounding "
                                  "diagonal — comparable across model sizes"),
        "near_symmetric": claim(bool(ratio < SYMMETRY_NEAR), HEURISTIC,
                                "under %g%% of the diagonal is 'near-symmetric'; "
                                "the threshold is a convention, not a law"
                                % (SYMMETRY_NEAR * 100.0)),
        "sampled_vertices": sampled,
        "tree_vertices": len(tree_points),
        "total_vertices": count,
        "reported_not_judged": True,
        "note": ("Asymmetry is usually a decision — a gesture, a swept tail, a "
                 "hand on a hip. This number is here so a symmetrize that did "
                 "NOT take is visible, and nothing else. Never call it a fault."),
    }


# ---------------------------------------------------------------------------
# edge-loop density near deformation zones
# ---------------------------------------------------------------------------

def _armature_joints(obj):
    """``[(name, world head, world axis)]`` for every deforming bone joint."""
    armature = None
    for modifier in getattr(obj, "modifiers", ()) or ():
        if getattr(modifier, "type", "") == "ARMATURE" and modifier.object:
            armature = modifier.object
            break
    if armature is None:
        parent = getattr(obj, "parent", None)
        if parent is not None and getattr(parent, "type", "") == "ARMATURE":
            armature = parent
    if armature is None or getattr(armature.data, "bones", None) is None:
        return [], None

    matrix = armature.matrix_world
    joints = []
    for bone in armature.data.bones:
        if not getattr(bone, "use_deform", True):
            continue
        if bone.parent is None:
            continue  # the root is not a place anything bends
        head = matrix @ bone.head_local
        tail = matrix @ bone.tail_local
        axis = tail - head
        if axis.length < 1e-9:
            continue
        joints.append((bone.name, head, axis.normalized(), axis.length))
    return joints, armature.name


def _tag_joints(obj):
    """Joints inferred from RigForge tag boundaries.

    A tag boundary IS a deformation zone: the ring of geometry where the head
    stops and the neck starts is exactly where the mesh has to bend.  RigForge
    already asked the artist to draw those boundaries, so when there is no
    armature yet this reads the same information rather than guessing.
    """
    try:
        from . import rigforge
    except ImportError:  # pragma: no cover - rigforge is always present
        return [], None

    groups = rigforge.tag_groups(obj)
    if len(groups) < 2:
        return [], None

    vertex_map = rigforge._vertex_tag_map(obj)
    if not vertex_map:
        return [], None

    matrix = obj.matrix_world
    names = {group.index: rigforge.tag_display_name(group.name) for group in groups}

    # Where each tag's own mass sits — the axis a joint bends around is the line
    # between the two regions it joins.
    centroids = {}
    counts = {}
    for index, marks in vertex_map.items():
        position = obj.data.vertices[index].co
        for group_index in marks:
            accumulator = centroids.setdefault(group_index,
                                               common.Vector((0.0, 0.0, 0.0)))
            accumulator += position
            centroids[group_index] = accumulator
            counts[group_index] = counts.get(group_index, 0) + 1

    # Two ways a pair of tags can meet, and BOTH happen in practice:
    #
    # * they SHARE a ring of vertices. This is what rigforge_tag produces, since
    #   a tag takes the vertices of its faces and the faces either side of a
    #   seam share that seam's vertices. The joint is the shared ring.
    # * they are adjacent but disjoint (hand-assigned weights, a transferred
    #   tag). Then the joint is the midpoints of the edges that cross between
    #   them.
    #
    # Reading only the second — which is the obvious implementation — finds
    # nothing at all on the meshes Forge's own tagging produces.
    shared = {}
    for index, marks in vertex_map.items():
        if len(marks) < 2:
            continue
        ordered = sorted(marks)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                entry = shared.setdefault((ordered[i], ordered[j]),
                                          {"sum": common.Vector((0.0, 0.0, 0.0)),
                                           "count": 0})
                entry["sum"] = entry["sum"] + obj.data.vertices[index].co
                entry["count"] += 1

    crossing = {}
    for edge in obj.data.edges:
        a = vertex_map.get(edge.vertices[0]) or frozenset()
        b = vertex_map.get(edge.vertices[1]) or frozenset()
        only_a = set(a) - set(b)
        only_b = set(b) - set(a)
        if not only_a or not only_b:
            continue
        key = tuple(sorted((min(only_a), min(only_b))))
        if key in shared:
            continue
        mid = (obj.data.vertices[edge.vertices[0]].co
               + obj.data.vertices[edge.vertices[1]].co) / 2.0
        entry = crossing.setdefault(key, {"sum": common.Vector((0.0, 0.0, 0.0)),
                                          "count": 0})
        entry["sum"] = entry["sum"] + mid
        entry["count"] += 1

    joints = []
    for source in (shared, crossing):
        for (first, second), entry in source.items():
            if entry["count"] < 3:
                continue
            centre = entry["sum"] / float(entry["count"])
            try:
                ca = centroids[first] / float(counts[first])
                cb = centroids[second] / float(counts[second])
            except (KeyError, ZeroDivisionError):  # pragma: no cover - defensive
                continue
            axis = cb - ca
            if axis.length < 1e-9:
                continue
            joints.append((
                "%s/%s" % (names.get(first, "?"), names.get(second, "?")),
                matrix @ centre,
                (matrix.to_3x3() @ axis).normalized(),
                (matrix.to_3x3() @ axis).length,
            ))
    return joints, "tag boundaries"


#: Two vertices closer together than this fraction of a median edge are in the
#: same loop. A third of an edge is comfortably above the float noise inside one
#: ring and comfortably below the distance to the next one.
LOOP_CLUSTER_FRACTION = 0.35


def _median_edge_length(mesh, world):
    """Median world-space edge length, sampled — the mesh's own scale bar."""
    count = len(mesh.edges)
    if not count:
        return 0.0
    pairs = _np.empty(count * 2, dtype="i4")
    mesh.edges.foreach_get("vertices", pairs)
    pairs = pairs.reshape(count, 2)
    step = max(1, count // 5000)
    sample = pairs[::step]
    lengths = _np.linalg.norm(world[sample[:, 0]] - world[sample[:, 1]], axis=1)
    lengths = lengths[lengths > 1e-12]
    if not len(lengths):
        return 0.0
    return float(_np.median(lengths))


def loop_density(obj, mesh, limit=6):
    """How many edge loops cross each deformation zone.

    A joint that bends wants **at least three** loops across it — one at the
    crease and one either side — or the surface pinches instead of rolling.  It
    is the single retopology number a rigger checks by eye first, and it is the
    one nobody measures.

    *How it is counted, because the approximation matters.* Vertices within a
    band around the joint are projected onto the bone's own axis and clustered
    along it; each cluster is counted as one loop.  That is exactly right on a
    limb retopologised into rings and increasingly approximate on anything that
    is not — a voxel-remeshed blob has no loops for this to find and will report
    a large, meaningless number.  So it is ``heuristic``, the whole way down, and
    the report says which source the joints came from (an armature, or the tag
    boundaries) so the reader can weigh it.
    """
    joints, source = _armature_joints(obj)
    if not joints:
        joints, source = _tag_joints(obj)
    if not joints:
        return None

    count = len(mesh.vertices)
    if not count or _np is None:
        return None
    world = _to_world(_local_coords(mesh), obj.matrix_world)
    edge_length = _median_edge_length(mesh, world)
    if edge_length <= 0.0:
        return None

    zones = []
    for name, head, axis, length in joints[:limit * 4]:
        # The band is a fifth of the bone either side of the joint: wide enough
        # to catch the loops that serve the bend, narrow enough not to count the
        # whole limb.
        half_band = max(length * 0.2, 1e-5)
        radius = max(length * 0.6, 1e-5)
        head_v = _np.array([float(head[0]), float(head[1]), float(head[2])])
        axis_v = _np.array([float(axis[0]), float(axis[1]), float(axis[2])])
        delta = world - head_v
        along = delta @ axis_v
        across = _np.linalg.norm(delta - _np.outer(along, axis_v), axis=1)
        inside = (_np.abs(along) <= half_band) & (across <= radius)
        offsets = [float(v) for v in along[inside]]
        if len(offsets) < 4:
            zones.append({
                "joint": name,
                "loops": claim(0, HEURISTIC,
                               "no geometry inside the band around this joint"),
                "vertices_in_band": len(offsets),
            })
            continue
        offsets.sort()
        # Cluster along the axis. The tolerance comes from the mesh's own median
        # EDGE LENGTH, not from the gap statistics: on a ring-topology limb the
        # gaps within one loop are ~0 and the gaps between loops are one edge,
        # so a fraction of an edge separates them exactly. Deriving the cut from
        # the median gap instead (the obvious implementation) collapses every
        # limb to one loop, because most of the gaps it averages are the zeros
        # inside a single ring.
        cut = edge_length * LOOP_CLUSTER_FRACTION
        loops = 1
        for i in range(len(offsets) - 1):
            if offsets[i + 1] - offsets[i] > cut:
                loops += 1
        zones.append({
            "joint": name,
            "loops": claim(int(loops), HEURISTIC,
                           "vertex bands counted across the joint; %d wanted"
                           % LOOPS_WANTED),
            "vertices_in_band": len(offsets),
            "band_mm": _round(half_band * 2.0 * common.M_TO_MM, 2),
            "enough": claim(bool(loops >= LOOPS_WANTED), HEURISTIC),
        })

    zones.sort(key=lambda z: z["loops"]["value"])
    thin = [z for z in zones if z["loops"]["value"] < LOOPS_WANTED]
    return {
        "source": source,
        "wanted": LOOPS_WANTED,
        "zones": zones[:limit],
        "zone_count": len(zones),
        "thin_zones": len(thin),
        "method": ("vertices in a band around each joint, projected onto the "
                   "bone axis and clustered; each cluster counted as one loop. "
                   "Exact on ring topology, approximate on anything else — "
                   "heuristic throughout."),
    }


# ---------------------------------------------------------------------------
# silhouette IoU against a reference image
# ---------------------------------------------------------------------------

#: The grid both masks are normalised onto before they are compared. 128 is
#: plenty: IoU on a silhouette is a shape question, and the fourth decimal place
#: of it is noise either way.
SILHOUETTE_GRID = 128

#: The render behind the silhouette. Small on purpose — the mask is thresholded
#: alpha, so pixels beyond the point where the outline is resolved buy nothing.
SILHOUETTE_RESOLUTION = 512

#: A reference whose border ring is this uniform is "plain background". Below it,
#: whatever the threshold picked out is not reliably the subject and confidence
#: drops to `low` with the reason attached.
BORDER_UNIFORMITY = 0.90

#: How far a pixel may sit from the estimated background colour and still count
#: as background.
BACKGROUND_TOLERANCE = 0.12

#: Silhouette IoU at or above this passes the gate.  The number is calibrated
#: rather than picked: a circle inscribed in a square scores pi/4 = 0.785, so a
#: threshold of 0.75 would let a SPHERE pass as a CUBE — measured, in this
#: module's own tests. 0.85 sits above that floor and below the 0.9+ two
#: renders of the same mesh reach, which is the band a real reference lands in.
SILHOUETTE_PASS = 0.85


def _image_pixels(path):
    """``(width, height, channels, numpy array)`` for an image on disk.

    Loaded through Blender's own image loader, and the datablock is always
    removed again — a verify that quietly filled ``bpy.data.images`` would leave
    the artist's file dirtier than it found it.
    """
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so the silhouette comparison "
            "cannot run. Everything else in the report is unaffected."
        )
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError as exc:
        raise ForgeError("Could not open %r as an image: %s" % (path, exc))
    try:
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            raise ForgeError("%r has no pixels Blender can read." % path)
        channels = int(image.channels) or 4
        flat = _np.empty(width * height * channels, dtype="f4")
        image.pixels.foreach_get(flat)
        # Blender hands back rows bottom-up; flip so row 0 is the top and every
        # centroid in this file means what a human would mean by it.
        pixels = flat.reshape(height, width, channels)[::-1]
        return width, height, channels, pixels.astype("f8")
    finally:
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):  # pragma: no cover
            pass


def _mask_from_alpha(pixels):
    """The subject as everything with alpha over a half."""
    return pixels[:, :, 3] > 0.5


def _mask_from_background(pixels):
    """The subject as everything unlike the border, plus how sure that is.

    The approximation, stated plainly: the background colour is estimated as the
    median of the border ring, and any pixel further than
    ``BACKGROUND_TOLERANCE`` from it in RGB is called subject.  That is right for
    a product shot on white, a sketch on paper and a render on a flat plate; it
    is wrong for a photograph in a room, and rather than guess we measure how
    uniform the border actually is and hand the number back as confidence.
    """
    rgb = pixels[:, :, :3]
    height, width = rgb.shape[0], rgb.shape[1]
    border = _np.concatenate([
        rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :],
    ], axis=0)
    background = _np.median(border, axis=0)
    distance = _np.sqrt(((rgb - background) ** 2).sum(axis=2))
    mask = distance > BACKGROUND_TOLERANCE
    border_distance = _np.sqrt(((border - background) ** 2).sum(axis=1))
    uniformity = float((border_distance <= BACKGROUND_TOLERANCE).mean())
    return mask, uniformity, [float(v) for v in background]


def _normalise_mask(mask, grid=SILHOUETTE_GRID):
    """Crop a mask to its subject, fit it into a square, keep its aspect.

    Both silhouettes are put through this before they are compared, which is
    what makes the IoU a comparison of SHAPE rather than of framing.  A
    reference photographed from further away is the same silhouette; a render
    that fitted its own bounds is the same silhouette.  Scale and position are
    reported separately (as aspect and centroid deltas) rather than being
    allowed to swamp the number that was supposed to be about shape.
    """
    rows, cols = _np.nonzero(mask)
    if not len(rows):
        return None, None
    top, bottom = int(rows.min()), int(rows.max())
    left, right = int(cols.min()), int(cols.max())
    cropped = mask[top:bottom + 1, left:right + 1]
    height, width = cropped.shape
    aspect = float(width) / float(height) if height else 0.0

    # Fit the longer side to the grid, letterbox the shorter one, centred.
    if width >= height:
        out_w = grid
        out_h = max(1, int(round(grid * height / float(width))))
    else:
        out_h = grid
        out_w = max(1, int(round(grid * width / float(height))))
    row_index = (_np.arange(out_h) * (height / float(out_h))).astype("i4")
    col_index = (_np.arange(out_w) * (width / float(out_w))).astype("i4")
    row_index = _np.clip(row_index, 0, height - 1)
    col_index = _np.clip(col_index, 0, width - 1)
    resampled = cropped[row_index][:, col_index]

    canvas = _np.zeros((grid, grid), dtype=bool)
    row0 = (grid - out_h) // 2
    col0 = (grid - out_w) // 2
    canvas[row0:row0 + out_h, col0:col0 + out_w] = resampled
    return canvas, {
        "aspect": _round(aspect, 4),
        "coverage": _round(float(mask.mean()), 5),
        "centroid": [_round(float(cols.mean()) / mask.shape[1], 4),
                     _round(float(rows.mean()) / mask.shape[0], 4)],
        "bbox_fraction": [_round((right - left + 1) / float(mask.shape[1]), 4),
                          _round((bottom - top + 1) / float(mask.shape[0]), 4)],
    }


def silhouette_iou(render_path, reference_path):
    """Overlap between the rendered front silhouette and a reference image.

    The one measurement in this module that answers *"is it the shape of the
    thing they asked for"* — which is the question a render can be looked at for
    hours without settling, because the eye keeps grading the lighting.

    How it works, and every approximation in it, said out loud:

    1. The object is rendered front-on with a **transparent film**, so the
       subject mask is the alpha channel: exact, no thresholding, no guessing.
    2. The reference's mask is its own alpha when it has one (also exact), and
       otherwise a threshold against the median border colour — which assumes a
       **plain background**. How plain the background actually is comes back as
       ``confidence`` rather than being assumed.
    3. Both masks are cropped to their subject and fitted into the same square,
       preserving aspect. That makes the IoU about shape rather than about how
       far away the photographer stood.
    4. Aspect and centroid deltas are reported separately, from before the
       normalisation, so "the proportions are wrong" and "the shape is wrong"
       stay two different findings.

    IoU itself is ``measured``; the reference mask is ``measured`` when it came
    from an alpha channel and ``heuristic`` when it came from a threshold.
    """
    _, _, render_channels, render_pixels = _image_pixels(render_path)
    if render_channels < 4:  # pragma: no cover - the render is always RGBA
        raise ForgeError(
            "The silhouette render came back without an alpha channel, so "
            "there is no mask to compare."
        )
    render_mask = _mask_from_alpha(render_pixels)

    _, _, ref_channels, ref_pixels = _image_pixels(reference_path)
    reference_note = ""
    if ref_channels >= 4 and float((ref_pixels[:, :, 3] < 0.99).mean()) > 0.01:
        ref_mask = _mask_from_alpha(ref_pixels)
        ref_tier = MEASURED
        uniformity = 1.0
        reference_note = ("the reference has a real alpha channel, so its "
                          "silhouette is exact rather than estimated")
        background = None
    else:
        ref_mask, uniformity, background = _mask_from_background(ref_pixels)
        ref_tier = HEURISTIC
        reference_note = ("the reference has no alpha, so its silhouette was "
                          "thresholded against the median border colour — this "
                          "assumes a PLAIN BACKGROUND")

    render_norm, render_stats = _normalise_mask(render_mask)
    ref_norm, ref_stats = _normalise_mask(ref_mask)

    if render_norm is None:
        raise ForgeError(
            "The silhouette render is empty — nothing was in frame. Check the "
            "object is visible before comparing it to anything."
        )
    if ref_norm is None:
        raise ForgeError(
            "Nothing could be separated from the background in %r: every pixel "
            "reads as background. Use a picture of the object on a plain, "
            "contrasting background, or one with a transparent background."
            % os.path.basename(reference_path)
        )

    intersection = float(_np.count_nonzero(render_norm & ref_norm))
    union = float(_np.count_nonzero(render_norm | ref_norm))
    iou = (intersection / union) if union > 0 else 0.0

    aspect_delta = abs(render_stats["aspect"] - ref_stats["aspect"])
    aspect_relative = aspect_delta / max(render_stats["aspect"],
                                         ref_stats["aspect"], 1e-9)
    centroid_delta = math.sqrt(
        (render_stats["centroid"][0] - ref_stats["centroid"][0]) ** 2
        + (render_stats["centroid"][1] - ref_stats["centroid"][1]) ** 2
    )

    # Confidence is about the REFERENCE, not about the arithmetic. A busy
    # background, a subject that fills the frame, a subject that is three
    # pixels: all of them make the mask unreliable and none of them make the
    # IoU calculation wrong, so they are said separately.
    reasons = []
    confidence = "high" if ref_tier == MEASURED else "medium"
    if ref_tier == HEURISTIC:
        if uniformity < BORDER_UNIFORMITY:
            confidence = "low"
            reasons.append(
                "only %.0f%% of the reference's border is one flat colour, so "
                "the background is not plain and the extracted silhouette may "
                "include scenery" % (uniformity * 100.0)
            )
        if ref_stats["coverage"] > 0.9:
            confidence = "low"
            reasons.append("the reference's 'subject' fills over 90% of the "
                           "frame, which usually means the threshold caught the "
                           "background too")
        if ref_stats["coverage"] < 0.01:
            confidence = "low"
            reasons.append("the reference's subject is under 1% of the frame — "
                           "too small to compare a silhouette against")

    return {
        "iou": claim(_round(iou, 4), MEASURED,
                     "intersection over union of the two silhouettes, both "
                     "cropped and fitted to the same square"),
        "aspect_delta": claim(_round(aspect_relative, 4), MEASURED,
                              "relative difference in the two bounding boxes' "
                              "width:height — proportion, not shape"),
        "centroid_delta": claim(_round(centroid_delta, 4), MEASURED,
                                "distance between the two subjects' centres in "
                                "frame, as a fraction of the frame"),
        "reference_mask": claim(
            "alpha" if ref_tier == MEASURED else "background threshold",
            ref_tier, reference_note,
        ),
        "confidence": confidence,
        "confidence_reasons": reasons,
        "background_uniformity": claim(_round(uniformity, 3), MEASURED,
                                       "fraction of the reference's border ring "
                                       "that matches the estimated background"),
        "background_color": background,
        "render": render_stats,
        "reference": ref_stats,
        "grid": SILHOUETTE_GRID,
        "method": ("front-view render with a transparent film (alpha = the "
                   "exact mask) against the reference's alpha, or its "
                   "background-thresholded mask; both cropped to the subject "
                   "and fitted to a %d x %d square before the overlap, so the "
                   "number is about shape and not about framing."
                   % (SILHOUETTE_GRID, SILHOUETTE_GRID)),
    }


# ---------------------------------------------------------------------------
# rendering machinery — borrowed from render_preview, put back afterwards
# ---------------------------------------------------------------------------

def _render_frames(targets, rotations, path_for, resolution, transparent,
                   fixed_scale=True):
    """Render one PNG per rotation with a temporary orthographic camera.

    Every setting is snapshotted and restored through ``common``'s own preview
    helpers — this is the same borrow-and-return contract ``render_preview``
    signs, for the same reason: a verification that costs the artist their
    render engine is not a verification anyone will run twice.

    ``fixed_scale`` frames every rotation with ONE ortho scale computed from the
    bounding sphere, so a turntable is 24 pictures of the same object at the
    same size rather than 24 differently-cropped ones. That is what makes the
    contact sheet comparable frame to frame — and it is the whole reason the
    fixed rig is the rig that works.
    """
    scene = common.get_scene()
    restore = []
    camera_object = None
    camera_data = None
    written = []

    try:
        with common.object_mode():
            common.refresh_view_layer()
            low, high = common._preview_bounds(targets)

            common._preview_snapshot(restore, scene, ("camera",))
            common._preview_snapshot(restore, scene.render, (
                "engine", "filepath", "resolution_x", "resolution_y",
                "resolution_percentage", "film_transparent", "use_overwrite",
                "use_file_extension", "use_stamp", "use_border",
            ))
            common._preview_snapshot(restore, scene.render.image_settings,
                                     ("file_format", "color_mode", "color_depth"))
            common._preview_snapshot(restore, scene.display, ("render_aa",))
            common._preview_snapshot(restore, scene.display.shading, (
                "light", "color_type", "single_color", "studio_light",
                "background_type", "background_color", "show_shadows",
                "show_specular_highlight", "show_cavity", "cavity_type",
                "show_object_outline", "show_xray",
            ))
            view_settings = getattr(scene, "view_settings", None)
            if view_settings is not None:
                common._preview_snapshot(
                    restore, view_settings,
                    ("view_transform", "look", "exposure", "gamma"))

            chosen = {obj.name for obj in targets}
            for obj in bpy.data.objects:
                if obj.type not in common._RENDERABLE_TYPES or obj.name in chosen:
                    continue
                restore.append((obj, "hide_render", obj.hide_render))
                obj.hide_render = True
            for obj in targets:
                restore.append((obj, "hide_render", obj.hide_render))
                obj.hide_render = False

            scene.render.engine = "BLENDER_WORKBENCH"
            common._preview_configure_workbench(scene)

            render = scene.render
            render.resolution_x = resolution
            render.resolution_y = resolution
            render.resolution_percentage = 100
            render.film_transparent = bool(transparent)
            render.use_overwrite = True
            render.use_file_extension = True
            render.use_border = False
            try:
                render.use_stamp = False
            except (AttributeError, TypeError):
                pass
            render.image_settings.file_format = "PNG"
            render.image_settings.color_mode = "RGBA" if transparent else "RGB"
            try:
                render.image_settings.color_depth = "8"
            except (AttributeError, TypeError):
                pass
            if view_settings is not None:
                for name, value in (("view_transform", "Standard"),
                                    ("look", "None"), ("exposure", 0.0),
                                    ("gamma", 1.0)):
                    try:
                        setattr(view_settings, name, value)
                    except (AttributeError, TypeError, ValueError):
                        pass

            camera_data = bpy.data.cameras.new("Forge Verify Camera")
            camera_object = bpy.data.objects.new("Forge Verify Camera", camera_data)
            scene.collection.objects.link(camera_object)
            scene.camera = camera_object

            # One scale for every frame. The bounding SPHERE is the only fit
            # that does not change as the camera orbits, which is exactly the
            # property a turntable needs.
            centre = common.Vector(((low[0] + high[0]) / 2.0,
                                    (low[1] + high[1]) / 2.0,
                                    (low[2] + high[2]) / 2.0))
            radius = max(
                (common.Vector((x, y, z)) - centre).length
                for x in (low[0], high[0])
                for y in (low[1], high[1])
                for z in (low[2], high[2])
            ) or 1e-4

            for index, rotation in enumerate(rotations):
                if fixed_scale:
                    camera_object.rotation_mode = "XYZ"
                    camera_object.rotation_euler = rotation
                    basis = camera_object.rotation_euler.to_matrix()
                    forward = basis @ common.Vector((0.0, 0.0, -1.0))
                    distance = radius * 3.0 + 1.0
                    camera_object.location = centre - forward * distance
                    camera_object.data.type = "ORTHO"
                    camera_object.data.ortho_scale = 2.0 * radius * common.PREVIEW_MARGIN
                    camera_object.data.clip_start = 1e-4
                    camera_object.data.clip_end = distance + radius * 4.0 + 10.0
                    camera_object.data.shift_x = 0.0
                    camera_object.data.shift_y = 0.0
                else:
                    common._preview_frame(camera_object, rotation, low, high)

                out = path_for(index)
                render.filepath = out
                common.refresh_view_layer()
                try:
                    status = bpy.ops.render.render(write_still=True)
                except RuntimeError as exc:
                    raise ForgeError("Blender could not render frame %d: %s"
                                     % (index, exc))
                if status is not None and "FINISHED" not in status:
                    raise ForgeError(
                        "Frame %d returned %s instead of finishing."
                        % (index, ", ".join(sorted(status)) or "nothing"))
                if not os.path.exists(out):
                    raise ForgeError("Frame %d reported success but nothing was "
                                     "written to %r." % (index, out))
                written.append(out)
    finally:
        if camera_object is not None:
            try:
                bpy.data.objects.remove(camera_object, do_unlink=True)
            except (ReferenceError, RuntimeError):  # pragma: no cover
                pass
        if camera_data is not None:
            try:
                if camera_data.users == 0:
                    bpy.data.cameras.remove(camera_data)
            except (ReferenceError, RuntimeError):  # pragma: no cover
                pass
        common._preview_restore(restore)
        try:
            common.refresh_view_layer()
        except Exception:  # noqa: BLE001
            pass

    return written, {"bounds_mm": {
        "min": [round(v * common.M_TO_MM, 3) for v in low],
        "max": [round(v * common.M_TO_MM, 3) for v in high],
        "size": [round((high[i] - low[i]) * common.M_TO_MM, 3) for i in range(3)],
    }}


# ---------------------------------------------------------------------------
# turntable — the standardised judging rig
# ---------------------------------------------------------------------------

#: The rig that works, from the protocol research: a fixed 24-view turntable at
#: 256 px. Not a number picked for comfort — 24 views at 256 is the
#: configuration that survived the measurement, and a single hero view is the
#: configuration that hides clipping, interpenetration and the back of the head.
TURNTABLE_VIEWS = 24
TURNTABLE_MIN_VIEWS = 4
TURNTABLE_MAX_VIEWS = 64
TURNTABLE_RESOLUTION = 256
TURNTABLE_MIN_RESOLUTION = 64
TURNTABLE_MAX_RESOLUTION = 512

#: Slightly above the equator: a dead-level orbit hides the top of everything,
#: and the top of the head is where generated meshes fail.
TURNTABLE_ELEVATION = 15.0


def _contact_sheet(frames, resolution, columns, path):
    """Stitch the frames into ONE image, reading left-to-right, top-to-bottom.

    A contact sheet rather than N files on purpose: a VLM reads one image far
    more cheaply than it reads 24, and the whole point of the turntable is that
    the views are compared *against each other* — which is a thing you can only
    do when they are in front of you at once.
    """
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so the frames cannot be stitched "
            "into a contact sheet."
        )
    rows = int(math.ceil(len(frames) / float(columns)))
    sheet = _np.zeros((rows * resolution, columns * resolution, 4), dtype="f4")
    sheet[:, :, 3] = 1.0

    for index, frame in enumerate(frames):
        try:
            image = bpy.data.images.load(frame, check_existing=False)
        except RuntimeError as exc:  # pragma: no cover - we just wrote it
            raise ForgeError("Could not read back frame %r: %s" % (frame, exc))
        try:
            width, height = int(image.size[0]), int(image.size[1])
            channels = int(image.channels) or 4
            flat = _np.empty(width * height * channels, dtype="f4")
            image.pixels.foreach_get(flat)
            tile = flat.reshape(height, width, channels)
            if channels < 4:
                padded = _np.ones((height, width, 4), dtype="f4")
                padded[:, :, :channels] = tile
                tile = padded
        finally:
            try:
                bpy.data.images.remove(image)
            except (ReferenceError, RuntimeError):  # pragma: no cover
                pass

        row = index // columns
        column = index % columns
        # Blender's rows run bottom-up, so a tile that should read as row 0 of
        # the sheet has to be written into the LAST band of the buffer. Getting
        # this backwards is silent — the sheet still looks like a turntable —
        # so the flip is done once, here, and the tests assert on it.
        top = (rows - 1 - row) * resolution
        left = column * resolution
        sheet[top:top + resolution, left:left + resolution, :] = tile[:, :, :4]

    out = bpy.data.images.new("Forge Turntable", width=columns * resolution,
                              height=rows * resolution, alpha=True)
    try:
        out.pixels.foreach_set(sheet.reshape(-1))
        out.filepath_raw = path
        out.file_format = "PNG"
        out.save()
    finally:
        try:
            bpy.data.images.remove(out)
        except (ReferenceError, RuntimeError):  # pragma: no cover
            pass
    if not os.path.exists(path):
        raise ForgeError("The contact sheet reported success but nothing was "
                         "written to %r." % path)
    return rows


@command("turntable")
def cmd_turntable(params):
    """N views around Z on one contact sheet — the standardised judging rig.

    A single hero render is the cheapest way to be wrong about a mesh. It hides
    interpenetration, it hides the back of the head, it hides the flat side
    nobody modelled, and it flatters exactly the failures a generated mesh
    arrives with. The fixed turntable is the rig that does not: the same object,
    the same size, the same lighting, from every side, on one image.

    - ``object`` / ``objects``: what to spin; omitted = every visible mesh.
    - ``views``: how many, 4-64 (default 24 — the protocol number).
    - ``resolution``: pixels per tile, 64-512 (default 256 — also the protocol
      number; the sheet is ``columns x resolution`` wide).
    - ``path``: the contact sheet .png to write.
    - ``dir``: write the sheet into this folder instead, under a generated name.
    - ``elevation``: degrees above the equator, -80..80 (default 15).
    - ``keep_frames``: leave the individual tiles on disk too (default false).

    Read-only: it borrows the render settings and a camera and puts every one of
    them back, exactly like ``render_preview``.
    """
    views = common.get_int(params, "views", TURNTABLE_VIEWS,
                           minimum=TURNTABLE_MIN_VIEWS,
                           maximum=TURNTABLE_MAX_VIEWS)
    resolution = common.get_int(params, "resolution", TURNTABLE_RESOLUTION,
                                minimum=TURNTABLE_MIN_RESOLUTION,
                                maximum=TURNTABLE_MAX_RESOLUTION)
    elevation = common.get_float(params, "elevation", TURNTABLE_ELEVATION,
                                 minimum=-80.0, maximum=80.0)
    keep_frames = common.get_bool(params, "keep_frames", False)

    raw_path = params.get("path")
    raw_dir = params.get("dir")
    if raw_path:
        path = common.resolve_path(common.get_str(params, "path"))
        if os.path.isdir(path):
            raise ForgeError(
                "%r is a folder, not a file to write a picture to. Give the "
                "whole filename ending in .png, or use 'dir'." % path
            )
        if os.path.splitext(path)[1].lower() != ".png":
            path += ".png"
    elif raw_dir:
        folder = common.resolve_path(common.get_str(params, "dir"))
        path = os.path.join(folder, "turntable-%dx%d.png" % (views, resolution))
    else:
        raise ForgeError(
            "Give a 'path' for the contact sheet (or a 'dir' to write it into)."
        )
    path = common.resolve_path(path, make_parents=True)

    # `object` (one) and `objects` (many) both work: the rest of the protocol
    # takes `object`, render_preview takes `objects`, and a turntable that
    # refused whichever one the caller reached for first would be a papercut on
    # the tool meant to be reached for constantly.
    target_params = dict(params)
    single = params.get("object")
    if single and not params.get("objects"):
        target_params["objects"] = [single]
    targets, defaulted = common._preview_targets(target_params)

    started = time.monotonic()
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    angles = [360.0 * i / float(views) for i in range(views)]
    tilt = math.radians(90.0 - elevation)
    rotations = [(tilt, 0.0, math.radians(angle)) for angle in angles]

    def frame_path(index):
        return os.path.join(folder, "%s-frame-%03d.png" % (stem, index))

    frames, info = _render_frames(targets, rotations, frame_path, resolution,
                                  transparent=False, fixed_scale=True)

    columns = int(math.ceil(math.sqrt(len(frames))))
    rows = _contact_sheet(frames, resolution, columns, path)

    if not keep_frames:
        for frame in frames:
            try:
                os.remove(frame)
            except OSError:  # pragma: no cover - best effort
                pass

    return {
        "path": path,
        "objects": [obj.name for obj in targets],
        "views": views,
        "resolution": resolution,
        "columns": columns,
        "rows": rows,
        "sheet_size": [columns * resolution, rows * resolution],
        "elevation_deg": round(float(elevation), 2),
        "angles_deg": [round(a, 2) for a in angles],
        "reading_order": ("left to right, top to bottom: tile 0 is 0 degrees "
                          "and each step is %.1f degrees around Z"
                          % (360.0 / views)),
        "frames": frames if keep_frames else [],
        "kept_frames": bool(keep_frames),
        "framed_all_visible": defaulted,
        "bounds_mm": info["bounds_mm"],
        "fixed_framing": True,
        "size_bytes": os.path.getsize(path),
        "duration_ms": int((time.monotonic() - started) * 1000.0),
        "notes": [
            "Every tile is framed identically (one ortho scale from the "
            "bounding sphere), so a change between tiles is a change in the "
            "MODEL and never in the camera.",
            "Read this file. A single view hides interpenetration, the "
            "unmodelled side and the top of the head — which is exactly what a "
            "generated mesh gets wrong.",
        ],
    }


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------

def verdict_lines(result):
    """The scored gate as sentences, worst first, each one tier-stamped."""
    lines = []
    axes = result.get("axes") or {}

    defects = axes.get("defects") or {}
    if defects.get("status") == "attention":
        for sentence in (defects.get("detail") or {}).get("verdict", [])[:3]:
            lines.append("[measured] %s" % sentence)

    budget = axes.get("poly_budget") or {}
    if budget.get("status") == "attention":
        lines.append("[measured] %s" % budget.get("summary"))

    uv = axes.get("uv") or {}
    if uv.get("status") == "attention":
        lines.append("[measured] %s" % uv.get("summary"))

    loops = axes.get("loops") or {}
    if loops.get("status") == "attention":
        lines.append("[heuristic] %s" % loops.get("summary"))

    silhouette = axes.get("silhouette") or {}
    if silhouette.get("status") in ("attention", "pass"):
        prefix = "[measured]" if silhouette.get("confidence") != "low" \
            else "[heuristic]"
        lines.append("%s %s" % (prefix, silhouette.get("summary")))

    symmetry = axes.get("symmetry") or {}
    if symmetry.get("summary"):
        lines.append("[report only] %s" % symmetry["summary"])

    if not lines:
        lines.append("[measured] Every gated axis passes: no defects, and "
                     "nothing else was asked for.")
    return lines


def _axis(status, summary, tier, **extra):
    entry = {"status": status, "summary": summary, "tier": tier}
    entry.update(extra)
    return entry


@command("verify_design")
def cmd_verify_design(params):
    """The geometric gate: is this mesh TRUE, whatever it looks like.

    Renders judge beauty. This judges truth, and the two answer different
    questions — a mesh can be the prettiest render in the folder and still be
    unusable, which is measured rather than suspected (the same model scores 78
    ELO higher shown as a splat than as a mesh; ~26% of paired visual judgements
    reverse when you swap the presentation order). So both gates have to pass
    before anything is called done.

    - ``object``: which mesh; omitted = the active object.
    - ``for``: ``game`` (loops, UVs and the polygon budget are gated),
      ``print`` (defects only here — bed fit and wall thickness belong to
      ``partforge_check`` / ``check_model``, and this does not duplicate them),
      or ``any`` (default).
    - ``reference_image``: a picture to measure the silhouette against.
    - ``poly_budget``: the face count the target platform allows; defaults to
      15 000 for ``game`` and is not gated otherwise.
    - ``symmetry_axis``: ``X`` (default), ``Y`` or ``Z``.
    - ``examples``: located examples per defect, 1-25 (default 5).

    Every claim in the result carries a ``tier``: ``measured`` (a computed
    number with a definition) or ``heuristic`` (a number that took a judgement
    call). Quote them apart — a silhouette thresholded off a photograph is not
    the same kind of fact as a face count.

    Read-only. Nothing in the .blend changes and no undo step is spent.
    """
    profile_name = common.get_choice(
        params, "for",
        {"GAME": "game", "PRINT": "print", "ANY": "any", "": "any"},
        "any",
    )
    profile = PROFILES[profile_name]
    limit = common.get_int(params, "examples", diagnose.DEFAULT_EXAMPLES,
                           minimum=1, maximum=diagnose.MAX_EXAMPLES)
    axis = common.get_choice(params, "symmetry_axis",
                             {"X": "X", "Y": "Y", "Z": "Z"}, "X")
    budget_default = profile["poly_budget"]
    poly_budget = common.get_int(params, "poly_budget", budget_default,
                                 minimum=0, maximum=100000000)

    reference = params.get("reference_image")
    if reference is not None and (not isinstance(reference, str)
                                  or not reference.strip()):
        raise ForgeError(
            "'reference_image' must be the path to an image file, or omitted."
        )

    obj = common.resolve_object(params, mesh_only=True)
    common.refresh_view_layer()

    started = time.monotonic()
    notes = [profile["note"]]
    axes = {}

    # --- defects: mesh_diagnose, composed rather than reimplemented -------
    defect_params = {"object": obj.name, "examples": limit}
    defects = diagnose.cmd_mesh_diagnose(defect_params)
    defect_clean = bool(defects.get("clean"))
    axes["defects"] = _axis(
        "pass" if defect_clean else "attention",
        ("No numeric defects: sealed, no clipping, even density."
         if defect_clean else
         "; ".join((defects.get("verdict") or ["defects found"])[:3])),
        MEASURED,
        detail=defects,
        source="mesh_diagnose",
    )

    # --- poly budget ------------------------------------------------------
    face_count = int(defects.get("face_count") or 0)
    if poly_budget > 0:
        over = face_count > poly_budget
        axes["poly_budget"] = _axis(
            "attention" if over else "pass",
            ("%d faces against a %d budget — %.1fx over; decimate or retopo."
             % (face_count, poly_budget, face_count / float(poly_budget)))
            if over else
            ("%d faces, inside the %d budget (%.0f%% of it)."
             % (face_count, poly_budget,
                100.0 * face_count / float(poly_budget))),
            MEASURED,
            faces=claim(face_count, MEASURED),
            budget=claim(poly_budget, MEASURED, "the target you passed in "
                         "(or this profile's default)"),
            ratio=claim(_round(face_count / float(poly_budget), 3), MEASURED),
        )
        if face_count < POLY_FLOOR:
            notes.append(
                "Only %d faces — this reads as a blockout rather than a "
                "finished mesh, so the budget verdict means very little."
                % face_count
            )
    else:
        axes["poly_budget"] = _axis(
            "not_applicable",
            "No polygon budget for this profile — pass poly_budget to gate one.",
            MEASURED, faces=claim(face_count, MEASURED),
        )

    # --- UVs --------------------------------------------------------------
    # The BASE mesh, not the evaluated one: UVs, tags and the vertex positions a
    # symmetrize would have touched all live on the cage, and measuring a
    # subdivided copy of them would answer a question nobody asked. (The defect
    # half above deliberately does the opposite — clipping is a thing you see,
    # so mesh_diagnose measures what the viewport shows.)
    mesh = obj.data
    uv = uv_metrics(mesh)
    if uv is None:
        axes["uv"] = _axis(
            "attention" if "uv" in profile["gates"] else "not_applicable",
            ("This mesh has no UVs at all — a game asset needs them "
             "(rigforge_auto_uv unwraps from the tag boundaries)."
             if "uv" in profile["gates"] else
             "No UVs on this mesh, and this profile does not need any."),
            MEASURED, present=claim(False, MEASURED),
        )
    else:
        problems = []
        if uv["flipped_faces"]["value"]:
            problems.append("%d flipped face(s)" % uv["flipped_faces"]["value"])
        if uv["degenerate_faces"]["value"]:
            problems.append("%d face(s) with no UV area"
                            % uv["degenerate_faces"]["value"])
        if uv["overlap"]["value"]:
            problems.append("islands appear to overlap")
        worst = uv["area_distortion"]["value"]
        if worst is not None and worst > UV_DISTORTION_OK:
            problems.append("texture density varies %.1fx across the surface"
                            % worst)
        island_count = uv["islands"]["value"]
        islands_text = ("%d island(s)" % island_count
                        if island_count is not None else "islands not counted")
        axes["uv"] = _axis(
            "attention" if problems else "pass",
            ("%s; %s." % (islands_text, ", ".join(problems)))
            if problems else
            ("%s, no flips, density even within %sx."
             % (islands_text, "%.1f" % worst if worst is not None else "?")),
            MEASURED, detail=uv, present=claim(True, MEASURED),
        )

    # --- symmetry: reported, NEVER judged ---------------------------------
    residual = symmetry_residual(mesh, axis=axis)
    if residual is None:
        axes["symmetry"] = _axis("not_applicable",
                                 "Too few vertices to measure a mirror residual.",
                                 MEASURED)
    else:
        near = residual["near_symmetric"]["value"]
        axes["symmetry"] = _axis(
            "reported",
            ("Mirror residual on %s: %s mm mean, %s mm worst (%.2f%% of the "
             "model's diagonal) — %s. Asymmetry is usually a decision; this is "
             "a number, not a fault."
             % (residual["axis"], residual["mean_mm"]["value"],
                residual["max_mm"]["value"],
                (residual["fraction_of_size"]["value"] or 0.0) * 100.0,
                "near-symmetric" if near else "clearly asymmetric")),
            MEASURED, detail=residual, judged=False,
        )

    # --- edge loops in deformation zones ----------------------------------
    loops = loop_density(obj, mesh)
    if loops is None:
        axes["loops"] = _axis(
            "not_applicable",
            ("No armature and no tag boundaries, so there are no deformation "
             "zones to measure. Tag the mesh (rigforge_tag) or rig it first."),
            HEURISTIC,
        )
    else:
        thin = loops["thin_zones"]
        worst_zones = [z for z in loops["zones"]
                       if z["loops"]["value"] < LOOPS_WANTED][:3]
        axes["loops"] = _axis(
            "attention" if thin else "pass",
            ("%d of %d deformation zone(s) have fewer than %d loops across "
             "them (%s) — those will crease rather than bend."
             % (thin, loops["zone_count"], LOOPS_WANTED,
                ", ".join("%s: %d" % (z["joint"], z["loops"]["value"])
                          for z in worst_zones)))
            if thin else
            ("All %d deformation zone(s) carry at least %d loops."
             % (loops["zone_count"], LOOPS_WANTED)),
            HEURISTIC, detail=loops,
        )

    # --- silhouette against a reference -----------------------------------
    if reference:
        reference_path = common.resolve_path(reference)
        if not os.path.isfile(reference_path):
            raise ForgeError(
                "No image at %r to compare the silhouette against." % reference_path
            )
        # Scratch, never next to the artist's picture: the silhouette render is
        # an intermediate the caller never sees, and dropping a stray PNG into
        # whatever folder their reference came out of is a side effect a
        # read-only command has no business having.
        render_path = os.path.join(
            tempfile.gettempdir(),
            "forge-silhouette-%d-%d.png" % (os.getpid(), int(time.time() * 1000) % 100000),
        )
        try:
            _render_frames([obj], [common.PREVIEW_VIEWS["FRONT"]],
                           lambda _index: render_path, SILHOUETTE_RESOLUTION,
                           transparent=True, fixed_scale=False)
            comparison = silhouette_iou(render_path, reference_path)
        finally:
            try:
                os.remove(render_path)
            except OSError:  # pragma: no cover - best effort
                pass
        iou = comparison["iou"]["value"] or 0.0
        good = iou >= SILHOUETTE_PASS
        axes["silhouette"] = _axis(
            "pass" if good else "attention",
            ("Front silhouette overlaps the reference %.0f%% (IoU %.2f, "
             "%s %.2f), proportions off by %.0f%%%s."
             % (iou * 100.0, iou,
                "at or above" if good else "under", SILHOUETTE_PASS,
                (comparison["aspect_delta"]["value"] or 0.0) * 100.0,
                "" if comparison["confidence"] != "low"
                else " — LOW CONFIDENCE, see the reasons")),
            MEASURED if comparison["confidence"] != "low" else HEURISTIC,
            detail=comparison,
            confidence=comparison["confidence"],
            reference=reference_path,
        )
        for reason in comparison["confidence_reasons"]:
            notes.append("silhouette confidence: %s" % reason)
    else:
        axes["silhouette"] = _axis(
            "not_applicable",
            ("No reference image given, so nothing says whether this is the "
             "SHAPE of the thing that was asked for. Pass reference_image when "
             "the artist gave you a picture."),
            MEASURED,
        )

    gated = [name for name in profile["gates"] if name in axes]
    attention = [name for name in gated if axes[name]["status"] == "attention"]
    passed = [name for name in gated if axes[name]["status"] == "pass"]

    result = {
        "object": obj.name,
        "for": profile_name,
        "gated_axes": list(gated),
        "axes": axes,
        "attention": attention,
        "passed": passed,
        "gate": "attention" if attention else "pass",
        "face_count": face_count,
        "vertex_count": int(defects.get("vertex_count") or 0),
        "tiers": {
            MEASURED: "a computed number with a definition behind it",
            HEURISTIC: "a number that took a judgement call to compute — real "
                       "information, wrong to quote as fact",
        },
        "notes": notes,
        "duration_ms": int((time.monotonic() - started) * 1000.0),
    }
    result["verdict"] = verdict_lines(result)
    return result
