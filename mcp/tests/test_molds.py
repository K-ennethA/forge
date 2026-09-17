"""Phase 12 on the MCP surface: undercut_check and make_mold.

The routes (`/mold`, `/export_mold`, `/mold_mesh`, `/export_mold_mesh`) and the
undercut analysis behind them have existed since Phase 12; what did not exist
was a tool that reached them, so the 2026-09-16 dogfood run had to read Forge's
own source and hand-write a flow with absolute machine paths in it to mold the
flame (docs/dogfood-litwick-2026-09-16.md, finding G-1). What is pinned here is
therefore the contract that closes that hole:

1. exactly what goes on the wire, against the real request models in
   ``service/main.py`` (``MoldOptions`` / ``MoldRequest`` / ``MoldMeshRequest``)
   — and that an option nobody set is ABSENT rather than sent as a null the
   service would have to interpret;
2. the three inputs route themselves: a script to the script endpoints, a mesh
   file and a Blender object to the ``_mesh`` ones, so the generated-mesh path
   is as first-class as the CAD one;
3. files land in ``projects/<slug>/molds/`` and nowhere else, chosen by the tool
   rather than by the model, and every written path is in the report;
4. the service's refusals arrive verbatim, with the voxel-repair fix attached to
   a mesh that has holes — the same passthrough ``check_model`` makes;
5. the report carries the things a mold turn is for: the per-half undercut
   verdict WITH the threshold that judged it, the pour instructions in full, and
   the silicone line that says what is still to buy.

The geometry service is the ``FakeService`` from ``test_print_readiness`` on an
ephemeral port (never 8765) and Blender is the NDJSON fake on another (never
9876), so this whole module runs with both backends closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import BackendError, ForgeError

from .test_blender_client import FakeBlender
from .test_print_readiness import PRINTER, service  # noqa: F401 - pytest fixture

# --- canned service answers, shaped like service/worker.py's ------------------

#: The service's own refusal for a mesh with holes, word for word from
#: ``require_watertight`` — the same sentence ``/segment_mesh`` produces, which
#: is what makes the repair advice reach the artist here too.
NOT_WATERTIGHT = (
    "mesh is not watertight (1 shell, 412 boundary edges): repair first "
    "(voxel remesh) and try again"
)

PARTING_Z = 42.5


def _half(severity: str, *, area: float = 0.0, patches: int = 0) -> dict[str, Any]:
    """One half of an undercut report (service/undercut.py `_half_report`)."""
    if severity == "none":
        return {
            "draw_direction": [0.0, 0.0, 1.0],
            "face_count": 12854,
            "surface_area_mm2": 9100.0,
            "opposing_face_count": 0,
            "opposing_area_mm2": 0.0,
            "opposing_area_fraction": 0.0,
            "max_angle_deg": 0.0,
            "max_depth_mm": 0.0,
            "patch_count": 0,
            "severe_patch_count": 0,
            "patches": [],
            "examples": [],
            "severity": "none",
            "verdict": "lifts straight off",
            "thresholds": {"patch_area_mm2": 40.0, "depth_mm": 1.6, "angle_deg": 25.0},
            "detail": (
                "Nothing below the parting plane hangs back over the mold; "
                "mold_bottom lifts straight off."
            ),
        }
    return {
        "draw_direction": [0.0, 0.0, 1.0],
        "face_count": 20896,
        "surface_area_mm2": 11200.0,
        "opposing_face_count": 630,
        "opposing_area_mm2": area or 214.7,
        "opposing_area_fraction": 0.019,
        "max_angle_deg": 5.37,
        "max_depth_mm": 0.2,
        "patch_count": patches or 1,
        "severe_patch_count": 1 if severity == "severe" else 0,
        "patches": [{"area_mm2": area or 214.7, "severe": severity == "severe"}],
        "examples": [
            {
                "position_mm": [3.4, -1.2, 47.8],
                "angle_deg": 5.37,
                "depth_mm": 0.2,
                "area_mm2": 0.41,
            }
        ],
        "severity": severity,
        "verdict": "drags but opens" if severity == "mild" else "will not open",
        "thresholds": {"patch_area_mm2": 40.0, "depth_mm": 1.6, "angle_deg": 25.0},
        "detail": (
            "630 faces above the parting plane lean back over mold_top "
            "(214.7 mm2 in 1 patch(es), worst 5.37 deg past vertical, 0.2 mm of "
            "sideways grip). A rubber mold flexes over that; a rigid printed "
            "mold will drag but usually still open."
        ),
    }


def undercuts(severity: str = "mild") -> dict[str, Any]:
    """The `undercuts` block every mold response carries."""
    return {
        "parting_z_mm": PARTING_Z,
        "halves": {
            "mold_top": _half(severity),
            "mold_bottom": _half("none"),
        },
        "severity": severity,
        "verdict": {
            "none": "both halves lift straight off",
            "mild": "a rigid mold will drag, a rubber one will not",
            "severe": "a rigid mold half cannot come off this",
        }[severity],
        "detail": "mold_top drags a little; mold_bottom lifts straight off.",
        "recommend_master_box": severity == "severe",
        "recommendation": (
            None
            if severity != "severe"
            else 'Print the figure and a pour box (mode: "master_box") and cast '
            "it in silicone: rubber flexes off an undercut a rigid half grips."
        ),
        "criterion": {
            "threshold_deg": 1.0,
            "mild_angle_deg": 25.0,
            "severe_min_patch_area_mm2": 40.0,
            "severe_patch_area_fraction": 0.02,
            "severe_min_depth_mm": 1.6,
            "severe_depth_fraction": 0.05,
            "footprint_width_mm": 32.0,
            "radius_bins": 64,
            "approximation": (
                "Depth is a radial bulge about the part's vertical centre axis, "
                "so lobes that sit off-centre in plan can over-report; the "
                "severity thresholds are a judgement call about typical "
                "tin/platinum silicone, not a simulation of it."
            ),
        },
    }


INSTRUCTIONS = [
    "Print mold_top and mold_bottom. Use a small layer height (0.1 mm or finer) "
    "and no supports inside the cavity.",
    "Sand the two cavity faces until they feel smooth, then wash the halves.",
    "Brush a thin, even coat of mold release into both cavities.",
    "Press the halves together -- the 4 bumps on mold_top drop into the 4 "
    "dimples in mold_bottom -- then band, clamp or tape them shut.",
    "Pour your casting resin into the spout in one thin, slow, steady stream.",
    "Leave it flat and still for the full demold time on the bottle.",
]


def _piece(name: str, volume: float) -> dict[str, Any]:
    return {
        "name": name,
        "stats": {
            "vertex_count": 10448,
            "face_count": 20896,
            "bounding_box_mm": [64.0, 64.0, 51.2],
            "watertight": True,
        },
        "volume_mm3": volume,
    }


def mold_payload(severity: str = "mild") -> dict[str, Any]:
    """A `/mold` answer: the report, no files, no meshes."""
    pieces = [_piece("mold_top", 74110.0), _piece("mold_bottom", 68020.0)]
    return {
        "pieces": pieces,
        "halves": pieces,
        "mode": "printed_negative",
        "undercuts": undercuts(severity),
        "recommendation": undercuts(severity)["recommendation"],
        "instructions": INSTRUCTIONS,
        "printer": PRINTER,
        "params": {},
        "options": {"mode": "printed_negative"},
        "stats_part": {"bounding_box_mm": [32.0, 32.0, 55.0], "watertight": True},
        "timings": {"mold_ms": 812.4},
        "parting_z_mm": PARTING_Z,
        "parting_source": "widest cross-section",
        "parting_profile": [],
        "draft": {"angle_deg": 2.0, "mold_top": "tapered", "mold_bottom": "tapered"},
        "box": {"shell_mm": 4.0, "size_mm": [64.0, 64.0, 102.4]},
        "cavity": {"volume_mm3": 18400.0, "part_volume_mm3": 18400.0, "clearance_mm": 0.0},
        "spout": {"diameter_mm": 6.0, "position_mm": [0.0, 0.0], "length_mm": 12.0},
        "vents": {"requested": "auto", "count": 2, "diameter_mm": 1.0},
        "registration_keys": {
            "count": 4,
            "radius_mm": 2.5,
            "tolerance_mm": 0.15,
            "male_half": "mold_top",
            "female_half": "mold_bottom",
        },
        "bed": {"bed_mm": [256, 256, 256], "margin_mm": 5.0},
    }


def export_payload(directory: Path, basename: str = "litwick-flame") -> dict[str, Any]:
    """An `/export_mold` answer, with real bytes on disk so sizes are reportable."""
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for name, size in (("mold_top", 3072), ("mold_bottom", 2048)):
        path = directory / f"{basename}_{name}.stl"
        path.write_bytes(b"s" * size)
        files.append(
            {
                "name": name,
                "format": "stl",
                "path": str(path),
                "stats": {"vertex_count": 10448, "face_count": 20896},
            }
        )
    payload = mold_payload()
    payload.pop("halves", None)
    return {
        "directory": str(directory),
        "files": files,
        "halves": payload["pieces"],
        **payload,
    }


def master_box_payload(directory: Path) -> dict[str, Any]:
    """The other mode: the figure plus a box to pour silicone into."""
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for name in ("master", "box"):
        path = directory / f"gecko_{name}.stl"
        path.write_bytes(b"m" * 1024)
        files.append({"name": name, "format": "stl", "path": str(path), "stats": {}})
    return {
        "directory": str(directory),
        "files": files,
        "pieces": [_piece("master", 21000.0), _piece("box", 190000.0)],
        "mode": "master_box",
        "master_box": {
            "size_mm": [72.0, 72.0, 96.0],
            "silicone_volume_ml": 318.0,
            "split": False,
            "pour_clearance_mm": 15.0,
            "platform_mm": 3.0,
            "funnels": {"count": 1},
            "registration_keys": {"count": 0},
        },
        "undercuts": undercuts("severe"),
        "recommendation": undercuts("severe")["recommendation"],
        "instructions": ["Print gecko_master.stl and gecko_box.stl.", "Pour slowly."],
        "printer": PRINTER,
        "params": None,
        "options": {"mode": "master_box"},
        "stats_part": {},
        "timings": {},
        "parting_z_mm": PARTING_Z,
        "parting_source": "widest cross-section",
        "bed": {"bed_mm": [256, 256, 256], "margin_mm": 5.0},
        "mesh_input": {"triangle_count": 41200, "source": "flame.stl"},
    }


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "flame.py"
    path.write_text(
        "PARAMS = {'h': {'value': 55.0, 'unit': 'mm'}}\n\ndef build(p):\n    return None\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def mesh_file(tmp_path: Path) -> Path:
    path = tmp_path / "flame.stl"
    path.write_bytes(b"solid flame\nendsolid flame\n")
    return path


@pytest.fixture
def projects_dir(monkeypatch, tmp_path: Path) -> Path:
    """projects/ is redirected, so nothing is ever written into the real repo."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


