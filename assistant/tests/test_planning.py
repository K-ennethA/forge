"""Tests for the creation flow and the planning room (Phase 21).

Run them the same way as the rest::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

The harness is ``test_webui``'s, which is ``test_bridge``'s: a real bridge
process on an ephemeral port with ``fake_claude.py`` standing in for the CLI,
every folder it reads or writes pointed at ``tmp_path``.  **Nothing here
touches port 8901 and nothing here touches port 9876.**

What is being pinned down, in order:

* the manifest — created, appended to, re-tagged and annotated, with every
  record already on disk written back byte-identical (the artist-edits journal
  pattern, applied to ``design/refs-manifest.json``);
* the board — a photo in ``refs/`` that nobody has tagged still lists, which is
  how the projects that predate this lane surface their pile at all;
* the upload gates — a traversal name, a tag that is not one of the five and a
  file that is not an image are each refused, by shape, before anything is
  written;
* creation — the folder, the prompt saved verbatim, the dropped references
  carried across, and the design turn composed and handed to ``/ask`` with the
  artist's words inside it unedited;
* the suggestion map — deterministic, disjoint, and tied to ``TASKS`` order;
* the workflow list — exactly ``forge_mcp.task_config.TASKS`` with exactly its
  ``TASK_BLURB`` strings, read out of that module's source;
* the sheet — knob cards rendered from a canned ``task-config.json``, and a
  change that is ASKED for rather than written.
"""

import ast
import base64
import io
import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ASSISTANT_DIR, os.pardir))

for _path in (TESTS_DIR, ASSISTANT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bridge  # noqa: E402

from test_webui import (  # noqa: E402,F401
    JPEG, PNG, bridges, client, fetch_text, raw_get,
)
from test_webui import last_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def b64(data):
    return base64.b64encode(data).decode("ascii")


def write(path, data=PNG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


@pytest.fixture
def projects(tmp_path):
    """A projects root with one project that has photos and no manifest.

    Exactly the shape every project that predates this lane is in: images sat
    in ``design/refs/`` since the design phase existed, with nothing anywhere
    saying what any of them is.
    """
    root = tmp_path / "projects"
    refs = root / "werewolf" / "design" / "refs"
    refs.mkdir(parents=True)
    write(str(refs / "form-a-front.png"))
    write(str(refs / "images.jpg"), JPEG)
    # Not a photo: it must not appear on the board.
    (root / "werewolf" / "design" / "requirements.md").write_text(
        "a werewolf\n", encoding="utf-8")
    return root


@pytest.fixture
def planning(bridges, projects):
    """A bridge reading that projects root and nothing else."""
    return bridges(env_extra={"FORGE_PROJECTS_DIR": str(projects)})


def folder_of(projects, name="werewolf"):
    return str(projects / name)


def manifest_text(projects, name="werewolf"):
    path = os.path.join(folder_of(projects, name), "design",
                        "refs-manifest.json")
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


#: A sheet shaped exactly as ``forge_mcp.task_config`` writes one: every entry
#: ``{value, default}`` with ``unit``/``choices``/``min``/``max``/``why`` where
#: they mean something.  Read off ``task_config.new_sheet`` and off the real
#: ``projects/werewolf/design/task-config.json``.
CANNED_SHEET = {
    "version": 1,
    "task": "character",
    "project": "werewolf",
    "settings": {
        "symmetry": {"value": "mirror_left", "default": "mirror_left",
                     "choices": ["mirror_left", "mirror_right", "as_designed"],
                     "why": "bipeds are symmetric unless you say otherwise"},
        "poly_budget_desktop": {"value": 20000, "default": 15000,
                                "unit": "triangles", "min": 500, "max": 200000,
                                "why": "LOD0's ceiling"},
        "correctives": {"value": True, "default": True,
                        "why": "a corrective shape key per bent joint"},
        "export_formats": {"value": ["stl"], "default": ["stl"],
                           "choices": ["stl", "step", "3mf"],
                           "why": "stl for the slicer"},
    },
    "history": [{"date": "2026-09-20T09:00:00", "note": "materialised"}],
}


def put_sheet(projects, sheet=None, name="werewolf"):
    design = os.path.join(folder_of(projects, name), "design")
    os.makedirs(design, exist_ok=True)
    path = os.path.join(design, "task-config.json")
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(sheet if sheet is not None else CANNED_SHEET, handle,
                  indent=2)
    return path


# ===========================================================================
# the manifest — the artist-edits journal pattern, applied to refs
# ===========================================================================

def test_a_project_with_no_manifest_reads_as_an_empty_one(projects):
    found = bridge.read_refs_manifest(folder_of(projects))
    assert found == {"records": [], "exists": False}


def test_a_manifest_that_will_not_parse_is_never_overwritten(projects):
    path = os.path.join(folder_of(projects), "design", "refs-manifest.json")
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    found = bridge.read_refs_manifest(folder_of(projects))
    assert found["unreadable"] is True
    # Both writers refuse rather than starting the file again.
    assert bridge.add_ref_record(folder_of(projects), "a.png", "front",
                                 "") == (None, None)
    assert bridge.edit_ref_record(folder_of(projects), "form-a-front.png",
                                  {"tag": "front"}) == (None, None)
    with io.open(path, encoding="utf-8") as handle:
        assert handle.read() == "{not json"


def test_a_record_the_assistant_wrote_by_hand_is_read_too(projects):
    """The manifest predates the endpoint, in both shapes it was written in."""
    path = os.path.join(folder_of(projects), "design", "refs-manifest.json")
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": 1,
                   "refs": [{"file": "images.jpg", "tag": "general"}]}, handle)
    found = bridge.read_refs_manifest(folder_of(projects))
    assert found["records"] == [{"file": "images.jpg", "tag": "general"}]


