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
import re
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
    """The only thing substituted into the code Blender runs is JSON.

    Three values now: the path, the object to export and the bone whose
    weights get baked into vertex colours for the heatmap. All three are
    ``json.dumps``-ed, and the last two are the ones a client can influence.
    """
    nasty = 'evil"); import os #'
    code = bridge.SNAPSHOT_SCRIPT % (json.dumps("C:/tmp/snapshot-cup.glb"),
                                     json.dumps(nasty),
                                     json.dumps(nasty))
    assert '"C:/tmp/snapshot-cup.glb"' in code
    # Whatever a name contains, it arrives as a JSON string literal rather
    # than as code.
    assert 'import os #' not in code.replace(json.dumps(nasty), "")
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


# ===========================================================================
# Phase 19 â€” nudging a joint ("I don't know how much 10mm is here")
# ===========================================================================


def a_task_config(symmetry="mirror_left"):
    """The settings sheet exactly as ``forge_mcp.task_config`` writes one."""
    return {
        "version": 1,
        "task": "character",
        "project": "werewolf",
        "settings": {
            "symmetry": {
                "value": symmetry,
                "default": "mirror_left",
                "choices": ["mirror_left", "mirror_right", "as_designed"],
                "why": "bipeds are symmetric unless you say otherwise",
            },
            "poly_budget_desktop": {"value": 15000, "default": 15000},
        },
    }


def write_task_config(folder, symmetry="mirror_left"):
    design = os.path.join(folder, "design")
    os.makedirs(design, exist_ok=True)
    path = os.path.join(design, "task-config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(a_task_config(symmetry), handle)
    return path


# ---------------------------------------------------------------------------
# the mirror, and the settings sheet it defaults from
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bone,counterpart", [
    ("DEF-foot.L", "DEF-foot.R"),
    ("DEF-foot.R", "DEF-foot.L"),
    ("DEF-upper_arm.L.001", "DEF-upper_arm.R.001"),
    ("DEF-thigh_R", "DEF-thigh_L"),
    ("DEF-hand.r", "DEF-hand.l"),
    ("DEF-breast.L.002", "DEF-breast.R.002"),
])
def test_a_sided_bone_has_a_counterpart(bone, counterpart):
    assert bridge.mirror_bone_name(bone) == counterpart


@pytest.mark.parametrize("bone", [
    "DEF-spine", "DEF-spine.005", "DEF-head", "root", "", "DEF-pelvis",
])
def test_a_centre_bone_has_no_counterpart(bone):
    """A spine has no other side, and inventing one would edit a bone nobody named."""
    assert bridge.mirror_bone_name(bone) == ""


def test_the_mirrored_move_flips_x_and_nothing_else():
    """+X is the mirror axis everywhere in Forge, so the other side is -X."""
    assert bridge.mirrored_delta([3.0, -4.0, 5.5]) == [-3.0, -4.0, 5.5]


@pytest.mark.parametrize("symmetry,mirrors", [
    ("mirror_left", True),
    ("mirror_right", True),
    ("as_designed", False),
])
def test_the_mirror_default_comes_off_the_projects_own_settings(tmp_path,
                                                                symmetry,
                                                                mirrors):
    folder = make_project(tmp_path, "werewolf")
    write_task_config(folder, symmetry)
    assert bridge.project_symmetry(folder) == symmetry
    assert bridge.symmetry_mirrors(folder) is mirrors


def test_a_project_with_no_settings_sheet_does_not_mirror(tmp_path):
    """A default that edits a bone nobody named is the wrong way round."""
    folder = make_project(tmp_path, "bare")
    assert bridge.project_symmetry(folder) == ""
    assert bridge.symmetry_mirrors(folder) is False


def test_a_settings_sheet_that_will_not_parse_does_not_mirror(tmp_path):
    folder = make_project(tmp_path, "broken")
    write(os.path.join(folder, "design", "task-config.json"), b"{ not json,")
    assert bridge.symmetry_mirrors(folder) is False


def test_a_symmetry_value_this_bridge_has_never_heard_of_does_not_mirror(tmp_path):
    folder = make_project(tmp_path, "odd")
    write_task_config(folder, "interpretive")
    assert bridge.project_symmetry(folder) == ""
    assert bridge.symmetry_mirrors(folder) is False


# ---------------------------------------------------------------------------
# what a client may send
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("delta", [
    None, "10", 10, [1, 2], [1, 2, 3, 4], ["1", 2, 3], [None, 0, 0],
    [True, 0, 0], [float("nan"), 0, 0], [float("inf"), 0, 0],
    [501.0, 0, 0], [0, 0, -500.5],
])
def test_a_delta_that_is_not_three_real_millimetres_is_refused(delta):
    assert bridge.nudge_delta(delta) is None


def test_a_real_delta_comes_back_as_three_floats():
    assert bridge.nudge_delta([1, -2.5, 0]) == [1.0, -2.5, 0.0]
    assert bridge.nudge_delta([500, -500, 0]) == [500.0, -500.0, 0.0]


# ---------------------------------------------------------------------------
# the journal
# ---------------------------------------------------------------------------

def a_record(bone="DEF-foot.L", when="2026-09-18T20:00:00Z"):
    return {"when": when, "bone": bone, "end": "head",
            "delta_mm": [0.0, 0.0, -10.0], "mirror": True, "source": "nudge"}


def test_the_journal_is_created_on_the_first_edit(tmp_path):
    """A flat list of records, oldest first — the shape already on disk."""
    folder = make_project(tmp_path, "cup")
    path = bridge.append_artist_edit(folder, a_record())
    assert path and os.path.isfile(path)
    assert path.endswith(os.path.join("design", "artist-edits.json"))
    with open(path, encoding="utf-8") as handle:
        journal = json.load(handle)
    assert isinstance(journal, list), journal
    assert len(journal) == 1
    assert journal[0]["bone"] == "DEF-foot.L"
    assert journal[0]["source"] == "nudge"
    # …and no temporary file is left lying beside it.
    assert [n for n in os.listdir(os.path.dirname(path))
            if n.endswith(".tmp")] == []


def test_two_edits_are_two_records_in_the_order_they_happened(tmp_path):
    folder = make_project(tmp_path, "cup")
    bridge.append_artist_edit(folder, a_record("DEF-foot.L", "2026-09-18T20:00:00Z"))
    bridge.append_artist_edit(folder, a_record("DEF-hand.R", "2026-09-18T20:05:00Z"))
    journal = bridge.read_journal(folder)
    assert [entry["bone"] for entry in journal["edits"]] == \
        ["DEF-foot.L", "DEF-hand.R"]
    assert [entry["when"] for entry in journal["edits"]] == \
        ["2026-09-18T20:00:00Z", "2026-09-18T20:05:00Z"]


def test_the_journal_never_writes_over_one_it_cannot_read(tmp_path):
    """An unreadable record of what somebody did by hand is still evidence."""
    folder = make_project(tmp_path, "cup")
    path = write(os.path.join(folder, "design", "artist-edits.json"),
                 b"{ half a journal,")
    assert bridge.append_artist_edit(folder, a_record()) is None
    with open(path, "rb") as handle:
        assert handle.read() == b"{ half a journal,"


#: What the assistant wrote into this journal from chat, before there was a
#: route for it: the same idea in a different shape — a joint in words and the
#: bones it stood for, rather than one bone and one end.
HAND_RECORD = {
    "when": "2026-09-18T21:33:50",
    "joint": "ankle",
    "bones": ["DEF-foot.L/R head + coincident IK/FK/tweak/metarig endpoints"],
    "delta_mm": [0, 0, -44],
    "mirror": True,
    "source": "artist decision in chat",
}


def test_records_written_by_hand_are_never_altered_or_dropped(tmp_path):
    """The other writer's entries are the ones this route must not touch.

    The assistant has been appending to this file from chat since before the
    endpoint existed, in a record shape of its own.  A journal that kept only
    the entries made by the tool reading it is not a record of anything.
    """
    folder = make_project(tmp_path, "werewolf")
    path = write(os.path.join(folder, "design", "artist-edits.json"),
                 json.dumps([HAND_RECORD], indent=2).encode("utf-8"))
    bridge.append_artist_edit(folder, a_record("DEF-foot.L"))
    with open(path, encoding="utf-8") as handle:
        journal = json.load(handle)
    assert isinstance(journal, list)
    assert len(journal) == 2
    # Byte-for-byte the same record, keys and all.
    assert journal[0] == HAND_RECORD
    assert journal[1]["source"] == "nudge"
    assert [entry["source"] for entry in journal] == \
        ["artist decision in chat", "nudge"]


def test_a_journal_that_someone_wrapped_in_an_object_keeps_its_records(tmp_path):
    folder = make_project(tmp_path, "cup")
    write(os.path.join(folder, "design", "artist-edits.json"),
          json.dumps({"version": 1, "edits": [a_record("DEF-old.L")]})
          .encode("utf-8"))
    bridge.append_artist_edit(folder, a_record("DEF-new.R"))
    journal = bridge.read_journal(folder)
    assert [entry["bone"] for entry in journal["edits"]] == \
        ["DEF-old.L", "DEF-new.R"]


def test_the_journal_is_capped_at_its_newest_records(tmp_path):
    folder = make_project(tmp_path, "cup")
    for index in range(5):
        bridge.append_artist_edit(folder, a_record("DEF-bone.%03d" % index),
                                  limit=3)
    journal = bridge.read_journal(folder)
    assert [entry["bone"] for entry in journal["edits"]] == \
        ["DEF-bone.002", "DEF-bone.003", "DEF-bone.004"]


def test_a_nudge_never_touches_the_build_plan(tmp_path):
    """pipeline.py is that file's only writer, and a placement is not a gate."""
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    plan_path = os.path.join(folder, "design", "build-plan.json")
    with open(plan_path, "rb") as handle:
        before = handle.read()
    bridge.append_artist_edit(folder, a_record())
    with open(plan_path, "rb") as handle:
        assert handle.read() == before


