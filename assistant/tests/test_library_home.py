"""Tests for Home's library half (Phase 21) — the cards, and the attach flow.

Run them the same way as the rest::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

The harness is ``test_planning``'s, which is ``test_webui``'s, which is
``test_bridge``'s: a real bridge process on an ephemeral port with
``fake_claude.py`` standing in for the CLI, every folder it reads or writes
pointed at ``tmp_path``.  **Nothing here touches port 8901 and nothing here
touches port 9876.**

What is being pinned down, in order:

* a card composes off the three files that already hold its facts — the
  settings sheet (the badge), the build plan (the ring, through the same
  ``pipeline_board`` the stage board reads) and ``renders/`` (the picture) —
  and **invents none of them**: a project with no plan reads "not started",
  never ``0/0``;
* a project with no PartForge script still lists, which is the ``/library``
  lesson the workspace picker learned the expensive way;
* the thumbnail goes through the same ``FILES.mint`` gate as every other
  picture on this page, and exactly ONE token is spent per card;
* last touched is the newest file under ``models/``, ``renders/`` or
  ``design/`` — and an export is not work on the project;
* the attach endpoint's refusals (no file, wrong extension, a path inside
  ``projects/``, a traversal that resolves back into it, a relative path) and
  the one behaviour it must never get wrong: the ``.blend`` is COPIED, and the
  artist's own file is still there afterwards;
* the kickoff turn says the mesh enters at ``verify_mesh`` and that nothing
  before it ran — and the endpoint writes neither a build plan nor a sheet;
* Home is the tab the app opens on.
"""

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
    PNG, bridges, client, fetch_text, raw_get,
)
from test_webui import last_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures — four projects in the four states a card has to draw
# ---------------------------------------------------------------------------

#: What a ``.blend`` looks like from the outside.  Nothing reads inside one on
#: this route — the copy is bytes, and Blender is never asked to open it — so a
#: recognisable header and some filler is the whole fixture.
BLEND = b"BLENDER-v500RENDH" + b"\x00" * 200


def put(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def stamp(path, when):
    os.utime(path, (when, when))
    return path


def stamp_tree(folder, when):
    """Every file under *folder* set to one time.

    The fixture writes a sheet and a plan into ``design/``, and those land with
    today's clock on them; a "last touched" assertion against a fixed number
    has to start from a folder whose every file is at a known time.  The files
    that are meant to be NEWER are stamped after this call.
    """
    for root, _dirs, names in os.walk(folder):
        for name in names:
            stamp(os.path.join(root, name), when)
    return folder


def put_plan(folder, task, statuses, project=""):
    """A build plan shaped exactly as ``pipeline.py`` writes one.

    *statuses* is ``[(id, status), ...]`` in chain order, which is the only
    thing the ring reads: ``pipeline_board`` counts the green ones and names
    the first red one, and this suite asserts on what it counted.
    """
    stages = []
    for ident, status in statuses:
        stages.append({
            "id": ident,
            "title": "%s — what this stage is for" % ident,
            "status": status,
            "does": "",
            "gate": [],
            "tools": [],
            "artifacts": [],
            "numbers": {},
            "history": [{"date": "2026-09-19T10:00:00", "action": "record",
                         "from": "in_progress", "to": status}],
        })
    path = os.path.join(folder, "design", "build-plan.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"version": 1,
                   "project": project or os.path.basename(folder),
                   "task": task, "stages": stages}, handle, indent=2)
    return path


def put_sheet(folder, task):
    path = os.path.join(folder, "design", "task-config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"version": 1, "task": task,
                   "project": os.path.basename(folder),
                   "settings": {"symmetry": {"value": "mirror_left",
                                             "default": "mirror_left"}},
                   "history": []}, handle, indent=2)
    return path


