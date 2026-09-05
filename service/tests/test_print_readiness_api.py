"""End-to-end tests of /check, /segment and /export_segments against real solids.

These need build123d and spawn the geometry worker, so every test that touches
geometry is skipped -- not failed -- when the kernel is missing.  The parts are
kept deliberately cheap (a ring band, a cylinder, a plate): segmenting is dozens
of OCC booleans and the suite has to stay fast enough to run on every change.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402
from service.printer import bed_size, normalize_printer  # noqa: E402

#: A ring wide enough that nothing about it fits a 256 mm bed.
OVERSIZE_RING = {"outer_diameter": 300.0, "wall_thickness": 6.0, "height": 20.0}

#: The same ring with a wall fat enough to take pins and magnets.
FAT_RING = {"outer_diameter": 300.0, "wall_thickness": 12.0, "height": 20.0}

THIN_PLATE = """
from build123d import *

PARAMS = {
    "wall": {"value": 0.5, "unit": "mm", "min": 0.1, "max": 10.0},
    "side": {"value": 30.0, "unit": "mm", "min": 5.0, "max": 200.0},
}

def build(p):
    return Pos(0, 0, p["wall"] / 2) * Box(p["side"], p["side"], p["wall"])
"""

POST = """
from build123d import *

PARAMS = {
    "diameter": {"value": 40.0, "unit": "mm", "min": 5.0, "max": 200.0},
    "height":   {"value": 60.0, "unit": "mm", "min": 5.0, "max": 200.0},
}

def build(p):
    return Pos(0, 0, p["height"] / 2) * Cylinder(p["diameter"] / 2, p["height"])
"""

#: A cap on a stem: a big flat ceiling as modelled, nothing at all upside down.
MUSHROOM = """
from build123d import *

PARAMS = {"cap": {"value": 40.0, "unit": "mm"}, "stem": {"value": 20.0, "unit": "mm"}}

def build(p):
    stem = Pos(0, 0, p["stem"] / 2) * Cylinder(3.0, p["stem"])
    cap = Pos(0, 0, p["stem"] + 2.0) * Box(p["cap"], p["cap"], 4.0)
    return stem + cap
"""

APPENDAGE_IMPORT = """
import forge_lib
from build123d import *

PARAMS = {"d": {"value": 6.0, "unit": "mm"}, "l": {"value": 8.0, "unit": "mm"}}

def build(p):
    spec = forge_lib.peg_spec(d=p["d"], l=p["l"])
    base = Pos(0, 0, 6.0) * Box(30.0, 30.0, 12.0)
    socket = Pos(0, 0, 12.0) * Rot(180, 0, 0) * forge_lib.socket_for(spec, 0.2)
    return base - socket
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def kernel(client):
    payload = client.get("/health").json()
    if payload.get("status") != "ok":
        pytest.skip(f"build123d unavailable: {payload.get('error')}")
    return payload


@pytest.fixture(scope="module")
def appendage_source(samples_dir) -> str:
    return (samples_dir / "appendage_peg.py").read_text(encoding="utf-8")


def named(checks, name):
    entry = next((c for c in checks if c["name"] == name), None)
    assert entry is not None, f"no {name} check in the response"
    return entry


# --------------------------------------------------------------------------
# /check
# --------------------------------------------------------------------------


def test_check_passes_everything_on_the_reference_ring_band(
    client, kernel, ring_band_source
):
    response = client.post("/check", json={"script": ring_band_source})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["overall"] == "pass"
    assert [c["name"] for c in body["checks"]] == [
        "bed_fit",
        "min_wall",
        "overhangs",
        "watertight",
    ]
    for entry in body["checks"]:
        assert entry["status"] == "pass", f"{entry['name']}: {entry['details']}"

    # The wall probe should read the script's own 2 mm wall back to us.
    wall = named(body["checks"], "min_wall")["data"]
    assert wall["min_measured_thickness_mm"] == pytest.approx(2.0, abs=0.05)
    assert wall["below_min_wall"] == 0

    assert body["printer"]["name"] == "Elegoo Centauri Carbon"
    assert body["stats"]["watertight"] is True


