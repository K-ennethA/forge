"""The Phase 2 tools: request shaping and report formatting.

Both backends are faked. The geometry service is a stdlib ``http.server`` on an
OS-assigned ephemeral port (never 8765) that records what it was sent and answers
with payloads shaped like the real service's; Blender is the NDJSON fake from
``test_blender_client``. Nothing here needs build123d, Blender, or the real ports.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server
from forge_mcp.errors import ForgeError
from forge_mcp.util import normalize_segment_mode

from .conftest import REAL_BACKEND_PORTS
from .test_blender_client import FakeBlender

# --- fixtures shaped like the real service's answers ------------------------

PRINTER = {
    "name": "Elegoo Centauri Carbon",
    "bed": {"x": 256.0, "y": 256.0, "z": 256.0},
    "min_wall_thickness": 0.8,
    "min_feature_size": 1.0,
    "max_unsupported_overhang_deg": 50.0,
    "tolerances": {"press_fit": 0.1, "magnet_pocket_extra": 0.05},
}


def _orientation(name: str, fits: bool) -> dict[str, Any]:
    return {
        "orientation": name,
        "footprint_mm": [300.0, 300.0],
        "height_mm": 40.0,
        "fits": fits,
        "fits_with_margin": fits,
    }


CHECK_FAIL = {
    "overall": "fail",
    "checks": [
        {
            "name": "bed_fit",
            "status": "fail",
            "details": "300.0x300.0x40.0 mm does not fit the 256x256x256 mm bed",
            "data": {
                "bed_mm": [256.0, 256.0, 256.0],
                "margin_mm": 5.0,
                "bounding_box_mm": [300.0, 300.0, 40.0],
                "orientations": [
                    _orientation(n, False)
                    for n in ("+Z", "-Z", "+X", "-X", "+Y", "-Y")
                ],
                "fitting_orientations": [],
                "suggested_segmentation": {
                    "kind": "radial",
                    "mode": {"radial": 4},
                    "feasible": True,
                    "reason": "ring-like part: cut into 4 equal arcs about Z",
                    "estimated_segment_bbox_mm": [48.177, 212.132, 40.0],
                },
            },
        },
        {
            "name": "min_wall",
            "status": "fail",
            "details": "3 of 700 probes measured less than the 0.8 mm minimum wall",
            "data": {
                "min_wall_thickness_mm": 0.8,
                "min_feature_size_mm": 1.0,
                "probe_mm": 3.2,
                "sampled_facets": 1008,
                "measured": 700,
                "unmeasured": 308,
                "below_min_wall": 3,
                "min_measured_thickness_mm": 0.612,
                "min_measured_is_capped": False,
                "thin_regions": [
                    {"thickness_mm": 0.612, "location_mm": [12.25, 4.5, 0.8], "facet": 17}
                ],
            },
        },
        {
            "name": "overhangs",
            "status": "warn",
            "details": "unsupported facets as modelled",
            "data": {
                "max_unsupported_overhang_deg": 50.0,
                "current_orientation": "+X",
                "best_orientation": "+Z",
                "orientations": {
                    "+Z": {
                        "unsupported_area_mm2": 0.0,
                        "unsupported_fraction": 0.0,
                        "fits_bed": True,
                    },
                    "+X": {
                        "unsupported_area_mm2": 175.856,
                        "unsupported_fraction": 0.16527,
                        "fits_bed": True,
                    },
                },
            },
        },
        {
            "name": "watertight",
            "status": "pass",
            "details": "the B-Rep is valid and the exported mesh is closed",
            "data": {
                "watertight": True,
                "solid_is_valid": True,
                "mesh_is_closed": True,
                "boundary_edges": 0,
                "nonmanifold_edges": 0,
            },
        },
    ],
    "printer": PRINTER,
    "params": {},
    "stats": {"vertex_count": 1000, "face_count": 2000, "bounding_box_mm": [300, 300, 40]},
    "timings": {"check_ms": 900},
}


def _segment(index: int, with_mesh: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": f"segment_{index}",
        "kind": "segment",
        "stats": {
            "vertex_count": 152,
            "face_count": 300,
            "bounding_box_mm": [151.8, 150.0, 40.0],
            "watertight": True,
        },
        "orient_deg": 44.858179 + 90.0 * (index - 1),
        "oriented_bbox_mm": [212.6455, 48.4285, 40.0],
    }
    if with_mesh:
        entry["mesh"] = {
            "vertices": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]],
            "faces": [[0, 1, 2]],
        }
    return entry


def _plate_item(index: int) -> dict[str, Any]:
    return {
        "name": f"segment_{index}",
        "index": index - 1,
        "pre_rotate_deg": 44.858179 + 90.0 * (index - 1),
        "rotate_deg": 0.0,
        "position_mm": [5.0, 5.0 + 53.4285 * (index - 1), 0.0],
        "size_mm": [212.6455, 48.4285, 40.0],
    }


def segment_payload(with_mesh: bool) -> dict[str, Any]:
    return {
        "mode": {"kind": "radial", "count": 4, "start_angle_deg": 0.0},
        "joint": {
            "type": "dovetail",
            "tolerance": 0.1,
            "tolerance_source": "printer.tolerances.press_fit",
        },
        "cuts": [{"name": f"cut_{i}", "kind": "radial"} for i in range(1, 5)],
        "segments": [_segment(i, with_mesh) for i in range(1, 5)],
        "plate": {
            "bed_mm": [256.0, 256.0, 256.0],
            "margin_mm": 5.0,
            "spacing_mm": 5.0,
            "rows": 4,
            "used_mm": [212.645, 208.714],
            "fits": True,
            "items": [_plate_item(i) for i in range(1, 5)],
        },
        "printer": PRINTER,
        "params": {},
        "stats": {},
    }


def export_payload(directory: Path) -> dict[str, Any]:
    files = []
    for index in range(1, 5):
        path = directory / f"ring_segment_{index}.stl"
        path.write_bytes(b"x" * (1024 * index))
        files.append(
            {
                "name": f"segment_{index}",
                "kind": "segment",
                "format": "stl",
                "path": str(path),
                "orient_deg": 44.86,
                "stats": {"vertex_count": 152, "face_count": 300},
            }
        )
    plate = directory / "ring_plate.3mf"
    plate.write_bytes(b"y" * 2048)
    return {
        "directory": str(directory),
        "files": files,
        "plate": {"bed_mm": [256, 256, 256], "fits": True, "items": []},
        "plate_path": str(plate),
        "mode": {"kind": "radial", "count": 4, "start_angle_deg": 0.0},
        "joint": {"type": "pin", "tolerance": 0.1},
        "cuts": [],
        "printer": PRINTER,
        "timings": {"export_ms": 120},
    }


# --- the fake geometry service ----------------------------------------------


class FakeService:
    """HTTP server on an ephemeral port that records requests and replays canned bodies."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, Any]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    body = {"__raw__": raw.decode("utf-8", "replace")}
                outer.requests.append((self.path, body))

                payload = outer.routes.get(self.path)
                if payload is None:
                    self._send(404, {"error": f"no route {self.path}"})
                    return
                if callable(payload):
                    payload = payload(body)
                self._send(200, payload)

            def _send(self, status: int, payload: Any) -> None:
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        assert self.port not in REAL_BACKEND_PORTS
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeService":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()

    def body_for(self, path: str) -> dict[str, Any]:
        for seen, body in self.requests:
            if seen == path:
                return body
        raise AssertionError(f"{path} was never called; saw {[p for p, _ in self.requests]}")


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "ring_band.py"
    path.write_text(
        "PARAMS = {'d': {'value': 300.0, 'unit': 'mm'}}\n\ndef build(p):\n    return None\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def printer_file(tmp_path: Path) -> Path:
    path = tmp_path / "printer.json"
    path.write_text(json.dumps(PRINTER), encoding="utf-8")
    return path


@pytest.fixture
def service(monkeypatch):
    """Factory: start a FakeService for these routes and point the client at it.

    The redirection happens when the factory is *called*, inside the test body,
    which is what lets a test take `dead_backends` as well — Blender stays on a
    closed port while the geometry service answers.
    """
    started: list[FakeService] = []

    def make(routes: dict[str, Any]) -> FakeService:
        fake = FakeService(routes)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "SERVICE_URL", f"http://127.0.0.1:{fake.port}")
        monkeypatch.setattr(config, "SERVICE_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "SERVICE_READ_TIMEOUT", 10.0)
        monkeypatch.setattr(config, "SERVICE_CHECK_TIMEOUT", 10.0)
        monkeypatch.setattr(config, "SERVICE_SEGMENT_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


# --- mode normalization (pure) ----------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, "auto"),
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("", "auto"),
        (4, {"radial": 4}),
        ("6", {"radial": 6}),
        (4.0, {"radial": 4}),
        ({"radial": 4}, {"radial": 4}),
        ({"radial": 6, "start_angle_deg": 45.0}, {"radial": 6, "start_angle_deg": 45.0}),
        ([30, 60], {"planar": [30.0, 60.0]}),
        ("30, 60", {"planar": [30.0, 60.0]}),
        ("[30, 60]", {"planar": [30.0, 60.0]}),
        ({"planar": [30]}, {"planar": [30.0]}),
        ('{"radial": 4}', {"radial": 4}),
    ],
)
def test_mode_normalization(given: Any, expected: Any) -> None:
    assert normalize_segment_mode(given) == expected