@pytest.fixture
def shelf(tmp_path):
    """Four projects, one per state a card has to be able to draw.

    ``werewolf``  — sheet, plan with a red gate, renders, models. The full card.
    ``lamp``      — sheet, plan with every stage green. The complete card.
    ``house-plan``— a plan and a sheet, and no renders at all.
    ``sketchbook``— a design folder and nothing else. Not started, no badge.

    None of them has a ``part.py``, which is the point: every one of these is
    invisible to ``project_entry`` and all four belong on the shelf.
    """
    root = tmp_path / "projects"

    wolf = str(root / "werewolf")
    put_sheet(wolf, "character")
    put_plan(wolf, "character", [
        ("reference", "passed"), ("design", "passed"), ("generate", "passed"),
        ("clean", "overridden"), ("verify_mesh", "failed"), ("rig", "pending"),
        ("skin", "pending"),
    ])
    put(os.path.join(wolf, "renders", "wolf-wip-1.png"), PNG)
    put(os.path.join(wolf, "renders", "wolf-wip-2.png"), PNG)
    put(os.path.join(wolf, "renders", "turntable.mp4"), b"\x00" * 64)
    put(os.path.join(wolf, "models", "werewolf-wip-2.blend"), BLEND)
    stamp_tree(wolf, 1_700_000_000)
    stamp(os.path.join(wolf, "renders", "wolf-wip-1.png"), 1_700_000_100)
    stamp(os.path.join(wolf, "renders", "wolf-wip-2.png"), 1_700_000_300)
    stamp(os.path.join(wolf, "renders", "turntable.mp4"), 1_700_000_400)
    stamp(os.path.join(wolf, "models", "werewolf-wip-2.blend"), 1_700_000_500)

    lamp = str(root / "lamp")
    put_sheet(lamp, "device")
    put_plan(lamp, "device", [("design", "passed"), ("circuit", "passed"),
                              ("author", "passed"), ("generate", "passed"),
                              ("check", "overridden"), ("export", "passed")])
    put(os.path.join(lamp, "renders", "lamp.png"), PNG)
    stamp_tree(lamp, 1_699_000_000)

    house = str(root / "house-plan")
    put_sheet(house, "floorplan")
    put_plan(house, "floorplan", [("extract", "passed"), ("validate", "passed"),
                                  ("echo", "in_progress"), ("build", "pending"),
                                  ("reconcile", "pending")])
    put(os.path.join(house, "design", "requirements.md"), b"rooms\n")
    stamp_tree(house, 1_698_000_000)

    book = str(root / "sketchbook")
    put(os.path.join(book, "design", "prompt.md"), b"someday\n")
    stamp_tree(book, 1_697_000_000)

    return root


@pytest.fixture
def shelved(bridges, shelf):
    """A bridge reading that projects root and nothing else."""
    return bridges(env_extra={"FORGE_PROJECTS_DIR": str(shelf)})


@pytest.fixture
def rooted(monkeypatch, tmp_path, shelf):
    """The in-process bridge module, reading the same fixture root.

    The models row is pointed at an empty folder too: one test compares this
    shelf against ``scan_library``, which scans the generated-mesh folders as
    well, and a test that reads whatever is in ``C:/forge-models`` on this
    machine is not a test.
    """
    monkeypatch.setenv("FORGE_PROJECTS_DIR", str(shelf))
    monkeypatch.setenv("FORGE_MESHGEN_OUTPUT_DIR", str(tmp_path / "meshes"))
    monkeypatch.setenv("FORGE_MODELS_DIRS", "")
    return shelf


def card_of(body, name):
    found = [card for card in body["projects"] if card["name"] == name]
    assert found, "%s is not on the shelf: %s" % (
        name, [card["name"] for card in body["projects"]])
    return found[0]


# ===========================================================================
# the ring — the real build plan, or an honest "not started"
# ===========================================================================

def test_the_ring_is_the_plans_own_count(rooted, shelf):
    card = bridge.library_card(str(shelf / "werewolf"))
    board = bridge.pipeline_board(str(shelf / "werewolf"), "werewolf")
    # The same two numbers the stage board draws, read from the same place —
    # so Home and the Workspace cannot disagree about where a build is.
    assert (card["done"], card["total"]) == (board["done"], board["total"])
    assert (card["done"], card["total"]) == (4, 7)     # 3 passed + 1 overridden
    assert card["progress"] == "4/7"
    assert card["has_plan"] is True


