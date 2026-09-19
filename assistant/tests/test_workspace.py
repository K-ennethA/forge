"""Tests for the Workspace — the bridge's third surface (Phase 18).

Run them the same way as the rest::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

The harness is ``test_webui``'s, which is ``test_bridge``'s: a real bridge
process on an ephemeral port with ``fake_claude.py`` standing in for the CLI,
every folder it reads or writes pointed at ``tmp_path``, and the Blender socket
faked on a port the OS handed out.  **Nothing here touches port 8901 and
nothing here touches port 9876** — the live bridge and the artist's live
Blender belong to whoever is using them, and a test that reached for either
would be a test that fails differently depending on what somebody else is
doing.

What is being pinned down, in order:

* version parsing — a file with no suffix is version 1, and ``wip-10`` comes
  after ``wip-9`` rather than between ``wip-1`` and ``wip-2``, which is exactly
  what sorting these as strings gets wrong;
* thumbnail matching — a still is a picture of the version it is named after
  and NOT of the version whose name is a prefix of it;
* restore — a copy to the end of the chain that overwrites nothing, and a
  refusal for every name that is not one plain file in that project's models
  folder;
* the pipeline board — ``build-plan.json`` read as it is written, with the red
  stage found the way ``pipeline.blocked`` finds it, and an honest empty state
  for a project that has no plan;
* the deliverables gallery — newest first, stills and films, each as a token;
* the snapshot — the request the bridge sends Blender, the report it reads
  back, and the 503 it answers with when Blender is not there.
"""

import json
import os
import shutil
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
WEBUI_DIR = os.path.join(ASSISTANT_DIR, "webui")

