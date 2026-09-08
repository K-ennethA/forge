"""Phase 15: the project's own .blend — the two mirrors, and what they refuse.

Blender is the NDJSON fake on an ephemeral port (never 9876), so these run with
Blender closed and pin the *contract* in ``docs/architecture.md`` rather than a
live add-on. No file is written and no scene is loaded anywhere: the add-on's
answers are canned.

What is being proved, and why each one is worth a test:

1. **the wire** — ``save_project_blend`` sends the project or nothing at all (an
   omitted project means "the one the panel is pointed at", which is the
   add-on's guess to make, not a null for it to interpret), and
   ``open_project_blend`` always sends an explicit ``confirm``;
2. **the save's promise is repeated, not summarised** — ``copy=True`` is the
   entire reason this command is safe to accept, and the report prints
   ``session_file`` so the model can *say* their own file did not move instead
   of asserting it;
3. **``needs_confirmation`` is relayed, never answered** — the confirmation
   branch must reach the artist as a question with the loss named, and the tool
   must not turn round and ask again with ``confirm: true``. There is no undo
   across a file load, so this is the one branch where being agreeable costs
   somebody an afternoon;
4. **an open says what an open costs** — undo does not cross a file load, and
   the report says so on the success path too;
5. **neither is a flow step.** A flow replays with no model in the loop, and a
   step that discards the running scene has no business in one.
"""

from __future__ import annotations

from typing import Any

import pytest

from forge_mcp import server, util
from forge_mcp.errors import BackendError, ForgeError

from .test_blender_client import FakeBlender, point_at, reply  # noqa: F401
from .test_rigforge import blender, sent  # noqa: F401 - pytest fixtures

# --- canned add-on results (the shapes addon/forge/tools/projects.py returns) -

SAVE_RESULT: dict[str, Any] = {
    "project": "gecko-bowl",
    "path": r"C:\forge\projects\gecko-bowl\gecko-bowl.blend",
    "folder": r"C:\forge\projects\gecko-bowl",
    "size": 12_582_912,
    "mtime": 1757260800.5,
    "exists": True,
    "replaced": True,
    "created_folder": False,
    "object_count": 3,
    "objects": ["gecko-bowl", "gecko-bowl-collar", "Ref-front"],
    # where THEIR Ctrl+S still goes — unchanged by the save
    "session_file": r"D:\sculpts\my-gecko.blend",
    "retargeted": False,
    "changed": "Saved 3 object(s) into "
               r"C:\forge\projects\gecko-bowl\gecko-bowl.blend"
               " (replacing the previous save).",
    "where": "projects/gecko-bowl/gecko-bowl.blend — your own file (File > "
             "Save) is untouched.",
}

NEEDS_CONFIRMATION: dict[str, Any] = {
    "project": "gecko-bowl",
    "path": r"C:\forge\projects\gecko-bowl\gecko-bowl.blend",
    "opened": False,
    "needs_confirmation": True,
    "dirty": True,
    "session_file": r"D:\sculpts\my-gecko.blend",
    "object_count": 12,
    "objects": ["Head", "Torso", "Ear.L"],
    "would_lose": "This session has unsaved changes since "
                  r"D:\sculpts\my-gecko.blend"
                  " was last saved (12 object(s) in the scene).",
    "hint": "Press Save scene to project first if you want this work kept, "
            "then open again — or open anyway to discard it.",
    "changed": "Nothing yet — this session has unsaved changes.",
    "where": "File > Open in Blender does the same thing, with the same warning.",
}

OPENED: dict[str, Any] = {
    "project": "gecko-bowl",
    "path": r"C:\forge\projects\gecko-bowl\gecko-bowl.blend",
    "opened": True,
    "needs_confirmation": False,
    "confirmed": True,
    "discarded": "",
    "session_file": r"C:\forge\projects\gecko-bowl\gecko-bowl.blend",
    "object_count": 3,
    "objects": ["gecko-bowl", "gecko-bowl-collar", "Ref-front"],
    "server_running": True,
    "server_port": 9876,
    "pump_survived": True,
    "changed": "Opened gecko-bowl.blend — 3 object(s). The Forge server is "
               "still listening.",
    "where": "The title bar now says gecko-bowl.blend. Undo does not cross a "
             "file load.",
}