@pytest.fixture
def blender(monkeypatch):
    """The add-on fake, answering export_stl — the only command this lane uses."""
    started: list[FakeBlender] = []

    def make(responder=None):
        def default(request: dict[str, Any], conn: Any) -> None:
            body = {
                "id": request.get("id"),
                "status": "success",
                "result": {"path": (request.get("params") or {}).get("path")},
                "message": "",
            }
            conn.sendall(json.dumps(body).encode("utf-8") + b"\n")

        fake = FakeBlender(responder or default)
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


# --- the inputs refuse before any round trip --------------------------------


def test_nothing_to_mold_is_refused_before_a_wire_is_touched(dead_backends) -> None:
    with pytest.raises(ForgeError) as caught:
        server.undercut_check()
    text = str(caught.value)
    assert "script_path" in text and "mesh_path" in text and "object" in text


def test_two_inputs_are_refused_rather_than_silently_ranked(dead_backends) -> None:
    """Molding "the script, or maybe that mesh" has no sensible answer."""
    with pytest.raises(ForgeError) as caught:
        server.make_mold("litwick-lamp", script_path="a.py", mesh_path="b.stl")
    assert "ONE input" in str(caught.value)


def test_a_bad_mold_name_is_refused_as_a_path(dead_backends) -> None:
    with pytest.raises(ForgeError) as caught:
        util.mold_basename("../../etc/mold")
    assert "looks like a path" in str(caught.value)


