"""The Phase 6d tools: check_model / segment_model on an IMPORTED mesh.

These two are thin mirrors of two Blender socket commands, so what is pinned
here is the same pair of things ``test_rigforge`` pins: exactly what goes on the
wire, and what the report says when a canned, contract-shaped result comes back
(docs/architecture.md, "Phase 6d sketch — mesh input").

Blender is the NDJSON fake from ``test_blender_client`` on an ephemeral port —
never 9876 — and the geometry service is never contacted at all: on this path it
is the add-on that talks to ``/check_mesh`` / ``/segment_mesh``, which is why a
service refusal ("repair first") reaches us as a socket error and has to be
surfaced word for word.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import BackendError, BackendUnavailable

from .test_blender_client import FakeBlender
from .test_print_readiness import PRINTER
from .test_rigforge import router

# --- fixtures shaped like the add-on's answers ------------------------------

#: The service's own refusal for a mesh with holes, as the add-on relays it.
NOT_WATERTIGHT = (
    "mesh is not watertight (1 shell, 412 boundary edges): repair first "
    "(voxel remesh) and try again"
)


def _mesh_check(overall: str = "fail") -> dict[str, Any]:
    """A /check_mesh answer as the add-on hands it back: no B-Rep, panel rows written."""
    return {
        "object": "dragon_bust",
        "overall": overall,
        "checks": [
            {
                "name": "bed_fit",
                "status": "fail",
                "details": "312.0x180.0x240.0 mm does not fit the 256x256x256 mm bed",
                "data": {
                    "bed_mm": [256.0, 256.0, 256.0],
                    "margin_mm": 5.0,
                    "bounding_box_mm": [312.0, 180.0, 240.0],
                    "orientations": [
                        {"orientation": name, "fits": False, "fits_with_margin": False}
                        for name in ("+Z", "-Z", "+X", "-X", "+Y", "-Y")
                    ],
                    "suggested_segmentation": {
                        "kind": "planar",
                        "mode": {"planar": [120.0]},
                        "feasible": True,
                        "reason": "tall part: one horizontal cut halves the height",
                        "estimated_segment_bbox_mm": [312.0, 180.0, 120.0],
                    },
                },
            },
            {
                "name": "min_wall",
                "status": "warn",
                "details": "2 of 640 probes measured less than the 0.8 mm minimum wall",
                "data": {
                    "min_wall_thickness_mm": 0.8,
                    "min_feature_size_mm": 1.0,
                    "probe_mm": 3.2,
                    "sampled_facets": 900,
                    "measured": 640,
                    "unmeasured": 260,
                    "below_min_wall": 2,
                    "min_measured_thickness_mm": 0.704,
                    "min_measured_is_capped": False,
                    "thin_regions": [
                        {"thickness_mm": 0.704, "location_mm": [8.0, 2.5, 91.0]}
                    ],
                },
            },
            {
                "name": "overhangs",
                "status": "warn",
                "details": "unsupported facets as modelled",
                "data": {
                    "max_unsupported_overhang_deg": 50.0,
                    "current_orientation": "+Z",
                    "best_orientation": "+Z",
                    "orientations": {
                        "+Z": {
                            "unsupported_area_mm2": 1840.5,
                            "unsupported_fraction": 0.081,
                            "fits_bed": False,
                        }
                    },
                },
            },
            {
                "name": "watertight",
                "status": "pass",
                "details": "the mesh is closed",
                "data": {
                    # No B-Rep on this path, so solid validity is null, not False.
                    "watertight": True,
                    "solid_is_valid": None,
                    "mesh_is_closed": True,
                    "boundary_edges": 0,
                    "nonmanifold_edges": 0,
                },
            },
        ],
        "printer": PRINTER,
        "stats": {
            "vertex_count": 48120,
            "face_count": 96236,
            "bounding_box_mm": [312.0, 180.0, 240.0],
            "watertight": True,
        },
        "mesh": {"vertex_count": 48120, "face_count": 96236, "scale": 1000.0},
        "panel": True,
    }


def _mesh_segment(fits: bool = True) -> dict[str, Any]:
    names = [f"dragon_bust_seg_{i}" for i in range(1, 3)]
    return {
        "object": "dragon_bust",
        "mode": {"kind": "planar", "heights": [120.0]},
        "joint": {
            "type": "dovetail",
            "tolerance": 0.1,
            "tolerance_source": "printer.tolerances.press_fit",
        },
        "segments": [
            {
                "name": name,
                "kind": "segment",
                "stats": {
                    "vertex_count": 24100,
                    "face_count": 48200,
                    "watertight": True,
                },
                "oriented_bbox_mm": [312.0, 180.0, 120.0],
            }
            for name in names
        ],
        "plate": {
            "bed_mm": [256.0, 256.0, 256.0],
            "margin_mm": 5.0,
            "spacing_mm": 5.0,
            "rows": 2,
            "used_mm": [312.0, 365.0],
            "fits": fits,
            "items": [
                {
                    "name": name,
                    "position_mm": [5.0, 5.0 + 185.0 * index, 0.0],
                    "pre_rotate_deg": 0.0,
                    "rotate_deg": 90.0,
                }
                for index, name in enumerate(names)
            ],
        },
        "objects": names,
        "count": 2,
        "mesh": {"vertex_count": 48120, "face_count": 96236},
    }


def refuse(message: str):
    """A responder that answers `status: "error"` — the add-on relaying a refusal."""

    def respond(request: dict[str, Any], conn: Any) -> None:
        conn.sendall(
            json.dumps(
                {
                    "id": request.get("id"),
                    "status": "error",
                    "result": None,
                    "message": message,
                }
            ).encode("utf-8")
            + b"\n"
        )

    return respond


@pytest.fixture
def blender(monkeypatch):
    """Factory: start a fake add-on (canned results, or a raw responder) and aim at it."""
    started: list[FakeBlender] = []

    def make(results: Any, connections: int = 1) -> FakeBlender:
        responder = router(results) if isinstance(results, dict) else results
        fake = FakeBlender(responder, connections=connections)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "BLENDER_PORT", fake.port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


@pytest.fixture
def printer_file(tmp_path: Path) -> Path:
    path = tmp_path / "printer.json"
    path.write_text(json.dumps(PRINTER), encoding="utf-8")
    return path


# --- check_model: the wire --------------------------------------------------


def test_check_model_sends_the_object_and_the_printer(blender, printer_file: Path) -> None:
    fake = blender({"check_model": _mesh_check()})
    server.check_model("dragon_bust", str(printer_file))

    sent = fake.requests[0]
    assert sent["type"] == "check_model"
    assert sent["params"]["object"] == "dragon_bust"
    assert sent["params"]["printer"]["bed"]["x"] == 256.0


def test_check_model_omits_the_object_to_mean_the_active_one(
    blender, printer_file: Path
) -> None:
    fake = blender({"check_model": _mesh_check()})
    server.check_model(None, str(printer_file))
    assert "object" not in fake.requests[0]["params"]


def test_check_model_without_a_profile_sends_no_printer_key(
    blender, tmp_path: Path, monkeypatch
) -> None:
    """No profile means "use your own defaults", not an empty object to merge."""
    fake = blender({"check_model": _mesh_check()})
    monkeypatch.setattr(config, "DEFAULT_PRINTER_PATH", str(tmp_path / "nope.json"))
    report = server.check_model()
    assert "printer" not in fake.requests[0]["params"]
    assert "service defaults" in report


# --- check_model: the report ------------------------------------------------


def test_check_model_report_leads_with_the_verdict_and_names_every_check(
    blender, printer_file: Path
) -> None:
    blender({"check_model": _mesh_check()})
    report = server.check_model("dragon_bust", str(printer_file))

    assert report.splitlines()[0].startswith("Print readiness: FAIL")
    assert "'dragon_bust' (imported mesh)" in report
    assert "[FAIL] bed_fit" in report
    assert "[WARN] min_wall" in report
    assert "[WARN] overhangs" in report
    assert "[PASS] watertight" in report
    assert "Elegoo Centauri Carbon" in report
    assert str(printer_file) in report


def test_check_model_report_says_what_was_measured_and_that_it_was_scaled(
    blender, printer_file: Path
) -> None:
    blender({"check_model": _mesh_check()})
    report = server.check_model("dragon_bust", str(printer_file))

    assert "vertices 48120" in report
    assert "312 x 180 x 240 mm" in report
    assert "scaled x1000 to mm" in report
    assert "Print Checks panel" in report  # the artist sees the same rows


def test_check_model_hands_the_suggested_mode_to_segment_model(
    blender, printer_file: Path
) -> None:
    """The point of the report: the model copies this straight into the cut."""
    blender({"check_model": _mesh_check()})
    report = server.check_model("dragon_bust", str(printer_file))

    assert '{"planar": [120.0]}' in report
    assert "segment_model" in report
    assert "partforge_segment" not in report  # that one needs a script
    assert util.normalize_segment_mode({"planar": [120.0]}) == {"planar": [120.0]}


def test_check_model_report_explains_the_missing_brep(blender, printer_file: Path) -> None:
    """`solid_is_valid` is null for a raw mesh; "None" would read like a bug."""
    blender({"check_model": _mesh_check()})
    report = server.check_model("dragon_bust", str(printer_file))

    assert "B-Rep valid n/a (no B-Rep)" in report
    assert "B-Rep valid None" not in report
    assert "no B-Rep to validate" in report


def test_check_model_renders_a_thin_result_without_exploding() -> None:
    """The add-on is being written in parallel: every field but the verdict may be absent."""
    report = util.fmt_model_check_report(None, {"overall": "pass"}, "service defaults")
    assert "Print readiness: PASS" in report
    assert "the active object" in report
    assert "(the add-on returned no checks)" in report


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize("tool", ["check_model", "segment_model"])
def test_a_model_with_holes_is_refused_verbatim_with_the_repair(blender, tool: str) -> None:
    """The service's own sentence survives, and the fix is attached to it."""
    blender(refuse(NOT_WATERTIGHT))
    with pytest.raises(BackendError) as caught:
        getattr(server, tool)("dragon_bust")

    text = str(caught.value)
    assert NOT_WATERTIGHT in text, "the service's message must reach the artist as written"
    assert 'remesh(mode="voxel")' in text
    assert "Voxel Repair" in text  # the button they can press themselves