@pytest.mark.parametrize("given", [1, 0, -3, True, {"nope": 1}, "banana", 2.5, []])
def test_bad_modes_are_rejected_with_a_readable_message(given: Any) -> None:
    with pytest.raises(ForgeError):
        normalize_segment_mode(given)


# --- partforge_check --------------------------------------------------------


def test_check_sends_script_overrides_and_printer(service, script: Path, printer_file: Path) -> None:
    fake = service({"/check": CHECK_FAIL})
    server.partforge_check(str(script), {"d": 300.0}, str(printer_file))

    body = fake.body_for("/check")
    assert body["script"] == script.read_text(encoding="utf-8")
    assert body["overrides"] == {"d": 300.0}
    assert body["printer"]["bed"]["x"] == 256.0


def test_check_report_leads_with_the_verdict_and_names_every_check(
    service, script: Path, printer_file: Path
) -> None:
    service({"/check": CHECK_FAIL})
    report = server.partforge_check(str(script), None, str(printer_file))

    assert report.splitlines()[0].startswith("Print readiness: FAIL")
    assert "[FAIL] bed_fit" in report
    assert "[FAIL] min_wall" in report
    assert "[WARN] overhangs" in report
    assert "[PASS] watertight" in report


def test_check_report_carries_the_numbers_that_decide_each_check(
    service, script: Path, printer_file: Path
) -> None:
    service({"/check": CHECK_FAIL})
    report = server.partforge_check(str(script), None, str(printer_file))

    # bed fit: the box, the bed and how many orientations fit
    assert "300 x 300 x 40 mm" in report
    assert "0/6 orientations fit" in report
    # thinnest wall AND where it is
    assert "0.612 mm" in report
    assert "(12.25, 4.5, 0.8)" in report
    # best orientation and its unsupported area
    assert "best orientation +Z" in report
    assert "175.9 mm2 unsupported" in report  # the as-modelled orientation too
    # watertight
    assert "watertight: B-Rep valid yes" in report


