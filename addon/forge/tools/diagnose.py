"""``mesh_diagnose`` — the numbers behind "I notice some clipping here".

Buddy mode's other eye.  ``capture_viewport`` and ``render_preview`` show the
assistant what the model *looks* like; this says what is *wrong* with it, and —
the part that matters — **where**.  A critique that says "there is some
self-intersection" is worth nothing to a sculptor; one that says "the left ear
passes through the head at about (-42, 18, 96) mm" is a place to put the mouse.

What it measures, and why each one is on the list:

* **self-intersections** — the artist's own word for this is *clipping*: two bits
  of the surface occupying the same space. It is invisible from most angles,
  fatal to a print, and the single most-requested "did you notice?".
* **non-manifold edges and vertices** — the mesh is not a solid. Every downstream
  Forge tool (segmenting, booleans, printing) either refuses or lies.
* **zero-area faces** — degenerate geometry that survives remeshes and breaks
  normals.
* **face-area distribution** — the density map. A region whose faces are far
  smaller than the median is a *hotspot* (detail crammed in, expensive, uneven);
  far larger is *starved* (no polygons to sculpt into). "We need to remesh here"
  is exactly this measurement with a location attached.
* **ngons** — faces with more than four sides. Fine in a blockout, trouble in a
  sculpt or a subdivision.
* **loose geometry** — stray vertices, wire edges and detached shells: usually
  the leftovers of a botched boolean.
* **scale anomalies** — an unapplied or non-uniform object scale, or a model
  that is 8 metres or 0.4 mm across. Every millimetre downstream is wrong until
  this is.
* **cross-part bridges** — faces welded between two body parts that have no
  business touching: a hand fused to a thigh, a forearm fused to a rib. Every
  weight gate reads clean (each *vertex* follows its correct bone) and the mesh
  is still wrong, because the defect is in the *faces*: any relative motion
  stretches the bridging faces into a visible membrane. See
  :func:`bridge_report`.
* **misplaced junctions** — the same defect inside a pair that *is* adjacent.
  An arm welded to the ribcage 300 mm below its own shoulder is an
  ``Arm``/``Torso`` contact like the shoulder itself, and only the rig can tell
  them apart: the junction bends about a bone, and where that bone cannot reach
  both sides there is nothing to blend and the contact tears. See
  :class:`JunctionRule`.

Read-only, always.  It answers a question and changes nothing, so it pushes no
undo step and it is safe to run on a timer while the artist works.

Speed
-----
The budget is under five seconds on 200 000 faces.  Bulk statistics come off the
Mesh with ``foreach_get`` into numpy arrays (milliseconds, not minutes);
topology comes off a bmesh; the self-intersection scan is a BVH overlap, which
is C-side but still the expensive one, so it is capped and **says so in
``notes``** rather than quietly not running.
"""

import math
import time

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from . import common
from .registry import ForgeError, command

try:  # Blender ships numpy; the fallbacks below keep this honest if it ever does not.
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is part of Blender
    _np = None

__all__ = [
    "SELF_INTERSECT_FACE_LIMIT",
    "DENSITY_RATIO",
    "DENSITY_GRID",
    "face_area_stats",
    "density_regions",
    "self_intersections",
    "topology_report",
    "scale_report",
    "verdict_lines",
    # cross-part bridges
    "TAG_PREFIX",
    "ANATOMY_ADJACENT",
    "BRIDGE_VERTEX_LIMIT",
    "BRIDGE_LINK_FACTOR",
    "BRIDGE_OUTLIER_FACTOR",
    "BRIDGE_REPAIR_MAX_FRACTION",
    "split_tag",
    "tags_adjacent",
    "dominant_tags",
    "bridge_report",
    # misplaced junctions
    "JunctionRule",
    "junction_rule",
    "rig_for",
]


#: Above this many faces the BVH overlap scan is skipped rather than run: the
#: pairwise result set explodes and the answer would arrive after the artist has
#: moved on. Said out loud in ``notes`` — a silent skip is a lie.
SELF_INTERSECT_FACE_LIMIT = 200000

#: How many overlapping pairs to sift for reportable (non-adjacent) hits. A
#: badly self-intersecting mesh can produce millions; the first few thousand
#: already say where the problem is.
SELF_INTERSECT_PAIR_LIMIT = 40000

#: A face this many times smaller than the median is "crammed"; this many times
#: larger is "starved". Four is roughly one subdivision level either way, which
#: is the smallest difference a sculptor actually feels.
DENSITY_RATIO = 4.0

#: The bounding box is bucketed this many cells per axis to turn a pile of
#: offending faces into a handful of PLACES. 8**3 = 512 cells: fine enough to
#: point at an ear, coarse enough that one cell is a sentence.
DENSITY_GRID = 8

#: A face has to be BOTH ``ratio`` off the median AND in the most extreme 2% of
#: the mesh before it counts as a density problem.  Without the second test a
#: plain UV sphere reports its own poles every single time — the pole triangles
#: really are seven times smaller than the equator quads, and saying so on every
#: check-in would train the artist to stop reading.  Two tests means only a
#: genuine outlier region survives.
DENSITY_TAIL = 0.02

#: And a place has to hold at least this many offending faces to be a place at
#: all. Three stray triangles are not a region to remesh.
DENSITY_MIN_FACES = 6

#: Anything below this (in square metres) is a face with no area at all.
#: Scale-relative in practice because it is compared against a mesh whose units
#: are metres and whose smallest meaningful feature is ~0.01 mm.
ZERO_AREA_EPSILON = 1e-14

#: Model sizes that are almost certainly a units mistake rather than a choice.
TINY_MM = 1.0
HUGE_MM = 2000.0

DEFAULT_EXAMPLES = 5
MAX_EXAMPLES = 25


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _mm(vector, matrix=None):
    """A local-space point as world millimetres, rounded to something readable."""
    point = vector
    if matrix is not None:
        try:
            point = matrix @ common.Vector(tuple(vector))
        except (TypeError, ValueError):
            point = vector
    return [round(float(component) * common.M_TO_MM, 2) for component in point]


def _percentile(values, fraction):
    """``fraction`` of the way through a SORTED sequence, without numpy."""
    if not len(values):
        return 0.0
    if _np is not None:
        return float(_np.percentile(values, fraction * 100.0))
    index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
    return float(sorted(values)[index])


# ---------------------------------------------------------------------------
# face areas and the density map
# ---------------------------------------------------------------------------