@pytest.mark.parametrize("tool", ["check_model", "segment_model"])
def test_a_down_addon_is_not_dressed_up_as_a_mesh_problem(dead_backends, tool: str) -> None:
    """"Start Blender" is the whole advice when nothing is listening."""
    with pytest.raises(BackendUnavailable) as caught:
        getattr(server, tool)("dragon_bust")

    text = str(caught.value)
    assert "Blender is not running or the Forge add-on server is stopped" in text
    assert f"127.0.0.1:{dead_backends[0]}" in text
    assert "Voxel Repair" not in text


@pytest.mark.parametrize("tool", ["check_model", "segment_model"])
def test_an_unrelated_refusal_is_passed_through_untouched(blender, tool: str) -> None:
    blender(refuse("no object named 'dragon_bust' in the scene"))
    with pytest.raises(BackendError) as caught:
        getattr(server, tool)("dragon_bust")

    text = str(caught.value)
    assert "no object named 'dragon_bust'" in text
    assert "Voxel Repair" not in text


# --- segment_model: the wire ------------------------------------------------


def test_segment_model_shapes_every_parameter_the_contract_lists(
    blender, printer_file: Path
) -> None:
    fake = blender({"segment_model": _mesh_segment()})
    server.segment_model(
        "dragon_bust",
        str(printer_file),
        joint_type="magnet",
        joint_tolerance=0.25,
        mode=4,
        collection="Pieces",
    )

    sent = fake.requests[0]
    assert sent["type"] == "segment_model"
    params = sent["params"]
    assert params["object"] == "dragon_bust"
    assert params["joint"] == {"type": "magnet", "tolerance": 0.25}
    assert params["mode"] == {"radial": 4}
    assert params["collection"] == "Pieces"
    assert params["printer"]["bed"]["x"] == 256.0


