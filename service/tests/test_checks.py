"""Print-readiness logic that needs no CAD kernel.

Everything here works on hand-built triangle meshes and plain dicts, so it runs
on a bare checkout the way ``test_params.py`` does.  The kernel-backed
end-to-end versions live in ``test_print_api.py``.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import pytest

from service import checks, joints, printer as printer_module, segmenting
from service.errors import ParamError, ScriptError
from service.runner import mesh_edge_report

# --------------------------------------------------------------------------
# Mesh fixtures, built by hand so the winding is known to be outward
# --------------------------------------------------------------------------


def box_mesh(
    size_x: float, size_y: float, size_z: float, origin=(0.0, 0.0, 0.0)
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]:
    """An axis-aligned box with its minimum corner at *origin*, normals outward."""
    ox, oy, oz = origin
    vertices = [
        (ox, oy, oz),
        (ox + size_x, oy, oz),
        (ox + size_x, oy + size_y, oz),
        (ox, oy + size_y, oz),
        (ox, oy, oz + size_z),
        (ox + size_x, oy, oz + size_z),
        (ox + size_x, oy + size_y, oz + size_z),
        (ox, oy + size_y, oz + size_z),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),          # bottom, -Z
        (4, 5, 6), (4, 6, 7),          # top, +Z
        (0, 1, 5), (0, 5, 4),          # -Y
        (1, 2, 6), (1, 6, 5),          # +X
        (2, 3, 7), (2, 7, 6),          # +Y
        (3, 0, 4), (3, 4, 7),          # -X
    ]
    return vertices, faces


def annulus_mesh(
    inner_radius: float, outer_radius: float, height: float, sides: int = 48
):
    """A closed prism approximation of a ring band sitting on Z=0."""
    vertices: List[Tuple[float, float, float]] = []
    for index in range(sides):
        angle = 2.0 * math.pi * index / sides
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        vertices.extend(
            [
                (outer_radius * cos_a, outer_radius * sin_a, 0.0),
                (outer_radius * cos_a, outer_radius * sin_a, height),
                (inner_radius * cos_a, inner_radius * sin_a, 0.0),
                (inner_radius * cos_a, inner_radius * sin_a, height),
            ]
        )

    faces: List[Tuple[int, int, int]] = []
    for index in range(sides):
        base = 4 * index
        nxt = 4 * ((index + 1) % sides)
        ob, ot, ib, it = base, base + 1, base + 2, base + 3
        nob, not_, nib, nit = nxt, nxt + 1, nxt + 2, nxt + 3
        faces += [
            (ob, nob, not_), (ob, not_, ot),      # outer wall, normals outward
            (ib, it, nit), (ib, nit, nib),        # inner wall, normals inward
            (it, ot, not_), (it, not_, nit),      # top annulus, +Z
            (ib, nob, ob), (ib, nib, nob),        # bottom annulus, -Z
        ]
    return vertices, faces


def stats_for(vertices: Sequence, faces: Sequence) -> dict:
    """The subset of ``/generate``'s stats the checks actually read."""
    report = mesh_edge_report(faces)
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    low = (min(xs), min(ys), min(zs))
    high = (max(xs), max(ys), max(zs))
    return {
        "bounding_box_mm": [high[i] - low[i] for i in range(3)],
        "bounding_box_min_mm": list(low),
        "bounding_box_max_mm": list(high),
        "watertight": bool(report["closed"] and report["oriented"]),
        "solid_is_valid": True,
        "mesh_is_closed": report["closed"],
        "mesh_is_oriented": report["oriented"],
        "boundary_edges": report["boundary_edges"],
        "nonmanifold_edges": report["nonmanifold_edges"],
        "degenerate_faces_dropped": 0,
    }


@pytest.fixture
def profile() -> dict:
    return printer_module.normalize_printer(None)


# --------------------------------------------------------------------------
# The hand-built meshes have to be sound, or nothing below means anything
# --------------------------------------------------------------------------


def test_the_fixture_meshes_are_closed_and_oriented():
    for vertices, faces in (box_mesh(10, 20, 30), annulus_mesh(8.0, 10.0, 5.0, 24)):
        assert stats_for(vertices, faces)["watertight"] is True