for _path in (TESTS_DIR, ASSISTANT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bridge  # noqa: E402

# The web UI's fixtures, verbatim: one bridge per test with every folder it
# can write to inside tmp_path.
from test_webui import (  # noqa: E402,F401
    bridges, client, fake_blender, fetch_text, raw_get,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

#: A .blend is opaque to this bridge — it stats them and copies them and never
#: opens one — so a few bytes with the right name is a faithful stand-in.
BLEND = b"BLENDER-v500RENDH" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 96
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def write(path, data=BLEND):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def make_project(root, name="werewolf", versions=(), renders=(), plan=None):
    """One project folder on disk, with only the parts a test asks for."""
    folder = os.path.join(str(root), name)
    os.makedirs(folder, exist_ok=True)
    for index, filename in enumerate(versions):
        path = write(os.path.join(folder, "models", filename))
        # Distinct mtimes, oldest first, so "newest" is never a coin toss on a
        # filesystem with a one-second stamp.
        os.utime(path, (1_700_000_000 + index * 60, 1_700_000_000 + index * 60))
    for index, filename in enumerate(renders):
        data = MP4 if filename.lower().endswith(".mp4") else PNG
        path = write(os.path.join(folder, "renders", filename), data)
        os.utime(path, (1_700_000_000 + index * 60, 1_700_000_000 + index * 60))
    if plan is not None:
        design = os.path.join(folder, "design")
        os.makedirs(design, exist_ok=True)
        with open(os.path.join(design, "build-plan.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(plan, handle)
    return folder


def a_plan(**overrides):
    """A build plan shaped exactly like the one ``forge_mcp.pipeline`` writes.

    Read off ``projects/werewolf/design/build-plan.json`` and off
    ``pipeline.stage_template``: ``{version, project, task, notes, components,
    history, stages}``, every stage ``{id, title, status, gate, artifacts,
    numbers, history, does, tools}``.
    """
    plan = {
        "version": 1,
        "project": "werewolf",
        "task": "character",
        "notes": "Three-form player character.",
        "components": [
            {"id": "form-a-human-base", "label": "Form A — human base",
             "status": "built", "description": "Rebuilt from wip-8."},
        ],
        "history": [{"date": "2026-09-18T13:49:59", "note": "materialised"}],
        "stages": [
            {"id": "reference", "title": "Reference", "status": "passed",
             "gate": ["references_on_disk"], "artifacts": ["design/refs/a.png"],
             "numbers": {"references_on_disk": 5}, "history": [],
             "does": "File the references.", "tools": ["save_design_doc"]},
            {"id": "rig", "title": "Rig", "status": "passed",
             "gate": ["rig_check.asymmetry_mm"], "artifacts": [],
             "numbers": {"asymmetry_mm": 0, "pre_bend_applied": True},
             "history": [{"date": "2026-09-18T13:51:47", "action": "advance",
                          "from": "pending", "to": "in_progress"}],
             "does": "Landmarks, metarig, rig.", "tools": ["rig_check"]},
            {"id": "skin", "title": "Skin", "status": "failed",
             "gate": ["rig_check.overlap", "rig_check.continuity"],
             "artifacts": [],
             "numbers": {"rig_check.overlap": "fail, stray_mass 24.37",
                         "isolation_mm": 0},
             "history": [{"date": "2026-09-18T13:54:03", "action": "record",
                          "from": "in_progress", "to": "failed"}],
             "does": "Bind, then measure the leakage.",
             "tools": ["rigforge_weights"]},
            {"id": "animate", "title": "Animate", "status": "pending",
             "gate": ["animation_check.foot_slide_mm"], "artifacts": [],
             "numbers": {}, "history": [], "does": "Clips that do not slide.",
             "tools": ["rigforge_action"]},
        ],
    }
    plan.update(overrides)
    return plan


@pytest.fixture
def projects(tmp_path):
    """A projects root with one fully-furnished project in it."""
    root = tmp_path / "projects"
    root.mkdir()
    make_project(
        root, "werewolf",
        versions=["werewolf-wip.blend", "werewolf-wip-2.blend",
                  "werewolf-wip-9.blend", "werewolf-wip-10.blend"],
        renders=["werewolf-wip-9.png", "werewolf-wip-10.png",
                 "form-a-walk.mp4"],
        plan=a_plan())
    return root


@pytest.fixture
def workspace(bridges, projects):
    """A bridge reading that projects root and nothing else."""
    return bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects)})


# ---------------------------------------------------------------------------
# version parsing — the bug that sorting as strings gets wrong every time
# ---------------------------------------------------------------------------

def test_a_file_with_no_suffix_is_version_one():
    assert bridge.split_version("werewolf-wip.blend") == ("werewolf-wip", 1)


@pytest.mark.parametrize("name,expected", [
    ("werewolf-wip-2.blend", ("werewolf-wip", 2)),
    ("werewolf-wip-9.blend", ("werewolf-wip", 9)),
    ("werewolf-wip-10.blend", ("werewolf-wip", 10)),
    ("werewolf-wip-007.blend", ("werewolf-wip", 7)),
    # Greedy on the left: a stem that already has a number in it keeps it.
    ("a-1-2.blend", ("a-1", 2)),
    # Not a version suffix at all.
    ("form-a-front.blend", ("form-a-front", 1)),
    ("-1.blend", ("-1", 1)),
])
def test_the_trailing_number_is_the_version(name, expected):
    assert bridge.split_version(name) == expected


def test_double_digits_sort_after_single_digits(tmp_path):
    """``wip-10`` is the tenth version, not one between ``wip-1`` and ``wip-2``."""
    folder = make_project(
        tmp_path, "cup",
        versions=["cup-wip.blend", "cup-wip-2.blend", "cup-wip-9.blend",
                  "cup-wip-10.blend", "cup-wip-11.blend"])
    board = bridge.project_versions(folder)
    assert board["chain_count"] == 1
    chain = board["chains"][0]
    assert [entry["version"] for entry in chain["versions"]] == [1, 2, 9, 10, 11]
    assert [entry["file"] for entry in chain["versions"]][-1] == "cup-wip-11.blend"
    assert chain["latest"] == 11


def test_only_the_newest_version_is_the_current_one(tmp_path):
    folder = make_project(tmp_path, "cup",
                          versions=["cup-wip.blend", "cup-wip-2.blend",
                                    "cup-wip-10.blend"])
    chain = bridge.project_versions(folder)["chains"][0]
    assert [entry["current"] for entry in chain["versions"]] == [False, False, True]


def test_two_stems_are_two_chains(tmp_path):
    folder = make_project(tmp_path, "cup",
                          versions=["cup-wip.blend", "cup-wip-2.blend",
                                    "lid-wip.blend"])
    board = bridge.project_versions(folder)
    assert board["count"] == 3
    assert sorted(chain["stem"] for chain in board["chains"]) == ["cup-wip", "lid-wip"]


def test_only_blends_are_versions(tmp_path):
    """A ``.glb`` beside a save is an export OF one, not a version of its own."""
    folder = make_project(tmp_path, "cup", versions=["cup-wip.blend"])
    write(os.path.join(folder, "models", "cup-wip.glb"), b"glb")
    write(os.path.join(folder, "models", "notes.txt"), b"hello")
    board = bridge.project_versions(folder)
    assert board["count"] == 1
    assert board["chains"][0]["versions"][0]["file"] == "cup-wip.blend"


def test_a_project_with_no_models_folder_says_so_rather_than_failing(tmp_path):
    folder = make_project(tmp_path, "bare")
    board = bridge.project_versions(folder)
    assert board["chains"] == [] and board["count"] == 0
    assert "models folder" in board["note"]


# ---------------------------------------------------------------------------
# thumbnails — and the boundary rule that keeps v10's picture off v1's row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("render,version,matches", [
    ("werewolf-wip-7", "werewolf-wip-7", True),
    ("werewolf-wip-07", "werewolf-wip-7", True),
    ("werewolf-wip-7-turntable", "werewolf-wip-7", True),
    ("werewolf-wip_preview", "werewolf-wip", True),
    ("werewolf-wip", "werewolf-wip", True),
    # The one that matters: version 1's stem is a PREFIX of version 7's name.
    ("werewolf-wip-7", "werewolf-wip", False),
    ("werewolf-wip-10", "werewolf-wip", False),
    ("something-else", "werewolf-wip", False),
])
def test_a_render_belongs_to_the_version_it_is_named_after(render, version,
                                                           matches):
    assert bridge.render_matches_version(render, version) is matches


def test_the_chain_carries_the_render_named_after_each_version(tmp_path):
    folder = make_project(
        tmp_path, "cup",
        versions=["cup-wip.blend", "cup-wip-2.blend"],
        renders=["cup-wip-2.png"])
    chain = bridge.project_versions(folder)["chains"][0]
    first, second = chain["versions"]
    assert first["thumbnail"] is None and first["thumbnail_url"] is None
    assert second["thumbnail"] == "cup-wip-2.png"
    assert second["thumbnail_url"].startswith("/file/")


def test_a_png_wins_over_a_jpg_of_the_same_version(tmp_path):
    folder = make_project(tmp_path, "cup", versions=["cup-wip-3.blend"],
                          renders=["cup-wip-3.jpg", "cup-wip-3.png"])
    chain = bridge.project_versions(folder)["chains"][0]
    assert chain["versions"][0]["thumbnail"] == "cup-wip-3.png"


