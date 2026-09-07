"""The third door into a base shape: the artist draws it.

Forge already had two ways to say what a shape is — describe it in words, or show
it a picture — and both end at the same place: ``forge_lib.soft_body`` for a body
of revolution, ``forge_lib.silhouette_part`` for an appendage.  Both take a
handful of control points, and both mean it when they say *proportions, not a
trace*: five to ten points for a body, six to sixteen for an ear.

This module is the third door.  The artist draws the silhouette as an ordinary
Blender curve — front view, Bezier or poly, however they like — and these two
read-only commands turn that stroke into exactly those control points.  Nothing
is built here.  The points go back to the caller, who writes them into a PARAMS
script, and what the artist gets is **still a parametric part**: their drawing
became a set of numbers with sliders on them, not a mesh that can never change
again.  That is the whole reason this is a sampler and not a converter.

Two ideas do the work:

* **The dominant plane.**  A drawn curve is flat, but not necessarily in the
  plane the artist thinks it is.  The axis with the smallest spread is the one
  they were looking down, so the other two are the drawing plane — front (XZ),
  side (YZ) or top (XY) — and the result says which it picked and how far from
  flat the stroke actually was.
* **Curvature extrema survive the simplification.**  Reducing a thousand
  tessellated samples to seven control points by taking every hundredth one
  loses the widest point of the vase, which is the one point that makes it a
  vase.  The reduction here is the Douglas-Peucker split — repeatedly keep the
  sample furthest from the chord it is meant to be on — so the shoulder, the
  waist and the rim survive by construction, and the samples in between (which
  say nothing) are the ones that go.
"""

import math

import bpy
import mathutils

from . import common
from .registry import ForgeError, command

#: ``forge_lib.soft_body`` takes 5 to 10 ``(radius, z)`` control points.  Fewer
#: is a cone, more is tracing a photograph — its words, and this is where the
#: sampler has to land.
PROFILE_POINTS_MIN = 5
PROFILE_POINTS_MAX = 10
PROFILE_POINTS_DEFAULT = 7

#: ``forge_lib.silhouette_part`` takes 6 to 16 ``[x, y]`` points.  8-16 is the
#: useful band for a drawn outline; 6 and 7 are legal and read as a blunt shape.
OUTLINE_POINTS_MIN = 6
OUTLINE_POINTS_MAX = 16
OUTLINE_POINTS_DEFAULT = 12

#: Curve types Blender can hand us points for.
CURVE_TYPES = frozenset({"CURVE", "SURFACE"})

#: How finely a Bezier segment is walked when the spline does not say.
BEZIER_RESOLUTION = 12

#: Samples closer together than this (in millimetres) are the same sample.
SAMPLE_EPSILON_MM = 1e-4

#: Below this the drawing is a line, not a shape, in that direction.
FLAT_EPSILON_MM = 1e-3

#: A stroke is "not flat" worth mentioning when its out-of-plane spread is more
#: than this fraction of its in-plane size.
FLATNESS_NOTE_RATIO = 0.02

#: Endpoints this close together mean the artist closed the loop by hand.
CLOSE_TOLERANCE_RATIO = 0.02

#: The three drawing planes, as (name, horizontal axis, vertical axis, normal).
_PLANES = {
    1: ("XZ", 0, 2, "Y"),   # front view — the one the panel and the prompt name
    0: ("YZ", 1, 2, "X"),   # side view
    2: ("XY", 0, 1, "Z"),   # top view
}


# ---------------------------------------------------------------------------
# reading the artist's stroke
# ---------------------------------------------------------------------------

def resolve_curve(params, key="curve_object"):
    """The named curve object, or a sentence saying what to do instead."""
    name = str(params.get(key) or "").strip()
    if not name:
        raise ForgeError(
            "Name the curve to read with '%s' — the object you drew, as it is "
            "called in the list at the top right.%s" % (key, _curve_hint()))
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ForgeError("There is no object called %r in this file.%s"
                         % (name, _curve_hint()))
    if obj.type not in CURVE_TYPES:
        raise ForgeError(
            "%r is a %s, not a curve. Draw the shape with Add > Curve > Bezier "
            "(or Add > Curve > Path) and give that object's name.%s"
            % (obj.name, obj.type.lower(), _curve_hint()))
    if obj.data is None or not len(obj.data.splines):
        raise ForgeError("The curve %r has nothing drawn in it yet." % obj.name)
    return obj


