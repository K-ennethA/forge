"""Undercut analysis on shapes whose answer we know by hand.

Everything here is pure arithmetic on a mesh -- no build123d, no worker -- so
the shapes are built as surfaces of revolution from an ``(r, z)`` silhouette.
That gives exact control over the one thing being measured: whether the part
re-widens *away* from the parting plane, which is the only way an undercut ever
happens.

The four silhouettes, and why each one is here:

``cone``     narrows all the way up: nothing above the plane fights the pull.
``bicone``   widest in the middle: neither half fights it -- the ideal mold.
``sphere``   parted off its equator, one half has to stretch over the bulge.
``spool``    a waist between two drums: the far drum is a wall the top half
             can never get past, and that is what "severe" means.
"""

from __future__ import annotations

import math

import pytest

from service.mesh_input import signed_volume
from service.undercut import (
    MILD_ANGLE_DEG,
    SEVERITY_VERDICT,
    analyze,
    face_area_normal,
    radius_profile,
)

# --------------------------------------------------------------------------
# Meshes from silhouettes
# --------------------------------------------------------------------------


def revolve_mesh(profile, segments: int = 48):
    """Spin an ``(r, z)`` silhouette about the Z axis into a closed mesh.

    *profile* runs bottom to top and must start and end on the axis (``r == 0``)
    so the surface closes; a flat base is written as ``(0, z), (r, z)``.  The
    result is welded by construction (poles are a single vertex) and the winding
    is corrected against the signed volume, so these meshes are as watertight as
    anything ``/generate`` returns.
    """
    points = [(float(r), float(z)) for r, z in profile]
    if points[0][0] != 0.0 or points[-1][0] != 0.0:
        raise ValueError("the profile has to start and end on the axis")

    angles = [2.0 * math.pi * i / segments for i in range(segments)]
    vertices = []
    rings = []
    for radius, z in points:
        if radius == 0.0:
            rings.append([len(vertices)])
            vertices.append((0.0, 0.0, z))
            continue
        ring = []
        for angle in angles:
            ring.append(len(vertices))
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
        rings.append(ring)

    faces = []
    for index in range(len(rings) - 1):
        lower, upper = rings[index], rings[index + 1]
        if len(lower) == 1 and len(upper) == 1:
            continue
        for step in range(segments):
            nxt = (step + 1) % segments
            if len(lower) == 1:
                # Wound to match the quad case below (which is outward): the
                # pole fan is the quad with its lower edge collapsed, so the
                # corner order has to survive the collapse.
                faces.append([lower[0], upper[nxt], upper[step]])
            elif len(upper) == 1:
                faces.append([lower[step], lower[nxt], upper[0]])
            else:
                faces.append([lower[step], lower[nxt], upper[nxt]])
                faces.append([lower[step], upper[nxt], upper[step]])

    if signed_volume(vertices, faces) < 0.0:
        faces = [[tri[0], tri[2], tri[1]] for tri in faces]
    return vertices, faces


def bounds(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def analyse(profile, parting_z, segments: int = 48, **kwargs):
    vertices, faces = revolve_mesh(profile, segments)
    low, high = bounds(vertices)
    return analyze(vertices, faces, parting_z, low, high, **kwargs)


#: A plain cone standing on its base: r=10 at the bottom, an apex 20 mm up.
CONE = [(0, 0), (10, 0), (0, 20)]

#: Two cones base to base.  The widest slice is the join, and both halves
#: narrow away from it: the shape a two-piece mold was invented for.
BICONE = [(0, 0), (10, 10), (0, 20)]

#: A waist between two drums.  Whichever drum you part at, the other one is a
#: wall the mold half has to get past, and it cannot.
SPOOL = [(0, 0), (10, 0), (10, 8), (3, 12), (3, 20), (8, 24), (8, 30), (0, 30)]

#: A stem with a wide cap: parted through the stem, the cap's underside is a
#: 7 mm ledge hanging over the top half.
MUSHROOM = [(0, 0), (3, 0), (3, 10), (10, 10), (10, 14), (0, 14)]


def sphere_profile(radius: float = 12.0, bands: int = 40):
    """A ball sitting on the ground, as an ``(r, z)`` silhouette."""
    points = []
    for index in range(bands + 1):
        angle = math.pi * index / bands
        points.append((radius * math.sin(angle), radius * (1.0 - math.cos(angle))))
    points[0] = (0.0, 0.0)
    points[-1] = (0.0, 2.0 * radius)
    return points


# --------------------------------------------------------------------------
# The arithmetic underneath
# --------------------------------------------------------------------------


def test_a_triangles_area_and_normal_are_the_obvious_ones():
    area, normal = face_area_normal((0, 0, 0), (2, 0, 0), (0, 3, 0))
    assert area == pytest.approx(3.0)
    assert normal == pytest.approx((0.0, 0.0, 1.0))


def test_a_degenerate_triangle_has_no_normal_to_offer():
    area, normal = face_area_normal((0, 0, 0), (1, 1, 1), (2, 2, 2))
    assert area == 0.0
    assert normal == (0.0, 0.0, 0.0)


def test_the_radius_profile_follows_the_silhouette():
    vertices, _faces = revolve_mesh(SPOOL)
    low, high = bounds(vertices)
    centre = ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)
    profile = radius_profile(vertices, centre, low[2], high[2], bins=30)

    def at(z):
        return profile[min(29, int((z - low[2]) / (high[2] - low[2]) * 30))]

    assert at(4.0) == pytest.approx(10.0, rel=0.02)   # bottom drum
    assert at(16.0) == pytest.approx(3.0, rel=0.02)   # the waist
    assert at(27.0) == pytest.approx(8.0, rel=0.02)   # top drum