def test_check_hands_back_the_suggested_mode_verbatim(
    service, script: Path, printer_file: Path
) -> None:
    """The point of the report: the model can copy this straight into segment."""
    service({"/check": CHECK_FAIL})
    report = server.partforge_check(str(script), None, str(printer_file))

    assert '{"radial": 4}' in report
    assert "partforge_segment" in report
    assert normalize_segment_mode({"radial": 4}) == {"radial": 4}


def test_check_names_the_printer_profile_it_used(
    service, script: Path, printer_file: Path
) -> None:
    service({"/check": CHECK_FAIL})
    report = server.partforge_check(str(script), None, str(printer_file))
    assert str(printer_file) in report
    assert "Elegoo Centauri Carbon" in report


def test_a_missing_printer_path_is_an_error_but_a_missing_default_is_not(
    service, script: Path, tmp_path: Path, monkeypatch
) -> None:
    service({"/check": CHECK_FAIL})

    with pytest.raises(ForgeError, match="No printer profile at"):
        server.partforge_check(str(script), None, str(tmp_path / "nope.json"))

    monkeypatch.setattr(config, "DEFAULT_PRINTER_PATH", str(tmp_path / "nope.json"))
    report = server.partforge_check(str(script))
    assert "service defaults" in report


def test_check_without_a_profile_sends_no_printer_key(
    service, script: Path, tmp_path: Path, monkeypatch
) -> None:
    """No profile must mean "use your own defaults", not an empty object to merge."""
    fake = service({"/check": CHECK_FAIL})
    monkeypatch.setattr(config, "DEFAULT_PRINTER_PATH", str(tmp_path / "nope.json"))
    server.partforge_check(str(script))
    assert "printer" not in fake.body_for("/check")


# --- partforge_segment ------------------------------------------------------


def test_segment_asks_for_no_meshes_and_forwards_the_joint(
    service, script: Path, printer_file: Path
) -> None:
    fake = service({"/segment": segment_payload(with_mesh=False)})
    server.partforge_segment(
        str(script),
        None,
        str(printer_file),
        joint_type="magnet",
        joint_tolerance=0.25,
        mode={"radial": 4},
    )

    body = fake.body_for("/segment")
    assert body["include_mesh"] is False
    assert body["mode"] == {"radial": 4}
    assert body["joint"] == {"type": "magnet", "tolerance": 0.25}


