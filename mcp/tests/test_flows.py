"""The flow tools: `flow_list`, `flow_run`, `flow_save` (Phase 6b).

Flows are the second thing this server writes to disk (after `partforge_new_part`)
and the only thing it writes that is *executed* later, so the tests care about
the same three things, in the same order of importance:

* **where the bytes land.** Only ever `flows/<slug>.json`. Path-shaped names are
  refused, never cleaned up.
* **what may be in them.** Every `op` is checked against the real command /
  endpoint set before the file exists, so a flow that will not run cannot be
  saved, and a single-step "flow" is refused because that is just a tool call.
* **the wire and the report.** `flow_run` sends one `flow_run` socket command and
  renders every step it gets back.

`flows/` is redirected to a tmp_path for every test in this module, so the real
folder is never written to. Blender is the NDJSON fake from `test_blender_client`
on an ephemeral port; nothing here touches 9876 or 8765.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import ForgeError

from .test_blender_client import FakeBlender, point_at, reply  # noqa: F401 (fixture)

STEPS: list[dict[str, Any]] = [
    {"kind": "service", "op": "/segment", "label": "Cut the part into 4",
     "args": {"script_path": "", "mode": {"radial": "{{wedges}}"},
              "include_mesh": True}},
    {"kind": "blender", "op": "load_meshes", "label": "Lay the pieces out",
     "args": {"meshes": "{{steps.0.result.segments}}",
              "plate": "{{steps.0.result.plate}}"}},
]

PARAMS = {"wedges": {"value": 4, "unit": "count", "description": "How many"}}

#: What the add-on answers a flow_run with.
RAN = {
    "flow": "segment-into-4",
    "description": "Cut it up and show the pieces.",
    "params": {"wedges": 6},
    "steps": [
        {"index": 0, "kind": "service", "op": "/segment",
         "label": "Cut the part into 4", "ok": True,
         "brief": "segments=6 (seg_00, seg_01, ...), mode={\"radial\":6}"},
        {"index": 1, "kind": "blender", "op": "load_meshes",
         "label": "Lay the pieces out", "ok": True,
         "brief": "count=6, names=6 (seg_00, seg_01, ...)"},
    ],
    "count": 2,
    "ok": True,
    "duration_ms": 4200,
}


@pytest.fixture(autouse=True)
def flows_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect flows/ so no test can write into the real repo folder."""
    root = tmp_path / "flows"
    root.mkdir()
    monkeypatch.setattr(config, "FLOWS_DIR", str(root))
    return root


def call(name: str, arguments: dict[str, Any] | None = None):
    import asyncio

    from mcp.client.client import Client

    async def run():
        async with Client(server.app) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def text_of(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def write_flow(root: Path, name: str, **overrides: Any) -> Path:
    doc = {"name": name, "description": "Cut it up and show the pieces.",
           "params": PARAMS, "steps": STEPS}
    doc.update(overrides)
    path = root / f"{name}.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


# --- slugs and paths: the whole write surface -------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("segment into 4", "segment-into-4"),
        ("Segment Into 4", "segment-into-4"),
        ("  segment into 4!  ", "segment-into-4"),
        ("segment_into_4", "segment-into-4"),
        ("segment-into-4", "segment-into-4"),
        ("segment-into-4.json", "segment-into-4"),
        ("Retopo & Unwrap", "retopo-unwrap"),
        ('"quoted name"', "quoted-name"),
        ("---weird---name---", "weird-name"),
    ],
)
def test_names_become_file_slugs(given: str, expected: str) -> None:
    assert util.flow_slug(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "../evil",
        "..\\evil",
        "../../etc/passwd",
        "flows/thing",
        "C:\\Windows\\System32\\thing",
        "/etc/passwd",
        "~/thing",
        "%APPDATA%\\thing",
        "$HOME/thing",
    ],
)
def test_path_shaped_names_are_refused_not_cleaned(given: str) -> None:
    with pytest.raises(ForgeError) as excinfo:
        util.flow_slug(given)
    message = str(excinfo.value)
    assert "flow name" in message
    assert "flows/<name>.json" in message or "environment variable" in message


def test_a_name_with_no_letters_is_refused() -> None:
    with pytest.raises(ForgeError, match="letters or digits"):
        util.flow_slug("---")
    with pytest.raises(ForgeError, match="No flow name"):
        util.flow_slug("  ")


