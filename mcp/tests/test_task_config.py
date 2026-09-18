"""The task config map — `projects/<slug>/design/task-config.json`.

The owner's directive, which is what every test here is measuring:

    "Our bipeds should be symmetric at least for what Forge does, unless the
    user specifies it shouldn't be. Honestly we should have some config map for
    the user to select values depending on the task, rather than assuming the
    user will tell the LLM all needed fields and values."

So four things have to hold, and each is a section below:

* **The template is COMPLETE.** Every task materialises every knob that kind of
  work has, at its default, with a reason. A sheet that arrives half-filled is
  the blank question it was supposed to replace.
* **The defaults are not retyped.** The floor-plan block is the level builder's
  own `DEFAULTS` and the maker choices are the real catalog's names — a ceiling
  height that means 2400 here and 2700 in the builder is a level built at a
  height nobody typed.
* **Values settle at READ time.** The consumer helper goes to disk every call.
  A value cached from three turns ago is a value the artist has since changed.
* **Refusals are sentences and leave the sheet alone.** Overwrite needs `force`,
  a bad choice names the choices, a number out of range names the range.

`projects/` is redirected to a tmp_path for every test in this module, exactly
as `test_design.py` does it, so the real one is never touched. Nothing here
binds a port or talks to a backend: a settings sheet is decided before any
geometry exists, so it needs neither.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client

from forge_mcp import config, server, task_config, util
from forge_mcp.errors import ForgeError


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect projects/ so no test can write into the real repo folder."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


def call(name: str, arguments: dict[str, Any] | None = None):
    """One tools/call over an in-memory session; returns the CallToolResult."""

    async def run():
        async with Client(server.app) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def text_of(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def sheet_on_disk(projects_dir: Path, slug: str) -> dict:
    path = projects_dir / slug / "design" / "task-config.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The templates are complete
# ---------------------------------------------------------------------------


def test_every_task_has_a_template_and_a_blurb() -> None:
    assert set(task_config.TEMPLATES) == set(task_config.TASKS)
    assert set(task_config.TASK_BLURB) == set(task_config.TASKS)


@pytest.mark.parametrize("task", task_config.TASKS)
def test_a_template_fills_every_setting_in(task: str) -> None:
    """A materialised sheet has no blanks: that is the whole idea."""
    settings = task_config.template(task)
    assert settings, task
    for name, entry in settings.items():
        assert "value" in entry and "default" in entry, name
        assert entry["value"] == entry["default"], name
        assert set(entry) <= set(task_config.SETTING_KEYS), name


@pytest.mark.parametrize("task", task_config.TASKS)
def test_every_setting_says_why_or_offers_choices(task: str) -> None:
    """A knob with no reason and no menu teaches the artist nothing."""
    for name, entry in task_config.template(task).items():
        assert entry.get("why") or entry.get("choices"), f"{task}.{name}"


@pytest.mark.parametrize("task", task_config.TASKS)
def test_a_template_is_json_round_trippable(task: str) -> None:
    sheet = task_config.new_sheet("demo", task)
    assert json.loads(json.dumps(sheet)) == sheet


def test_the_character_template_carries_the_owners_knobs() -> None:
    settings = task_config.template("character")
    assert set(settings) == {
        "symmetry", "target_engine", "poly_budget_desktop", "lod_chain",
        "texture_res", "rig", "correctives", "face_detail_pass",
    }
    assert settings["target_engine"]["default"] == "godot"
    assert settings["poly_budget_desktop"]["default"] == 15000
    assert settings["poly_budget_desktop"]["unit"] == "triangles"
    assert settings["lod_chain"]["default"] == "auto"
    assert settings["texture_res"]["default"] == 2048
    assert settings["rig"]["default"] == "biped_ik"
    assert settings["correctives"]["default"] is True
    assert settings["face_detail_pass"]["default"] is False


def test_bipeds_are_symmetric_unless_the_artist_says_otherwise() -> None:
    """THE directive. Symmetry is a setting, it defaults on, and it says why."""
    symmetry = task_config.template("character")["symmetry"]
    assert symmetry["default"] is True and symmetry["value"] is True
    assert "symmetric" in symmetry["why"]
    assert "unless you say otherwise" in symmetry["why"]


def test_the_part_template_carries_the_print_knobs() -> None:
    settings = task_config.template("part")
    assert set(settings) == {"printer_profile", "wall_mm", "bed_fit",
                             "export_formats"}
    assert settings["printer_profile"]["default"] == str(
        config.DEFAULT_PRINTER_PATH)
    assert settings["wall_mm"]["unit"] == "mm"
    # The architecture's law, encoded as the default rather than as a hope.
    assert settings["bed_fit"]["default"] == "design_full_size"
    assert settings["export_formats"]["default"] == ["stl"]
    assert set(settings["export_formats"]["choices"]) == {"stl", "step", "3mf"}


def test_the_device_template_takes_its_choices_from_the_real_catalog() -> None:
    """Not a written-down parts list: the catalog the wiring maths reads."""
    from forge_mcp import maker

    settings = task_config.template("device")
    assert set(settings) == {"battery", "voltage_v", "switch", "led"}
    for name, category in (("battery", "power"), ("switch", "switch"),
                           ("led", "light")):
        real = sorted(record["name"]
                      for record in maker.catalog(category)["records"])
        assert settings[name]["choices"] == real, name
        assert settings[name]["default"] in real, name
    assert settings["voltage_v"]["unit"] == "V"


def test_the_floorplan_template_mirrors_the_builders_own_defaults() -> None:
    """A ceiling height that means two things is a level nobody asked for."""
    from forge_mcp import floorplan

    real = floorplan.defaults()
    settings = task_config.template("floorplan")
    assert set(settings) == set(real)
    for name, default in real.items():
        assert settings[name]["default"] == default, name
    assert settings["ceiling_mm"]["default"] == 2400.0
    assert settings["wall_mm"]["default"] == 100.0
    assert settings["door_w_mm"]["default"] == 820.0
    assert settings["door_h_mm"]["default"] == 2040.0


def test_the_written_down_plan_defaults_match_the_service() -> None:
    """The fallback exists for a checkout with no service/; it must not drift."""
    from forge_mcp import floorplan

    assert task_config.PLAN_DEFAULTS_FALLBACK == floorplan.defaults()
    plan_module, _appliance = floorplan.modules()
    assert task_config.PLAN_ANCHORS_FALLBACK == tuple(plan_module.ANCHORS)


def test_the_mold_template_carries_the_casting_knobs() -> None:
    settings = task_config.template("mold")
    assert set(settings) == {"mode", "shell_mm", "draft_deg",
                             "registration_keys", "silicone"}
    assert settings["mode"]["default"] == "printed_negative"
    assert set(settings["mode"]["choices"]) == {"printed_negative",
                                                "master_box"}
    assert settings["shell_mm"]["default"] == 4.0
    assert settings["registration_keys"]["default"] == 4
    assert "shore" in settings["silicone"]["why"]


def test_a_task_that_is_not_one_is_refused_with_the_list() -> None:
    with pytest.raises(ForgeError) as caught:
        task_config.normalize_task("vehicle")
    message = str(caught.value)
    for name in task_config.TASKS:
        assert name in message


def test_the_sheet_is_not_in_the_reading_order() -> None:
    """It is the machine's copy of what was decided, like floorplan.json.

    `DESIGN_READING_ORDER` is mirrored byte for byte in assistant/bridge.py, so
    a name added here that is not added there is a sheet that reads one way in
    the tool report and another on the Library card.
    """
    assert task_config.CONFIG_FILENAME not in util.DESIGN_READING_ORDER


# ---------------------------------------------------------------------------
# init / get / set, round trip
# ---------------------------------------------------------------------------


def test_init_writes_the_sheet_where_design_docs_live(projects_dir: Path) -> None:
    result = call("task_config_init", {"project": "gecko",
                                       "task": "character"})
    assert not result.is_error, text_of(result)
    path = projects_dir / "gecko" / "design" / "task-config.json"
    assert path.is_file()
    sheet = json.loads(path.read_text(encoding="utf-8"))
    assert sheet["task"] == "character"
    assert sheet["project"] == "gecko"
    assert sheet["version"] == task_config.CONFIG_VERSION
    assert sheet["settings"]["symmetry"]["value"] is True
    assert sheet["history"] and "materialised" in sheet["history"][0]["note"]


def test_the_project_name_is_slugged_like_every_other_writer(projects_dir):
    result = call("task_config_init", {"project": "a Small Gecko!",
                                       "task": "character"})
    assert not result.is_error, text_of(result)
    assert (projects_dir / "a-small-gecko" / "design" /
            "task-config.json").is_file()


def test_init_does_not_need_the_project_to_exist(projects_dir: Path) -> None:
    """The sheet comes before the part, exactly as the rest of design/ does."""
    assert not (projects_dir / "ankle-fan").exists()
    result = call("task_config_init", {"project": "ankle fan", "task": "device"})
    assert not result.is_error, text_of(result)
    assert (projects_dir / "ankle-fan" / "design" /
            "task-config.json").is_file()


def test_the_init_report_echoes_every_value(projects_dir: Path) -> None:
    report = text_of(call("task_config_init", {"project": "gecko",
                                               "task": "character"}))
    for name in task_config.template("character"):
        assert name in report, name
    assert "none changed from their defaults yet" in report
    assert "8 settings" in report


def test_get_echoes_the_sheet_without_writing(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    before = path.read_bytes()
    report = text_of(call("task_config_get", {"project": "gecko"}))
    assert path.read_bytes() == before
    assert "biped_ik" in report and "godot" in report


def test_get_of_a_project_with_no_sheet_says_how_to_get_one(projects_dir) -> None:
    result = call("task_config_get", {"project": "gecko"})
    assert result.is_error
    assert "task_config_init" in text_of(result)


def test_set_changes_one_value_and_leaves_the_rest(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko",
                                      "name": "poly_budget_desktop",
                                      "value": 8000})
    assert not result.is_error, text_of(result)
    sheet = sheet_on_disk(projects_dir, "gecko")
    assert sheet["settings"]["poly_budget_desktop"]["value"] == 8000
    assert sheet["settings"]["poly_budget_desktop"]["default"] == 15000
    assert sheet["settings"]["symmetry"]["value"] is True


def test_set_records_the_change_in_history(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    history = sheet_on_disk(projects_dir, "gecko")["history"]
    assert history[-1]["setting"] == "symmetry"
    assert history[-1]["from"] is True and history[-1]["to"] is False
    assert history[-1]["date"]


def test_asymmetry_is_an_explicit_choice_on_the_sheet(projects_dir: Path) -> None:
    """The directive's other half: it can be turned off, and then it is recorded."""
    call("task_config_init", {"project": "gecko", "task": "character"})
    report = text_of(call("task_config_set", {"project": "gecko",
                                              "name": "symmetry",
                                              "value": False}))
    assert "true -> false" in report
    assert "CHANGED" in report
    assert task_config.setting("gecko", "symmetry") is False