def test_a_red_gate_is_named_on_the_card(rooted, shelf):
    card = bridge.library_card(str(shelf / "werewolf"))
    assert card["state"] == "blocked"
    assert card["blocked"] == "verify_mesh"
    assert card["blocked_title"].startswith("verify_mesh")
    # …and it is the same stage `pipeline.blocked` would name.
    assert card["blocked"] == bridge.pipeline_board(
        str(shelf / "werewolf"), "werewolf")["blocked"]


def test_every_stage_green_reads_complete(rooted, shelf):
    card = bridge.library_card(str(shelf / "lamp"))
    assert card["state"] == "complete"
    assert card["progress"] == "6/6"
    assert not card["blocked"]


def test_work_in_progress_is_neither_complete_nor_blocked(rooted, shelf):
    card = bridge.library_card(str(shelf / "house-plan"))
    assert card["state"] == "building"
    assert card["progress"] == "2/5"
    assert card["next"] == "echo"


def test_a_project_with_no_plan_says_not_started_never_zero_of_zero(rooted,
                                                                    shelf):
    """A grey ``0/0`` reads as a measurement, and there has been none."""
    card = bridge.library_card(str(shelf / "sketchbook"))
    assert card["has_plan"] is False
    assert card["state"] == "not_started"
    assert card["progress"] == "not started"
    # Not zero, and not a number at all: there is nothing to count.
    assert card["done"] is None and card["total"] is None
    assert "0/0" not in json.dumps(card)
    assert "no build plan" in card["note"].lower()


def test_a_plan_that_will_not_parse_is_not_started_rather_than_a_crash(rooted,
                                                                      shelf):
    path = os.path.join(str(shelf / "lamp"), "design", "build-plan.json")
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    card = bridge.library_card(str(shelf / "lamp"))
    assert card["has_plan"] is False and card["state"] == "not_started"
    assert card["done"] is None


# ===========================================================================
# the badge — the sheet's own task, in the artist's words
# ===========================================================================

def test_the_badge_is_the_sheets_task_spelled_the_way_a_person_says_it(rooted,
                                                                       shelf):
    assert bridge.library_card(str(shelf / "werewolf"))["label"] == "Character"
    assert bridge.library_card(str(shelf / "lamp"))["label"] == "Device"
    assert bridge.library_card(str(shelf / "house-plan"))["label"] == "Floor Plan"


def test_a_project_with_no_sheet_carries_no_badge_rather_than_a_guess(rooted,
                                                                      shelf):
    card = bridge.library_card(str(shelf / "sketchbook"))
    assert card["task"] == "" and card["label"] == ""
    assert card["has_sheet"] is False


def test_a_sheet_with_a_task_nobody_recognises_carries_no_badge(rooted, shelf):
    put_sheet(str(shelf / "sketchbook"), "sculpture")
    card = bridge.library_card(str(shelf / "sketchbook"))
    assert card["task"] == "" and card["label"] == ""


# ===========================================================================
# the picture — the same gate as every other image on this page
# ===========================================================================

def test_the_newest_still_is_the_thumbnail(rooted, shelf):
    card = bridge.library_card(str(shelf / "werewolf"))
    assert card["thumbnail"]["file"] == "wolf-wip-2.png"
    assert card["thumbnail"]["url"].startswith("/file/")


def test_a_film_is_not_a_thumbnail(rooted, shelf):
    """``turntable.mp4`` is the newest file in renders/ and is not a picture."""
    card = bridge.library_card(str(shelf / "werewolf"))
    assert not card["thumbnail"]["file"].endswith(".mp4")


def test_a_project_with_no_renders_has_no_thumbnail(rooted, shelf):
    assert bridge.library_card(str(shelf / "house-plan"))["thumbnail"] is None
    assert bridge.library_card(str(shelf / "sketchbook"))["thumbnail"] is None