@pytest.mark.parametrize(
    ("kwargs", "marker"),
    [
        ({"shell_mm": -1.0}, "shell_mm"),
        ({"registration_keys": -2}, "registration_keys"),
        ({"spout_diameter_mm": -3.0}, "spout_diameter_mm"),
        ({"vents": -1}, "vents"),
    ],
)
def test_negative_geometry_is_refused_here_not_by_the_kernel(
    dead_backends, script: Path, kwargs: dict[str, Any], marker: str
) -> None:
    with pytest.raises(ForgeError) as caught:
        server.make_mold("litwick-lamp", script_path=str(script), **kwargs)
    assert marker in str(caught.value)


def test_an_unknown_mode_names_both_of_them(dead_backends) -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_mold_mode("silicone")
    text = str(caught.value)
    assert "printed_negative" in text and "master_box" in text


# --- the wire ---------------------------------------------------------------


def test_undercut_check_posts_the_script_to_mold_and_writes_nothing(
    service, script: Path  # noqa: F811
) -> None:
    fake = service({"/mold": mold_payload("mild")})

    report = server.undercut_check(script_path=str(script))

    body = fake.body_for("/mold")
    assert body["script"].startswith("PARAMS")
    assert body["include_mesh"] is False, "the analysis never needs the meshes"
    assert body["mode"] == "printed_negative"
    # An option nobody set is absent, so the service's own defaults apply.
    for absent in ("draft_deg", "shell_mm", "registration_keys", "spout", "vents"):
        assert absent not in body
    assert "MILD" in report
    assert "Nothing was written" in report


