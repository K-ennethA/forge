"""The generator-first tools: `partforge_new_part` and `partforge_open_in_panel`.

These are the two tools that let the assistant CREATE a part rather than open
one someone already wrote, so the tests care about exactly three things:

* **where the bytes land.** Only ever `projects/<slug>/part.py`. The slug rules
  and the traversal refusals are the whole security surface of this server —
  everything else it does is read-only or goes through a backend.
* **validate before write.** A script the geometry service refuses must leave no
  trace on disk, or `projects/` fills with drafts that do not run.
* **the wire.** `partforge_open_in_panel` sends one `partforge_open` command with
  a resolved absolute path, and its report tells the model to generate next.

`projects/` is redirected to a tmp_path for every test in this module, so the
real one is never touched. The geometry service is the same ephemeral-port fake
`test_print_readiness` uses; Blender is the NDJSON fake from
`test_blender_client`. Neither real port is bound or connected to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import BackendError, BackendUnavailable, ForgeError

from .test_blender_client import FakeBlender, point_at, reply  # noqa: F401 (fixture)
from .test_print_readiness import FakeService

# --- a valid script, and the schema the service would resolve from it -------

MAGNET_HOLDER = '''"""A small magnet holder: a disc with a press-fit magnet pocket."""

from build123d import *  # noqa: F403

PARAMS = {
    "magnet_diameter": {"value": 10.0, "unit": "mm", "min": 3.0, "max": 40.0,
                        "step": 0.5, "description": "Diameter of the magnet"},
    "magnet_thickness": {"value": 3.0, "unit": "mm", "min": 1.0, "max": 20.0,
                         "step": 0.5, "description": "Thickness of the magnet"},
    "wall": {"value": 2.0, "unit": "mm", "min": 0.8, "max": 10.0, "step": 0.2,
             "description": "Wall around and under the pocket"},
}


def build(p):
    return None
'''

PARSED = {
    "magnet_diameter": {
        "value": 10.0, "unit": "mm", "min": 3.0, "max": 40.0, "step": 0.5,
        "description": "Diameter of the magnet",
    },
    "magnet_thickness": {
        "value": 3.0, "unit": "mm", "min": 1.0, "max": 20.0, "step": 0.5,
        "description": "Thickness of the magnet",
    },
    "wall": {
        "value": 2.0, "unit": "mm", "min": 0.8, "max": 10.0, "step": 0.2,
        "description": "Wall around and under the pocket",
    },
}

#: What the service really answers for a script with no PARAMS block.
PARSE_REFUSED = (
    400,
    {"error": "PARAMS must be a dict at the top level of the script; found none."},
)


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect projects/ so no test can write into the real repo folder."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


@pytest.fixture
def service(monkeypatch):
    """Start a FakeService for these routes and point the client at it."""
    started: list[FakeService] = []

    def make(routes: dict[str, Any]) -> FakeService:
        fake = FakeService(routes)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "SERVICE_URL", f"http://127.0.0.1:{fake.port}")
        monkeypatch.setattr(config, "SERVICE_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "SERVICE_READ_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


@pytest.fixture
def parses(service):
    """The common case: /parse_params accepts and returns PARSED."""
    return service({"/parse_params": {"params": PARSED}})


# --- slugs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("small magnet holder", "small-magnet-holder"),
        ("Small Magnet Holder", "small-magnet-holder"),
        ("  a small magnet holder!  ", "a-small-magnet-holder"),
        ("magnet_holder", "magnet-holder"),
        ("magnet-holder", "magnet-holder"),
        ("Bowl Holder v2", "bowl-holder-v2"),
        ("6in bowl holder", "6in-bowl-holder"),
        ("part.py", "part-py"),
        ("---weird---name---", "weird-name"),
        ('"quoted name"', "quoted-name"),
    ],
)
def test_names_become_folder_slugs(given: str, expected: str) -> None:
    assert util.project_slug(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "../evil",
        "..\\evil",
        "../../etc/passwd",
        "projects/thing",
        "C:\\Windows\\System32\\thing",
        "/etc/passwd",
        "..",
        "~/thing",
        "%APPDATA%",
        "$HOME",
    ],
)
def test_paths_are_refused_not_slugged(given: str) -> None:
    with pytest.raises(ForgeError) as caught:
        util.project_slug(given)
    assert "part name" in str(caught.value)


@pytest.mark.parametrize("given", ["", "   ", None, "!!!", "***"])
def test_names_that_cannot_be_a_folder_are_refused(given: Any) -> None:
    with pytest.raises(ForgeError):
        util.project_slug(given)


def test_a_long_name_is_trimmed_to_a_usable_folder_name() -> None:
    slug = util.project_slug("a " + "very " * 40 + "long name")
    assert len(slug) <= util.PROJECT_SLUG_MAX
    assert not slug.endswith("-")


def test_project_paths_stay_under_projects(projects_dir: Path) -> None:
    folder, script, spec = util.project_paths("small-magnet-holder")
    assert folder.parent == projects_dir.resolve()
    assert script == folder / "part.py"
    assert spec == folder / "spec.json"


# --- partforge_new_part: the happy path -------------------------------------


def test_new_part_writes_only_the_script_and_a_spec(parses, projects_dir: Path) -> None:
    report = server.partforge_new_part("small magnet holder", MAGNET_HOLDER)

    folder = projects_dir / "small-magnet-holder"
    assert sorted(p.name for p in folder.iterdir()) == ["part.py", "spec.json"]
    assert [p.name for p in projects_dir.iterdir()] == ["small-magnet-holder"]
    assert (folder / "part.py").read_text(encoding="utf-8") == MAGNET_HOLDER
    assert "Created small-magnet-holder" in report
    assert str(folder / "part.py") in report


def test_the_report_carries_the_parameter_table_and_the_next_step(parses) -> None:
    report = server.partforge_new_part("small magnet holder", MAGNET_HOLDER)

    assert "3 parameter(s)" in report
    for name in PARSED:
        assert name in report
    assert "magnet_diameter = 10 mm [3..40]" in report
    assert "partforge_open_in_panel" in report
    assert "partforge_generate" in report
    assert "partforge_check" in report


def test_the_script_is_validated_before_it_is_written(parses) -> None:
    server.partforge_new_part("small magnet holder", MAGNET_HOLDER)
    body = parses.body_for("/parse_params")
    assert body["script"] == MAGNET_HOLDER


def test_line_endings_are_normalized_and_a_trailing_newline_is_added(
    parses, projects_dir: Path
) -> None:
    server.partforge_new_part(
        "crlf part", MAGNET_HOLDER.replace("\n", "\r\n").rstrip("\r\n")
    )
    written = (projects_dir / "crlf-part" / "part.py").read_bytes()
    assert b"\r" not in written
    assert written.endswith(b"\n")


# --- partforge_new_part: spec.json ------------------------------------------


def test_the_spec_mirrors_the_parsed_schema(parses, projects_dir: Path) -> None:
    server.partforge_new_part("small magnet holder", MAGNET_HOLDER)
    spec = json.loads(
        (projects_dir / "small-magnet-holder" / "spec.json").read_text(encoding="utf-8")
    )

    assert spec["name"] == "small-magnet-holder"
    assert spec["script"] == "part.py"
    assert spec["description"]
    assert spec["print"]["printer"] == "templates/printer.json"
    assert set(spec["parameters"]) == set(PARSED)
    assert spec["parameters"]["magnet_diameter"] == {
        "value": 10.0, "unit": "mm", "min": 3.0, "max": 40.0, "step": 0.5,
        "description": "Diameter of the magnet",
    }


def test_an_existing_spec_is_never_overwritten(parses, projects_dir: Path) -> None:
    folder = projects_dir / "small-magnet-holder"
    folder.mkdir()
    (folder / "part.py").write_text(MAGNET_HOLDER, encoding="utf-8")
    (folder / "spec.json").write_text('{"name": "hand written"}', encoding="utf-8")

    report = server.partforge_new_part(
        "small magnet holder", MAGNET_HOLDER, overwrite=True
    )
    assert json.loads((folder / "spec.json").read_text(encoding="utf-8")) == {
        "name": "hand written"
    }
    assert "left as it was" in report


# --- partforge_new_part: refusals -------------------------------------------


def test_an_existing_script_is_refused_without_overwrite(
    parses, projects_dir: Path
) -> None:
    folder = projects_dir / "small-magnet-holder"
    folder.mkdir()
    (folder / "part.py").write_text("# the original\n", encoding="utf-8")

    with pytest.raises(ForgeError) as caught:
        server.partforge_new_part("small magnet holder", MAGNET_HOLDER)

    message = str(caught.value)
    assert "already exists" in message
    assert "overwrite=true" in message
    assert (folder / "part.py").read_text(encoding="utf-8") == "# the original\n"
    assert not parses.requests, "the service was called for a write that never happened"


def test_overwrite_replaces_the_script(parses, projects_dir: Path) -> None:
    folder = projects_dir / "small-magnet-holder"
    folder.mkdir()
    (folder / "part.py").write_text("# the original\n", encoding="utf-8")

    report = server.partforge_new_part(
        "small magnet holder", MAGNET_HOLDER, overwrite=True
    )
    assert (folder / "part.py").read_text(encoding="utf-8") == MAGNET_HOLDER
    assert "Updated small-magnet-holder" in report


def test_a_script_the_service_refuses_writes_nothing(service, projects_dir: Path) -> None:
    service({"/parse_params": PARSE_REFUSED})

    with pytest.raises(BackendError) as caught:
        server.partforge_new_part("small magnet holder", "x = 1\n")

    assert "PARAMS must be a dict" in str(caught.value)
    assert list(projects_dir.iterdir()) == []


def test_a_failed_revision_leaves_the_working_script_alone(
    service, projects_dir: Path
) -> None:
    service({"/parse_params": PARSE_REFUSED})
    folder = projects_dir / "small-magnet-holder"
    folder.mkdir()
    (folder / "part.py").write_text(MAGNET_HOLDER, encoding="utf-8")

    with pytest.raises(BackendError):
        server.partforge_new_part("small magnet holder", "broken", overwrite=True)

    assert (folder / "part.py").read_text(encoding="utf-8") == MAGNET_HOLDER


@pytest.mark.parametrize("name", ["../evil", "..\\evil", "C:\\Windows\\evil", "/evil"])
def test_a_path_shaped_name_writes_nothing_anywhere(
    parses, projects_dir: Path, name: str
) -> None:
    with pytest.raises(ForgeError):
        server.partforge_new_part(name, MAGNET_HOLDER)
    assert list(projects_dir.iterdir()) == []
    assert not parses.requests


@pytest.mark.parametrize("source", ["", "   \n\n", None])
def test_an_empty_script_is_refused_before_the_service(
    parses, projects_dir: Path, source: Any
) -> None:
    with pytest.raises(ForgeError) as caught:
        server.partforge_new_part("small magnet holder", source)
    assert "PARAMS" in str(caught.value)
    assert list(projects_dir.iterdir()) == []
    assert not parses.requests


def test_the_service_being_down_writes_nothing(dead_backends, projects_dir: Path) -> None:
    with pytest.raises(BackendUnavailable):
        server.partforge_new_part("small magnet holder", MAGNET_HOLDER)
    assert list(projects_dir.iterdir()) == []


# --- partforge_open_in_panel ------------------------------------------------


@pytest.fixture
def script(tmp_path: Path) -> Path:
    folder = tmp_path / "projects" / "small-magnet-holder"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "part.py"
    path.write_text(MAGNET_HOLDER, encoding="utf-8")
    return path


OPEN_RESULT = {
    "status": "success",
    "result": {
        "script": "C:\\forge\\projects\\small-magnet-holder\\part.py",
        "param_count": 3,
        "params": list(PARSED),
        "object": "small-magnet-holder",
        "schema_source": "service",
    },
}


def test_open_sends_one_partforge_open_with_an_absolute_path(
    point_at, script: Path
) -> None:
    with FakeBlender(reply(OPEN_RESULT)) as blender:
        point_at(blender.port)
        server.partforge_open_in_panel(str(script))

    assert blender.error is None
    (request,) = blender.requests
    assert request["type"] == "partforge_open"
    assert request["params"] == {"script_path": str(script.resolve())}


def test_open_reports_the_sliders_and_says_to_generate_next(
    point_at, script: Path
) -> None:
    with FakeBlender(reply(OPEN_RESULT)) as blender:
        point_at(blender.port)
        report = server.partforge_open_in_panel(str(script))

    assert "3 slider(s)" in report
    assert "magnet_diameter" in report
    assert "small-magnet-holder" in report
    assert "partforge_generate" in report
    assert "Forge" in report and "PartForge" in report


def test_a_relative_path_is_resolved_before_it_crosses_the_wire(
    point_at, script: Path, monkeypatch
) -> None:
    monkeypatch.chdir(script.parent)
    with FakeBlender(reply(OPEN_RESULT)) as blender:
        point_at(blender.port)
        server.partforge_open_in_panel("part.py")

    (request,) = blender.requests
    assert Path(request["params"]["script_path"]).is_absolute()
    assert Path(request["params"]["script_path"]) == script.resolve()


def test_a_missing_script_is_refused_before_the_socket(point_at, tmp_path: Path) -> None:
    with FakeBlender(reply(OPEN_RESULT)) as blender:
        point_at(blender.port)
        with pytest.raises(ForgeError) as caught:
            server.partforge_open_in_panel(str(tmp_path / "nope.py"))
        assert not blender.requests
    assert "No file at" in str(caught.value)


def test_blender_down_says_how_to_start_it(dead_backends, script: Path) -> None:
    with pytest.raises(BackendUnavailable) as caught:
        server.partforge_open_in_panel(str(script))
    assert "Start Server" in str(caught.value)


def test_a_thin_result_still_renders(point_at, script: Path) -> None:
    """The add-on side may be older than the tool; a bare result must not crash."""
    with FakeBlender(reply({"status": "success", "result": {}})) as blender:
        point_at(blender.port)
        report = server.partforge_open_in_panel(str(script))
    assert "partforge_generate" in report