# ---------------------------------------------------------------------------
# the script Blender runs: nothing a client sends is ever code
# ---------------------------------------------------------------------------

#: Bone names that are attempts to get out of a string literal and into the
#: script.  Every one of them must die on the ALPHABET, before any script is
#: built â€” which is why `_BONE_NAME_RE` is an allow-list and not an escaper.
INJECTIONS = [
    'DEF-foot.L"); import os; os.system("calc"); ("',
    "DEF-foot.L'); __import__('os').system('calc'); ('",
    "DEF-foot.L\\\"",
    "DEF-foot.L\nimport os",
    "DEF-foot.L\\nimport os",
    "DEF-foot.L + open('x','w').write('y')",
    "../../../etc/passwd",
    "DEF foot L",
    "DEF-foot.L;print(1)",
    "%s",
    "{}",
    "DEF-" + "x" * 80,
    "",
]

#: The same list without the empty string.  An absent name is not an attack —
#: on the routes that take an optional object, rig or action it means "the
#: tool picks", which is what those tools do.
NAMED_INJECTIONS = [one for one in INJECTIONS if one.strip()]


@pytest.mark.parametrize("bone", INJECTIONS)
def test_a_bone_name_that_could_be_code_never_reaches_a_script(bone):
    assert not bridge._BONE_NAME_RE.match(bone) or len(bone) > 63


@pytest.mark.parametrize("bone", ["DEF-foot.L", "DEF-upper_arm.R.001",
                                  "root", "a", "A.1_b-c"])
def test_a_real_bone_name_passes_the_alphabet(bone):
    assert bridge._BONE_NAME_RE.match(bone)


def test_the_substituted_script_is_always_valid_python():
    """Every value goes through json.dumps, so it is a literal or nothing."""
    code = bridge.JOINT_MOVE_SCRIPT % (
        json.dumps("werewolf-form-a_retopo_rig"),
        json.dumps("DEF-foot.L"),
        json.dumps("head"),
        json.dumps([0.0, 0.0, -10.0]),
        json.dumps("DEF-foot.R"),
        json.dumps([0.0, 0.0, -10.0]),
    )
    compile(code, "<joint>", "exec")
    assert '"DEF-foot.L"' in code
    assert "FORGE_JOINT" in code


def test_the_script_has_no_formatting_holes_left_in_it():
    """A stray %s in the template would swallow the next value as code.

    The script uses ``%%`` for its own format strings, so the template takes
    exactly six values â€” and if that ever stops being true this fails rather
    than silently substituting a bone name into the wrong place.
    """
    with pytest.raises(TypeError):
        bridge.JOINT_MOVE_SCRIPT % (json.dumps("a"),)
    # Six holes and no more: every OTHER per-cent in the template is doubled,
    # which is how the script's own runtime format strings survive this
    # substitution instead of eating the value after them.
    holes = bridge.JOINT_MOVE_SCRIPT.replace("%%", "")
    assert holes.count("%s") == 6
    assert "%r" not in holes
    code = bridge.JOINT_MOVE_SCRIPT % tuple(
        json.dumps(one) for one in ["", "DEF-a.L", "head", [0, 0, 1],
                                    "DEF-a.R", [0, 0, 1]])
    # …and what comes out is six JSON literals on six assignment lines.
    assignments = [line for line in code.splitlines()
                   if re.match(r"^(rig_name|bone_name|end|delta_mm|"
                               r"mirror_name|mirror_delta_mm) = ", line)]
    assert len(assignments) == 6, assignments
    for line in assignments:
        json.loads(line.split(" = ", 1)[1])


# ---------------------------------------------------------------------------
# the edit itself, run against a faked bpy â€” the chain has to stay connected
# ---------------------------------------------------------------------------
#
# The socket is mocked everywhere else in this file; here the mock is one level
# further in.  `JOINT_MOVE_SCRIPT` is the real script text, exec'd against a
# Blender-shaped stand-in, because the one thing that has to be right â€” moving
# a tail drags its connected children's heads, and moving a connected head
# drags its parent's tail â€” is written in that script and nowhere else.