def test_something_that_is_not_an_image_is_never_minted(rooted, shelf):
    stamp(put(os.path.join(str(shelf / "house-plan"), "renders", "notes.txt"),
              b"hello"), 1_700_100_000)
    stamp(put(os.path.join(str(shelf / "house-plan"), "renders", "part.py"),
              b"import os"), 1_700_100_001)
    assert bridge.library_card(str(shelf / "house-plan"))["thumbnail"] is None


def test_exactly_one_token_is_spent_per_card(rooted, shelf):
    """The token store is an LRU of 400; a shelf must not evict the chat's own.

    The werewolf has two stills and a film in ``renders/``.  Drawing its card
    mints one token, not three — which is the whole reason this route does not
    simply call ``project_deliverables``.
    """
    before = bridge.FILES.count()
    bridge.library_card(str(shelf / "werewolf"))
    assert bridge.FILES.count() == before + 1
    # …and a second draw of the same card re-uses it rather than minting again.
    bridge.library_card(str(shelf / "werewolf"))
    assert bridge.FILES.count() == before + 1


def test_the_thumbnail_is_actually_fetchable_through_the_token_gate(shelved):
    status, body = shelved.request("/library/cards")
    assert status == 200, body
    url = card_of(body, "werewolf")["thumbnail"]["url"]
    code, _headers, data = raw_get(shelved, url)
    assert code == 200 and data == PNG


# ===========================================================================
# last touched
# ===========================================================================

def test_last_touched_is_the_newest_file_in_models_renders_or_design(rooted,
                                                                     shelf):
    card = bridge.library_card(str(shelf / "werewolf"))
    assert card["mtime"] == pytest.approx(1_700_000_500, abs=1)


def test_an_export_is_not_work_on_the_project(rooted, shelf):
    """Re-exporting last month's part is a file operation, not a session."""
    before = bridge.library_card(str(shelf / "house-plan"))["mtime"]
    stamp(put(os.path.join(str(shelf / "house-plan"), "exports", "house.glb"),
              b"glTF"), 1_800_000_000)
    assert bridge.library_card(str(shelf / "house-plan"))["mtime"] == before


def test_a_project_with_nothing_in_it_says_never_rather_than_1970(rooted,
                                                                  shelf):
    empty = str(shelf / "empty")
    os.makedirs(empty)
    card = bridge.library_card(empty)
    assert card["mtime"] == 0.0
    assert card["touched"] == "never"
    assert card["modified"] == ""


@pytest.mark.parametrize("seconds,wanted", [
    (0, "just now"),
    (40, "40s ago"),
    (60 * 12, "12 min ago"),
    (3600 * 5, "5 h ago"),
    (86400, "1 day ago"),
    (86400 * 3, "3 days ago"),
    (86400 * 7, "1 week ago"),
    (86400 * 30, "4 weeks ago"),
])
def test_last_touched_is_said_in_the_units_a_shelf_is_read_in(seconds, wanted):
    now = 1_700_000_000.0
    assert bridge.touched_words(now - seconds, now) == wanted


def test_a_file_stamped_in_the_future_is_not_a_negative_age():
    now = 1_700_000_000.0
    assert bridge.touched_words(now + 5000, now) == "just now"


# ===========================================================================
# the shelf — every project folder, script or no script
# ===========================================================================

def test_a_project_with_no_partforge_script_still_lists(rooted, shelf):
    """Lane 1's ``?scene=0`` lesson, pinned as the difference it actually is.

    ``GET /projects`` lists the folders the workbench can open dimensions for —
    the ones with a PartForge script in them.  None of these four has one, and
    every one of them is somebody's project.
    """
    names = [card["name"] for card in bridge.library_cards()["projects"]]
    assert sorted(names) == ["house-plan", "lamp", "sketchbook", "werewolf"]
    assert bridge.scan_projects()["projects"] == []