def test_appending_keeps_every_record_already_there_byte_identical(projects):
    """Append-only in spirit, meant literally.

    The record on disk carries keys this bridge never writes (``prompt``,
    ``source``) and an order of its own; after an append it is byte for byte
    the same object, in the same place, with the new one after it.
    """
    folder = folder_of(projects)
    first = {"file": "form-a-front.png", "tag": "front",
             "note": "the reference the whole form came off",
             "added": "2026-09-01T10:00:00Z",
             "prompt": "a werewolf character", "source": "chat"}
    assert bridge.write_refs_manifest(folder, [first])
    before = manifest_text(projects)

    record, path = bridge.add_ref_record(folder, "images.jpg", "back", "")
    assert record["file"] == "images.jpg" and record["tag"] == "back"
    records = bridge.read_refs_manifest(folder)["records"]
    assert len(records) == 2
    # The whole object, including the keys nothing here knows about.
    assert records[0] == first
    assert list(records[0]) == list(first)
    # …and the bytes of its block are unchanged inside the new file.
    kept = json.dumps(first, indent=2, ensure_ascii=False)
    assert kept.splitlines()[1] in before
    with io.open(path, encoding="utf-8") as handle:
        after = handle.read()
    for line in kept.splitlines()[1:-1]:
        assert line in after, line


def test_retagging_changes_one_field_and_nothing_else(projects):
    folder = folder_of(projects)
    bridge.write_refs_manifest(folder, [
        {"file": "form-a-front.png", "tag": "general", "note": "keep me",
         "added": "2026-09-01T10:00:00Z", "source": "chat"},
        {"file": "images.jpg", "tag": "side", "note": "",
         "added": "2026-09-01T10:01:00Z"},
    ])
    record, _path = bridge.edit_ref_record(folder, "form-a-front.png",
                                           {"tag": "front"})
    assert record["tag"] == "front"
    # Every other field of the edited record survives, including the one this
    # bridge does not write…
    assert record["note"] == "keep me"
    assert record["source"] == "chat"
    assert record["added"] == "2026-09-01T10:00:00Z"
    # …and the record beside it is untouched.
    records = bridge.read_refs_manifest(folder)["records"]
    assert records[1] == {"file": "images.jpg", "tag": "side", "note": "",
                          "added": "2026-09-01T10:01:00Z"}