def test_undercut_check_sends_only_the_options_it_was_given(
    service, script: Path  # noqa: F811
) -> None:
    fake = service({"/mold": mold_payload()})

    server.undercut_check(
        script_path=str(script), parting_z_mm=40.0, undercut_threshold_deg=2.5
    )

    body = fake.body_for("/mold")
    assert body["parting_z_mm"] == 40.0
    assert body["undercut_threshold_deg"] == 2.5
    assert "draft_deg" not in body


def test_a_mesh_file_goes_to_the_mesh_route_by_path_not_by_triangles(
    service, mesh_file: Path  # noqa: F811
) -> None:
    """The generated-mesh lane: the service reads the file, nothing is uploaded."""
    fake = service({"/mold_mesh": mold_payload()})

    report = server.undercut_check(mesh_path=str(mesh_file))

    body = fake.body_for("/mold_mesh")
    assert body["file_path"] == str(mesh_file)
    assert "mesh" not in body, "triangles never cross this wire"
    assert "flame.stl" in report


def test_a_blender_object_is_exported_in_mm_then_molded_as_a_mesh(
    service, blender, projects_dir: Path  # noqa: F811
) -> None:
    """The flame case: mesh in the scene, mold halves out, one call."""
    blender_fake = blender()
    fake = service({"/mold_mesh": mold_payload()})

    report = server.undercut_check(object="flame")

    assert blender_fake.requests[0]["type"] == "export_stl"
    exported = blender_fake.requests[0]["params"]["path"]
    assert blender_fake.requests[0]["params"]["objects"] == ["flame"]
    assert exported.endswith(".stl")
    # No `scale` override: the add-on's own default is metres -> millimetres,
    # which is the unit every number in the geometry service is in.
    assert "scale" not in blender_fake.requests[0]["params"]
    assert fake.body_for("/mold_mesh")["file_path"] == exported
    assert "flame" in report


def test_each_export_gets_its_own_scratch_file(blender) -> None:
    """Two molds in one session are two inputs, never one file overwritten."""
    first = util.mold_input_path("flame")
    second = util.mold_input_path("flame")
    assert first != second


# --- where the files land ---------------------------------------------------