def test_segment_model_takes_check_models_suggestion_verbatim(
    blender, printer_file: Path
) -> None:
    fake = blender({"segment_model": _mesh_segment()})
    server.segment_model("dragon_bust", str(printer_file), mode={"planar": [120.0]})
    assert fake.requests[0]["params"]["mode"] == {"planar": [120.0]}


def test_segment_model_omits_what_it_was_not_given(blender, printer_file: Path) -> None:
    """No tolerance = the printer profile decides; no collection = the scene one."""
    fake = blender({"segment_model": _mesh_segment()})
    server.segment_model(None, str(printer_file), collection="   ")

    params = fake.requests[0]["params"]
    assert params["joint"] == {"type": "dovetail"}
    assert params["mode"] == "auto"
    assert "collection" not in params
    assert "object" not in params


def test_segment_model_rejects_a_negative_tolerance_before_the_socket(
    dead_backends,
) -> None:
    with pytest.raises(Exception, match="cannot be negative"):
        server.segment_model("dragon_bust", None, joint_tolerance=-1.0)


# --- segment_model: the report ----------------------------------------------


def test_segment_model_report_names_the_objects_that_landed_in_blender(
    blender, printer_file: Path
) -> None:
    blender({"segment_model": _mesh_segment()})
    report = server.segment_model(
        "dragon_bust", str(printer_file), mode={"planar": [120.0]}, collection="Pieces"
    )

    assert "Cut 'dragon_bust' into 2 piece(s)" in report
    assert "planar at Z (120) mm" in report
    assert "dovetail (tolerance 0.1 mm from printer.tolerances.press_fit)" in report
    assert "312 x 180 x 120 mm" in report
    assert "Plate: fits" in report
    assert "Loaded 2 object(s) into Blender in collection 'Pieces'" in report
    assert "dragon_bust_seg_1, dragon_bust_seg_2" in report
    assert str(printer_file) in report


