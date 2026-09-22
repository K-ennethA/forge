"""Auto-seat a part into a measured socket — the operation that ends trial renders.

What this exists for
--------------------
eevee-bowl-v2 seated its ears by hand, and the log of it
(``projects/eevee-bowl-v2/design/organic-rework-log.md``) is the specification
for this module read backwards:

* the *formula* said the ear-R peg tip would land 0.33 mm off the socket axis;
  the *measured* geometry said 0.886 mm — "nearly 3x the formula's optimistic"
  number, and the difference was a hand-estimated swell term nobody had read off
  the real mesh;
* the measurement that finally happened captured "every vertex within 9 mm of
  the formula's predicted socket position" and got 153 vertices of which some
  were "the OUTER lug rib (not the bore wall) mixed into the 9 mm capture
  radius, so it is not a clean axis reading, only a bound";
* and the way each round ended was a render, looked at, and disagreed with.

Three lessons, three mechanisms:

1. **A capture radius is not a bore.**  The bore is found here by flooding
   across the mesh from the face nearest the hint, stopping at every edge
   sharper than :data:`MAX_DIHEDRAL_DEG`, and then keeping only the faces whose
   normals point *at* the axis.  A lug's outer wall faces the other way and is
   gone by construction rather than by a radius that happened to be tight
   enough.  That single sign test is the difference between "a bound" and an
   axis.
2. **Fit, do not assume.**  The axis is a line fitted through the circle-fitted
   centres of the bore's vertex rings at every depth the mesh has one (the
   ``ring_`` block of every report says how many and how far off each sat).  No
   step of the seat uses a nominal number where a measured one exists.
3. **Verify before you render, or refuse.**  Every seat is checked numerically
   *before* it is applied: the tip's offset from the measured axis, its depth
   against the measured floor, the axis-to-axis angle against a tolerance
   derived from the fit's own clearance, and a sampled intersection test of the
   part's body against the base everywhere except the bore.  A seat that fails
   is raised as an error **with all of those numbers in the message** and the
   scene is not touched.  Nothing here is "probably right, have a look".

Circumscribed, inscribed, and why both are reported
---------------------------------------------------
A bore is a polygon, not a circle.  A 32-segment bore of nominal radius 3.2 mm
has its *vertices* on the 3.2 mm circle and its *walls* at 3.2·cos(π/32) =
3.1846 mm.  The ring fit measures the first (that is where the vertices are);
what a peg has to fit through is the second.  Both are in every report
(``radius_mm`` and ``inscribed_radius_mm``), and every clearance check uses the
inscribed one.  The 0.0154 mm between them is nothing at this size and is the
whole tolerance budget on a coarse 8-segment bore — which is exactly the class
of "formula said, geometry did" gap that cost eevee-bowl-v2 its rounds.

The peg side
------------
``bosses.attach_boss`` already records every attachment on the part: the boss's
world matrix at attach time and its resolved spec.  That ledger is the peg — a
spec'd peg's axis is its matrix's +Z, its tip is one length along it, and its
anti-rotation rib is at local +X, all exactly.  The ledger is nonetheless
*checked against the mesh* before it is trusted (the union put a cap face at the
tip; if the recorded tip is not near the part's surface, the part moved after
the attach and the record's world matrix is stale), and a part with no ledger —
or one whose ledger is stale — falls back to measuring its peg with the same
cylinder fit, run outward instead of inward.

Seating the ledger keeps it true
--------------------------------
The ledger stores **world** matrices and the retained cutters are separate
objects that do not follow the part.  Moving a part without moving them would
silently cost that part its reversibility — the exact failure ``bosses``
exists to prevent.  So ``seat_part`` applies its transform to the retained
objects and rewrites the recorded matrices too, and says so in the report.

Units
-----
Millimetres on the wire and millimetres in every intermediate here; metres only
at the ``bpy`` boundary, converted once — the house rule.  Every number in a
report is a millimetre, a square/cubic millimetre, or a degree.
"""

import math

import bmesh
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

from . import bosses, common
from .registry import ForgeError, command

MM = common.MM_TO_M
M_TO_MM = common.M_TO_MM

# ---------------------------------------------------------------------------
# thresholds, each with its derivation
# ---------------------------------------------------------------------------

#: The flood across the mesh stops at any edge sharper than this.  Between the
#: two things it has to separate: the coarsest bore worth measuring is an
#: 12-segment cylinder, whose wall facets meet at 30°, and the features that
#: bound a bore — a floor cap (90°), a lead chamfer or a keyway side (45° or
#: sharper) — never meet the wall shallower than 45°.  35° is the middle of the
#: only gap there is.
MAX_DIHEDRAL_DEG = 35.0

#: A wall face's normal is perpendicular to the axis.  0.35 admits 20.5° of
#: tilt, which covers a draft angle and a sloppy boolean seam and excludes a
#: 45° chamfer (0.707) and a floor cap (1.0).
WALL_PERPENDICULAR_MAX = 0.35

#: A wall face's normal is also *radial*: it points at the axis, not along the
#: surface.  0.7 admits 45° of splay, which excludes a keyway's side walls
#: (perpendicular to the radius, so ~0.0) without excluding a faceted wall.
WALL_RADIAL_MIN = 0.7

#: A vertex further than this fraction off the ring's median radius is not bore
#: wall — it is a keyway, a chamfer or a neighbouring feature the flood reached.
#: 0.15 is wide enough to keep a 12-segment polygon's own 3.4% chord sag and
#: narrow enough to drop the shallowest keyway worth cutting.
RADIUS_OUTLIER_FRACTION = 0.15

#: Refit passes.  The fit converges in two or three on clean geometry; six is
#: the cap, and the report says how many it actually used and what the last one
#: moved.
FIT_PASSES = 6

#: A ring needs this many points before its circle fit means anything, and they
#: have to span more than half the circle (the largest angular gap between
#: consecutive points must be under 180°) or the fit is an extrapolation.
MIN_RING_VERTICES = 5
MAX_RING_GAP_DEG = 179.0

#: Two rings is the minimum for a line.
MIN_RINGS = 2

#: A hint names a feature by being *in* it.  Twice the fitted radius off the
#: axis allows a hint dropped roughly on the rim and rejects a cylinder that
#: merely fell inside the search radius — which is the eevee-bowl capture
#: failure wearing a confident fit.  Three radii past either end of the wall
#: allows a hint held clear of the mouth.
HINT_RADIAL_FACTOR = 2.0
HINT_DEPTH_FACTOR = 3.0

#: A keyway wider than this on either side of its centre has stopped being an
#: anti-rotation feature: 120° of slot leaves no material to resist against, so
#: something that wide is a counterbore or a collar and is not reported as one.
MAX_KEYWAY_HALF_WIDTH_DEG = 60.0

#: How many planes the wall is sliced by to make those rings.  Slicing rather
#: than grouping the mesh's own vertices by depth, because a bore that meets
#: its surface at an angle — the eevee-bowl case, a peg into a sloping rim —
#: opens in an **ellipse**, and the vertices along that ellipse are spread over
#: 2·R·tan(tilt) of depth in no ring at all.  A plane cut across the wall is a
#: ring wherever the wall is whole; the incomplete slices across the elliptical
#: mouth fail the angular-gap test and drop out on their own.  16 leaves a
#: bore tilted 45° — where the ellipse eats two radii of the depth — with rings
#: to spare.
RING_SLICES = 16

#: A fit whose wall vertices sit further than this fraction of the radius off
#: the fitted cylinder is not a cylinder, and is refused as one.
MAX_RADIAL_RESIDUAL_FRACTION = 0.08

#: ``forge_lib.DEFAULT_DEPTH_EXTRA_MM`` — a socket built by the geometry service
#: is cut 0.5 mm deeper than its peg is long, precisely so the peg seats on the
#: part's shoulder and not on the bore floor.  Repeated rather than imported
#: because the add-on does not import the service.
DEFAULT_INSERTION_CLEARANCE_MM = 0.5

#: The floor under the derived angle tolerance: below this, what is being
#: measured is float noise in the fit, not a tilt.
MIN_ANGLE_TOLERANCE_DEG = 1.0e-3

#: The floor under the derived radial tolerance, one micron.  Two reasons, both
#: measured: Blender's exact solver places the vertices it creates to about a
#: micron, and ``mathutils`` is single precision, so a 50 mm coordinate carries
#: ~6 nm of representation error and two routes to the same point differ by a
#: few of those.  Without the floor, a peg that does not fit at all derives a
#: negative clearance, and the tip check fails a second time on float noise —
#: two failures for one fault, and the second one meaningless.
MIN_RADIAL_TOLERANCE_MM = 1.0e-3

#: How many of the part's vertices the intersection test may sample.  The test
#: only ever considers vertices inside the base's bounding box, so this cap is
#: reached by a dense sculpt seated into a large base, and the report always
#: says how many were tested and whether a stride was used.
DEFAULT_MAX_SAMPLES = 8000

#: Fixed, deliberately irrational ray directions for the inside/outside parity
#: test.  Three of them, majority vote: a single ray that grazes an edge
#: miscounts, and three that all graze the same edge do not exist.
_PARITY_DIRECTIONS = (
    Vector((0.5773502691896258, 0.5773502691896258, 0.5773502691896258)),
    Vector((-0.4247081905, 0.8164965809, 0.3912303982)).normalized(),
    Vector((0.2672612419, -0.5345224838, 0.8017837257)).normalized(),
)

#: Ray restart offset for the parity walk, in millimetres.  One micron: large
#: enough that the solver does not re-report the face just crossed, small
#: enough to be invisible against any printable feature.
_PARITY_EPSILON_MM = 1.0e-3