class V(object):
    """Enough of ``mathutils.Vector`` for the script to do its work."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def copy(self):
        return V(self.x, self.y, self.z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __eq__(self, other):
        return tuple(self) == tuple(other)

    def __repr__(self):
        return "V(%g, %g, %g)" % (self.x, self.y, self.z)


def _as_vector(value):
    if isinstance(value, V):
        return value.copy()
    return V(*tuple(value))


class FakeBone(object):
    """One bone, in both its edit and its rest identity (they are the same here)."""

    def __init__(self, name, head, tail, parent=None, connect=False):
        self.name = name
        self._head = _as_vector(head)
        self._tail = _as_vector(tail)
        self.parent = parent
        self.use_connect = connect
        self.children = []
        if parent is not None:
            parent.children.append(self)

    # The script assigns tuples to these, and reads .x/.y/.z back off them.
    def _get_head(self):
        return self._head

    def _set_head(self, value):
        self._head = _as_vector(value)

    head = property(_get_head, _set_head)

    def _get_tail(self):
        return self._tail

    def _set_tail(self, value):
        self._tail = _as_vector(value)

    tail = property(_get_tail, _set_tail)

    # The rest pose reads the same points back after edit mode closes.
    head_local = property(lambda self: self._head)
    tail_local = property(lambda self: self._tail)


class FakeBoneTable(object):
    def __init__(self, bones):
        self._bones = bones

    def get(self, name, default=None):
        for bone in self._bones:
            if bone.name == name:
                return bone
        return default

    def __contains__(self, name):
        return self.get(name) is not None

    def __iter__(self):
        return iter(self._bones)


class FakeMatrix(object):
    """An identity world matrix: ``matrix_world @ v`` is ``v``."""

    def __matmul__(self, other):
        return other


class FakeObject(object):
    def __init__(self, name, kind, bones=None):
        self.name = name
        self.type = kind
        self.mode = "OBJECT"
        self.modifiers = []
        self.matrix_world = FakeMatrix()
        self._selected = False
        if bones is not None:
            table = FakeBoneTable(bones)
            self.data = type("Data", (), {"bones": table, "edit_bones": table})()

    def select_get(self):
        return self._selected

    def select_set(self, value):
        self._selected = bool(value)


class FakeModifier(object):
    def __init__(self, rig):
        self.type = "ARMATURE"
        self.object = rig


def a_rig(name="rig"):
    """thigh -> shin -> foot, every joint connected, 1 unit apart going down."""
    thigh = FakeBone("DEF-thigh.L", (0, 0, 1.0), (0, 0, 0.5))
    shin = FakeBone("DEF-shin.L", (0, 0, 0.5), (0, 0, 0.1), thigh, connect=True)
    foot = FakeBone("DEF-foot.L", (0, 0, 0.1), (0.1, 0, 0.1), shin, connect=True)
    right = FakeBone("DEF-foot.R", (0, 0, 0.1), (0.1, 0, 0.1))
    loose = FakeBone("DEF-tail.001", (0, 1, 0), (0, 1.2, 0))
    loose.use_connect = False
    return FakeObject(name, "ARMATURE", [thigh, shin, foot, right, loose])


def run_joint_script(rig_objects, bone, end, delta_mm, mirror="",
                     mirror_delta=None, rig_name="", meshes=None,
                     unit_scale=1.0):
    """Exec the REAL script against a fake Blender. Returns its report."""
    printed = []
    objects = list(rig_objects) + list(meshes or [])

    scene = type("Scene", (), {})()
    scene.objects = objects
    scene.unit_settings = type("Units", (), {"scale_length": unit_scale})()

    view = type("View", (), {})()
    view.objects = type("Objects", (), {"active": None})()

    ops_log = []

    def mode_set(mode="OBJECT"):
        ops_log.append(mode)
        if view.objects.active is not None:
            view.objects.active.mode = mode

    fake_bpy = type("Bpy", (), {})()
    fake_bpy.context = type("Context", (), {"scene": scene, "view_layer": view})()
    fake_bpy.data = type("Data", (), {"objects": objects})()
    fake_bpy.ops = type("Ops", (), {})()
    fake_bpy.ops.object = type("ObjectOps", (), {"mode_set": staticmethod(mode_set)})()

    code = bridge.JOINT_MOVE_SCRIPT % (
        json.dumps(rig_name),
        json.dumps(bone),
        json.dumps(end),
        json.dumps(list(delta_mm)),
        json.dumps(mirror),
        json.dumps(list(mirror_delta if mirror_delta is not None
                        else bridge.mirrored_delta(delta_mm))),
    )
    namespace = {"__name__": "__forge__"}
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bpy":
            return fake_bpy
        return real_import(name, *args, **kwargs)

    def capture(*args, **kwargs):
        printed.append(" ".join(str(one) for one in args))

    namespace["__builtins__"] = dict(vars(builtins))
    namespace["__builtins__"]["__import__"] = fake_import
    namespace["__builtins__"]["print"] = capture
    try:
        exec(compile(code, "<joint>", "exec"), namespace)  # noqa: S102
    except SystemExit:
        pass
    return bridge.joint_move_report("\n".join(printed)), ops_log


def test_moving_a_tail_drags_every_connected_childs_head():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-shin.L", "tail", [0, 0, -10])
    assert report and report["ok"] is True, report
    shin = rig.data.bones.get("DEF-shin.L")
    foot = rig.data.bones.get("DEF-foot.L")
    # 10 mm down, in metres, on a scene whose unit is the metre.
    assert round(shin.tail.z, 6) == 0.09
    # The foot's head IS the shin's tail: it has to have come with it.
    assert round(foot.head.z, 6) == 0.09
    assert tuple(foot.head) == tuple(shin.tail)
    whys = [entry["why"] for entry in report["moved"]]
    assert "connected child follows" in whys


def test_moving_a_connected_head_drags_its_parents_tail():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-foot.L", "head", [0, 0, -10])
    assert report and report["ok"] is True, report
    foot = rig.data.bones.get("DEF-foot.L")
    shin = rig.data.bones.get("DEF-shin.L")
    assert round(foot.head.z, 6) == 0.09
    assert tuple(shin.tail) == tuple(foot.head)
    assert [entry["bone"] for entry in report["moved"]] == \
        ["DEF-foot.L", "DEF-shin.L"]


def test_an_unconnected_bone_takes_nothing_with_it():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-thigh.L", "head", [0, 0, -10])
    assert report["ok"] is True
    assert [entry["bone"] for entry in report["moved"]] == ["DEF-thigh.L"]
    # The thigh's head is not connected to anything above it.
    assert round(rig.data.bones.get("DEF-thigh.L").head.z, 6) == 0.99


def test_a_mirrored_nudge_moves_both_sides():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-foot.L", "head", [5, 0, -10],
                                    mirror="DEF-foot.R")
    assert report["ok"] is True
    left = rig.data.bones.get("DEF-foot.L")
    right = rig.data.bones.get("DEF-foot.R")
    assert round(left.head.x, 6) == 0.005
    # X flipped, Z the same: the same move on the other side of the midplane.
    assert round(right.head.x, 6) == -0.005
    assert round(left.head.z, 6) == round(right.head.z, 6) == 0.09
    assert report["mirror_bone"] == "DEF-foot.R"


def test_a_mirror_bone_that_is_not_on_the_rig_is_simply_not_moved():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-thigh.L", "head", [5, 0, 0],
                                    mirror="DEF-thigh.R")
    assert report["ok"] is True
    assert report["mirror_bone"] == ""
    assert [entry["bone"] for entry in report["moved"]] == ["DEF-thigh.L"]


def test_the_scenes_unit_scale_is_what_a_millimetre_means():
    rig = a_rig()
    run_joint_script([rig], "DEF-thigh.L", "head", [0, 0, -10], unit_scale=0.01)
    # 1 unit = 1 cm, so 10 mm is a whole unit.
    assert round(rig.data.bones.get("DEF-thigh.L").head.z, 6) == 0.0


def test_a_bone_that_is_not_on_the_rig_is_refused_by_name():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-nostril.L", "head", [0, 0, -1])
    assert report["ok"] is False
    assert report["kind"] == "no_bone"
    assert "DEF-nostril.L" in report["error"]


def test_a_scene_with_no_armature_says_so():
    mesh = FakeObject("body", "MESH")
    report, _ops = run_joint_script([], "DEF-foot.L", "head", [0, 0, -1],
                                    meshes=[mesh])
    assert report["ok"] is False
    assert report["kind"] == "no_rig"


def test_the_rig_bound_to_the_mesh_wins_when_two_have_the_same_bone():
    bound = a_rig("bound-rig")
    spare = a_rig("spare-rig")
    mesh = FakeObject("body", "MESH")
    mesh.modifiers.append(FakeModifier(bound))
    report, _ops = run_joint_script([bound, spare], "DEF-foot.L", "head",
                                    [0, 0, -10], meshes=[mesh])
    assert report["ok"] is True
    assert report["rig"] == "bound-rig"
    # â€¦and the one nobody is skinned to was not touched.
    assert round(spare.data.bones.get("DEF-foot.L").head.z, 6) == 0.1


def test_two_unbound_rigs_with_the_same_bone_ask_which():
    report, _ops = run_joint_script([a_rig("one"), a_rig("two")],
                                    "DEF-foot.L", "head", [0, 0, -1])
    assert report["ok"] is False
    assert report["kind"] == "ambiguous"
    assert sorted(report["rigs"]) == ["one", "two"]


def test_the_named_rig_is_the_one_that_is_edited():
    first, second = a_rig("one"), a_rig("two")
    report, _ops = run_joint_script([first, second], "DEF-foot.L", "head",
                                    [0, 0, -10], rig_name="two")
    assert report["ok"] is True and report["rig"] == "two"
    assert round(first.data.bones.get("DEF-foot.L").head.z, 6) == 0.1


def test_a_named_rig_that_is_not_there_is_refused():
    report, _ops = run_joint_script([a_rig("one")], "DEF-foot.L", "head",
                                    [0, 0, -1], rig_name="nope")
    assert report["ok"] is False and report["kind"] == "no_rig"


def test_the_edit_goes_through_edit_mode_and_comes_back_out():
    rig = a_rig()
    _report, ops = run_joint_script([rig], "DEF-foot.L", "head", [0, 0, -1])
    assert "EDIT" in ops
    assert ops[-1] == "OBJECT" or ops.index("EDIT") < len(ops) - 1
    assert rig.mode == "OBJECT"


def test_the_artists_own_selection_is_put_back():
    """Placing a joint must not move the selection out from under them."""
    rig = a_rig()
    mesh = FakeObject("body", "MESH")
    mesh.modifiers.append(FakeModifier(rig))
    mesh.select_set(True)
    report, _ops = run_joint_script([rig], "DEF-foot.L", "head", [0, 0, -1],
                                    meshes=[mesh])
    assert report["ok"] is True
    assert mesh.select_get() is True
    assert rig.select_get() is False


def test_the_report_carries_the_world_positions_of_everything_that_moved():
    rig = a_rig()
    report, _ops = run_joint_script([rig], "DEF-foot.L", "head", [0, 0, -10])
    positions = report["positions"]
    assert set(positions) == {"DEF-foot.L", "DEF-shin.L"}
    assert positions["DEF-foot.L"]["head"] == [0.0, 0.0, 0.09]
    assert positions["DEF-shin.L"]["tail"] == [0.0, 0.0, 0.09]


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------

def joint_responder(ok=True, kind="", error="", mirror_bone="DEF-foot.R"):
    """A fake add-on that answers a nudge without running any Blender."""
    def responder(request):
        report = ({"ok": True, "rig": "werewolf-rig", "bone": "DEF-foot.L",
                   "end": "head", "mirror_bone": mirror_bone,
                   "moved": [{"bone": "DEF-foot.L", "end": "head", "why": "nudged"},
                             {"bone": "DEF-shin.L", "end": "tail",
                              "why": "connected parent follows"}],
                   "positions": {"DEF-foot.L": {"head": [0, 0, 0.09],
                                                "tail": [0.1, 0, 0.09]}},
                   "unit_scale": 1.0}
                  if ok else {"ok": False, "kind": kind, "error": error})
        return {"id": request.get("id"), "status": "success",
                "result": {"output": "Info: editing\nFORGE_JOINT "
                                     + json.dumps(report) + "\n",
                           "result": None}}
    return responder


@pytest.fixture
def nudger(bridges, projects, fake_blender):
    """A bridge whose Blender answers nudges, over the werewolf fixture."""
    def factory(responder=None, symmetry="mirror_left"):
        folder = str(projects / "werewolf")
        if symmetry:
            write_task_config(folder, symmetry)
        server = fake_blender(responder or joint_responder())
        client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                    "FORGE_BLENDER_PORT": str(server.port)})
        return client, server
    return factory


def test_a_nudge_moves_the_bone_and_records_it(nudger, projects):
    client, server = nudger()
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head",
                 "delta_mm": [0, 0, -10]})
    assert status == 200, body
    assert body["project"] == "werewolf"
    assert body["rig"] == "werewolf-rig"
    assert body["delta_mm"] == [0.0, 0.0, -10.0]
    assert body["symmetry"] == "mirror_left"
    assert body["mirror"] is True
    assert "rig_check" in body["note"]

    code = server.seen[0]["params"]["code"]
    assert server.seen[0]["type"] == "execute_python"
    assert '"DEF-foot.L"' in code and '"DEF-foot.R"' in code

    journal = json.loads(
        (projects / "werewolf" / "design" / "artist-edits.json")
        .read_text(encoding="utf-8"))
    assert isinstance(journal, list)
    assert len(journal) == 1
    record = journal[0]
    assert record["bone"] == "DEF-foot.L"
    assert record["end"] == "head"
    assert record["delta_mm"] == [0.0, 0.0, -10.0]
    assert record["mirror"] is True
    assert record["source"] == "nudge"
    assert record["when"].endswith("Z")


def test_two_nudges_are_two_records(nudger, projects):
    client, _server = nudger()
    for step in (-2, -3):
        client.request("/projects/werewolf/joint_move",
                       payload={"bone": "DEF-foot.L", "end": "head",
                                "delta_mm": [0, 0, step]})
    journal = json.loads(
        (projects / "werewolf" / "design" / "artist-edits.json")
        .read_text(encoding="utf-8"))
    assert isinstance(journal, list)
    assert len(journal) == 2
    # In the order they happened, which is what an append-only trail is for.
    assert [record["delta_mm"][2] for record in journal] == [-2.0, -3.0]


@pytest.mark.parametrize("symmetry,mirrored", [
    ("mirror_left", True),
    ("mirror_right", True),
    ("as_designed", False),
])
def test_the_route_mirrors_by_the_projects_own_setting(nudger, symmetry,
                                                       mirrored):
    client, server = nudger(joint_responder(
        mirror_bone="DEF-foot.R" if mirrored else ""), symmetry=symmetry)
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [1, 0, 0]})
    assert status == 200, body
    assert body["mirror"] is mirrored
    code = server.seen[0]["params"]["code"]
    assert ('mirror_name = "DEF-foot.R"' in code) is mirrored


@pytest.mark.parametrize("asked,expected", [(True, True), (False, False)])
def test_an_explicit_mirror_beats_the_setting(nudger, asked, expected):
    client, server = nudger(joint_responder(
        mirror_bone="DEF-foot.R" if expected else ""), symmetry="as_designed")
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [1, 0, 0],
                 "mirror": asked})
    assert status == 200, body
    assert body["mirror"] is expected


def test_a_centre_bone_is_never_mirrored_however_symmetric_the_project(nudger):
    client, server = nudger(joint_responder(mirror_bone=""))
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-spine", "end": "tail", "delta_mm": [0, 0, 1],
                 "mirror": True})
    assert status == 200, body
    assert body["mirror"] is False
    assert 'mirror_name = ""' in server.seen[0]["params"]["code"]


@pytest.mark.parametrize("bone", INJECTIONS)
def test_the_route_refuses_a_bone_name_before_blender_is_asked_anything(
        nudger, bone, projects):
    client, server = nudger()
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": bone, "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 400, body
    assert "bone name" in body["error"]
    # The whole point of the ordering: no script was built, so Blender was
    # never handed one.
    assert server.seen == []
    assert not (projects / "werewolf" / "design" / "artist-edits.json").exists()


@pytest.mark.parametrize("payload,fragment", [
    ({"end": "head", "delta_mm": [0, 0, 1]}, "bone name"),
    ({"bone": "DEF-foot.L", "delta_mm": [0, 0, 1]}, "Which end"),
    ({"bone": "DEF-foot.L", "end": "middle", "delta_mm": [0, 0, 1]}, "Which end"),
    ({"bone": "DEF-foot.L", "end": "head"}, "delta_mm"),
    ({"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, 9000]}, "delta_mm"),
    ({"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, 0]}, "zero"),
    ({"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, 1],
      "rig": "not a name"}, "object name"),
])
def test_a_request_that_is_not_a_nudge_is_refused(nudger, payload, fragment):
    client, server = nudger()
    status, body = client.request("/projects/werewolf/joint_move",
                                  payload=payload)
    assert status == 400, body
    assert fragment in body["error"]
    assert server.seen == []


def test_the_route_needs_a_json_body(nudger):
    client, _server = nudger()
    status, body = client.request("/projects/werewolf/joint_move",
                                  payload=[1, 2, 3])
    assert status == 400
    assert "JSON object" in body["error"]


def test_a_project_that_is_not_there_is_a_404_and_asks_blender_nothing(nudger):
    client, server = nudger()
    status, _body = client.request("/projects/nope/joint_move",
                                   payload={"bone": "DEF-foot.L", "end": "head",
                                            "delta_mm": [0, 0, -1]})
    assert status == 404
    assert server.seen == []


def test_a_bone_blender_does_not_have_is_a_404(nudger, projects):
    client, _server = nudger(joint_responder(
        ok=False, kind="no_bone", error="'rig' has no bone called 'DEF-x.L'."))
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-x.L", "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 404, body
    assert "no bone called" in body["error"]
    # A move that did not happen is not written down.
    assert not (projects / "werewolf" / "design" / "artist-edits.json").exists()


def test_a_scene_with_no_rig_is_a_404(nudger):
    client, _server = nudger(joint_responder(
        ok=False, kind="no_rig",
        error="There is no armature in the Blender scene to nudge."))
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 404, body
    assert "no armature" in body["error"]


def test_two_rigs_with_the_same_bone_is_a_409(nudger):
    client, _server = nudger(joint_responder(
        ok=False, kind="ambiguous",
        error="2 armatures have a bone called 'DEF-foot.L'; say which rig."))
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 409, body


def test_a_nudge_with_blender_closed_says_so_and_writes_nothing(workspace,
                                                                projects):
    """The fixture's Blender port has nothing listening on it â€” the real path."""
    status, body = workspace.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 503, body
    assert "Blender is not running" in body["error"]
    assert body["blender"] is False
    assert not (projects / "werewolf" / "design" / "artist-edits.json").exists()