def test_setting_it_back_says_it_is_back_at_its_default(projects_dir) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    report = text_of(call("task_config_set", {"project": "gecko",
                                              "name": "symmetry",
                                              "value": True}))
    assert "back at its default" in report
    assert "none changed from their defaults yet" in report


def test_a_list_setting_round_trips(projects_dir: Path) -> None:
    call("task_config_init", {"project": "bracket", "task": "part"})
    call("task_config_set", {"project": "bracket", "name": "export_formats",
                             "value": ["stl", "step"]})
    assert task_config.setting("bracket", "export_formats") == ["stl", "step"]


def test_a_floorplan_sheet_round_trips_through_all_three(projects_dir) -> None:
    call("task_config_init", {"project": "upstairs flat", "task": "floorplan"})
    call("task_config_set", {"project": "upstairs flat", "name": "ceiling_mm",
                             "value": 2700})
    report = text_of(call("task_config_get", {"project": "upstairs flat"}))
    assert "2700" in report
    assert "CHANGED (default 2400)" in report


# ---------------------------------------------------------------------------
# The echo-back marks what is no longer default
# ---------------------------------------------------------------------------


def test_the_report_prints_settings_nobody_touched(projects_dir: Path) -> None:
    """The Sloyd pattern: the control surface teaches itself by arriving full.

    A report that listed only what somebody had already thought to mention
    would tell the artist exactly the things they already knew.
    """
    call("task_config_init", {"project": "gecko", "task": "character"})
    report = text_of(call("task_config_set", {"project": "gecko",
                                              "name": "texture_res",
                                              "value": 1024}))
    for name in task_config.template("character"):
        assert name in report, name
    assert report.count("[default]") == 7
    assert "CHANGED (default 2048)" in report