def test_a_project_that_is_only_a_mesh_still_lists(rooted, shelf):
    """No script, no spec, no design folder — and still somebody's project."""
    put(os.path.join(str(shelf), "greybox", "models", "greybox-wip-1.blend"),
        BLEND)
    names = [card["name"] for card in bridge.library_cards()["projects"]]
    assert "greybox" in names
    # The Library tab's own reading drops it: `library_entry` answers None for
    # a folder with neither a script nor a design sheet, which is the right
    # answer for a part picker and the wrong one for a shelf.
    assert bridge.library_entry(os.path.join(str(shelf), "greybox")) is None


def test_the_shelf_is_newest_work_first(rooted, shelf):
    names = [card["name"] for card in bridge.library_cards()["projects"]]
    assert names == ["werewolf", "lamp", "house-plan", "sketchbook"]


def test_a_project_nothing_has_been_written_into_sorts_by_name_at_the_bottom(
        rooted, shelf):
    os.makedirs(str(shelf / "aaa-empty"))
    os.makedirs(str(shelf / "zzz-empty"))
    names = [card["name"] for card in bridge.library_cards()["projects"]]
    assert names[-2:] == ["aaa-empty", "zzz-empty"]


def test_the_route_draws_the_whole_shelf(shelved):
    status, body = shelved.request("/library/cards")
    assert status == 200, body
    assert body["total"] == 4
    assert card_of(body, "werewolf")["progress"] == "4/7"
    assert card_of(body, "sketchbook")["progress"] == "not started"
    assert card_of(body, "lamp")["state"] == "complete"


def test_the_shelf_draws_with_no_projects_folder_at_all(bridges, tmp_path):
    client = bridges(env_extra={"FORGE_PROJECTS_DIR": str(tmp_path / "nope")})
    status, body = client.request("/library/cards")
    assert status == 200, body
    assert body["projects"] == [] and body["count"] == 0
    assert "no projects folder" in body["note"].lower()


# ===========================================================================
# attach — the gates, in the order a wrong path trips them
# ===========================================================================

@pytest.fixture
def outside(tmp_path):
    """A ``.blend`` on the artist's disk, well outside ``projects/``."""
    path = str(tmp_path / "elsewhere" / "Wolf Man.blend")
    return put(path, BLEND)


def test_nothing_is_attached_without_a_path(shelved):
    status, body = shelved.request("/projects/attach", {})
    assert status == 400, body
    assert "Type the path" in body["error"]


def test_a_relative_path_is_refused_in_plain_words(shelved):
    status, body = shelved.request("/projects/attach", {"path": "wolf.blend"})
    assert status == 400, body
    assert "whole path" in body["error"] and "drive letter" in body["error"]


def test_a_file_that_is_not_there_is_refused(shelved, tmp_path):
    missing = str(tmp_path / "elsewhere" / "nope.blend")
    status, body = shelved.request("/projects/attach", {"path": missing})
    assert status == 400, body
    assert "no file at" in body["error"]


@pytest.mark.parametrize("name", ["wolf.glb", "wolf.stl", "wolf.png",
                                  "wolf.blend1", "wolf"])
def test_a_file_that_is_not_a_blend_is_refused(shelved, tmp_path, name):
    path = put(str(tmp_path / "elsewhere" / name), BLEND)
    status, body = shelved.request("/projects/attach", {"path": path})
    assert status == 400, body
    assert "not a .blend" in body["error"]


def test_a_folder_is_refused(shelved, tmp_path):
    folder = str(tmp_path / "elsewhere" / "scenes.blend")
    os.makedirs(folder)
    status, body = shelved.request("/projects/attach", {"path": folder})
    assert status == 400, body
    assert "folder" in body["error"]


def test_a_file_already_inside_projects_is_refused(shelved, shelf):
    """Attaching wraps a NEW project around a file. That one already has one."""
    inside = put(os.path.join(str(shelf / "werewolf"), "models",
                              "werewolf-wip-3.blend"), BLEND)
    status, body = shelved.request("/projects/attach", {"path": inside})
    assert status == 400, body
    assert "already inside" in body["error"]
    assert sorted(os.listdir(str(shelf))) == ["house-plan", "lamp",
                                              "sketchbook", "werewolf"]