# ---------------------------------------------------------------------------
# small linear algebra (no numpy: every routine here is 3x3 and deterministic)
# ---------------------------------------------------------------------------

def _symmetric_eigen(matrix):
    """Eigenvalues/vectors of a symmetric 3x3, ascending.  Cyclic Jacobi.

    Hand-rolled rather than numpy's ``eigh`` for one reason that matters to this
    module: the iteration order is fixed here, so the result is bit-identical
    run to run and build to build, and every number in a report downstream of it
    is too.
    """
    a = [[float(matrix[row][col]) for col in range(3)] for row in range(3)]
    vectors = [[1.0 if row == col else 0.0 for col in range(3)] for row in range(3)]
    for _ in range(24):
        off = sum(a[row][col] ** 2 for row, col in ((0, 1), (0, 2), (1, 2)))
        if off <= 1.0e-30:
            break
        for row, col in ((0, 1), (0, 2), (1, 2)):
            if abs(a[row][col]) <= 1.0e-30:
                continue
            theta = (a[col][col] - a[row][row]) / (2.0 * a[row][col])
            sign = 1.0 if theta >= 0.0 else -1.0
            t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
            cos = 1.0 / math.sqrt(t * t + 1.0)
            sin = t * cos
            for k in range(3):
                a_rk, a_ck = a[row][k], a[col][k]
                a[row][k] = cos * a_rk - sin * a_ck
                a[col][k] = sin * a_rk + cos * a_ck
            for k in range(3):
                a_kr, a_kc = a[k][row], a[k][col]
                a[k][row] = cos * a_kr - sin * a_kc
                a[k][col] = sin * a_kr + cos * a_kc
            for k in range(3):
                v_kr, v_kc = vectors[k][row], vectors[k][col]
                vectors[k][row] = cos * v_kr - sin * v_kc
                vectors[k][col] = sin * v_kr + cos * v_kc
    pairs = sorted(
        ((a[index][index], Vector((vectors[0][index], vectors[1][index],
                                   vectors[2][index]))) for index in range(3)),
        key=lambda item: item[0])
    return [value for value, _ in pairs], [vec.normalized() for _, vec in pairs]


def _solve3(matrix, rhs):
    """``matrix @ x = rhs`` for a 3x3, or ``None`` when it is singular."""
    try:
        inverse = Matrix(matrix).inverted()
    except ValueError:
        return None
    return inverse @ Vector(rhs)


def _outer_accumulate(vectors, weights=None):
    """Sum of ``w * v v^T`` as a 3x3 list-of-lists."""
    acc = [[0.0] * 3 for _ in range(3)]
    for index, vec in enumerate(vectors):
        weight = 1.0 if weights is None else weights[index]
        for row in range(3):
            for col in range(3):
                acc[row][col] += weight * vec[row] * vec[col]
    return acc


def _basis_for(axis):
    """A deterministic orthonormal ``(u, v)`` spanning the plane through ``axis``."""
    axis = Vector(axis).normalized()
    # The world axis least aligned with the fitted one: a fixed choice, so two
    # runs of the same fit report the same angles rather than mirrored ones.
    reference = min(
        (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))),
        key=lambda candidate: abs(candidate.dot(axis)))
    u = (reference - axis * reference.dot(axis))
    if u.length < 1.0e-12:  # pragma: no cover - reference is chosen to avoid this
        raise ForgeError("Degenerate axis %r; a bore cannot be fitted to it." % (tuple(axis),))
    u.normalize()
    return u, axis.cross(u).normalized()


def _signed_angle(from_vec, to_vec, axis):
    """Signed angle from ``from_vec`` to ``to_vec`` about ``axis``, in radians."""
    axis = Vector(axis).normalized()
    a = Vector(from_vec) - axis * Vector(from_vec).dot(axis)
    b = Vector(to_vec) - axis * Vector(to_vec).dot(axis)
    if a.length < 1.0e-12 or b.length < 1.0e-12:
        return 0.0
    a.normalize()
    b.normalize()
    return math.atan2(a.cross(b).dot(axis), a.dot(b))


def _circle_fit(points):
    """Algebraic (Kasa) circle fit of 2D points.  Returns ``(cx, cy, r, rms)``.

    Closed form, so it is the same number every run — an iterative geometric fit
    would be a hair more accurate on noisy input and would make every digest
    downstream depend on its stopping rule.
    """
    count = len(points)
    if count < 3:
        return None
    sx = sy = sxx = syy = sxy = sz = szx = szy = 0.0
    for x, y in points:
        z = x * x + y * y
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        sz += z
        szx += z * x
        szy += z * y
    matrix = [[2.0 * sxx, 2.0 * sxy, sx],
              [2.0 * sxy, 2.0 * syy, sy],
              [2.0 * sx, 2.0 * sy, float(count)]]
    solution = _solve3(matrix, (szx, szy, sz))
    if solution is None:
        return None
    cx, cy, c = solution[0], solution[1], solution[2]
    inner = c + cx * cx + cy * cy
    if inner <= 0.0:
        return None
    radius = math.sqrt(inner)
    rms = math.sqrt(sum((math.hypot(x - cx, y - cy) - radius) ** 2
                        for x, y in points) / count)
    return cx, cy, radius, rms


def _line_fit(points):
    """Total-least-squares line through 3D points.  Returns ``(point, direction)``."""
    count = len(points)
    if count < 2:
        return None
    centroid = Vector((0.0, 0.0, 0.0))
    for point in points:
        centroid += Vector(point)
    centroid /= float(count)
    covariance = _outer_accumulate([Vector(point) - centroid for point in points])
    _, vectors = _symmetric_eigen(covariance)
    return centroid, vectors[2]  # largest eigenvalue = the line's direction


def _closest_point_to_lines(origins, directions):
    """Least-squares point nearest a bundle of infinite lines."""
    matrix = [[0.0] * 3 for _ in range(3)]
    rhs = Vector((0.0, 0.0, 0.0))
    for origin, direction in zip(origins, directions):
        direction = Vector(direction).normalized()
        projector = [[(1.0 if row == col else 0.0) - direction[row] * direction[col]
                      for col in range(3)] for row in range(3)]
        for row in range(3):
            for col in range(3):
                matrix[row][col] += projector[row][col]
            rhs[row] += sum(projector[row][col] * origin[col] for col in range(3))
    return _solve3(matrix, rhs)


# ---------------------------------------------------------------------------
# the mesh, in millimetres
# ---------------------------------------------------------------------------

class _Surface(object):
    """One object's world-space mesh in millimetres, with the bits a fit needs.

    Deliberately the object's own mesh datablock rather than the evaluated one,
    for the reason ``bosses`` gives: a socket is cut and applied, so measuring
    the evaluated mesh would fold an artist's unrelated subsurf into the axis
    this module reports as measured geometry.
    """

    def __init__(self, obj):
        if obj.type != "MESH" or obj.data is None:
            raise ForgeError("Object %r is a %s, this command needs a MESH."
                             % (obj.name, obj.type))
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        self.name = obj.name
        self.vertices = [Vector(vert.co) * M_TO_MM for vert in bm.verts]
        self.face_verts = [[vert.index for vert in face.verts] for face in bm.faces]
        self.face_center = [Vector(face.calc_center_median()) * M_TO_MM for face in bm.faces]
        self.face_normal = [Vector(face.normal).normalized() if face.normal.length > 1e-12
                            else Vector((0.0, 0.0, 1.0)) for face in bm.faces]
        self.face_area = [face.calc_area() * 1.0e6 for face in bm.faces]
        # Adjacency and dihedral, read once: the flood asks for both per edge.
        self.neighbours = [[] for _ in bm.faces]
        for edge in bm.edges:
            linked = list(edge.link_faces)
            if len(linked) != 2:
                continue
            try:
                angle = math.degrees(edge.calc_face_angle())
            except (ValueError, RuntimeError):
                continue
            first, second = linked[0].index, linked[1].index
            self.neighbours[first].append((second, angle))
            self.neighbours[second].append((first, angle))
        for row in self.neighbours:
            row.sort()
        low = [min(co[axis] for co in self.vertices) for axis in range(3)]
        high = [max(co[axis] for co in self.vertices) for axis in range(3)]
        self.bounds = (Vector(low), Vector(high))
        self.diagonal_mm = (self.bounds[1] - self.bounds[0]).length
        self.centroid = Vector((0.0, 0.0, 0.0))
        for co in self.vertices:
            self.centroid += co
        if self.vertices:
            self.centroid /= float(len(self.vertices))
        self._tree = None
        self._triangles = None
        bm.free()

    # -- the BVH, built on demand (only the verification path needs it) -----

    def _build_tree(self):
        triangles = []
        for verts in self.face_verts:
            for index in range(1, len(verts) - 1):
                triangles.append((verts[0], verts[index], verts[index + 1]))
        self._triangles = triangles
        self._tree = BVHTree.FromPolygons(
            [tuple(co) for co in self.vertices], triangles, all_triangles=True)

    @property
    def tree(self):
        if self._tree is None:
            self._build_tree()
        return self._tree

    def nearest_distance_mm(self, point):
        hit = self.tree.find_nearest(Vector(point))
        if hit[0] is None:
            return float("inf")
        return (hit[0] - Vector(point)).length

    def contains(self, point):
        """Inside/outside by ray parity, majority of three fixed directions."""
        votes = 0
        for direction in _PARITY_DIRECTIONS:
            origin = Vector(point)
            crossings = 0
            for _ in range(96):
                hit = self.tree.ray_cast(origin, direction, 1.0e7)
                if hit[0] is None:
                    break
                crossings += 1
                origin = Vector(hit[0]) + direction * _PARITY_EPSILON_MM
            votes += crossings % 2
        return votes >= 2


