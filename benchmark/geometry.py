"""Pure-Python shape measurements for the benchmark's fidelity tier.

No ``bpy`` and no numpy: everything here takes millimetre vertex lists and
triangle index lists, so it runs the same under Blender's Python (where the
artifact is loaded) and under plain pytest (where it is unit-tested).

Ray queries do not reimplement anything: they go through the geometry
service's own :class:`service.checks.TriangleGrid`, the same grid the
``min_wall`` gate casts its probes against.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def unit(a: Sequence[float]) -> Vec3:
    length = _norm(a)
    if length <= 0.0:
        raise ValueError("zero-length axis %r" % (tuple(a),))
    return (a[0] / length, a[1] / length, a[2] / length)


# ---------------------------------------------------------------------------
# surface moments and the principal frame
# ---------------------------------------------------------------------------

def surface_moments(vertices: Sequence[Vec3],
                    triangles: Sequence[Sequence[int]]):
    """``(area, centroid, covariance)`` of the SURFACE, area-weighted and exact.

    Per triangle the second moment about the origin is
    ``A/12 * (a a^T + b b^T + c c^T + s s^T)`` with ``s = a + b + c`` — exact
    for a uniform density over the triangle, so a mesh's tessellation density
    does not bias the frame the way a vertex cloud would.
    """
    area = 0.0
    first = [0.0, 0.0, 0.0]
    second = [[0.0] * 3 for _ in range(3)]
    for tri in triangles:
        a, b, c = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
        cross = _cross(_sub(b, a), _sub(c, a))
        tri_area = 0.5 * _norm(cross)
        if tri_area <= 0.0:
            continue
        area += tri_area
        s = _add(_add(a, b), c)
        for i in range(3):
            first[i] += tri_area * s[i] / 3.0
        for i in range(3):
            for j in range(3):
                second[i][j] += tri_area / 12.0 * (
                    a[i] * a[j] + b[i] * b[j] + c[i] * c[j] + s[i] * s[j])
    if area <= 0.0:
        return 0.0, (0.0, 0.0, 0.0), [[0.0] * 3 for _ in range(3)]
    centroid = (first[0] / area, first[1] / area, first[2] / area)
    cov = [[second[i][j] / area - centroid[i] * centroid[j] for j in range(3)]
           for i in range(3)]
    return area, centroid, cov


def jacobi_eigen(matrix: Sequence[Sequence[float]], sweeps: int = 64):
    """Eigen-decomposition of a symmetric 3x3: ``(values, vectors)`` ascending.

    Cyclic Jacobi rotations; converges to machine precision in a handful of
    sweeps for 3x3.  Each vector's sign is fixed (largest-magnitude component
    positive) so the frame is deterministic run to run.
    """
    a = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(sweeps):
        off = a[0][1] ** 2 + a[0][2] ** 2 + a[1][2] ** 2
        if off < 1e-30:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) < 1e-300:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
            t = (1.0 if theta >= 0.0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = c * akp - s * akq
                a[k][q] = s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k] = c * apk - s * aqk
                a[q][k] = s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
    pairs = []
    for i in range(3):
        vec = (v[0][i], v[1][i], v[2][i])
        big = max(range(3), key=lambda k: abs(vec[k]))
        if vec[big] < 0.0:
            vec = (-vec[0], -vec[1], -vec[2])
        pairs.append((a[i][i], unit(vec)))
    pairs.sort(key=lambda item: item[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def principal_frame(vertices, triangles):
    """``{"centroid", "axes": [thin, mid, long], "variances"}`` of the surface."""
    area, centroid, cov = surface_moments(vertices, triangles)
    values, vectors = jacobi_eigen(cov)
    return {"area": area, "centroid": centroid, "axes": vectors, "variances": values}


def obb_extents(vertices, frame) -> Vec3:
    """Extents of the vertices along the frame's three axes (thin, mid, long)."""
    out = []
    centroid = frame["centroid"]
    for axis in frame["axes"]:
        values = [_dot(_sub(v, centroid), axis) for v in vertices]
        out.append(max(values) - min(values) if values else 0.0)
    return tuple(out)


def enclosed_volume(vertices, triangles, about: Vec3 = (0.0, 0.0, 0.0)) -> float:
    """``|signed volume|`` by the divergence theorem, taken about ``about``.

    Exact and origin-independent for a closed mesh.  Taken about the surface
    centroid, a zero-thickness sheet (open, planar through its own centroid)
    reads 0 rather than whatever cone it happens to subtend from the world
    origin.
    """
    total = 0.0
    for tri in triangles:
        a = _sub(vertices[tri[0]], about)
        b = _sub(vertices[tri[1]], about)
        c = _sub(vertices[tri[2]], about)
        total += _dot(a, _cross(b, c))
    return abs(total) / 6.0