def test_long_names_are_trimmed_to_a_usable_filename() -> None:
    slug = util.flow_slug("segment " * 40)
    assert len(slug) <= util.FLOW_NAME_MAX
    assert not slug.endswith("-")


def test_flow_paths_stay_inside_the_flows_folder(flows_dir: Path) -> None:
    assert util.flow_path("segment-into-4").parent == flows_dir.resolve()


# --- flow_save validation ---------------------------------------------------


def test_saving_writes_exactly_one_file_in_flows(flows_dir: Path) -> None:
    report = text_of(call("flow_save", {
        "name": "segment into 4",
        "description": "Cut it up and show the pieces.",
        "steps": STEPS,
        "params": PARAMS,
    }))
    written = sorted(p.name for p in flows_dir.iterdir())
    assert written == ["segment-into-4.json"], written

    doc = json.loads((flows_dir / "segment-into-4.json").read_text(encoding="utf-8"))
    assert doc["name"] == "segment-into-4"
    assert doc["description"] == "Cut it up and show the pieces."
    assert doc["params"] == PARAMS
    assert [step["op"] for step in doc["steps"]] == ["/segment", "load_meshes"]
    assert [step["label"] for step in doc["steps"]] == [
        "Cut the part into 4", "Lay the pieces out"]

    # the report tells the model what to tell the artist
    assert "segment-into-4" in report
    assert "Flows box" in report and "Run" in report
    assert "wedges" in report


def test_saving_refuses_to_clobber_unless_asked(flows_dir: Path) -> None:
    write_flow(flows_dir, "segment-into-4")
    result = call("flow_save", {"name": "segment into 4", "description": "again",
                                "steps": STEPS})
    assert result.is_error
    assert "overwrite=true" in text_of(result)

    report = text_of(call("flow_save", {
        "name": "segment into 4", "description": "the revised one",
        "steps": STEPS, "overwrite": True}))
    assert "Replaced" in report
    doc = json.loads((flows_dir / "segment-into-4.json").read_text(encoding="utf-8"))
    assert doc["description"] == "the revised one"


def test_a_single_step_flow_is_not_worth_saving(flows_dir: Path) -> None:
    result = call("flow_save", {"name": "just one", "description": "nope",
                                "steps": STEPS[:1]})
    assert result.is_error
    assert "ONE step" in text_of(result)
    assert not list(flows_dir.iterdir())


def test_an_empty_step_list_is_refused(flows_dir: Path) -> None:
    result = call("flow_save", {"name": "nothing", "description": "nope",
                                "steps": []})
    assert result.is_error
    assert "non-empty list" in text_of(result)
    assert not list(flows_dir.iterdir())


def test_a_flow_needs_a_description(flows_dir: Path) -> None:
    result = call("flow_save", {"name": "x", "description": "   ", "steps": STEPS})
    assert result.is_error
    assert "tooltip" in text_of(result)
    assert not list(flows_dir.iterdir())


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ({"kind": "blender", "op": "remesh_it_good", "args": {}},
         "is not a Blender command"),
        ({"kind": "service", "op": "/frobnicate", "args": {}},
         "is not a geometry-service endpoint"),
        ({"kind": "python", "op": "remesh", "args": {}}, "it must be"),
        ({"kind": "blender", "op": "", "args": {}}, "has no 'op'"),
        ({"kind": "blender", "op": "remesh", "args": "not an object"},
         "'args' must be an object"),
        ({"kind": "blender", "op": "flow_run", "args": {}}, "do not nest"),
    ],
)
def test_only_real_operations_may_be_saved(flows_dir: Path, step: Any,
                                           expected: str) -> None:
    result = call("flow_save", {"name": "bad", "description": "d",
                                "steps": [STEPS[0], step]})
    assert result.is_error
    assert expected in text_of(result)
    assert not list(flows_dir.iterdir()), "nothing may be written on a refusal"


def test_a_step_that_is_not_an_object_is_refused(flows_dir: Path) -> None:
    """The schema catches this one before the tool body does; either is fine."""
    result = call("flow_save", {"name": "bad", "description": "d",
                                "steps": [STEPS[0], "not a step at all"]})
    assert result.is_error
    assert "steps" in text_of(result)
    assert not list(flows_dir.iterdir())
    # and the tool body refuses it too, for a caller that gets past the schema
    with pytest.raises(ForgeError, match="must be an object"):
        util.normalize_flow_steps([STEPS[0], "not a step at all"])


