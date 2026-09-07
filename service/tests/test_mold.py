"""Mold arithmetic that runs without the kernel: sections, placement, options.

Everything here is pure Python on a mesh or a dict, which is the half of
:mod:`service.mold` that is allowed to run in the HTTP process.  The geometry
itself is tested end to end in ``test_mold_api.py``.
"""

from __future__ import annotations

import math

import pytest

from service.errors import ParamError
from service.mold import (
    auto_parting_z,
    cross_section_area,
    normalize_options,
    perimeter_positions,
    resolve_vent_count,
    section_profile,
)
from service.printer import normalize_printer


def box_mesh(size=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0)):
    """A closed, outward-wound box: the smallest honest mesh to measure."""
    sx, sy, sz = size
    ox, oy, oz = origin
    vertices = [
        (ox, oy, oz),
        (ox + sx, oy, oz),
        (ox + sx, oy + sy, oz),
        (ox, oy + sy, oz),
        (ox, oy, oz + sz),
        (ox + sx, oy, oz + sz),
        (ox + sx, oy + sy, oz + sz),
        (ox, oy + sy, oz + sz),
    ]
    faces = [
        (0, 3, 2), (0, 2, 1),      # bottom, normal -Z
        (4, 5, 6), (4, 6, 7),      # top, normal +Z
        (0, 1, 5), (0, 5, 4),      # front, normal -Y
        (1, 2, 6), (1, 6, 5),      # right, normal +X
        (2, 3, 7), (2, 7, 6),      # back, normal +Y
        (3, 0, 4), (3, 4, 7),      # left, normal -X
    ]
    return vertices, faces


# --------------------------------------------------------------------------
# Cross-sections
# --------------------------------------------------------------------------


def test_a_box_reports_its_own_footprint_at_every_height():
    vertices, faces = box_mesh(size=(10.0, 4.0, 6.0))
    for z in (0.1, 3.0, 5.9):
        assert cross_section_area(vertices, faces, z) == pytest.approx(40.0, rel=1e-9)


def test_a_plane_outside_the_part_has_no_section():
    vertices, faces = box_mesh(size=(10.0, 4.0, 6.0))
    assert cross_section_area(vertices, faces, -1.0) == 0.0
    assert cross_section_area(vertices, faces, 7.0) == 0.0


def test_a_plane_grazing_the_top_face_is_not_a_section():
    """Touching is not crossing: a flat lid must not read as an area."""
    vertices, faces = box_mesh(size=(10.0, 4.0, 6.0))
    assert cross_section_area(vertices, faces, 6.0) == 0.0
    assert cross_section_area(vertices, faces, 0.0) == 0.0


def test_a_hole_is_subtracted_rather_than_counted_twice():
    """Two nested boxes, the inner one wound inside-out, is a square tube."""
    outer_v, outer_f = box_mesh(size=(10.0, 10.0, 6.0), origin=(-5.0, -5.0, 0.0))
    inner_v, inner_f = box_mesh(size=(4.0, 4.0, 6.0), origin=(-2.0, -2.0, 0.0))
    offset = len(outer_v)
    vertices = list(outer_v) + list(inner_v)
    # Reversing the winding turns the inner box's normals inwards, which is
    # what a hole through a solid actually looks like.
    faces = list(outer_f) + [
        (b + offset, a + offset, c + offset) for a, b, c in inner_f
    ]
    assert cross_section_area(vertices, faces, 3.0) == pytest.approx(100.0 - 16.0)


def test_the_profile_samples_strictly_inside_the_part():
    vertices, faces = box_mesh(size=(2.0, 2.0, 10.0))
    profile = section_profile(vertices, faces, 0.0, 10.0, samples=9)
    assert len(profile) == 9
    assert all(0.0 < z < 10.0 for z, _area in profile)
    assert all(area == pytest.approx(4.0) for _z, area in profile)


# --------------------------------------------------------------------------
# The automatic parting plane
# --------------------------------------------------------------------------


def test_a_prismatic_part_parts_in_the_middle_of_its_tie():
    """Every slice of a box is the widest, so the tie breaks toward the middle."""
    vertices, faces = box_mesh(size=(4.0, 4.0, 20.0))
    z, profile = auto_parting_z(vertices, faces, 0.0, 20.0)
    assert z == pytest.approx(10.0, abs=0.5)
    assert len(profile) > 10


def test_the_widest_slice_wins_over_the_middle():
    """A wide plate low down beats the geometric mid-height."""
    tall_v, tall_f = box_mesh(size=(4.0, 4.0, 20.0), origin=(-2.0, -2.0, 0.0))
    plate_v, plate_f = box_mesh(size=(16.0, 16.0, 2.0), origin=(-8.0, -8.0, 3.0))
    offset = len(tall_v)
    vertices = list(tall_v) + list(plate_v)
    faces = list(tall_f) + [(a + offset, b + offset, c + offset) for a, b, c in plate_f]

    z, _profile = auto_parting_z(vertices, faces, 0.0, 20.0)
    assert 3.0 < z < 5.0, "the parting plane belongs in the plate, not at z=10"


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------