def test_annulus_volume_matches_the_analytic_prism():
    vertices, faces = annulus_mesh(8.0, 10.0, 5.0, 240)
    geometry = checks.MeshGeometry(vertices, faces)
    expected = math.pi * (10.0**2 - 8.0**2) * 5.0
    assert geometry.volume() == pytest.approx(expected, rel=0.01)


# --------------------------------------------------------------------------
# bed_fit
# --------------------------------------------------------------------------


def test_bed_fit_passes_for_a_small_part(profile):
    vertices, faces = box_mesh(50, 50, 50)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_bed_fit(geometry, stats_for(vertices, faces), profile)

    assert result["status"] == "pass"
    assert len(result["data"]["orientations"]) == 6
    assert len(result["data"]["fitting_orientations"]) == 6
    assert "suggested_segmentation" not in result["data"]


def test_bed_fit_uses_the_orientation_that_works():
    """A short printer: a 250 x 250 x 100 slab only fits lying flat."""
    profile = printer_module.normalize_printer({"bed": {"x": 256, "y": 256, "z": 120}})
    vertices, faces = box_mesh(250, 250, 100)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_bed_fit(geometry, stats_for(vertices, faces), profile)

    # It fits the bed, but not with the 5 mm edge margin, so: warn.
    assert result["status"] == "warn"
    assert set(result["data"]["fitting_orientations"]) == {"+Z", "-Z"}


def test_bed_fit_fails_and_proposes_planar_cuts_for_a_tall_block(profile):
    vertices, faces = box_mesh(100, 100, 400)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_bed_fit(geometry, stats_for(vertices, faces), profile)

    assert result["status"] == "fail"
    suggestion = result["data"]["suggested_segmentation"]
    assert suggestion["kind"] == "planar"
    assert suggestion["feasible"] is True
    assert suggestion["mode"] == {"planar": [200.0]}
    assert suggestion["metrics"]["ring_like"] is False


def test_bed_fit_proposes_radial_cuts_for_a_ring(profile):
    vertices, faces = annulus_mesh(144.0, 150.0, 20.0, 96)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_bed_fit(geometry, stats_for(vertices, faces), profile)

    assert result["status"] == "fail"
    suggestion = result["data"]["suggested_segmentation"]
    assert suggestion["kind"] == "radial"
    assert suggestion["mode"] == {"radial": 4}
    metrics = suggestion["metrics"]
    assert metrics["ring_like"] is True
    assert metrics["central_axis_crossings"] == 0
    assert metrics["outer_radius_mm"] == pytest.approx(150.0, abs=0.5)
    # Each wedge, spun to its tightest orientation, has to fit the usable bed.
    width, depth, height = suggestion["estimated_segment_bbox_mm"]
    assert max(width, depth) <= 246.0
    assert height == pytest.approx(20.0)


def test_a_solid_cylinder_is_not_ring_like(profile):
    # An annulus with no hole: the central axis is covered, so no radial cut.
    vertices, faces = annulus_mesh(0.001, 150.0, 20.0, 96)
    geometry = checks.MeshGeometry(vertices, faces)
    low, high = geometry.bounds()
    size = tuple(high[i] - low[i] for i in range(3))
    metrics = checks.ring_metrics(geometry, low, high, size)
    assert metrics["ring_like"] is False


# --------------------------------------------------------------------------
# min_wall
# --------------------------------------------------------------------------


def test_min_wall_passes_on_a_chunky_block(profile):
    """Nothing within the probe distance is a pass, not a shrug."""
    vertices, faces = box_mesh(20, 20, 20)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_min_wall(geometry, profile)
    assert result["status"] == "pass"
    assert result["data"]["below_min_wall"] == 0
    assert result["data"]["measured"] == 0
    assert result["data"]["unmeasured"] == len(faces)


def test_min_wall_fails_on_a_thin_plate_and_says_where(profile):
    vertices, faces = box_mesh(30, 30, 0.6)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_min_wall(geometry, profile)

    assert result["status"] == "fail"
    assert result["data"]["min_measured_thickness_mm"] == pytest.approx(0.6, abs=1e-3)
    regions = result["data"]["thin_regions"]
    assert regions, "a failing wall check must name the thin places"
    assert regions[0]["thickness_mm"] == pytest.approx(0.6, abs=1e-3)
    assert len(regions[0]["location_mm"]) == 3


