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
# the verdict
# ---------------------------------------------------------------------------

def verdict_lines(result):
    """The three or four sentences a teacher would actually say, worst first."""
    lines = []
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