# ---------------------------------------------------------------------------
# finding the bore
# ---------------------------------------------------------------------------

def _seed_candidates(surface, hint, interior, radius_mm, limit=24):
    """Faces near the hint, the likeliest first, for the flood to start from.

    "Likeliest" is a preference, not a filter: a hint on a bore's axis has the
    bore's walls facing back at it, and a hint on a peg's axis has the peg's
    walls facing away, so that sign sorts the right faces to the front — but a
    hint dropped just outside a peg's wall has the sign the other way round and
    is still a perfectly good hint.  What actually decides whether a face
    belongs to the feature is the fit's own inward/outward classification, which
    is why a seed that leads nowhere just costs the next one its turn.
    """
    scored = []
    for index, center in enumerate(surface.face_center):
        to_hint = Vector(hint) - center
        distance = to_hint.length
        if distance > radius_mm or surface.face_area[index] <= 0.0:
            continue
        if distance > 1.0e-9:
            facing = surface.face_normal[index].dot(to_hint / distance)
        else:
            facing = 1.0 if interior else -1.0
        expected = (facing > 0.0) if interior else (facing < 0.0)
        scored.append((0 if expected else 1, distance, index))
    scored.sort()
    return [index for _, _, index in scored[:limit]]


def _flood(surface, seed, hint, radius_mm):
    """Faces reachable from ``seed`` without crossing an edge sharper than the cap."""
    seen = {seed}
    queue = [seed]
    while queue:
        current = queue.pop()
        for neighbour, angle in surface.neighbours[current]:
            if neighbour in seen or angle > MAX_DIHEDRAL_DEG:
                continue
            if (surface.face_center[neighbour] - Vector(hint)).length > radius_mm:
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return sorted(seen)


def _slice_ring(surface, edges, depths, axis, point, plane_depth):
    """Where a plane at ``plane_depth`` crosses the wall: one ring of points.

    Every point comes from one wall edge crossing the plane, so a ring is the
    wall's own cross-section rather than whatever depth the mesh happened to put
    its vertices at.
    """
    points = []
    for first, second in edges:
        low, high = depths.get(first), depths.get(second)
        if low is None or high is None:
            continue
        if (low - plane_depth) * (high - plane_depth) >= 0.0:
            continue
        ratio = (plane_depth - low) / (high - low)
        start = surface.vertices[first]
        points.append(start + (surface.vertices[second] - start) * ratio)
    return points


def _ring_from_points(points, axis, point, u, v):
    """Circle-fit one ring of world points.  ``None`` when it is not a ring."""
    if len(points) < MIN_RING_VERTICES:
        return None
    planar = []
    angles = []
    for co in points:
        offset = co - point
        x, y = offset.dot(u), offset.dot(v)
        planar.append((x, y))
        angles.append(math.atan2(y, x))
    angles.sort()
    gaps = [angles[index + 1] - angles[index] for index in range(len(angles) - 1)]
    gaps.append(angles[0] + 2.0 * math.pi - angles[-1])
    if math.degrees(max(gaps)) > MAX_RING_GAP_DEG:
        return None
    fitted = _circle_fit(planar)
    if fitted is None:
        return None
    cx, cy, radius, rms = fitted
    depth = sum((co - point).dot(axis) for co in points) / len(points)
    return {
        "centre": point + u * cx + v * cy + axis * depth,
        "points": len(points),
        "depth_mm": depth,
        "radius_mm": radius,
        "residual_rms_mm": rms,
    }


