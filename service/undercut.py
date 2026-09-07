"""Undercut analysis: which faces fight the direction the mold has to open.

A two-piece mold opens along one axis.  The half above the parting plane is
pulled **+Z**, the half below it **-Z**.  Any face of the part that points back
*against* the direction its own half travels is an undercut: material sits over
it, and the casting is locked in until something bends.

What this module measures, per half
-----------------------------------

Every triangle is assigned to a half by the height of its centroid, and given
three numbers:

``angle_deg``
    How far past vertical the face opposes the draw.  ``0`` is a wall parallel
    to the pull (no draft, but it releases); ``90`` is a face pointing straight
    back at the mold half.  It is ``asin(-n . draw)`` in degrees, so a face
    sloping *with* the draw scores zero and is not counted at all.

``depth_mm``
    How far sideways the silicone has to stretch to clear the pocket -- the
    single number that separates "a rubber mold peels off this" from "nothing
    short of cutting the mold gets this out".  It is the **radial bulge**:
    the widest the part gets anywhere ahead of the face (in the draw direction,
    within the same half) minus the face's own smallest radius, both measured
    from the part's vertical centre axis.

``area_mm2``
    The triangle's area.  Opposing triangles that share an edge are grouped into
    **patches** (union-find), because one deep pocket and a thousand specks of
    tessellation noise are not the same problem.

The verdict, and how honest it is
---------------------------------

A half is **severe** when some single patch is all three of: not a speck
(``area >= max(1 mm^2, 0.5% of the half's surface)``), steeply opposed
(``angle >= 25 deg``) and deep (``depth >= max(1.5 mm, 5% of the part's
footprint width)``).  Anything opposing that is not severe is **mild**;
nothing opposing at all is **none**.

Those five numbers are a judgement call, not physics.  Real silicone release
depends on the rubber's Shore hardness and elongation, on how thick the wall of
the mold is at the pocket, and on how patient the caster is -- none of which
this service knows.  Two further approximations are worth saying out loud:

* **The depth is radial.**  It is computed about the part's vertical centre
  axis, so a part whose lobes sit off-centre in plan -- an arm out to one side
  -- can report a bulge that is really on the far side of the part.  It
  over-reports rather than under-reports, which is the safer direction for a
  warning, but it is not a swept-volume undercut test.
* **A triangle belongs to one half.**  Triangles that straddle the parting
  plane are filed by their centroid rather than split, so the band right at the
  plane is approximate by exactly one triangle's width.

Nothing here imports build123d: it is arithmetic on the same welded mesh
:mod:`service.checks` reads, so it runs in the HTTP process and is tested
without the kernel.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

#: A face has to oppose the draw by more than this to be counted at all.  Below
#: a degree it is a vertical wall with tessellation noise on it, not an undercut.
DEFAULT_THRESHOLD_DEG = 1.0

#: Located examples returned per half.
DEFAULT_EXAMPLES = 6

#: Past this much opposition a face is no longer "a wall that drags a little".
MILD_ANGLE_DEG = 25.0

#: A patch smaller than both of these is a speck: tessellation noise at the
#: parting band, a chamfer, one stray triangle.
SEVERE_MIN_PATCH_AREA_MM2 = 1.0
SEVERE_PATCH_AREA_FRACTION = 0.005

#: A pocket shallower than both of these is something silicone stretches over.
SEVERE_MIN_DEPTH_MM = 1.5
SEVERE_DEPTH_FRACTION = 0.05

#: Height bins used for the radius profile.  More bins resolve a thinner neck;
#: fewer are steadier on a coarse mesh.
RADIUS_BINS = 96

#: Patches described individually in the response.  The rest are counted only.
MAX_PATCHES_REPORTED = 8

#: The exact words the response uses.  ASCII on purpose, like the rest of the
#: service: these strings travel through JSON, logs and a Blender panel.
SEVERITY_VERDICT = {
    "none": "none",
    "mild": "mild -- flexible silicone releases this",
    "severe": "severe -- a rigid mold cannot release this",
}

#: Order of badness, for taking the worse of two halves.
SEVERITY_ORDER = {"none": 0, "mild": 1, "severe": 2}

#: What to tell the caller when a half is severe.  Plain words, no jargon.
MASTER_BOX_RECOMMENDATION = (
    "Parts of this shape hang out over the mold, so a printed two-piece mold "
    "would grip the casting and neither half would come off. Ask for "
    'mode: "master_box" instead: you print the figure itself plus an open '
    "box, pour liquid silicone around the figure, and the rubber mold that "
    "results bends out of the way of the overhangs when you pull each copy out."
)


# --------------------------------------------------------------------------
# Mesh arithmetic
# --------------------------------------------------------------------------


def face_area_normal(
    a: Sequence[float], b: Sequence[float], c: Sequence[float]
) -> Tuple[float, Vec3]:
    """``(area_mm2, unit_normal)`` of one triangle, wound outward.

    A degenerate triangle gets zero area and a zero normal; callers skip those
    rather than dividing by their length.
    """
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 0.0:
        return 0.0, (0.0, 0.0, 0.0)
    return 0.5 * length, (nx / length, ny / length, nz / length)


def radius_profile(
    vertices: Sequence[Sequence[float]],
    centre: Vec2,
    z_min: float,
    z_max: float,
    bins: int = RADIUS_BINS,
) -> List[float]:
    """How wide the part is at each height: max radius from the centre axis.

    Vertices are bucketed by height and each bucket keeps its largest radius.
    A bucket no vertex landed in -- a long thin triangle spanning several
    buckets on a coarse mesh -- is filled by interpolating its nearest occupied
    neighbours, which is what the silhouette does between two tessellation
    bands anyway.
    """
    count = max(2, int(bins))
    span = z_max - z_min
    profile: List[Optional[float]] = [None] * count
    if span <= 0.0:
        radius = 0.0
        for vertex in vertices:
            radius = max(radius, math.hypot(vertex[0] - centre[0], vertex[1] - centre[1]))
        return [radius] * count

    for vertex in vertices:
        index = int((float(vertex[2]) - z_min) / span * count)
        index = min(count - 1, max(0, index))
        radius = math.hypot(float(vertex[0]) - centre[0], float(vertex[1]) - centre[1])
        current = profile[index]
        if current is None or radius > current:
            profile[index] = radius

    occupied = [i for i, value in enumerate(profile) if value is not None]
    if not occupied:  # pragma: no cover - a mesh with vertices always fills one
        return [0.0] * count

    filled: List[float] = []
    for index in range(count):
        value = profile[index]
        if value is not None:
            filled.append(value)
            continue
        before = [i for i in occupied if i < index]
        after = [i for i in occupied if i > index]
        if not before:
            filled.append(float(profile[after[0]]))  # type: ignore[arg-type]
        elif not after:
            filled.append(float(profile[before[-1]]))  # type: ignore[arg-type]
        else:
            lo, hi = before[-1], after[0]
            low_value = float(profile[lo])  # type: ignore[arg-type]
            high_value = float(profile[hi])  # type: ignore[arg-type]
            t = (index - lo) / (hi - lo)
            filled.append(low_value + (high_value - low_value) * t)
    return filled


def _bin_of(z: float, z_min: float, z_max: float, bins: int) -> int:
    span = z_max - z_min
    if span <= 0.0:
        return 0
    index = int((z - z_min) / span * bins)
    return min(bins - 1, max(0, index))


def _patches(
    faces: Sequence[Sequence[int]], indices: Sequence[int]
) -> Dict[int, int]:
    """Union-find over the opposing faces: which of them touch each other.

    Returns ``{face index: patch root}``.  Two opposing triangles are in the
    same patch when they share an edge -- one pocket, however many triangles
    the tessellator spent on it.
    """
    parent: Dict[int, int] = {index: index for index in indices}

    def find(item: int) -> int:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    by_edge: Dict[Tuple[int, int], int] = {}
    for index in indices:
        tri = faces[index]
        for corner in range(3):
            i, j = tri[corner], tri[(corner + 1) % 3]
            key = (i, j) if i < j else (j, i)
            other = by_edge.get(key)
            if other is None:
                by_edge[key] = index
            else:
                union(other, index)
    return {index: find(index) for index in indices}


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------


def _half_report(
    name: str,
    draw: Vec3,
    records: List[Dict[str, Any]],
    half_area: float,
    face_count: int,
    faces: Sequence[Sequence[int]],
    footprint_width: float,
    examples_wanted: int,
) -> Dict[str, Any]:
    """Turn one half's opposing faces into patches, a verdict and examples."""
    area_floor = max(SEVERE_MIN_PATCH_AREA_MM2, SEVERE_PATCH_AREA_FRACTION * half_area)
    depth_floor = max(SEVERE_MIN_DEPTH_MM, SEVERE_DEPTH_FRACTION * footprint_width)

    roots = _patches(faces, [record["face"] for record in records])
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(roots[record["face"]], []).append(record)

    patches: List[Dict[str, Any]] = []
    for members in grouped.values():
        area = sum(member["area_mm2"] for member in members)
        max_angle = max(member["angle_deg"] for member in members)
        max_depth = max(member["depth_mm"] for member in members)
        deepest = max(members, key=lambda m: (m["depth_mm"], m["angle_deg"], m["area_mm2"]))
        severe = area >= area_floor and max_angle >= MILD_ANGLE_DEG and max_depth >= depth_floor
        patches.append(
            {
                "face_count": len(members),
                "area_mm2": round(area, 4),
                "max_angle_deg": round(max_angle, 3),
                "max_depth_mm": round(max_depth, 4),
                "deepest_point_mm": [round(v, 4) for v in deepest["position_mm"]],
                "severe": bool(severe),
                "members": members,
            }
        )

    patches.sort(
        key=lambda p: (p["severe"], p["max_depth_mm"], p["area_mm2"]), reverse=True
    )

    if not records:
        severity = "none"
    elif any(patch["severe"] for patch in patches):
        severity = "severe"
    else:
        severity = "mild"

    # One example from each patch first -- three separate pockets are three
    # things to look at -- then the next worst faces wherever they are.
    examples: List[Dict[str, Any]] = []
    used = set()
    for patch in patches:
        if len(examples) >= examples_wanted:
            break
        best = max(
            patch["members"],
            key=lambda m: (m["depth_mm"], m["angle_deg"], m["area_mm2"]),
        )
        used.add(best["face"])
        examples.append(best)
    if len(examples) < examples_wanted:
        rest = sorted(
            (record for record in records if record["face"] not in used),
            key=lambda m: (m["depth_mm"], m["angle_deg"], m["area_mm2"]),
            reverse=True,
        )
        examples.extend(rest[: examples_wanted - len(examples)])

    total_area = sum(record["area_mm2"] for record in records)
    report: Dict[str, Any] = {
        "draw_direction": [round(v, 4) for v in draw],
        "face_count": face_count,
        "surface_area_mm2": round(half_area, 4),
        "opposing_face_count": len(records),
        "opposing_area_mm2": round(total_area, 4),
        "opposing_area_fraction": round(total_area / half_area, 6) if half_area > 0 else 0.0,
        "max_angle_deg": round(max((r["angle_deg"] for r in records), default=0.0), 3),
        "max_depth_mm": round(max((r["depth_mm"] for r in records), default=0.0), 4),
        "patch_count": len(patches),
        "severe_patch_count": sum(1 for patch in patches if patch["severe"]),
        "patches": [
            {key: value for key, value in patch.items() if key != "members"}
            for patch in patches[:MAX_PATCHES_REPORTED]
        ],
        "examples": [
            {
                "position_mm": [round(v, 4) for v in record["position_mm"]],
                "angle_deg": round(record["angle_deg"], 3),
                "depth_mm": round(record["depth_mm"], 4),
                "area_mm2": round(record["area_mm2"], 5),
            }
            for record in examples
        ],
        "severity": severity,
        "verdict": SEVERITY_VERDICT[severity],
        "thresholds": {
            "patch_area_mm2": round(area_floor, 4),
            "depth_mm": round(depth_floor, 4),
            "angle_deg": MILD_ANGLE_DEG,
        },
    }
    report["detail"] = _detail_sentence(name, report)
    return report