def _curve_hint():
    names = [o.name for o in bpy.data.objects if o.type in CURVE_TYPES]
    if not names:
        return " There are no curve objects in this file."
    return " Curves in this file: %s." % ", ".join(sorted(names)[:10])


def _one_spline(obj):
    """The single stroke in ``obj``, or a refusal naming how many there are.

    Two strokes in one object is not a profile with a detail on it — it is two
    profiles, and picking one silently is how the artist ends up with a body
    they did not draw.
    """
    splines = obj.data.splines
    if len(splines) > 1:
        raise ForgeError(
            "The curve %r has %d separate strokes in it; a silhouette is one "
            "stroke. Enter Edit Mode (Tab), select the extra one and press X > "
            "Vertices, or draw a fresh curve with just the outline in it."
            % (obj.name, len(splines)))
    return splines[0]


def spline_samples(obj):
    """``(samples_mm, kind, notes)`` — the stroke walked densely, in world mm.

    Bezier segments are evaluated exactly (``interpolate_bezier``, the same
    De Casteljau walk Blender draws with), poly points are themselves, and NURBS
    goes through Blender's own tessellation because there is no honest way to
    guess its knot vector from Python.
    """
    spline = _one_spline(obj)
    matrix = obj.matrix_world
    notes = []
    kind = spline.type
    points = []

    if kind == "BEZIER":
        knots = list(spline.bezier_points)
        if len(knots) < 2:
            raise ForgeError(
                "The curve %r has only %d point in it. A silhouette needs at "
                "least two." % (obj.name, len(knots)))
        resolution = int(getattr(spline, "resolution_u", 0) or BEZIER_RESOLUTION)
        steps = max(resolution, 4) + 1
        pairs = list(zip(knots, knots[1:]))
        if spline.use_cyclic_u:
            pairs.append((knots[-1], knots[0]))
        for first, second in pairs:
            segment = mathutils.geometry.interpolate_bezier(
                first.co, first.handle_right, second.handle_left, second.co, steps)
            points.extend(segment[:-1])
        points.append(knots[0].co.copy() if spline.use_cyclic_u
                      else knots[-1].co.copy())
    else:
        raw = [mathutils.Vector(point.co[:3]) for point in spline.points]
        if kind == "NURBS":
            tessellated = _tessellated_points(obj)
            if tessellated:
                raw = tessellated
            else:
                notes.append(
                    "NURBS curve read from its control points — Blender would "
                    "not tessellate it, so the sampled silhouette is the "
                    "control polygon rather than the drawn curve.")
        if len(raw) < 2:
            raise ForgeError(
                "The curve %r has only %d point in it. A silhouette needs at "
                "least two." % (obj.name, len(raw)))
        points = raw
        if spline.use_cyclic_u and points[0] != points[-1]:
            points.append(points[0].copy())

    samples = []
    for point in points:
        world = matrix @ mathutils.Vector((point[0], point[1], point[2]))
        samples.append((world.x * common.M_TO_MM,
                        world.y * common.M_TO_MM,
                        world.z * common.M_TO_MM))
    return samples, kind, notes, bool(spline.use_cyclic_u)


def _tessellated_points(obj):
    """NURBS through Blender's own evaluation, or ``[]`` when it will not."""
    try:
        mesh = obj.to_mesh()
    except (RuntimeError, AttributeError):
        return []
    try:
        if mesh is None or not len(mesh.vertices):
            return []
        return [vertex.co.copy() for vertex in mesh.vertices]
    finally:
        try:
            obj.to_mesh_clear()
        except (AttributeError, RuntimeError):
            pass


# ---------------------------------------------------------------------------
# the drawing plane
# ---------------------------------------------------------------------------

def dominant_plane(samples):
    """``(plane_name, h_axis, v_axis, normal_axis, spread_mm)`` for a stroke.

    The axis the stroke spreads along least is the one the artist was looking
    down; the other two are what they drew on.  Ties go to the front view,
    because that is the one every instruction in Forge names.
    """
    extents = []
    for axis in range(3):
        values = [point[axis] for point in samples]
        extents.append(max(values) - min(values))
    order = sorted(range(3), key=lambda axis: (extents[axis], axis != 1, axis))
    normal_axis = order[0]
    name, h_axis, v_axis, normal_name = _PLANES[normal_axis]
    return name, h_axis, v_axis, normal_name, extents[normal_axis], extents