def _fit_cylinder(surface, faces, hint, interior):
    """Fit a cylinder to a flooded face set.  Returns a dict, or raises.

    The loop is: classify faces against the current axis (perpendicular normal,
    radial normal, right sign), take those faces' vertices, drop the radial
    outliers, circle-fit every ring of them, and fit a line through the ring
    centres.  Three passes is usually convergence; the report carries how much
    the last one moved so "converged" is a number and not a claim.
    """
    centers = [surface.face_center[index] for index in faces]
    normals = [surface.face_normal[index] for index in faces]
    areas = [surface.face_area[index] for index in faces]
    if len(faces) < 3:
        raise ForgeError(
            "Only %d face(s) of %r flooded from the hint — that is not a bore wall. "
            "Check the hint point, or widen search_radius_mm." % (len(faces), surface.name))

    # Seed: the axis is the direction every wall normal is perpendicular to, and
    # the axis point is the least-squares meeting point of the normals' lines.
    _, vectors = _symmetric_eigen(_outer_accumulate(normals, areas))
    axis = vectors[0]
    point = _closest_point_to_lines(centers, normals)
    if point is None:
        raise ForgeError(
            "The faces near the hint on %r have parallel normals — that is a flat "
            "surface, not a bore." % surface.name)

    history = []
    kept_faces = list(faces)
    ring_groups = []
    radius = 0.0
    residual = 0.0
    for _ in range(FIT_PASSES):
        # 1. which of the flooded faces are wall, measured against this axis
        wall = []
        for index in faces:
            center = surface.face_center[index]
            normal = surface.face_normal[index]
            if abs(normal.dot(axis)) > WALL_PERPENDICULAR_MAX:
                continue
            radial = (center - point) - axis * (center - point).dot(axis)
            if radial.length < 1.0e-9:
                continue
            aligned = normal.dot(radial.normalized())
            if abs(aligned) < WALL_RADIAL_MIN:
                continue
            if interior and aligned > 0.0:
                continue
            if not interior and aligned < 0.0:
                continue
            wall.append(index)
        if len(wall) < 2:
            raise ForgeError(
                "No %s cylindrical wall near the hint on %r: %d face(s) flooded but "
                "none of them face the fitted axis the right way. The hint is probably "
                "not in a bore (or, for a peg, not on one)."
                % ("interior" if interior else "exterior", surface.name, len(faces)))
        kept_faces = wall

        # 2. their vertices, minus the radial outliers (keyway, chamfer, junk)
        vertex_indices = sorted({vi for index in wall for vi in surface.face_verts[index]})
        radii = []
        depths = []
        for vi in vertex_indices:
            offset = surface.vertices[vi] - point
            depth = offset.dot(axis)
            radii.append((offset - axis * depth).length)
            depths.append(depth)
        median = sorted(radii)[len(radii) // 2]
        if median <= 1.0e-9:
            raise ForgeError("The wall near the hint on %r has zero radius." % surface.name)
        keep = [i for i, value in enumerate(radii)
                if abs(value - median) <= RADIUS_OUTLIER_FRACTION * median]
        if len(keep) < MIN_RING_VERTICES * MIN_RINGS:
            raise ForgeError(
                "Only %d wall vertices survived the radial filter on %r (median radius "
                "%.4f mm) — too few to fit a cylinder to."
                % (len(keep), surface.name, median))

        kept_depths = [depths[i] for i in keep]
        kept_set = {vertex_indices[i] for i in keep}
        span = max(kept_depths) - min(kept_depths)
        if span <= 1.0e-9:
            raise ForgeError(
                "The wall near the hint on %r is one ring deep (span %.6f mm): a "
                "cylinder axis cannot be fitted to a single circle."
                % (surface.name, span))

        # 3. slice the wall into rings, circle-fit each, line-fit the centres
        u, v = _basis_for(axis)
        edges = set()
        for index in wall:
            loop = surface.face_verts[index]
            for position in range(len(loop)):
                a_index, b_index = loop[position], loop[(position + 1) % len(loop)]
                if a_index in kept_set and b_index in kept_set:
                    edges.add((min(a_index, b_index), max(a_index, b_index)))
        edges = sorted(edges)
        depth_of = {vi: (surface.vertices[vi] - point).dot(axis) for vi in kept_set}
        low = min(kept_depths)
        ring_groups = []
        centres_3d = []
        for step in range(1, RING_SLICES + 1):
            plane_depth = low + span * (float(step) / (RING_SLICES + 1))
            ring = _ring_from_points(
                _slice_ring(surface, edges, depth_of, axis, point, plane_depth),
                axis, point, u, v)
            if ring is None:
                continue
            centres_3d.append(ring.pop("centre"))
            ring_groups.append(ring)
        if len(ring_groups) < MIN_RINGS:
            raise ForgeError(
                "Only %d usable ring(s) sliced out of the wall near the hint on %r; %d "
                "are needed to fit an axis. A ring needs %d crossings spanning more than "
                "half the circle — a wall this broken is not a bore."
                % (len(ring_groups), surface.name, MIN_RINGS, MIN_RING_VERTICES))

        new_point, new_axis = _line_fit(centres_3d)
        if new_axis.dot(axis) < 0.0:
            new_axis = -new_axis
        moved_deg = math.degrees(new_axis.angle(axis))
        shifted = ((new_point - point) - new_axis * (new_point - point).dot(new_axis)).length
        history.append({"axis_moved_deg": round(moved_deg, 9),
                        "centre_moved_mm": round(shifted, 9)})
        point, axis = new_point, new_axis

        radius = sum(ring["radius_mm"] for ring in ring_groups) / len(ring_groups)
        # The residual that gates the fit is measured on the wall's own vertices
        # against the fitted cylinder, not on the slices: the slices are where
        # the fit looked, and a fit graded by its own sample points grades itself.
        squares = 0.0
        for vi in kept_set:
            offset = surface.vertices[vi] - point
            squares += ((offset - axis * offset.dot(axis)).length - radius) ** 2
        residual = math.sqrt(squares / len(kept_set))
        if moved_deg < 1.0e-9 and shifted < 1.0e-9:
            break

    if residual > MAX_RADIAL_RESIDUAL_FRACTION * radius:
        raise ForgeError(
            "The surface near the hint on %r is not cylindrical: its vertices sit "
            "%.5f mm RMS off the best-fit cylinder of radius %.4f mm (%.2f%%, the "
            "limit is %.0f%%). Nothing is seated against a fit this bad."
            % (surface.name, residual, radius, 100.0 * residual / radius,
               100.0 * MAX_RADIAL_RESIDUAL_FRACTION))

    # What a peg actually has to fit through: the walls, not the vertices.
    inscribed = radius
    for index in kept_faces:
        center = surface.face_center[index]
        offset = center - point
        inscribed = min(inscribed, (offset - axis * offset.dot(axis)).length)

    depths = []
    for index in kept_faces:
        for vi in surface.face_verts[index]:
            depths.append((surface.vertices[vi] - point).dot(axis))
    return {
        "axis": axis,
        "point": point,
        "radius_mm": radius,
        "inscribed_radius_mm": inscribed,
        "residual_rms_mm": residual,
        "rings": ring_groups,
        "wall_faces": kept_faces,
        "flooded_faces": len(faces),
        "depth_low": min(depths),
        "depth_high": max(depths),
        "passes": history,
    }


def _cap_depth(surface, near_faces, axis, point, radius, target_depth, want_sign):
    """A cap across the bore at one end, or ``None``.

    A blind bore ends in a face whose outward normal points **back towards the
    mouth**, and that sign is what tells the two ends of a fitted cylinder
    apart.  It is a better answer than "the end nearer the hint" because it is a
    property of the geometry rather than of where the caller pointed, and it is
    a better answer than the wall's own far end because a chamfered or filleted
    floor puts the wall's end above the floor.
    """
    candidates = []
    for index in near_faces:
        normal = surface.face_normal[index]
        if normal.dot(axis) * want_sign < 0.9:
            continue
        # EVERY vertex inside the bore, not just the face's centre: a cap is
        # bounded by the bore's own rim.  Measured the expensive way on this
        # module's own fixture — a boolean had re-tessellated the base's top
        # face into pieces, one piece's centre landed 3 mm from the socket axis,
        # and its 1180 mm2 of area dragged the area-weighted floor from 10.5 mm
        # to 0.28 mm.  The seat that came out of that was refused, correctly,
        # for the wrong reason.
        depths = []
        outside = False
        for vertex in surface.face_verts[index]:
            offset = surface.vertices[vertex] - point
            depth = offset.dot(axis)
            if (offset - axis * depth).length > radius * 1.2:
                outside = True
                break
            depths.append(depth)
        if outside or not depths:
            continue
        depth = sum(depths) / len(depths)
        if abs(depth - target_depth) > radius:
            continue
        candidates.append((depth, surface.face_area[index]))
    if not candidates:
        return None
    total = sum(area for _, area in candidates)
    if total <= 0.0:
        return None
    return sum(depth * area for depth, area in candidates) / total


def _keyway(surface, near_faces, axis, point, radius, low, high, interior):
    """The anti-rotation slot (or rib), measured, or ``None``.

    Found as the radial faces that sit *beyond* the bore's own radius and still
    face the axis — a keyway's far wall is the only thing in a socket that does
    that.  For a peg (``interior=False``) the same test with the sign flipped
    finds the rib.
    """
    span = high - low
    margin = max(0.1 * span, 0.2 * radius)
    verts = set()
    faces = []
    for index in near_faces:
        normal = surface.face_normal[index]
        if abs(normal.dot(axis)) > WALL_PERPENDICULAR_MAX:
            continue
        center = surface.face_center[index]
        offset = center - point
        depth = offset.dot(axis)
        if depth < low - margin or depth > high + margin:
            continue
        radial = offset - axis * depth
        if radial.length < 1.0e-9:
            continue
        aligned = normal.dot(radial.normalized())
        # Radial enough for its sign to mean something: a slot's SIDE walls are
        # perpendicular to the radius, so their sign is noise, and letting one
        # side in and not the other would tilt the measured angle.
        if abs(aligned) < WALL_RADIAL_MIN:
            continue
        if interior and aligned > 0.0:
            continue
        if not interior and aligned < 0.0:
            continue
        if radial.length <= radius * 1.05 or radial.length > radius * 3.0:
            continue
        faces.append(index)
        verts.update(surface.face_verts[index])
    if len(faces) < 1 or len(verts) < 3:
        return None

    u, v = _basis_for(axis)
    angles = []
    radii = []
    depths = []
    for vi in sorted(verts):
        offset = surface.vertices[vi] - point
        depth = offset.dot(axis)
        radial = offset - axis * depth
        angles.append(math.atan2(radial.dot(v), radial.dot(u)))
        radii.append(radial.length)
        depths.append(depth)
    # Circular mean, so a slot straddling the ±180° seam reports its own angle
    # rather than the average of the two sides of the cut.
    mean_x = sum(math.cos(angle) for angle in angles) / len(angles)
    mean_y = sum(math.sin(angle) for angle in angles) / len(angles)
    if math.hypot(mean_x, mean_y) < 1.0e-9:
        return None
    mean_angle = math.atan2(mean_y, mean_x)
    direction = (u * math.cos(mean_angle) + v * math.sin(mean_angle)).normalized()
    lateral = axis.cross(direction).normalized()
    laterals = [(surface.vertices[vi] - point).dot(lateral) for vi in sorted(verts)]
    deviations = [abs(math.atan2(math.sin(angle - mean_angle),
                                 math.cos(angle - mean_angle))) for angle in angles]
    if math.degrees(max(deviations)) > MAX_KEYWAY_HALF_WIDTH_DEG:
        # Not a keyway: something that wraps most of the way round the axis is a
        # counterbore, a collar or a neighbouring wall the capture reached.
        return None
    return {
        "direction": direction,
        "angle_deg": math.degrees(mean_angle),
        "angular_half_width_deg": math.degrees(max(deviations)),
        "width_mm": max(laterals) - min(laterals),
        "outer_radius_mm": max(radii),
        "radial_depth_mm": max(radii) - radius,
        "depth_low_mm": min(depths),
        "depth_high_mm": max(depths),
        "faces": len(faces),
        "vertices": len(verts),
    }


# ---------------------------------------------------------------------------
# the measured socket / peg
# ---------------------------------------------------------------------------

def _hint_point(surface, params, prefix):
    """The hint, either as a point or as an angle on the part's own rim."""
    raw = params.get(prefix + "_hint_mm")
    if raw is not None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ForgeError("Parameter %r must be three numbers (a world-space "
                             "millimetre point), got %r." % (prefix + "_hint_mm", raw))
        try:
            point = Vector([float(value) for value in raw])
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r must be three numbers, got %r."
                             % (prefix + "_hint_mm", raw))
        if not all(math.isfinite(value) for value in point):
            raise ForgeError("Parameter %r must be finite." % (prefix + "_hint_mm"))
        return point, "given as a point"

    angle = params.get(prefix + "_angle_deg")
    if angle is None:
        raise ForgeError(
            "Give the socket's whereabouts: %s_hint_mm=[x,y,z] (a world point in or "
            "just outside the bore's mouth) or %s_angle_deg plus %s_z_mm (an angle on "
            "the part's rim, the eevee-bowl form)." % (prefix, prefix, prefix))
    angle = math.radians(common.get_float(params, prefix + "_angle_deg"))
    height = common.get_float(params, prefix + "_z_mm")
    low, high = surface.bounds
    center = Vector(((low[0] + high[0]) * 0.5, (low[1] + high[1]) * 0.5, height))
    given_radius = params.get(prefix + "_radius_mm")
    if given_radius is not None:
        radius = common.get_float(params, prefix + "_radius_mm", minimum=0.0)
        basis = "rim radius given as %.4f mm" % radius
    else:
        band = max(1.0, 0.02 * (high[2] - low[2]))
        direction = Vector((math.cos(angle), math.sin(angle), 0.0))
        best = None
        for co in surface.vertices:
            if abs(co[2] - height) > band:
                continue
            radial = Vector((co[0] - center[0], co[1] - center[1], 0.0))
            if radial.length < 1.0e-9:
                continue
            if radial.normalized().dot(direction) < math.cos(math.radians(15.0)):
                continue
            if best is None or radial.length > best:
                best = radial.length
        if best is None:
            raise ForgeError(
                "No vertices of %r within %.3f mm of z=%.3f mm at %.2f deg, so there is "
                "no rim there to hang a hint on. Give %s_hint_mm instead."
                % (surface.name, band, height, math.degrees(angle), prefix))
        radius = best
        basis = ("rim radius measured as %.4f mm (furthest vertex within %.3f mm of "
                 "z=%.3f at this angle)" % (radius, band, height))
    point = center + Vector((math.cos(angle), math.sin(angle), 0.0)) * radius
    return point, basis


def _search_radius(surface, params):
    given = params.get("search_radius_mm")
    if given is not None:
        value = common.get_float(params, "search_radius_mm", minimum=1.0e-3)
        return value, "given explicitly"
    value = min(50.0, max(5.0, 0.25 * surface.diagonal_mm))
    return value, ("0.25 x the part's %.3f mm bounding diagonal, clamped to [5, 50] mm"
                   % surface.diagonal_mm)