def test_the_report_names_the_changed_settings_in_its_tally(projects_dir) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    report = text_of(call("task_config_set", {"project": "gecko",
                                              "name": "rig",
                                              "value": "biped_fk"}))
    assert "2 changed from default: rig, symmetry" in report


def test_the_report_says_values_settle_at_read_time(projects_dir: Path) -> None:
    report = text_of(call("task_config_init", {"project": "gecko",
                                               "task": "character"}))
    assert "AT THE MOMENT IT RUNS" in report
    assert "conversation memory" in report


def test_the_report_carries_the_path_and_the_choices(projects_dir: Path) -> None:
    report = text_of(call("task_config_init", {"project": "gecko",
                                               "task": "character"}))
    assert str(projects_dir / "gecko" / "design" / "task-config.json") in report
    assert "one of: godot, unity, unreal, gltf" in report


def test_changed_is_in_sheet_order(projects_dir: Path) -> None:
    sheet = task_config.new_sheet("gecko", "character")
    assert task_config.changed(sheet) == []
    sheet["settings"]["rig"]["value"] = "none"
    sheet["settings"]["symmetry"]["value"] = False
    assert task_config.changed(sheet) == ["symmetry", "rig"]


# ---------------------------------------------------------------------------
# Settle at read time
# ---------------------------------------------------------------------------