def test_four_registration_keys_land_on_the_corners():
    points = perimeter_positions((-10.0, -10.0), (10.0, 10.0), 4, inset=2.0)
    assert set(points) == {(-8.0, -8.0), (8.0, -8.0), (8.0, 8.0), (-8.0, 8.0)}


def test_keys_stay_inside_the_inset_rectangle_at_any_count():
    for count in (1, 2, 3, 5, 8):
        points = perimeter_positions((0.0, 0.0), (30.0, 20.0), count, inset=3.0)
        assert len(points) == count
        for x, y in points:
            assert 3.0 - 1e-9 <= x <= 27.0 + 1e-9
            assert 3.0 - 1e-9 <= y <= 17.0 + 1e-9


def test_no_room_means_no_keys_rather_than_a_negative_rectangle():
    assert perimeter_positions((0.0, 0.0), (4.0, 4.0), 4, inset=3.0) == []


def test_auto_vents_scale_with_the_cavity_width():
    assert resolve_vent_count("auto", (20.0, 20.0)) == 1
    assert resolve_vent_count("auto", (90.0, 20.0)) == 3
    assert resolve_vent_count("auto", (400.0, 400.0)) == 4  # capped
    assert resolve_vent_count(0, (400.0, 400.0)) == 0  # an explicit count wins
    assert resolve_vent_count(6, (10.0, 10.0)) == 6


# --------------------------------------------------------------------------
# Request options
# --------------------------------------------------------------------------


@pytest.fixture
def printer():
    return normalize_printer(None)


def test_an_empty_request_resolves_to_the_documented_defaults(printer):
    options = normalize_options({}, printer)
    assert options == {
        "mode": "printed_negative",
        "parting_z_mm": "auto",
        "draft_deg": 2.0,
        "shell_mm": 4.0,
        "clearance_mm": 0.0,
        "registration_keys": 4,
        "vents": "auto",
        "spout": {},
        # Phase 12, additive: the undercut report runs on every mold, and the
        # pour-box numbers are resolved whether or not this request wants one.
        "undercut_threshold_deg": 1.0,
        "undercut_examples": 6,
        "master_box": {
            "margin_mm": 10.0,
            "wall_mm": 3.0,
            "floor_mm": 3.0,
            "pour_clearance_mm": 15.0,
            "platform_mm": 3.0,
            "funnels": 1,
            "split": False,
            "registration_keys": 4,
        },
    }


def test_spout_false_is_no_spout_and_spout_absent_is_the_default_one(printer):
    assert normalize_options({"spout": False}, printer)["spout"] is None
    assert normalize_options({"spout": None}, printer)["spout"] == {}
    resolved = normalize_options(
        {"spout": {"diameter_mm": 6.0, "position": [1, 2]}}, printer
    )["spout"]
    assert resolved["diameter_mm"] == 6.0
    assert resolved["position"] == (1.0, 2.0)


def test_the_options_that_cannot_mean_anything_are_rejected(printer):
    for body, message in (
        ({"parting_z_mm": "middle"}, "auto"),
        ({"parting_z_mm": float("nan")}, "finite"),
        ({"draft_deg": 45.0}, "between"),
        ({"draft_deg": -1.0}, "between"),
        ({"shell_mm": 0.0}, "between"),
        ({"registration_keys": 99}, "between"),
        ({"registration_keys": 2.5}, "integer"),
        ({"vents": "some"}, "auto"),
        ({"vents": -1}, "between"),
        ({"spout": {"position": [1.0]}}, "position"),
        ({"spout": "yes"}, "object or false"),
        ({"clearance_mm": 99.0}, "between"),
    ):
        with pytest.raises(ParamError, match=message):
            normalize_options(body, printer)


def test_the_shell_floor_follows_the_printer_not_a_constant():
    """A shell thinner than the printer's minimum feature cannot be printed."""
    coarse = normalize_printer({"min_feature_size": 2.0})
    with pytest.raises(ParamError, match="between 2"):
        normalize_options({"shell_mm": 1.0}, coarse)
    assert normalize_options({"shell_mm": 2.5}, coarse)["shell_mm"] == 2.5


def test_a_numeric_parting_height_survives_as_a_float(printer):
    assert normalize_options({"parting_z_mm": 7}, printer)["parting_z_mm"] == 7.0
    assert math.isclose(
        normalize_options({"parting_z_mm": 3.5}, printer)["parting_z_mm"], 3.5
    )