def test_a_version_with_no_render_simply_has_none(tmp_path):
    folder = make_project(tmp_path, "cup", versions=["cup-wip.blend"],
                          renders=["unrelated.png"])
    entry = bridge.project_versions(folder)["chains"][0]["versions"][0]
    assert entry["thumbnail"] is None
    assert entry["thumbnail_path"] is None


# ---------------------------------------------------------------------------
# the gates — what a client may name, and what it may not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "",
    ".",
    "..",
    "../../system_prompt.md",
    "..\\..\\bridge.py",
    "%2e%2e%2fbridge.py",
    "sub/cup-wip.blend",
    "sub\\cup-wip.blend",
    "C:\\Windows\\notepad.exe",
    ".hidden.blend",
    "cup-wip.py",          # in the folder, wrong kind of file
    "cup-wip.glb",         # in the folder, not a version
    "missing-wip.blend",   # the right shape, not on disk
])
def test_a_version_file_outside_the_models_folder_is_refused(tmp_path, name):
    folder = make_project(tmp_path, "cup", versions=["cup-wip.blend"])
    write(os.path.join(folder, "models", "cup-wip.py"), b"# not a version")
    write(os.path.join(folder, "models", "cup-wip.glb"), b"glb")
    assert bridge.resolve_version_file(folder, name) is None


def test_a_version_file_in_the_models_folder_resolves(tmp_path):
    folder = make_project(tmp_path, "cup", versions=["cup-wip-2.blend"])
    resolved = bridge.resolve_version_file(folder, "cup-wip-2.blend")
    assert resolved == os.path.join(folder, "models", "cup-wip-2.blend")


def test_the_next_name_is_one_past_the_highest_on_disk(tmp_path):
    folder = make_project(tmp_path, "cup",
                          versions=["cup-wip.blend", "cup-wip-9.blend",
                                    "cup-wip-10.blend"])
    directory = os.path.join(folder, "models")
    highest = bridge.highest_version(directory, "cup-wip")
    assert highest == 10
    assert bridge.next_version_name(directory, "cup-wip", highest) == \
        ("cup-wip-11.blend", 11)


def test_the_next_name_steps_over_a_name_that_is_already_taken(tmp_path):
    folder = make_project(tmp_path, "cup", versions=["cup-wip-2.blend"])
    directory = os.path.join(folder, "models")
    # Something else wrote -3 between the listing and the copy.
    write(os.path.join(directory, "cup-wip-3.blend"))
    assert bridge.next_version_name(directory, "cup-wip", 2)[0] == "cup-wip-4.blend"


# ---------------------------------------------------------------------------
# the pipeline board — build-plan.json, read as pipeline.py writes it
# ---------------------------------------------------------------------------

