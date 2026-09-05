"""Print-readiness checks: bed fit, wall thickness, overhangs, watertightness.

Everything in here works on the **welded triangle mesh** that ``/generate``
already produces, plus the ``stats`` block and a printer profile.  No build123d,
no OCP: the checks are the same arithmetic a slicer would do, so they can be
unit-tested without the kernel and they measure exactly the geometry that leaves
the service.

The four checks
---------------

``bed_fit``
    Bounding box against the bed, in each of the six axis-aligned orientations
    (a part axis pointing at the ceiling) and both 90-degree placements on the
    bed.  When nothing fits, the check proposes a segmentation: radial for
    ring-like parts (a hole down the middle and a mostly-empty bounding box),
    planar Z cuts otherwise.

``min_wall``
    Inward ray casting from facet centroids.  See :func:`check_min_wall` for the
    method and, more importantly, for what it cannot see.

``overhangs``
    Per-facet angle from vertical against
    ``printer.max_unsupported_overhang_deg``, evaluated for all six axis-aligned
    build directions, with the unsupported area and a rough support-volume
    estimate per orientation and the best one suggested.

``watertight``
    A restatement of the existing manifold analysis in
    :func:`runner.compute_stats` -- no new geometry work.

Status vocabulary is the contract's: ``pass`` / ``warn`` / ``fail``.  The
overall status is the worst of the four.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .printer import DEFAULT_PLATE_MARGIN_MM, bed_size

Vec3 = Tuple[float, float, float]

PASS = "pass"
WARN = "warn"
FAIL = "fail"

_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}

#: Axis labels for the six axis-aligned build directions.  The label names the
#: part axis that ends up pointing at the ceiling, so ``"+Z"`` is "as modelled".
ORIENTATIONS: Tuple[Tuple[str, Vec3], ...] = (
    ("+Z", (0.0, 0.0, 1.0)),
    ("-Z", (0.0, 0.0, -1.0)),
    ("+X", (1.0, 0.0, 0.0)),
    ("-X", (-1.0, 0.0, 0.0)),
    ("+Y", (0.0, 1.0, 0.0)),
    ("-Y", (0.0, -1.0, 0.0)),
)

#: The orientation the part is modelled in.
CURRENT_ORIENTATION = "+Z"

#: Facets whose highest point sits within this of the build plate are resting on
#: it and need no support, however flat they are.
BED_CONTACT_EPS_MM = 0.05

#: Most facets a wall probe will sample.  Above this the facets are strided so a
#: dense tessellation costs time linear in this constant, not in the mesh.
DEFAULT_MAX_WALL_SAMPLES = 4000

#: Thin regions are clustered onto a grid this many multiples of the printer's
#: minimum wall across, so a report names places rather than triangles.
THIN_CLUSTER_CELLS = 8.0

#: At most this many thin regions come back; the thinnest survive.
MAX_THIN_REGIONS = 20

#: Ray-triangle intersections closer than this to the ray origin are the source
#: facet meeting itself.
RAY_EPS_MM = 1e-6

# A part is "ring-like", and so a candidate for radial cuts, when all three of
# these hold: its bounding box is at most RING_MAX_MATERIAL_RATIO full, its
# footprint is no more elongated than RING_MAX_FOOTPRINT_ASPECT, and a vertical
# line down the middle of that footprint passes through no material at all.
RING_MAX_MATERIAL_RATIO = 0.7
RING_MAX_FOOTPRINT_ASPECT = 1.6

#: Largest radial segment count auto-segmentation will propose.
MAX_RADIAL_SEGMENTS = 64
#: Largest number of planar slices auto-segmentation will propose.
MAX_PLANAR_SLICES = 32


# --------------------------------------------------------------------------
# Small vector helpers (tuples, not numpy: the meshes here are thousands of
# triangles, and a numpy dependency would have to be justified to the worker)
# --------------------------------------------------------------------------


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


# --------------------------------------------------------------------------
# Facet geometry
# --------------------------------------------------------------------------


class MeshGeometry:
    """Per-facet normals, areas and centroids, computed once and shared.

    Triangles with zero area are dropped up front: they carry no normal, so any
    angle or thickness derived from them would be noise.
    """

    def __init__(
        self,
        vertices: Sequence[Sequence[float]],
        faces: Sequence[Sequence[int]],
    ) -> None:
        self.vertices: List[Vec3] = [
            (float(v[0]), float(v[1]), float(v[2])) for v in vertices
        ]
        self.triangles: List[Tuple[int, int, int]] = []
        self.normals: List[Vec3] = []
        self.areas: List[float] = []
        self.centroids: List[Vec3] = []
        self.degenerate = 0

        for face in faces:
            if len(face) != 3:
                # Only triangles reach here; the tessellator emits nothing else.
                self.degenerate += 1
                continue
            ia, ib, ic = int(face[0]), int(face[1]), int(face[2])
            a, b, c = self.vertices[ia], self.vertices[ib], self.vertices[ic]
            normal = _cross(_sub(b, a), _sub(c, a))
            magnitude = _length(normal)
            if magnitude <= 0.0:
                self.degenerate += 1
                continue
            self.triangles.append((ia, ib, ic))
            self.normals.append(
                (normal[0] / magnitude, normal[1] / magnitude, normal[2] / magnitude)
            )
            self.areas.append(magnitude * 0.5)
            self.centroids.append(
                (
                    (a[0] + b[0] + c[0]) / 3.0,
                    (a[1] + b[1] + c[1]) / 3.0,
                    (a[2] + b[2] + c[2]) / 3.0,
                )
            )

        self.total_area = math.fsum(self.areas)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.triangles)

    def bounds(self) -> Tuple[Vec3, Vec3]:
        if not self.vertices:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def volume(self) -> float:
        """Signed volume via the divergence theorem; meaningful when closed."""
        total = 0.0
        for ia, ib, ic in self.triangles:
            a, b, c = self.vertices[ia], self.vertices[ib], self.vertices[ic]
            total += _dot(a, _cross(b, c))
        return abs(total) / 6.0


# --------------------------------------------------------------------------
# Short-ray casting against the mesh
# --------------------------------------------------------------------------


class TriangleGrid:
    """A uniform grid of triangle indices, for *short* ray queries.

    Deliberately not a BVH and deliberately not a full DDA traversal.  Every
    query this module makes is a probe a few millimetres long, so gathering the
    triangles in the cells overlapping the probe segment's own bounding box is
    both exact and cheap.  A long ray would gather far too much -- which is why
    the one long-ray question the module asks (does the central axis pass
    through material?) is answered by a 2-D point-in-triangle sweep instead.
    """

    def __init__(self, geometry: MeshGeometry, cell_size: float) -> None:
        self.geometry = geometry
        self.cell = max(float(cell_size), 1e-6)
        self.buckets: Dict[Tuple[int, int, int], List[int]] = {}

        for index, (ia, ib, ic) in enumerate(geometry.triangles):
            a, b, c = (
                geometry.vertices[ia],
                geometry.vertices[ib],
                geometry.vertices[ic],
            )
            lo = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]), min(a[2], b[2], c[2]))
            hi = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]), max(a[2], b[2], c[2]))
            for key in self._cells(lo, hi):
                self.buckets.setdefault(key, []).append(index)

    def _cells(self, lo: Vec3, hi: Vec3):
        cell = self.cell
        x0, y0, z0 = (int(math.floor(v / cell)) for v in lo)
        x1, y1, z1 = (int(math.floor(v / cell)) for v in hi)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    yield (x, y, z)

    def candidates(self, lo: Vec3, hi: Vec3) -> List[int]:
        seen: Dict[int, None] = {}
        for key in self._cells(lo, hi):
            bucket = self.buckets.get(key)
            if bucket:
                for index in bucket:
                    seen[index] = None
        return list(seen)

    def first_hit(
        self,
        origin: Vec3,
        direction: Vec3,
        max_distance: float,
        skip: Optional[int] = None,
    ) -> Optional[float]:
        """Distance to the nearest triangle along *direction*, or ``None``."""
        end = (
            origin[0] + direction[0] * max_distance,
            origin[1] + direction[1] * max_distance,
            origin[2] + direction[2] * max_distance,
        )
        lo = (min(origin[0], end[0]), min(origin[1], end[1]), min(origin[2], end[2]))
        hi = (max(origin[0], end[0]), max(origin[1], end[1]), max(origin[2], end[2]))

        best: Optional[float] = None
        geometry = self.geometry
        for index in self.candidates(lo, hi):
            if index == skip:
                continue
            ia, ib, ic = geometry.triangles[index]
            distance = _ray_triangle(
                origin,
                direction,
                geometry.vertices[ia],
                geometry.vertices[ib],
                geometry.vertices[ic],
            )
            if distance is None or distance <= RAY_EPS_MM or distance > max_distance:
                continue
            if best is None or distance < best:
                best = distance
        return best


def _ray_triangle(
    origin: Vec3, direction: Vec3, a: Vec3, b: Vec3, c: Vec3
) -> Optional[float]:
    """Moller-Trumbore, two-sided (a wall probe hits back faces on purpose)."""
    edge1 = _sub(b, a)
    edge2 = _sub(c, a)
    pvec = _cross(direction, edge2)
    det = _dot(edge1, pvec)
    if -1e-12 < det < 1e-12:
        return None
    inv_det = 1.0 / det
    tvec = _sub(origin, a)
    u = _dot(tvec, pvec) * inv_det
    if u < -1e-9 or u > 1.0 + 1e-9:
        return None
    qvec = _cross(tvec, edge1)
    v = _dot(direction, qvec) * inv_det
    if v < -1e-9 or u + v > 1.0 + 1e-9:
        return None
    return _dot(edge2, qvec) * inv_det


def _axis_crossings(geometry: MeshGeometry, x: float, y: float) -> int:
    """How many facets a vertical line at (x, y) passes through.

    Zero means the part has a hole (or a gap) straight down its middle, which is
    the signal radial segmentation looks for.  Answered by projecting to XY and
    testing point-in-triangle, so it costs one pass over the facets and needs no
    acceleration structure.
    """
    crossings = 0
    for ia, ib, ic in geometry.triangles:
        ax, ay = geometry.vertices[ia][0], geometry.vertices[ia][1]
        bx, by = geometry.vertices[ib][0], geometry.vertices[ib][1]
        cx, cy = geometry.vertices[ic][0], geometry.vertices[ic][1]
        d1 = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        d2 = (cx - bx) * (y - by) - (cy - by) * (x - bx)
        d3 = (ax - cx) * (y - cy) - (ay - cy) * (x - cx)
        has_negative = d1 < 0.0 or d2 < 0.0 or d3 < 0.0
        has_positive = d1 > 0.0 or d2 > 0.0 or d3 > 0.0
        if not (has_negative and has_positive):
            crossings += 1
    return crossings


# --------------------------------------------------------------------------
# bed_fit
# --------------------------------------------------------------------------


def _orientation_size(size: Vec3, up_axis: int) -> Tuple[float, float, float]:
    """(footprint_a, footprint_b, height) for a part axis pointing up."""
    height = size[up_axis]
    footprint = [size[i] for i in range(3) if i != up_axis]
    return footprint[0], footprint[1], height


def _footprint_fits(a: float, b: float, bed_x: float, bed_y: float) -> bool:
    return (a <= bed_x and b <= bed_y) or (b <= bed_x and a <= bed_y)


def check_bed_fit(
    geometry: MeshGeometry,
    stats: Mapping[str, Any],
    printer: Mapping[str, Any],
    margin_mm: float = DEFAULT_PLATE_MARGIN_MM,
) -> Dict[str, Any]:
    """Bounding box against the bed, with a segmentation proposal on failure."""
    bed_x, bed_y, bed_z = bed_size(printer)
    size = tuple(float(v) for v in stats["bounding_box_mm"])  # type: ignore[arg-type]
    low = tuple(float(v) for v in stats.get("bounding_box_min_mm", (0.0, 0.0, 0.0)))
    high = tuple(float(v) for v in stats.get("bounding_box_max_mm", size))

    usable_x = max(bed_x - 2.0 * margin_mm, 0.0)
    usable_y = max(bed_y - 2.0 * margin_mm, 0.0)

    placements: List[Dict[str, Any]] = []
    for label, direction in ORIENTATIONS:
        axis = 0 if direction[0] else (1 if direction[1] else 2)
        fa, fb, height = _orientation_size(size, axis)  # type: ignore[arg-type]
        placements.append(
            {
                "orientation": label,
                "footprint_mm": [round(fa, 4), round(fb, 4)],
                "height_mm": round(height, 4),
                "fits": bool(height <= bed_z and _footprint_fits(fa, fb, bed_x, bed_y)),
                "fits_with_margin": bool(
                    height <= bed_z and _footprint_fits(fa, fb, usable_x, usable_y)
                ),
            }
        )

    fitting = [p for p in placements if p["fits"]]
    with_margin = [p for p in placements if p["fits_with_margin"]]

    data: Dict[str, Any] = {
        "bed_mm": [bed_x, bed_y, bed_z],
        "margin_mm": margin_mm,
        "bounding_box_mm": [round(v, 4) for v in size],
        "orientations": placements,
        "fitting_orientations": [p["orientation"] for p in fitting],
    }

    if with_margin:
        status = PASS
        details = (
            f"fits the {bed_x:g}x{bed_y:g}x{bed_z:g} mm bed with {margin_mm:g} mm to "
            f"spare in {len(with_margin)} of 6 axis-aligned orientations"
        )
    elif fitting:
        status = WARN
        details = (
            f"fits the bed only without the {margin_mm:g} mm edge margin "
            f"(orientations: {', '.join(p['orientation'] for p in fitting)})"
        )
    else:
        status = FAIL
        suggestion = suggest_segmentation(geometry, size, low, high, printer, margin_mm)
        data["suggested_segmentation"] = suggestion
        details = (
            f"{size[0]:.1f}x{size[1]:.1f}x{size[2]:.1f} mm does not fit the "
            f"{bed_x:g}x{bed_y:g}x{bed_z:g} mm bed in any axis-aligned orientation; "
            + str(suggestion.get("reason", ""))
        )

    return {"name": "bed_fit", "status": status, "details": details, "data": data}


def ring_metrics(
    geometry: MeshGeometry, low: Vec3, high: Vec3, size: Vec3
) -> Dict[str, Any]:
    """The evidence behind the ring-like verdict, so a caller can second-guess it."""
    centre_x = (low[0] + high[0]) / 2.0
    centre_y = (low[1] + high[1]) / 2.0

    radii = [
        math.hypot(v[0] - centre_x, v[1] - centre_y) for v in geometry.vertices
    ] or [0.0]
    outer_radius = max(radii)
    inner_radius = min(radii)

    bbox_volume = size[0] * size[1] * size[2]
    material_ratio = (geometry.volume() / bbox_volume) if bbox_volume > 0 else 1.0

    footprint = sorted((size[0], size[1]))
    aspect = (footprint[1] / footprint[0]) if footprint[0] > 0 else math.inf

    crossings = _axis_crossings(geometry, centre_x, centre_y)

    ring_like = bool(
        crossings == 0
        and material_ratio < RING_MAX_MATERIAL_RATIO
        and aspect <= RING_MAX_FOOTPRINT_ASPECT
        and outer_radius > 0.0
    )

    return {
        "ring_like": ring_like,
        "centre_mm": [round(centre_x, 4), round(centre_y, 4)],
        "outer_radius_mm": round(outer_radius, 4),
        "inner_radius_mm": round(inner_radius, 4),
        "material_ratio": round(material_ratio, 5),
        "footprint_aspect": round(aspect, 4) if math.isfinite(aspect) else None,
        "central_axis_crossings": crossings,
    }


def _wedge_footprint(
    outer_radius: float, inner_radius: float, count: int
) -> Tuple[float, float]:
    """XY bounding box of one of *count* equal sectors of an annulus."""
    half = math.pi / count
    if half >= math.pi / 2.0:
        x_min = -outer_radius
        y_max = outer_radius
    else:
        x_min = inner_radius * math.cos(half)
        y_max = outer_radius * math.sin(half)
    return outer_radius - x_min, 2.0 * y_max


def suggest_segmentation(
    geometry: MeshGeometry,
    size: Vec3,
    low: Vec3,
    high: Vec3,
    printer: Mapping[str, Any],
    margin_mm: float = DEFAULT_PLATE_MARGIN_MM,
) -> Dict[str, Any]:
    """Propose a cut that would make the part printable.

    Radial when the part reads as ring-like -- a hole straight down the middle,
    a roughly round footprint and a mostly-empty bounding box -- otherwise
    planar cuts along Z.  The result is exactly the ``mode`` object
    ``/segment`` accepts, so ``"auto"`` is just this call plumbed through.
    """
    bed_x, bed_y, bed_z = bed_size(printer)
    usable_x = max(bed_x - 2.0 * margin_mm, 0.0)
    usable_y = max(bed_y - 2.0 * margin_mm, 0.0)

    metrics = ring_metrics(geometry, low, high, size)

    # Asked about a part that already fits, the honest answer is "don't cut it".
    # ``mode: "auto"`` routes through here, so this is the case where the caller
    # wants segments and the part does not need any.
    if size[2] <= bed_z and _footprint_fits(size[0], size[1], usable_x, usable_y):
        return {
            "kind": "none",
            "mode": {"planar": []},
            "feasible": True,
            "reason": (
                f"{size[0]:.1f}x{size[1]:.1f}x{size[2]:.1f} mm already fits the "
                f"usable bed ({usable_x:g}x{usable_y:g}x{bed_z:g} mm); no cuts needed"
            ),
            "estimated_segment_bbox_mm": [round(v, 3) for v in size],
            "metrics": metrics,
        }

    if metrics["ring_like"]:
        outer = float(metrics["outer_radius_mm"])
        inner = float(metrics["inner_radius_mm"])
        for count in range(2, MAX_RADIAL_SEGMENTS + 1):
            width_x, width_y = _wedge_footprint(outer, inner, count)
            if size[2] <= bed_z and _footprint_fits(width_x, width_y, usable_x, usable_y):
                return {
                    "kind": "radial",
                    "mode": {"radial": count},
                    "feasible": True,
                    "reason": (
                        f"ring-like part (nothing on the central axis, bounding box "
                        f"{metrics['material_ratio'] * 100:.1f}% full): cut into "
                        f"{count} equal arcs about Z"
                    ),
                    "estimated_segment_bbox_mm": [
                        round(width_x, 3),
                        round(width_y, 3),
                        round(size[2], 3),
                    ],
                    "metrics": metrics,
                }
        return {
            "kind": "radial",
            "mode": None,
            "feasible": False,
            "reason": (
                f"ring-like, but even {MAX_RADIAL_SEGMENTS} radial segments would not "
                f"fit the bed (height {size[2]:.1f} mm vs bed Z {bed_z:g} mm?)"
            ),
            "metrics": metrics,
        }

    # Planar: only Z cuts, so the XY footprint has to fit on its own.
    footprint_ok = _footprint_fits(size[0], size[1], usable_x, usable_y)
    if not footprint_ok:
        return {
            "kind": "planar",
            "mode": None,
            "feasible": False,
            "reason": (
                f"the {size[0]:.1f}x{size[1]:.1f} mm footprint is larger than the "
                f"usable bed ({usable_x:g}x{usable_y:g} mm) and planar cuts only "
                "divide Z; reduce the part or cut it by hand"
            ),
            "metrics": metrics,
        }

    slices = max(2, int(math.ceil(size[2] / bed_z)))
    if slices > MAX_PLANAR_SLICES:
        return {
            "kind": "planar",
            "mode": None,
            "feasible": False,
            "reason": (
                f"{size[2]:.1f} mm tall would need {slices} slices, more than the "
                f"{MAX_PLANAR_SLICES} this service will propose"
            ),
            "metrics": metrics,
        }
    step = size[2] / slices
    cuts = [round(low[2] + step * (i + 1), 4) for i in range(slices - 1)]
    return {
        "kind": "planar",
        "mode": {"planar": cuts},
        "feasible": True,
        "reason": (
            f"not ring-like: cut into {slices} slabs with planar Z cuts at "
            f"{', '.join(f'{c:g}' for c in cuts)} mm"
        ),
        "estimated_segment_bbox_mm": [
            round(size[0], 3),
            round(size[1], 3),
            round(step, 3),
        ],
        "metrics": metrics,
    }


# --------------------------------------------------------------------------
# min_wall
# --------------------------------------------------------------------------


def check_min_wall(
    geometry: MeshGeometry,
    printer: Mapping[str, Any],
    probe_mm: Optional[float] = None,
    max_samples: int = DEFAULT_MAX_WALL_SAMPLES,
) -> Dict[str, Any]:
    """Approximate thin-wall detection by casting rays into the solid.

    Method
    ------
    From each sampled facet's centroid, a ray is cast **inwards** -- along the
    reversed facet normal -- and the distance to the first triangle it meets is
    taken as the wall thickness at that point.  Rays are cut off at ``probe_mm``
    (four times the printer's minimum wall by default): the question is only
    whether a wall is too thin, so anything thicker than the probe is reported
    as "at least ``probe_mm``" and costs nothing more to find out.

    What this does not see
    ----------------------
    This is a mesh approximation, not a B-Rep wall analysis, and it is wrong in
    known directions:

    * **It measures along the normal, not the shortest path.**  A rib that is
      thin across a direction other than its own surface normal -- a wedge, a
      tapering fin -- reads thicker than it prints.  Thickness is under-reported
      for any feature whose thinnest axis is not perpendicular to the surface
      being sampled.
    * **Narrow gaps read as thin walls.**  A ray leaving a concave pocket can
      cross empty space and strike the far side, reporting the gap as the
      thickness.  That is a conservative error: a gap narrower than the nozzle
      is its own printing problem, but the reported *number* is then a gap, not
      a wall.
    * **It samples facets, not the surface.**  Large flat facets contribute one
      probe each, so a thin spot smaller than a facet in the middle of a big
      face can be missed; and with more facets than ``max_samples`` the facets
      are strided, which can miss a thin region entirely.
    * **Curvature biases the result.**  On a convex surface the inward normal
      leaves the material sooner than the true wall thickness; on a concave one
      it stays inside longer.
    * **Open meshes give nothing.**  A ray that hits no triangle within the
      probe is recorded as unmeasured rather than as infinitely thick.

    Treat a ``fail`` as "look here", not as a dimension.  The authoritative
    numbers are the ones in the script's ``PARAMS``.
    """
    min_wall = float(printer["min_wall_thickness"])
    min_feature = float(printer.get("min_feature_size", min_wall))
    probe = float(probe_mm) if probe_mm else max(4.0 * min_wall, 3.0)
    probe = max(probe, min_feature * 1.5, min_wall * 1.5)

    facet_count = len(geometry.triangles)
    if facet_count == 0:
        return {
            "name": "min_wall",
            "status": FAIL,
            "details": "the mesh has no facets to measure",
            "data": {"sampled_facets": 0},
        }

    stride = max(1, int(math.ceil(facet_count / float(max(1, max_samples)))))
    grid = TriangleGrid(geometry, probe)

    thinnest: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    cell = max(min_wall * THIN_CLUSTER_CELLS, probe)
    measured = 0
    unmeasured = 0
    min_thickness: Optional[float] = None
    below_wall = 0
    below_feature = 0

    for index in range(0, facet_count, stride):
        normal = geometry.normals[index]
        centroid = geometry.centroids[index]
        inward = (-normal[0], -normal[1], -normal[2])
        origin = (
            centroid[0] + inward[0] * RAY_EPS_MM,
            centroid[1] + inward[1] * RAY_EPS_MM,
            centroid[2] + inward[2] * RAY_EPS_MM,
        )
        distance = grid.first_hit(origin, inward, probe, skip=index)
        if distance is None:
            unmeasured += 1
            continue

        measured += 1
        if min_thickness is None or distance < min_thickness:
            min_thickness = distance
        if distance < min_wall:
            below_wall += 1
        elif distance < min_feature:
            below_feature += 1
        else:
            continue

        key = (
            int(math.floor(centroid[0] / cell)),
            int(math.floor(centroid[1] / cell)),
            int(math.floor(centroid[2] / cell)),
        )
        existing = thinnest.get(key)
        if existing is None or distance < existing["thickness_mm"]:
            thinnest[key] = {
                "thickness_mm": round(distance, 4),
                "location_mm": [round(v, 3) for v in centroid],
                "facet": index,
            }

    regions = sorted(thinnest.values(), key=lambda r: r["thickness_mm"])[
        :MAX_THIN_REGIONS
    ]

    data: Dict[str, Any] = {
        "method": "inward facet-normal ray casting (approximate)",
        "min_wall_thickness_mm": min_wall,
        "min_feature_size_mm": min_feature,
        "probe_mm": round(probe, 4),
        "facet_count": facet_count,
        "sampled_facets": len(range(0, facet_count, stride)),
        "sample_stride": stride,
        "measured": measured,
        "unmeasured": unmeasured,
        "below_min_wall": below_wall,
        "below_min_feature": below_feature,
        "thin_regions": regions,
        "thin_region_count": len(thinnest),
    }
    if min_thickness is not None:
        data["min_measured_thickness_mm"] = round(min_thickness, 4)
        data["min_measured_is_capped"] = bool(min_thickness >= probe - 1e-9)

    if below_wall:
        status = FAIL
        details = (
            f"{below_wall} of {measured} probes measured less than the "
            f"{min_wall:g} mm minimum wall (thinnest {min_thickness:.3f} mm at "
            f"{regions[0]['location_mm'] if regions else '?'})"
        )
    elif below_feature:
        status = WARN
        details = (
            f"{below_feature} probes fell between the {min_wall:g} mm minimum wall "
            f"and the {min_feature:g} mm minimum feature size "
            f"(thinnest {min_thickness:.3f} mm)"
        )
    elif measured == 0 and unmeasured == 0:
        status = WARN
        details = "no facet could be probed at all"
    elif measured == 0:
        # Every ray ran the full probe length without meeting anything, which on
        # a closed mesh means every sampled wall is thicker than the probe.  The
        # watertight check is what catches an open mesh; this one does not
        # second-guess it.
        status = PASS
        details = (
            f"every one of {unmeasured} probes ran the full {probe:g} mm without "
            f"meeting the far side, so nothing sampled is near the {min_wall:g} mm "
            "minimum wall"
        )
    else:
        status = PASS
        capped = data.get("min_measured_is_capped")
        thinnest_text = (
            f"at least {probe:g} mm" if capped else f"{min_thickness:.3f} mm"
        )
        details = (
            f"thinnest of {measured} probes is {thinnest_text}, clear of the "
            f"{min_wall:g} mm minimum wall"
        )

    return {"name": "min_wall", "status": status, "details": details, "data": data}


# --------------------------------------------------------------------------
# overhangs
# --------------------------------------------------------------------------


def _overhang_for_direction(
    geometry: MeshGeometry,
    up: Vec3,
    limit_deg: float,
    bed_x: float,
    bed_y: float,
    bed_z: float,
) -> Dict[str, Any]:
    """Unsupported facet area and a rough support volume for one build direction."""
    heights = [_dot(v, up) for v in geometry.vertices]
    base = min(heights) if heights else 0.0

    unsupported_area = 0.0
    steepest = 0.0
    support_volume = 0.0
    facets = 0

    for index, normal in enumerate(geometry.normals):
        downward = -_dot(normal, up)
        if downward <= 0.0:
            continue  # facing up or vertical: never an overhang
        # Angle from vertical: 0 for a wall parallel to the build direction,
        # 90 for a flat ceiling.  This is the slicer's convention, which is what
        # printer.max_unsupported_overhang_deg is expressed in.
        angle = math.degrees(math.asin(min(1.0, downward)))
        if angle <= limit_deg:
            continue

        ia, ib, ic = geometry.triangles[index]
        top = max(
            _dot(geometry.vertices[ia], up),
            _dot(geometry.vertices[ib], up),
            _dot(geometry.vertices[ic], up),
        )
        if top <= base + BED_CONTACT_EPS_MM:
            continue  # resting on the plate

        area = geometry.areas[index]
        unsupported_area += area
        facets += 1
        steepest = max(steepest, angle)
        # Rough: the column under the facet's projected footprint, all the way
        # down to the lowest point of the part.  It ignores material already in
        # the way, so it over-estimates -- it ranks orientations, it does not
        # price filament.
        centroid_height = _dot(geometry.centroids[index], up) - base
        support_volume += area * downward * max(centroid_height, 0.0)

    size = _oriented_size(geometry, up)
    fits = size[2] <= bed_z and _footprint_fits(size[0], size[1], bed_x, bed_y)

    return {
        "unsupported_area_mm2": round(unsupported_area, 3),
        "unsupported_facets": facets,
        "unsupported_fraction": round(
            unsupported_area / geometry.total_area if geometry.total_area else 0.0, 5
        ),
        "steepest_overhang_deg": round(steepest, 2),
        "support_volume_estimate_mm3": round(support_volume, 1),
        "fits_bed": bool(fits),
    }


def _oriented_size(geometry: MeshGeometry, up: Vec3) -> Tuple[float, float, float]:
    """Bounding-box size with *up* as the build direction (axis-aligned only)."""
    low, high = geometry.bounds()
    size = (high[0] - low[0], high[1] - low[1], high[2] - low[2])
    axis = 0 if up[0] else (1 if up[1] else 2)
    a, b, height = _orientation_size(size, axis)
    return a, b, height


def check_overhangs(
    geometry: MeshGeometry, printer: Mapping[str, Any]
) -> Dict[str, Any]:
    """Per-facet overhang angles in every axis-aligned orientation."""
    limit = float(printer["max_unsupported_overhang_deg"])
    bed_x, bed_y, bed_z = bed_size(printer)

    orientations: Dict[str, Dict[str, Any]] = {}
    for label, up in ORIENTATIONS:
        orientations[label] = _overhang_for_direction(
            geometry, up, limit, bed_x, bed_y, bed_z
        )

    def rank(item: Tuple[str, Dict[str, Any]]):
        label, entry = item
        return (
            0 if entry["fits_bed"] else 1,
            entry["unsupported_area_mm2"],
            entry["support_volume_estimate_mm3"],
            0 if label == CURRENT_ORIENTATION else 1,
        )

    best_label, best = min(orientations.items(), key=rank)
    current = orientations[CURRENT_ORIENTATION]

    data = {
        "max_unsupported_overhang_deg": limit,
        "convention": (
            "angle from vertical: 0 is a wall parallel to the build direction, "
            "90 is a flat ceiling; facets resting on the plate are excluded"
        ),
        "current_orientation": CURRENT_ORIENTATION,
        "best_orientation": best_label,
        "total_facet_area_mm2": round(geometry.total_area, 3),
        "orientations": orientations,
        "support_volume_note": (
            "column under each unsupported facet down to the lowest point of the "
            "part; ignores material already underneath, so it over-estimates and "
            "is only meaningful for ranking orientations"
        ),
    }

    if current["unsupported_area_mm2"] <= 0.0:
        status = PASS
        details = (
            f"no facet exceeds {limit:g} deg from vertical as modelled "
            f"({CURRENT_ORIENTATION} up)"
        )
    else:
        status = WARN
        if best_label != CURRENT_ORIENTATION and best["unsupported_area_mm2"] < current[
            "unsupported_area_mm2"
        ]:
            details = (
                f"{current['unsupported_area_mm2']:.1f} mm2 unsupported as modelled "
                f"(steepest {current['steepest_overhang_deg']:.0f} deg); printing with "
                f"{best_label} up would leave {best['unsupported_area_mm2']:.1f} mm2"
            )
        else:
            details = (
                f"{current['unsupported_area_mm2']:.1f} mm2 unsupported as modelled "
                f"(steepest {current['steepest_overhang_deg']:.0f} deg); no "
                "axis-aligned orientation does better, so it needs supports"
            )

    return {"name": "overhangs", "status": status, "details": details, "data": data}


# --------------------------------------------------------------------------
# watertight
# --------------------------------------------------------------------------


def check_watertight(stats: Mapping[str, Any]) -> Dict[str, Any]:
    """Restate the manifold analysis :func:`runner.compute_stats` already did."""
    watertight = bool(stats.get("watertight"))
    data = {
        "watertight": watertight,
        "solid_is_valid": stats.get("solid_is_valid"),
        "mesh_is_closed": stats.get("mesh_is_closed"),
        "mesh_is_oriented": stats.get("mesh_is_oriented"),
        "boundary_edges": stats.get("boundary_edges"),
        "nonmanifold_edges": stats.get("nonmanifold_edges"),
        "degenerate_faces_dropped": stats.get("degenerate_faces_dropped"),
    }
    if watertight:
        return {
            "name": "watertight",
            "status": PASS,
            "details": "the B-Rep is valid and the exported mesh is closed and oriented",
            "data": data,
        }

    problems = []
    if stats.get("solid_is_valid") is False:
        problems.append("the B-Rep solid fails OpenCascade's validity check")
    if not stats.get("mesh_is_closed"):
        problems.append(
            f"{stats.get('boundary_edges', '?')} boundary and "
            f"{stats.get('nonmanifold_edges', '?')} non-manifold edges"
        )
    if not stats.get("mesh_is_oriented"):
        problems.append("inconsistent triangle winding")
    return {
        "name": "watertight",
        "status": FAIL,
        "details": "; ".join(problems) or "the mesh is not watertight",
        "data": data,
    }


# --------------------------------------------------------------------------
# The whole report
# --------------------------------------------------------------------------


def run_checks(
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    stats: Mapping[str, Any],
    printer: Mapping[str, Any],
    margin_mm: float = DEFAULT_PLATE_MARGIN_MM,
    min_wall_probe_mm: Optional[float] = None,
    max_wall_samples: int = DEFAULT_MAX_WALL_SAMPLES,
) -> Dict[str, Any]:
    """Run all four checks and fold them into the ``/check`` response body."""
    geometry = MeshGeometry(vertices, faces)

    entries = [
        check_bed_fit(geometry, stats, printer, margin_mm=margin_mm),
        check_min_wall(
            geometry,
            printer,
            probe_mm=min_wall_probe_mm,
            max_samples=max_wall_samples,
        ),
        check_overhangs(geometry, printer),
        check_watertight(stats),
    ]

    overall = max((e["status"] for e in entries), key=lambda s: _SEVERITY[s])
    return {"overall": overall, "checks": entries}


__all__ = [
    "CURRENT_ORIENTATION",
    "FAIL",
    "MeshGeometry",
    "ORIENTATIONS",
    "PASS",
    "TriangleGrid",
    "WARN",
    "check_bed_fit",
    "check_min_wall",
    "check_overhangs",
    "check_watertight",
    "ring_metrics",
    "run_checks",
    "suggest_segmentation",
]