def test_a_consumer_reads_the_current_sheet_not_a_remembered_value(
        projects_dir: Path) -> None:
    """THE law. `setting()` goes to disk every single call."""
    call("task_config_init", {"project": "gecko", "task": "character"})
    assert task_config.setting("gecko", "poly_budget_desktop") == 15000
    call("task_config_set", {"project": "gecko",
                             "name": "poly_budget_desktop", "value": 4000})
    assert task_config.setting("gecko", "poly_budget_desktop") == 4000


def test_a_consumer_sees_an_edit_made_outside_the_tools(projects_dir: Path):
    """The artist opening the file in an editor is a supported way to edit it."""
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["settings"]["symmetry"]["value"] = False
    path.write_text(json.dumps(sheet), encoding="utf-8")
    assert task_config.setting("gecko", "symmetry") is False


def test_a_consumer_of_a_project_with_no_sheet_gets_its_fallback(projects_dir):
    assert task_config.setting("nothing-here", "symmetry", True) is True
    assert task_config.setting("nothing-here", "symmetry") is None
    assert task_config.settled("nothing-here") == {}


def test_a_consumer_of_a_setting_that_does_not_exist_gets_its_fallback(
        projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    assert task_config.setting("gecko", "wall_mm", 2.0) == 2.0


def test_settled_is_the_whole_sheet_as_plain_values(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    values = task_config.settled("gecko")
    assert values["symmetry"] is False
    assert values["target_engine"] == "godot"
    assert set(values) == set(task_config.template("character"))


def test_exists_answers_without_raising(projects_dir: Path) -> None:
    assert task_config.exists("gecko") is False
    assert task_config.exists("../evil") is False
    call("task_config_init", {"project": "gecko", "task": "character"})
    assert task_config.exists("gecko") is True


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_init_refuses_to_overwrite_a_sheet(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    result = call("task_config_init", {"project": "gecko", "task": "character"})
    assert result.is_error
    message = text_of(result)
    assert "task_config_set" in message and "force=true" in message
    # And the artist's value is exactly where they left it.
    assert task_config.setting("gecko", "symmetry") is False


def test_force_rebuilds_the_sheet_from_the_template(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "symmetry",
                             "value": False})
    result = call("task_config_init", {"project": "gecko", "task": "character",
                                       "force": True})
    assert not result.is_error, text_of(result)
    assert task_config.setting("gecko", "symmetry") is True
    assert "Rebuilt" in text_of(result)


def test_force_can_switch_a_project_to_another_task(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_init", {"project": "gecko", "task": "mold",
                              "force": True})
    assert sheet_on_disk(projects_dir, "gecko")["task"] == "mold"


def test_a_setting_that_does_not_exist_names_the_ones_that_do(projects_dir):
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko", "name": "polycount",
                                      "value": 9000})
    assert result.is_error
    message = text_of(result)
    assert "poly_budget_desktop" in message and "symmetry" in message
    assert "Nothing was changed" in message