def _flatness_note(plane, spread, extents, h_axis, v_axis):
    span = max(extents[h_axis], extents[v_axis])
    if span <= 0.0 or spread <= max(FLAT_EPSILON_MM, span * FLATNESS_NOTE_RATIO):
        return None
    return ("The stroke is not flat — it wanders %.2f mm out of the %s plane, "
            "and was flattened onto it. Draw in an orthographic view (Numpad 1 "
            "for front) to keep it flat." % (spread, plane))


def _dedupe(points):
    out = []
    for point in points:
        if out and _distance(out[-1], point) <= SAMPLE_EPSILON_MM:
            continue
        out.append(point)
    return out


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# simplification — the Douglas-Peucker split, run to an exact point count
# ---------------------------------------------------------------------------

def _deviation(points, start, end):
    """``(index, distance)`` of the sample furthest off the chord start->end."""
    ax, ay = points[start]
    bx, by = points[end]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    worst_index, worst = -1, -1.0
    for index in range(start + 1, end):
        px, py = points[index]
        if length <= 1e-12:
            distance = math.hypot(px - ax, py - ay)
        else:
            distance = abs(dx * (ay - py) - (ax - px) * dy) / length
        if distance > worst:
            worst_index, worst = index, distance
    return worst_index, worst


def simplify_open(points, count):
    """Indices of ``count`` points that keep the shape's own extremes.

    Straight Douglas-Peucker with the tolerance replaced by a budget: split the
    chord with the biggest error, over and over, until the budget is spent.  The
    first split of a vase profile is its widest point; the second is the waist.
    Sampling every Nth point instead would keep neither.
    """
    if len(points) <= count:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    segments = []
    index, error = _deviation(points, 0, len(points) - 1)
    if index >= 0:
        segments.append((error, 0, len(points) - 1, index))
    while len(keep) < count and segments:
        segments.sort(key=lambda item: item[0])
        error, start, end, index = segments.pop()
        if index < 0 or error <= 0.0:
            break
        keep.add(index)
        for a, b in ((start, index), (index, end)):
            if b - a >= 2:
                worst_index, worst = _deviation(points, a, b)
                if worst_index >= 0:
                    segments.append((worst, a, b, worst_index))
    return sorted(keep)