def test_segment_model_warns_when_the_plate_does_not_fit(
    blender, printer_file: Path
) -> None:
    blender({"segment_model": _mesh_segment(fits=False)})
    report = server.segment_model("dragon_bust", str(printer_file))

    assert "Plate: DOES NOT FIT" in report
    assert "WARNING: the packed plate does not fit the bed" in report
    assert "more" in report  # and what to do about it


def test_segment_model_says_so_when_nothing_was_loaded() -> None:
    report = util.fmt_model_segment_report(
        "dragon_bust", {"segments": [], "count": 0}, "service defaults"
    )
    assert "NOT loaded into Blender" in report


def test_loaded_object_names_reads_both_shapes_the_addon_might_send() -> None:
    """Plain names per the contract; `load_meshes`-style records just in case."""
    assert util.loaded_object_names(["a", "b"]) == ["a", "b"]
    assert util.loaded_object_names([{"object": "a"}, {"name": "b"}]) == ["a", "b"]
    assert util.loaded_object_names(None) == []
    assert util.loaded_object_names([{"vertex_count": 3}]) == []


# --- flows ------------------------------------------------------------------


def test_the_two_mesh_commands_can_be_flow_steps() -> None:
    """Both are real socket commands, so flow_save must accept them as ops."""
    assert "check_model" in util.KNOWN_BLENDER_OPS
    assert "segment_model" in util.KNOWN_BLENDER_OPS