def test_annotating_a_photo_that_has_no_record_gives_it_one(projects):
    """An old project's untagged pile becomes a board one photo at a time."""
    folder = folder_of(projects)
    record, path = bridge.edit_ref_record(folder, "images.jpg",
                                          {"note": "this jacket, but longer"})
    assert record["file"] == "images.jpg"
    assert record["note"] == "this jacket, but longer"
    assert record["tag"] == bridge.DEFAULT_REF_TAG
    assert os.path.isfile(path)


def test_a_file_that_is_not_on_the_board_cannot_be_given_a_record(projects):
    assert bridge.edit_ref_record(folder_of(projects), "nope.png",
                                  {"tag": "front"}) == (None, None)


def test_the_manifest_write_is_atomic_and_leaves_no_temporary(projects):
    folder = folder_of(projects)
    bridge.add_ref_record(folder, "images.jpg", "side", "")
    design = os.path.join(folder, "design")
    assert not [name for name in os.listdir(design) if name.endswith(".tmp")]


# ===========================================================================
# the board
# ===========================================================================

def test_an_untagged_photo_still_lists(planning, projects):
    """The werewolf's twelve photos are the whole reason this is true."""
    status, body = planning.request("/projects/werewolf/refs")
    assert status == 200, body
    names = [ref["file"] for ref in body["refs"]]
    assert sorted(names) == ["form-a-front.png", "images.jpg"]
    assert all(ref["tag"] == "" and ref["tracked"] is False
               for ref in body["refs"])
    assert body["untagged"] == 2
    assert body["manifest_exists"] is False
    # A design document is not a reference.
    assert "requirements.md" not in names


def test_tagged_records_come_first_and_carry_their_note(planning, projects):
    bridge.write_refs_manifest(folder_of(projects), [
        {"file": "images.jpg", "tag": "back", "note": "the shoulders",
         "added": "2026-09-01T10:00:00Z"}])
    status, body = planning.request("/projects/werewolf/refs")
    assert status == 200, body
    assert body["refs"][0]["file"] == "images.jpg"
    assert body["refs"][0]["tag"] == "back"
    assert body["refs"][0]["note"] == "the shoulders"
    assert body["refs"][0]["tracked"] is True
    assert body["refs"][1]["file"] == "form-a-front.png"
    assert body["refs"][1]["tracked"] is False
    assert body["untagged"] == 1


def test_a_record_whose_file_is_gone_lists_as_missing(planning, projects):
    bridge.write_refs_manifest(folder_of(projects), [
        {"file": "deleted.png", "tag": "front", "note": "",
         "added": "2026-09-01T10:00:00Z"}])
    status, body = planning.request("/projects/werewolf/refs")
    assert status == 200, body
    gone = [ref for ref in body["refs"] if ref["file"] == "deleted.png"]
    assert gone and gone[0]["missing"] is True and gone[0]["url"] is None


def test_every_listed_photo_can_actually_be_fetched(planning, projects):
    status, body = planning.request("/projects/werewolf/refs")
    assert status == 200, body
    for ref in body["refs"]:
        assert ref["url"], ref
        code, _headers, data = raw_get(planning, ref["url"])
        assert code == 200 and data


def test_the_board_of_a_project_that_does_not_exist_is_a_404(planning):
    status, body = planning.request("/projects/nope/refs")
    assert status == 404, body


# ===========================================================================
# the upload gates — refused by SHAPE, before anything is written
# ===========================================================================

def test_a_photo_lands_in_refs_with_its_tag_and_note(planning, projects):
    status, body = planning.request("/projects/werewolf/refs", {
        "name": "back.png", "data": b64(PNG), "tag": "back",
        "note": "this jacket, but longer"})
    assert status == 200, body
    assert body["ref"]["tag"] == "back"
    assert body["ref"]["note"] == "this jacket, but longer"
    stored = body["ref"]["file"]
    # The name on disk is this process's choice, never the browser's.
    assert stored.endswith("back.png") and stored != "back.png"
    assert os.path.isfile(os.path.join(folder_of(projects), "design", "refs",
                                       stored))
    records = bridge.read_refs_manifest(folder_of(projects))["records"]
    assert [item["file"] for item in records] == [stored]