def test_a_value_outside_a_choice_list_names_the_choices(projects_dir: Path):
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko", "name": "rig",
                                      "value": "quadruped"})
    assert result.is_error
    assert "biped_ik, biped_fk, none" in text_of(result)
    assert task_config.setting("gecko", "rig") == "biped_ik"


def test_a_number_outside_its_range_names_the_range(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko",
                                      "name": "poly_budget_desktop",
                                      "value": 5000000})
    assert result.is_error
    assert "200000" in text_of(result)
    assert task_config.setting("gecko", "poly_budget_desktop") == 15000


def test_a_number_below_its_range_is_refused(projects_dir: Path) -> None:
    call("task_config_init", {"project": "bracket", "task": "part"})
    result = call("task_config_set", {"project": "bracket", "name": "wall_mm",
                                      "value": 0.1})
    assert result.is_error
    assert "0.4" in text_of(result)


def test_a_numeric_choice_list_is_enforced(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko", "name": "texture_res",
                                      "value": 3000})
    assert result.is_error
    assert "512, 1024, 2048, 4096" in text_of(result)


def test_words_are_refused_where_a_number_belongs(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko",
                                      "name": "poly_budget_desktop",
                                      "value": "lots"})
    assert result.is_error
    assert "is a number" in text_of(result)


def test_a_non_answer_is_refused_where_a_yes_no_belongs(projects_dir: Path):
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("task_config_set", {"project": "gecko", "name": "symmetry",
                                      "value": "maybe"})
    assert result.is_error
    assert "yes/no" in text_of(result)
    assert task_config.setting("gecko", "symmetry") is True


def test_a_yes_no_accepts_the_words_a_person_would_type(projects_dir: Path):
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "correctives",
                             "value": "no"})
    assert task_config.setting("gecko", "correctives") is False