def test_an_addon_that_says_nothing_readable_is_a_502(bridges, projects,
                                                      fake_blender):
    def responder(request):
        return {"id": request.get("id"), "status": "success",
                "result": {"output": "Info: nothing", "result": None}}

    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request(
        "/projects/werewolf/joint_move",
        payload={"bone": "DEF-foot.L", "end": "head", "delta_mm": [0, 0, -1]})
    assert status == 502, body
    assert "could read back" in body["error"]


def test_the_nudge_route_answers_on_both_spellings_of_the_prefix(nudger):
    client, _server = nudger()
    for path in ("/projects/werewolf/joint_move", "/project/werewolf/joint_move"):
        status, _body = client.request(
            path, payload={"bone": "DEF-foot.L", "end": "head",
                           "delta_mm": [0, 0, -1]})
        assert status == 200, path


def test_a_nudge_leaves_the_build_plan_exactly_as_it_was(nudger, projects):
    client, _server = nudger()
    plan = projects / "werewolf" / "design" / "build-plan.json"
    before = plan.read_bytes()
    client.request("/projects/werewolf/joint_move",
                   payload={"bone": "DEF-foot.L", "end": "head",
                            "delta_mm": [0, 0, -10]})
    assert plan.read_bytes() == before


# ---------------------------------------------------------------------------
# the viewer's handle maths, on a real (tiny) rigged glb
# ---------------------------------------------------------------------------

def make_rigged_glb():
    """A skinned two-bone chain, with the second joint NESTED under the first.

    That nesting is the whole point of the fixture: ``DEF-shin.L`` sits at
    ``(0, 0.5, 0)`` in its parent's space and at ``(0, 1.5, 0)`` in the world,
    so a handle drawn at its local translation would be half a metre up the
    thigh â€” which is exactly the bug this test exists to catch.
    """
    import struct

    positions = struct.pack("<9f", 0, 1, 0, 0.1, 1.5, 0, 0, 2, 0)        # 36
    normals = struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)              # 36
    indices = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"                  # 6 (+2)
    joints = struct.pack("<12B", 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)     # 12
    weights = struct.pack("<12f", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)    # 48

    def ibm(y):
        flat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -y, 0, 1]
        return struct.pack("<16f", *flat)

    inverse = ibm(1.0) + ibm(1.5)                                        # 128
    blob = positions + normals + indices + joints + weights + inverse
    assert len(blob) == 268, len(blob)

    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 3]}],
        "nodes": [
            {"name": "rig", "children": [1]},
            {"name": "DEF-thigh.L", "children": [2], "translation": [0, 1, 0]},
            {"name": "DEF-shin.L", "translation": [0, 0.5, 0]},
            {"name": "body", "mesh": 0, "skin": 0},
        ],
        "meshes": [{"name": "body", "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1,
                           "JOINTS_0": 2, "WEIGHTS_0": 3},
            "indices": 4, "mode": 4}]}],
        "skins": [{"joints": [1, 2], "inverseBindMatrices": 5}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 1, 0], "max": [0.1, 2, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5121, "count": 3, "type": "VEC4"},
            {"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC4"},
            {"bufferView": 4, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 5, "componentType": 5126, "count": 2, "type": "MAT4"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 36},
            {"buffer": 0, "byteOffset": 80, "byteLength": 12},
            {"buffer": 0, "byteOffset": 92, "byteLength": 48},
            {"buffer": 0, "byteOffset": 72, "byteLength": 6},
            {"buffer": 0, "byteOffset": 140, "byteLength": 128},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }

    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    body = blob + b"\x00" * ((4 - len(blob) % 4) % 4)
    total = 12 + 8 + len(payload) + 8 + len(body)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(payload), 0x4E4F534A) + payload
    out += struct.pack("<II", len(body), 0x004E4942) + body
    return out


JOINT_HARNESS = r"""
const fs = require("fs");
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
console.log(JSON.stringify({
  skinned: model.skinned,
  handles: window.ForgeGLB.jointHandles(model),
  unfiltered: window.ForgeGLB.jointHandles(model, "").length,
  noneMatch: window.ForgeGLB.jointHandles(model, "CTRL-").length,
  names: ["DEF-foot.L", "DEF-shin.R", "DEF-upper_arm.L.001", "DEF-spine.005",
          "MCH-thing", "root", ""]
    .map(n => window.ForgeGLB.plainJointName(n)),
  // glTF is Y-up, Blender is Z-up: (x, y, z)_gltf -> (x, -z, y)_blender.
  blender: window.ForgeGLB.toBlenderMillimetres([0.001, 0.002, 0.003])
}));
"""


def run_joint_viewer(tmp_path, glb):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to run glbview.js with")
    harness = tmp_path / "joints.js"
    harness.write_text(JOINT_HARNESS, encoding="utf-8")
    blob = tmp_path / "rigged.glb"
    blob.write_bytes(glb)
    import subprocess
    done = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "glbview.js"), str(blob)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_handle_lands_on_every_joint_in_world_space(tmp_path):
    read = run_joint_viewer(tmp_path, make_rigged_glb())
    assert read["skinned"] == 1
    handles = read["handles"]
    assert [h["bone"] for h in handles] == ["DEF-thigh.L", "DEF-shin.L"]
    assert handles[0]["position"] == [0, 1, 0]
    # The nested one: 0.5 up from a parent that is 1.0 up, so 1.5 in the world
    # and NOT the 0.5 its own translation says.
    assert handles[1]["position"] == [0, 1.5, 0]


def test_every_handle_is_a_bones_head_and_the_last_one_is_a_leaf(tmp_path):
    read = run_joint_viewer(tmp_path, make_rigged_glb())
    handles = read["handles"]
    assert [h["end"] for h in handles] == ["head", "head"]
    assert [h["leaf"] for h in handles] == [False, True]


def test_the_handles_are_named_in_words_a_person_uses(tmp_path):
    read = run_joint_viewer(tmp_path, make_rigged_glb())
    assert [h["label"] for h in read["handles"]] == ["left hip", "left knee"]
    assert read["names"] == [
        "left ankle", "right knee", "left shoulder 2", "spine 6",
        "thing", "root", ""]


def test_a_prefix_that_matches_nothing_still_draws_the_rig(tmp_path):
    """An empty viewport looks like a bug, so a rig named otherwise still shows."""
    read = run_joint_viewer(tmp_path, make_rigged_glb())
    assert read["unfiltered"] == 2
    assert read["noneMatch"] == 2


def test_the_viewers_axes_are_converted_to_blenders(tmp_path):
    read = run_joint_viewer(tmp_path, make_rigged_glb())
    # 1 mm right, 2 mm up and 3 mm toward the viewer in the viewer's own axes
    # is 1 mm X, -3 mm Y and 2 mm Z in Blender's.
    assert read["blender"] == [1, -3, 2]