def test_an_upload_with_no_tag_is_general_not_a_view(planning):
    """An unlabelled photo claiming to be the front view is the whole bug."""
    status, body = planning.request("/projects/werewolf/refs",
                                    {"name": "a.png", "data": b64(PNG)})
    assert status == 200, body
    assert body["ref"]["tag"] == "general"


@pytest.mark.parametrize("name", [
    "../../../system_prompt.md",
    "..\\..\\bridge.py",
    "refs/../../escape.png",
    "C:\\Windows\\System32\\evil.png",
])
def test_a_traversal_name_cannot_leave_the_refs_folder(planning, projects,
                                                       name):
    status, body = planning.request("/projects/werewolf/refs",
                                    {"name": name, "data": b64(PNG)})
    refs = os.path.join(folder_of(projects), "design", "refs")
    if status == 200:
        # Accepted only because the sanitiser reduced it to a plain filename —
        # and that filename is directly inside refs/, with no parent touched.
        stored = body["ref"]["file"]
        assert os.sep not in stored and "/" not in stored
        assert os.path.dirname(os.path.abspath(
            os.path.join(refs, stored))) == os.path.abspath(refs)
    else:
        assert status == 400, body
    # Nothing was written anywhere above the project, whichever way it went:
    # design/ still holds exactly what it held, plus at most refs/ itself.
    design = os.path.join(folder_of(projects), "design")
    assert set(os.listdir(design)) <= {"refs", "refs-manifest.json",
                                       "requirements.md"}
    assert not os.path.exists(os.path.join(REPO_ROOT, "escape.png"))
    assert not os.path.exists(str(projects / "escape.png"))
    assert not os.path.exists(str(projects / "werewolf" / "escape.png"))


@pytest.mark.parametrize("given,wanted", [
    ("Front", "front"), ("  side ", "side"), ("Floor plan", "floorplan"),
    ("floor-plan", "floorplan"), ("GENERAL", "general"),
])
def test_a_tag_is_forgiven_its_spelling_but_not_its_meaning(given, wanted):
    assert bridge.normalize_ref_tag(given) == wanted


@pytest.mark.parametrize("tag", ["sideways", "FRONT-ish", "views", "1"])
def test_a_tag_that_is_not_one_of_the_five_is_refused(planning, projects, tag):
    status, body = planning.request("/projects/werewolf/refs", {
        "name": "a.png", "data": b64(PNG), "tag": tag})
    assert status == 400, body
    assert "front, back, side, floorplan, general" in body["error"]
    # Nothing landed: the refusal is on the SHAPE, before any file is written.
    refs = os.path.join(folder_of(projects), "design", "refs")
    assert sorted(os.listdir(refs)) == ["form-a-front.png", "images.jpg"]


@pytest.mark.parametrize("name,data", [
    ("notes.txt", b"hello"),
    ("part.py", b"import os"),
    ("sketch.svg", b"<svg/>"),
    ("model.glb", b"glTF"),
    ("lies.png", b"MZ\x90\x00 this is an exe"),
])
def test_a_file_that_is_not_an_image_is_refused(planning, projects, name,
                                                data):
    status, body = planning.request("/projects/werewolf/refs",
                                    {"name": name, "data": b64(data)})
    assert status == 400, body
    refs = os.path.join(folder_of(projects), "design", "refs")
    assert sorted(os.listdir(refs)) == ["form-a-front.png", "images.jpg"]


def test_retag_and_annotate_refuse_a_file_that_is_not_on_the_board(planning):
    for route, payload in (("/projects/werewolf/refs/tag",
                            {"file": "../bridge.py", "tag": "front"}),
                           ("/projects/werewolf/refs/note",
                            {"file": "nope.png", "note": "hi"})):
        status, body = planning.request(route, payload)
        assert status == 404, body


def test_retagging_through_the_route_writes_the_manifest(planning, projects):
    status, body = planning.request("/projects/werewolf/refs/tag",
                                    {"file": "form-a-front.png",
                                     "tag": "Front"})
    assert status == 200, body
    assert body["ref"]["tag"] == "front"
    records = bridge.read_refs_manifest(folder_of(projects))["records"]
    assert records[0]["file"] == "form-a-front.png"
    assert records[0]["tag"] == "front"