def test_a_traversal_that_lands_back_inside_projects_is_refused(shelved, shelf):
    """Refused for what it RESOLVES to, not for how it is spelled."""
    put(os.path.join(str(shelf / "werewolf"), "models", "werewolf-wip-3.blend"),
        BLEND)
    crooked = os.path.join(str(shelf), "lamp", "..", "werewolf", "models",
                           "werewolf-wip-3.blend")
    status, body = shelved.request("/projects/attach", {"path": crooked})
    assert status == 400, body
    assert "already inside" in body["error"]


def test_a_path_that_climbs_out_of_projects_is_taken_on_its_resolved_value(
        shelved, shelf, tmp_path):
    """The mirror of the test above: ``..`` that really does leave is fine."""
    put(str(tmp_path / "elsewhere" / "wolf.blend"), BLEND)
    crooked = os.path.join(str(shelf), "werewolf", "..", "..", "elsewhere",
                           "wolf.blend")
    status, body = shelved.request("/projects/attach", {"path": crooked})
    assert status == 200, body
    assert body["source"] == os.path.abspath(str(tmp_path / "elsewhere" /
                                                 "wolf.blend"))


# ===========================================================================
# attach — what it does, and the one thing it must never do
# ===========================================================================

def test_the_blend_is_copied_and_the_artists_own_file_is_still_there(shelved,
                                                                     shelf,
                                                                     outside):
    with open(outside, "rb") as handle:
        before = handle.read()
    status, body = shelved.request("/projects/attach", {"path": outside})
    assert status == 200, body
    assert body["copied"] is True and body["moved"] is False
    assert body["source_kept"] is True

    # The original is exactly where it was, byte for byte.
    assert os.path.isfile(outside)
    with open(outside, "rb") as handle:
        assert handle.read() == before

    # …and the copy is version one of the new project's chain.
    assert body["project"] == "wolf-man"
    copy = str(shelf / "wolf-man" / "models" / "wolf-man-wip-1.blend")
    assert body["model"] == copy
    with open(copy, "rb") as handle:
        assert handle.read() == before
    # The chain reader agrees that is version one.
    assert bridge.split_version("wolf-man-wip-1.blend") == ("wolf-man-wip", 1)


def test_the_name_defaults_to_the_files_own_and_is_slugged(shelved, shelf,
                                                           outside):
    status, body = shelved.request("/projects/attach", {"path": outside})
    assert status == 200, body
    assert body["project"] == "wolf-man"
    assert os.path.isdir(str(shelf / "wolf-man" / "design"))


def test_a_name_can_be_given_instead(shelved, shelf, outside):
    status, body = shelved.request("/projects/attach",
                                   {"path": outside, "name": "Big Bad!"})
    assert status == 200, body
    assert body["project"] == "big-bad"
    assert os.path.isfile(str(shelf / "big-bad" / "models" /
                              "big-bad-wip-1.blend"))


def test_an_empty_name_box_falls_back_to_the_file_rather_than_refusing(
        shelved, outside):
    status, body = shelved.request("/projects/attach",
                                   {"path": outside, "name": "   "})
    assert status == 200, body
    assert body["project"] == "wolf-man"


@pytest.mark.parametrize("name", ["../escape", "C:\\Windows", "..", "~/wolf"])
def test_a_name_that_is_a_path_is_refused_before_anything_is_written(
        shelved, shelf, outside, name):
    status, body = shelved.request("/projects/attach",
                                   {"path": outside, "name": name})
    assert status == 400, body
    assert "not a project name" in body["error"]
    assert sorted(os.listdir(str(shelf))) == ["house-plan", "lamp",
                                              "sketchbook", "werewolf"]
    assert not os.path.exists(os.path.join(str(shelf), "escape"))


def test_attaching_over_a_project_that_exists_is_refused(shelved, shelf,
                                                         outside):
    status, body = shelved.request("/projects/attach",
                                   {"path": outside, "name": "werewolf"})
    assert status == 409, body
    assert "already a project" in body["error"]
    # Nothing was copied into it.
    assert sorted(os.listdir(str(shelf / "werewolf" / "models"))) == \
        ["werewolf-wip-2.blend"]


