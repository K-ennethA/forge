"""End-to-end tests of /mold, /export_mold and /slice against real solids.

These spawn the geometry worker and need build123d, so the geometry tests skip
themselves when the kernel is missing.  The parts are cheap on purpose -- a ring
band, a sphere, a bicone -- because a mold is a dozen OCC booleans and the suite
has to stay fast enough to run on every change.

``/slice`` is driven through ``tests/fake_slicer.py`` rather than a real
install: the endpoint's job is to build a command line, run it hidden, and
classify the outcome, and none of that needs OrcaSlicer to be present.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi is not installed yet")
pytest.importorskip("httpx", reason="fastapi.testclient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from service.main import app  # noqa: E402
from service.mold import cross_section_area  # noqa: E402

FAKE_SLICER = str(Path(__file__).resolve().parent / "fake_slicer.py")

#: A dome on the ground: nothing planar, cylindrical or conical to draft, so the
#: fallback taper is the only thing that can carry it.
SPHERE = """
from build123d import *

PARAMS = {"radius": {"value": 12.0, "unit": "mm", "min": 2.0, "max": 60.0}}

def build(p):
    return Pos(0, 0, p["radius"]) * Sphere(p["radius"])
"""

#: Two cones base to base, deliberately lopsided: the widest slice is at z=10
#: while the mid-height is at z=8, so "auto" cannot pass by accident.
BICONE = """
from build123d import *

PARAMS = {
    "radius": {"value": 15.0, "unit": "mm", "min": 2.0, "max": 60.0},
    "lower":  {"value": 10.0, "unit": "mm", "min": 1.0, "max": 60.0},
    "upper":  {"value": 6.0,  "unit": "mm", "min": 1.0, "max": 60.0},
}