def test_annotating_through_the_route_writes_the_manifest(planning, projects):
    status, body = planning.request("/projects/werewolf/refs/note",
                                    {"file": "images.jpg",
                                     "note": "vibe: van helsing"})
    assert status == 200, body
    assert body["ref"]["note"] == "vibe: van helsing"
    records = bridge.read_refs_manifest(folder_of(projects))["records"]
    assert records[0]["note"] == "vibe: van helsing"


def test_a_note_is_capped_rather_than_refused(projects):
    assert len(bridge.ref_note("x" * 5000)) == bridge.MAX_REF_NOTE


# ===========================================================================
# the five workflows — the mirror has to stay honest
# ===========================================================================

def task_config_source():
    """``TASKS`` and ``TASK_BLURB`` read out of the module that OWNS them.

    Parsed rather than imported: ``forge_mcp`` has its own venv and its own
    dependencies, and this suite runs in the assistant's.  The literals are
    what matters and ``ast`` can read them without importing anything.
    """
    path = os.path.join(REPO_ROOT, "mcp", "forge_mcp", "task_config.py")
    with io.open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
    return (ast.literal_eval(found["TASKS"]),
            ast.literal_eval(found["TASK_BLURB"]))


def test_the_served_workflows_are_exactly_task_configs_tasks(planning):
    tasks, blurbs = task_config_source()
    status, body = planning.request("/workflows")
    assert status == 200, body
    served = body["workflows"]
    assert [item["task"] for item in served] == list(tasks)
    assert {item["task"]: item["blurb"] for item in served} == blurbs
    assert body["tasks"] == list(tasks)
    # …and the module-level mirror the bridge answers from.
    assert bridge.WORKFLOW_TASKS == tasks
    assert bridge.WORKFLOW_BLURBS == blurbs
    assert set(bridge.WORKFLOW_LABELS) == set(tasks)


def test_every_workflow_has_a_name_a_blurb_and_some_words(planning):
    status, body = planning.request("/workflows")
    assert status == 200, body
    for item in body["workflows"]:
        assert item["label"] and item["blurb"] and item["keywords"]


def test_no_word_belongs_to_two_workflows():
    """Two owners for one word is how a tie becomes a coin toss."""
    seen = {}
    for task, words in bridge.WORKFLOW_KEYWORDS.items():
        for word in words:
            assert word not in seen, "%r is both %s and %s" % (word,
                                                               seen[word], task)
            seen[word] = task


@pytest.mark.parametrize("text,task", [
    ("a werewolf character for my game", "character"),
    ("a bracket to hold my router to the desk", "part"),
    ("a lamp with an led in it", "device"),
    ("the floor plan of my apartment", "floorplan"),
    ("a silicone mold for a figurine", "mold"),
    # The words that must NOT match: a substring is not a word.
    ("an apartment", "floorplan"),
    ("something nice", ""),
    ("", ""),
    (None, ""),
])
def test_the_suggestion_is_the_map_and_nothing_else(text, task):
    assert bridge.suggest_workflow(text)["task"] == task


def test_the_suggestion_is_deterministic_and_says_why():
    found = bridge.suggest_workflow("A WEREWOLF character, rigged")
    assert found["task"] == "character"
    assert found["matched"] == ["character", "werewolf", "rigged"]
    for _ in range(5):
        assert bridge.suggest_workflow("A WEREWOLF character, rigged") == found


def test_a_tie_is_broken_by_tasks_order_not_by_luck():
    """One word each for two tasks: the earlier task in TASKS wins, always."""
    found = bridge.suggest_workflow("a lamp in the kitchen")
    assert found["scores"]["device"] == found["scores"]["floorplan"] == 1
    assert found["task"] == "device"       # device is third, floorplan fourth
    assert bridge.WORKFLOW_TASKS.index("device") < \
        bridge.WORKFLOW_TASKS.index("floorplan")