def face_area_stats(areas_mm2):
    """Mean / median / spread of a face-area array, in square millimetres."""
    count = len(areas_mm2)
    if not count:
        return {"count": 0}
    if _np is not None:
        array = _np.asarray(areas_mm2, dtype="f8")
        return {
            "count": int(count),
            "mean_mm2": round(float(array.mean()), 6),
            "median_mm2": round(float(_np.median(array)), 6),
            "min_mm2": round(float(array.min()), 8),
            "max_mm2": round(float(array.max()), 6),
            "p05_mm2": round(_percentile(array, 0.05), 6),
            "p95_mm2": round(_percentile(array, 0.95), 6),
        }
    ordered = sorted(float(v) for v in areas_mm2)
    return {
        "count": int(count),
        "mean_mm2": round(sum(ordered) / count, 6),
        "median_mm2": round(ordered[count // 2], 6),
        "min_mm2": round(ordered[0], 8),
        "max_mm2": round(ordered[-1], 6),
        "p05_mm2": round(_percentile(ordered, 0.05), 6),
        "p95_mm2": round(_percentile(ordered, 0.95), 6),
    }


def _outliers(values, low_cut, high_cut):
    """Indices of ``values`` outside ``(low_cut, high_cut)``, vectorised."""
    if _np is not None and hasattr(values, "shape"):
        mask = (values <= low_cut) | (values >= high_cut)
        return [int(i) for i in _np.nonzero(mask)[0]]
    return [index for index, value in enumerate(values)
            if not (low_cut < float(value) < high_cut)]


def _below(values, cut):
    """Indices of ``values`` at or below ``cut``, vectorised."""
    if _np is not None and hasattr(values, "shape"):
        return [int(i) for i in _np.nonzero(values <= cut)[0]]
    return [index for index, value in enumerate(values) if float(value) <= cut]


def _above(values, cut):
    """Indices of ``values`` strictly above ``cut``, vectorised."""
    if _np is not None and hasattr(values, "shape"):
        return [int(i) for i in _np.nonzero(values > cut)[0]]
    return [index for index, value in enumerate(values) if float(value) > cut]


def density_regions(areas_mm2, centers, low, high, median, ratio=DENSITY_RATIO,
                    grid=DENSITY_GRID, limit=DEFAULT_EXAMPLES, matrix=None):
    """Where the mesh is crammed and where it is starved, as PLACES not indices.

    Faces are bucketed into a coarse grid over the bounding box and each bucket
    is scored by how many offending faces landed in it.  The answer is a handful
    of "here, and it is this bad" entries, which is the shape a sentence needs:
    *"the ear is four times denser than the rest — remesh there"*.
    """
    if median <= 0 or not len(areas_mm2):
        return [], []
    # Both tests, not either: `ratio` off the median AND out in the distribution's
    # own tail. See DENSITY_TAIL for why the second one is not optional.
    dense_cut = min(median / float(ratio), _percentile(areas_mm2, DENSITY_TAIL))
    starve_cut = max(median * float(ratio),
                     _percentile(areas_mm2, 1.0 - DENSITY_TAIL))
    span = [max(high[i] - low[i], 1e-9) for i in range(3)]

    # Find the offending faces with one vectorised pass, then walk only those.
    # On a 200k-face sculpt the interesting set is usually a few hundred, and
    # looping over 200k of them in Python is the difference between this
    # command being timer-safe and not.
    offenders = _outliers(areas_mm2, dense_cut, starve_cut)

    buckets = {}
    for index in offenders:
        area = float(areas_mm2[index])
        center = centers[index]
        cell = tuple(
            min(grid - 1, max(0, int((float(center[axis]) - low[axis])
                                     / span[axis] * grid)))
            for axis in range(3)
        )
        kind = "dense" if area <= dense_cut else "starved"
        entry = buckets.setdefault((kind, cell), {"count": 0, "area": 0.0,
                                                  "point": center})
        entry["count"] += 1
        entry["area"] += area

    dense, starved = [], []
    for (kind, _cell), entry in buckets.items():
        if entry["count"] < DENSITY_MIN_FACES:
            continue
        record = {
            "faces": int(entry["count"]),
            "mean_area_mm2": round(entry["area"] / max(entry["count"], 1), 6),
            "location_mm": _mm(entry["point"], matrix),
        }
        record["times_median"] = round(
            (record["mean_area_mm2"] / median) if kind == "starved"
            else (median / max(record["mean_area_mm2"], 1e-12)), 1)
        (dense if kind == "dense" else starved).append(record)

    dense.sort(key=lambda r: -r["faces"])
    starved.sort(key=lambda r: -r["faces"])
    return dense[:limit], starved[:limit]


# ---------------------------------------------------------------------------
# self-intersection — the artist's "clipping"
# ---------------------------------------------------------------------------

def self_intersections(bm, limit=DEFAULT_EXAMPLES, matrix=None,
                       face_limit=SELF_INTERSECT_FACE_LIMIT):
    """Faces that pass through other faces they do not touch topologically.

    ``BVHTree.overlap`` against itself reports every pair of faces whose
    triangles intersect — which includes every pair that merely *shares an
    edge*, since those touch by definition.  Filtering the pairs that share a
    vertex is what turns "the mesh has faces" into "the mesh clips itself".
    """
    face_count = len(bm.faces)
    if face_count > face_limit:
        return {
            "count": 0, "faces": 0, "examples": [], "scanned": False,
            "note": ("Skipped the self-intersection scan: %d faces is above the "
                     "%d-face budget for it. Decimate or remesh first, or ask "
                     "for it on one part." % (face_count, face_limit)),
        }
    try:
        tree = BVHTree.FromBMesh(bm, epsilon=0.0)
    except (ValueError, TypeError, MemoryError) as exc:
        return {"count": 0, "faces": 0, "examples": [], "scanned": False,
                "note": "Could not build a search tree for this mesh: %s" % exc}

    try:
        pairs = tree.overlap(tree)
    except (RuntimeError, MemoryError) as exc:  # pragma: no cover - defensive
        return {"count": 0, "faces": 0, "examples": [], "scanned": False,
                "note": "The self-intersection scan gave up: %s" % exc}

    bm.faces.ensure_lookup_table()
    hits = 0
    offenders = set()
    examples = []
    seen_cells = set()
    for index, (a, b) in enumerate(pairs):
        if index >= SELF_INTERSECT_PAIR_LIMIT:
            break
        if a == b or a >= face_count or b >= face_count:
            continue
        face_a = bm.faces[a]
        face_b = bm.faces[b]
        verts_a = {v.index for v in face_a.verts}
        if verts_a & {v.index for v in face_b.verts}:
            continue  # they share a corner: touching, not clipping
        hits += 1
        offenders.add(a)
        offenders.add(b)
        if len(examples) < limit:
            point = (face_a.calc_center_median() + face_b.calc_center_median()) / 2.0
            # One example per neighbourhood: five reports of the same ear is
            # four wasted lines.
            cell = tuple(round(float(c), 3) for c in point)
            if cell in seen_cells:
                continue
            seen_cells.add(cell)
            examples.append({
                "location_mm": _mm(point, matrix),
                "faces": [int(a), int(b)],
            })

    truncated = len(pairs) > SELF_INTERSECT_PAIR_LIMIT
    result = {
        "count": int(hits),
        "faces": len(offenders),
        "examples": examples,
        "scanned": True,
    }
    if truncated:
        result["note"] = ("Stopped after %d overlapping pairs — there are more; "
                          "this mesh intersects itself badly."
                          % SELF_INTERSECT_PAIR_LIMIT)
    return result


# ---------------------------------------------------------------------------
# topology
# ---------------------------------------------------------------------------

def topology_report(bm, limit=DEFAULT_EXAMPLES, matrix=None):
    """Non-manifold edges and vertices, boundaries, wires and loose parts."""
    non_manifold_edges = 0
    boundary_edges = 0
    wire_edges = 0
    multi_face_edges = 0
    edge_examples = []
    for edge in bm.edges:
        faces = len(edge.link_faces)
        if faces == 0:
            wire_edges += 1
        elif faces == 1:
            boundary_edges += 1
        elif faces > 2:
            multi_face_edges += 1
        if faces != 2:
            non_manifold_edges += 1
            if len(edge_examples) < limit:
                mid = (edge.verts[0].co + edge.verts[1].co) / 2.0
                edge_examples.append({
                    "location_mm": _mm(mid, matrix),
                    "faces_on_edge": faces,
                    "kind": ("loose wire" if faces == 0
                             else "open hole" if faces == 1
                             else "%d faces meet here" % faces),
                })

    non_manifold_verts = 0
    loose_verts = 0
    vert_examples = []
    for vert in bm.verts:
        if not vert.link_edges:
            loose_verts += 1
        try:
            manifold = vert.is_manifold
        except (AttributeError, ReferenceError):  # pragma: no cover
            manifold = True
        if not manifold and vert.link_edges:
            non_manifold_verts += 1
            if len(vert_examples) < limit:
                vert_examples.append({"location_mm": _mm(vert.co, matrix)})

    return {
        "non_manifold_edges": non_manifold_edges,
        "boundary_edges": boundary_edges,
        "wire_edges": wire_edges,
        "multi_face_edges": multi_face_edges,
        "non_manifold_vertices": non_manifold_verts,
        "loose_vertices": loose_verts,
        "edge_examples": edge_examples,
        "vertex_examples": vert_examples,
        "watertight": (non_manifold_edges == 0 and non_manifold_verts == 0
                       and loose_verts == 0),
    }


def _shell_count(bm):
    """How many disconnected pieces the mesh is in (union-find over edges)."""
    parent = list(range(len(bm.verts)))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in bm.edges:
        a, b = find(edge.verts[0].index), find(edge.verts[1].index)
        if a != b:
            parent[a] = b
    roots = set()
    for vert in bm.verts:
        if vert.link_edges:
            roots.add(find(vert.index))
    return len(roots)


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------

def scale_report(obj, size_mm):
    """Is this object's scale a decision or an accident?"""
    try:
        scale = [round(float(v), 6) for v in obj.scale]
    except (AttributeError, TypeError):
        scale = [1.0, 1.0, 1.0]
    non_uniform = max(scale) - min(scale) > 1e-4
    unapplied = any(abs(v - 1.0) > 1e-4 for v in scale)
    mirrored = any(v < 0.0 for v in scale)
    longest = max(size_mm) if size_mm else 0.0
    problems = []
    if mirrored:
        problems.append("the object scale is negative on one axis, which turns "
                        "the surface inside out")
    if non_uniform:
        problems.append("the object scale is not the same on every axis (%g, "
                        "%g, %g), so thickness and joints will be wrong" % tuple(scale))
    elif unapplied:
        problems.append("the object scale is %g, not 1 — apply it before "
                        "printing or rigging" % scale[0])
    if longest and longest < TINY_MM:
        problems.append("the whole model is %.3f mm across, which is almost "
                        "certainly a units mistake" % longest)
    elif longest > HUGE_MM:
        problems.append("the model is %.0f mm across — bigger than any printer "
                        "bed, so it is either scaled wrong or needs cutting"
                        % longest)
    return {
        "object_scale": scale,
        "non_uniform": bool(non_uniform),
        "unapplied": bool(unapplied),
        "mirrored": bool(mirrored),
        "dimensions_mm": [round(float(v), 2) for v in size_mm],
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# cross-part bridges — geometry welded between parts that must stay separate
# ---------------------------------------------------------------------------
#
# The defect, in the artist's words: *"the arms mesh or skin pulls from the legs
# — this happens on every animation."*  The sculpt had the hands resting beside
# the thighs; the retopo welded the two touching surfaces into one sheet; every
# clip that separates an arm from a leg now stretches that sheet into a web.
#
# Why no existing gate sees it: the skinning gates are per-VERTEX and they are
# right — every vertex of the membrane follows its correct bone, stray weight is
# 0.0 and isolation is 0.  The broken thing is a FACE, whose corners follow two
# bones that are about to walk away from each other.  So this check reads edges
# and faces, not weights.
#
# The primary signal is the **tag adjacency rule**: an edge whose two endpoints'
# dominant ``tag_*`` groups name two parts that are not anatomically adjacent is
# a bridge, full stop.  It needs nothing but the mesh and its tags — no rig, no
# pose, no animation — so it runs at the ``verify_mesh`` stage on a retopo that
# has not been skinned yet.
#
# Two richer signals were measured on werewolf-wip-15 and REJECTED, recorded
# here so nobody pays for them twice:
#
# * *Articulation stretch* — score each cross-part edge by how far a canonical
#   45-degree articulation about the two bones' nearest common joint would drag
#   its endpoints apart, relative to the edge's rest length.  Measured medians:
#   legal ``Leg/Torso`` seams **12.3**, real ``Arm/Leg`` membrane **8.3**.  The
#   legitimate seams score HIGHER than the defect, because a hip seam is a short
#   edge a long way from the hip pivot.  Noise, not signal.
# * *Bone-graph distance* — how many joints apart the two endpoints' dominant
#   ``DEF-`` bones sit.  On a Rigify rig the DEF chains are not one tree:
#   ``DEF-spine.006`` climbs to ``root`` while ``DEF-upper_arm.L`` climbs to
#   ``MCH-torso.parent`` through six control bones.  149 of the werewolf's
#   cross-part edges have no common ancestor at all, and the spans that do
#   resolve (1 for a hip seam, 19 for the membrane) measure Rigify's control
#   plumbing rather than anatomy.
#
# A third signal — the POSED mesh, which :data:`BRIDGE_OUTLIER_FACTOR` below
# names as the way out of its own blind spot — was measured on 2026-09-19 and
# is REJECTED for the same reason, with harder numbers.  Recorded at length
# because it is the obvious next idea and it costs a day to re-measure.
#
# * *Posed elongation* — drive the rig through its own actions (the werewolf's
#   ``jump``/``punch.L``/``punch.R``/``walk-loop``, eight frames each), and
#   score every edge by ``max(posed length) / rest length``.  The signal is
#   real: on wip-15 the CONFIRMED ``Arm/Leg`` membrane runs at a median of
#   **17.9x** (``Arm.R/Leg.R``, n=47) and **19.8x** (``Arm.L/Leg.L``, n=26),
#   against **1.2x** for the legitimate hip seams (``Leg.*/Torso``, n=433) and
#   **1.1x** for the neck (``Head/Torso``, n=23).  What fails is the
#   **threshold**.  No fence derived from a pair's own distribution works,
#   because on a welded pair the weld IS most of the distribution: on wip-16,
#   Tukey's far-out fence (q75 + 3 IQR) over ``Arm.R/Torso`` lands at x25.7 and
#   flags **0** of the 135 edges that carry the defect, while the same fence
#   over the clean ``Leg.R/Torso`` lands at x2.4 and flags **7**.  Otsu on the
#   logs splits ``Arm.R/Torso`` at 93 of 135 and ``Leg.R/Torso`` at 10 of 219;
#   the largest-log-gap cut gives 104 and 4.  Every rule is either backwards or
#   arbitrary.
# * *Posed elongation normalised by the local skin* — divide an edge's
#   elongation by the largest elongation among the WITHIN-part edges touching
#   its endpoints ("does this boundary line stretch more than the skin it is
#   attached to?").  This is the best of the family and it nearly works: on
#   wip-15 the confirmed membrane never drops below **4.8** (median 16.5-18.6)
#   while the neck seam never passes **0.90** and the hip seams sit at p95
#   **1.4**.  It still cannot cut the werewolf's armpit, because the whole
#   ``Arm/Torso`` and ``Arm/Head`` junction reads weld-like along its entire
#   length (medians 3.8 and 3.3, q75 6.8 and 6.9) — a fence that takes the
#   armpit also takes the shoulder, and a fence that spares the shoulder takes
#   nothing.
#
# The reason is worth stating once, because it retires the whole idea: **the
# posed mesh is a deterministic function of the rest positions, the weights and
# the bone motion.  It carries no information the weights do not.**  Where the
# weights break, the posed mesh tears, and a tear is indistinguishable from a
# weld.  The werewolf's arm root is exactly that: measured on wip-16, adjacent
# vertices across the junction at z = 1.17 m carry ``DEF-spine.001`` 1.00 and
# ``DEF-upper_arm.R.001`` 1.00 — not one shared bone — while the hip carries a
# blended ``DEF-pelvis.R``/``DEF-thigh.R`` pair and stays quiet at 1.2x.  The
# arm is held on by a hard weight break along its whole root, so every edge
# there stretches whether it is weld or anatomy.  Separating them needs the
# skin fixed first (an ``Arm``-root weight-blend lane), or an artist-marked
# cut; it does not need more geometry.  Nothing rest-pose rescues it either:
# the fold across that junction is smooth (dihedral median 2 deg, q25 -10, q75
# 15, the same as ordinary surface), and the confirmed membrane's rest edges
# are ordinary length (1.07-1.31x their own local skin edge) while the
# legitimate neck seam's are the longest on the mesh (3.25x).
#
# What survived as the secondary signal is cheap, needs no rig, and falls out of
# machinery the report needs anyway: within a pair that IS adjacent, the two
# parts should meet at ONE seam.  A contact patch sitting far away from that
# seam is a bridge inside a legal pair.  It is **reported only** and never turns
# the gate red (see :data:`BRIDGE_OUTLIER_FACTOR`).

#: Semantic tag groups carry this prefix; everything else on the object (deform
#: weights, masks) is not anatomy and is ignored here.  Same constant as
#: ``rigforge.TAG_PREFIX``, repeated rather than imported because ``diagnose``
#: sits below ``rigforge`` in the import order and must stay there.
TAG_PREFIX = "tag_"

#: Which base parts legitimately share surface.  Unordered, by base name, side
#: agnostic — the side rule is separate and absolute (see :func:`tags_adjacent`).
#:
#: ``Torso`` touches everything: it is the residual the limbs and the head are
#: carved out of.  ``Head`` touches ``Arm`` because ``rigforge_autotag`` builds
#: ``tag_Head`` as the *neck split's residual* — head plus neck plus whatever
#: the shoulder girdle did not claim — so the trapezius is in ``tag_Head`` and
#: the deltoid is in ``tag_Arm``, and they are one continuous surface.
#: **Measured on werewolf-wip-15**: all 149 ``Arm``/``Head`` cross edges sit at
#: z = 1.53–1.62 m on ``DEF-shoulder.*`` and ``DEF-spine.005/.006`` — the
#: trapezius seam, in one blob per side.  Calling that pair a bridge would fire
#: 149 false positives on the one mesh this gate exists for.
#:
#: Everything absent from this table is non-adjacent and gates red: ``Arm``/
#: ``Leg``, ``Leg``/``Head``, and (by the side rule) ``Arm.L``/``Arm.R`` and
#: ``Leg.L``/``Leg.R``.
ANATOMY_ADJACENT = frozenset({
    frozenset(("Torso", "Head")),
    frozenset(("Torso", "Arm")),
    frozenset(("Torso", "Leg")),
    frozenset(("Head", "Arm")),
})

#: Base names this table has an opinion about.  A tag outside it (a custom part
#: on somebody else's character) is reported under ``unclassified`` and does NOT
#: gate: a check that reddens on vocabulary it does not know teaches the artist
#: to ignore it.
ANATOMY_KNOWN = frozenset(("Torso", "Head", "Arm", "Leg"))

#: Above this many vertices the bridge scan is skipped rather than run — it is a
#: Python walk over every vertex's group memberships, and the command's budget is
#: five seconds on a 200 000-face sculpt.  Said out loud in ``notes``; a silent
#: skip is a lie.  A tagged mesh at the ``verify_mesh`` stage is a retopo (the
#: werewolf's is 8 405 vertices), so this is a guard, not a working limit.
BRIDGE_VERTEX_LIMIT = 400000

#: Cross-part edges are clustered into contact patches so the report says
#: "here", once per place, instead of listing 47 edges.  Two edges join the same
#: patch when they share a vertex, or when their midpoints are within this many
#: times the mesh's OWN median edge length.  Measured rather than constant
#: because a 25 mm retopo and a 0.4 mm dental part need the same rule.  Three
#: median edges is one quad ring: close enough to be the same weld, far enough
#: that the hand membrane (z = 0.85 m) and the elbow one (z = 1.16 m) stay two
#: places on the werewolf.
BRIDGE_LINK_FACTOR = 3.0

#: Secondary signal, reported only.  Inside an ADJACENT pair the two parts
#: should meet at one seam; a contact patch whose centroid is farther than this
#: many times the mesh's median edge length from the pair's largest patch is
#: flagged as a suspected bridge in a legal pair.  Six median edges ~ 150 mm on
#: the werewolf, which keeps the armpit and the shoulder-top one seam.  It never
#: touches ``clean``.
#:
#: **Its measured blind spot, and what closed it.**  The werewolf also has the
#: forearm welded to the flank at z ~ 1.15 m.  Half of that weld is
#: ``Arm``/``Leg`` and the primary rule takes it; the other half is
#: ``Arm.R``/``Torso``, which is a legitimate pair, and the weld runs
#: *continuously* into the genuine armpit seam — one patch, no spatial gap — so
#: no outlier test can separate them.  The cost was visible: after the repair
#: the worst edge anywhere on the mesh still stretched to **536.9 mm** at frame
#: 13 of the rig's own ``jump`` action (it was 1198.8 mm before), and it was
#: that ``Arm.R``/``Torso`` edge.  **The posed mesh does not rescue it** — that
#: was measured and rejected on 2026-09-19, the third rejected signal in the
#: section header above.
#:
#: What did rescue it is not a spatial test at all but the rig's own contract:
#: see :class:`JunctionRule`, which asks *which slab is this contact in, and can
#: the junction's own bone reach it* and cuts the werewolf's 89 lower-armpit
#: edges without touching the 188 shoulder ones beside them.  This factor stays
#: as the signal that needs no rig, and stays report-only.
BRIDGE_OUTLIER_FACTOR = 6.0

#: The repair's refusal line.  A bridge that eats more than this fraction of a
#: part's faces is not a weld artifact — it is a mis-tag, or a part that really
#: is fused by design — and deleting it would gouge the part rather than free
#: it.  5% of the werewolf's smallest part (``Head``, 1 077 vertices) is ~54
#: faces; the whole measured membrane is 43 faces across both sides, so the real
#: defect clears the bar by an order of magnitude and a mis-tagged limb (which
#: would show up as thousands of faces) does not.
BRIDGE_REPAIR_MAX_FRACTION = 0.05


def split_tag(tag):
    """``"Arm.L"`` -> ``("Arm", "L")``; ``"Torso"`` -> ``("Torso", "")``."""
    text = str(tag or "")
    if text.startswith(TAG_PREFIX):
        text = text[len(TAG_PREFIX):]
    base, _, side = text.rpartition(".")
    if base and side.upper() in ("L", "R"):
        return base, side.upper()
    return text, ""


def tags_adjacent(tag_a, tag_b):
    """Do these two tags legitimately share surface?

    ``True`` adjacent, ``False`` non-adjacent (a bridge between them is a
    defect), ``None`` when the anatomy table has no opinion — an unknown part
    name, which is reported and never gated on.
    """
    base_a, side_a = split_tag(tag_a)
    base_b, side_b = split_tag(tag_b)
    if base_a == base_b and side_a == side_b:
        return True
    if base_a not in ANATOMY_KNOWN or base_b not in ANATOMY_KNOWN:
        return None
    # The side rule is absolute and comes first: a left arm and a right arm are
    # never one surface, whatever the base table says about Arm/Arm.
    if base_a == base_b:
        return False
    return frozenset((base_a, base_b)) in ANATOMY_ADJACENT


def dominant_tags(obj):
    """``vertex index -> tag display name`` for the heaviest ``tag_*`` group.

    Ties break on the lower vertex-group index, so two groups at weight 1.0 —
    which is exactly what the autotagger writes — resolve the same way on every
    run and on every machine.  Vertices in no tag map to ``None``.
    """
    groups = {g.index: str(g.name)[len(TAG_PREFIX):]
              for g in getattr(obj, "vertex_groups", ())
              if str(g.name).startswith(TAG_PREFIX)}
    if not groups:
        return {}, {}
    mapping = {}
    for vertex in obj.data.vertices:
        best_name = None
        best_weight = 0.0
        best_index = None
        for entry in vertex.groups:
            name = groups.get(entry.group)
            if name is None or entry.weight <= 0.0:
                continue
            weight = float(entry.weight)
            if (best_index is None or weight > best_weight
                    or (weight == best_weight and entry.group < best_index)):
                best_name, best_weight, best_index = name, weight, entry.group
        mapping[vertex.index] = best_name
    return mapping, groups


# ---------------------------------------------------------------------------
# misplaced junctions — cross-part contact inside a LEGAL pair, in the wrong PLACE
# ---------------------------------------------------------------------------
#
# The residual the section above documents and cannot cut.  ``ANATOMY_ADJACENT``
# says ``Arm``/``Torso`` is a legitimate pair, so every edge between them is
# waved through — and on the werewolf **45 of them per side are a weld**.  The
# arm tube is fused to the ribcage at z = 1.17–1.33 m, three hundred millimetres
# below its own shoulder joint: ``Arm.*``/``Torso.spine.001`` (9 edges left,
# 8 right) and ``Arm.*``/``Torso.spine.002`` (35 left, 37 right).  The real
# shoulder junction — ``Arm.*``/``Torso.spine.003``, 98 and 90 edges — is
# anatomy and must stay.  A pair-level rule cannot tell them apart because they
# are *the same pair*.
#
# The arm-root weight-blend lane (2026-09-19) proved the residual is GEOMETRY
# rather than skinning, by measuring both ways out and failing at both:
# trunk-weighting the welded flesh tears the arm at 636 mm posed;
# arm-weighting it re-admits 3.19 of stray mass and tears the hip at 368 mm
# against a 31 mm rest edge.  ``rigforge_skin.articulations`` carries that
# measurement in its own comment and refuses to open a band there.  So the skin
# is right and the mesh is wrong, and this is the check that says so.
#
# The rule, derived — no free constant, no threshold, nothing tuned
# ----------------------------------------------------------------
# ``rigforge_skin`` already cuts a trunk into **slabs** of its own spine and a
# leg into thigh/shin/foot (:class:`~forge.tools.rigforge_skin.TagSplit`), and
# its contract already names, per tag, the **hinge** — the bone that tag hangs
# from, the one the articulation rule blends the junction about.  ``Arm.L``
# hinges on ``DEF-shoulder.L``; ``Leg.R.thigh`` hinges on ``DEF-spine``, the
# hips; ``Head`` hinges on ``DEF-spine.005``.  Legality is per SLAB: the
# werewolf's ``DEF-shoulder.L`` is legal on ``Torso.spine.003`` and on nothing
# else, because that is the slab it lives in.
#
#     A cross-part contact is a junction only where the bone that junction
#     bends about can reach **both** sides of it.  Where it cannot, the two
#     sides have no bone in common, nothing blends, and the contact is a weld
#     however smooth the surface looks.
#
# Read off the contract that is already there, per refined (slab-level) pair:
# the contact is legitimate when either side's hinge bone is in the other
# side's legal set.  Measured on werewolf-wip-16, every legal-pair contact on
# the figure:
#
#   ================================  =====  =========
#   refined pair                      edges  verdict
#   ================================  =====  =========
#   ``Arm.L``/``Torso.spine.003``        98  junction   (owns ``DEF-shoulder.L``)
#   ``Arm.R``/``Torso.spine.003``        90  junction
#   ``Head``/``Torso.spine.003``         23  junction   (owns ``DEF-spine.005``)
#   ``Leg.L.thigh``/``Torso.spine``     100  junction   (owns ``DEF-spine``)
#   ``Leg.R.thigh``/``Torso.spine``      99  junction
#   ``Leg.L.thigh``/``Torso.spine.001`` 118  junction   (hinges on ``DEF-spine``)
#   ``Leg.R.thigh``/``Torso.spine.001`` 120  junction
#   ``Arm.L``/``Torso.spine.002``        35  MISPLACED
#   ``Arm.R``/``Torso.spine.002``        37  MISPLACED
#   ``Arm.L``/``Torso.spine.001``         9  MISPLACED
#   ``Arm.R``/``Torso.spine.001``         8  MISPLACED
#   ================================  =====  =========
#
# **The hip is the row that makes the rule rather than breaks it.**  238 of the
# leg's edges land on ``Torso.spine.001``, the abdomen slab, which does *not*
# own ``DEF-spine``.  "Owns" alone would call the whole upper hip seam a weld.
# It is legitimate because ``Torso.spine.001`` **hinges on** ``DEF-spine`` — the
# contract says that bone reaches this flesh — so the two sides do share the
# bone the hip bends about.  Legality, not ownership, is the line; ownership is
# just the case where the bone lives there.
#
# Where the rule keeps quiet, deliberately
# ----------------------------------------
# * **No slab granularity, no opinion.**  ``Arm``/``Head`` is the trapezius
#   seam — 149 edges on this figure, neither tag split — and neither hinge is in
#   the other's legal set, so a bare "do they share the hinge" test would fire
#   149 false positives on the one mesh this gate exists for.  A contact is only
#   judged when at least one end was refined into a slab, because only then does
#   the question "is this the right *place*?" mean anything.
# * **No anchor, no opinion.**  If no refined pair under two tags reads as a
#   junction — a rig with no metarig whose hinges do not resolve, say — then the
#   rule does not know where the right place IS and cannot call any place wrong.
#   It says so in ``junction.note`` rather than reddening the whole seam.
# * **No rig, no check.**  Slab ownership is a fact about the armature, so this
#   category runs where a rig exists and is skipped with a sentence where one
#   does not.  ``verify_mesh`` on a raw retopo gets the tag-adjacency rule and a
#   note; the same mesh after ``rigforge_generate_rig`` gets both.


class JunctionRule(object):
    """Where a limb may touch the trunk, read off the rig's own contract.

    Holds three things and nothing else: the slab each vertex of a split tag
    landed in, which tag each slab is a slab of, and — per tag and per slab —
    the hinge bones and the legal bone set
    :func:`~forge.tools.rigforge_skin.legal_bone_sets` derived.  Nothing here
    re-derives anatomy; it asks the contract the skinner enforces.
    """

    def __init__(self, membership, parent_of, hinges, legal, note=""):
        #: ``{vertex index: sub-tag}`` for every vertex of a split tag.
        self.membership = dict(membership)
        self._parent_of = dict(parent_of)
        self._by_parent = {}
        for name, parent in sorted(self._parent_of.items()):
            self._by_parent.setdefault(parent, []).append(name)
        self.hinges = {key: frozenset(value) for key, value in hinges.items()}
        self.legal = {key: frozenset(value) for key, value in legal.items()}
        #: Every slab name, for the report — the reader should be able to see
        #: which view the verdicts were reached in.
        self.slabs = tuple(sorted(self._parent_of))
        self.note = note
        self._anchors = {}

    # -- the slab view -----------------------------------------------------

    def merged(self, tag):
        """A slab folded back into the tag it is a slab of; anything else kept."""
        return self._parent_of.get(tag, tag)

    def refine(self, index, tag):
        """``tag``, replaced by the slab this vertex landed in where there is one."""
        sub = self.membership.get(index)
        if sub is not None and self._parent_of.get(sub) == tag:
            return sub
        return tag

    def _views(self, base):
        """The slabs of ``base``, or ``base`` itself when it is not split."""
        return self._by_parent.get(base) or (base,)

    # -- the rule ----------------------------------------------------------

    def joined(self, tag_a, tag_b):
        """Can the bone this junction bends about reach both sides of it?"""
        empty = frozenset()
        return bool(self.hinges.get(tag_a, empty) & self.legal.get(tag_b, empty)
                    or self.hinges.get(tag_b, empty) & self.legal.get(tag_a, empty))

    def anchored(self, base_a, base_b):
        """Is there ANY place these two tags read as a real junction?

        Without one there is no right place, so no place can be the wrong one.
        """
        key = (base_a, base_b) if base_a <= base_b else (base_b, base_a)
        cached = self._anchors.get(key)
        if cached is None:
            cached = any(self.joined(one, other)
                         for one in self._views(base_a)
                         for other in self._views(base_b))
            self._anchors[key] = cached
        return cached

    def misplaced(self, tag_a, tag_b):
        """``True`` when this refined contact is a junction in the wrong place.

        Both arguments are already refined (see :meth:`refine`).  Returns
        ``False`` for anything the rule has no opinion about — same part, no
        slab granularity, or no anchor — so a caller can use it as a plain
        predicate without re-checking the guards.
        """
        base_a, base_b = self.merged(tag_a), self.merged(tag_b)
        if base_a == base_b:
            return False
        if tag_a == base_a and tag_b == base_b:
            return False
        if not self.anchored(base_a, base_b):
            return False
        return not self.joined(tag_a, tag_b)


def junction_rule(obj, rig=None, metarig=None):
    """``(JunctionRule | None, note)`` — the slab view plus the rig's contract.

    Lazily imported from ``rigforge_skin`` inside the call, never at module
    scope: ``diagnose`` sits BELOW ``rigforge`` in the import order (``rigcheck``
    and ``verify`` import this module) and must stay there.  Nothing in
    ``rigforge_skin``'s own import list reaches back here, so the deferred
    import cannot close a cycle.

    A missing rig, a missing split or a refusal anywhere underneath costs the
    category and returns a sentence, never an exception: ``mesh_diagnose`` runs
    at the retopo stage where there may be no armature at all, and a check that
    raises there would take the whole report down with it.
    """
    if rig is None:
        return None, ("Skipped the misplaced-junction check: %r is not bound to "
                      "an armature, and which slab of the trunk carries a limb's "
                      "junction bone is a fact about the rig. Pass 'rig', or run "
                      "this again after rigforge_generate_rig." % obj.name)
    try:
        from . import rigforge_rig, rigforge_skin
    except ImportError as error:  # pragma: no cover - the add-on ships both
        return None, ("Skipped the misplaced-junction check: %s." % error)
    try:
        if metarig is None:
            stored = str(rigforge_rig._prop(obj, rigforge_rig.PROP_METARIG, "") or "")
            if stored:
                metarig = bpy.data.objects.get(stored)
        regions, _empty = rigforge_rig.measure_tags(obj)
        if not regions:
            return None, ("Skipped the misplaced-junction check: %r has no tagged "
                          "geometry to read slabs off." % obj.name)
        split, _torso, _legs = rigforge_skin.body_split(obj, regions, rig, metarig)
        if split is None:
            return None, ("Skipped the misplaced-junction check: no tag on %r could "
                          "be cut into slabs, so there is no finer place to test a "
                          "junction against." % obj.name)
        contract = rigforge_skin.legal_bone_sets(rig, metarig, regions, split)
    except (AttributeError, IndexError, KeyError, RuntimeError, ReferenceError,
            TypeError, ValueError) as error:
        return None, ("Skipped the misplaced-junction check: the tag contract "
                      "could not be read off %r (%s)." % (rig.name, error))
    membership = {}
    parent_of = {}
    for member in split.members:
        for name in member.names:
            parent_of[name] = member.parent
        membership.update(member.membership)
    rule = JunctionRule(membership, parent_of, contract.get("hinges") or {},
                        contract.get("legal") or {})
    return rule, ""


def _contact_is_defect(tag_a, tag_b, rule=None):
    """Must these two (refined) tags NOT share surface?

    One predicate for both categories, so the detector, the repair's cut and
    every rim fill agree by construction rather than by two lists staying in
    step.  The anatomy table is asked in the COARSE view — ``Torso.spine.002``
    is not a part name and ``tags_adjacent`` has no opinion about it — and the
    junction rule is asked in the refined one.
    """
    if not tag_a or not tag_b:
        return False
    base_a = rule.merged(tag_a) if rule is not None else tag_a
    base_b = rule.merged(tag_b) if rule is not None else tag_b
    if tags_adjacent(base_a, base_b) is False:
        return True
    if rule is None:
        return False
    return rule.misplaced(tag_a, tag_b)


def _median_edge_length(mesh):
    """Median edge length in metres — the mesh's own sense of "next door"."""
    lengths = []
    verts = mesh.vertices
    for edge in mesh.edges:
        a, b = edge.vertices
        lengths.append((verts[a].co - verts[b].co).length)
    if not lengths:
        return 0.0
    lengths.sort()
    return float(lengths[len(lengths) // 2])


class _Union:
    """Tiny union-find keyed on hashables, iteration-order independent."""

    def __init__(self):
        self.parent = {}

    def find(self, node):
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Lower key wins the root so the labelling is deterministic.
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _cluster_edges(entries, link_distance):
    """Group ``(edge_index, va, vb, midpoint)`` into contact patches.

    Two entries join when they share a vertex (topology) or when their midpoints
    are within ``link_distance`` (geometry — a weld can be a chain of separate
    quads).  A uniform grid keeps the geometric pass linear.
    """
    union = _Union()
    by_vertex = {}
    for index, (_edge, va, vb, _mid) in enumerate(entries):
        union.find(index)
        for vertex in (va, vb):
            other = by_vertex.get(vertex)
            if other is None:
                by_vertex[vertex] = index
            else:
                union.join(other, index)
    if link_distance > 0.0:
        cells = {}
        for index, (_edge, _a, _b, mid) in enumerate(entries):
            cell = tuple(int(math.floor(float(c) / link_distance)) for c in mid)
            cells.setdefault(cell, []).append(index)
        limit = link_distance * link_distance
        for cell, members in cells.items():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        neighbours = cells.get((cell[0] + dx, cell[1] + dy,
                                                cell[2] + dz))
                        if not neighbours:
                            continue
                        for i in members:
                            mid_i = entries[i][3]
                            for j in neighbours:
                                if j <= i:
                                    continue
                                mid_j = entries[j][3]
                                if (mid_i - mid_j).length_squared <= limit:
                                    union.join(i, j)
    clusters = {}
    for index in range(len(entries)):
        clusters.setdefault(union.find(index), []).append(index)
    return [clusters[key] for key in sorted(clusters)]


def _faces_by_edge(mesh, edge_indices):
    """``flagged edge index -> [(face index, area m^2)]``, in ONE pass.

    A face touching several flagged edges is attributed to the lowest of them,
    so no face's area is counted twice and the attribution does not depend on
    iteration order.  ``polygon.edge_keys`` gives vertex pairs rather than edge
    indices, so the lookup is built once instead of searching per face.
    """
    wanted = set(edge_indices)
    key_of = {}
    for edge in mesh.edges:
        if edge.index in wanted:
            a, b = edge.vertices
            key_of[(min(a, b), max(a, b))] = edge.index
    out = {}
    if not key_of:
        return out
    for polygon in mesh.polygons:
        hit = None
        for a, b in polygon.edge_keys:
            found = key_of.get((min(a, b), max(a, b)))
            if found is not None and (hit is None or found < hit):
                hit = found
        if hit is None:
            continue
        out.setdefault(hit, []).append((polygon.index, float(polygon.area)))
    return out


def bridge_report(obj, limit=DEFAULT_EXAMPLES, matrix=None,
                  vertex_limit=BRIDGE_VERTEX_LIMIT, rule=None):
    """Faces welded between parts that must stay separate, and WHERE.

    Reads the object's own mesh (not the evaluated one): tags live on the cage,
    the repair edits the cage, and a membrane is a property of the rest surface
    rather than of whatever pose the depsgraph happens to be holding.

    Two categories, reported apart and gated together:

    * ``pairs`` — contact between two parts that are **not anatomically
      adjacent** at all (a hand fused to a thigh).  Needs only the mesh and its
      tags.  ``count``/``faces``/``area_mm2`` are this category's totals.
    * ``misplaced`` — contact inside a pair that IS adjacent but in a place
      where the junction cannot bend, keyed by the refined **slab** pair (the
      werewolf's arm welded to the abdomen, 300 mm below its own shoulder).
      Needs ``rule``, a :class:`JunctionRule` from :func:`junction_rule`;
      without one the category is empty and ``junction`` says why.
      ``misplaced_edges``/``misplaced_faces``/``misplaced_area_mm2`` are its
      totals, kept separate so the two rules stay separately auditable.

    Both are one entry per contact patch with its edge count, face count,
    bridged area in mm^2 and its centroid in world millimetres — the shape the
    workspace pins want.  ``suspected`` holds the weaker spatial-outlier signal
    (see :data:`BRIDGE_OUTLIER_FACTOR`) and ``unclassified`` holds pairs the
    anatomy table has no opinion about; neither gates.
    """
    mesh = obj.data
    blank = {
        "count": 0, "faces": 0, "area_mm2": 0.0, "pairs": [], "examples": [],
        "misplaced": [], "misplaced_edges": 0, "misplaced_faces": 0,
        "misplaced_area_mm2": 0.0, "misplaced_examples": [],
        "junction": {"scanned": rule is not None,
                     "slabs": list(rule.slabs) if rule is not None else []},
        "suspected": [], "unclassified": [], "tags": [], "scanned": False,
    }
    dominant, groups = dominant_tags(obj)
    if not groups:
        blank["note"] = ("No tag_* vertex groups on %r, so there is no anatomy to "
                         "check parts against. Run rigforge_autotag first."
                         % obj.name)
        return blank
    blank["tags"] = sorted(groups.values())
    if len(mesh.vertices) > vertex_limit:
        blank["note"] = ("Skipped the cross-part bridge scan: %d vertices is above "
                         "the %d-vertex budget for it."
                         % (len(mesh.vertices), vertex_limit))
        return blank

    untagged = sum(1 for value in dominant.values() if value is None)
    entries_by_pair = {}
    unclassified = {}
    verts = mesh.vertices
    for edge in mesh.edges:
        a, b = edge.vertices
        tag_a, tag_b = dominant.get(a), dominant.get(b)
        if tag_a is None or tag_b is None or tag_a == tag_b:
            continue
        verdict = tags_adjacent(tag_a, tag_b)
        pair = tuple(sorted((tag_a, tag_b)))
        mid = (verts[a].co + verts[b].co) / 2.0
        if verdict is None:
            unclassified[pair] = unclassified.get(pair, 0) + 1
            continue
        kind = "junction" if verdict else "bridge"
        if verdict and rule is not None:
            # Refined and keyed on the SLAB pair: "Arm.R / Torso.spine.002" is
            # the whole finding, and folding it back to "Arm.R / Torso" would
            # throw away the only thing that separates it from the shoulder.
            refined = (rule.refine(a, tag_a), rule.refine(b, tag_b))
            if rule.misplaced(*refined):
                kind = "misplaced"
                pair = tuple(sorted(refined))
        entries_by_pair.setdefault((pair, kind), []).append(
            (edge.index, a, b, mid))

    # A face can bridge two parts without owning a single bad EDGE: corners in
    # two non-adjacent parts with a third part's corner sitting between them.
    # There are none on werewolf-wip-15 (measured: the edge rule and the corner
    # rule both select exactly 77 faces), but an edge-only gate would miss one,
    # and it is precisely the shape a careless hole-fill leaves behind — so the
    # faces are counted as well as the edges.
    bad_keys = set()
    for (_pair, kind), entries in entries_by_pair.items():
        if kind != "bridge":
            continue
        for _edge, a, b, _mid in entries:
            bad_keys.add((min(a, b), max(a, b)))
    corner_hits = []
    corner_area = 0.0
    for polygon in mesh.polygons:
        pair = _nonadjacent_pair({dominant.get(index)
                                  for index in polygon.vertices})
        if pair is None:
            continue
        if any((min(a, b), max(a, b)) in bad_keys for a, b in polygon.edge_keys):
            continue  # already counted through its edge
        corner_area += float(polygon.area)
        if len(corner_hits) < limit:
            corner_hits.append({
                "parts": list(pair),
                "location_mm": _mm(polygon.center, matrix),
                "face": int(polygon.index),
            })
    corner_count = len(corner_hits)
    if corner_area > 0.0 and corner_count == limit:
        corner_count = sum(
            1 for polygon in mesh.polygons
            if _nonadjacent_pair({dominant.get(i) for i in polygon.vertices})
            and not any((min(a, b), max(a, b)) in bad_keys
                        for a, b in polygon.edge_keys))

    median_edge = _median_edge_length(mesh)
    link = median_edge * BRIDGE_LINK_FACTOR
    outlier = median_edge * BRIDGE_OUTLIER_FACTOR
    face_map = _faces_by_edge(
        mesh, [entry[0] for entries in entries_by_pair.values()
               for entry in entries])

    def patches(entries):
        out = []
        for members in _cluster_edges(entries, link):
            edge_indices = [entries[i][0] for i in members]
            points = [entries[i][3] for i in members]
            centre = points[0].copy()
            for point in points[1:]:
                centre += point
            centre /= float(len(points))
            faces = []
            area = 0.0
            for edge_index in edge_indices:
                for face_index, face_area in face_map.get(edge_index, ()):
                    faces.append(face_index)
                    area += face_area
            out.append({
                "edges": len(edge_indices),
                "faces": len(faces),
                "area_mm2": round(area * common.M_TO_MM * common.M_TO_MM, 3),
                "location_mm": _mm(centre, matrix),
                "_centre": centre,
                "_edges": edge_indices,
            })
        out.sort(key=lambda entry: (-entry["area_mm2"], -entry["edges"],
                                    entry["location_mm"]))
        return out

    def summarise(pair, found):
        """One reportable row for a pair, from its clustered patches."""
        places = [{key: value for key, value in patch.items()
                   if not key.startswith("_")} for patch in found]
        return {
            "parts": list(pair),
            "edges": sum(patch["edges"] for patch in found),
            "faces": sum(patch["faces"] for patch in found),
            "area_mm2": round(sum(patch["area_mm2"] for patch in found), 3),
            "places": places[:limit],
            "place_count": len(places),
        }

    pairs = []
    misplaced = []
    suspected = []
    for (pair, kind), entries in sorted(entries_by_pair.items()):
        found = patches(entries)
        if kind == "junction":
            if len(found) < 2:
                continue
            seam = found[0]["_centre"]
            for patch in found[1:]:
                if (patch["_centre"] - seam).length > outlier and patch["faces"]:
                    record = {key: value for key, value in patch.items()
                              if not key.startswith("_")}
                    record["parts"] = list(pair)
                    record["why"] = (
                        "%s and %s are a legitimate junction, but this patch is "
                        "%.0f mm from their main seam — it may be a weld rather "
                        "than the seam."
                        % (pair[0], pair[1],
                           (patch["_centre"] - seam).length * common.M_TO_MM))
                    suspected.append(record)
            continue
        row = summarise(pair, found)
        if kind == "misplaced":
            row["why"] = (
                "%s and %s do touch legitimately somewhere, but not here: %s "
                "does not carry the bone %s's junction bends about, so the two "
                "sides share no bone and this contact is a weld."
                % (rule.merged(pair[0]), rule.merged(pair[1]), pair[1], pair[0]))
            misplaced.append(row)
        else:
            pairs.append(row)

    by_size = lambda entry: (-entry["area_mm2"], -entry["edges"], entry["parts"])
    pairs.sort(key=by_size)
    misplaced.sort(key=by_size)
    suspected.sort(key=lambda entry: (-entry["area_mm2"], entry["location_mm"]))

    def located(rows):
        out = []
        for row in rows:
            for place in row["places"]:
                out.append({
                    "parts": row["parts"],
                    "location_mm": place["location_mm"],
                    "faces": place["faces"],
                    "area_mm2": place["area_mm2"],
                })
        out.sort(key=lambda entry: (-entry["area_mm2"], entry["location_mm"]))
        return out[:limit]

    corner_area_mm2 = round(corner_area * common.M_TO_MM * common.M_TO_MM, 3)
    junction = {"scanned": rule is not None,
                "slabs": list(rule.slabs) if rule is not None else []}
    result = {
        "count": int(sum(row["edges"] for row in pairs)),
        "faces": int(sum(row["faces"] for row in pairs) + corner_count),
        "area_mm2": round(sum(row["area_mm2"] for row in pairs)
                          + corner_area_mm2, 3),
        "corner_faces": {"count": int(corner_count),
                         "area_mm2": corner_area_mm2,
                         "examples": corner_hits},
        "pairs": pairs,
        "examples": located(pairs),
        "misplaced": misplaced,
        "misplaced_edges": int(sum(row["edges"] for row in misplaced)),
        "misplaced_faces": int(sum(row["faces"] for row in misplaced)),
        "misplaced_area_mm2": round(sum(row["area_mm2"] for row in misplaced), 3),
        "misplaced_examples": located(misplaced),
        "junction": junction,
        "suspected": suspected[:limit],
        "unclassified": [{"parts": list(pair), "edges": count}
                         for pair, count in sorted(unclassified.items())],
        "tags": sorted(groups.values()),
        "median_edge_mm": round(median_edge * common.M_TO_MM, 3),
        "untagged_vertices": int(untagged),
        "scanned": True,
    }
    if untagged:
        result["note"] = ("%d vertices are in no tag_* group and were skipped; "
                          "re-run rigforge_autotag to cover them." % untagged)
    return result


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------

def verdict_lines(result):
    """The three or four sentences a teacher would actually say, worst first."""
    lines = []
    # First, above clipping: a cross-part bridge is invisible at rest and ruins
    # EVERY animation, which is worse than a defect you can see standing still.
    bridges = result.get("part_bridges") or {}
    if bridges.get("count") or bridges.get("faces"):
        where = (bridges.get("examples")
                 or (bridges.get("corner_faces") or {}).get("examples") or [])
        place = ""
        if where:
            place = (" — the worst is around %s mm, between %s"
                     % (", ".join("%g" % v for v in where[0]["location_mm"]),
                        " and ".join(where[0]["parts"])))
        lines.append("%d face%s weld body parts that should be separate (%.1f "
                     "mm2 of bridging surface across %d welding edge%s)%s. "
                     "Anything that moves those parts apart will stretch that "
                     "geometry into a membrane."
                     % (bridges.get("faces", 0),
                        "" if bridges.get("faces") == 1 else "s",
                        bridges.get("area_mm2", 0.0), bridges.get("count", 0),
                        "" if bridges.get("count") == 1 else "s", place))
    if bridges.get("misplaced_edges"):
        where = bridges.get("misplaced_examples") or []
        place = ""
        if where:
            place = (" — the worst is around %s mm, where %s meets %s"
                     % (", ".join("%g" % v for v in where[0]["location_mm"]),
                        where[0]["parts"][0], where[0]["parts"][1]))
        lines.append("%d face%s join two parts that do meet somewhere, but not "
                     "here (%.1f mm2 across %d edge%s)%s. The junction bends "
                     "about a bone that cannot reach this flesh, so nothing "
                     "blends across it and it tears like a weld."
                     % (bridges.get("misplaced_faces", 0),
                        "" if bridges.get("misplaced_faces") == 1 else "s",
                        bridges.get("misplaced_area_mm2", 0.0),
                        bridges["misplaced_edges"],
                        "" if bridges["misplaced_edges"] == 1 else "s", place))
    clip = result.get("self_intersections") or {}
    if clip.get("count"):
        where = clip.get("examples") or []
        place = (" — first one around %s mm"
                 % ", ".join("%g" % v for v in where[0]["location_mm"])) if where else ""
        lines.append("The surface passes through itself in %d place%s%s "
                     "(clipping)." % (clip["count"], "" if clip["count"] == 1 else "s",
                                      place))
    topo = result.get("topology") or {}
    if topo.get("non_manifold_edges"):
        lines.append("%d edge%s are not sealed (%d open holes, %d loose wires, "
                     "%d where more than two faces meet), so this is not a solid."
                     % (topo["non_manifold_edges"],
                        "" if topo["non_manifold_edges"] == 1 else "s",
                        topo.get("boundary_edges", 0), topo.get("wire_edges", 0),
                        topo.get("multi_face_edges", 0)))
    density = result.get("density") or {}
    if density.get("starved"):
        first = density["starved"][0]
        lines.append("There is a starved patch around %s mm — faces about %gx "
                     "bigger than the rest, nothing to sculpt into. Remesh there."
                     % (", ".join("%g" % v for v in first["location_mm"]),
                        first["times_median"]))
    if density.get("dense"):
        first = density["dense"][0]
        lines.append("A crammed patch around %s mm runs about %gx denser than "
                     "the rest of the surface."
                     % (", ".join("%g" % v for v in first["location_mm"]),
                        first["times_median"]))
    if result.get("zero_area_faces", {}).get("count"):
        lines.append("%d face%s have no area at all (degenerate geometry)."
                     % (result["zero_area_faces"]["count"],
                        "" if result["zero_area_faces"]["count"] == 1 else "s"))
    loose = result.get("loose") or {}
    if loose.get("shells", 1) > 1:
        lines.append("The mesh is in %d separate pieces." % loose["shells"])
    if loose.get("vertices"):
        lines.append("%d stray vertices are connected to nothing."
                     % loose["vertices"])
    for problem in (result.get("scale") or {}).get("problems", []):
        lines.append(problem[:1].upper() + problem[1:] + ".")
    if not lines:
        lines.append("Nothing is wrong with this mesh numerically: sealed, no "
                     "clipping, even density.")
    return lines


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def rig_for(obj, params=None):
    """The armature this mesh is bound to, or ``None`` — never an error.

    Deliberately not ``rigforge_rig._rig_for_mesh``, which raises: this module
    runs at the retopo stage where there is often no armature yet, and a
    diagnostic that refuses to answer *"what is wrong with this mesh"* because
    it has not been rigged is a diagnostic nobody runs.  A ``rig`` named in
    ``params`` and not found IS an error, because that is a typo rather than a
    stage of the pipeline.
    """
    name = (params or {}).get("rig")
    if isinstance(name, str) and name.strip():
        rig = bpy.data.objects.get(name.strip())
        if rig is None:
            raise ForgeError("No object named %r to read slabs off." % name.strip())
        if rig.type != "ARMATURE":
            raise ForgeError("Object %r is a %s, not an armature."
                             % (rig.name, rig.type))
        return rig
    for modifier in getattr(obj, "modifiers", ()) or ():
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    parent = getattr(obj, "parent", None)
    if parent is not None and getattr(parent, "type", "") == "ARMATURE":
        return parent
    return None


def _evaluated_mesh(obj, apply_modifiers):
    """``(mesh, owner, evaluated)`` — free ``owner`` with ``to_mesh_clear``."""
    if not apply_modifiers or not getattr(obj, "modifiers", None):
        return obj.data, None, False
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        source = obj.evaluated_get(depsgraph)
        mesh = source.to_mesh()
    except (AttributeError, RuntimeError, ReferenceError):
        return obj.data, None, False
    if mesh is None:
        return obj.data, None, False
    return mesh, source, True


@command("mesh_diagnose")
def cmd_mesh_diagnose(params):
    """Measure what is wrong with a mesh, and say WHERE, in millimetres.

    - ``object``: which mesh; omitted = the active object.
    - ``examples``: how many located examples per problem, 1-25 (default 5).
    - ``apply_modifiers``: measure what the artist SEES, modifiers included
      (default true) rather than the raw cage.
    - ``density_ratio``: how much denser (or sparser) than the median a region
      has to be before it is worth mentioning; default 4.
    - ``rig``: which armature to read the tag contract off, for the
      misplaced-junction category; omitted = the mesh's own armature modifier
      or armature parent, and no rig at all means that one category is skipped
      with a sentence in ``notes`` (see :func:`junction_rule`).

    Read-only: it answers a question, changes nothing and pushes no undo step,
    which is what makes it safe to run on the buddy timer while they work.
    """
    # Numbers first, object second: "examples must be >= 1" is the answer to
    # `examples=0` whether or not there happens to be an active object, and
    # answering with the object problem instead sends the caller after a
    # different bug.
    limit = common.get_int(params, "examples", DEFAULT_EXAMPLES,
                           minimum=1, maximum=MAX_EXAMPLES)
    apply_modifiers = common.get_bool(params, "apply_modifiers", True)
    ratio = common.get_float(params, "density_ratio", DENSITY_RATIO,
                             minimum=1.5, maximum=100.0)
    obj = common.resolve_object(params, mesh_only=True)
    # A scale set from a script has not reached `obj.dimensions` until the
    # depsgraph has been through; without this, "is this a units mistake?" is
    # answered from a stale bounding box.
    common.refresh_view_layer()

    started = time.monotonic()
    notes = []
    mesh, owner, evaluated = _evaluated_mesh(obj, apply_modifiers)
    matrix = getattr(obj, "matrix_world", None)

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        face_count = len(bm.faces)
        vertex_count = len(bm.verts)
        edge_count = len(bm.edges)

        areas, centers, sides = _face_arrays(mesh)
        areas_mm2 = [float(a) * common.M_TO_MM * common.M_TO_MM for a in areas] \
            if _np is None else (_np.asarray(areas, dtype="f8")
                                 * (common.M_TO_MM ** 2))

        stats = face_area_stats(areas_mm2)
        median = float(stats.get("median_mm2") or 0.0)

        zero = _zero_area(areas, centers, limit, matrix)
        ngons = _ngons(sides, centers, limit, matrix)

        low, high = _local_bounds(mesh)
        dense, starved = density_regions(areas_mm2, centers, low, high, median,
                                         ratio=ratio, limit=limit, matrix=matrix)

        topology = topology_report(bm, limit=limit, matrix=matrix)
        shells = _shell_count(bm)
        clipping = self_intersections(bm, limit=limit, matrix=matrix)
        if clipping.get("note"):
            notes.append(clipping["note"])

        # Deliberately off the evaluated mesh and onto the object's own cage:
        # tags live on the cage, the repair edits the cage, and a weld is a
        # property of the rest surface rather than of the pose the depsgraph is
        # holding this frame.
        rule, junction_note = junction_rule(obj, rig_for(obj, params))
        bridges = bridge_report(obj, limit=limit, matrix=matrix, rule=rule)
        if junction_note:
            bridges.setdefault("junction", {})["note"] = junction_note
            notes.append(junction_note)
        if bridges.get("note"):
            notes.append(bridges["note"])
    finally:
        bm.free()
        if owner is not None:
            try:
                owner.to_mesh_clear()
            except (AttributeError, RuntimeError, ReferenceError):
                pass

    size_mm = [round(float(v) * common.M_TO_MM, 2)
               for v in getattr(obj, "dimensions", (0.0, 0.0, 0.0))]
    scale = scale_report(obj, size_mm)
    if evaluated:
        notes.append("Measured with modifiers applied — this is what you see in "
                     "the viewport, not the raw cage.")

    result = {
        "object": obj.name,
        "vertex_count": vertex_count,
        "face_count": face_count,
        "edge_count": edge_count,
        "evaluated": bool(evaluated),
        "self_intersections": clipping,
        "part_bridges": bridges,
        "topology": topology,
        "zero_area_faces": zero,
        "ngons": ngons,
        "loose": {
            "vertices": topology["loose_vertices"],
            "wire_edges": topology["wire_edges"],
            "shells": shells,
        },
        "density": {
            "faces": stats,
            "ratio": ratio,
            "dense": dense,
            "starved": starved,
        },
        "scale": scale,
        "notes": notes,
        "duration_ms": int((time.monotonic() - started) * 1000.0),
    }
    result["verdict"] = verdict_lines(result)
    result["clean"] = (
        not clipping.get("count")
        # Red on a bridge between NON-ADJACENT parts, and on a misplaced
        # junction — both are welds and both tear under pose. `suspected` (the
        # weaker spatial-outlier signal) and `unclassified` (parts the anatomy
        # table has no opinion about) are reported and never gate.
        and not bridges.get("count") and not bridges.get("faces")
        and not bridges.get("misplaced_edges")
        and topology["watertight"]
        and not zero["count"]
        and not dense and not starved
        and not scale["problems"]
    )
    return result


def _face_arrays(mesh):
    """``(areas, centers, sides)`` straight off the Mesh — numpy where possible."""
    count = len(mesh.polygons)
    if not count:
        return [], [], []
    if _np is not None:
        areas = _np.empty(count, dtype="f4")
        centers = _np.empty(count * 3, dtype="f4")
        sides = _np.empty(count, dtype="i4")
        mesh.polygons.foreach_get("area", areas)
        mesh.polygons.foreach_get("center", centers)
        mesh.polygons.foreach_get("loop_total", sides)
        return areas, centers.reshape(count, 3), sides
    areas, centers, sides = [], [], []
    for polygon in mesh.polygons:
        areas.append(polygon.area)
        centers.append(tuple(polygon.center))
        sides.append(polygon.loop_total)
    return areas, centers, sides


def _zero_area(areas, centers, limit, matrix):
    hits = _below(areas, ZERO_AREA_EPSILON)
    examples = [{"location_mm": _mm(centers[index], matrix), "face": int(index)}
                for index in hits[:limit]]
    return {"count": len(hits), "examples": examples}


def _ngons(sides, centers, limit, matrix):
    hits = _above(sides, 4)
    biggest = max((int(sides[index]) for index in hits), default=0)
    examples = [{"location_mm": _mm(centers[index], matrix),
                 "sides": int(sides[index]), "face": int(index)}
                for index in hits[:limit]]
    return {"count": len(hits), "max_sides": biggest, "examples": examples}


# ---------------------------------------------------------------------------
# the repair
# ---------------------------------------------------------------------------

def _boundary_keys(bm):
    """Vertex-index pairs of every edge with exactly one face."""
    return {(min(e.verts[0].index, e.verts[1].index),
             max(e.verts[0].index, e.verts[1].index))
            for e in bm.edges if len(e.link_faces) == 1}


#: Hard stop on a single rim walk.  A rim longer than this is not a hole this
#: tool should be closing; it means the deletion set was wrong.
BRIDGE_MAX_RIM = 4096


def _other_edge_at(face, vert, edge):
    for candidate in face.edges:
        if candidate is not edge and vert in candidate.verts:
            return candidate
    return None


def _next_boundary(edge, vert):
    """The next boundary edge at ``vert``, by walking the face fan.

    Naive "the other boundary edge at this vertex" picks wrong at a point-weld,
    where four boundary edges meet and only two of them are on the same side of
    the surface.  Rotating through the linked faces instead follows the actual
    rim.  On the werewolf this is the difference between 9 closed loops plus 6
    fragments and **10 closed loops, no fragments**.
    """
    current_edge = edge
    current_face = edge.link_faces[0]
    for _ in range(64):
        nxt = _other_edge_at(current_face, vert, current_edge)
        if nxt is None:
            return None
        faces = nxt.link_faces
        if len(faces) == 1:
            return nxt
        if len(faces) != 2:
            return None
        current_face = faces[0] if faces[1] is current_face else faces[1]
        current_edge = nxt
    return None


def _boundary_loops(bm, skip_keys):
    """Closed rims opened by the deletion, as ``(vertex cycle, edge list)``.

    Returns ``(loops, strays)``: ``strays`` are boundary edges that never joined
    a closed cycle, which is a rim this tool will not guess at.
    """
    fresh = []
    for edge in bm.edges:
        if len(edge.link_faces) != 1:
            continue
        a, b = edge.verts[0].index, edge.verts[1].index
        if (min(a, b), max(a, b)) in skip_keys:
            continue
        fresh.append(edge)
    allowed = {edge.index for edge in fresh}
    loops = []
    used = set()
    strays = []
    for edge in fresh:  # bm.edges order: deterministic
        if edge.index in used:
            continue
        cycle = [edge.verts[0], edge.verts[1]]
        edges = [edge]
        used.add(edge.index)
        current, cursor = edge, edge.verts[1]
        closed = False
        for _ in range(BRIDGE_MAX_RIM):
            nxt = _next_boundary(current, cursor)
            if nxt is None or nxt.index not in allowed:
                break
            if nxt.index in used:
                closed = nxt.index == edge.index
                break
            used.add(nxt.index)
            edges.append(nxt)
            cursor = nxt.other_vert(cursor)
            current = nxt
            if cursor is cycle[0]:
                closed = True
                break
            cycle.append(cursor)
        if closed and len(cycle) >= 3:
            loops.append((cycle, edges))
        else:
            strays.extend(edges)
    return loops, strays


def _defect_pair(parts, rule=None):
    """The first pair among ``parts`` that must not share surface, or ``None``.

    With a :class:`JunctionRule` this covers both categories — a non-adjacent
    pair and a misplaced junction — so the cut, every fill and the detector
    read the same line rather than two lists that have to be kept in step.
    """
    ordered = sorted(part for part in parts if part)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if _contact_is_defect(first, second, rule):
                return (first, second)
    return None


def _nonadjacent_pair(parts):
    """The first non-adjacent pair among ``parts``, or ``None``."""
    return _defect_pair(parts, None)


def _rim_direction(edge):
    """``(from, to)`` as the rim's one surviving face traverses ``edge``."""
    face = edge.link_faces[0]
    for loop in face.loops:
        if loop.edge is edge:
            return loop.vert, loop.link_loop_next.vert
    return edge.verts[0], edge.verts[1]


def _shell_groups(bm):
    """``[[BMVert, ...], ...]`` — the mesh's connected pieces, largest first."""
    union = _Union()
    for vert in bm.verts:
        union.find(vert.index)
    for edge in bm.edges:
        union.join(edge.verts[0].index, edge.verts[1].index)
    groups = {}
    for vert in bm.verts:
        groups.setdefault(union.find(vert.index), []).append(vert)
    return sorted(groups.values(), key=lambda members: -len(members))


def _patch_ok(created, dominant, rule=None):
    """Is this fill legal — no re-bridge, and nothing left non-manifold?"""
    for face in created:
        parts = {dominant.get(vert.index) for vert in face.verts}
        if _defect_pair(parts, rule):
            return False
        for edge in face.edges:
            if len(edge.link_faces) > 2:
                return False
    return True


def _drop(bm, faces):
    live = [face for face in faces if face.is_valid]
    if live:
        bmesh.ops.delete(bm, geom=live, context="FACES_ONLY")


#: Above this many vertices a rim gets neither of the two searching fills — both
#: are O(n^2) in the rim's length, and a rim that long is a deletion set this
#: tool got wrong rather than a hole to be clever about.  The werewolf's armpit
#: rims are 4 to 28 vertices and its hand/thigh rims smaller still, so this is a
#: guard rather than a working limit.
BRIDGE_MAX_FILL_RIM = 512


def _ring_edges(cycle, edges):
    """``edges`` reordered so entry *k* joins ``cycle[k]`` and ``cycle[k+1]``.

    ``_boundary_loops`` already walks in that order, but it has a second exit
    (a rim that closes onto an edge it has already used) that can leave the two
    lists off by one.  Rebuilding the ring from the vertex pairs costs nothing
    and means the chord search below cannot silently slice the wrong arc.
    """
    lookup = {}
    for edge in edges:
        lookup[frozenset((edge.verts[0].index, edge.verts[1].index))] = edge
    ring = []
    count = len(cycle)
    for position in range(count):
        edge = lookup.get(frozenset((cycle[position].index,
                                     cycle[(position + 1) % count].index)))
        if edge is None:
            return None
        ring.append(edge)
    return ring


def _split_fill(bm, cycle, edges, dominant, rule):
    """Close a rim as TWO patches either side of a chord neither of them crosses.

    The fill the werewolf's armpit needs, and the one the fan cannot give it.
    After the cut, each of those rims reads (in walk order) a run of ``Arm.R``,
    one ``Torso.spine.003`` vertex, a run of ``Torso.spine.002`` and ``.001``,
    and a second ``Torso.spine.003`` vertex — an arm-to-abdomen edge is exactly
    what was deleted, so the rim can only cross between arm and trunk at the
    shoulder slab, where the junction is real.  A single n-gon over that rim
    puts an ``Arm.R`` corner and a ``Torso.spine.002`` corner on one face: the
    weld, back.  A fan from the shoulder-slab pivot is legal face by face and
    geometrically worse — it spans 260 mm of open slot with triangles from one
    vertex and re-creates the membrane under a legitimate label, which the
    detector would then pass.

    Cutting the rim at the two shoulder-slab vertices instead gives an arm arc
    and a trunk arc that share exactly those two endpoints, and one chord
    between them closes both: the arm closes as an arm, the trunk as a trunk,
    the chord carries two faces so nothing is left open, and — the part that
    shows up in the posed numbers — **not one new cross-part edge is created**,
    because the chord's two ends are in the same part.  Measured on wip-16
    against the ear clip below, which does create some: 10 faces added rather
    than 92, and the worst posed ``Arm``/``Torso`` edge after a re-skin at
    293.7 mm rather than 324.1 mm.

    The chord is found rather than assumed: every pair of rim vertices whose
    two arcs and whose own endpoints are all defect-free is a candidate, and
    the **shortest** one wins — a chord is a straight line through whatever the
    rim curves around, so the shortest legal one cuts through least.  Ties
    break on the vertex indices, so the choice is the same on every run and
    every machine.  It returns ``None`` — leaving the mesh untouched, for the
    ear clip below — on any rim where no such pair exists.
    """
    count = len(cycle)
    if count < 4 or count > BRIDGE_MAX_FILL_RIM:
        return None
    ring = _ring_edges(cycle, edges)
    if ring is None:
        return None
    parts = [dominant.get(vert.index) for vert in cycle]
    names = sorted({part for part in parts if part})
    if len(names) < 2:
        return None  # a single-part rim never needed splitting
    bit_of = {name: index for index, name in enumerate(names)}
    bits = [1 << bit_of[part] if part else 0 for part in parts]
    memo = {}

    def mask_ok(mask):
        answer = memo.get(mask)
        if answer is None:
            answer = _defect_pair([names[bit] for bit in range(len(names))
                                   if mask >> bit & 1], rule) is None
            memo[mask] = answer
        return answer

    # Suffix and prefix unions, so the far arc's parts are one OR rather than a
    # walk and the whole search stays quadratic instead of cubic.
    suffix = [0] * (count + 1)
    for position in range(count - 1, -1, -1):
        suffix[position] = suffix[position + 1] | bits[position]
    prefix = [0] * (count + 1)
    for position in range(count):
        prefix[position + 1] = prefix[position] | bits[position]

    candidates = []
    for low in range(count - 2):
        near = bits[low] | bits[low + 1]
        for high in range(low + 2, count):
            near |= bits[high]
            # Both arcs have to be a face: three corners each, counting the two
            # they share.
            if low + count - high < 2:
                break
            if _contact_is_defect(parts[low], parts[high], rule):
                continue
            if not mask_ok(near):
                continue
            if not mask_ok(suffix[high] | prefix[low + 1]):
                continue
            span = (cycle[low].co - cycle[high].co).length
            candidates.append((round(float(span), 9), low, high))
    candidates.sort()
    for _span, low, high in candidates:
        made = _two_patches(bm, cycle, ring, low, high, dominant, rule)
        if made:
            return made
    return None


def _two_patches(bm, cycle, ring, low, high, dominant, rule):
    """Build the chord and the two n-gons, or roll the lot back and return None."""
    one, other = cycle[low], cycle[high]
    chord = bm.edges.get((one, other))
    minted = chord is None
    if minted:
        try:
            chord = bm.edges.new((one, other))
        except ValueError:  # pragma: no cover - defensive
            return None

    def unmint():
        if minted and chord.is_valid and not chord.link_faces:
            bmesh.ops.delete(bm, geom=[chord], context="EDGES")

    created = []
    for arc in (list(ring[low:high]) + [chord],
                list(ring[high:]) + list(ring[:low]) + [chord]):
        try:
            made = bmesh.ops.contextual_create(bm, geom=arc)
        except (RuntimeError, ValueError):  # pragma: no cover - defensive
            made = {}
        faces = list(made.get("faces", ()))
        if len(faces) != 1:
            _drop(bm, created + faces)
            unmint()
            return None
        created.extend(faces)
    if _patch_ok(created, dominant, rule):
        return created
    _drop(bm, created)
    unmint()
    return None


def _ear_fill(bm, cycle, edges, dominant, rule):
    """Close a rim by clipping **legal ears**, so no new face re-welds anything.

    The general case, for a rim no single chord splits in two.  On wip-15 the
    hand/thigh weld runs into the armpit one, so after the cut a rim carries
    ``Arm``, ``Leg.*.thigh`` and three trunk slabs at once: no pivot is
    compatible with all of them (the fan refuses outright and the hole stays
    open — 106 boundary edges measured), and no single chord leaves two
    defect-free arcs, because that rim wants three.

    An ear is three CONSECUTIVE rim vertices, so it inherits the rim's own
    shape instead of reaching across the hole, and a triangle is only clipped
    when its three parts may share a face — which makes re-welding impossible
    by construction rather than by a check after the fact.  The shortest legal
    ear goes first (ties on the clipped vertex's index, so the walk is
    identical on every machine), which closes a slot the way a zip does, from
    its narrow ends inward.

    Returns the new faces, or ``None`` having left the mesh exactly as it was
    when no legal triangulation exists.
    """
    count = len(cycle)
    if count < 3 or count > BRIDGE_MAX_FILL_RIM:
        return None
    ring = _ring_edges(cycle, edges)
    if ring is None:
        return None
    # Winding, decided once off the surface being closed: a new face has to
    # traverse every shared edge the opposite way to the face already on it, or
    # the patch faces backwards.
    first, second = _rim_direction(ring[0])
    forward = first is cycle[0] and second is cycle[1]

    live = list(cycle)
    created = []
    while True:
        best = None
        total = len(live)
        for position in range(total):
            prev = live[position - 1]
            cur = live[position]
            nxt = live[(position + 1) % total]
            if _defect_pair({dominant.get(vert.index)
                             for vert in (prev, cur, nxt)}, rule):
                continue
            if total > 3:
                # The edge this ear leaves behind becomes the new rim, so it
                # cannot already be carrying two faces of its own.
                closing = bm.edges.get((prev, nxt))
                if closing is not None and len(closing.link_faces) >= 2:
                    continue
            if bm.faces.get((prev, cur, nxt)) is not None:
                continue
            span = ((prev.co - cur.co).length + (cur.co - nxt.co).length
                    + (nxt.co - prev.co).length)
            key = (round(float(span), 9), int(cur.index))
            if best is None or key < best[0]:
                best = (key, position, (prev, cur, nxt))
        if best is None:
            _drop(bm, created)
            return None
        _key, position, (prev, cur, nxt) = best
        try:
            created.append(bm.faces.new((nxt, cur, prev) if forward
                                        else (prev, cur, nxt)))
        except ValueError:  # pragma: no cover - the pre-checks cover this
            _drop(bm, created)
            return None
        if total <= 3:
            break
        live.pop(position)
    if _patch_ok(created, dominant, rule):
        return created
    _drop(bm, created)
    return None


def _close_rim(bm, cycle, edges, dominant, rule=None):
    """Close one rim without re-welding anything. Returns the new faces or None.

    Three strategies, cheapest first:

    1. **One n-gon over the rim's own edges** (``contextual_create``). It adds no
       new edge at all, so it cannot join two vertices that were not already
       joined — the safest possible fill. It is used whenever the rim's parts
       contain no pair that must stay apart, which covers every rim that is
       wholly inside one part.
    2. **Two n-gons either side of a chord** (:func:`_split_fill`), for a rim
       that cuts cleanly into two arcs which must not touch each other. It adds
       exactly one edge and that edge is inside one part, so the arm closes as
       an arm and the trunk as a trunk with no new contact between them. This
       is the werewolf armpit's fill.
    3. **Legal ear clipping** (:func:`_ear_fill`), for a rim that wants more
       than two arcs — wip-15's, where the hand/thigh weld runs into the armpit
       one and the rim carries five parts.
    4. **A triangle fan from a part-compatible pivot**, kept as the last
       fallback. The pivot is the lowest-index rim vertex whose part may touch
       *every* part on the rim, so every edge the fan creates is a within-part
       or junction edge.

    Every candidate is built, checked by :func:`_patch_ok` and rolled back if it
    re-bridges or leaves an edge with three faces, so a fill that cannot be done
    cleanly is not done at all.
    """
    parts = {dominant.get(vert.index) for vert in cycle}
    parts.discard(None)
    if not _defect_pair(parts, rule):
        try:
            made = bmesh.ops.contextual_create(bm, geom=list(edges))
            created = list(made.get("faces", ()))
        except (RuntimeError, ValueError):  # pragma: no cover - defensive
            created = []
        if created and _patch_ok(created, dominant, rule):
            return created
        _drop(bm, created)

    for fill in (_split_fill, _ear_fill):
        made = fill(bm, cycle, edges, dominant, rule)
        if made:
            return made

    for pivot in sorted(cycle, key=lambda vert: vert.index):
        mine = dominant.get(pivot.index)
        if mine is None:
            continue
        if any(_contact_is_defect(mine, other, rule)
               for other in parts if other != mine):
            continue
        created = []
        for edge in edges:
            if pivot in edge.verts:
                continue
            start, end = _rim_direction(edge)
            # Reverse of the surviving face's winding, so the patch faces the
            # same way as the surface it closes.
            try:
                created.append(bm.faces.new((pivot, end, start)))
            except ValueError:
                created = None
                break
        if created and _patch_ok(created, dominant, rule):
            return created
        _drop(bm, created or ())
    return None


@command("mesh_repair_bridges")
def cmd_mesh_repair_bridges(params):
    """Cut the welds between body parts that should be separate, and close up.

    The membrane the artist films is a sheet of faces whose corners belong to two
    parts that never touch — a hand fused to a thigh.  Deleting those faces frees
    the parts and opens one hole in each; both holes are then filled **inside
    their own part's surface**, so the arm closes as an arm and the thigh as a
    thigh and nothing new crosses between them.

    It cuts the **misplaced junctions** too, on the same pass and by the same
    rule: a contact inside an adjacent pair that sits where the junction cannot
    bend (see :class:`JunctionRule`).  That category needs the rig, so the
    detector and the cut both read the one supplied or discovered here, and a
    mesh with no armature gets the tag-adjacency cut alone and a sentence
    saying so.

    - ``object``: which mesh; omitted = the active object.
    - ``rig``: which armature to read the tag contract off; omitted = the
      mesh's own armature modifier or armature parent.
    - ``dry_run``: measure and report the plan, change nothing (default false).
    - ``max_fraction``: refuse when a bridge would take more than this fraction
      of any one part's faces (default :data:`BRIDGE_REPAIR_MAX_FRACTION`, 5%).
      A bridge that big is a mis-tag or a part fused by design, not a weld, and
      deleting it would gouge the part rather than free it.
    - ``examples``: how many located examples to carry back, 1-25.

    Deterministic (every walk is in index order), undo-safe (the registry pushes
    a checkpoint before it runs) and reported: the result quotes the detector's
    numbers before, what was removed and filled, and the detector's numbers
    after.  It fixes the MESH only — re-skinning, re-weighting and re-baking the
    parts it separated are the pipeline's job, not this tool's.
    """
    limit = common.get_int(params, "examples", DEFAULT_EXAMPLES,
                           minimum=1, maximum=MAX_EXAMPLES)
    dry_run = common.get_bool(params, "dry_run", False)
    max_fraction = common.get_float(params, "max_fraction",
                                    BRIDGE_REPAIR_MAX_FRACTION,
                                    minimum=0.0, maximum=1.0)
    obj = common.resolve_object(params, mesh_only=True)
    common.refresh_view_layer()

    started = time.monotonic()
    matrix = getattr(obj, "matrix_world", None)
    rule, junction_note = junction_rule(obj, rig_for(obj, params))
    before = bridge_report(obj, limit=limit, matrix=matrix, rule=rule)
    if junction_note:
        before.setdefault("junction", {})["note"] = junction_note
    result = {
        "object": obj.name,
        "dry_run": bool(dry_run),
        "max_fraction": max_fraction,
        "junction_note": junction_note,
        "before": before,
        "repaired": False,
        "faces_removed": 0,
        "faces_added": 0,
        "edges_removed": 0,
        "loose_vertices_removed": 0,
        "holes_filled": 0,
        "holes_refused": [],
        "vertices_before": len(obj.data.vertices),
        "vertices_after": len(obj.data.vertices),
    }
    if not before.get("scanned"):
        result["message"] = before.get("note", "Nothing to check.")
        result["after"] = before
        result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
        return result
    if not before.get("count") and not before.get("misplaced_edges"):
        result["message"] = ("No cross-part bridges on %r: every edge either "
                             "stays inside one part or crosses a legitimate "
                             "junction." % obj.name)
        result["after"] = before
        result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
        return result

    dominant, _groups = dominant_tags(obj)
    # Two views of the same tags, on purpose. `refined` (slab granularity) is
    # what the cut and every rim fill read, because a misplaced junction is
    # only visible there. `dominant` (the coarse part) is what the refusal line
    # below is measured in, because BRIDGE_REPAIR_MAX_FRACTION is derived from
    # how much of a PART a weld may eat — measured against a slab instead, the
    # werewolf's own armpit (94 faces off one chest slab) would refuse itself.
    refined = dict(dominant)
    if rule is not None:
        for index, tag in dominant.items():
            if tag is not None:
                refined[index] = rule.refine(index, tag)
    with common.object_mode():
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

            bad_edges = []
            for edge in bm.edges:
                tag_a = refined.get(edge.verts[0].index)
                tag_b = refined.get(edge.verts[1].index)
                if tag_a is None or tag_b is None or tag_a == tag_b:
                    continue
                if _contact_is_defect(tag_a, tag_b, rule):
                    bad_edges.append(edge)

            doomed = []
            doomed_seen = set()
            for edge in bad_edges:
                for face in edge.link_faces:
                    if face.index not in doomed_seen:
                        doomed_seen.add(face.index)
                        doomed.append(face)

            # The refusal line, per part: how much of this part would go.
            part_faces = {}
            for face in bm.faces:
                for vert in face.verts:
                    tag = dominant.get(vert.index)
                    if tag:
                        part_faces[tag] = part_faces.get(tag, 0) + 1
            part_doomed = {}
            for face in doomed:
                for vert in face.verts:
                    tag = dominant.get(vert.index)
                    if tag:
                        part_doomed[tag] = part_doomed.get(tag, 0) + 1
            shares = []
            for tag in sorted(part_doomed):
                total = max(part_faces.get(tag, 0), 1)
                shares.append({"part": tag, "faces": part_doomed[tag],
                               "of_part_faces": total,
                               "fraction": round(part_doomed[tag] / float(total), 5)})
            result["part_shares"] = shares
            worst = max(shares, key=lambda s: s["fraction"]) if shares else None
            if worst is not None and worst["fraction"] > max_fraction:
                result["refused"] = True
                result["message"] = (
                    "Refusing to auto-repair %r: the bridge touches %d of %s's "
                    "%d faces (%.1f%%), above the %.1f%% ceiling. A weld that "
                    "big is a mis-tag or a part that is fused by design, and "
                    "deleting it would gouge the part rather than free it. Fix "
                    "the tags (rigforge_autotag) or raise max_fraction "
                    "deliberately."
                    % (obj.name, worst["faces"], worst["part"],
                       worst["of_part_faces"], worst["fraction"] * 100.0,
                       max_fraction * 100.0))
                result["after"] = before
                result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
                return result

            result["faces_removed"] = len(doomed)
            result["edges_removed"] = len(bad_edges)
            if dry_run:
                result["message"] = (
                    "Dry run: would delete %d bridging face%s and %d welding "
                    "edge%s on %r, then close the holes inside each part."
                    % (len(doomed), "" if len(doomed) == 1 else "s",
                       len(bad_edges), "" if len(bad_edges) == 1 else "s",
                       obj.name))
                result["after"] = before
                result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
                return result

            pre_boundary = _boundary_keys(bm)
            shells_before = len(_shell_groups(bm))
            # FACES_ONLY, never FACES: the latter also sweeps up the vertices and
            # edges the deleted faces leave orphaned, which RENUMBERS every
            # vertex after the first one it takes — and `dominant` is keyed on
            # the numbering we walked in with. Four swept vertices were enough to
            # shift the tags under the rim analysis and turn clean single-part
            # rims into mixed ones. Orphans are swept at the end instead, once
            # nothing is looking anything up by index.
            bmesh.ops.delete(bm, geom=doomed, context="FACES_ONLY")
            for sequence in (bm.verts, bm.edges, bm.faces):
                sequence.ensure_lookup_table()

            loops, strays = _boundary_loops(bm, pre_boundary)
            faces_before_fill = len(bm.faces)
            filled = 0
            for cycle, edges in loops:
                parts = {refined.get(vert.index) for vert in cycle}
                parts.discard(None)
                created = _close_rim(bm, cycle, edges, refined, rule)
                if created is None:
                    # No vertex on this rim is adjacent to all the others, so
                    # every way of closing it re-welds something. Leave the hole
                    # and say where it is.
                    result["holes_refused"].append({
                        "parts": sorted(parts),
                        "edges": len(edges),
                        "location_mm": _mm(cycle[0].co, matrix),
                        "why": ("this rim runs through %s and no part on it "
                                "borders all the others, so any fill would "
                                "re-bridge them"
                                % " and ".join(sorted(parts))),
                    })
                    continue
                filled += 1
            for sequence in (bm.verts, bm.edges, bm.faces):
                sequence.ensure_lookup_table()
            result["holes_filled"] = filled
            result["faces_added"] = len(bm.faces) - faces_before_fill
            result["rims"] = len(loops)
            if strays:
                result["stray_boundary_edges"] = len(strays)

            wire = [edge for edge in bm.edges if not edge.link_faces]
            if wire:
                bmesh.ops.delete(bm, geom=wire, context="EDGES")
            loose = [vert for vert in bm.verts if not vert.link_faces]
            if loose:
                bmesh.ops.delete(bm, geom=loose, context="VERTS")
            result["loose_vertices_removed"] = len(loose)
            result["wire_edges_removed"] = len(wire)

            # Cutting a weld can set a flap free: a patch of surface that was
            # held on by the membrane and nothing else. That is the correct
            # result geometrically and a surprise if it is not said out loud, so
            # every piece that is not the body is counted and located.
            pieces = _shell_groups(bm)
            result["shells_before"] = shells_before
            result["shells_after"] = len(pieces)
            if len(pieces) > shells_before:
                detached = []
                for members in pieces[1:]:
                    centre = members[0].co.copy()
                    for vert in members[1:]:
                        centre += vert.co
                    centre /= float(len(members))
                    parts = sorted({dominant.get(vert.index)
                                    for vert in members} - {None})
                    detached.append({"vertices": len(members), "parts": parts,
                                     "location_mm": _mm(centre, matrix)})
                detached.sort(key=lambda entry: -entry["vertices"])
                result["detached_pieces"] = detached[:limit]

            bm.normal_update()
            bm.to_mesh(obj.data)
        finally:
            bm.free()
    obj.data.update()
    common.refresh_view_layer()

    # Rebuilt, not reused: the sweep of orphaned vertices at the end of the edit
    # RENUMBERS the mesh, and `rule`'s slab membership is keyed on the numbering
    # we walked in with. Reusing it read 233 phantom misplaced edges on a mesh
    # the detector calls clean when asked again from scratch.
    rule, junction_note = junction_rule(obj, rig_for(obj, params))
    after = bridge_report(obj, limit=limit, matrix=matrix, rule=rule)
    if junction_note:
        after.setdefault("junction", {})["note"] = junction_note
    result["after"] = after
    result["repaired"] = True
    result["vertices_after"] = len(obj.data.vertices)
    result["clean"] = not after.get("count") and not after.get("misplaced_edges")
    cut = [" and ".join(row["parts"])
           for row in before["pairs"] + before.get("misplaced", [])]
    left = after.get("count", 0) + after.get("misplaced_edges", 0)
    result["message"] = (
        "Removed %d bridging face%s and %d welding edge%s between %s on %r, "
        "closed %d hole%s inside their own parts; %d bridging edge%s left."
        % (result["faces_removed"], "" if result["faces_removed"] == 1 else "s",
           result["edges_removed"], "" if result["edges_removed"] == 1 else "s",
           ", ".join(cut), obj.name, result["holes_filled"],
           "" if result["holes_filled"] == 1 else "s",
           left, "" if left == 1 else "s"))
    result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
    return result


def _local_bounds(mesh):
    """Local-space min/max corner of a Mesh, vectorised where numpy exists."""
    count = len(mesh.vertices)
    if not count:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    if _np is not None:
        flat = _np.empty(count * 3, dtype="f4")
        mesh.vertices.foreach_get("co", flat)
        coords = flat.reshape(count, 3)
        return (tuple(float(v) for v in coords.min(axis=0)),
                tuple(float(v) for v in coords.max(axis=0)))
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for vertex in mesh.vertices:
        for axis in range(3):
            value = float(vertex.co[axis])
            low[axis] = min(low[axis], value)
            high[axis] = max(high[axis], value)
    return tuple(low), tuple(high)