def test_a_glb_with_no_skeleton_has_no_handles(tmp_path):
    """No skin, no joints â€” and honestly none, rather than a guess at some."""
    read = run_joint_viewer(tmp_path, make_glb())
    assert read["handles"] == []


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_the_nudge_controls_are_on_the_page(client):
    html = fetch_text(client, "/")
    for anchor in ("ws-nudge", "ws-nudge-bar", "ws-nudge-name", "ws-nudge-bone",
                   "ws-axis-up", "ws-axis-forward", "ws-axis-side",
                   "ws-nudge-mirror", "ws-nudge-mm", "ws-nudge-scale",
                   "ws-nudge-cancel", "ws-nudge-apply", "ws-nudge-status"):
        assert ('id="%s"' % anchor) in html, anchor
    # It starts hidden: the default screen is still the simple one.
    bar = html.split('id="ws-nudge-bar"', 1)[1].split(">", 1)[0]
    assert "hidden" in bar


def test_the_nudge_offers_single_axes_rather_than_free_dragging(client):
    """"Up a bit" is the request, and a one-axis drag cannot go sideways."""
    script = fetch_text(client, "/webui/glbview.js")
    axes = script.split("var AXES = {", 1)[1].split("};", 1)[0]
    assert '"up / down"' in axes
    assert '"forward / back"' in axes
    assert '"side to side"' in axes
    html = fetch_text(client, "/")
    assert 'data-axis="up"' in html
    assert 'data-axis="forward"' in html


def test_the_readout_gives_millimetres_and_something_to_measure_them_against(
        client):
    """"I don't know how much 10mm is here" â€” so the mm never travels alone."""
    script = fetch_text(client, "/webui/app.js")
    assert "function nudgeScaleText(" in script
    assert "of him" in script
    assert 'read.mm.toFixed(1) + " mm"' in script
    # …and the distance never carries a direction with it: a drag can run
    # along two axes one after the other, so "186 mm side to side" when
    # 124 mm of it was vertical is a lie the viewport's own line avoids.
    assert 'read.mm.toFixed(1) + " mm " + read.axisLabel' not in script
    viewer = fetch_text(client, "/webui/glbview.js")
    # The drawn half of the answer: a grey ghost where the drag started.
    assert "HANDLE_WAS" in viewer
    assert "state.origin" in viewer


def test_applying_a_nudge_re_snapshots_rather_than_trusting_the_drag(client):
    """What is on screen afterwards has to be what Blender did."""
    script = fetch_text(client, "/webui/app.js")
    apply_fn = script.split("function wsApplyNudge(", 1)[1] \
                     .split("\n  function ", 1)[0]
    assert "/joint_move" in apply_fn
    assert "wsSnapshot()" in apply_fn
    assert "res.data.note" in apply_fn


def test_escape_abandons_a_placement(client):
    script = fetch_text(client, "/webui/app.js")
    assert 'event.key !== "Escape"' in script
    assert "wsCancelNudge" in script


def test_the_nudge_styles_ship_with_the_stylesheet(client):
    css = fetch_text(client, "/webui/app.css")
    for rule in (".ws-nudge", ".ws-nudge-mm", ".btn.ws-axis"):
        assert rule in css, rule


# ===========================================================================
# Phase 20 â€” the stage as an inspection surface, and the two tiers
# ===========================================================================
#
# Canned reports, shaped exactly as the add-on's own commands return them:
# `verify_design`'s axes with `mesh_diagnose` under the defects one,
# `rig_check`'s placement block, and `animation_check`'s gates.  The
# normaliser is a pure function over these, so what is pinned on a model is
# testable without a Blender anywhere.

VERIFY_REPORT = {
    "object": "werewolf-form-a_retopo",
    "axes": {
        "defects": {
            "verdict": "attention",
            "says": "608 clipping, 18 ngons",
            "tier": "measured",
            "detail": {
                "face_count": 15558,
                "self_intersections": {
                    "count": 2, "faces": 4, "scanned": True,
                    "examples": [
                        {"location_mm": [10.0, -20.0, 1700.0], "faces": [3, 9]},
                        {"location_mm": [-10.0, -20.0, 1700.0], "faces": [4, 8]},
                    ]},
                "zero_area_faces": {
                    "count": 1,
                    "examples": [{"location_mm": [0.0, 0.0, 900.0], "face": 7}]},
                "ngons": {"count": 18, "max_sides": 6, "examples": []},
            },
        },
        "poly_budget": {
            "verdict": "pass", "says": "15558 faces, inside the 15000 budget",
            "faces": {"value": 15558, "tier": "measured"},
            "budget": {"value": 15000, "tier": "measured"},
        },
        "uv": {"verdict": "attention", "says": "75 flipped faces",
               "present": {"value": True, "tier": "measured"}},
    },
}

RIG_REPORT = {
    "rig": "werewolf-form-a_retopo_rig",
    "placement": {
        "centering": {
            "verdict": "attention",
            "bones": [
                {"bone": "DEF-thigh.L", "gated": True, "verdict": "attention",
                 "worst_offset_mm": 38.6, "worst_offset_pct_of_radius": 54.1,
                 "head_offset_mm": 12.0, "worst_at_fraction": 0.5},
                {"bone": "DEF-shin.L", "gated": True, "verdict": "ok",
                 "worst_offset_mm": 2.0, "worst_offset_pct_of_radius": 9.6},
            ]},
        "asymmetry": {"verdict": "ok", "worst_mm": 0.0,
                      "says": "every .R bone mirrors its .L twin"},
        "side_naming": {"verdict": "ok", "wrong": 0, "of": 26,
                        "says": "0/26 wrong"},
        "overlap": {
            "verdict": "fail", "says": "stray mass 24.37",
            "stray_mass": 24.37, "outlier_pairs": 8,
            "outliers": [
                {"a": "DEF-breast.L", "b": "DEF-pelvis.R", "gap_mm": 382.0,
                 "shared_vertices": 320},
            ]},
        "continuity": {
            "verdict": "attention", "says": "376 holes / 20064 region verts",
            "holes": 376, "region_vertices": 20064, "hole_pct": 1.87,
            "bones": [
                {"bone": "DEF-pelvis.R", "verdict": "fail", "holes": 94,
                 "hole_pct": 4.7},
                {"bone": "DEF-foot.L", "verdict": "ok", "holes": 1,
                 "hole_pct": 0.1},
            ]},
        "bend_direction": {"verdict": "fail", "says": "the knees fold backwards",
                           "worst_bone": "DEF-shin.R"},
    },
}

ANIMATE_REPORT = {
    "action": "walk-loop", "mode": "in_place",
    "gate": {"verdict": "ok", "says": "feet hold to 1.1 mm",
             "worst_drift_mm": 1.1, "threshold_ok_mm": 5.0, "worst_frame": 12},
    "deformation_gate": {"verdict": "fail", "says": "the legs stretch 32%",
                         "worst_stretch_pct": 32.0, "worst_frame": 19},
    "loop_seam_closure": {"verdict": "attention", "says": "seam is 3.2 mm open",
                          "max_mm": 3.2},
}


# ---------------------------------------------------------------------------
# severity, and the verdict words every gate in Forge writes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verdict,severity", [
    ("ok", "ok"),
    ("pass", "ok"),
    ("attention", "attention"),
    ("attention overall, 376 holes / 20064 region verts = 1.87%", "attention"),
    ("fail", "fail"),
    ("fail, stray_mass 24.37 (threshold ok<=0.05)", "fail"),
    ("needs_attention (documented, pre-existing)", "attention"),
    ("unmeasured", "unknown"),
    ("not_applicable", "none"),
    ("", "unknown"),
    (None, "unknown"),
    ("marinating", "unknown"),
])
def test_a_gates_verdict_becomes_one_of_four_colours(verdict, severity):
    assert bridge.severity_of(verdict) == severity


# ---------------------------------------------------------------------------
# verify_mesh: the defects that carry a place on the model
# ---------------------------------------------------------------------------

def test_a_located_defect_becomes_a_pin_where_it_was_measured():
    """``mesh_diagnose`` reports ``location_mm``; that is the whole feature."""
    findings = bridge.normalize_verify(VERIFY_REPORT)
    clipping = [one for one in findings
                if one["gate"] == "self_intersections"]
    assert len(clipping) == 2
    # World millimetres in the report, world metres out of the bridge.
    assert clipping[0]["world_pos"] == [0.01, -0.02, 1.7]
    assert clipping[1]["world_pos"] == [-0.01, -0.02, 1.7]
    assert clipping[0]["severity"] == "attention"
    # Which two faces are passing through each other is what somebody who
    # clicked the pin came to find out.
    assert clipping[0]["numbers"]["faces"] == [3, 9]
    assert "1 of 2" in clipping[0]["label"]
    # Two examples of one defect are two pins, not one.
    assert clipping[0]["id"] != clipping[1]["id"]


def test_a_defect_that_was_counted_but_not_located_still_lists():
    """18 ngons with no examples is still worth saying, just not pinnable."""
    findings = bridge.normalize_verify(VERIFY_REPORT)
    ngons = [one for one in findings if one["gate"] == "ngons"]
    assert len(ngons) == 1
    assert ngons[0]["world_pos"] is None
    assert ngons[0]["numbers"]["count"] == 18


def test_the_other_axes_list_with_their_own_numbers():
    findings = bridge.normalize_verify(VERIFY_REPORT)
    budget = [one for one in findings if one["gate"] == "poly_budget"][0]
    assert budget["severity"] == "ok"
    assert budget["world_pos"] is None
    # A claim is {"value", "tier"}; the value is the part a person reads.
    assert budget["numbers"]["faces"] == 15558
    assert budget["numbers"]["budget"] == 15000
    uv = [one for one in findings if one["gate"] == "uv"][0]
    assert uv["severity"] == "attention"