def test_the_suggest_route_answers_with_the_same_map(planning):
    status, body = planning.request("/workflows/suggest",
                                    {"prompt": "a werewolf for my game"})
    assert status == 200, body
    assert body["task"] == "character"
    assert body["scores"] == bridge.suggest_workflow(
        "a werewolf for my game")["scores"]
    assert [item["task"] for item in body["workflows"]] == \
        list(bridge.WORKFLOW_TASKS)


def test_the_suggestion_never_decides(planning):
    """It comes back as a suggestion and nothing on disk moves."""
    status, body = planning.request("/workflows/suggest", {"prompt": "a mold"})
    assert status == 200, body
    assert set(body) >= {"task", "score", "matched", "scores"}
    assert "project" not in body


# ===========================================================================
# creation — folder, prompt, references, and the turn that starts the plan
# ===========================================================================

def test_creating_materialises_the_folder_and_saves_the_prompt(planning,
                                                               projects):
    words = "a werewolf character for my game, 6'2\", chunky boots"
    status, body = planning.request("/projects/create", {
        "name": "Wolf Man!", "task": "character", "prompt": words})
    assert status == 200, body
    assert body["created"] is True and body["planned"] is True
    assert body["project"] == "wolf-man"
    folder = str(projects / "wolf-man")
    assert os.path.isdir(os.path.join(folder, "design"))
    # Verbatim means verbatim: the file is the prompt and a newline.
    with io.open(os.path.join(folder, "design", "prompt.md"),
                 encoding="utf-8") as handle:
        assert handle.read() == words + "\n"


def test_creation_composes_the_design_turn_with_the_words_inside_it(planning):
    words = "a werewolf character for my game, 6'2\", chunky boots"
    status, body = planning.request("/projects/create", {
        "name": "wolfman", "task": "character", "prompt": words})
    assert status == 200, body
    prompt = last_prompt(planning)
    # The artist's sentence, unedited, in the turn that starts the plan.
    assert words in prompt
    assert 'task_config_init("wolfman", "character")' in prompt
    assert 'pipeline_status("wolfman")' in prompt
    assert "at most five questions" in prompt
    # …and it is a planning turn, not a build one.
    assert "Do not build any geometry yet" in prompt


def test_creation_writes_no_sheet_and_no_plan_itself(planning, projects):
    """``task_config`` and ``pipeline.py`` own those files. This adds no door."""
    status, body = planning.request("/projects/create", {
        "name": "wolfman", "task": "character", "prompt": "a werewolf"})
    assert status == 200, body
    design = os.listdir(str(projects / "wolfman" / "design"))
    assert "task-config.json" not in design
    assert "build-plan.json" not in design


def test_dropped_references_are_carried_onto_the_new_board(planning, projects):
    status, body = planning.request("/upload", {"name": "front.png",
                                                "data": b64(PNG)})
    assert status == 200, body
    dropped = body["path"]
    status, body = planning.request("/projects/create", {
        "name": "wolfman", "task": "character", "prompt": "a werewolf",
        "refs": [{"path": dropped, "tag": "front", "note": "the face"}]})
    assert status == 200, body
    assert body["problems"] == []
    assert len(body["refs"]) == 1
    assert body["refs"][0]["tag"] == "front"
    assert body["refs"][0]["note"] == "the face"
    stored = body["refs"][0]["file"]
    assert os.path.isfile(str(projects / "wolfman" / "design" / "refs" / stored))
    # …and the turn says what is on the board and what the tags mean.
    prompt = last_prompt(planning)
    assert "1 reference photo" in prompt and "1 front" in prompt


def test_a_reference_path_this_bridge_did_not_write_is_refused(planning,
                                                               projects):
    """A path is never taken on trust, even inside a body we accepted."""
    outsider = str(projects / "werewolf" / "design" / "refs" /
                   "form-a-front.png")
    status, body = planning.request("/projects/create", {
        "name": "wolfman", "task": "character", "prompt": "a werewolf",
        "refs": [{"path": outsider}, {"path": os.path.join(REPO_ROOT,
                                                           "assistant",
                                                           "bridge.py")}]})
    assert status == 200, body
    assert body["refs"] == []
    assert len(body["problems"]) == 2
    # The project is still made: a photo that did not come across is not a
    # reason to lose the words.
    assert body["created"] is True
    assert os.path.isdir(str(projects / "wolfman"))