def test_the_board_is_the_plans_own_stages_in_the_plans_own_order(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    board = bridge.pipeline_board(folder, "werewolf")
    assert board["has_plan"] is True
    assert board["task"] == "character"
    assert [stage["id"] for stage in board["stages"]] == \
        ["reference", "rig", "skin", "animate"]
    assert board["counts"] == {"pending": 1, "in_progress": 0, "passed": 2,
                               "failed": 1, "overridden": 0}
    assert board["total"] == 4 and board["done"] == 2


def test_the_board_finds_the_red_stage_the_way_pipeline_blocked_does(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    board = bridge.pipeline_board(folder, "werewolf")
    assert board["blocked"] == "skin"
    assert board["blocked_stage"]["gate"] == ["rig_check.overlap",
                                              "rig_check.continuity"]
    # ``next`` is the first stage that is not green — which, on a blocked
    # plan, is the red one itself.
    assert board["next"] == "skin"


def test_an_overridden_stage_counts_as_green(tmp_path):
    plan = a_plan()
    plan["stages"][2]["status"] = "overridden"
    folder = make_project(tmp_path, "werewolf", plan=plan)
    board = bridge.pipeline_board(folder, "werewolf")
    assert board["blocked"] is None
    assert board["next"] == "animate"
    assert board["done"] == 3
    assert board["stages"][2]["green"] is True


def test_the_measurements_are_printed_as_the_gate_recorded_them(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    board = bridge.pipeline_board(folder, "werewolf")
    rig = board["stages"][1]
    measured = dict((entry["name"], entry["text"]) for entry in rig["numbers"])
    assert measured["asymmetry_mm"] == "0"
    # pipeline.coerce_numbers takes booleans and strings as well as numbers,
    # so the board has to print all three rather than assume a float.
    assert measured["pre_bend_applied"] == "yes"
    skin = board["stages"][2]
    texts = [entry["text"] for entry in skin["numbers"]]
    assert "fail, stray_mass 24.37" in texts


def test_a_status_this_bridge_has_never_heard_of_does_not_take_the_board_down(tmp_path):
    plan = a_plan()
    plan["stages"][3]["status"] = "marinating"
    folder = make_project(tmp_path, "werewolf", plan=plan)
    board = bridge.pipeline_board(folder, "werewolf")
    assert board["stages"][3]["status"] == "pending"
    assert board["has_plan"] is True


def test_a_project_with_no_plan_says_so_and_invents_nothing(tmp_path):
    folder = make_project(tmp_path, "bare")
    board = bridge.pipeline_board(folder, "bare")
    assert board["has_plan"] is False
    assert board["stages"] == []
    assert "no build plan" in board["note"].lower()


def test_a_plan_that_will_not_parse_is_not_a_green_board(tmp_path):
    folder = make_project(tmp_path, "broken")
    write(os.path.join(folder, "design", "build-plan.json"), b"{ not json,")
    board = bridge.pipeline_board(folder, "broken")
    assert board["has_plan"] is False
    assert board["exists"] is True
    assert "could not be read" in board["note"]


def test_the_components_block_rides_along(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    board = bridge.pipeline_board(folder, "werewolf")
    assert board["component_count"] == 1
    assert board["components"][0]["id"] == "form-a-human-base"
    assert board["components"][0]["status"] == "built"


# ---------------------------------------------------------------------------
# the deliverables gallery
# ---------------------------------------------------------------------------

def test_deliverables_are_newest_first_and_carry_a_token(tmp_path):
    folder = make_project(tmp_path, "cup",
                          renders=["old.png", "newer.png", "newest.mp4"])
    gallery = bridge.project_deliverables(folder)
    assert [entry["file"] for entry in gallery["files"]] == \
        ["newest.mp4", "newer.png", "old.png"]
    assert gallery["files"][0]["kind"] == "video"
    assert gallery["files"][1]["kind"] == "image"
    assert all(entry["url"].startswith("/file/") for entry in gallery["files"])


def test_a_project_with_no_renders_folder_says_so(tmp_path):
    folder = make_project(tmp_path, "bare")
    gallery = bridge.project_deliverables(folder)
    assert gallery["files"] == [] and gallery["count"] == 0
    assert "renders folder" in gallery["note"]


def test_the_gallery_ignores_what_is_not_worth_looking_at(tmp_path):
    folder = make_project(tmp_path, "cup", renders=["shot.png"])
    write(os.path.join(folder, "renders", "notes.txt"), b"hello")
    write(os.path.join(folder, "renders", "scene.blend"))
    gallery = bridge.project_deliverables(folder)
    assert [entry["file"] for entry in gallery["files"]] == ["shot.png"]


# ---------------------------------------------------------------------------
# the snapshot's plumbing, with the socket mocked
# ---------------------------------------------------------------------------

def test_the_snapshot_report_is_found_by_its_marker_not_its_position():
    output = ("Blender said something first\n"
              'FORGE_SNAPSHOT {"ok": true, "path": "x.glb"}\n'
              "Info: and something after\n")
    assert bridge.snapshot_report(output) == {"ok": True, "path": "x.glb"}


def test_a_report_that_is_not_json_is_no_report():
    assert bridge.snapshot_report("FORGE_SNAPSHOT not json at all") is None
    assert bridge.snapshot_report("nothing here") is None
    assert bridge.snapshot_report("") is None


def test_the_snapshot_script_carries_no_client_input():
    """The only thing substituted into the code Blender runs is JSON."""
    code = bridge.SNAPSHOT_SCRIPT % (json.dumps("C:/tmp/snapshot-cup.glb"),
                                     json.dumps('evil"); import os #'))
    assert '"C:/tmp/snapshot-cup.glb"' in code
    # Whatever an object name contains, it arrives as a JSON string literal
    # rather than as code.
    assert 'import os #' not in code.replace(
        json.dumps('evil"); import os #'), "")
    compile(code, "<snapshot>", "exec")   # it is still valid Python


def test_the_snapshot_path_is_one_file_per_project_in_the_previews_folder(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ASSISTANT_PREVIEWS", str(tmp_path / "previews"))
    path = bridge.new_snapshot_path("werewolf")
    assert os.path.basename(path) == "snapshot-werewolf.glb"
    assert os.path.dirname(path) == os.path.abspath(str(tmp_path / "previews"))
    # Called twice, it is the same file: a snapshot is a picture of now.
    assert bridge.new_snapshot_path("werewolf") == path


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

def test_versions_route_answers_with_the_chain(workspace):
    status, body = workspace.request("/projects/werewolf/versions")
    assert status == 200, body
    assert body["project"] == "werewolf"
    assert body["count"] == 4
    chain = body["chains"][0]
    assert chain["stem"] == "werewolf-wip"
    assert [entry["version"] for entry in chain["versions"]] == [1, 2, 9, 10]
    newest = chain["versions"][-1]
    assert newest["file"] == "werewolf-wip-10.blend"
    assert newest["current"] is True
    assert newest["thumbnail"] == "werewolf-wip-10.png"
    assert newest["modified"].endswith("Z")
    assert newest["size"] == len(BLEND)


def test_the_versions_route_answers_on_both_spellings_of_the_prefix(workspace):
    plural = workspace.request("/projects/werewolf/versions")[1]
    singular = workspace.request("/project/werewolf/versions")[1]
    assert plural["count"] == singular["count"] == 4


@pytest.mark.parametrize("path", [
    "/projects/nope/versions",
    "/projects/..%2F..%2Fassistant/versions",
    "/projects/.../versions",
    "/project/nope/pipeline",
    "/projects/nope/deliverables",
])
def test_a_project_that_is_not_there_is_a_404(workspace, path):
    status, body = workspace.request(path)
    assert status == 404, body
    assert "error" in body


def test_restore_copies_to_the_end_of_the_chain(workspace, projects):
    status, body = workspace.request(
        "/projects/werewolf/versions/restore",
        payload={"file": "werewolf-wip-2.blend"})
    assert status == 200, body
    assert body["file"] == "werewolf-wip-11.blend"
    assert body["version"] == 11
    assert body["source"] == "werewolf-wip-2.blend"
    assert body["source_version"] == 2
    models = projects / "werewolf" / "models"
    assert (models / "werewolf-wip-11.blend").is_file()
    # Nothing was moved and nothing was lost.
    assert (models / "werewolf-wip-2.blend").is_file()
    assert (models / "werewolf-wip-10.blend").is_file()
    assert len(list(models.glob("*.blend"))) == 5


def test_restore_never_overwrites_the_version_it_is_restoring_from(workspace,
                                                                  projects):
    source = projects / "werewolf" / "models" / "werewolf-wip-2.blend"
    source.write_bytes(b"the-original-bytes")
    workspace.request("/projects/werewolf/versions/restore",
                      payload={"file": "werewolf-wip-2.blend"})
    assert source.read_bytes() == b"the-original-bytes"
    copy = projects / "werewolf" / "models" / "werewolf-wip-11.blend"
    assert copy.read_bytes() == b"the-original-bytes"


def test_restoring_twice_makes_two_versions(workspace, projects):
    first = workspace.request("/projects/werewolf/versions/restore",
                              payload={"file": "werewolf-wip.blend"})[1]
    second = workspace.request("/projects/werewolf/versions/restore",
                               payload={"file": "werewolf-wip.blend"})[1]
    assert first["file"] == "werewolf-wip-11.blend"
    assert second["file"] == "werewolf-wip-12.blend"


@pytest.mark.parametrize("filename", [
    "../../system_prompt.md",
    "..\\..\\bridge.py",
    "%2e%2e%2fbridge.py",
    "sub/werewolf-wip.blend",
    "werewolf-wip.py",
    "nothing-here.blend",
    "",
    None,
])
def test_restore_refuses_anything_that_is_not_one_of_its_own_versions(
        workspace, projects, filename):
    before = sorted(p.name for p in (projects / "werewolf" / "models").iterdir())
    status, body = workspace.request("/projects/werewolf/versions/restore",
                                     payload={"file": filename})
    assert status == 400, body
    assert "saved versions" in body["error"]
    after = sorted(p.name for p in (projects / "werewolf" / "models").iterdir())
    assert before == after


def test_restore_needs_a_json_body(workspace):
    status, body = workspace.request("/projects/werewolf/versions/restore",
                                     payload=[1, 2, 3])
    assert status == 400
    assert "JSON object" in body["error"]


def test_pipeline_route_answers_with_the_board(workspace):
    status, body = workspace.request("/projects/werewolf/pipeline")
    assert status == 200, body
    assert body["project"] == "werewolf"
    assert body["has_plan"] is True
    assert body["blocked"] == "skin"
    assert [stage["id"] for stage in body["stages"]] == \
        ["reference", "rig", "skin", "animate"]
    assert body["stages"][2]["red"] is True
    assert body["stages"][2]["marker"] == "[!]"
    assert body["path"].endswith(os.path.join("design", "build-plan.json"))


def test_the_pipeline_route_never_writes_the_plan(workspace, projects):
    plan_path = projects / "werewolf" / "design" / "build-plan.json"
    before = plan_path.read_bytes()
    workspace.request("/projects/werewolf/pipeline")
    workspace.request("/project/werewolf/pipeline")
    assert plan_path.read_bytes() == before


def test_deliverables_route_answers_with_the_gallery(workspace):
    status, body = workspace.request("/projects/werewolf/deliverables")
    assert status == 200, body
    assert body["project"] == "werewolf"
    files = body["files"]
    assert [entry["file"] for entry in files][0] == "form-a-walk.mp4"
    assert files[0]["kind"] == "video"
    assert all(entry["url"].startswith("/file/") for entry in files)


def test_a_deliverable_token_serves_the_real_bytes(workspace):
    """A gallery of filenames is the state this panel exists to leave behind."""
    body = workspace.request("/projects/werewolf/deliverables")[1]
    picture = [entry for entry in body["files"]
               if entry["file"].endswith(".png")][0]
    status, headers, served = raw_get(workspace, picture["url"])
    assert status == 200
    assert served == PNG
    assert headers.get("Content-Type") == "image/png"

    film = [entry for entry in body["files"]
            if entry["file"].endswith(".mp4")][0]
    status, headers, served = raw_get(workspace, film["url"])
    assert status == 200 and served == MP4
    assert headers.get("Content-Type") == "video/mp4"


def test_a_project_with_nothing_in_it_draws_honest_empty_states(bridges, tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    make_project(root, "bare")
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(root)})
    versions = client.request("/projects/bare/versions")[1]
    plan = client.request("/projects/bare/pipeline")[1]
    gallery = client.request("/projects/bare/deliverables")[1]
    assert versions["chains"] == [] and versions["note"]
    assert plan["has_plan"] is False and plan["stages"] == []
    assert gallery["files"] == [] and gallery["note"]


# ---------------------------------------------------------------------------
# the snapshot route, against a faked socket — never against 9876
# ---------------------------------------------------------------------------

def snapshot_responder(written=b"glTF-ish bytes", ok=True, error=""):
    """A fake add-on that writes the .glb the bridge asked it to write."""
    seen = {}

    def responder(request):
        code = (request.get("params") or {}).get("code") or ""
        seen["code"] = code
        # The bridge substitutes the target path in as a JSON string literal on
        # the ``target = `` line; that is the only thing this fake needs.
        target = None
        for line in code.splitlines():
            if line.startswith("target = "):
                target = json.loads(line[len("target = "):])
                break
        if target and ok:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(written)
        report = ({"ok": True, "path": target,
                   "objects": ["werewolf-form-a_retopo", "werewolf-rig"],
                   "meshes": ["werewolf-form-a_retopo"],
                   "armatures": ["werewolf-rig"],
                   "animations": ["walk-loop"]}
                  if ok else {"ok": False, "error": error})
        return {"id": request.get("id"), "status": "success",
                "result": {"output": "Info: exporting\nFORGE_SNAPSHOT "
                                     + json.dumps(report) + "\n",
                           "result": None}}

    responder.seen = seen
    return responder


def test_snapshot_exports_the_scene_and_hands_back_a_token(bridges, projects,
                                                           fake_blender, tmp_path):
    responder = snapshot_responder()
    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/snapshot", payload={})
    assert status == 200, body
    assert body["project"] == "werewolf"
    assert body["url"].startswith("/file/")
    assert body["size"] == len(b"glTF-ish bytes")
    assert body["meshes"] == ["werewolf-form-a_retopo"]
    assert body["animations"] == ["walk-loop"]
    assert os.path.basename(body["path"]) == "snapshot-werewolf.glb"

    # The command really was execute_python, and the code it carried is the
    # bridge's own constant rather than anything a client sent.
    request = server.seen[0]
    assert request["type"] == "execute_python"
    assert "export_scene.gltf" in request["params"]["code"]
    assert "FORGE_SNAPSHOT" in request["params"]["code"]

    # …and the token serves the bytes Blender wrote.
    assert fetch_text(client, body["url"]).startswith("glTF")


def test_snapshot_passes_a_named_object_through_as_a_string(bridges, projects,
                                                            fake_blender):
    responder = snapshot_responder()
    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    client.request("/projects/werewolf/snapshot",
                   payload={"object": "werewolf-form-a_retopo"})
    code = server.seen[0]["params"]["code"]
    assert 'only = "werewolf-form-a_retopo"' in code


def test_snapshot_says_blender_is_not_running_in_words_an_artist_can_act_on(
        workspace):
    # The fixture's FORGE_BLENDER_PORT is a port nothing is listening on, so
    # this is the real "Blender is closed" path and not a mock of it.
    status, body = workspace.request("/projects/werewolf/snapshot", payload={})
    assert status == 503, body
    assert "Blender is not running" in body["error"]
    assert body["blender"] is False


def test_a_scene_with_nothing_in_it_is_a_409_not_a_crash(bridges, projects,
                                                         fake_blender):
    responder = snapshot_responder(
        ok=False, error="The Blender scene has nothing visible to export.")
    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/snapshot", payload={})
    assert status == 409, body
    assert "nothing visible" in body["error"]


def test_an_addon_that_answers_with_nothing_readable_is_a_502(bridges, projects,
                                                              fake_blender):
    def responder(request):
        return {"id": request.get("id"), "status": "success",
                "result": {"output": "Info: nothing to say", "result": None}}

    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/snapshot", payload={})
    assert status == 502, body
    assert "could read back" in body["error"]


def test_snapshot_refuses_a_project_that_is_not_there(bridges, projects,
                                                      fake_blender):
    server = fake_blender(snapshot_responder())
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, _body = client.request("/projects/nope/snapshot", payload={})
    assert status == 404
    # Blender was never asked about a project that does not exist.
    assert server.seen == []


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_the_viewer_is_served_from_the_webui_folder(client):
    text = fetch_text(client, "/webui/glbview.js")
    assert "ForgeGLB" in text
    assert "parseGLB" in text


def test_the_page_loads_the_viewer_locally_and_not_from_a_cdn(client):
    """This page has to work on a machine with no internet, forever."""
    import re

    html = fetch_text(client, "/")
    for script in ("app.js", "glbview.js", "format.js", "follow.js"):
        assert ("/webui/" + script) in html
    sources = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    assert sources, "the page stopped loading its scripts by src"
    assert all(src.startswith("/webui/") for src in sources), sources
    links = re.findall(r'<link[^>]*\shref="([^"]+)"', html)
    assert not [href for href in links if href.startswith("http")], links


def test_the_workspace_tab_and_its_panels_are_on_the_page(client):
    html = fetch_text(client, "/")
    for anchor in ("tab-workspace", "panel-workspace", "ws-project",
                   "ws-summary", "ws-stepper", "ws-focus",
                   "ws-viewer", "ws-canvas", "ws-now", "ws-history",
                   "ws-deliverables", "ws-versions", "ws-ask", "ws-message"):
        assert ('id="%s"' % anchor) in html, anchor


def test_the_default_screen_is_five_things_and_not_ten(client):
    """"The workspace is too cluttered ... the ui should be simpler."

    The redesign's whole claim is that what is on screen without touching
    anything is: where the build is, the stage it is on, the model, one status
    line and the chat box.  Everything else has to be behind something.  So
    the panels that used to be permanently open — the ten expanded stage
    cards, the standing activity list, the renders and the versions — are
    pinned here as ``<details>`` that start CLOSED, and the headings and
    explanatory paragraphs they carried are pinned as gone.
    """
    import re

    html = fetch_text(client, "/")
    workspace = html.split('id="panel-workspace"', 1)[1].split("</section>", 1)[0]

    # Three <details>, and every one of them starts shut.
    tags = re.findall(r"<details\b[^>]*>", workspace)
    assert len(tags) == 3, \
        "the default screen has %d collapsible panels, not 3: %s" % (len(tags), tags)
    assert not [tag for tag in tags if re.search(r"\bopen\b", tag)], tags
    for anchor in ("ws-now-box", "ws-renders", "ws-saves"):
        assert [tag for tag in tags if ('id="%s"' % anchor) in tag], anchor

    # The old chrome: one <h2> and four <h3> section headings, five
    # explanatory paragraphs.  None of them survive.
    assert "<h2>" not in workspace
    assert "<h3>" not in workspace
    for gone in ("Every stage of the staged build", "When a gate goes red",
                 "Everything in this project's", "Every saved"):
        assert gone not in workspace, gone


def test_the_script_never_reaches_for_a_workspace_element_the_page_lacks(client):
    """``$("typo")`` is null and the next line throws, taking init() with it."""
    import re

    html = fetch_text(client, "/")
    script = fetch_text(client, "/webui/app.js")
    wanted = sorted(set(re.findall(r'\$\("(ws-[A-Za-z0-9_-]+)"\)', script)))
    assert wanted, "the workspace stopped using $() — this test is now blind"
    missing = [name for name in wanted if ('id="%s"' % name) not in html]
    assert not missing, "app.js reaches for ids the page does not have: %s" % missing


def test_the_stepper_jumps_focus_and_the_focused_stage_is_the_only_one_drawn(client):
    """"Utilize the pipeline as a stage we're on and can jump back n forth."

    One chip per stage and ONE stage rendered, rather than ten cards: the
    stepper writes into ``ws.focus`` and redraws, and the focus panel asks
    ``focusedStage`` for the single stage to draw.  Pinned at the source,
    because the alternative is a screen that quietly goes back to ten.
    """
    script = fetch_text(client, "/webui/app.js")
    assert "function stepChip(" in script
    assert "function focusedStage(" in script
    assert "ws.focus = stage.id" in script          # a chip click jumps focus
    assert "var stage = focusedStage(data);" in script
    assert "stagePanel(stage, data)" in script


def test_the_focus_falls_on_the_blocked_stage_then_the_next_then_the_last(client):
    """Where the eye lands before anybody clicks, in that order.

    The order is the one an artist asking "where are we?" wants: what is
    stopping the build, else what happens next, else what happened last.  Both
    of the first two are the bridge's own answers (``blocked`` and ``next``,
    computed the way ``pipeline.blocked`` and ``pipeline.next_stage`` compute
    them), so this rule never disagrees with the plan.
    """
    script = fetch_text(client, "/webui/app.js")
    rule = script.split("function defaultFocus(", 1)[1].split("\n  }", 1)[0]
    assert rule.index("data.blocked") < rule.index("data.next"), rule
    assert rule.index("data.next") < rule.index("stages.length - 1"), rule


def test_activity_is_one_line_with_the_rest_behind_it(client):
    """"See the current activity and have past hidden or expandable."

    The summary line IS the running turn's newest step; the turns before it
    live in the drawer it opens.  And the drawer is not rebuilt while it is
    shut, because a list nobody is looking at does not need redrawing once a
    second.
    """
    script = fetch_text(client, "/webui/app.js")
    assert "function newestStep(" in script
    assert 'line.textContent = "idle"' in script
    assert "if (!box.open) { return; }" in script


def test_the_workspace_styles_ship_with_the_stylesheet(client):
    css = fetch_text(client, "/webui/app.css")
    for rule in (".ws-body", ".ws-stepper", ".ws-step", ".ws-stage",
                 ".ws-decision", ".ws-now", ".ws-drawer", ".ws-version",
                 ".ws-deliverable", ".ws-viewer", ".ws-dock"):
        assert rule in css, rule
    # The three-column board and its bordered panels are gone with it.
    assert ".ws-grid" not in css
    assert ".ws-panel-head" not in css
    assert ".ws-board-strip" not in css


# ---------------------------------------------------------------------------
# the viewer's parser, run for real under node
# ---------------------------------------------------------------------------
#
# The half of glbview.js that can be wrong in a way nobody sees.  A shader that
# will not compile is a black rectangle somebody reports in a minute; an
# accessor read at the wrong offset is a model that is subtly the wrong shape,
# which is exactly the kind of thing this whole pipeline exists to catch in
# everything EXCEPT its own viewer.  So a .glb is built here byte by byte and
# the parser is made to read it back.

def make_glb():
    """One indexed triangle on a translated parent, with one rotation clip."""
    import struct

    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)          # 36
    normals = struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)            # 36
    indices = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"                # 6 (+2 pad)
    times = struct.pack("<2f", 0.0, 1.0)                               # 8
    values = struct.pack("<8f", 0, 0, 0, 1, 0, 0, 0.7071, 0.7071)      # 32
    blob = positions + normals + indices + times + values
    assert len(blob) == 120

    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "parent", "children": [1], "translation": [10, 0, 0]},
            {"name": "triangle", "mesh": 0},
        ],
        "meshes": [{"name": "tri", "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2,
             "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 3, "componentType": 5126, "count": 2, "type": "SCALAR",
             "min": [0.0], "max": [1.0]},
            {"bufferView": 4, "componentType": 5126, "count": 2, "type": "VEC4"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 36},
            {"buffer": 0, "byteOffset": 72, "byteLength": 6},
            {"buffer": 0, "byteOffset": 80, "byteLength": 8},
            {"buffer": 0, "byteOffset": 88, "byteLength": 32},
        ],
        "buffers": [{"byteLength": len(blob)}],
        "animations": [{
            "name": "walk-loop",
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
            "samplers": [{"input": 3, "output": 4, "interpolation": "LINEAR"}],
        }],
    }

    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    body = blob + b"\x00" * ((4 - len(blob) % 4) % 4)
    total = 12 + 8 + len(payload) + 8 + len(body)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(payload), 0x4E4F534A) + payload
    out += struct.pack("<II", len(body), 0x004E4942) + body
    return out