def volume_ratio(vertices, triangles) -> Dict[str, float]:
    """Enclosed volume over oriented-bounding-box volume, plus its parts.

    0 for a degenerate box (a sheet has no box volume), so a flat plane can
    never divide its way to a pass.
    """
    frame = principal_frame(vertices, triangles)
    extents = obb_extents(vertices, frame)
    box = extents[0] * extents[1] * extents[2]
    volume = enclosed_volume(vertices, triangles, frame["centroid"])
    ratio = volume / box if box > 1e-9 else 0.0
    return {"volume_mm3": volume, "obb_volume_mm3": box,
            "obb_extents_mm": list(extents), "ratio": ratio}


# ---------------------------------------------------------------------------
# facing direction
# ---------------------------------------------------------------------------

def facing_yaw_deg(vertices, triangles, front_axis, up_axis) -> Optional[Dict[str, float]]:
    """Yaw of a thin part's face normal about ``up``, measured from ``front``.

    The face normal is the surface's thinnest principal axis (for a blade or an
    ear, the direction through its thickness), signed so it looks toward the
    front — the side the artist views.  Yaw is the signed angle from the front
    axis to that normal's horizontal projection, positive counter-clockwise
    about ``up``.  ``None`` when the normal is (near) vertical: a part lying
    flat has no yaw to speak of.
    """
    front = unit(front_axis)
    up = unit(up_axis)
    frame = principal_frame(vertices, triangles)
    normal = frame["axes"][0]
    if _dot(normal, front) < 0.0:
        normal = _scale(normal, -1.0)
    horizontal = _sub(normal, _scale(up, _dot(normal, up)))
    if _norm(horizontal) < 1e-6:
        return None
    yaw = math.degrees(math.atan2(_dot(_cross(front, horizontal), up),
                                  _dot(front, horizontal)))
    return {"yaw_deg": yaw, "normal": list(normal),
            "centroid_mm": list(frame["centroid"])}


def mirror_angle_deg(axis_a, axis_b, front_axis, up_axis) -> float:
    """Angle between line ``a`` and the mirror image of line ``b``, in degrees.

    The mirror is the bowl's symmetry plane — the plane containing the front
    and up axes.  Two parts placed as true mirror images have face axes whose
    lines coincide after reflecting one of them: 0 deg.  Two parts that are one
    transform rotated to two positions about ``up`` do not, unless their face
    axis happens to be purely horizontal and tangential: a rotated copy with
    local yaw phi and tilt tau reads ``acos|cos^2(tau) cos(2 phi) + sin^2(tau)|``.
    Unsigned (lines, not vectors), so it does not depend on which way the
    principal axis happened to point.
    """
    plane = unit(_cross(unit(front_axis), unit(up_axis)))
    b = unit(axis_b)
    mirrored = _sub(b, _scale(plane, 2.0 * _dot(b, plane)))
    cosine = min(1.0, abs(_dot(unit(axis_a), mirrored)))
    return math.degrees(math.acos(cosine))


# ---------------------------------------------------------------------------
# rays, through the service's own grid
# ---------------------------------------------------------------------------

def ray_hits(vertices, triangles, origin, direction, max_distance=1000.0,
             cell_mm=5.0, max_hits=64) -> List[float]:
    """Every surface crossing along a ray, as distances from ``origin`` (mm)."""
    from service.checks import MeshGeometry, TriangleGrid

    geometry = MeshGeometry(vertices, triangles)
    grid = TriangleGrid(geometry, cell_mm)
    direction = unit(direction)
    hits: List[float] = []
    travelled = 0.0
    point = tuple(float(v) for v in origin)
    while len(hits) < max_hits and travelled < max_distance:
        distance = grid.first_hit(point, direction, max_distance - travelled)
        if distance is None:
            break
        travelled += distance
        hits.append(travelled)
        step = 1e-4
        travelled += step
        point = (float(origin[0]) + direction[0] * travelled,
                 float(origin[1]) + direction[1] * travelled,
                 float(origin[2]) + direction[2] * travelled)
    return hits


def bounds(vertices) -> Tuple[Vec3, Vec3]:
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