def test_every_known_op_really_is_a_known_op() -> None:
    """The mirrored command list must not drift from the contract."""
    # the commands docs/architecture.md tables, spot-checked across every phase
    for name in ("ping", "get_scene_info", "load_meshes", "partforge_open",
                 "rigforge_tag", "rigforge_retopo", "rigforge_cloth",
                 "rigforge_export_godot", "export_stl"):
        assert name in util.KNOWN_BLENDER_OPS, name
    assert "flow_run" not in util.KNOWN_BLENDER_OPS
    for endpoint in ("/parse_params", "/generate", "/check", "/segment",
                     "/export_segments", "/slice", "/mold"):
        assert endpoint in util.KNOWN_SERVICE_OPS, endpoint


def test_a_service_op_without_its_slash_is_accepted(flows_dir: Path) -> None:
    call("flow_save", {"name": "loose", "description": "d",
                       "steps": [{"kind": "service", "op": "check", "args": {}},
                                 STEPS[1]]})
    doc = json.loads((flows_dir / "loose.json").read_text(encoding="utf-8"))
    assert doc["steps"][0]["op"] == "/check"


def test_arguments_must_survive_a_round_trip_to_disk() -> None:
    """A flow is a file: an argument that cannot be written cannot be saved.

    Checked at the helper rather than through a tool call because the JSON
    transport in front of the tool already refuses most of these — this is the
    guard for everything that gets past it (NaN and infinities do, on some
    encoders).
    """
    for bad in (float("inf"), float("nan"), {1, 2}):
        with pytest.raises(ForgeError, match="JSON-serialisable"):
            util.normalize_flow_steps(
                [{"kind": "blender", "op": "remesh", "args": {"voxel_size": bad}}])


def test_bare_parameter_values_are_grown_into_the_full_shape(flows_dir: Path) -> None:
    call("flow_save", {"name": "bare", "description": "d", "steps": STEPS,
                       "params": {"wedges": 4}})
    doc = json.loads((flows_dir / "bare.json").read_text(encoding="utf-8"))
    assert doc["params"] == {"wedges": {"value": 4}}


def test_a_parameter_without_a_value_is_refused(flows_dir: Path) -> None:
    result = call("flow_save", {"name": "bad-params", "description": "d",
                                "steps": STEPS,
                                "params": {"wedges": {"unit": "count"}}})
    assert result.is_error
    assert "has no 'value'" in text_of(result)
    assert not list(flows_dir.iterdir())


# --- flow_list --------------------------------------------------------------


def test_listing_reads_the_folder_without_blender(flows_dir: Path) -> None:
    write_flow(flows_dir, "segment-into-4")
    write_flow(flows_dir, "retopo-and-unwrap", description="Rebuild and unwrap it.")
    report = text_of(call("flow_list"))

    assert "2 saved flow(s)" in report
    assert "segment-into-4" in report and "retopo-and-unwrap" in report
    assert "Cut it up and show the pieces." in report
    assert "wedges" in report and "count" in report        # params with units
    assert "1. Cut the part into 4" in report              # labelled steps, in order
    assert "[service /segment]" in report
    assert "flow_run(name, params)" in report


def test_an_empty_folder_says_so_and_suggests_saving_one(flows_dir: Path) -> None:
    report = text_of(call("flow_list"))
    assert "No saved flows" in report
    assert "flow_save" in report


def test_a_broken_flow_file_is_listed_not_hidden(flows_dir: Path) -> None:
    write_flow(flows_dir, "good")
    (flows_dir / "broken.json").write_text("{not json", encoding="utf-8")
    report = text_of(call("flow_list"))
    assert "good" in report
    assert "broken" in report and "BROKEN" in report


def test_listing_a_missing_folder_is_not_a_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "FLOWS_DIR", str(tmp_path / "nowhere"))
    assert "No saved flows" in text_of(call("flow_list"))


# --- flow_run ---------------------------------------------------------------


def test_running_sends_one_flow_run_command(point_at) -> None:  # noqa: F811
    with FakeBlender(reply({"status": "success", "result": RAN})) as fake:
        point_at(fake.port)
        report = text_of(call("flow_run", {"name": "Segment Into 4",
                                           "params": {"wedges": 6}}))

    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request["type"] == "flow_run"
    # the name is slugged on the way out, so "Segment Into 4" finds the file
    assert request["params"] == {"name": "segment-into-4", "params": {"wedges": 6}}

    assert "Ran flow 'segment-into-4'" in report
    assert "2 step(s) in 4.2s" in report
    assert "1. [ok  ] Cut the part into 4  (service /segment)" in report
    assert "2. [ok  ] Lay the pieces out  (blender load_meshes)" in report
    assert "count=6" in report                      # the per-step brief
    assert "wedges" in report                       # the parameters it used
    assert "no model in the loop" in report or "decided by a model" in report