def test_only_the_defect_that_merging_fixes_offers_a_one_click_fix():
    """Everything else on a mesh is a judgement, so it stays a conversation."""
    findings = bridge.normalize_verify(VERIFY_REPORT)
    fixable = [one for one in findings if one["fix"]]
    assert [one["gate"] for one in fixable] == ["zero_area_faces"]
    assert fixable[0]["fix"]["op"] == "merge_doubles"
    assert fixable[0]["fix"]["op"] in bridge.MESH_FIX_OPS
    clipping = [one for one in findings if one["gate"] == "self_intersections"]
    assert all(one["fix"] is None for one in clipping)


# ---------------------------------------------------------------------------
# rig and skin: the gates that name a bone
# ---------------------------------------------------------------------------

def test_a_rig_finding_names_the_bone_it_measured():
    """No coordinates are invented: bone_centering reports a NAME, so that is
    what comes out, and the viewer places it on the joint it already drew."""
    findings = bridge.normalize_rig(RIG_REPORT)
    centering = [one for one in findings if one["gate"] == "centering"]
    assert [one["bone"] for one in centering] == ["DEF-thigh.L"]
    assert centering[0]["world_pos"] is None
    assert centering[0]["severity"] == "attention"
    assert centering[0]["numbers"]["worst_offset_pct_of_radius"] == 54.1


def test_a_green_bone_is_not_a_finding():
    findings = bridge.normalize_rig(RIG_REPORT)
    assert "DEF-shin.L" not in [one["bone"] for one in findings]
    assert "asymmetry" not in [one["gate"] for one in findings]
    assert "side_naming" not in [one["gate"] for one in findings]


def test_a_failing_placement_gate_lists_with_its_worst_bone():
    findings = bridge.normalize_rig(RIG_REPORT)
    bend = [one for one in findings if one["gate"] == "bend_direction"][0]
    assert bend["severity"] == "fail"
    assert bend["bone"] == "DEF-shin.R"
    assert "backwards" in bend["label"]


def test_the_skin_gates_come_out_of_the_same_run():
    """rig_check measures placement and skinning in one pass."""
    findings = bridge.normalize_skin(RIG_REPORT)
    gates = [one["gate"] for one in findings]
    assert "overlap" in gates and "continuity" in gates
    # â€¦and the rig stage does not claim them.
    assert "overlap" not in [one["gate"] for one in bridge.normalize_rig(RIG_REPORT)]


def test_an_overlap_finding_names_both_bones_and_pins_on_the_first():
    findings = bridge.normalize_skin(RIG_REPORT)
    overlap = [one for one in findings if one["gate"] == "overlap"][0]
    assert overlap["bone"] == "DEF-breast.L"
    assert overlap["bones"] == ["DEF-breast.L", "DEF-pelvis.R"]
    assert overlap["numbers"]["gap_mm"] == 382.0
    assert overlap["severity"] == "fail"


def test_a_continuity_hole_pins_on_the_bone_whose_region_it_is_in():
    findings = bridge.normalize_skin(RIG_REPORT)
    holes = [one for one in findings if one["gate"] == "continuity"]
    assert [one["bone"] for one in holes] == ["DEF-pelvis.R"]
    assert holes[0]["numbers"]["hole_pct"] == 4.7
    assert "smooth it locally" in holes[0]["fix_hint"]


# ---------------------------------------------------------------------------
# animate
# ---------------------------------------------------------------------------

def test_the_clip_gates_that_failed_are_the_findings():
    findings = bridge.normalize_animate(ANIMATE_REPORT)
    gates = dict((one["gate"], one) for one in findings)
    assert "deformation_gate" in gates
    assert gates["deformation_gate"]["severity"] == "fail"
    assert gates["deformation_gate"]["numbers"]["worst_frame"] == 19
    assert gates["loop_seam_closure"]["severity"] == "attention"
    # The foot-slide gate passed, so it is not a finding.
    assert "gate" not in gates


def test_a_clip_that_passes_everything_says_so_with_its_numbers():
    findings = bridge.normalize_animate(
        {"gate": "ok", "worst_drift_mm": 1.1, "action": "walk-loop"})
    assert len(findings) == 1
    assert findings[0]["severity"] == "ok"
    assert findings[0]["numbers"]["worst_drift_mm"] == 1.1


# ---------------------------------------------------------------------------
# the normaliser never takes the page down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report", [
    {}, {"axes": None}, {"axes": []}, {"placement": "nope"},
    {"placement": {"centering": {"bones": "no"}}},
    {"axes": {"defects": {"detail": {"self_intersections": {"examples": 3}}}}},
    {"axes": {"defects": {"detail": {"self_intersections": {
        "count": 1, "examples": [{"location_mm": ["a", "b", "c"]}]}}}}},
])
def test_a_report_shaped_oddly_is_no_findings_rather_than_a_crash(report):
    for stage in ("verify_mesh", "rig", "skin", "animate"):
        out = bridge.normalize_findings(stage, report)
        assert isinstance(out, list)


def test_a_stage_with_no_check_has_no_normaliser():
    assert bridge.normalize_findings("design", VERIFY_REPORT) == []
    assert bridge.normalize_findings("", VERIFY_REPORT) == []


def test_the_summary_counts_what_the_pins_will_be():
    findings = bridge.normalize_skin(RIG_REPORT)
    counts = bridge.findings_summary(findings)
    assert counts["fail"] >= 2
    assert sum(counts.values()) == len(findings)


# ---------------------------------------------------------------------------
# the authoring table: mirrored from the contracts, never invented
# ---------------------------------------------------------------------------
#
# A canned copy of what `mcp/forge_mcp/server.py` declares and what
# `addon/forge/tools/rigforge_anim.py` bounds them by.  If either moves, this
# fails â€” which is the point: a slider offering a parameter the authoring tool
# does not have is a turn that dies with a confusing error.

MCP_CONTRACTS = {
    "walk": {
        "command": "rigforge_walk",
        "names": {"cycle_frames", "step_length", "step_height",
                  "stance_fraction", "hip_drop", "hip_sway", "hip_twist_deg",
                  "arm_swing_deg", "elbow_bend_deg", "foot_roll_deg", "travel",
                  "loop", "interpolation", "stride_width"},
        "bounds": {"cycle_frames": (4, 600), "stance_fraction": (0.2, 0.95)},
        "defaults": {"cycle_frames": 32, "stance_fraction": 0.62,
                     "travel": True, "loop": True, "interpolation": "LINEAR"},
    },
    "punch": {
        "command": "rigforge_punch",
        "names": {"side", "frames", "strike_fraction", "lead_frames",
                  "target_distance", "target_height", "hip_rotation_deg",
                  "chest_rotation_deg", "shoulder_rotation_deg", "weight_shift",
                  "guard_rise", "chamber_draw", "loop", "interpolation"},
        "bounds": {"frames": (8, 600), "strike_fraction": (0.15, 0.85),
                   "hip_rotation_deg": (0.0, 60.0),
                   "chest_rotation_deg": (0.0, 60.0),
                   "shoulder_rotation_deg": (0.0, 60.0)},
        "defaults": {"frames": 24, "strike_fraction": 0.45, "loop": False,
                     "interpolation": "LINEAR"},
    },
    "jump": {
        "command": "rigforge_jump",
        "names": {"frames", "apex_height", "jump_distance", "crouch_depth",
                  "landing_depth", "anticipation_fraction", "gravity",
                  "chest_pitch_deg", "arm_swing_back_deg", "arm_swing_up_deg",
                  "elbow_bend_deg", "foot_roll_deg", "loop", "interpolation"},
        "bounds": {"frames": (12, 600), "anticipation_fraction": (0.05, 0.5),
                   "gravity": (0.1, 100.0)},
        "defaults": {"frames": 36, "jump_distance": 0.0, "gravity": 9.81,
                     "loop": False, "interpolation": "LINEAR"},
    },
}


@pytest.mark.parametrize("kind", sorted(MCP_CONTRACTS))
def test_the_param_table_matches_the_tool_that_owns_it(kind):
    contract = MCP_CONTRACTS[kind]
    table = bridge.AUTHORING_PARAMS[kind]
    assert table["command"] == contract["command"]
    names = set(row["name"] for row in table["params"])
    # Every name this panel offers is a parameter the wrapper really takes.
    assert names <= contract["names"], names - contract["names"]
    rows = dict((row["name"], row) for row in table["params"])
    for name, (low, high) in contract["bounds"].items():
        assert name in rows, name
        assert rows[name]["min"] == low, name
        assert rows[name]["max"] == high, name
    for name, value in contract["defaults"].items():
        assert rows[name]["default"] == value, name


def test_a_parameter_with_no_default_says_it_comes_off_the_rig():
    """There is no number to show until the tool has run, so none is shown."""
    for kind, table in bridge.AUTHORING_PARAMS.items():
        for row in table["params"]:
            if row["default"] is None and row["kind"] in ("int", "float"):
                assert row["note"] or row["min"] is not None, (kind, row["name"])


def test_every_row_declares_a_kind_the_ui_can_draw():
    for table in bridge.AUTHORING_PARAMS.values():
        for row in table["params"]:
            assert row["kind"] in ("int", "float", "bool", "choice")
            if row["kind"] == "choice":
                assert row["choices"]


@pytest.mark.parametrize("name,kind", [
    ("walk-loop", "walk"), ("punch.R", "punch"), ("JUMP-01", "jump"),
    ("take-14", ""), ("", ""), ("idle", ""),
])
def test_a_clips_name_suggests_which_tool_authored_it(name, kind):
    assert bridge.authoring_kind(name) == kind


# ---------------------------------------------------------------------------
# the direct tier: authoring
# ---------------------------------------------------------------------------

def test_a_number_inside_the_bounds_is_passed_through():
    params, error = bridge.clean_authoring_params(
        "punch", {"strike_fraction": 0.5, "frames": 30})
    assert error == ""
    assert params == {"strike_fraction": 0.5, "frames": 30}


def test_an_int_parameter_arrives_as_an_int():
    params, _error = bridge.clean_authoring_params("walk", {"cycle_frames": 40.0})
    assert params["cycle_frames"] == 40
    assert isinstance(params["cycle_frames"], int)