def test_make_mold_writes_into_the_projects_molds_folder_and_nowhere_else(
    service, projects_dir: Path, script: Path  # noqa: F811
) -> None:
    out = projects_dir / "litwick-lamp" / "molds"
    fake = service({"/export_mold": lambda body: export_payload(out)})

    report = server.make_mold(
        "Litwick Lamp!", script_path=str(script), name="litwick flame"
    )

    assert out.is_dir(), "the directory exists before the service is asked"
    body = fake.body_for("/export_mold")
    assert body["directory"] == str(out)
    assert body["basename"] == "litwick-flame", "a NAME, slugged, never a path"
    assert body["format"] == "stl"
    # Every written path is in the report, on its own line: that is what puts
    # the file in the artist's hands (dogfood 2026-09-16, turn 11 / F-4).
    for name in ("litwick-flame_mold_top.stl", "litwick-flame_mold_bottom.stl"):
        assert str(out / name) in report
    assert "NAME EVERY ONE OF THOSE PATHS" in report


def test_a_project_name_that_is_a_path_is_refused(dead_backends, script: Path) -> None:
    with pytest.raises(ForgeError) as caught:
        server.make_mold("../../elsewhere", script_path=str(script))
    assert "looks like a path" in str(caught.value)


def test_make_mold_from_a_mesh_uses_the_mesh_export_route(
    service, projects_dir: Path, mesh_file: Path  # noqa: F811
) -> None:
    out = projects_dir / "gecko" / "molds"
    fake = service({"/export_mold_mesh": lambda body: master_box_payload(out)})

    report = server.make_mold(
        "gecko", mesh_path=str(mesh_file), mode="master_box", split=True,
        margin_mm=12.0, pour_clearance_mm=18.0,
    )

    body = fake.body_for("/export_mold_mesh")
    assert body["file_path"] == str(mesh_file)
    assert body["mode"] == "master_box"
    assert body["split"] is True
    assert body["margin_mm"] == 12.0
    assert body["pour_clearance_mm"] == 18.0
    assert body["directory"] == str(out)
    assert "master_box" in report
    assert "318 ml of silicone" in report or "318" in report


def test_a_zero_spout_means_no_spout_rather_than_a_zero_wide_one(
    service, projects_dir: Path, script: Path  # noqa: F811
) -> None:
    out = projects_dir / "litwick-lamp" / "molds"
    fake = service({"/export_mold": lambda body: export_payload(out)})

    server.make_mold("litwick-lamp", script_path=str(script), spout_diameter_mm=0.0)

    assert fake.body_for("/export_mold")["spout"] is False


# --- refusals reach the artist as written -----------------------------------


@pytest.mark.parametrize("tool", ["undercut_check", "make_mold"])
def test_a_mesh_with_holes_is_refused_verbatim_with_the_repair(
    service, mesh_file: Path, tool: str  # noqa: F811
) -> None:
    """The service's own sentence survives, and the fix is attached to it."""
    route = "/mold_mesh" if tool == "undercut_check" else "/export_mold_mesh"
    service({route: (400, {"error": NOT_WATERTIGHT})})

    with pytest.raises(BackendError) as caught:
        if tool == "undercut_check":
            server.undercut_check(mesh_path=str(mesh_file))
        else:
            server.make_mold("gecko", mesh_path=str(mesh_file))

    text = str(caught.value)
    assert NOT_WATERTIGHT in text, "the service's message must arrive as written"
    assert 'remesh(mode="voxel")' in text


def test_a_service_refusal_about_the_mold_itself_is_not_dressed_as_a_repair(
    service, script: Path  # noqa: F811
) -> None:
    message = (
        "mold_top came out empty once the spout and vents were cut; reduce their "
        "diameters or raise shell_mm"
    )
    service({"/mold": (400, {"error": message})})

    with pytest.raises(BackendError) as caught:
        server.undercut_check(script_path=str(script))

    text = str(caught.value)
    assert message in text
    assert "voxel" not in text, "only a holes-in-the-mesh refusal gets that advice"


# --- the reports ------------------------------------------------------------


def test_the_undercut_report_carries_the_threshold_that_judged_it() -> None:
    """A verdict without its criterion is a number pretending to be a fact."""
    report = util.fmt_undercut_report("flame.py", mold_payload("mild"))

    assert "MILD" in report
    assert "5.37 deg past vertical" in report
    assert "0.2 mm of sideways grip" in report
    assert "mold_bottom" in report and "NONE" in report
    assert "judged at" in report and "25 deg" in report
    assert "judgement" in report.lower()
    assert "recommend_master_box: false" in report
    assert "look at" in report, "a located face to go and look at"