def test_segment_omits_the_tolerance_so_the_printer_profile_decides(
    service, script: Path, printer_file: Path
) -> None:
    fake = service({"/segment": segment_payload(with_mesh=False)})
    server.partforge_segment(str(script), None, str(printer_file), mode=4)
    assert fake.body_for("/segment")["joint"] == {"type": "dovetail"}


def test_segment_report_summarises_pieces_and_the_plate(
    service, script: Path, printer_file: Path
) -> None:
    service({"/segment": segment_payload(with_mesh=False)})
    report = server.partforge_segment(str(script), None, str(printer_file), mode=4)

    assert "Segmented ring_band.py into 4 piece(s)" in report
    assert "radial x4" in report
    assert "dovetail (tolerance 0.1 mm from printer.tolerances.press_fit)" in report
    for index in range(1, 5):
        assert f"segment_{index}" in report
    assert "212.65 x 48.43 x 40 mm" in report
    assert "Plate: fits" in report
    assert "rotated 44.86 deg" in report
    # planning only: no triangles reach the model
    assert "vertices" not in report


def test_a_negative_joint_tolerance_is_rejected(service, script: Path, printer_file: Path) -> None:
    service({"/segment": segment_payload(with_mesh=False)})
    with pytest.raises(ForgeError, match="cannot be negative"):
        server.partforge_segment(str(script), None, str(printer_file), joint_tolerance=-1.0)


# --- partforge_load_segments ------------------------------------------------


def _load_meshes_responder(request: dict[str, Any], conn: Any) -> None:
    meshes = request["params"]["meshes"]
    conn.sendall(
        json.dumps(
            {
                "id": request.get("id"),
                "status": "success",
                "result": {
                    "objects": [
                        {
                            "object": mesh["name"],
                            "vertex_count": len(mesh["vertices"]),
                            "face_count": len(mesh["faces"]),
                            "replaced": True,
                        }
                        for mesh in meshes
                    ],
                    "count": len(meshes),
                },
                "message": "",
            }
        ).encode("utf-8")
        + b"\n"
    )


def test_load_segments_asks_for_meshes_and_sends_one_bulk_load(
    service, script: Path, printer_file: Path, monkeypatch
) -> None:
    fake = service({"/segment": segment_payload(with_mesh=True)})
    with FakeBlender(_load_meshes_responder) as blender:
        monkeypatch.setattr(config, "BLENDER_PORT", blender.port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 10.0)
        report = server.partforge_load_segments(
            str(script), None, str(printer_file), mode=4, collection="Segments"
        )

    assert fake.body_for("/segment")["include_mesh"] is True

    assert len(blender.requests) == 1, "one bulk load, not one call per segment"
    sent = blender.requests[0]
    assert sent["type"] == "load_meshes"
    assert sent["params"]["replace"] is True
    assert sent["params"]["collection"] == "Segments"

    meshes = sent["params"]["meshes"]
    assert [m["name"] for m in meshes] == [f"segment_{i}" for i in range(1, 5)]
    # every segment carries its plate placement, forwarded verbatim
    positions = [tuple(m["plate"]["position_mm"]) for m in meshes]
    assert len(set(positions)) == 4
    assert meshes[1]["plate"]["pre_rotate_deg"] == pytest.approx(134.858179)

    assert "Loaded 4 object(s)" in report
    assert "collection 'Segments'" in report


def test_load_segments_still_reports_the_cut_when_blender_is_down(
    service, script: Path, printer_file: Path, dead_backends
) -> None:
    service({"/segment": segment_payload(with_mesh=True)})
    report = server.partforge_load_segments(str(script), None, str(printer_file), mode=4)

    assert "Segmented ring_band.py into 4 piece(s)" in report
    assert "NOT loaded into Blender" in report
    assert "Blender is not running" in report


# --- partforge_export_segments ----------------------------------------------


def test_export_segments_creates_the_directory_and_lists_what_was_written(
    service, script: Path, printer_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "exports" / "nested"
    fake = service({"/export_segments": lambda body: export_payload(out)})

    report = server.partforge_export_segments(
        str(script),
        str(out),
        None,
        str(printer_file),
        joint_type="pin",
        mode=4,
        basename="ring",
        format="stl",
    )

    assert out.is_dir(), "the directory is created before the service is asked"
    body = fake.body_for("/export_segments")
    assert body["directory"] == str(out)
    assert body["basename"] == "ring"
    assert body["format"] == "stl"
    assert body["include_mesh"] is False

    assert "ring_segment_1.stl" in report
    assert "1.0 KB" in report and "4.0 KB" in report  # per-file sizes on disk
    assert "ring_plate.3mf" in report
    assert "5 file(s)" in report