def measure_bore(surface, hint, interior, radius_mm, want_keyway=True):
    """Measure the bore (or the peg) nearest ``hint``.  Raises when there is none."""
    near = [index for index, center in enumerate(surface.face_center)
            if (center - hint).length <= radius_mm]
    if not near:
        raise ForgeError(
            "No face of %r is within %.3f mm of the hint at (%.4f, %.4f, %.4f) mm. The "
            "nearest surface is %.4f mm away."
            % (surface.name, radius_mm, hint[0], hint[1], hint[2],
               surface.nearest_distance_mm(hint)))

    seeds = _seed_candidates(surface, hint, interior, radius_mm)
    failures = []
    for seed in seeds:
        flooded = _flood(surface, seed, hint, radius_mm)
        try:
            fit = _fit_cylinder(surface, flooded, hint, interior)
        except ForgeError as exc:
            failures.append(str(exc))
            continue
        # The hint has to be IN the thing it names.  Without this a hint dropped
        # on a flat face finds whatever cylinder happens to be inside the search
        # radius and reports it with total confidence — which is the eevee
        # capture-radius failure wearing a better fit.
        hint_offset = Vector(hint) - fit["point"]
        hint_depth = hint_offset.dot(fit["axis"])
        hint_radial = (hint_offset - fit["axis"] * hint_depth).length
        allowed = HINT_RADIAL_FACTOR * fit["radius_mm"]
        margin = HINT_DEPTH_FACTOR * fit["radius_mm"]
        if (hint_radial > allowed
                or hint_depth < fit["depth_low"] - margin
                or hint_depth > fit["depth_high"] + margin):
            failures.append(
                "a %.4f mm cylinder was fitted but the hint sits %.4f mm off its axis "
                "(limit %.4f mm) at depth %.4f mm against a wall spanning %.4f..%.4f mm "
                "— that cylinder is somewhere else on %r, not where the hint points."
                % (fit["radius_mm"], hint_radial, allowed, hint_depth,
                   fit["depth_low"], fit["depth_high"], surface.name))
            continue
        fit["seed_face"] = seed
        fit["seed_distance_mm"] = (surface.face_center[seed] - hint).length
        fit["near_faces"] = len(near)
        fit["seeds_tried"] = seeds.index(seed) + 1
        break
    else:
        raise ForgeError(
            "No %s cylinder could be fitted near the hint at (%.4f, %.4f, %.4f) mm on "
            "%r; %d candidate seed face(s) were tried. The last said: %s"
            % ("interior" if interior else "exterior", hint[0], hint[1], hint[2],
               surface.name, len(seeds), failures[-1] if failures else "(nothing)"))

    axis, point = fit["axis"], fit["point"]
    low, high = fit["depth_low"], fit["depth_high"]
    radius = fit["radius_mm"]

    # Orient: the axis is reported pointing INTO the bore, which is the
    # direction a peg travels.  A cap at one end and not the other says which
    # end is the floor; failing that, the end further from the hint is.
    cap_high = _cap_depth(surface, near, axis, point, radius, high, -1.0)
    cap_low = _cap_depth(surface, near, axis, point, radius, low, +1.0)
    if cap_high is not None and cap_low is None:
        flip, floor_source = False, "cap face"
    elif cap_low is not None and cap_high is None:
        flip, floor_source = True, "cap face"
    else:
        hint_depth = (Vector(hint) - point).dot(axis)
        flip = abs(hint_depth - low) > abs(hint_depth - high)
        floor_source = ("the far end of the wall (%s)"
                        % ("no cap face found" if cap_high is None
                           else "capped at both ends"))
    # When the axis flips, every depth measured along it flips with it — the fit
    # is rewritten rather than the sign carried, so nothing downstream has to
    # remember which way round this went.
    if flip:
        axis = -axis
        fit["axis"] = axis
        low, high = -high, -low
        fit["depth_low"], fit["depth_high"] = low, high
        cap_high, cap_low = (None if cap_low is None else -cap_low,
                             None if cap_high is None else -cap_high)
        for ring in fit["rings"]:
            ring["depth_mm"] = -ring["depth_mm"]
    mouth_depth = low
    if floor_source == "cap face" and cap_high is not None and cap_high > mouth_depth:
        floor_depth = cap_high
    else:
        floor_depth = high
        if floor_source == "cap face":
            floor_source = "the far end of the wall (the cap sat above the mouth)"

    mouth_center = point + axis * mouth_depth
    floor_center = point + axis * floor_depth
    keyway = (_keyway(surface, near, axis, point, fit["radius_mm"], low, high, interior)
              if want_keyway else None)

    return {
        "object": surface.name,
        "interior": interior,
        "hint_mm": [round(value, 6) for value in hint],
        "search_radius_mm": round(radius_mm, 6),
        "axis": axis,
        "axis_unit": [round(value, 9) for value in axis],
        "mouth_center": mouth_center,
        "mouth_center_mm": [round(value, 6) for value in mouth_center],
        "floor_center": floor_center,
        "floor_center_mm": [round(value, 6) for value in floor_center],
        "depth_mm": round(floor_depth - mouth_depth, 6),
        "floor_source": floor_source,
        "wall_span_mm": round(high - low, 6),
        "radius_mm": round(fit["radius_mm"], 6),
        "inscribed_radius_mm": round(fit["inscribed_radius_mm"], 6),
        "polygon_sag_mm": round(fit["radius_mm"] - fit["inscribed_radius_mm"], 6),
        "residual_rms_mm": round(fit["residual_rms_mm"], 8),
        "circularity_pct": round(100.0 * fit["residual_rms_mm"] / fit["radius_mm"], 6),
        "rings": [{key: round(value, 6) if isinstance(value, float) else value
                   for key, value in ring.items()} for ring in fit["rings"]],
        "ring_count": len(fit["rings"]),
        "wall_faces": len(fit["wall_faces"]),
        "flooded_faces": fit["flooded_faces"],
        "near_faces": fit["near_faces"],
        "seed_face": fit["seed_face"],
        "seeds_tried": fit["seeds_tried"],
        "seed_distance_mm": round(fit["seed_distance_mm"], 6),
        "fit_passes": fit["passes"],
        "keyway": keyway,
        # Where the mouth sits along the fitted axis, measured from the fit's own
        # axis point — the frame every other depth in this dict is quoted in.
        "_mouth_depth": mouth_depth,
        "_raw": fit,
    }


def _public(measurement):
    """The report-safe half of a measurement (no mathutils objects)."""
    out = {key: value for key, value in measurement.items()
           if not key.startswith("_")
           and key not in ("axis", "mouth_center", "floor_center", "keyway")}
    keyway = measurement.get("keyway")
    if keyway is not None:
        out["keyway"] = {key: (round(value, 6) if isinstance(value, float) else value)
                         for key, value in keyway.items() if key != "direction"}
        out["keyway"]["direction_unit"] = [round(value, 9)
                                           for value in keyway["direction"]]
    else:
        out["keyway"] = None
    return out


# ---------------------------------------------------------------------------
# the peg
# ---------------------------------------------------------------------------

def _spec_radius_mm(spec):
    kind = (spec or {}).get("kind")
    if kind in ("cylinder", "peg"):
        return float(spec["diameter_mm"]) * 0.5
    if kind == "cone":
        return max(float(spec["bottom_diameter_mm"]), float(spec["top_diameter_mm"])) * 0.5
    return None


def _peg_from_record(part, record):
    """The peg a boss record describes: axis, tip, radius, rib — all exact.

    The record's matrix is the boss's **world** matrix as it stood when it was
    attached.  Everything here therefore also carries the part-local form of it,
    which is what survives the part being moved, and the caller checks the world
    form against the part's own surface before trusting it.
    """
    spec = record.get("spec")
    radius = _spec_radius_mm(spec)
    if radius is None:
        return None
    matrix = bosses.matrix_from_mm(record["matrix_mm"])
    rotation = matrix.to_3x3()
    axis_raw = rotation @ Vector((0.0, 0.0, 1.0))
    rib_raw = rotation @ Vector((1.0, 0.0, 0.0))
    scale_z = axis_raw.length
    scale_x = rib_raw.length
    if scale_z < 1.0e-9 or scale_x < 1.0e-9:
        raise ForgeError("Boss %r on %r has a degenerate matrix (zero scale)."
                         % (record.get("id"), part.name))
    axis = axis_raw.normalized()
    base = Vector(matrix.translation) * M_TO_MM
    length = float(spec["length_mm"]) * scale_z
    peg = {
        "source": "ledger",
        "boss_id": record.get("id"),
        "label": record.get("label", ""),
        "kind": record.get("kind", ""),
        "spec": spec,
        "axis": axis,
        "base": base,
        "tip": base + axis * length,
        "length_mm": length,
        "radius_mm": radius * scale_x,
        "scale": [round(scale_x, 6), round(rotation.col[1].length, 6), round(scale_z, 6)],
        "matrix": matrix,
        "rib": None,
    }
    rib = (spec or {}).get("rib")
    if isinstance(rib, dict):
        peg["rib"] = {
            "direction": rib_raw.normalized(),
            "width_mm": float(rib["width_mm"]) * rotation.col[1].length,
            "height_mm": float(rib["height_mm"]) * scale_x,
            "source": "ledger spec",
        }
    return peg