def test_the_source_path_is_written_down_where_it_survives(shelved, shelf,
                                                           outside):
    """Six months later, "what did this start as" has an answer on disk."""
    status, body = shelved.request("/projects/attach", {"path": outside})
    assert status == 200, body
    with io.open(str(shelf / "wolf-man" / "design" / "prompt.md"),
                 encoding="utf-8") as handle:
        written = handle.read()
    assert outside in written
    assert "copied, not moved" in written
    assert "verify_mesh" in written


def test_attaching_writes_no_plan_and_no_sheet(shelved, shelf, outside):
    """``pipeline.py`` and ``task_config`` own those. This adds no door."""
    status, body = shelved.request("/projects/attach", {"path": outside})
    assert status == 200, body
    design = os.listdir(str(shelf / "wolf-man" / "design"))
    assert "build-plan.json" not in design
    assert "task-config.json" not in design
    assert design == ["prompt.md"]


def test_the_kickoff_turn_says_the_mesh_enters_at_verify_mesh(shelved, shelf,
                                                              outside):
    status, body = shelved.request("/projects/attach", {"path": outside})
    assert status == 200, body
    assert body["enters_at"] == "verify_mesh"
    prompt = last_prompt(shelved)
    assert "enters the pipeline at verify_mesh" in prompt
    # The line that keeps the board honest: no stage may be marked as if it
    # had run, and the override is the mechanism that says so out loud.
    assert "nothing may be recorded as if it had" in prompt
    assert "override" in prompt
    assert 'pipeline_status("wolf-man")' in prompt
    assert "task_config_init" in prompt
    # …and it must not ask for geometry: the mesh is the one that was brought.
    assert "Do not generate any geometry" in prompt
    # The source path and the copy are both named in the turn.
    assert outside in prompt
    assert "wolf-man-wip-1.blend" in prompt


def test_the_new_project_lands_on_the_shelf_not_started(shelved, outside):
    shelved.request("/projects/attach", {"path": outside})
    status, body = shelved.request("/library/cards")
    assert status == 200, body
    card = card_of(body, "wolf-man")
    # A mesh and no plan: "not started" is the true answer until the pipeline
    # writes one, and the card says exactly that rather than inventing a stage.
    assert card["state"] == "not_started"
    assert card["done"] is None
    assert card["label"] == ""


def test_the_copy_goes_through_the_never_overwrite_helper(rooted, shelf,
                                                          tmp_path):
    """Filing is a copy, and a copy that overwrote a mesh is unrecoverable.

    The route itself cannot reach this: a project whose folder already exists
    is refused before anything is copied.  What is pinned here is that the
    helper the copy goes through is the one that never lands on top of a file
    — so the day something else writes into ``models/`` first, the artist's
    mesh is still theirs.
    """
    source = put(str(tmp_path / "elsewhere" / "dup.blend"), BLEND)
    body, error = bridge.attach_blend("dup", source)
    assert error is None, body
    assert body["renamed"] is False
    models = os.path.join(str(shelf), "dup", "models")
    taken, renamed = bridge.free_model_path(models, os.path.basename(
        body["model"]))
    assert renamed is True and not os.path.exists(taken)


# ===========================================================================
# the page — the shelf, the tile, and the tab the app opens on
# ===========================================================================

def test_home_is_the_tab_the_app_opens_on(shelved):
    """docs/ux-flow.md Screen 1: "The app opens here."

    Both halves exist now, so the default moved.  It is a DEFAULT: a browser
    left on another tab still opens on that tab, which is why the assertion is
    on the fallback and not on the stored value.
    """
    script = fetch_text(shelved, "/webui/app.js")
    assert 'var wanted = tabName(saved) || "home";' in script
    assert 'which = tabName(which) || "home";' in script
    assert '|| "studio";' not in script