def test_a_severe_verdict_sends_them_to_the_master_box() -> None:
    report = util.fmt_undercut_report("gecko.stl", mold_payload("severe"))

    assert "will NOT open" in report
    assert 'mode="master_box"' in report
    assert "silicone" in report


def test_the_mold_report_prints_the_pour_instructions_in_full(
    tmp_path: Path,
) -> None:
    """They are the half the artist performs; summarising them away loses it."""
    out = tmp_path / "molds"
    report = util.fmt_mold_report(
        "flame.py", export_payload(out), out, "templates/printer.json"
    )

    assert f"HOW TO CAST FROM IT ({len(INSTRUCTIONS)} steps)" in report
    assert "mold release" in report
    assert "4 registration key(s)" in report
    assert "spout 6 mm" in report
    assert "2 vent(s)" in report
    assert "3.0 KB" in report and "2.0 KB" in report, "sizes measured on disk"


def test_the_mold_report_says_what_is_still_to_buy_and_never_buys_it(
    tmp_path: Path,
) -> None:
    out = tmp_path / "molds"
    report = util.fmt_mold_report("flame.py", export_payload(out), out, "defaults")

    assert "smooth-on.com" in report, "a link, not a search string (G-2)"
    assert "Never buy anything" in report


def test_a_mold_written_against_a_severe_verdict_says_so_before_they_print(
    tmp_path: Path,
) -> None:
    out = tmp_path / "molds"
    payload = export_payload(out)
    payload["undercuts"] = undercuts("severe")
    report = util.fmt_mold_report("gecko.stl", payload, out, "defaults")

    assert "WARNING" in report
    assert "will not come off" in report


def test_the_reports_survive_a_thin_payload() -> None:
    """A missing field costs a line, never the call."""
    assert "Moldability" in util.fmt_undercut_report("thing", {})
    assert "Molded" in util.fmt_mold_report("thing", {}, "C:\\molds", "defaults")


# --- flow legality -----------------------------------------------------------


def test_the_mold_routes_a_flow_may_call_match_the_addon() -> None:
    """`/mold` and `/export_mold` are legal flow steps; the mesh twins are not.

    Flows execute inside the ADD-ON (`flow_run` is a socket command), so this
    list may never be wider than `addon/forge/tools/flows.py`'s `SERVICE_OPS` —
    a step `flow_save` accepts and `flow_run` then refuses is worse than a step
    that could not be saved. The mesh routes go in on both sides at once or not
    at all; until then the mold lane is reached by its tools, which is what it
    was missing.
    """
    assert "/mold" in util.KNOWN_SERVICE_OPS
    assert "/export_mold" in util.KNOWN_SERVICE_OPS
    assert "/mold_mesh" not in util.KNOWN_SERVICE_OPS
    assert "/export_mold_mesh" not in util.KNOWN_SERVICE_OPS


def test_a_mold_flow_step_validates(tmp_path: Path) -> None:
    steps = util.normalize_flow_steps(
        [
            {
                "kind": "service",
                "op": "/export_mold",
                "label": "Mold it",
                "args": {"script_path": "{{ script }}", "directory": "{{ out }}"},
            },
            {"kind": "blender", "op": "render_preview", "label": "Look at it"},
        ]
    )
    assert steps[0]["op"] == "/export_mold"


# --- the file the service reads is never the repo -----------------------------


def test_the_scratch_input_folder_is_temp_not_the_project(tmp_path: Path) -> None:
    path = util.mold_input_path("flame")
    assert util.MOLD_INPUT_DIRNAME in str(path)
    assert "projects" not in str(path).lower()


def test_json_is_the_only_thing_on_the_wire(service, script: Path) -> None:  # noqa: F811
    """No pickles, no paths the service cannot read: one JSON object per call."""
    fake = service({"/mold": mold_payload()})
    server.undercut_check(script_path=str(script))
    _, body = fake.requests[0]
    json.dumps(body)  # raises if anything unserialisable got in