def _peg_measured(surface, hint, radius_mm):
    """A peg measured off the mesh, for a part with no usable ledger."""
    measurement = measure_bore(surface, hint, interior=False, radius_mm=radius_mm)
    axis = measurement["axis"]
    point = measurement["_raw"]["point"]
    low, high = measurement["_raw"]["depth_low"], measurement["_raw"]["depth_high"]
    # The tip is the end further from the part's own centroid: a peg sticks out.
    centroid_depth = (surface.centroid - point).dot(axis)
    if abs(high - centroid_depth) >= abs(low - centroid_depth):
        tip_depth, base_depth = high, low
    else:
        axis = -axis
        tip_depth, base_depth = -low, -high
    tip = point + axis * tip_depth
    base = point + axis * base_depth
    peg = {
        "source": "measured",
        "boss_id": None,
        "label": "",
        "kind": "measured cylinder",
        "spec": None,
        "axis": axis,
        "base": base,
        "tip": tip,
        "length_mm": tip_depth - base_depth,
        "radius_mm": measurement["radius_mm"],
        "inscribed_radius_mm": measurement["inscribed_radius_mm"],
        "circumscribed_radius_mm": measurement["radius_mm"],
        "matrix": None,
        "rib": None,
        "measurement": measurement,
    }
    keyway = measurement.get("keyway")
    if keyway is not None:
        peg["rib"] = {
            "direction": keyway["direction"],
            "width_mm": keyway["width_mm"],
            "height_mm": keyway["radial_depth_mm"],
            "source": "measured off the peg",
        }
    return peg


def _peg_public(peg):
    out = {
        "source": peg["source"],
        "boss_id": peg.get("boss_id"),
        "label": peg.get("label", ""),
        "kind": peg.get("kind", ""),
        "axis_unit": [round(value, 9) for value in peg["axis"]],
        "base_mm": [round(value, 6) for value in peg["base"]],
        "tip_mm": [round(value, 6) for value in peg["tip"]],
        "length_mm": round(peg["length_mm"], 6),
        "radius_mm": round(peg["radius_mm"], 6),
    }
    if peg.get("scale"):
        out["boss_scale"] = peg["scale"]
    if peg.get("rib"):
        out["rib"] = {
            "direction_unit": [round(value, 9) for value in peg["rib"]["direction"]],
            "width_mm": round(peg["rib"]["width_mm"], 6),
            "height_mm": round(peg["rib"]["height_mm"], 6),
            "source": peg["rib"]["source"],
        }
    else:
        out["rib"] = None
    if peg.get("measurement"):
        out["measurement"] = _public(peg["measurement"])
    return out


def _transform_peg(peg, matrix):
    """The same peg after a rigid world transform (millimetre points, unit axes)."""
    rotation = matrix.to_3x3()

    def move(point):
        return Vector(matrix @ (Vector(point) * MM)) * M_TO_MM

    moved = dict(peg)
    moved["axis"] = (rotation @ peg["axis"]).normalized()
    moved["base"] = move(peg["base"])
    moved["tip"] = move(peg["tip"])
    if peg.get("rib"):
        moved["rib"] = dict(peg["rib"])
        moved["rib"]["direction"] = (rotation @ peg["rib"]["direction"]).normalized()
    if peg.get("matrix") is not None:
        moved["matrix"] = matrix @ peg["matrix"]
    return moved


# ---------------------------------------------------------------------------
# verification — the part that refuses
# ---------------------------------------------------------------------------

def _inside_bore(point, socket, radius, tolerance):
    offset = Vector(point) - socket["mouth_center"]
    depth = offset.dot(socket["axis"])
    if depth < -tolerance or depth > socket["depth_mm"] + tolerance:
        return False
    return (offset - socket["axis"] * depth).length <= radius + tolerance


def _inside_keyway(point, socket, tolerance):
    keyway = socket.get("keyway")
    if keyway is None:
        return False
    offset = Vector(point) - socket["mouth_center"]
    depth = offset.dot(socket["axis"])
    if depth < keyway["depth_low_mm"] - socket["_mouth_depth"] - tolerance:
        return False
    radial = offset - socket["axis"] * depth
    if radial.length > keyway["outer_radius_mm"] + tolerance:
        return False
    if radial.length < 1.0e-9:
        return True
    angle = math.degrees(math.acos(
        max(-1.0, min(1.0, radial.normalized().dot(keyway["direction"])))))
    return angle <= keyway["angular_half_width_deg"] + 1.0


def _intersection_check(part_surface, base_surface, matrix, socket, peg, max_samples):
    """Part vertices that land inside the base, outside the bore.  Should be zero.

    The same pass measures the **shoulder gap**: how close the part's own body
    comes to the bore's mouth plane in the annulus just outside the bore.  That
    number is the seam a render shows — eevee-bowl-v2's ear "still short of
    fully flush" — and measuring it here means it is reported rather than
    discovered in a picture.
    """
    low, high = base_surface.bounds
    tolerance = max(1.0e-3, 0.002 * peg["radius_mm"])
    bore_radius = peg["radius_mm"] + tolerance
    shoulder_inner = socket["inscribed_radius_mm"]
    shoulder_outer = 3.0 * socket["radius_mm"]
    shoulder_gap = None
    candidates = []
    for co in part_surface.vertices:
        moved = Vector(matrix @ (co * MM)) * M_TO_MM
        offset = moved - socket["mouth_center"]
        depth = offset.dot(socket["axis"])
        if depth < 0.0:
            radial = (offset - socket["axis"] * depth).length
            if shoulder_inner < radial <= shoulder_outer:
                if shoulder_gap is None or -depth < shoulder_gap:
                    shoulder_gap = -depth
        if (moved[0] < low[0] or moved[0] > high[0]
                or moved[1] < low[1] or moved[1] > high[1]
                or moved[2] < low[2] or moved[2] > high[2]):
            continue
        candidates.append(moved)
    in_bounds = len(candidates)
    stride = 1
    if len(candidates) > max_samples:
        stride = int(math.ceil(len(candidates) / float(max_samples)))
        candidates = candidates[::stride]

    inside_base = 0
    offenders = []
    for moved in candidates:
        if not base_surface.contains(moved):
            continue
        inside_base += 1
        if _inside_bore(moved, socket, bore_radius, tolerance):
            continue
        if _inside_keyway(moved, socket, tolerance):
            continue
        offenders.append((base_surface.nearest_distance_mm(moved), moved))
    offenders.sort(key=lambda item: -item[0])
    return {
        "part_vertices": len(part_surface.vertices),
        "in_base_bounds": in_bounds,
        "sampled": len(candidates),
        "sample_stride": stride,
        "inside_base": inside_base,
        "inside_base_outside_bore": len(offenders),
        "deepest_penetration_mm": round(offenders[0][0], 6) if offenders else 0.0,
        "deepest_point_mm": ([round(value, 6) for value in offenders[0][1]]
                             if offenders else None),
        "bore_exclusion_radius_mm": round(bore_radius, 6),
        "shoulder_gap_mm": (round(shoulder_gap, 6) if shoulder_gap is not None else None),
        "shoulder_annulus_mm": [round(shoulder_inner, 6), round(shoulder_outer, 6)],
    }