def test_an_empty_height_bin_is_filled_from_its_neighbours():
    # Two rings 10 mm apart with nothing between them: every bin in the gap has
    # to answer with something, and the something has to be between the two.
    vertices = [(5.0, 0.0, 0.0), (0.0, 5.0, 0.0), (9.0, 0.0, 10.0), (0.0, 9.0, 10.0)]
    profile = radius_profile(vertices, (0.0, 0.0), 0.0, 10.0, bins=20)
    assert profile[0] == pytest.approx(5.0)
    assert profile[-1] == pytest.approx(9.0)
    assert all(5.0 <= value <= 9.0 for value in profile)


# --------------------------------------------------------------------------
# None: nothing hangs back over either half
# --------------------------------------------------------------------------


def test_a_bicone_has_no_undercuts_at_all():
    report = analyse(BICONE, 10.0)
    assert report["severity"] == "none"
    assert report["verdict"] == "none"
    assert report["recommend_master_box"] is False
    assert report["recommendation"] is None
    for half in report["halves"].values():
        assert half["severity"] == "none"
        assert half["opposing_face_count"] == 0
        assert half["opposing_area_mm2"] == 0.0
        assert half["examples"] == []
        assert half["patch_count"] == 0


def test_a_cone_holds_nothing_back_from_the_half_it_narrows_into():
    # Parted just above the base, which is where "auto" would put it: the whole
    # cone is in the top half and every face of it slopes with the pull.
    report = analyse(CONE, 0.4)
    top = report["halves"]["mold_top"]
    assert top["severity"] == "none"
    assert top["opposing_face_count"] == 0
    assert top["draw_direction"] == [0.0, 0.0, 1.0]

    # The other half is the base disc and a 0.4 mm sliver of wall.  The wall
    # triangles run the whole height of the cone, so their centroids file them
    # under the top half and the bottom half is left with a floor that points
    # straight down -- the direction it is being pulled.  Nothing opposes.
    bottom = report["halves"]["mold_bottom"]
    assert bottom["draw_direction"] == [0.0, 0.0, -1.0]
    assert bottom["severity"] == "none"
    assert report["severity"] == "none"


def test_a_cones_flare_shows_up_once_the_mesh_is_fine_enough_to_see_it():
    # The same cone with rings up its side: now there *are* triangles below the
    # plane, and they flare 0.15 mm on the way down.  Real, tiny, and mild --
    # which is the honest report, not a rounding of it to "none".
    fine = [(0, 0), (10, 0)] + [(10 - i * 0.5, i) for i in range(1, 21)]
    report = analyse(fine, 0.4)
    bottom = report["halves"]["mold_bottom"]
    assert bottom["severity"] == "mild"
    assert 0.0 < bottom["max_depth_mm"] < 1.0
    assert report["halves"]["mold_top"]["severity"] == "none"


def test_a_sphere_parted_at_its_equator_draws_cleanly_both_ways():
    report = analyse(sphere_profile(), 12.0)
    assert report["severity"] in ("none", "mild")
    for half in report["halves"].values():
        assert half["severity"] != "severe"
        # Whatever the tessellation band at the equator contributes, it is a
        # rounding error, not a pocket.
        assert half["max_depth_mm"] < 0.5


# --------------------------------------------------------------------------
# Mild: silicone stretches over it
# --------------------------------------------------------------------------