def build(p):
    lower = Pos(0, 0, p["lower"] / 2) * Cone(0.0, p["radius"], p["lower"])
    upper = Pos(0, 0, p["lower"] + p["upper"] / 2) * Cone(p["radius"], 0.0, p["upper"])
    return lower + upper
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
def ring_mold(client, kernel, ring_band_source):
    """One plain mold of the reference ring band; several tests read it."""
    response = client.post(
        "/mold", json={"script": ring_band_source, "include_mesh": False}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# /mold on the reference part
# --------------------------------------------------------------------------


def test_both_halves_come_back_watertight(ring_mold):
    assert [half["name"] for half in ring_mold["halves"]] == [
        "mold_top",
        "mold_bottom",
    ]
    for half in ring_mold["halves"]:
        stats = half["stats"]
        assert stats["watertight"] is True, half["name"]
        assert stats["boundary_edges"] == 0
        assert stats["nonmanifold_edges"] == 0
        assert stats["solid_is_valid"] is True


def test_the_halves_together_are_the_box_split_at_the_parting_plane(ring_mold):
    box = ring_mold["box"]
    parting = ring_mold["parting_z_mm"]
    assert box["shell_mm"] == 4.0
    # The ring band is 20 mm across and 8 mm tall: 4 mm of shell on each side.
    assert box["size_mm"] == [28.0, 28.0, 16.0]
    assert box["min_mm"][2] == pytest.approx(-4.0)
    assert box["max_mm"][2] == pytest.approx(12.0)

    heights = {
        half["name"]: half["stats"]["bounding_box_mm"][2] for half in ring_mold["halves"]
    }
    # The top half also carries its registration bosses, which hang the key
    # radius below the parting plane; the sockets in the bottom half are cut
    # into it and change nothing about its extents.
    boss = ring_mold["registration_keys"]["radius_mm"]
    assert heights["mold_top"] == pytest.approx(
        box["max_mm"][2] - parting + boss, abs=1e-3
    )
    assert heights["mold_bottom"] == pytest.approx(parting - box["min_mm"][2], abs=1e-3)
    for half in ring_mold["halves"]:
        assert half["stats"]["bounding_box_mm"][:2] == [28.0, 28.0]


def test_the_cavity_is_the_part_plus_a_little_draft_and_no_more(ring_mold):
    cavity = ring_mold["cavity"]
    part_volume = cavity["part_volume_mm3"]
    assert part_volume > 0
    # It must swallow the part whole...
    assert cavity["volume_mm3"] >= part_volume
    # ...and 2 degrees of draft over a 4 mm half is a few percent, not a funnel.
    assert cavity["volume_mm3"] < part_volume * 1.5
    assert cavity["clearance_mm"] == 0.0


def test_the_default_mold_gets_a_spout_vents_and_four_keys(ring_mold):
    assert ring_mold["spout"]["diameter_mm"] >= 1.0  # the printer's min feature
    assert len(ring_mold["spout"]["position_mm"]) == 2
    assert ring_mold["spout"]["top_z_mm"] > ring_mold["spout"]["bottom_z_mm"]

    vents = ring_mold["vents"]
    assert vents["count"] >= 1
    assert vents["diameter_mm"] >= 1.0
    assert len(vents["positions_mm"]) == vents["count"]

    keys = ring_mold["registration_keys"]
    assert keys["count"] == 4
    assert keys["male_half"] == "mold_top"
    assert keys["tolerance_mm"] == 0.1  # printer.tolerances.press_fit


def test_the_registration_keys_move_material_from_one_half_to_the_other(
    client, kernel, ring_band_source
):
    """Isolated from the spout and vents, which also cut into the top half."""
    response = client.post(
        "/mold",
        json={
            "script": ring_band_source,
            "spout": False,
            "vents": 0,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    plain = body["cavity"]["plain_half_volume_mm3"]
    volumes = {half["name"]: half["volume_mm3"] for half in body["halves"]}
    keys = body["registration_keys"]

    # A boss on the top half is material the bottom half no longer has.
    assert volumes["mold_top"] > plain["mold_top"]
    assert volumes["mold_bottom"] < plain["mold_bottom"]

    # Four hemispheres of the nominal radius added, four of the grown radius
    # removed, so the sockets take slightly more than the bosses give.
    boss = 4.0 / 3.0 * math.pi * keys["radius_mm"] ** 3 / 2.0
    socket = 4.0 / 3.0 * math.pi * (keys["radius_mm"] + keys["tolerance_mm"]) ** 3 / 2.0
    assert volumes["mold_top"] - plain["mold_top"] == pytest.approx(
        keys["count"] * boss, rel=0.02
    )
    assert plain["mold_bottom"] - volumes["mold_bottom"] == pytest.approx(
        keys["count"] * socket, rel=0.02
    )


def test_no_keys_leaves_the_two_halves_plain(client, kernel, ring_band_source):
    response = client.post(
        "/mold",
        json={
            "script": ring_band_source,
            "registration_keys": 0,
            "spout": False,
            "vents": 0,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["registration_keys"]["count"] == 0
    plain = body["cavity"]["plain_half_volume_mm3"]
    for half in body["halves"]:
        assert half["volume_mm3"] == pytest.approx(plain[half["name"]], rel=1e-9)
        assert half["stats"]["watertight"] is True


def test_spout_false_omits_the_spout_and_leaves_more_material(
    client, kernel, ring_band_source, ring_mold
):
    response = client.post(
        "/mold",
        json={"script": ring_band_source, "spout": False, "include_mesh": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["spout"] is None
    with_spout = {h["name"]: h["volume_mm3"] for h in ring_mold["halves"]}
    without = {h["name"]: h["volume_mm3"] for h in body["halves"]}
    assert without["mold_top"] > with_spout["mold_top"]
    # The spout only ever cuts the top half.
    assert without["mold_bottom"] == pytest.approx(with_spout["mold_bottom"], rel=1e-9)
    assert all(h["stats"]["watertight"] for h in body["halves"])


def test_the_ring_bands_draft_uses_the_real_occ_draft(ring_mold):
    """Cylindrical walls are exactly what BRepOffsetAPI_DraftAngle handles."""
    assert ring_mold["draft"]["angle_deg"] == 2.0
    assert ring_mold["draft"]["mold_top"] == "occ_draft"
    assert ring_mold["draft"]["mold_bottom"] == "occ_draft"


def test_zero_draft_makes_the_cavity_exactly_the_part(
    client, kernel, ring_band_source
):
    response = client.post(
        "/mold",
        json={"script": ring_band_source, "draft_deg": 0, "include_mesh": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"]["mold_top"] == "none"
    assert body["cavity"]["volume_mm3"] == pytest.approx(
        body["cavity"]["part_volume_mm3"], rel=1e-6
    )


def test_include_mesh_returns_triangles_a_viewer_can_show(
    client, kernel, ring_band_source
):
    response = client.post("/mold", json={"script": ring_band_source})
    assert response.status_code == 200, response.text
    for half in response.json()["halves"]:
        mesh = half["mesh"]
        assert len(mesh["vertices"]) == half["stats"]["vertex_count"]
        assert len(mesh["faces"]) == half["stats"]["face_count"]
        assert len(mesh["vertices"][0]) == 3


# --------------------------------------------------------------------------
# The parting plane
# --------------------------------------------------------------------------


def test_auto_parting_lands_on_the_widest_slice_not_the_mid_height(client, kernel):
    """The bicone's waist is at z=10; its mid-height is at z=8."""
    response = client.post("/mold", json={"script": BICONE, "include_mesh": False})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["parting_source"] == "auto"
    assert body["parting_z_mm"] == pytest.approx(10.0, abs=0.4)

    # ...and the profile it was chosen from agrees: nothing is wider.
    widest = max(area for _z, area in body["parting_profile"])
    at_parting = min(
        body["parting_profile"], key=lambda entry: abs(entry[0] - body["parting_z_mm"])
    )
    assert at_parting[1] >= widest * 0.999
    assert all(half["stats"]["watertight"] for half in body["halves"])


def test_the_section_arithmetic_agrees_with_the_ring_it_measured(
    client, kernel, ring_band_source
):
    """The same function that picks the parting plane, checked against geometry."""
    response = client.post("/generate", json={"script": ring_band_source})
    assert response.status_code == 200, response.text
    mesh = response.json()["mesh"]

    # Default ring band: 20 mm outer diameter, 2 mm wall, sampled clear of the
    # 0.5 mm chamfers at either end.
    expected = math.pi * (10.0**2 - 8.0**2)
    measured = cross_section_area(mesh["vertices"], mesh["faces"], 4.0)
    assert measured == pytest.approx(expected, rel=0.01)


def test_an_explicit_parting_height_is_honoured(client, kernel, ring_band_source):
    response = client.post(
        "/mold",
        json={
            "script": ring_band_source,
            "parting_z_mm": 2.0,
            "registration_keys": 0,
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["parting_source"] == "request"
    assert body["parting_z_mm"] == pytest.approx(2.0)
    heights = {h["name"]: h["stats"]["bounding_box_mm"][2] for h in body["halves"]}
    assert heights["mold_bottom"] == pytest.approx(6.0, abs=1e-3)  # 2 + 4 shell
    assert heights["mold_top"] == pytest.approx(10.0, abs=1e-3)  # 8 - 2 + 4 shell


def test_a_parting_plane_off_the_end_of_the_part_is_a_400(
    client, kernel, ring_band_source
):
    response = client.post(
        "/mold", json={"script": ring_band_source, "parting_z_mm": 40.0}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "parting_z_mm" in error
    assert "the part spans z=0.000 to 8.000 mm" in error


# --------------------------------------------------------------------------
# A curved part: the draft fallback
# --------------------------------------------------------------------------


def test_a_sphere_falls_back_to_the_taper_and_still_verifies(client, kernel):
    response = client.post("/mold", json={"script": SPHERE, "include_mesh": False})
    assert response.status_code == 200, response.text
    body = response.json()

    # OCC only drafts planar / cylindrical / conical faces, so a dome takes the
    # documented fallback -- and the fallback still has to produce a mold.
    assert body["draft"]["mold_top"] == "taper_union"
    assert body["draft"]["mold_bottom"] == "taper_union"
    for half in body["halves"]:
        assert half["stats"]["watertight"] is True, half["name"]

    # The equator is the widest slice, and it is where the mold opens.
    assert body["parting_z_mm"] == pytest.approx(12.0, abs=0.6)
    # Even the fallback taper has to swallow the part whole.
    assert body["cavity"]["volume_mm3"] >= body["cavity"]["part_volume_mm3"]


# --------------------------------------------------------------------------
# The errors that matter
# --------------------------------------------------------------------------


def test_a_mold_that_cannot_be_printed_says_to_segment_first(
    client, kernel, ring_band_source
):
    """A 260 mm ring plus a 4 mm shell is wider than the bed, box and all."""
    response = client.post(
        "/mold",
        json={
            "script": ring_band_source,
            "overrides": {
                "outer_diameter": 260.0,
                "wall_thickness": 6.0,
                "height": 20.0,
            },
        },
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "over the usable bed" in error
    assert "/segment" in error


def test_a_bigger_bed_takes_the_same_mold(client, kernel, ring_band_source):
    """The bed check is the printer's, not a constant."""
    response = client.post(
        "/mold",
        json={
            "script": ring_band_source,
            "overrides": {"outer_diameter": 260.0, "wall_thickness": 6.0, "height": 20.0},
            "printer": {"bed": {"x": 400, "y": 400, "z": 400}},
            "include_mesh": False,
        },
    )
    assert response.status_code == 200, response.text
    assert all(h["stats"]["watertight"] for h in response.json()["halves"])


def test_a_spout_thinner_than_the_nozzle_can_print_is_a_400(
    client, kernel, ring_band_source
):
    response = client.post(
        "/mold",
        json={"script": ring_band_source, "spout": {"diameter_mm": 0.4}},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "minimum feature size" in error


def test_a_spout_that_misses_the_part_is_a_400(client, kernel, ring_band_source):
    response = client.post(
        "/mold",
        json={"script": ring_band_source, "spout": {"position": [40.0, 40.0]}},
    )
    assert response.status_code == 400
    assert "does not reach the cavity" in response.json()["error"]


def test_a_shell_too_thin_for_its_keys_is_a_400(client, kernel, ring_band_source):
    response = client.post(
        "/mold",
        json={"script": ring_band_source, "shell_mm": 1.2, "registration_keys": 4},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "registration_keys" in error


def test_a_nonsense_option_is_a_400_naming_it(client, kernel, ring_band_source):
    response = client.post(
        "/mold", json={"script": ring_band_source, "draft_deg": 60.0}
    )
    assert response.status_code == 400
    assert "draft_deg" in response.json()["error"]


# --------------------------------------------------------------------------
# /export_mold
# --------------------------------------------------------------------------


def test_export_mold_writes_one_file_per_half(
    client, kernel, ring_band_source, tmp_path
):
    target = tmp_path / "band mold"  # a space, on purpose: Windows is the platform
    response = client.post(
        "/export_mold",
        json={
            "script": ring_band_source,
            "directory": str(target),
            "basename": "band",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert [entry["name"] for entry in body["files"]] == ["mold_top", "mold_bottom"]
    for entry in body["files"]:
        path = Path(entry["path"])
        assert path.parent == target
        assert path.name == f"band_{entry['name']}.stl"
        data = path.read_bytes()
        # Binary STL: 80-byte header, uint32 count, 50 bytes per triangle.
        (triangles,) = struct.unpack("<I", data[80:84])
        assert triangles > 0
        assert len(data) == 84 + 50 * triangles
        assert entry["stats"]["watertight"] is True

    # The report is the same one /mold gives, so a caller need not ask twice.
    assert body["parting_z_mm"] == pytest.approx(4.0, abs=0.5)
    assert body["registration_keys"]["count"] == 4
    assert "export_ms" in body["timings"]


def test_export_mold_can_write_step_and_rejects_a_relative_directory(
    client, kernel, ring_band_source, tmp_path
):
    response = client.post(
        "/export_mold",
        json={
            "script": ring_band_source,
            "directory": str(tmp_path),
            "format": "step",
        },
    )
    assert response.status_code == 200, response.text
    for entry in response.json()["files"]:
        assert Path(entry["path"]).suffix == ".step"
        assert Path(entry["path"]).stat().st_size > 0

    relative = client.post(
        "/export_mold", json={"script": ring_band_source, "directory": "out"}
    )
    assert relative.status_code == 400
    assert "absolute" in relative.json()["error"]


# --------------------------------------------------------------------------
# /slice
# --------------------------------------------------------------------------


def test_health_reports_whether_there_is_a_slicer_to_call(client):
    payload = client.get("/health").json()
    assert "slicer" in payload
    assert isinstance(payload["slicer"]["found"], bool)
    if not payload["slicer"]["found"]:
        assert payload["slicer"]["probed"]
        assert "configure" in payload["slicer"]


def test_slice_runs_the_exported_file_through_the_slicer(
    client, kernel, ring_band_source, tmp_path
):
    """The whole pipeline: build, export, slice, one file at the end of it."""
    model = tmp_path / "band.stl"
    exported = client.post(
        "/export",
        json={"script": ring_band_source, "format": "stl", "path": str(model)},
    )
    assert exported.status_code == 200, exported.text

    gcode = tmp_path / "out" / "band.gcode"
    response = client.post(
        "/slice",
        json={
            "input": str(model),
            "output": str(gcode),
            "slicer_path": FAKE_SLICER,
            "timeout_s": 60,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"] == str(gcode)
    assert gcode.is_file()
    assert body["duration_ms"] > 0
    assert "fake slicer" in body["stdout_tail"]


def test_slice_reports_the_slicers_own_complaint_as_a_400(
    client, kernel, ring_band_source, tmp_path
):
    model = tmp_path / "band.stl"
    client.post(
        "/export",
        json={"script": ring_band_source, "format": "stl", "path": str(model)},
    )

    response = client.post(
        "/slice",
        json={
            "input": str(model),
            "output": str(tmp_path / "band.gcode"),
            "slicer_path": FAKE_SLICER,
            "extra_args": ["--forge-test-mode", "fail"],
            "timeout_s": 60,
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert "outside the print area" in body["error"]
    assert body["slicer"]["returncode"] == 13
    assert "found error" in body["slicer"]["stderr_tail"]


def test_slice_with_nothing_installed_explains_how_to_configure_it(
    client, monkeypatch, tmp_path
):
    monkeypatch.delenv("FORGE_SLICER", raising=False)
    monkeypatch.setattr("service.slicer.INSTALL_CANDIDATES", ())
    monkeypatch.setattr("service.slicer.PATH_NAMES", ())

    model = tmp_path / "band.stl"
    model.write_bytes(b"\0" * 84)

    response = client.post(
        "/slice", json={"input": str(model), "output": str(tmp_path / "band.gcode")}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["slicer"]["found"] is False
    assert "FORGE_SLICER" in body["error"]
    assert "traceback" in body  # still the contract's error shape


def test_slice_rejects_an_output_it_cannot_produce(client, tmp_path):
    model = tmp_path / "band.stl"
    model.write_bytes(b"\0" * 84)
    response = client.post(
        "/slice",
        json={
            "input": str(model),
            "output": str(tmp_path / "band.stl"),
            "slicer_path": FAKE_SLICER,
        },
    )
    assert response.status_code == 400
    assert "does not produce" in response.json()["error"]