def _verify(part_surface, base_surface, matrix, socket, peg, params):
    """Every number the seat is judged on.  Returns ``(checks, measured)``."""
    seated = _transform_peg(peg, matrix)
    axis = socket["axis"]
    mouth = socket["mouth_center"]

    offset = seated["tip"] - mouth
    tip_depth = offset.dot(axis)
    tip_radial = (offset - axis * tip_depth).length
    angle_deg = math.degrees(seated["axis"].angle(axis))
    floor_gap = socket["depth_mm"] - tip_depth
    base_offset = seated["base"] - mouth
    base_depth = base_offset.dot(axis)

    radial_clearance = socket["inscribed_radius_mm"] - peg["radius_mm"]
    # The tilt that closes the whole radial clearance over the peg's length: past
    # it the peg is jammed against the wall rather than seated in the bore.
    derived_angle = max(MIN_ANGLE_TOLERANCE_DEG,
                        math.degrees(math.atan2(max(radial_clearance, 0.0),
                                                max(peg["length_mm"], 1.0e-9))))
    angle_tolerance = params.get("angle_tolerance_deg")
    if angle_tolerance is not None:
        angle_tolerance = common.get_float(params, "angle_tolerance_deg", minimum=0.0)
        angle_basis = "given explicitly"
    else:
        angle_tolerance = derived_angle
        angle_basis = ("atan(radial clearance %.5f mm / peg length %.4f mm), floored at "
                       "%g deg" % (radial_clearance, peg["length_mm"],
                                   MIN_ANGLE_TOLERANCE_DEG))
    radial_tolerance = params.get("radial_tolerance_mm")
    if radial_tolerance is not None:
        radial_tolerance = common.get_float(params, "radial_tolerance_mm", minimum=0.0)
        radial_basis = "given explicitly"
    else:
        radial_tolerance = max(radial_clearance, MIN_RADIAL_TOLERANCE_MM)
        radial_basis = ("the measured radial clearance (inscribed bore %.5f mm - peg "
                        "%.5f mm)" % (socket["inscribed_radius_mm"], peg["radius_mm"]))
        if radial_clearance < MIN_RADIAL_TOLERANCE_MM:
            radial_basis += (", floored at %g mm (the peg does not fit at all; that is "
                             "the check above, not this one)" % MIN_RADIAL_TOLERANCE_MM)

    max_samples = common.get_int(params, "max_samples", DEFAULT_MAX_SAMPLES, minimum=16)
    overlap = _intersection_check(part_surface, base_surface, matrix, socket, peg,
                                  max_samples)

    shoulder_gap = overlap["shoulder_gap_mm"]

    rib_error_deg = None
    rib_fits = True
    rib_detail = ""
    if seated.get("rib") and socket.get("keyway"):
        rib_error_deg = abs(math.degrees(_signed_angle(
            seated["rib"]["direction"], socket["keyway"]["direction"], axis)))
        rib_fits = (seated["rib"]["width_mm"] <= socket["keyway"]["width_mm"] + 1.0e-6
                    and seated["rib"]["height_mm"]
                    <= socket["keyway"]["radial_depth_mm"] + 1.0e-6)
        rib_detail = ("rib %.4f x %.4f mm into a %.4f x %.4f mm keyway"
                      % (seated["rib"]["width_mm"], seated["rib"]["height_mm"],
                         socket["keyway"]["width_mm"],
                         socket["keyway"]["radial_depth_mm"]))

    measured = {
        "tip_axis_offset_mm": round(tip_radial, 8),
        "tip_depth_mm": round(tip_depth, 6),
        "floor_gap_mm": round(floor_gap, 6),
        "peg_base_depth_mm": round(base_depth, 6),
        "axis_angle_deg": round(angle_deg, 8),
        "axis_angle_tolerance_deg": round(angle_tolerance, 8),
        "axis_angle_tolerance_basis": angle_basis,
        "radial_clearance_mm": round(radial_clearance, 6),
        "radial_tolerance_mm": round(radial_tolerance, 8),
        "radial_tolerance_basis": radial_basis,
        "socket_depth_mm": socket["depth_mm"],
        "peg_length_mm": round(peg["length_mm"], 6),
        # How much of the peg the bore does NOT hold. Positive is normal when
        # the peg's base is buried in the part (a boss's spec length is measured
        # from its own Z=0, not from the part's surface); it is only a problem
        # when the part's body then has to be somewhere solid, and that is what
        # the intersection check below is for.
        "peg_outside_bore_mm": round(peg["length_mm"] - tip_depth, 6),
        "peg_radius_mm": round(peg["radius_mm"], 6),
        "bore_radius_mm": socket["radius_mm"],
        "bore_inscribed_radius_mm": socket["inscribed_radius_mm"],
        "shoulder_gap_mm": (round(shoulder_gap, 6) if shoulder_gap is not None else None),
        "rib_alignment_error_deg": (round(rib_error_deg, 8)
                                    if rib_error_deg is not None else None),
        "overlap": overlap,
        "seated_tip_mm": [round(value, 6) for value in seated["tip"]],
        "seated_base_mm": [round(value, 6) for value in seated["base"]],
        "seated_axis_unit": [round(value, 9) for value in seated["axis"]],
    }

    checks = [
        ("peg fits the bore",
         radial_clearance >= 0.0,
         "peg radius %.5f mm against an inscribed bore radius of %.5f mm: clearance "
         "%.5f mm" % (peg["radius_mm"], socket["inscribed_radius_mm"], radial_clearance)),
        ("the tip is on the socket axis",
         tip_radial <= radial_tolerance + 1.0e-9,
         "tip sits %.6f mm off the measured axis; tolerance %.6f mm (%s)"
         % (tip_radial, radial_tolerance, radial_basis)),
        ("the tip is inside the bore",
         -1.0e-6 <= tip_depth <= socket["depth_mm"] + 1.0e-6,
         "tip %.4f mm past the mouth of a %.4f mm bore (floor gap %.4f mm)"
         % (tip_depth, socket["depth_mm"], floor_gap)),
        ("the axes are parallel",
         angle_deg <= angle_tolerance + 1.0e-9,
         "peg axis %.6f deg off the socket axis; tolerance %.6f deg (%s)"
         % (angle_deg, angle_tolerance, angle_basis)),
        ("the part does not cut into the base",
         overlap["inside_base_outside_bore"] == 0,
         "%d of %d sampled part vertices are inside %r and outside the bore "
         "(deepest %.4f mm in)"
         % (overlap["inside_base_outside_bore"], overlap["sampled"],
            base_surface.name, overlap["deepest_penetration_mm"])),
    ]
    if rib_error_deg is not None:
        checks.append(("the rib fits the keyway", rib_fits,
                       rib_detail or "rib against keyway"))
    return checks, measured


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

@command("measure_socket")
def cmd_measure_socket(params):
    """Measure the bore near a hint: axis, mouth, floor, radius, keyway.

    Nothing is changed.  This is the half of ``seat_part`` worth running on its
    own — it is the answer to "where is the socket, actually", which is the
    question eevee-bowl-v2 kept answering with a formula.
    """
    base = common.resolve_object(params, key="object", mesh_only=True)
    surface = _Surface(base)
    hint, hint_basis = _hint_point(surface, params, "socket")
    radius, radius_basis = _search_radius(surface, params)
    interior = common.get_bool(params, "interior", True)
    measurement = measure_bore(surface, hint, interior, radius)
    report = _public(measurement)
    report.update({
        "hint_basis": hint_basis,
        "search_radius_basis": radius_basis,
        "honesty": (
            "radius_mm is fitted to the bore's VERTICES; inscribed_radius_mm is the "
            "closest its wall FACES come to the axis, and that is what a peg has to "
            "fit through. The %.5f mm between them is the polygon's own sag, not a "
            "measurement error. Every seat check uses the inscribed number."
            % report["polygon_sag_mm"]),
        "next": "seat_part with this same socket hint",
    })
    return report


@command("measure_peg")
def cmd_measure_peg(params):
    """Measure a peg on a part: axis, tip, length, radius, rib.

    The ledger route is better when there is a ledger (``list_bosses`` shows
    it); this is the fallback for a part whose peg was sculpted, imported, or
    attached before ``attach_boss`` existed.
    """
    part = common.resolve_object(params, key="object", mesh_only=True)
    surface = _Surface(part)
    hint, hint_basis = _hint_point(surface, params, "peg")
    radius, radius_basis = _search_radius(surface, params)
    peg = _peg_measured(surface, hint, radius)
    report = _peg_public(peg)
    report.update({
        "object": part.name,
        "hint_basis": hint_basis,
        "search_radius_basis": radius_basis,
        "honesty": (
            "A measured peg's length is the length of its EXPOSED cylindrical wall. "
            "If it is unioned into a part, the buried part of it is not in the mesh "
            "any more and is not in this number."),
        "next": "seat_part (it takes the same peg_hint_mm)",
    })
    return report


def _pick_record(part, params):
    entries = bosses.records(part)
    if not entries:
        return None, entries
    wanted = params.get("boss_id")
    if wanted is not None:
        return bosses.find_record(part, common.get_str(params, "boss_id"), entries), entries
    if len(entries) == 1:
        return entries[0], entries
    raise ForgeError(
        "%r carries %d bosses (%s) and no boss_id was given, so there is no telling "
        "which one is the peg to seat."
        % (part.name, len(entries), ", ".join(repr(e.get("id")) for e in entries)))


def _apply_to_ledger(part, entries, transform):
    """Carry the boss ledger and its retained cutters along with the part.

    Not optional bookkeeping: the ledger holds WORLD matrices and the retained
    cutters are separate objects that do not follow the part, so a seat that
    skipped this would leave every boss on the part un-detachable at its old
    address — the exact failure the bosses module exists to prevent.
    """
    moved_objects = []
    for entry in entries:
        raw = entry.get("matrix_mm")
        if raw:
            entry["matrix_mm"] = bosses.matrix_to_mm(
                transform @ bosses.matrix_from_mm(raw))
        for role in ("cutter", "added"):
            obj = bosses._find_retained(entry, role)
            if obj is None:
                continue
            obj.matrix_world = transform @ obj.matrix_world
            moved_objects.append(obj.name)
    if entries:
        bosses.write_records(part, entries)
    common.refresh_view_layer()
    return moved_objects