@pytest.mark.parametrize("kind,values,fragment", [
    ("punch", {"strike_fraction": 0.9}, "at most 0.85"),
    ("punch", {"strike_fraction": 0.01}, "at least 0.15"),
    ("walk", {"cycle_frames": 2}, "at least 4"),
    ("walk", {"cycle_frames": 9999}, "at most 600"),
    ("punch", {"nope": 1}, "not a parameter"),
    ("punch", {"frames": "lots"}, "is a number"),
    ("punch", {"frames": True}, "is a number"),
    ("walk", {"travel": 1}, "yes/no"),
    ("walk", {"interpolation": "SPLINE"}, "one of"),
    ("cartwheel", {"frames": 10}, "not something this panel can author"),
    ("walk", {}, "nothing to author"),
    ("walk", "frames=10", "JSON object"),
])
def test_a_parameter_the_tool_would_refuse_is_refused_here_first(kind, values,
                                                                 fragment):
    params, error = bridge.clean_authoring_params(kind, values)
    assert params is None
    assert fragment in error


def test_a_none_means_leave_it_to_the_rig():
    params, error = bridge.clean_authoring_params(
        "walk", {"step_length": None, "cycle_frames": 24})
    assert error == ""
    assert params == {"cycle_frames": 24}


# ---------------------------------------------------------------------------
# the direct tier: the weight brush and the mesh repair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("radius,ok", [
    (5, True), (40, True), (200, True),
    (4.9, False), (201, False), (0, False), (-10, False),
    ("40", False), (True, False), (None, False), (float("nan"), False),
])
def test_the_brush_radius_is_bounded(radius, ok):
    assert (bridge.brush_radius(radius) is not None) is ok


@pytest.mark.parametrize("point,ok", [
    ([0, 1, 2], True), ([0.0, -1.5, 2.25], True),
    ([0, 1], False), ([0, 1, 2, 3], False), ("0,1,2", False),
    ([0, 1, "2"], False), ([0, 1, True], False),
    ([0, 1, float("inf")], False), ([0, 1, 10000], False), (None, False),
])
def test_a_brush_point_is_three_real_metres(point, ok):
    assert (bridge.world_point(point) is not None) is ok


def test_the_brush_script_is_always_valid_python():
    code = bridge.WEIGHTS_LOCAL_SCRIPT % tuple(json.dumps(one) for one in [
        "body", "rig", [0.0, 0.1, 0.2], 0.04, "smooth", "DEF-foot.L",
        4, 0.5, 0.65, 20000])
    compile(code, "<brush>", "exec")
    assert '"DEF-foot.L"' in code
    assert "FORGE_WEIGHTS" in code


def test_the_brush_template_has_exactly_its_own_holes():
    holes = bridge.WEIGHTS_LOCAL_SCRIPT.replace("%%", "")
    assert holes.count("%s") == 10
    with pytest.raises(TypeError):
        bridge.WEIGHTS_LOCAL_SCRIPT % (json.dumps("a"),)


def test_only_one_mesh_repair_is_deterministic_enough_to_be_a_button():
    """The reasons the others are not are the design, so they are pinned."""
    assert sorted(bridge.MESH_FIX_OPS) == ["merge_doubles"]
    spec = bridge.MESH_FIX_OPS["merge_doubles"]
    assert spec["command"] == "merge_by_distance"
    assert spec["param"] == "distance_mm"
    assert spec["min"] < spec["default"] < spec["max"]


def test_the_brush_does_exactly_two_things():
    assert sorted(bridge.WEIGHT_OPS) == ["harden", "smooth"]


# ---------------------------------------------------------------------------
# "changed since last measure"
# ---------------------------------------------------------------------------

def test_a_stage_measured_after_the_last_edit_is_not_dirty(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    bridge.append_artist_edit(folder, {
        "when": "2020-01-01T00:00:00Z", "bone": "DEF-foot.L", "end": "head",
        "delta_mm": [0, 0, -1], "mirror": False, "source": "nudge"})
    board = bridge.pipeline_board(folder, "werewolf")
    assert bridge.dirty_stages(folder, board) == {}


def test_a_stage_measured_before_the_last_edit_is_dirty(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    bridge.append_artist_edit(folder, {
        "when": "2099-01-01T00:00:00Z", "bone": "DEF-foot.L", "end": "head",
        "delta_mm": [0, 0, -1], "mirror": False, "source": "nudge"})
    board = bridge.pipeline_board(folder, "werewolf")
    dirty = bridge.dirty_stages(folder, board)
    # rig and skin both carry history in the fixture plan; reference does not.
    assert "rig" in dirty and "skin" in dirty
    assert "reference" not in dirty
    assert dirty["rig"]["edited"] > dirty["rig"]["since"]


def test_a_project_that_has_never_been_hand_edited_is_never_dirty(tmp_path):
    folder = make_project(tmp_path, "werewolf", plan=a_plan())
    board = bridge.pipeline_board(folder, "werewolf")
    assert bridge.dirty_stages(folder, board) == {}


def test_the_board_carries_the_badge_and_what_can_be_checked(nudger):
    client, _server = nudger()
    status, body = client.request("/projects/werewolf/pipeline")
    assert status == 200
    assert isinstance(body["dirty"], dict)
    assert sorted(body["inspectable"]) == ["animate", "rig", "skin",
                                           "verify_mesh"]


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

def inspect_responder(report, command=None):
    """A fake add-on that answers a check command with a canned report."""
    def responder(request):
        if command and request.get("type") != command:
            return {"id": request.get("id"), "status": "error",
                    "message": "unexpected command %r" % request.get("type")}
        return {"id": request.get("id"), "status": "success", "result": report}
    return responder


@pytest.mark.parametrize("stage,command,report,gate", [
    ("verify_mesh", "verify_design", VERIFY_REPORT, "self_intersections"),
    ("rig", "rig_check", RIG_REPORT, "centering"),
    ("skin", "rig_check", RIG_REPORT, "overlap"),
])
def test_inspect_runs_the_stages_own_command(bridges, projects, fake_blender,
                                             stage, command, report, gate):
    server = fake_blender(inspect_responder(report))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/inspect",
                                  payload={"stage": stage})
    assert status == 200, body
    assert body["stage"] == stage
    assert body["command"] == command
    assert server.seen[0]["type"] == command
    assert gate in [one["gate"] for one in body["findings"]]
    assert body["ran_at"].endswith("Z")
    assert body["note"]


def test_inspect_on_a_clip_needs_the_clips_name(bridges, projects,
                                                fake_blender):
    server = fake_blender(inspect_responder(ANIMATE_REPORT))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/inspect",
                                  payload={"stage": "animate"})
    assert status == 400
    assert "Which clip" in body["error"]
    assert server.seen == []

    status, body = client.request(
        "/projects/werewolf/inspect",
        payload={"stage": "animate", "action": "walk-loop"})
    assert status == 200, body
    assert server.seen[0]["type"] == "animation_check"
    assert server.seen[0]["params"]["action"] == "walk-loop"


def test_inspect_counts_what_can_be_placed(bridges, projects, fake_blender):
    server = fake_blender(inspect_responder(VERIFY_REPORT))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    body = client.request("/projects/werewolf/inspect",
                          payload={"stage": "verify_mesh"})[1]
    placed = [one for one in body["findings"] if one["world_pos"]]
    assert body["positioned"] == len(placed)
    assert placed


@pytest.mark.parametrize("stage", ["design", "", "generate", "../rig"])
def test_a_stage_with_no_check_is_refused(bridges, projects, fake_blender,
                                          stage):
    server = fake_blender(inspect_responder(RIG_REPORT))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/inspect",
                                  payload={"stage": stage})
    assert status == 400, body
    assert "no check to run" in body["error"]
    assert server.seen == []


@pytest.mark.parametrize("name", NAMED_INJECTIONS)
def test_inspect_refuses_a_name_before_blender_is_asked(bridges, projects,
                                                        fake_blender, name):
    server = fake_blender(inspect_responder(RIG_REPORT))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request(
        "/projects/werewolf/inspect",
        payload={"stage": "rig", "object": name})
    assert status == 400, body
    assert server.seen == []


def test_inspect_with_blender_closed_says_so(workspace):
    status, body = workspace.request("/projects/werewolf/inspect",
                                     payload={"stage": "rig"})
    assert status == 503, body
    assert "Blender is not running" in body["error"]


def test_the_authoring_table_is_served(client):
    status, body = client.request("/authoring")
    assert status == 200
    assert sorted(body["kinds"]) == ["jump", "punch", "walk"]
    assert body["kinds"]["punch"]["command"] == "rigforge_punch"
    assert body["note"]


def test_authoring_re_authors_and_journals(bridges, projects, fake_blender):
    server = fake_blender(inspect_responder(
        {"action": "punch.R", "keys": 42, "frames": 24}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/author", payload={
        "kind": "punch", "action": "punch.R",
        "params": {"strike_fraction": 0.5}})
    assert status == 200, body
    assert body["command"] == "rigforge_punch"
    assert server.seen[0]["type"] == "rigforge_punch"
    assert server.seen[0]["params"]["strike_fraction"] == 0.5
    assert server.seen[0]["params"]["action"] == "punch.R"

    journal = json.loads(
        (projects / "werewolf" / "design" / "artist-edits.json")
        .read_text(encoding="utf-8"))
    assert isinstance(journal, list)
    record = journal[-1]
    assert record["source"] == "author-panel"
    assert record["kind"] == "punch"
    assert record["action"] == "punch.R"
    assert record["params"] == {"strike_fraction": 0.5}
    assert record["when"].endswith("Z")


def test_authoring_refuses_out_of_bounds_before_blender(bridges, projects,
                                                        fake_blender):
    server = fake_blender(inspect_responder({}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/author", payload={
        "kind": "punch", "params": {"strike_fraction": 2.0}})
    assert status == 400, body
    assert "at most" in body["error"]
    assert server.seen == []
    assert not (projects / "werewolf" / "design" / "artist-edits.json").exists()