@pytest.mark.parametrize("payload,word", [
    ({"task": "character", "prompt": "hi"}, "not a project name"),
    ({"name": "../escape", "task": "character", "prompt": "hi"},
     "not a project name"),
    ({"name": "ok", "task": "sculpture", "prompt": "hi"}, "Pick one of the five"),
    ({"name": "ok", "task": "", "prompt": "hi"}, "Pick one of the five"),
    ({"name": "ok", "task": "character", "prompt": "   "}, "Say what you are"),
])
def test_creation_refuses_before_it_writes_anything(planning, projects,
                                                    payload, word):
    status, body = planning.request("/projects/create", payload)
    assert status == 400, body
    assert word in body["error"]
    assert sorted(os.listdir(str(projects))) == ["werewolf"]


def test_creating_a_project_that_exists_is_refused(planning, projects):
    status, body = planning.request("/projects/create", {
        "name": "werewolf", "task": "character", "prompt": "again"})
    assert status == 409, body
    assert "already a project" in body["error"]
    # The one on disk is untouched.
    assert not os.path.exists(os.path.join(folder_of(projects), "design",
                                           "prompt.md"))


# ===========================================================================
# the sheet, as knob cards
# ===========================================================================

def test_the_sheet_renders_as_knobs_from_a_canned_config(planning, projects):
    put_sheet(projects)
    status, body = planning.request("/projects/werewolf/task_config")
    assert status == 200, body
    assert body["has_sheet"] is True
    assert body["task"] == "character"
    assert body["label"] == "Character"
    assert body["blurb"] == bridge.WORKFLOW_BLURBS["character"]
    knobs = {item["name"]: item for item in body["settings"]}
    assert set(knobs) == set(CANNED_SHEET["settings"])
    # Every knob carries the plain-words why the sheet already documents…
    assert knobs["symmetry"]["why"].startswith("bipeds are symmetric")
    assert knobs["symmetry"]["choices"] == ["mirror_left", "mirror_right",
                                            "as_designed"]
    # …its unit and its range where it has them…
    assert knobs["poly_budget_desktop"]["unit"] == "triangles"
    assert knobs["poly_budget_desktop"]["min"] == 500
    # …and which ones are no longer at their default.
    assert knobs["poly_budget_desktop"]["changed"] is True
    assert knobs["correctives"]["changed"] is False
    assert body["changed"] == ["poly_budget_desktop"]


def test_a_project_with_no_sheet_says_so_rather_than_drawing_one(planning):
    status, body = planning.request("/projects/werewolf/task_config")
    assert status == 200, body
    assert body["has_sheet"] is False and body["settings"] == []
    assert "No settings sheet yet" in body["note"]


def test_changing_a_knob_is_asked_for_never_written(planning, projects):
    path = put_sheet(projects)
    with io.open(path, encoding="utf-8") as handle:
        before = handle.read()
    status, body = planning.request("/projects/werewolf/task_config",
                                    {"name": "symmetry",
                                     "value": "as_designed"})
    assert status == 200, body
    assert body["job_id"]
    # The sheet on disk is byte for byte what it was: task_config owns it.
    with io.open(path, encoding="utf-8") as handle:
        assert handle.read() == before
    # What changed is that a turn was spent asking for the change.
    prompt = last_prompt(planning)
    assert 'task_config_set("werewolf", "symmetry", "as_designed")' in prompt


def test_a_knob_that_is_not_on_the_sheet_is_refused(planning, projects):
    put_sheet(projects)
    status, body = planning.request("/projects/werewolf/task_config",
                                    {"name": "nonsense", "value": 1})
    assert status == 404, body
    assert "not a setting" in body["error"]