def test_a_choice_is_matched_case_insensitively(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("task_config_set", {"project": "gecko", "name": "target_engine",
                             "value": "Godot"})
    assert task_config.setting("gecko", "target_engine") == "godot"


def test_a_list_setting_refuses_a_format_that_is_not_one(projects_dir) -> None:
    call("task_config_init", {"project": "bracket", "task": "part"})
    result = call("task_config_set", {"project": "bracket",
                                      "name": "export_formats",
                                      "value": ["stl", "gcode"]})
    assert result.is_error
    assert "stl, step, 3mf" in text_of(result)
    assert task_config.setting("bracket", "export_formats") == ["stl"]


def test_a_whole_number_setting_refuses_a_fraction(projects_dir: Path) -> None:
    call("task_config_init", {"project": "figure", "task": "mold"})
    result = call("task_config_set", {"project": "figure",
                                      "name": "registration_keys",
                                      "value": 2.5})
    assert result.is_error
    assert "whole number" in text_of(result)


@pytest.mark.parametrize(
    "given", ["../evil", "..\\evil", "C:\\Windows\\thing", "/etc/passwd",
              "~/notes", "%APPDATA%", "$HOME"],
)
def test_a_path_shaped_project_is_refused_not_cleaned(given: str) -> None:
    """`save_design_doc`'s rules, reused verbatim — this is the same folder."""
    result = call("task_config_init", {"project": given, "task": "character"})
    assert result.is_error
    message = text_of(result)
    assert "path" in message or "environment variable" in message


def test_a_sheet_that_is_not_json_is_a_sentence_not_a_traceback(projects_dir):
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    path.write_text("{ not json", encoding="utf-8")
    result = call("task_config_get", {"project": "gecko"})
    assert result.is_error
    assert "force=true" in text_of(result)


def test_a_sheet_with_an_unknown_task_is_refused(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["task"] = "vehicle"
    path.write_text(json.dumps(sheet), encoding="utf-8")
    result = call("task_config_get", {"project": "gecko"})
    assert result.is_error
    assert "character" in text_of(result)


def test_a_sheet_with_no_settings_is_refused(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    path.write_text(json.dumps({"task": "character", "settings": {}}),
                    encoding="utf-8")
    result = call("task_config_get", {"project": "gecko"})
    assert result.is_error
    assert "task_config_init" in text_of(result)


def test_a_malformed_entry_is_refused_by_name(projects_dir: Path) -> None:
    call("task_config_init", {"project": "gecko", "task": "character"})
    path = projects_dir / "gecko" / "design" / "task-config.json"
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["settings"]["symmetry"] = True
    path.write_text(json.dumps(sheet), encoding="utf-8")
    result = call("task_config_get", {"project": "gecko"})
    assert result.is_error
    assert "symmetry" in text_of(result)


# ---------------------------------------------------------------------------
# It is wired into the design phase
# ---------------------------------------------------------------------------


def test_saving_a_design_doc_names_the_settings_sheet(projects_dir: Path):
    """A control surface nobody is told about is a control surface nobody uses."""
    call("task_config_init", {"project": "gecko", "task": "character"})
    result = call("save_design_doc", {"project": "gecko",
                                      "filename": "requirements.md",
                                      "content": "# Gecko\n\n1. 180 mm long\n"})
    report = text_of(result)
    assert "settings sheet" in report
    assert str(projects_dir / "gecko" / "design" / "task-config.json") in report
    assert "task_config_get" in report


def test_saving_a_design_doc_with_no_sheet_says_how_to_make_one(projects_dir):
    result = call("save_design_doc", {"project": "gecko",
                                      "filename": "requirements.md",
                                      "content": "# Gecko\n\n1. 180 mm\n"})
    report = text_of(result)
    assert "no settings sheet yet" in report
    assert "task_config_init" in report


def test_the_sheet_is_listed_with_the_other_design_documents(projects_dir):
    call("task_config_init", {"project": "gecko", "task": "character"})
    call("save_design_doc", {"project": "gecko", "filename": "requirements.md",
                             "content": "# Gecko\n"})
    names = [item["file"] for item in util.design_documents("gecko")]
    # Reading order first, then alphabetically — like floorplan.json.
    assert names == ["requirements.md", "task-config.json"]


# -- a real consumer, settling at read time ---------------------------------

#: The smallest plan that validates: one room, one wall, no openings.
PLAN = {
    "version": 1, "units": "mm",
    "rooms": [{"id": "room-a", "polygon_mm": [[0, 0], [3000, 0], [3000, 2400],
                                              [0, 2400]]}],
    "walls": [{"id": "wall-01", "from_mm": [0, 0], "to_mm": [3000, 0]}],
}


def test_a_plan_takes_its_ceiling_from_the_sheet(projects_dir: Path) -> None:
    """`floorplan_validate` is a real consumer, and it reads at the moment it runs."""
    call("task_config_init", {"project": "flat", "task": "floorplan"})
    call("task_config_set", {"project": "flat", "name": "ceiling_mm",
                             "value": 2700})
    report = text_of(call("floorplan_validate", {"project": "flat",
                                                 "plan": PLAN, "save": False}))
    assert "from the settings sheet" in report and "ceiling_mm" in report
    assert "2700" in report


def test_a_number_the_plan_states_beats_the_sheet(projects_dir: Path) -> None:
    """Plan, then sheet, then the service's DEFAULTS — in that order."""
    call("task_config_init", {"project": "flat", "task": "floorplan"})
    call("task_config_set", {"project": "flat", "name": "ceiling_mm",
                             "value": 2700})
    plan = dict(PLAN, defaults={"ceiling_mm": 3000})
    report = text_of(call("floorplan_validate", {"project": "flat",
                                                 "plan": plan, "save": False}))
    assert "3000" in report and "2700" not in report
    # The other sheet values still fill in what the plan left open.
    assert "wall_mm" in report


def test_a_project_with_no_sheet_validates_exactly_as_before(projects_dir):
    report = text_of(call("floorplan_validate", {"project": "flat",
                                                 "plan": PLAN, "save": False}))
    assert "from the settings sheet" not in report
    assert "2400" in report


def test_a_sheet_for_another_task_is_not_read_as_plan_defaults(projects_dir):
    call("task_config_init", {"project": "flat", "task": "character"})
    assert task_config.plan_defaults("flat") == {}
    report = text_of(call("floorplan_validate", {"project": "flat",
                                                 "plan": PLAN, "save": False}))
    assert "from the settings sheet" not in report


def test_the_consumer_sees_the_change_on_the_very_next_call(projects_dir):
    """No cache anywhere on the path: set, then validate, and it is the new one."""
    call("task_config_init", {"project": "flat", "task": "floorplan"})
    first = text_of(call("floorplan_validate", {"project": "flat",
                                                "plan": PLAN, "save": False}))
    assert "2400" in first
    call("task_config_set", {"project": "flat", "name": "ceiling_mm",
                             "value": 3200})
    second = text_of(call("floorplan_validate", {"project": "flat",
                                                 "plan": PLAN, "save": False}))
    assert "3200" in second


def test_the_mention_never_raises_on_an_odd_name() -> None:
    assert task_config.mention("../evil") == ""
    assert task_config.mention("") == ""


def test_the_server_exposes_exactly_the_three_tools() -> None:
    async def run():
        async with Client(server.app) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = asyncio.run(run())
    assert {"task_config_init", "task_config_get",
            "task_config_set"} <= names


def test_the_tools_take_the_arguments_the_contract_says() -> None:
    async def run():
        async with Client(server.app) as client:
            return {tool.name: tool.input_schema
                    for tool in (await client.list_tools()).tools}

    schemas = asyncio.run(run())
    assert set(schemas["task_config_init"]["required"]) == {"project", "task"}
    assert set(schemas["task_config_get"]["required"]) == {"project"}
    assert set(schemas["task_config_set"]["required"]) == {"project", "name",
                                                           "value"}
    tasks = schemas["task_config_init"]["properties"]["task"]
    assert set(tasks.get("enum", [])) == set(task_config.TASKS)