def test_check_fails_bed_fit_and_proposes_radial_cuts(client, kernel, ring_band_source):
    response = client.post(
        "/check", json={"script": ring_band_source, "overrides": OVERSIZE_RING}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["overall"] == "fail"
    bed_fit = named(body["checks"], "bed_fit")
    assert bed_fit["status"] == "fail"
    assert bed_fit["data"]["fitting_orientations"] == []

    suggestion = bed_fit["data"]["suggested_segmentation"]
    assert suggestion["kind"] == "radial"
    assert suggestion["feasible"] is True
    assert suggestion["mode"]["radial"] >= 2
    assert suggestion["metrics"]["ring_like"] is True
    assert suggestion["metrics"]["central_axis_crossings"] == 0


def test_check_fails_min_wall_on_a_thin_plate(client, kernel):
    response = client.post("/check", json={"script": THIN_PLATE})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["overall"] == "fail"
    wall = named(body["checks"], "min_wall")
    assert wall["status"] == "fail"
    assert wall["data"]["min_measured_thickness_mm"] == pytest.approx(0.5, abs=0.02)
    assert wall["data"]["below_min_wall"] > 0
    regions = wall["data"]["thin_regions"]
    assert regions and len(regions[0]["location_mm"]) == 3
    # Everything else about a thin plate is fine, which is the point of
    # reporting per check rather than one verdict.
    assert named(body["checks"], "watertight")["status"] == "pass"
    assert named(body["checks"], "bed_fit")["status"] == "pass"


def test_min_wall_respects_a_stricter_printer(client, kernel, ring_band_source):
    """The 2 mm ring wall is fine at 0.8 mm minimum and too thin at 3 mm."""
    response = client.post(
        "/check",
        json={
            "script": ring_band_source,
            "printer": {"min_wall_thickness": 3.0, "min_feature_size": 3.0},
        },
    )
    assert response.status_code == 200, response.text
    wall = named(response.json()["checks"], "min_wall")
    assert wall["status"] == "fail"
    assert wall["data"]["min_wall_thickness_mm"] == 3.0


def test_check_suggests_flipping_a_part_with_a_ceiling(client, kernel):
    response = client.post("/check", json={"script": MUSHROOM})
    assert response.status_code == 200, response.text
    overhangs = named(response.json()["checks"], "overhangs")

    assert overhangs["status"] == "warn"
    assert overhangs["data"]["best_orientation"] == "-Z"
    orientations = overhangs["data"]["orientations"]
    assert orientations["+Z"]["unsupported_area_mm2"] > 1000.0
    # A real union deletes the internal faces, so upside down is spotless.
    assert orientations["-Z"]["unsupported_area_mm2"] == 0.0
    assert orientations["-Z"]["support_volume_estimate_mm3"] == 0.0
    assert all(entry["fits_bed"] for entry in orientations.values())


def test_check_rejects_a_bad_printer_profile(client, kernel, ring_band_source):
    response = client.post(
        "/check", json={"script": ring_band_source, "printer": {"bed": {"x": -1}}}
    )
    assert response.status_code == 400
    assert "bed" in response.json()["error"]


# --------------------------------------------------------------------------
# /segment -- radial with dovetails
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auto_dovetail_ring(client, kernel, ring_band_source):
    """Auto-segment the oversize ring once; several tests read the result."""
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": OVERSIZE_RING,
            "mode": "auto",
            "joint": {"type": "dovetail"},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_auto_segmentation_follows_the_bed_fit_suggestion(auto_dovetail_ring):
    body = auto_dovetail_ring
    assert body["mode"]["kind"] == "radial"
    assert body["mode"]["count"] == body["suggestion"]["mode"]["radial"]
    assert body["suggestion"]["kind"] == "radial"

    segments = [s for s in body["segments"] if s["kind"] == "segment"]
    assert len(segments) == body["mode"]["count"]
    assert len(body["cuts"]) == body["mode"]["count"]


def test_every_segment_is_watertight(auto_dovetail_ring):
    for segment in auto_dovetail_ring["segments"]:
        stats = segment["stats"]
        assert stats["watertight"] is True, segment["name"]
        assert stats["boundary_edges"] == 0
        assert stats["nonmanifold_edges"] == 0
        assert stats["solid_is_valid"] is True


def test_every_segment_fits_the_bed(auto_dovetail_ring):
    printer = normalize_printer(auto_dovetail_ring["printer"])
    bed_x, bed_y, bed_z = bed_size(printer)
    for segment in auto_dovetail_ring["segments"]:
        width, depth, height = segment["oriented_bbox_mm"]
        assert height <= bed_z
        assert (width <= bed_x and depth <= bed_y) or (
            depth <= bed_x and width <= bed_y
        ), f"{segment['name']} is {width}x{depth} mm"


def test_the_plate_holds_every_segment_without_overlaps(auto_dovetail_ring):
    plate = auto_dovetail_ring["plate"]
    assert plate["fits"] is True
    assert len(plate["items"]) == len(auto_dovetail_ring["segments"])

    bed_x, bed_y, _ = plate["bed_mm"]
    margin = plate["margin_mm"]
    rectangles = []
    for item in plate["items"]:
        x, y, z = item["position_mm"]
        width, depth, _height = item["size_mm"]
        assert z == 0.0
        assert margin - 1e-6 <= x and x + width <= bed_x - margin + 1e-6
        assert margin - 1e-6 <= y and y + depth <= bed_y - margin + 1e-6
        rectangles.append((x, y, x + width, y + depth))

    for index, a in enumerate(rectangles):
        for b in rectangles[index + 1 :]:
            gap_x = min(a[2], b[2]) - max(a[0], b[0])
            gap_y = min(a[3], b[3]) - max(a[1], b[1])
            assert gap_x <= 1e-6 or gap_y <= 1e-6


def test_the_dovetail_is_sized_from_the_cut_face_and_the_press_fit(auto_dovetail_ring):
    joint = auto_dovetail_ring["cuts"][0]["joint"]
    assert joint["type"] == "dovetail"
    assert joint["tolerance"] == 0.1
    assert joint["tolerance_source"] == "printer.tolerances.press_fit"
    # The 6 mm wall is the narrow axis, so the tail lives across it and slides
    # along the 20 mm height.
    assert joint["across_axis"] == "u"
    assert joint["slide_axis"] == "v"
    assert joint["slide_length_mm"] == pytest.approx(20.0, abs=0.1)
    assert 0.0 < joint["width_mm"] + 2 * joint["flare_mm"] < 6.0
    assert joint["flank_angle_deg"] > 1.0


def test_dovetails_take_material_from_one_side_and_add_it_to_the_other(
    client, kernel, ring_band_source
):
    """A cut with joints must not silently be a cut without joints."""
    plain = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": OVERSIZE_RING,
            "mode": {"radial": 4},
            "joint": {"type": "none"},
            "include_mesh": True,
        },
    ).json()
    jointed = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": OVERSIZE_RING,
            "mode": {"radial": 4},
            "joint": {"type": "dovetail"},
            "include_mesh": True,
        },
    ).json()

    # The dovetailed segments carry the tail's extra faces; a plain wedge does not.
    for plain_segment, joint_segment in zip(plain["segments"], jointed["segments"]):
        assert (
            joint_segment["stats"]["face_count"] > plain_segment["stats"]["face_count"]
        ), plain_segment["name"]