HARNESS = r"""
const fs = require("fs");
// glbview.js closes over `window`, exactly as the page gives it to it.
global.window = {
  requestAnimationFrame: function () { return 0; },
  cancelAnimationFrame: function () {},
  devicePixelRatio: 1
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const bytes = fs.readFileSync(process.argv[3]);
const buffer = bytes.buffer.slice(bytes.byteOffset,
                                  bytes.byteOffset + bytes.byteLength);
const model = window.ForgeGLB.loadModel(buffer);
const triangle = model.primitives[0];
const quarter = window.ForgeGLB.sampleChannel(model.animations[0].channels[0], 0.5);
console.log(JSON.stringify({
  primitives: model.primitives.length,
  vertices: model.vertices,
  triangles: model.triangles,
  skinned: model.skinned,
  animations: model.animations.map(a => ({ name: a.name, duration: a.duration })),
  positions: Array.from(triangle.position),
  normals: Array.from(triangle.normal),
  indices: Array.from(triangle.indices),
  // The parent's translation has to reach the child, or a scene of several
  // objects lands in a heap at the origin.
  world: Array.from(triangle.node.world),
  halfwayRotation: Array.from(quarter)
}));
"""


def run_viewer(tmp_path, glb):
    """Read one .glb through glbview.js under node, or skip."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to run glbview.js with")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    blob = tmp_path / "model.glb"
    blob.write_bytes(glb)
    import subprocess
    done = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "glbview.js"), str(blob)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_viewer_reads_a_glb_back_exactly(tmp_path):
    read = run_viewer(tmp_path, make_glb())
    assert read["primitives"] == 1
    assert read["vertices"] == 3
    assert read["triangles"] == 1
    assert read["skinned"] == 0
    assert read["positions"] == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert read["normals"] == [0, 0, 1, 0, 0, 1, 0, 0, 1]
    assert read["indices"] == [0, 1, 2]


def test_the_viewer_carries_a_parents_transform_down_to_its_child(tmp_path):
    read = run_viewer(tmp_path, make_glb())
    # Column-major: the translation is the last row of the flat array.
    assert read["world"][12:15] == [10, 0, 0]


def test_the_viewer_finds_the_animation_and_samples_between_keys(tmp_path):
    read = run_viewer(tmp_path, make_glb())
    assert read["animations"] == [{"name": "walk-loop", "duration": 1.0}]
    # Halfway between identity and a 90° turn is 45°, which a slerp gets right
    # and a straight lerp does not.
    halfway = read["halfwayRotation"]
    assert abs(halfway[2] - 0.38268) < 0.001, halfway
    assert abs(halfway[3] - 0.92388) < 0.001, halfway


def test_the_viewer_refuses_something_that_is_not_a_glb(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to run glbview.js with")
    harness = tmp_path / "bad.js"
    harness.write_text(r"""