# --- save_project_blend: the wire -------------------------------------------


def test_save_with_no_project_sends_no_project(blender) -> None:
    """An omitted project is the panel's own guess — never a null on the wire."""
    fake = blender({"save_project_blend": SAVE_RESULT})
    server.save_project_blend()

    assert fake.requests[0]["type"] == "save_project_blend"
    assert sent(fake, "save_project_blend") == {}


def test_save_passes_the_named_project_through(blender) -> None:
    fake = blender({"save_project_blend": SAVE_RESULT})
    server.save_project_blend(project="  gecko-bowl  ")

    assert sent(fake, "save_project_blend") == {"project": "gecko-bowl"}


def test_save_never_sends_create(blender) -> None:
    """`create` defaults to true in the add-on; the mirror does not second-guess it."""
    fake = blender({"save_project_blend": SAVE_RESULT})
    server.save_project_blend(project="gecko-bowl")

    assert "create" not in sent(fake, "save_project_blend")


# --- save_project_blend: the report -----------------------------------------


def test_save_report_says_their_own_file_did_not_move(blender) -> None:
    """copy=True is the promise; the report repeats it with the proof beside it."""
    blender({"save_project_blend": SAVE_RESULT})
    report = server.save_project_blend()

    assert "COPY" in report
    assert "did not move" in report
    assert r"D:\sculpts\my-gecko.blend" in report      # session_file, verbatim
    assert "File > Save" in report


def test_save_report_points_at_the_library_open_button(blender) -> None:
    """Saving is only worth doing because Open loads it back. Say where Open is."""
    blender({"save_project_blend": SAVE_RESULT})
    report = server.save_project_blend()

    assert "Library" in report
    assert "Open" in report
    assert "Save Scene to Project" in report           # the artist's own button


def test_save_report_carries_the_file_facts(blender) -> None:
    blender({"save_project_blend": SAVE_RESULT})
    report = server.save_project_blend()

    assert r"C:\forge\projects\gecko-bowl\gecko-bowl.blend" in report
    assert "3 object(s)" in report
    assert "replacing the previous save" in report
    assert "12.0 MB" in report


def test_a_new_project_folder_is_mentioned() -> None:
    result = dict(SAVE_RESULT, created_folder=True, replaced=False)
    report = util.fmt_project_save_report(result)

    assert "did not exist" in report
    assert "replacing the previous save" not in report


def test_an_unsaved_session_still_gets_the_promise() -> None:
    """No filepath is not "no answer" — it is "still unsaved", which is the news."""
    report = util.fmt_project_save_report(dict(SAVE_RESULT, session_file=""))

    assert "still unsaved" in report
    assert "COPY" in report


def test_a_retargeted_session_is_shouted_not_dropped() -> None:
    """Cannot happen with copy=True. If it ever does, the artist hears it here."""
    report = util.fmt_project_save_report(dict(SAVE_RESULT, retargeted=True))

    assert "WARNING" in report
    assert "RETARGETED" in report
    assert "Save As" in report


# --- open_project_blend: the wire -------------------------------------------


def test_open_sends_the_name_and_an_explicit_unconfirmed(blender) -> None:
    fake = blender({"open_project_blend": OPENED})
    server.open_project_blend("  gecko-bowl  ")

    assert fake.requests[0]["type"] == "open_project_blend"
    assert sent(fake, "open_project_blend") == {"name": "gecko-bowl",
                                                "confirm": False}


def test_open_passes_a_confirmation_through_when_it_is_given(blender) -> None:
    fake = blender({"open_project_blend": OPENED})
    server.open_project_blend("gecko-bowl", confirm=True)

    assert sent(fake, "open_project_blend") == {"name": "gecko-bowl",
                                                "confirm": True}


def test_a_blank_name_never_reaches_blender(blender) -> None:
    """An empty name would let the add-on guess which scene to destroy."""
    fake = blender({"open_project_blend": OPENED})

    with pytest.raises(ForgeError) as excinfo:
        server.open_project_blend("   ")

    assert "Which project?" in str(excinfo.value)
    assert fake.requests == []


# --- open_project_blend: needs_confirmation is a QUESTION --------------------