def test_explicit_radial_mode_is_honoured(client, kernel, ring_band_source):
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": FAT_RING,
            "mode": {"radial": 6},
            "joint": {"type": "dovetail", "tolerance": 0.25},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == {"kind": "radial", "count": 6, "start_angle_deg": 0.0}
    assert len([s for s in body["segments"] if s["kind"] == "segment"]) == 6
    assert body["cuts"][0]["joint"]["tolerance"] == 0.25
    assert body["cuts"][0]["joint"]["tolerance_source"] == "request"
    assert all(s["stats"]["watertight"] for s in body["segments"])


# --------------------------------------------------------------------------
# /segment -- planar cuts, pins and magnets
# --------------------------------------------------------------------------


def test_a_planar_cut_with_pins_emits_the_pins_as_their_own_parts(client, kernel):
    response = client.post(
        "/segment",
        json={
            "script": POST,
            "mode": {"planar": [30.0]},
            "joint": {"type": "pin"},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    segments = [s for s in body["segments"] if s["kind"] == "segment"]
    pins = [s for s in body["segments"] if s["kind"] == "hardware"]
    assert len(segments) == 2
    assert all(s["stats"]["bounding_box_mm"][2] == pytest.approx(30.0, abs=0.1) for s in segments)

    joint = body["cuts"][0]["joint"]
    assert joint["type"] == "pin"
    assert joint["tolerance"] == 0.1
    assert len(pins) == joint["placed_count"] == joint["count"]
    assert all(name.startswith("cut_1_pin_") for name in (p["name"] for p in pins))

    # A pin is nominal; the socket it goes into is grown by the tolerance.
    assert joint["pocket_diameter_mm"] == pytest.approx(
        joint["diameter_mm"] + 2 * joint["tolerance"], abs=1e-6
    )
    # ...and it is short enough to seat before it bottoms out.
    assert joint["pin_length_mm"] == pytest.approx(
        2 * joint["socket_depth_mm"] - joint["clearance_mm"], abs=1e-6
    )
    for pin in pins:
        length = pin["stats"]["bounding_box_mm"][2]
        assert length == pytest.approx(joint["pin_length_mm"], abs=0.05)
        assert pin["stats"]["watertight"] is True

    assert all(s["stats"]["watertight"] for s in body["segments"])


def test_pin_sockets_actually_remove_material(client, kernel):
    plain = client.post(
        "/segment",
        json={"script": POST, "mode": {"planar": [30.0]}, "joint": {"type": "none"},
              "include_mesh": False},
    ).json()
    pinned = client.post(
        "/segment",
        json={"script": POST, "mode": {"planar": [30.0]}, "joint": {"type": "pin"},
              "include_mesh": False},
    ).json()

    plain_faces = [s["stats"]["face_count"] for s in plain["segments"]]
    pinned_faces = [
        s["stats"]["face_count"] for s in pinned["segments"] if s["kind"] == "segment"
    ]
    for before, after in zip(plain_faces, pinned_faces):
        assert after > before, "a socket has to show up in the mesh"


def test_magnet_pockets_use_the_magnet_tolerance_and_a_six_by_three_disc(client, kernel):
    response = client.post(
        "/segment",
        json={
            "script": POST,
            "mode": {"planar": [30.0]},
            "joint": {"type": "magnet"},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    joint = body["cuts"][0]["joint"]
    assert joint["type"] == "magnet"
    assert joint["diameter_mm"] == 6.0
    assert joint["thickness_mm"] == 3.0
    assert joint["tolerance"] == 0.05
    assert joint["tolerance_source"] == "printer.tolerances.magnet_pocket_extra"
    assert joint["pocket_diameter_mm"] == pytest.approx(6.1, abs=1e-6)
    assert joint["socket_depth_mm"] == pytest.approx(3.05, abs=1e-6)

    # Magnets are hardware you buy: nothing extra is printed.
    assert all(s["kind"] == "segment" for s in body["segments"])
    assert len(body["segments"]) == 2
    assert all(s["stats"]["watertight"] for s in body["segments"])


def test_magnets_on_a_radial_cut_of_a_fat_ring(client, kernel, ring_band_source):
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": FAT_RING,
            "mode": {"radial": 6},
            "joint": {"type": "magnet"},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["segments"]) == 6
    assert all(s["stats"]["watertight"] for s in body["segments"])
    assert body["cuts"][0]["joint"]["placed_count"] >= 1


# --------------------------------------------------------------------------
# /segment -- the errors that matter
# --------------------------------------------------------------------------


def test_too_few_radial_segments_to_fit_the_bed_is_a_clear_400(
    client, kernel, ring_band_source
):
    """Three arcs of a 300 mm ring are still 260 mm across."""
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "overrides": OVERSIZE_RING,
            "mode": {"radial": 3},
            "joint": {"type": "dovetail"},
        },
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "does not fit the usable bed" in error
    assert "segment it further" in error


def test_a_magnet_too_big_for_the_wall_is_a_clear_400(client, kernel, ring_band_source):
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,  # default: a 2 mm wall
            "mode": {"radial": 3},
            "joint": {"type": "magnet"},
        },
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "magnet pocket does not fit" in error
    assert "dovetail" in error  # the message says what to do instead


def test_a_dovetail_over_a_hole_is_a_clear_400(client, kernel, ring_band_source):
    """A planar cut through a ring gives an annular face with a hole in the middle."""
    response = client.post(
        "/segment",
        json={
            "script": ring_band_source,
            "mode": {"planar": [4.0]},
            "joint": {"type": "dovetail"},
        },
    )
    assert response.status_code == 400
    assert "hole" in response.json()["error"]


def test_a_planar_cut_outside_the_part_is_a_400(client, kernel, ring_band_source):
    response = client.post(
        "/segment",
        json={"script": ring_band_source, "mode": {"planar": [99.0]}},
    )
    assert response.status_code == 400
    assert "outside the part" in response.json()["error"]


def test_a_nonsense_mode_is_a_400(client, kernel, ring_band_source):
    response = client.post(
        "/segment", json={"script": ring_band_source, "mode": {"radial": 1}}
    )
    assert response.status_code == 400
    assert "radial" in response.json()["error"]


def test_an_unknown_joint_type_is_a_400(client, kernel, ring_band_source):
    response = client.post(
        "/segment",
        json={"script": ring_band_source, "mode": {"radial": 3}, "joint": {"type": "glue"}},
    )
    assert response.status_code == 400
    assert "joint.type" in response.json()["error"]


def test_auto_mode_on_a_part_that_already_fits_makes_one_segment(
    client, kernel, ring_band_source
):
    response = client.post(
        "/segment",
        json={"script": ring_band_source, "mode": "auto", "include_mesh": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestion"]["kind"] == "none"
    assert len(body["segments"]) == 1
    assert body["cuts"] == []
    assert body["segments"][0]["stats"]["watertight"] is True


# --------------------------------------------------------------------------
# /export_segments
# --------------------------------------------------------------------------


def test_export_segments_writes_one_stl_each_plus_one_3mf_plate(
    client, kernel, ring_band_source, tmp_path
):
    target = tmp_path / "big ring"  # a space, on purpose: Windows is the platform
    response = client.post(
        "/export_segments",
        json={
            "script": ring_band_source,
            "overrides": OVERSIZE_RING,
            "mode": "auto",
            "joint": {"type": "dovetail"},
            "directory": str(target),
            "basename": "band",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["files"]) == len(body["plate"]["items"])
    for entry in body["files"]:
        path = Path(entry["path"])
        assert path.parent == target
        assert path.suffix == ".stl"
        data = path.read_bytes()
        # Binary STL: 80-byte header, uint32 count, 50 bytes per triangle.
        (triangles,) = struct.unpack("<I", data[80:84])
        assert triangles > 0
        assert len(data) == 84 + 50 * triangles

    plate = Path(body["plate_path"])
    assert plate.name == "band_plate.3mf"
    assert plate.exists() and plate.stat().st_size > 0
    # A 3MF is a zip with a 3D model part in it, one object per segment.
    with zipfile.ZipFile(plate) as archive:
        model = next(n for n in archive.namelist() if n.endswith(".model"))
        text = archive.read(model).decode("utf-8", "replace")
    assert text.count("<object") >= len(body["files"])


def test_export_segments_rejects_a_relative_directory(client, kernel, ring_band_source):
    response = client.post(
        "/export_segments",
        json={"script": ring_band_source, "mode": {"radial": 3}, "directory": "out"},
    )
    assert response.status_code == 400
    assert "absolute" in response.json()["error"]


def test_export_segments_cannot_be_talked_out_of_its_directory(
    client, kernel, ring_band_source, tmp_path
):
    response = client.post(
        "/export_segments",
        json={
            "script": ring_band_source,
            "mode": {"radial": 3},
            "directory": str(tmp_path),
            "basename": "../../escape",
        },
    )
    assert response.status_code == 200, response.text
    for entry in response.json()["files"]:
        assert Path(entry["path"]).parent == tmp_path


# --------------------------------------------------------------------------
# forge_lib: appendage slots
# --------------------------------------------------------------------------


def test_a_script_can_import_forge_lib(client, kernel):
    response = client.post("/generate", json={"script": APPENDAGE_IMPORT})
    assert response.status_code == 200, response.text
    stats = response.json()["stats"]
    assert stats["watertight"] is True
    assert stats["bounding_box_mm"][:2] == [30.0, 30.0]


def test_the_appendage_sample_builds_both_halves(client, kernel, appendage_source):
    base = client.post(
        "/generate", json={"script": appendage_source, "overrides": {}}
    )
    assert base.status_code == 200, base.text
    assert base.json()["stats"]["watertight"] is True

    ear = client.post(
        "/generate",
        json={"script": appendage_source, "overrides": {"make_appendage": True}},
    )
    assert ear.status_code == 200, ear.text
    assert ear.json()["stats"]["watertight"] is True


def test_the_socket_swallows_the_peg_with_exactly_the_tolerance(kernel):
    """The mating promise, measured on the solids rather than on a mesh."""
    pytest.importorskip("build123d")
    from service import forge_lib

    spec = forge_lib.peg_spec(d=6.0, l=8.0)
    peg = forge_lib.peg(spec)
    socket = forge_lib.socket_for(spec, tolerance=0.2)

    # Every cubic millimetre of the peg is inside the cavity: nothing to file off.
    assert (peg & socket).volume == pytest.approx(peg.volume, rel=1e-9)

    # Measured without the lead-in, which deliberately flares past the cavity.
    plain = forge_lib.socket_for(spec, tolerance=0.2, mouth_chamfer=0.0)
    peg_box, socket_box = peg.bounding_box(), plain.bounding_box()
    assert socket_box.min.X == pytest.approx(peg_box.min.X - 0.2, abs=1e-6)
    assert socket_box.max.X == pytest.approx(peg_box.max.X + 0.2, abs=1e-6)
    assert socket_box.max.Z > peg_box.max.Z  # bored deeper than the peg is long
    assert socket.bounding_box().min.X < socket_box.min.X  # the mouth is flared

    # And it is derived from the spec, not from a constant.
    wider = forge_lib.socket_for(
        forge_lib.peg_spec(d=12.0, l=8.0), tolerance=0.2, mouth_chamfer=0.0
    )
    assert wider.bounding_box().size.Y == pytest.approx(
        socket_box.size.Y + 6.0, abs=1e-6
    )


def test_the_socket_tracks_the_peg_diameter_through_the_script(
    client, kernel, appendage_source
):
    """Change the peg once and the ear that plugs in follows."""
    sizes = {}
    for diameter in (6.0, 14.0):
        response = client.post(
            "/generate",
            json={
                "script": appendage_source,
                "overrides": {"peg_diameter": diameter, "make_appendage": True},
            },
        )
        assert response.status_code == 200, response.text
        stats = response.json()["stats"]
        assert stats["watertight"] is True
        sizes[diameter] = stats["bounding_box_mm"]

    # The 14 mm peg is wider than the 10 mm ear, so it sets the footprint.
    assert sizes[14.0][0] > sizes[6.0][0]


def test_the_appendage_sample_passes_its_own_print_checks(
    client, kernel, appendage_source
):
    response = client.post("/check", json={"script": appendage_source})
    assert response.status_code == 200, response.text
    body = response.json()
    assert named(body["checks"], "watertight")["status"] == "pass"
    assert named(body["checks"], "bed_fit")["status"] == "pass"


def test_the_sample_rejects_a_base_too_thin_for_its_peg(
    client, kernel, appendage_source
):
    response = client.post(
        "/generate",
        json={
            "script": appendage_source,
            "overrides": {"peg_length": 20.0, "base_height": 12.0},
        },
    )
    assert response.status_code == 400
    assert "clear the peg" in response.json()["error"]