def _detail_sentence(name: str, report: Mapping[str, Any]) -> str:
    """One plain sentence about this half, with the numbers in it."""
    side = "above" if name.endswith("top") else "below"
    if report["severity"] == "none":
        return (
            f"Nothing {side} the parting plane hangs back over the mold; "
            f"{name} lifts straight off."
        )
    count = report["opposing_face_count"]
    area = report["opposing_area_mm2"]
    depth = report["max_depth_mm"]
    angle = report["max_angle_deg"]
    if report["severity"] == "mild":
        return (
            f"{count} faces {side} the parting plane lean back over {name} "
            f"({area:g} mm2 in {report['patch_count']} patch(es), worst {angle:g} deg "
            f"past vertical, {depth:g} mm of sideways grip). A rubber mold flexes "
            "over that; a rigid printed mold will drag but usually still open."
        )
    return (
        f"{report['severe_patch_count']} deep pocket(s) {side} the parting plane "
        f"grip {name}: {area:g} mm2 of opposing faces, worst {angle:g} deg past "
        f"vertical with {depth:g} mm of sideways overhang. A rigid mold half "
        "cannot come off that."
    )


def analyze(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    parting_z: float,
    bounds_low: Sequence[float],
    bounds_high: Sequence[float],
    threshold_deg: float = DEFAULT_THRESHOLD_DEG,
    examples: int = DEFAULT_EXAMPLES,
) -> Dict[str, Any]:
    """Per-half undercut report for a two-piece mold parted at *parting_z*.

    ``bounds_low`` / ``bounds_high`` are the part's bounding box in mm -- the
    same ``stats["bounding_box_min_mm"]`` every other endpoint returns.
    """
    low = [float(v) for v in bounds_low]
    high = [float(v) for v in bounds_high]
    centre: Vec2 = ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)
    footprint_width = max(high[0] - low[0], high[1] - low[1], 1e-6)
    z_min, z_max = low[2], high[2]

    profile = radius_profile(vertices, centre, z_min, z_max, RADIUS_BINS)
    bins = len(profile)
    parting_bin = _bin_of(float(parting_z), z_min, z_max, bins)

    # The widest the part gets between each height and the far end of its own
    # half.  A face is only blocked by material its own half still has to clear.
    ahead_up: List[float] = [0.0] * bins
    running = 0.0
    for index in range(bins - 1, -1, -1):
        running = max(running, profile[index])
        ahead_up[index] = running if index >= parting_bin else 0.0
    ahead_down: List[float] = [0.0] * bins
    running = 0.0
    for index in range(bins):
        running = max(running, profile[index])
        ahead_down[index] = running if index <= parting_bin else 0.0

    threshold = max(0.0, float(threshold_deg))
    sin_threshold = math.sin(math.radians(min(threshold, 89.999)))

    halves: Dict[str, Dict[str, Any]] = {}
    records: Dict[str, List[Dict[str, Any]]] = {"mold_top": [], "mold_bottom": []}
    areas = {"mold_top": 0.0, "mold_bottom": 0.0}
    counts = {"mold_top": 0, "mold_bottom": 0}

    for index, tri in enumerate(triangles):
        if len(tri) != 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        area, normal = face_area_normal(a, b, c)
        if area <= 0.0:
            continue
        cz = (float(a[2]) + float(b[2]) + float(c[2])) / 3.0
        name = "mold_top" if cz >= parting_z else "mold_bottom"
        areas[name] += area
        counts[name] += 1

        # Opposition is the component of the outward normal pointing back at
        # the half's own travel: -n.draw, which is -n_z going up and +n_z down.
        opposing = -normal[2] if name == "mold_top" else normal[2]
        if opposing <= sin_threshold:
            continue

        px = (float(a[0]) + float(b[0]) + float(c[0])) / 3.0
        py = (float(a[1]) + float(b[1]) + float(c[1])) / 3.0
        face_radius = min(
            math.hypot(float(corner[0]) - centre[0], float(corner[1]) - centre[1])
            for corner in (a, b, c)
        )
        bin_index = _bin_of(cz, z_min, z_max, bins)
        widest_ahead = (ahead_up if name == "mold_top" else ahead_down)[bin_index]
        records[name].append(
            {
                "face": index,
                "area_mm2": area,
                "angle_deg": math.degrees(math.asin(min(1.0, opposing))),
                "depth_mm": max(0.0, widest_ahead - face_radius),
                "position_mm": (px, py, cz),
            }
        )

    wanted = max(0, int(examples))
    for name, draw in (("mold_top", (0.0, 0.0, 1.0)), ("mold_bottom", (0.0, 0.0, -1.0))):
        halves[name] = _half_report(
            name,
            draw,
            records[name],
            areas[name],
            counts[name],
            triangles,
            footprint_width,
            wanted,
        )

    severity = max(
        (half["severity"] for half in halves.values()),
        key=lambda value: SEVERITY_ORDER[value],
    )
    report: Dict[str, Any] = {
        "parting_z_mm": round(float(parting_z), 6),
        "halves": halves,
        "severity": severity,
        "verdict": SEVERITY_VERDICT[severity],
        "detail": " ".join(half["detail"] for half in halves.values()),
        "recommend_master_box": severity == "severe",
        "criterion": {
            "threshold_deg": round(threshold, 4),
            "mild_angle_deg": MILD_ANGLE_DEG,
            "severe_min_patch_area_mm2": SEVERE_MIN_PATCH_AREA_MM2,
            "severe_patch_area_fraction": SEVERE_PATCH_AREA_FRACTION,
            "severe_min_depth_mm": SEVERE_MIN_DEPTH_MM,
            "severe_depth_fraction": SEVERE_DEPTH_FRACTION,
            "footprint_width_mm": round(footprint_width, 4),
            "radius_bins": bins,
            "approximation": (
                "Depth is a radial bulge about the part's vertical centre axis, "
                "so lobes that sit off-centre in plan can over-report; triangles "
                "straddling the parting plane are filed by their centroid rather "
                "than split. The severity thresholds are a judgement call about "
                "typical tin/platinum silicone, not a simulation of it."
            ),
        },
    }
    report["recommendation"] = MASTER_BOX_RECOMMENDATION if severity == "severe" else None
    return report


__all__ = [
    "DEFAULT_EXAMPLES",
    "DEFAULT_THRESHOLD_DEG",
    "MASTER_BOX_RECOMMENDATION",
    "MILD_ANGLE_DEG",
    "SEVERITY_VERDICT",
    "analyze",
    "face_area_normal",
    "radius_profile",
]