def test_a_sphere_parted_off_its_equator_is_a_mild_band():
    # The plane sits 2.4 mm above the widest slice, so the lower half has to
    # come off past a bulge -- gently, but past it.
    report = analyse(sphere_profile(), 14.4)
    assert report["severity"] == "mild"
    assert report["verdict"] == "mild -- flexible silicone releases this"
    assert report["recommend_master_box"] is False

    assert report["halves"]["mold_top"]["severity"] == "none"
    bottom = report["halves"]["mold_bottom"]
    assert bottom["severity"] == "mild"
    assert bottom["opposing_face_count"] > 0
    assert bottom["max_angle_deg"] < MILD_ANGLE_DEG
    assert 0.0 < bottom["max_depth_mm"] < 1.0

    # Located, in millimetres, on the band between the equator and the plane.
    assert bottom["examples"], "a mild undercut still has to say where it is"
    for example in bottom["examples"]:
        x, y, z = example["position_mm"]
        assert 11.0 <= z <= 14.5
        assert math.hypot(x, y) == pytest.approx(12.0, rel=0.05)
        assert example["angle_deg"] > 0.0


def test_the_band_is_one_patch_around_the_part_not_a_hundred_specks():
    report = analyse(sphere_profile(), 14.4)
    bottom = report["halves"]["mold_bottom"]
    assert bottom["patch_count"] == 1
    assert bottom["patches"][0]["face_count"] == bottom["opposing_face_count"]
    assert bottom["severe_patch_count"] == 0


# --------------------------------------------------------------------------
# Severe: a rigid mold is not getting off this
# --------------------------------------------------------------------------


def test_a_spool_traps_the_half_that_has_to_climb_past_the_far_drum():
    # "auto" would part inside the bottom drum; the top half then has to lift
    # over the waist and the whole of the top drum.
    report = analyse(SPOOL, 4.0)
    assert report["severity"] == "severe"
    assert report["verdict"] == "severe -- a rigid mold cannot release this"
    assert report["recommend_master_box"] is True
    assert "master_box" in report["recommendation"]

    top = report["halves"]["mold_top"]
    assert top["severity"] == "severe"
    assert top["severe_patch_count"] >= 1
    assert top["max_angle_deg"] > MILD_ANGLE_DEG
    assert top["max_depth_mm"] > 3.0

    # The flare under the top drum, located: 20 to 24 mm up, out at r = 3..8.
    assert top["examples"]
    worst = top["examples"][0]
    assert 19.0 <= worst["position_mm"][2] <= 25.0
    assert worst["depth_mm"] > 3.0

    # ... and the half that only ever narrows is still clean.
    assert report["halves"]["mold_bottom"]["severity"] == "none"


def test_a_mushroom_parted_through_its_stem_is_severe():
    report = analyse(MUSHROOM, 5.0)
    top = report["halves"]["mold_top"]
    assert top["severity"] == "severe"
    # The cap's underside is a flat ledge: straight down, all the way over.
    assert top["max_angle_deg"] == pytest.approx(90.0, abs=1e-6)
    assert top["max_depth_mm"] == pytest.approx(7.0, rel=0.05)
    ledge = top["examples"][0]
    assert ledge["position_mm"][2] == pytest.approx(10.0, abs=1e-6)
    assert report["detail"]


def test_the_same_mushroom_parted_at_its_widest_slice_is_fine():
    # This is the honest limit of the whole exercise: an undercut is a property
    # of the *plane*, not of the shape, and "auto" already picks the plane that
    # avoids one wherever a shape lets it.
    report = analyse(MUSHROOM, 12.0)
    assert report["severity"] == "none"


# --------------------------------------------------------------------------
# The knobs and the honesty
# --------------------------------------------------------------------------


def test_raising_the_threshold_stops_counting_the_shallow_stuff():
    gentle = analyse(sphere_profile(), 14.4, threshold_deg=1.0)
    strict = analyse(sphere_profile(), 14.4, threshold_deg=20.0)
    assert (
        strict["halves"]["mold_bottom"]["opposing_face_count"]
        < gentle["halves"]["mold_bottom"]["opposing_face_count"]
    )


def test_the_number_of_examples_is_the_callers_to_choose():
    report = analyse(SPOOL, 4.0, examples=2)
    assert len(report["halves"]["mold_top"]["examples"]) == 2
    none = analyse(SPOOL, 4.0, examples=0)
    assert none["halves"]["mold_top"]["examples"] == []
    # Turning the examples off must not change the verdict.
    assert none["severity"] == "severe"


def test_the_report_says_out_loud_what_it_is_approximating():
    report = analyse(SPOOL, 4.0)
    criterion = report["criterion"]
    assert criterion["mild_angle_deg"] == MILD_ANGLE_DEG
    assert "approximation" in criterion
    assert "radial" in criterion["approximation"]
    assert set(SEVERITY_VERDICT) == {"none", "mild", "severe"}


def test_every_verdict_string_is_the_one_the_contract_names():
    assert SEVERITY_VERDICT["none"] == "none"
    assert SEVERITY_VERDICT["mild"] == "mild -- flexible silicone releases this"
    assert (
        SEVERITY_VERDICT["severe"] == "severe -- a rigid mold cannot release this"
    )