def test_the_six_tabs_are_still_the_six(shelved):
    """The pin lane 1 set, unmoved: this lane adds no tab."""
    script = fetch_text(shelved, "/webui/app.js")
    assert ('var TABS = ["home", "planning", "studio", "workspace", '
            '"library", "flows"];') in script


def test_the_library_half_is_its_own_section_with_one_caption(shelved):
    html = fetch_text(shelved, "/")
    block = html[html.index('id="home-library-block"'):
                 html.index('id="panel-planning"')]
    assert 'id="home-library"' in block
    # One caption for the section, and no more — the beginner bar's rule.
    assert block.count('class="muted small"') == 1
    # No project name is hardcoded: the grid is drawn from /library/cards.
    assert "werewolf" not in block


def test_the_cards_are_drawn_from_the_route_and_nothing_else(shelved):
    script = fetch_text(shelved, "/webui/app.js")
    body = script[script.index("function loadHomeLibrary()"):]
    body = body[:body.index("function attachStatus(")]
    assert 'api("/library/cards")' in body
    # Nothing here computes progress: it prints what the bridge composed.
    assert "not started" not in body


def test_a_project_with_no_plan_is_drawn_quiet_rather_than_at_zero(shelved):
    script = fetch_text(shelved, "/webui/app.js")
    body = script[script.index("function ringText("):]
    body = body[:body.index("function projectCard(")]
    assert 'return "not started"' in body
    # …and the quiet state has no colour of its own in the stylesheet, while
    # done and in-progress do.
    css = fetch_text(shelved, "/webui/app.css")
    assert ".proj-ring.is-complete { border-color: var(--ok)" in css
    assert ".proj-ring.is-building { border-color: var(--accent)" in css
    assert ".proj-ring.is-not_started" not in css


def test_clicking_a_card_resumes_where_the_build_stopped(shelved):
    """The Workspace's own focus rule does it: cleared focus = blocked stage."""
    script = fetch_text(shelved, "/webui/app.js")
    body = script[script.index("function resumeProject("):]
    body = body[:body.index("function renderShelf(")]
    assert "ws.project = name;" in body
    assert "ws.focus = null;" in body
    # The cached panels are dropped, or the last project's plan would be
    # redrawn under this project's name.
    assert "ws.pipeline = null;" in body
    assert 'showTab("workspace")' in body
    # …and the rule it relies on is still the one that is there.
    focus = script[script.index("function defaultFocus("):]
    focus = focus[:focus.index("function focusedStage(")]
    assert "if (data.blocked) { return data.blocked; }" in focus


def test_the_attach_tile_says_there_is_no_file_picker_in_plain_words(shelved):
    html = fetch_text(shelved, "/")
    help_text = html[html.index('id="attach-help"'):]
    help_text = help_text[:help_text.index("</p>")]
    assert "cannot open a file picker" in help_text
    assert "Copy as path" in help_text
    # And the promise the endpoint keeps, said where it is read.
    assert "never" in help_text and "moved" in help_text
    # No Browse button anywhere on the flow: there is nothing behind one.
    form = html[html.index('id="home-attach"'):html.index('id="attach-help"')]
    assert "Browse" not in form and 'type="file"' not in form


def test_enter_in_the_path_box_is_wired_rather_than_assumed(shelved):
    """One box, one button: Enter means go, and this page says so itself.

    Wired by hand rather than left to the browser's implicit submission, so
    the gesture is a behaviour of this page — checkable here — instead of a
    rule that varies with the shape of the form.
    """
    script = fetch_text(shelved, "/webui/app.js")
    body = script[script.index('$("attach-path").addEventListener("keydown"'):]
    body = body[:body.index("});") + 3]
    assert 'event.key !== "Enter"' in body
    assert "attachExisting();" in body


def test_escape_backs_out_of_the_path_entry_flow_first(shelved):
    script = fetch_text(shelved, "/webui/app.js")
    body = script[script.index('if (event.key !== "Escape") { return; }'):]
    assert 'if (!$("home-attach").hidden) {' in body
    assert body.index('$("home-attach").hidden') < \
        body.index('$("home-prompt").value = ""')