const fs = require("fs");
global.window = { requestAnimationFrame: function () {},
                  cancelAnimationFrame: function () {}, devicePixelRatio: 1 };
eval(fs.readFileSync(process.argv[2], "utf8"));
let said = "no error";
try { window.ForgeGLB.parseGLB(new Uint8Array([1,2,3,4,5,6,7,8,9,10,11,12]).buffer); }
catch (err) { said = String(err.message); }
console.log(said);
""", encoding="utf-8")
    import subprocess
    done = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "glbview.js")],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "magic number" in done.stdout


def test_the_decision_buttons_go_through_ask_and_never_write_the_plan(client):
    """pipeline.py owns build-plan.json. The UI asks; it does not edit.

    Unchanged by the redesign — the decision moved INTO the stage it is about
    rather than sitting in a panel of its own, but every button still composes
    a sentence and sends it down /ask.
    """
    script = fetch_text(client, "/webui/app.js")
    assert "function decisionBlock(" in script
    assert "wsSend(message, label)" in script
    assert "pipeline_advance" in script
    # The only call this tab makes to /pipeline is the GET that reads it.
    assert script.count('"/pipeline"') == 1
    reader = 'api("/projects/" + encodeURIComponent(ws.project) + "/pipeline")'
    assert reader in script


def test_the_decision_only_appears_on_the_stage_whose_gate_is_red(client):
    """A red gate and what to do about it are one thing, so they are one card.

    Guarded at the source because the failure mode is silent and bad: a
    decision block drawn beside a green stage would be offering to override
    something nobody measured as failing.
    """
    script = fetch_text(client, "/webui/app.js")
    panel = script.split("function stagePanel(", 1)[1].split("\n  function ", 1)[0]
    assert "if (stage.red) { box.appendChild(decisionBlock(stage, data)); }" in panel


def test_an_override_still_needs_a_name_and_a_reason(client):
    """`pipeline.py` will not take an unsigned waiver, so neither does this."""
    script = fetch_text(client, "/webui/app.js")
    form = script.split("function overrideForm(", 1)[1].split("\n  function ", 1)[0]
    assert form.count("required = true") == 2
    assert "if (!name || !reason) { return; }" in form
    assert 'override=true, who=\\"' in form and 'why=\\"' in form


def test_restoring_a_version_is_still_the_only_confirm_this_tab_adds(client):
    """test_webui pins the page's confirm count at three; this is the third.

    The redesign moved the version list into a drawer but did not change what
    the button does or what it says, so that count and that sentence are both
    still right.
    """
    script = fetch_text(client, "/webui/app.js")
    row = script.split("function versionRow(", 1)[1].split("\n  function ", 1)[0]
    assert row.count("window.confirm(") == 1
    assert "Nothing is overwritten and nothing is deleted" in row