@pytest.mark.parametrize("name", NAMED_INJECTIONS)
def test_authoring_refuses_an_action_name_that_could_be_code(bridges, projects,
                                                             fake_blender, name):
    server = fake_blender(inspect_responder({}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, _body = client.request("/projects/werewolf/author", payload={
        "kind": "walk", "action": name, "params": {"cycle_frames": 30}})
    assert status == 400
    assert server.seen == []


def test_the_brush_applies_and_journals(bridges, projects, fake_blender):
    def responder(request):
        code = (request.get("params") or {}).get("code") or ""
        assert "FORGE_WEIGHTS" in code
        report = {"ok": True, "mesh": "body", "rig": "rig", "op": "smooth",
                  "bone": "DEF-foot.L", "vertices": 120, "changed": 118,
                  "radius_m": 0.04}
        return {"id": request.get("id"), "status": "success",
                "result": {"output": "FORGE_WEIGHTS " + json.dumps(report),
                           "result": None}}

    server = fake_blender(responder)
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/weights_local", payload={
        "world_pos": [0.1, 0.0, 0.9], "radius_mm": 40, "op": "smooth",
        "bone": "DEF-foot.L"})
    assert status == 200, body
    assert body["changed"] == 118
    code = server.seen[0]["params"]["code"]
    assert 'op = "smooth"' in code
    assert 'only_bone = "DEF-foot.L"' in code
    assert "radius_m = 0.04" in code

    journal = json.loads(
        (projects / "werewolf" / "design" / "artist-edits.json")
        .read_text(encoding="utf-8"))
    record = journal[-1]
    assert record["source"] == "weights-brush"
    assert record["op"] == "smooth"
    assert record["radius_mm"] == 40.0
    assert record["world_pos"] == [0.1, 0.0, 0.9]


@pytest.mark.parametrize("payload,fragment", [
    ({"world_pos": [0, 0, 1], "radius_mm": 40, "op": "melt"}, "brush does"),
    ({"world_pos": [0, 0], "radius_mm": 40, "op": "smooth"}, "world_pos"),
    ({"world_pos": [0, 0, 1], "radius_mm": 1, "op": "smooth"}, "radius_mm"),
    ({"world_pos": [0, 0, 1], "radius_mm": 900, "op": "smooth"}, "radius_mm"),
    ({"world_pos": [0, 0, 1], "radius_mm": 40, "op": "smooth",
      "bone": "a b"}, "bone name"),
])
def test_the_brush_refuses_what_it_cannot_do(bridges, projects, fake_blender,
                                             payload, fragment):
    server = fake_blender(inspect_responder({}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/weights_local",
                                  payload=payload)
    assert status == 400, body
    assert fragment in body["error"]
    assert server.seen == []


def test_the_mesh_fix_merges_and_journals(bridges, projects, fake_blender):
    server = fake_blender(inspect_responder({"removed": 42, "object": "body"}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/mesh_fix", payload={
        "finding_id": "verify_mesh:zero_area_faces:0",
        "op": "merge_doubles", "distance_mm": 0.2})
    assert status == 200, body
    assert server.seen[0]["type"] == "merge_by_distance"
    assert server.seen[0]["params"]["distance"] == pytest.approx(0.0002)
    assert body["report"]["removed"] == 42
    journal = json.loads(
        (projects / "werewolf" / "design" / "artist-edits.json")
        .read_text(encoding="utf-8"))
    assert journal[-1]["source"] == "mesh-fix"
    assert journal[-1]["finding_id"] == "verify_mesh:zero_area_faces:0"


@pytest.mark.parametrize("payload,fragment", [
    ({"op": "delete_island"}, "not a one-click repair"),
    ({"op": "fill_hole"}, "not a one-click repair"),
    ({"op": ""}, "not a one-click repair"),
    ({"op": "merge_doubles", "distance_mm": 99}, "between"),
    ({"op": "merge_doubles", "distance_mm": "near"}, "is a number"),
])
def test_a_repair_that_is_a_judgement_is_not_a_button(bridges, projects,
                                                      fake_blender, payload,
                                                      fragment):
    server = fake_blender(inspect_responder({}))
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, body = client.request("/projects/werewolf/mesh_fix",
                                  payload=payload)
    assert status == 400, body
    assert fragment in body["error"]
    assert server.seen == []


def test_the_heatmap_bone_goes_through_the_bone_alphabet(bridges, projects,
                                                         fake_blender):
    server = fake_blender(snapshot_responder())
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects),
                                "FORGE_BLENDER_PORT": str(server.port)})
    status, _body = client.request("/projects/werewolf/snapshot",
                                   payload={"weight_bone": "DEF foot L"})
    assert status == 400
    assert server.seen == []

    status, body = client.request("/projects/werewolf/snapshot",
                                  payload={"weight_bone": "DEF-foot.L"})
    assert status == 200, body
    assert 'weight_bone = "DEF-foot.L"' in server.seen[-1]["params"]["code"]


def test_the_snapshot_script_still_compiles_with_a_weight_bone():
    code = bridge.SNAPSHOT_SCRIPT % (json.dumps("C:/t/x.glb"), json.dumps(""),
                                     json.dumps("DEF-foot.L"))
    compile(code, "<snap>", "exec")
    holes = bridge.SNAPSHOT_SCRIPT.replace("%%", "")
    assert holes.count("%s") == 3


# ---------------------------------------------------------------------------
# the viewer: pins on the model, and the clip controls
# ---------------------------------------------------------------------------

PIN_HARNESS = r"""
const fs = require("fs");
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
const handles = window.ForgeGLB.jointHandles(model);
console.log(JSON.stringify({
  painted: !!model.painted,
  joints: handles.map(h => ({ bone: h.bone, position: h.position })),
  // Blender measured this in metres, Z up; the viewer draws Y up.
  roundTrip: window.ForgeGLB.fromBlenderMetres([0.1, -0.2, 1.7]),
  backAgain: window.ForgeGLB.toBlenderMillimetres(
    window.ForgeGLB.fromBlenderMetres([0.1, -0.2, 1.7]))
}));
"""


def run_pin_viewer(tmp_path, glb):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to run glbview.js with")
    harness = tmp_path / "pins.js"
    harness.write_text(PIN_HARNESS, encoding="utf-8")
    blob = tmp_path / "pinned.glb"
    blob.write_bytes(glb)
    import subprocess
    done = subprocess.run(
        [node, str(harness), os.path.join(WEBUI_DIR, "glbview.js"), str(blob)],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_position_blender_measured_lands_where_the_viewer_draws(tmp_path):
    """The one conversion between a finding and a pin, both ways."""
    read = run_pin_viewer(tmp_path, make_rigged_glb())
    assert read["roundTrip"] == [0.1, 1.7, 0.2]
    # â€¦and back out again in millimetres, unchanged.
    assert [round(one, 6) for one in read["backAgain"]] == [100.0, -200.0, 1700.0]


def test_a_bone_named_by_a_finding_resolves_to_the_joint_it_is_on(tmp_path):
    read = run_pin_viewer(tmp_path, make_rigged_glb())
    joints = dict((one["bone"], one["position"]) for one in read["joints"])
    # A rig finding names DEF-shin.L and nothing else; the viewer already
    # knows exactly where that is.
    assert joints["DEF-shin.L"] == [0, 1.5, 0]
    assert joints["DEF-thigh.L"] == [0, 1, 0]


def test_a_glb_without_a_colour_layer_is_not_painted(tmp_path):
    assert run_pin_viewer(tmp_path, make_rigged_glb())["painted"] is False


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_the_stage_tools_are_on_the_page_and_start_hidden(client):
    html = fetch_text(client, "/")
    for anchor in ("ws-stage-tools", "ws-tools-title", "ws-inspect",
                   "ws-tools-note", "ws-tools-body", "ws-findings"):
        assert ('id="%s"' % anchor) in html, anchor
    strip = html.split('id="ws-stage-tools"', 1)[1].split(">", 1)[0]
    assert "hidden" in strip


def test_the_controls_follow_the_stepper_and_nothing_else(client):
    """"Blender is often overwhelming" â€” so one stage's controls at a time."""
    script = fetch_text(client, "/webui/app.js")
    assert "function stageToolsFollow(" in script
    assert "stageToolsFollow(stage ? stage.id : null)" in script
    follow = script.split("function stageToolsFollow(", 1)[1] \
                   .split("\n  }", 1)[0]
    # Moving off a stage drops its findings rather than leaving them on the
    # model, and re-arms or disarms the joint handles.
    assert "tools.findings = []" in follow
    assert "viewer.clearPins()" in follow
    assert 'stage === "rig"' in follow


def test_the_direct_buttons_do_not_go_through_chat(client):
    script = fetch_text(client, "/webui/app.js")
    for direct, route in (("applyMeshFix", "/mesh_fix"),
                          ("brushAt", "/weights_local"),
                          ("reAuthor", "/author")):
        body = script.split("function " + direct + "(", 1)[1] \
                     .split("\n  function ", 1)[0]
        assert route in body, direct
        assert "wsSend(" not in body, direct
        # â€¦and each one re-snapshots, so what is on screen is what Blender did.
        assert "wsSnapshot(" in body, direct


def test_a_finding_with_no_deterministic_fix_offers_only_the_ask(client):
    script = fetch_text(client, "/webui/app.js")
    card = script.split("function findingCard(", 1)[1] \
                 .split("\n  function ", 1)[0]
    assert "if (finding.fix && finding.fix.op)" in card
    assert "Apply fix" in card
    assert "Ask to fix this" in card
    assert "wsSend(" in card


def test_the_stage_tool_styles_ship_with_the_stylesheet(client):
    css = fetch_text(client, "/webui/app.css")
    for rule in (".ws-tools", ".ws-finding", ".ws-dot", ".ws-clips",
                 ".ws-scrub", ".ws-params", ".ws-step.is-dirty"):
        assert rule in css, rule