def test_min_wall_warns_between_the_wall_and_feature_limits(profile):
    # 0.9 mm: over the 0.8 mm wall, under the 1.0 mm minimum feature.
    vertices, faces = box_mesh(30, 30, 0.9)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_min_wall(geometry, profile)
    assert result["status"] == "warn"
    assert result["data"]["below_min_wall"] == 0
    assert result["data"]["below_min_feature"] > 0


def test_min_wall_strides_a_dense_mesh(profile):
    vertices, faces = annulus_mesh(8.0, 10.0, 5.0, 200)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_min_wall(geometry, profile, max_samples=50)
    assert result["data"]["sample_stride"] > 1
    assert result["data"]["sampled_facets"] <= 51


# --------------------------------------------------------------------------
# overhangs
# --------------------------------------------------------------------------


def test_a_box_has_no_overhangs(profile):
    vertices, faces = box_mesh(20, 20, 20)
    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_overhangs(geometry, profile)
    assert result["status"] == "pass"
    for entry in result["data"]["orientations"].values():
        assert entry["unsupported_area_mm2"] == 0.0


def test_overhangs_recommend_flipping_a_ceiling_over(profile):
    """A slab held up in the air by nothing: printing it the other way up is free."""
    stem_v, stem_f = box_mesh(6, 6, 20)
    cap_v, cap_f = box_mesh(40, 40, 4, origin=(-17.0, -17.0, 20.0))
    offset = len(stem_v)
    vertices = list(stem_v) + list(cap_v)
    faces = list(stem_f) + [tuple(i + offset for i in f) for f in cap_f]

    geometry = checks.MeshGeometry(vertices, faces)
    result = checks.check_overhangs(geometry, profile)

    assert result["status"] == "warn"
    assert result["data"]["best_orientation"] == "-Z"
    orientations = result["data"]["orientations"]
    # The whole underside of the cap: these are two overlapping boxes, so the
    # stem does not punch a hole in the facet the way a real union would.
    assert orientations["+Z"]["unsupported_area_mm2"] == pytest.approx(
        40 * 40, abs=1.0
    )
    # Flipped, all that is left is the stem's own top face -- which a real
    # union would have deleted as an internal face.  The kernel-backed version
    # of this test in test_print_api.py gets a clean zero.
    assert orientations["-Z"]["unsupported_area_mm2"] == pytest.approx(6 * 6, abs=1.0)
    assert (
        orientations["-Z"]["unsupported_area_mm2"]
        < 0.05 * orientations["+Z"]["unsupported_area_mm2"]
    )
    assert (
        orientations["-Z"]["support_volume_estimate_mm3"]
        < orientations["+Z"]["support_volume_estimate_mm3"]
    )


def test_overhang_angles_use_the_from_vertical_convention(profile):
    """A 45-degree chamfer is under a 50-degree limit; a ceiling is over it."""
    vertices, faces = box_mesh(20, 20, 20)
    geometry = checks.MeshGeometry(vertices, faces)
    steep = dict(profile, max_unsupported_overhang_deg=0.0)
    # With the limit at 0, every downward facet counts -- but the box's only
    # downward facet is the one on the plate, which is excluded.
    assert checks.check_overhangs(geometry, steep)["status"] == "pass"


# --------------------------------------------------------------------------
# watertight and the overall roll-up
# --------------------------------------------------------------------------


def test_watertight_reports_an_open_mesh():
    vertices, faces = box_mesh(10, 10, 10)
    open_faces = faces[2:]  # drop the bottom
    result = checks.check_watertight(stats_for(vertices, open_faces))
    assert result["status"] == "fail"
    assert result["data"]["boundary_edges"] > 0


def test_overall_is_the_worst_of_the_four(profile):
    vertices, faces = box_mesh(30, 30, 0.6)
    report = checks.run_checks(vertices, faces, stats_for(vertices, faces), profile)
    assert report["overall"] == "fail"
    assert [entry["name"] for entry in report["checks"]] == [
        "bed_fit",
        "min_wall",
        "overhangs",
        "watertight",
    ]

    vertices, faces = box_mesh(20, 20, 20)
    good = checks.run_checks(vertices, faces, stats_for(vertices, faces), profile)
    assert good["overall"] == "pass"


# --------------------------------------------------------------------------
# Printer profiles
# --------------------------------------------------------------------------