def simplify_closed(points, count):
    """The same, for a loop: two anchors first, then two open chains."""
    if len(points) <= count:
        return list(range(len(points)))
    far, best = 0, -1.0
    for index in range(1, len(points)):
        distance = _distance(points[0], points[index])
        if distance > best:
            far, best = index, distance
    first = simplify_open(points[:far + 1], max(2, (count + 1) // 2 + 1))
    second = simplify_open(points[far:], max(2, count - len(first) + 2))
    keep = set(first) | {far + index for index in second}
    keep.discard(len(points) - 1)  # the loop's repeated first point
    return sorted(keep)


def resample_uniform(points, count):
    """``count`` points spread evenly along a polyline that is too short.

    A three-point poly curve has no curvature to preserve, and refusing it would
    only teach the artist to draw more points before they know why.  Walking the
    stroke by arc length gives the helper the count it insists on and changes
    the drawn shape by nothing at all.
    """
    spans = [_distance(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(spans)
    if total <= 0.0:
        return [points[0]] * count
    out = [points[0]]
    step = total / float(count - 1)
    index, walked = 0, 0.0
    for number in range(1, count - 1):
        target = step * number
        while index < len(spans) - 1 and walked + spans[index] < target:
            walked += spans[index]
            index += 1
        span = spans[index] or 1e-12
        ratio = min(max((target - walked) / span, 0.0), 1.0)
        ax, ay = points[index]
        bx, by = points[index + 1]
        out.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
    out.append(points[-1])
    return out


def _point_count(params, default, low, high):
    count = common.get_int(params, "points", default)
    if count < low or count > high:
        raise ForgeError(
            "'points' is %d; this sampler returns between %d and %d control "
            "points, because that is what the forge_lib helper takes. A drawn "
            "silhouette is a set of proportions the artist can nudge, not a "
            "trace of their stroke." % (count, low, high))
    return count


def _round_pairs(points, digits=3):
    return [[round(float(a), digits), round(float(b), digits)] for a, b in points]


# ---------------------------------------------------------------------------
# profile_from_curve — a drawn silhouette becomes soft_body's control points
# ---------------------------------------------------------------------------

@command("profile_from_curve")
def cmd_profile_from_curve(params):
    """Sample a drawn curve into ``(radius, z)`` points for ``soft_body``.

    params: ``curve_object`` (required), ``points?`` (5-10, default 7),
    ``close_bottom?`` (default true).

    Read-only: nothing is built, nothing in the scene changes.  The points come
    back so the caller can write them into a PARAMS script, which is what keeps
    the drawing parametric.

    ``radius`` is the distance from the world **Z axis**, so the model's centre
    line is the blue vertical line at the origin.  A stroke drawn on the left of
    it is folded onto the right; a stroke that crosses it is folded too, and
    said so in ``notes``.

    ``close_bottom`` closes the silhouette against the build plate: the lowest
    sample is dropped to ``z = 0`` and the rest ride down with it, so the body
    stands on the plate the way every other Forge part does.  False keeps the
    heights exactly as drawn.
    """
    obj = resolve_curve(params)
    count = _point_count(params, PROFILE_POINTS_DEFAULT,
                         PROFILE_POINTS_MIN, PROFILE_POINTS_MAX)
    close_bottom = common.get_bool(params, "close_bottom", True)

    samples, kind, notes, cyclic = spline_samples(obj)
    plane, h_axis, v_axis, normal, spread, extents = dominant_plane(samples)
    flat = _flatness_note(plane, spread, extents, h_axis, v_axis)
    if flat:
        notes.append(flat)

    if extents[v_axis] <= FLAT_EPSILON_MM:
        raise ForgeError(
            "The curve %r is flat in %s — it has no height, so there is nothing "
            "to revolve. A profile is the silhouette's right-hand edge, drawn "
            "going up: press Numpad 1 for the front view and draw it there."
            % (obj.name, "XYZ"[v_axis]))

    signs = [point[h_axis] for point in samples]
    if max(signs) > 0.0 and min(signs) < 0.0:
        notes.append(
            "The stroke crosses the Z axis, so it was drawn as a whole "
            "silhouette rather than one half. Radii are distances from the "
            "axis, which folds the two halves together — draw only the "
            "right-hand edge if that is not what you meant.")

    flat_points = [(abs(point[h_axis]), point[v_axis]) for point in samples]
    flat_points = _dedupe(flat_points)
    if flat_points[0][1] > flat_points[-1][1]:
        flat_points.reverse()

    climbing, dropped = [flat_points[0]], 0
    for radius, height in flat_points[1:]:
        if height > climbing[-1][1] + SAMPLE_EPSILON_MM:
            climbing.append((radius, height))
        else:
            dropped += 1
    if dropped:
        notes.append(
            "%d sample(s) doubled back downward and were dropped: a body of "
            "revolution's silhouette can only climb. If the shape really does "
            "overhang itself, that part has to be a separate piece."
            % dropped)
    if len(climbing) < 2:
        raise ForgeError(
            "The curve %r does not climb: every sample sits at the same height. "
            "Draw the silhouette from the base upward." % obj.name)

    if len(climbing) < count:
        chosen = resample_uniform(climbing, count)
        notes.append(
            "The stroke only had %d usable points, so the %d control points "
            "were spread evenly along it." % (len(climbing), count))
    else:
        chosen = [climbing[index] for index in simplify_open(climbing, count)]

    z_offset = 0.0
    if close_bottom:
        z_offset = -chosen[0][1]
        chosen = [(radius, height + z_offset) for radius, height in chosen]

    chosen = [(max(radius, 0.0), height) for radius, height in chosen]
    heights = [height for _, height in chosen]
    radii = [radius for radius, _ in chosen]
    if radii[0] < 1.0:
        notes.append(
            "The base radius is %.2f mm; soft_body will clamp it up to the "
            "printer's minimum land so the first layer is not a feather edge."
            % radii[0])

    return {
        "object": obj.name,
        "points_mm": _round_pairs(chosen),
        "height_mm": round(heights[-1] - heights[0], 3),
        "max_radius_mm": round(max(radii), 3),
        "min_radius_mm": round(min(radii), 3),
        "base_radius_mm": round(radii[0], 3),
        "plane": plane,
        "plane_normal": normal,
        "point_count": len(chosen),
        "sample_count": len(samples),
        "spline_type": kind,
        "closed_curve": cyclic,
        "close_bottom": bool(close_bottom),
        "z_offset_mm": round(z_offset, 3),
        "flatness_mm": round(spread, 4),
        "helper": "forge_lib.soft_body",
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# outline_from_curve — a drawn loop becomes silhouette_part's outline
# ---------------------------------------------------------------------------

@command("outline_from_curve")
def cmd_outline_from_curve(params):
    """Sample a **closed** drawn curve into ``[x, y]`` points for ``silhouette_part``.

    params: ``curve_object`` (required), ``points?`` (6-16, default 12; 8-16 is
    the useful band), ``recenter?`` (default true).

    Read-only.  The outline comes back in the plane's own two axes, with the
    part lying the way ``silhouette_part`` models it: outline in XY, bottom on
    ``y = 0``, centred on ``x = 0`` — which is where its optional peg attaches.
    ``recenter: false`` returns the drawn coordinates untouched.
    """
    obj = resolve_curve(params)
    count = _point_count(params, OUTLINE_POINTS_DEFAULT,
                         OUTLINE_POINTS_MIN, OUTLINE_POINTS_MAX)
    recenter = common.get_bool(params, "recenter", True)

    samples, kind, notes, cyclic = spline_samples(obj)
    plane, h_axis, v_axis, normal, spread, extents = dominant_plane(samples)
    flat = _flatness_note(plane, spread, extents, h_axis, v_axis)
    if flat:
        notes.append(flat)

    flat_points = [(point[h_axis], point[v_axis]) for point in samples]
    flat_points = _dedupe(flat_points)

    span = max(extents[h_axis], extents[v_axis])
    gap = _distance(flat_points[0], flat_points[-1])
    joined = gap <= max(FLAT_EPSILON_MM, span * CLOSE_TOLERANCE_RATIO)
    if not (cyclic or joined):
        raise ForgeError(
            "The curve %r is open — its two ends are %.1f mm apart, and an "
            "outline has to be a closed loop or there is no inside to fill. In "
            "Edit Mode select everything (A) and press Alt+C (Curve > Toggle "
            "Cyclic), then ask again." % (obj.name, gap))
    if joined and len(flat_points) > 1:
        flat_points = flat_points[:-1]

    if min(extents[h_axis], extents[v_axis]) <= FLAT_EPSILON_MM:
        raise ForgeError(
            "The curve %r is a line, not a loop: it has no width in the %s "
            "plane." % (obj.name, plane))

    if len(flat_points) < count:
        loop = flat_points + [flat_points[0]]
        chosen = resample_uniform(loop, count + 1)[:count]
        notes.append(
            "The loop only had %d usable points, so the %d outline points were "
            "spread evenly around it." % (len(flat_points), count))
    else:
        loop = flat_points + [flat_points[0]]
        chosen = [loop[index] for index in simplify_closed(loop, count)]

    if len(chosen) > count:
        chosen = chosen[:count]

    xs = [x for x, _ in chosen]
    ys = [y for _, y in chosen]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    offset = (0.0, 0.0)
    if recenter:
        offset = (-(max(xs) + min(xs)) / 2.0, -min(ys))
        chosen = [(x + offset[0], y + offset[1]) for x, y in chosen]

    crossings = _self_intersections(chosen)
    if crossings:
        notes.append(
            "The simplified outline crosses itself in %d place(s), and "
            "silhouette_part refuses an outline that does. Ask for fewer "
            "points, or redraw the loop so it does not overlap." % crossings)

    return {
        "object": obj.name,
        "points_mm": _round_pairs(chosen),
        "width_mm": round(width, 3),
        "height_mm": round(height, 3),
        "plane": plane,
        "plane_normal": normal,
        "point_count": len(chosen),
        "sample_count": len(samples),
        "spline_type": kind,
        "closed_curve": True,
        "cyclic_flag": cyclic,
        "recentered": bool(recenter),
        "offset_mm": [round(offset[0], 3), round(offset[1], 3)],
        "self_intersections": crossings,
        "flatness_mm": round(spread, 4),
        "helper": "forge_lib.silhouette_part",
        "notes": notes,
    }


def _self_intersections(points):
    """How many non-adjacent edge pairs of this closed outline cross."""
    count = len(points)
    if count < 4:
        return 0
    crossings = 0
    for i in range(count):
        a1, a2 = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            b1, b2 = points[j], points[(j + 1) % count]
            if _segments_cross(a1, a2, b1, b2):
                crossings += 1
    return crossings


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a1, a2, b1, b2):
    d1 = _orientation(b1, b2, a1)
    d2 = _orientation(b1, b2, a2)
    d3 = _orientation(a1, a2, b1)
    d4 = _orientation(a1, a2, b2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))