def test_the_confirmation_branch_never_confirms_itself(blender) -> None:
    """One request, and it carried confirm: false. No retry, ever."""
    fake = blender({"open_project_blend": NEEDS_CONFIRMATION})
    server.open_project_blend("gecko-bowl")

    assert [r["type"] for r in fake.requests] == ["open_project_blend"]
    assert fake.requests[0]["params"]["confirm"] is False


def test_the_confirmation_report_is_a_question_with_the_loss_named(blender) -> None:
    blender({"open_project_blend": NEEDS_CONFIRMATION})
    report = server.open_project_blend("gecko-bowl")

    assert "NOT opened" in report
    assert "nothing was touched" in report.lower()
    assert "unsaved changes" in report                 # would_lose, verbatim
    assert "12 object(s)" in report
    assert "RELAY THIS AS A QUESTION AND STOP" in report
    assert "wait for a plain yes" in report
    assert "confirm=true" in report


def test_the_confirmation_report_offers_the_save_first(blender) -> None:
    """Saving makes the choice free — offer it before asking them to discard."""
    blender({"open_project_blend": NEEDS_CONFIRMATION})
    report = server.open_project_blend("gecko-bowl")

    assert "save_project_blend" in report
    assert "copy" in report
    assert "undo" in report.lower()


def test_the_confirmation_report_keeps_blenders_own_hint(blender) -> None:
    blender({"open_project_blend": NEEDS_CONFIRMATION})
    report = server.open_project_blend("gecko-bowl")

    assert "Save scene to project" in report


# --- open_project_blend: the world replaced ---------------------------------


def test_the_opened_report_says_what_came_back(blender) -> None:
    blender({"open_project_blend": OPENED})
    report = server.open_project_blend("gecko-bowl", confirm=True)

    assert r"C:\forge\projects\gecko-bowl\gecko-bowl.blend" in report
    assert "3 object(s)" in report
    assert "kept listening" in report
    assert "9876" in report


def test_the_opened_report_says_undo_does_not_cross_a_file_load(blender) -> None:
    blender({"open_project_blend": OPENED})
    report = server.open_project_blend("gecko-bowl", confirm=True)

    assert "undo does not" in report.lower()
    assert "get_scene_info" in report


def test_confirmed_discards_are_named_in_the_report() -> None:
    result = dict(OPENED, discarded="This session had unsaved changes since "
                                    "my-gecko.blend was last saved.")
    report = util.fmt_project_open_report(result)

    assert "discarded, as confirmed" in report
    assert "my-gecko.blend" in report


def test_a_dead_server_after_the_load_is_reported_as_a_problem() -> None:
    """The pump is persistent and survives — but if it ever did not, say so."""
    report = util.fmt_project_open_report(
        dict(OPENED, server_running=False, server_port=0))

    assert "NOT listening" in report
    assert "Start under Forge Server" in report


def test_a_project_with_no_scene_file_keeps_the_add_ons_sentence(point_at) -> None:
    """The refusal names the button that makes one — do not swallow it."""
    refusal = {
        "status": "error",
        "result": None,
        "message": "gecko-bowl has no scene file yet (nothing at "
                   r"C:\forge\projects\gecko-bowl\gecko-bowl.blend"
                   "). Open the scene you want kept and press Save scene to "
                   "project — that writes it — then try opening again.",
    }
    with FakeBlender(reply(refusal)) as fake:
        point_at(fake.port)
        with pytest.raises(BackendError) as excinfo:
            server.open_project_blend("gecko-bowl")

    assert "has no scene file yet" in str(excinfo.value)
    assert "Save scene to project" in str(excinfo.value)


# --- neither one is a flow step ---------------------------------------------


def test_the_project_blend_commands_are_not_flow_steps() -> None:
    """A flow replays with nobody watching.

    ``open_project_blend`` would discard the running scene inside a saved
    sequence — with no confirmation round trip, because a flow has no model in
    the loop to relay one. ``save_project_blend`` stays out for the same
    reason: a flow that quietly writes a project file the artist did not ask
    for is a surprise, and every other route to it (the panel button, the
    Library card, this tool) has a human at the near end.
    """
    assert "open_project_blend" not in util.KNOWN_BLENDER_OPS
    assert "save_project_blend" not in util.KNOWN_BLENDER_OPS