def test_printer_defaults_mirror_the_centauri_carbon_template():
    profile = printer_module.normalize_printer(None)
    assert printer_module.bed_size(profile) == (256.0, 256.0, 256.0)
    assert profile["min_wall_thickness"] == 0.8
    assert profile["tolerances"]["press_fit"] == 0.1
    assert profile["tolerances"]["magnet_pocket_extra"] == 0.05


def test_printer_overrides_merge_rather_than_replace():
    profile = printer_module.normalize_printer(
        {"bed": {"x": 180.0, "y": 180.0, "z": 180.0}, "min_wall_thickness": 1.2}
    )
    assert printer_module.bed_size(profile) == (180.0, 180.0, 180.0)
    assert profile["min_wall_thickness"] == 1.2
    # Untouched keys survive from the default profile.
    assert profile["tolerances"]["press_fit"] == 0.1


@pytest.mark.parametrize(
    "bad",
    [
        {"bed": {"x": 0, "y": 100, "z": 100}},
        {"bed": "256x256"},
        {"min_wall_thickness": -1},
        {"max_unsupported_overhang_deg": 120},
        {"tolerances": {"press_fit": "loose"}},
    ],
)
def test_printer_rejects_nonsense(bad):
    with pytest.raises(ParamError):
        printer_module.normalize_printer(bad)


# --------------------------------------------------------------------------
# Cut modes
# --------------------------------------------------------------------------


def test_mode_auto_forms():
    assert segmenting.normalize_mode(None) == {"kind": "auto"}
    assert segmenting.normalize_mode("auto") == {"kind": "auto"}


def test_mode_radial_and_planar():
    assert segmenting.normalize_mode({"radial": 6}) == {
        "kind": "radial",
        "count": 6,
        "start_angle_deg": 0.0,
    }
    assert segmenting.normalize_mode({"planar": [30.0, 10.0]}) == {
        "kind": "planar",
        "heights": [10.0, 30.0],
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"radial": 1},
        {"radial": 1000},
        {"radial": 2.5},
        {"planar": 10.0},
        {"planar": [5.0, 5.0]},
        {"spiral": 3},
        "diagonal",
    ],
)
def test_mode_rejects_nonsense(bad):
    with pytest.raises(ParamError):
        segmenting.normalize_mode(bad)


def test_auto_mode_refuses_an_impossible_suggestion():
    with pytest.raises(ParamError, match="auto"):
        segmenting.resolve_auto_mode({"feasible": False, "reason": "too wide"})


# --------------------------------------------------------------------------
# Joint specs
# --------------------------------------------------------------------------


def test_joint_tolerance_comes_from_the_printer_profile(profile):
    for kind, expected in (("dovetail", 0.1), ("pin", 0.1), ("magnet", 0.05)):
        spec = joints.resolve_joint({"type": kind}, profile)
        assert spec["tolerance"] == expected
        assert "printer.tolerances" in spec["tolerance_source"]


def test_joint_tolerance_can_be_overridden(profile):
    spec = joints.resolve_joint({"type": "dovetail", "tolerance": 0.25}, profile)
    assert spec["tolerance"] == 0.25
    assert spec["tolerance_source"] == "request"


def test_joint_defaults_to_a_dovetail(profile):
    assert joints.resolve_joint(None, profile)["type"] == "dovetail"


def test_magnet_defaults_to_a_six_by_three_disc(profile):
    spec = joints.resolve_joint({"type": "magnet"}, profile)
    assert spec["diameter_mm"] == 6.0
    assert spec["thickness_mm"] == 3.0


@pytest.mark.parametrize(
    "bad",
    [{"type": "welded"}, {"type": "pin", "count": 0}, {"type": "pin", "diameter_mm": 0}],
)
def test_joint_rejects_nonsense(bad, profile):
    with pytest.raises(ParamError):
        joints.resolve_joint(bad, profile)


def test_dovetail_sizing_scales_with_the_cut_face(profile):
    spec = joints.resolve_joint({"type": "dovetail"}, profile)
    frame = joints.CutFrame(
        name="cut_1",
        kind="planar",
        to_world=None,
        u_min=-3.0,
        u_max=3.0,
        v_min=-25.0,
        v_max=25.0,
        side_a=0,
        side_b=1,
    )
    planned = joints.plan_joint(spec, frame)
    assert planned["slide_axis"] == "v"  # the long way across the face
    assert planned["across_axis"] == "u"
    assert planned["width_mm"] + 2 * planned["flare_mm"] < 6.0
    assert planned["flank_angle_deg"] > 0.0