def test_a_number_knob_keeps_its_type_in_the_composed_call(planning, projects):
    put_sheet(projects)
    status, body = planning.request("/projects/werewolf/task_config",
                                    {"name": "poly_budget_desktop",
                                     "value": 24000})
    assert status == 200, body
    assert 'task_config_set("werewolf", "poly_budget_desktop", 24000)' in \
        last_prompt(planning)


# ===========================================================================
# "Start building" — the kickoff turn, and nothing else
# ===========================================================================

def test_start_building_composes_the_kickoff_and_moves_no_stage(planning,
                                                                projects):
    put_sheet(projects)
    status, body = planning.request("/projects/werewolf/build", {})
    assert status == 200, body
    assert body["project"] == "werewolf" and body["task"] == "character"
    prompt = last_prompt(planning)
    assert 'pipeline_status("werewolf")' in prompt
    assert "pipeline_advance to the first stage that is not done" in prompt
    assert "pipeline_record the real" in prompt or "pipeline_record" in prompt
    # No plan was written by this port.
    assert not os.path.exists(os.path.join(folder_of(projects), "design",
                                           "build-plan.json"))


def test_start_building_on_a_project_that_does_not_exist_is_a_404(planning):
    status, body = planning.request("/projects/nope/build", {})
    assert status == 404, body


def test_start_building_with_no_sheet_asks_for_one(planning):
    """A real state: the design turn may still be running. Say so honestly."""
    status, body = planning.request("/projects/werewolf/build", {})
    assert status == 200, body
    assert body["task"] == ""
    prompt = last_prompt(planning)
    assert "has no settings sheet yet" in prompt
    assert "task_config_init" in prompt


# ===========================================================================
# the page — the two screens exist and say what the flow doc says
# ===========================================================================

def test_home_has_one_prompt_box_and_five_cards(planning):
    html = fetch_text(planning, "/")
    # "Start something" is the top half of Home; the library half below it is
    # its own section with its own caption, so the slice stops where it does.
    home = html[html.index('id="panel-home"'):
                html.index('id="home-library-block"')]
    # One prompt box on the whole screen.
    assert home.count("<textarea") == 1
    assert 'id="home-prompt"' in home
    # The cards are drawn from /workflows, so the markup holds the host and
    # not five hardcoded names that could drift from task_config.
    assert 'id="home-cards"' in home
    assert "Character" not in home
    # One caption for the section, and no more.
    assert home.count('class="muted small"') == 1


def test_the_planning_room_hosts_the_studios_own_chat(planning):
    """Not a second chat: the conversation column is moved, not copied."""
    html = fetch_text(planning, "/")
    script = fetch_text(planning, "/webui/app.js")
    room = html[html.index('id="panel-planning"'):
                html.index('id="panel-studio"')]
    assert 'id="plan-chat"' in room
    # The planning room's markup holds no thread and no composer of its own.
    assert 'id="thread"' not in room and 'id="composer"' not in room
    assert "function hostChat(" in script
    assert '$("plan-chat").appendChild(chat)' in script


def test_the_page_and_the_bridge_agree_on_the_five_tags(planning):
    script = fetch_text(planning, "/webui/app.js")
    served = 'var REF_TAGS = ["%s"];' % '", "'.join(bridge.REF_TAGS)
    assert served in script, served


def test_escape_backs_out_and_needs_no_modifier(planning):
    """The beginner bar: Escape backs out, and no shortcut needs a modifier."""
    script = fetch_text(planning, "/webui/app.js")
    body = script[script.index('if (event.key !== "Escape") { return; }'):]
    assert "event.ctrlKey || event.altKey || event.metaKey || event.shiftKey" \
        in body
    assert 'showTab("home")' in body


def test_the_suggestion_lights_a_card_and_never_picks_one(planning):
    """Typing sets `suggested`; only a click sets `pick`, and only `pick` counts."""
    script = fetch_text(planning, "/webui/app.js")
    body = script[script.index("var askSuggestion = debounce("):]
    body = body[:body.index("function suggestName()")]
    assert "home.suggested = res.data.task" in body
    assert "home.pick" not in body
    picker = script[script.index("function pickWorkflow("):]
    picker = picker[:picker.index("function updateCreateButton()")]
    assert "home.pick =" in picker
