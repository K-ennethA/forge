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
#: **Its measured blind spot, recorded rather than papered over.**  The
#: werewolf also has the forearm welded to the flank at z ~ 1.15 m.  Half of
#: that weld is ``Arm``/``Leg`` and the primary rule takes it; the other half is
#: ``Arm.R``/``Torso``, which is a legitimate pair, and the weld runs
#: *continuously* into the genuine armpit seam — one patch, no spatial gap — so
#: no outlier test can separate them.  The cost is visible: after the repair the
#: worst edge anywhere on the mesh still stretches to **536.9 mm** at frame 13
#: of the rig's own ``jump`` action (it was 1198.8 mm before), and it is that
#: ``Arm.R``/``Torso`` edge.  Separating a weld from the seam it grew out of
#: needs the posed mesh — an armature and its actions — which is data this
#: stage does not have.  That is a ``verify_anim`` gate, not this one.
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
                  vertex_limit=BRIDGE_VERTEX_LIMIT):
    """Faces welded between parts that must stay separate, and WHERE.

    Reads the object's own mesh (not the evaluated one): tags live on the cage,
    the repair edits the cage, and a membrane is a property of the rest surface
    rather than of whatever pose the depsgraph happens to be holding.

    The result carries, per non-adjacent pair, one entry per contact patch with
    its edge count, face count, bridged area in mm^2 and its centroid in world
    millimetres — the shape the workspace pins want.  ``suspected`` holds the
    secondary signal (outlying patches inside a LEGAL pair) and ``unclassified``
    holds pairs the anatomy table has no opinion about; neither gates.
    """
    mesh = obj.data
    blank = {
        "count": 0, "faces": 0, "area_mm2": 0.0, "pairs": [], "examples": [],
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
        entries_by_pair.setdefault((pair, bool(verdict)), []).append(
            (edge.index, a, b, mid))

    # A face can bridge two parts without owning a single bad EDGE: corners in
    # two non-adjacent parts with a third part's corner sitting between them.
    # There are none on werewolf-wip-15 (measured: the edge rule and the corner
    # rule both select exactly 77 faces), but an edge-only gate would miss one,
    # and it is precisely the shape a careless hole-fill leaves behind — so the
    # faces are counted as well as the edges.
    bad_keys = set()
    for (_pair, legal), entries in entries_by_pair.items():
        if legal:
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

    pairs = []
    suspected = []
    total_edges = 0
    total_faces = 0
    total_area = 0.0
    for (pair, legal), entries in sorted(entries_by_pair.items()):
        found = patches(entries)
        if legal:
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
        places = [{key: value for key, value in patch.items()
                   if not key.startswith("_")} for patch in found]
        pair_edges = sum(patch["edges"] for patch in found)
        pair_faces = sum(patch["faces"] for patch in found)
        pair_area = sum(patch["area_mm2"] for patch in found)
        total_edges += pair_edges
        total_faces += pair_faces
        total_area += pair_area
        pairs.append({
            "parts": list(pair),
            "edges": pair_edges,
            "faces": pair_faces,
            "area_mm2": round(pair_area, 3),
            "places": places[:limit],
            "place_count": len(places),
        })

    pairs.sort(key=lambda entry: (-entry["area_mm2"], -entry["edges"],
                                  entry["parts"]))
    suspected.sort(key=lambda entry: (-entry["area_mm2"], entry["location_mm"]))
    examples = []
    for pair in pairs:
        for place in pair["places"]:
            examples.append({
                "parts": pair["parts"],
                "location_mm": place["location_mm"],
                "faces": place["faces"],
                "area_mm2": place["area_mm2"],
            })
    examples.sort(key=lambda entry: (-entry["area_mm2"], entry["location_mm"]))

    corner_area_mm2 = round(corner_area * common.M_TO_MM * common.M_TO_MM, 3)
    result = {
        "count": int(total_edges),
        "faces": int(total_faces + corner_count),
        "area_mm2": round(total_area + corner_area_mm2, 3),
        "corner_faces": {"count": int(corner_count),
                         "area_mm2": corner_area_mm2,
                         "examples": corner_hits},
        "pairs": pairs,
        "examples": examples[:limit],
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
        bridges = bridge_report(obj, limit=limit, matrix=matrix)
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
        # Red on a bridge between NON-ADJACENT parts only. `suspected` (the
        # secondary signal) and `unclassified` (parts the anatomy table has no
        # opinion about) are reported and never gate.
        and not bridges.get("count") and not bridges.get("faces")
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


def _nonadjacent_pair(parts):
    """The first non-adjacent pair among ``parts``, or ``None``."""
    ordered = sorted(part for part in parts if part)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if tags_adjacent(first, second) is False:
                return (first, second)
    return None


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


def _patch_ok(created, dominant):
    """Is this fill legal — no re-bridge, and nothing left non-manifold?"""
    for face in created:
        parts = {dominant.get(vert.index) for vert in face.verts}
        if _nonadjacent_pair(parts):
            return False
        for edge in face.edges:
            if len(edge.link_faces) > 2:
                return False
    return True


def _drop(bm, faces):
    live = [face for face in faces if face.is_valid]
    if live:
        bmesh.ops.delete(bm, geom=live, context="FACES_ONLY")


def _close_rim(bm, cycle, edges, dominant):
    """Close one rim without re-welding anything. Returns the new faces or None.

    Two strategies, cheapest first:

    1. **One n-gon over the rim's own edges** (``contextual_create``). It adds no
       new edge at all, so it cannot join two vertices that were not already
       joined — the safest possible fill. It is used whenever the rim's parts
       contain no non-adjacent pair, which covers every rim that is wholly
       inside one part.
    2. **A triangle fan from a part-compatible pivot**, for a rim that spans two
       parts which must not touch. The pivot is the lowest-index rim vertex
       whose part is adjacent to *every* part on the rim, so every edge the fan
       creates is a within-part or junction edge. At each end of the werewolf's
       hand/thigh weld the rim reads ``Arm.R, Arm.R, Torso, Leg.R, Leg.R,
       Leg.R, Torso``: an n-gon there would put an ``Arm.R`` corner and a
       ``Leg.R`` corner on one face and the membrane would be back, while a fan
       from a ``Torso`` vertex only ever makes ``Torso``-to-something edges.

    Every candidate is built, checked by :func:`_patch_ok` and rolled back if it
    re-bridges or leaves an edge with three faces, so a fill that cannot be done
    cleanly is not done at all.
    """
    parts = {dominant.get(vert.index) for vert in cycle}
    parts.discard(None)
    if not _nonadjacent_pair(parts):
        try:
            made = bmesh.ops.contextual_create(bm, geom=list(edges))
            created = list(made.get("faces", ()))
        except (RuntimeError, ValueError):  # pragma: no cover - defensive
            created = []
        if created and _patch_ok(created, dominant):
            return created
        _drop(bm, created)

    for pivot in sorted(cycle, key=lambda vert: vert.index):
        mine = dominant.get(pivot.index)
        if mine is None:
            continue
        if any(tags_adjacent(mine, other) is False
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
        if created and _patch_ok(created, dominant):
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

    - ``object``: which mesh; omitted = the active object.
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
    before = bridge_report(obj, limit=limit, matrix=matrix)
    result = {
        "object": obj.name,
        "dry_run": bool(dry_run),
        "max_fraction": max_fraction,
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
    if not before.get("count"):
        result["message"] = ("No cross-part bridges on %r: every edge either "
                             "stays inside one part or crosses a legitimate "
                             "junction." % obj.name)
        result["after"] = before
        result["duration_ms"] = int((time.monotonic() - started) * 1000.0)
        return result

    dominant, _groups = dominant_tags(obj)
    with common.object_mode():
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

            bad_edges = []
            for edge in bm.edges:
                tag_a = dominant.get(edge.verts[0].index)
                tag_b = dominant.get(edge.verts[1].index)
                if tag_a is None or tag_b is None or tag_a == tag_b:
                    continue
                if tags_adjacent(tag_a, tag_b) is False:
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
                parts = {dominant.get(vert.index) for vert in cycle}
                parts.discard(None)
                created = _close_rim(bm, cycle, edges, dominant)
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

    after = bridge_report(obj, limit=limit, matrix=matrix)
    result["after"] = after
    result["repaired"] = True
    result["vertices_after"] = len(obj.data.vertices)
    result["clean"] = not after.get("count")
    result["message"] = (
        "Removed %d bridging face%s and %d welding edge%s between %s on %r, "
        "closed %d hole%s inside their own parts; %d bridging edge%s left."
        % (result["faces_removed"], "" if result["faces_removed"] == 1 else "s",
           result["edges_removed"], "" if result["edges_removed"] == 1 else "s",
           ", ".join(" and ".join(pair["parts"]) for pair in before["pairs"]),
           obj.name, result["holes_filled"],
           "" if result["holes_filled"] == 1 else "s",
           after.get("count", 0), "" if after.get("count") == 1 else "s"))
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