def test_a_magnet_that_cannot_fit_the_face_is_an_error(profile):
    spec = joints.resolve_joint({"type": "magnet"}, profile)
    frame = joints.CutFrame(
        name="cut_1",
        kind="radial",
        to_world=None,
        u_min=0.0,
        u_max=2.0,  # a 2 mm wall cannot hold a 6 mm magnet
        v_min=0.0,
        v_max=20.0,
        side_a=0,
        side_b=1,
    )
    with pytest.raises(ParamError, match="does not fit"):
        joints.plan_joint(spec, frame)


# --------------------------------------------------------------------------
# Orientation and plate packing
# --------------------------------------------------------------------------


def test_min_area_orientation_squares_up_a_tilted_rectangle():
    angle = math.radians(37.0)
    corners = [(0.0, 0.0), (60.0, 0.0), (60.0, 10.0), (0.0, 10.0)]
    tilted = [
        (x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle))
        for x, y in corners
    ]
    found = segmenting.min_area_orientation(tilted)
    width, depth = segmenting.rotated_bounds(tilted, found)
    assert sorted((round(width, 3), round(depth, 3))) == pytest.approx([10.0, 60.0], abs=0.01)


def test_pack_plate_puts_parts_in_rows_inside_the_bed(profile):
    items = [
        {"name": f"segment_{i + 1}", "size_mm": [212.0, 48.0, 20.0]} for i in range(4)
    ]
    plate = segmenting.pack_plate(items, profile)

    assert plate["fits"] is True
    assert plate["rows"] == 4
    assert len(plate["items"]) == 4
    assert [item["name"] for item in plate["items"]] == [
        "segment_1",
        "segment_2",
        "segment_3",
        "segment_4",
    ]
    assert_placements_are_legal(plate, profile)


def test_pack_plate_lays_tall_parts_on_their_side(profile):
    plate = segmenting.pack_plate(
        [{"name": "a", "size_mm": [40.0, 200.0, 10.0]}], profile
    )
    assert plate["items"][0]["rotate_deg"] == 90.0
    assert plate["items"][0]["size_mm"][:2] == [200.0, 40.0]


def test_pack_plate_carries_the_segment_orientation_through(profile):
    plate = segmenting.pack_plate(
        [{"name": "a", "size_mm": [200.0, 40.0, 10.0], "orient_deg": 31.5}], profile
    )
    assert plate["items"][0]["pre_rotate_deg"] == 31.5


def test_pack_plate_refuses_a_part_taller_than_the_bed(profile):
    with pytest.raises(ScriptError, match="tall"):
        segmenting.pack_plate(
            [{"name": "a", "size_mm": [10.0, 10.0, 300.0]}], profile
        )


def test_pack_plate_refuses_a_part_wider_than_the_bed(profile):
    with pytest.raises(ScriptError, match="alone"):
        segmenting.pack_plate(
            [{"name": "a", "size_mm": [300.0, 10.0, 10.0]}], profile
        )


def test_pack_plate_refuses_a_plate_that_overflows(profile):
    items = [
        {"name": f"segment_{i + 1}", "size_mm": [240.0, 60.0, 20.0]} for i in range(6)
    ]
    with pytest.raises(ScriptError, match="one 256x256 mm plate"):
        segmenting.pack_plate(items, profile)


def assert_placements_are_legal(plate, profile) -> None:
    """Everything on the plate is on the bed and nothing is on top of anything."""
    bed_x, bed_y, _bed_z = printer_module.bed_size(profile)
    margin = plate["margin_mm"]
    rectangles = []
    for item in plate["items"]:
        x, y, z = item["position_mm"]
        width, depth, _height = item["size_mm"]
        assert z == 0.0
        assert x >= margin - 1e-6 and y >= margin - 1e-6
        assert x + width <= bed_x - margin + 1e-6
        assert y + depth <= bed_y - margin + 1e-6
        rectangles.append((x, y, x + width, y + depth))

    for i, a in enumerate(rectangles):
        for b in rectangles[i + 1 :]:
            overlap_x = min(a[2], b[2]) - max(a[0], b[0])
            overlap_y = min(a[3], b[3]) - max(a[1], b[1])
            assert overlap_x <= 1e-6 or overlap_y <= 1e-6, "two segments overlap"