def test_running_without_overrides_sends_no_params(point_at) -> None:  # noqa: F811
    with FakeBlender(reply({"status": "success", "result": RAN})) as fake:
        point_at(fake.port)
        call("flow_run", {"name": "segment-into-4"})
    assert fake.requests[0]["params"] == {"name": "segment-into-4"}


def test_a_failed_step_is_rendered_as_failed(point_at) -> None:  # noqa: F811
    failed = dict(RAN)
    failed["ok"] = False
    failed["steps"] = [
        dict(RAN["steps"][0]),
        {"index": 1, "kind": "blender", "op": "load_meshes",
         "label": "Lay the pieces out", "ok": False,
         "brief": "Blender is not running"},
    ]
    with FakeBlender(reply({"status": "success", "result": failed})) as fake:
        point_at(fake.port)
        report = text_of(call("flow_run", {"name": "segment-into-4"}))
    assert "[FAIL] Lay the pieces out" in report
    assert "Blender is not running" in report


def test_a_flow_that_fails_reports_the_step_that_broke(point_at) -> None:  # noqa: F811
    message = ("Flow 'segment-into-4' failed at step 2 of 2 (Lay the pieces out): "
               "Unknown object.  (done first: 1 Cut the part into 4)")
    with FakeBlender(reply({"status": "error", "message": message})) as fake:
        point_at(fake.port)
        result = call("flow_run", {"name": "segment-into-4"})
    assert result.is_error
    text = text_of(result)
    assert "step 2 of 2" in text and "Lay the pieces out" in text


def test_running_a_path_shaped_name_is_refused_before_the_socket() -> None:
    result = call("flow_run", {"name": "../../etc/passwd"})
    assert result.is_error
    assert "not a flow name" in text_of(result)


def test_running_with_a_non_object_params_is_refused() -> None:
    result = call("flow_run", {"name": "segment-into-4", "params": "wedges=6"})
    assert result.is_error


def test_a_flow_run_gets_a_longer_socket_budget_than_one_command() -> None:
    """A flow can contain a 300-second /segment; the default 180 would cut it off."""
    assert config.FLOW_RUN_TIMEOUT > config.BLENDER_READ_TIMEOUT


def test_backend_down_says_which_backend(dead_backends) -> None:
    result = call("flow_run", {"name": "segment-into-4"})
    assert result.is_error
    assert "Blender is not running" in text_of(result)


# --- the round trip ---------------------------------------------------------


def test_a_saved_flow_is_immediately_listable_and_runnable(
        flows_dir: Path, point_at) -> None:  # noqa: F811
    call("flow_save", {"name": "segment into 4", "description": "Cut and show.",
                       "steps": STEPS, "params": PARAMS})
    listing = text_of(call("flow_list"))
    assert "segment-into-4" in listing and "1 saved flow(s)" in listing

    with FakeBlender(reply({"status": "success", "result": RAN})) as fake:
        point_at(fake.port)
        call("flow_run", {"name": "segment-into-4"})
    assert fake.requests[0]["params"]["name"] == "segment-into-4"


def test_the_repos_own_starter_flow_is_valid() -> None:
    """flows/segment-into-4.json must pass the same validation flow_save applies."""
    repo_flows = Path(__file__).resolve().parents[2] / "flows"
    path = repo_flows / "segment-into-4.json"
    assert path.is_file(), f"the starter flow is missing from {repo_flows}"
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert doc["name"] == "segment-into-4"
    assert len(doc["description"]) > 30
    steps = util.normalize_flow_steps(doc["steps"])
    assert [step["op"] for step in steps] == ["/segment", "load_meshes"]
    assert steps[0]["args"]["include_mesh"] is True
    assert steps[1]["args"]["meshes"] == "{{steps.0.result.segments}}"
    params = util.normalize_flow_params(doc["params"])
    assert set(params) >= {"script_path", "wedges", "joint_type", "collection"}
    assert params["wedges"]["value"] == 4