@command("seat_part")
def cmd_seat_part(params):
    """Seat a part's peg into a measured socket on another object, or refuse.

    Measures the socket, takes the peg from the part's boss ledger (or measures
    it), computes the rigid transform that puts the peg's tip on the socket's
    floor along the socket's own axis, **verifies it numerically**, and only
    then moves the part.  A failure is an error carrying every number.
    """
    part = common.resolve_object(params, key="object", mesh_only=True)
    common.require_in_view_layer(part)
    base = common.find_object(common.get_str(params, "base"), mesh_only=True)
    if base is part:
        raise ForgeError("The base must be a different object from the part.")

    part_surface = _Surface(part)
    base_surface = _Surface(base)

    # --- the socket ------------------------------------------------------
    hint, hint_basis = _hint_point(base_surface, params, "socket")
    base_radius, base_radius_basis = _search_radius(base_surface, params)
    socket = measure_bore(base_surface, hint, interior=True, radius_mm=base_radius)

    # --- the peg ---------------------------------------------------------
    notes = []
    peg_hint = (params.get("peg_hint_mm") is not None
                or params.get("peg_angle_deg") is not None)
    # The ledger is carried through the move whether or not it is the peg source,
    # so it is read either way; only the CHOICE of record needs a boss_id.
    entries = bosses.records(part)
    record = None if peg_hint else _pick_record(part, params)[0]
    peg = None
    if record is not None:
        peg = _peg_from_record(part, record)
        if peg is None:
            notes.append(
                "Boss %r on %r has no spec (it was attached as an object), so the peg "
                "was measured off the mesh instead of read from the ledger."
                % (record.get("id"), part.name))
    if peg is not None:
        # The ledger's matrix is a world matrix from attach time; if the part has
        # moved since, it is stale, and a stale peg is a wrong seat that verifies
        # perfectly against itself. The union left a cap at the tip, so the mesh
        # can say whether the record still describes this part where it stands.
        tip_distance = part_surface.nearest_distance_mm(peg["tip"])
        stale_limit = max(1.0, 0.2 * peg["length_mm"])
        peg["ledger_tip_to_surface_mm"] = round(tip_distance, 6)
        if tip_distance > stale_limit:
            raise ForgeError(
                "The boss ledger on %r is stale: boss %r records a tip at (%.4f, %.4f, "
                "%.4f) mm, and the nearest surface of %r is %.4f mm away (the limit is "
                "%.4f mm = max(1 mm, 20%% of the %.4f mm peg)). The part was moved after "
                "the attach, so the recorded WORLD matrix no longer describes it. Seat "
                "it with peg_hint_mm instead, or re-attach the boss."
                % (part.name, record.get("id"), peg["tip"][0], peg["tip"][1],
                   peg["tip"][2], part.name, tip_distance, stale_limit, peg["length_mm"]))
        if tip_distance > max(0.05, 0.01 * peg["length_mm"]):
            notes.append(
                "The ledger's tip sits %.4f mm off %r's surface. It is inside the stale "
                "limit of %.4f mm, but it is not zero: the boss was moved, or the part "
                "was edited since the attach."
                % (tip_distance, part.name, stale_limit))
    else:
        part_radius, part_radius_basis = _search_radius(part_surface, params)
        hint_point, peg_hint_basis = _hint_point(part_surface, params, "peg")
        peg = _peg_measured(part_surface, hint_point, part_radius)
        peg["hint_basis"] = peg_hint_basis
        peg["search_radius_basis"] = part_radius_basis
        if record is None and not peg_hint:
            notes.append("%r carries no boss ledger, so its peg was measured."
                         % part.name)

    if peg["radius_mm"] <= 0.0 or peg["length_mm"] <= 0.0:
        raise ForgeError("The peg on %r measures %.4f mm radius by %.4f mm long; that is "
                         "not a peg." % (part.name, peg["radius_mm"], peg["length_mm"]))

    # --- the transform ---------------------------------------------------
    axis = socket["axis"]
    rotation = peg["axis"].rotation_difference(axis)
    if peg["axis"].dot(axis) < -0.999999:
        notes.append(
            "The peg points almost exactly opposite the socket axis, so the roll of the "
            "flip is arbitrary; give roll_deg if the part has a right way up.")

    keyway = socket.get("keyway")
    roll_auto = 0.0
    roll_basis = "no keyway measured and no rib to align"
    if peg.get("rib") and keyway is not None:
        turned = (rotation @ peg["rib"]["direction"]).normalized()
        roll_auto = _signed_angle(turned, keyway["direction"], axis)
        roll_basis = ("the %s rib turned onto the keyway measured at %.4f deg "
                      "(half-width %.4f deg)"
                      % (peg["rib"]["source"], keyway["angle_deg"],
                         keyway["angular_half_width_deg"]))
    elif peg.get("rib") and keyway is None:
        roll_basis = "the peg is keyed but NO keyway was measurable in the bore"
        notes.append(
            "The peg carries a rib (%.4f mm wide, %.4f mm proud) but no keyway was found "
            "in the bore, so the roll was NOT aligned to anything. A keyed peg only goes "
            "in one way round: check the socket, or set roll_deg."
            % (peg["rib"]["width_mm"], peg["rib"]["height_mm"]))
    elif keyway is not None:
        roll_basis = ("a keyway was measured at %.4f deg but the peg has no rib to put "
                      "in it" % keyway["angle_deg"])

    roll_extra = 0.0
    if params.get("roll_deg") is not None:
        roll_extra = math.radians(common.get_float(params, "roll_deg"))
        roll_basis += ", plus roll_deg=%.4f" % math.degrees(roll_extra)
    total_roll = roll_auto + roll_extra
    full_rotation = Quaternion(axis, total_roll) @ rotation

    clearance = params.get("insertion_clearance_mm")
    if clearance is not None:
        clearance = common.get_float(params, "insertion_clearance_mm", minimum=0.0)
        clearance_basis = "given explicitly"
    else:
        clearance = DEFAULT_INSERTION_CLEARANCE_MM
        clearance_basis = ("forge_lib's DEFAULT_DEPTH_EXTRA_MM: a service-built socket "
                           "is cut this much deeper than its peg is long")
    if params.get("tip_depth_mm") is not None:
        tip_depth = common.get_float(params, "tip_depth_mm", minimum=0.0)
        target_tip = socket["mouth_center"] + axis * tip_depth
        position_basis = "tip_depth_mm=%.4f past the measured mouth" % tip_depth
    else:
        target_tip = socket["floor_center"] - axis * clearance
        position_basis = ("the measured floor minus %.4f mm of insertion clearance (%s)"
                          % (clearance, clearance_basis))

    transform = (Matrix.Translation(target_tip * MM)
                 @ full_rotation.to_matrix().to_4x4()
                 @ Matrix.Translation(-peg["tip"] * MM))
    prospective = transform @ part.matrix_world

    # --- verify, before anything moves -----------------------------------
    checks, measured = _verify(part_surface, base_surface, transform, socket, peg, params)
    failed = [(label, detail) for label, ok, detail in checks if not ok]

    # A cross-check of the composition itself, for the ledger route: the boss's
    # new world matrix taken through the part's new matrix has to agree with the
    # peg this function transformed by hand.
    if peg.get("matrix") is not None:
        by_matrix = transform @ peg["matrix"]
        tip_by_matrix = (Vector(by_matrix.translation) * M_TO_MM
                         + (by_matrix.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
                         * peg["length_mm"])
        # Against the unrounded tip: the rounded one in the report would make
        # this agreement a measurement of the report's own decimal places.
        measured["ledger_matrix_agreement_mm"] = round(
            (tip_by_matrix - _transform_peg(peg, transform)["tip"]).length, 12)

    verification = {
        "passed": not failed,
        "checks": [{"check": label, "passed": bool(ok), "detail": detail}
                   for label, ok, detail in checks],
        "measured": measured,
    }

    if failed:
        lines = ["Refusing to seat %r into the socket on %r: %d of %d verification "
                 "checks failed." % (part.name, base.name, len(failed), len(checks))]
        for label, detail in failed:
            lines.append("  FAILED %s: %s" % (label, detail))
        lines.append("  socket: axis (%.6f, %.6f, %.6f), mouth (%.4f, %.4f, %.4f) mm, "
                     "floor (%.4f, %.4f, %.4f) mm, depth %.4f mm, radius %.4f mm "
                     "(inscribed %.4f mm), %d rings, %.5f mm RMS"
                     % (tuple(socket["axis"]) + tuple(socket["mouth_center"])
                        + tuple(socket["floor_center"])
                        + (socket["depth_mm"], socket["radius_mm"],
                           socket["inscribed_radius_mm"], socket["ring_count"],
                           socket["residual_rms_mm"])))
        lines.append("  peg (%s): tip (%.4f, %.4f, %.4f) mm, axis (%.6f, %.6f, %.6f), "
                     "%.4f mm long, %.4f mm radius"
                     % ((peg["source"],) + tuple(peg["tip"]) + tuple(peg["axis"])
                        + (peg["length_mm"], peg["radius_mm"])))
        lines.append("  would-be seat: tip offset %.6f mm, depth %.4f mm, floor gap "
                     "%.4f mm, axis angle %.6f deg, %d/%d sampled vertices inside the "
                     "base outside the bore (deepest %.4f mm)"
                     % (measured["tip_axis_offset_mm"], measured["tip_depth_mm"],
                        measured["floor_gap_mm"], measured["axis_angle_deg"],
                        measured["overlap"]["inside_base_outside_bore"],
                        measured["overlap"]["sampled"],
                        measured["overlap"]["deepest_penetration_mm"]))
        lines.append("  NOTHING was moved.")
        raise ForgeError("\n".join(lines))

    dry_run = common.get_bool(params, "dry_run", False)
    previous = bosses.matrix_to_mm(part.matrix_world)
    moved_objects = []
    if not dry_run:
        part.matrix_world = prospective
        common.refresh_view_layer()
        moved_objects = _apply_to_ledger(part, entries, transform)

    report = {
        "part": part.name,
        "base": base.name,
        "applied": not dry_run,
        "socket": _public(socket),
        "socket_hint_basis": hint_basis,
        "socket_search_radius_basis": base_radius_basis,
        "peg": _peg_public(peg),
        "seat": {
            "position_basis": position_basis,
            "insertion_clearance_mm": round(clearance, 6),
            "target_tip_mm": [round(value, 6) for value in target_tip],
            "roll_deg": round(math.degrees(total_roll), 6),
            "roll_auto_deg": round(math.degrees(roll_auto), 6),
            "roll_basis": roll_basis,
            "axis_turn_deg": round(math.degrees(rotation.angle), 6),
            "transform_mm": bosses.matrix_to_mm(transform),
            "part_matrix_before_mm": previous,
            "part_matrix_after_mm": bosses.matrix_to_mm(prospective),
        },
        "verification": verification,
        "ledger": {
            "records": len(entries),
            "ids": [entry.get("id") for entry in entries],
            "retained_moved": moved_objects,
            "carried": bool(moved_objects) and not dry_run,
        },
        "notes": notes,
        "honesty": (
            "Every number above is measured off the two meshes, not computed from a "
            "spec: the socket axis is a line through %d circle-fitted vertex rings "
            "(%.5f mm RMS), and the clearance checks use the bore's INSCRIBED radius "
            "%.5f mm, not the %.5f mm its vertices sit on. The seat was verified before "
            "it was applied; the shoulder gap of %s is the seam that would show in a "
            "render, and it is a measurement, not a prediction."
            % (socket["ring_count"], socket["residual_rms_mm"],
               socket["inscribed_radius_mm"], socket["radius_mm"],
               ("%.4f mm" % measured["shoulder_gap_mm"])
               if measured["shoulder_gap_mm"] is not None
               else "(not measurable: nothing of the part lies across the mouth)")),
        "next": ("nothing to check by eye: if a render still shows a gap, it is the "
                 "%s shoulder gap above, not a seating error"
                 % (("%.4f mm" % measured["shoulder_gap_mm"])
                    if measured["shoulder_gap_mm"] is not None else "unmeasurable")),
    }
    return report
