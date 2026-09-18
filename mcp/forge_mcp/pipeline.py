"""The staged build pipeline — `projects/<slug>/design/build-plan.json`.

THE PROBLEM IT SOLVES, IN THE OWNER'S WORDS
===========================================
A character build is *"make the mesh"*, then *"check it"*, then *"now rig it"* —
a staged pipeline the artist drives step by step, **never an assumed one-shot**.
Each stage ends with its gate verdict and its artifacts, and **a red gate blocks
the next stage**.

Without a file that says so, the stages live in the conversation, which means
they live nowhere: the next turn re-rolls the whole build, a failed gate is
stepped over because nothing remembers it was red, and "what state is this
character in" has no answer but scrollback.

WHAT THIS MODULE IS AND IS NOT
==============================
It is **the state machine, not the builder**. No function here runs Blender,
generates a mesh, fits a skeleton or measures anything. The assistant does the
work between the stages and writes the verdict back in with
:func:`record`; these tools only say what stage the build is at, what may
happen next, and what the last gate measured.

It is **not a second writer**. The plan goes through ``save_design_doc``'s own
rules — :func:`~forge_mcp.util.design_paths`,
:func:`~forge_mcp.util.normalize_design_content` — exactly as
``task-config.json`` does, so one set of slug/traversal/JSON checks guards
``projects/<slug>/design/`` and this file adds no new door onto it.

THE THREE LAWS
==============
* **The plan settles at read time.** Every tool reads the file at the moment it
  runs. Nothing caches, and no stage verdict is ever taken from a tool argument
  a model filled in from memory.
* **Additive over what is already on disk.** ``build-plan.json`` already exists
  for real projects (the werewolf's is the live example) carrying ``notes`` and
  a ``components`` list. This module adds ``task`` and ``stages`` **beside**
  them and rewrites neither: a plan written before stages existed reads, renders
  and round-trips without losing a byte.
* **An override is signed.** Stepping over a red gate is allowed — the artist is
  in charge — but it takes ``override=true`` plus a *who* and a *why*, the
  stepped-over stage is marked ``overridden`` rather than quietly passed, and
  both stages carry it in their history for ever.

WHERE THE CHAIN COMES FROM
==========================
The chain is chosen by the project's **task**, not by an argument: the plan's own
``task`` if it has one, else ``design/task-config.json``'s. Precedence is
plan → sheet, the same shape ``floorplan_validate`` uses, and a project with
neither is a refusal naming ``task_config_init`` rather than a guess.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import task_config
from .errors import ForgeError
from .util import (
    design_documents,
    design_paths,
    normalize_design_content,
    project_slug,
)

#: The one filename. A design document like any other, so it passes
#: `design_filename`'s alphabet unchanged and files alphabetically — it is the
#: machine's record of what was built, not a thing a person reads front to back.
PLAN_FILENAME = "build-plan.json"

#: Bumped only when the shape changes in a way a reader must notice. Adding the
#: stages block did NOT bump it: a version-1 reader that knows nothing about
#: stages still reads every plan correctly, which is what additive means.
PLAN_VERSION = 1

#: The five verdicts a stage can carry.
STATUSES: Tuple[str, ...] = (
    "pending", "in_progress", "passed", "failed", "overridden",
)

#: What :func:`record` accepts. ``overridden`` is deliberately not here — it is
#: not a verdict anybody measures, it is what :func:`advance` writes onto a red
#: stage somebody signed their way past.
RECORDABLE: Tuple[str, ...] = ("pending", "in_progress", "passed", "failed")

#: Green enough for the next stage to start.
GREEN: Tuple[str, ...] = ("passed", "overridden")

#: What a stage entry may carry. `id`, `title` and `status` are always there.
STAGE_KEYS: Tuple[str, ...] = (
    "id", "title", "status", "gate", "artifacts", "numbers", "history",
    "does", "tools",
)

#: One marker per status, so the board scans as a column rather than as prose.
MARKERS: Dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "passed": "[x]",
    "failed": "[!]",
    "overridden": "[~]",
}


# ---------------------------------------------------------------------------
# The stage templates
# ---------------------------------------------------------------------------
#
# Each entry is `(id, title, does, gate, tools)`. A list, not a dict, because the
# ORDER *is* the pipeline: it is what "the next stage" means and what a skip is
# measured against.


def _character_stages() -> List[Tuple[str, str, str, List[str], List[str]]]:
    """The owner's chain, verbatim, with a real Forge gate on every step.

    Ten stages because a character really is ten decisions, and the artist
    drives them one at a time: *make the mesh*, *check it*, *now rig it*.
    """
    return [
        ("reference",
         "Reference — the pictures the build is measured against",
         "File the artist's references under design/refs/ and read them back in "
         "words they can correct. Nothing is generated from a picture nobody "
         "agreed was the picture.",
         ["references_on_disk"],
         ["save_design_doc", "load_reference"]),
        ("design",
         "Design — requirements, concept diagram, settings sheet, sign-off",
         "The design phase: at most five questions in one message, then "
         "requirements.md, a hand-written concept.svg and the task-config sheet "
         "materialised at its defaults. Ends at the artist's yes.",
         ["sign_off", "settings_sheet"],
         ["save_design_doc", "task_config_init", "task_config_get"]),
        ("generate",
         "Generate — the base mesh exists",
         "A mesh in the scene, however rough. Judged on existence and "
         "watertightness only: shape is the next stage's problem and beauty is "
         "the artist's.",
         ["mesh_exists", "watertight"],
         ["generate_3d", "partforge_generate", "get_scene_info"]),
        ("clean",
         "Clean — symmetrize, retopo, bake, unwrap",
         "The finishing chain in its one correct order: repair, symmetrize to "
         "the task-config setting, retopo, UV unwrap, bake high->low, LODs. "
         "Every step after this one reads the atlas this one wrote.",
         ["symmetry_residual_mm", "poly_budget_triangles", "uv_coverage"],
         ["symmetrize", "rigforge_retopo", "rigforge_auto_uv"]),
        ("verify_mesh",
         "Verify the mesh — truth, not beauty",
         "The geometric gate before a single bone is placed. A defect fixed "
         "here costs a retopo; the same defect found after skinning costs the "
         "rig, the correctives and the clips.",
         ["verify_design", "mesh_diagnose", "silhouette_iou"],
         ["verify_design", "mesh_diagnose", "turntable"]),
        ("rig",
         "Rig — landmarks, metarig, generated control rig",
         "The human rigger's workflow: orient, symmetrize, sides from geometry, "
         "landmarks, X-mirror, then Rigify. Legs IK, arms FK, poles on.",
         ["rig_check.asymmetry_mm", "rig_check.side_naming",
          "rig_check.centering"],
         ["rigforge_metarig", "rigforge_generate_rig", "rig_check"]),
        ("skin",
         "Skin — weights that belong to the right bone",
         "Bind, then measure the leakage. Stray mass between two bones 380 mm "
         "apart is a tagging problem, not a painting problem, and the gate says "
         "which.",
         ["rig_check.overlap", "rig_check.continuity"],
         ["rigforge_weights", "rig_check"]),
        ("correctives",
         "Correctives — the collapsed knee, fixed with a number",
         "A corrective shape key per bent joint, driven by bend angle. The fix "
         "ladder is law: correctives first quoting before/after, weight "
         "painting second, never a blind density pass (50.3% -> 51.1%).",
         ["rig_check.volume"],
         ["rig_check"]),
        ("animate",
         "Animate — clips that do not slide",
         "Walk cycle and whatever else the character needs, each measured on "
         "the foot-slide metric at the ball of the foot.",
         ["animation_check.foot_slide_mm"],
         ["rigforge_action", "rigforge_keyframe", "render_animation"]),
        ("export",
         "Export — the .glb the engine actually loads",
         "Baked deform rig, LOD chain sharing one atlas, correctives baked from "
         "their drivers into morph animation. glTF has no concept of a driver, "
         "so an unbaked morph reaches the engine pinned at zero, silently.",
         ["glb_written", "morph_targets", "animated_nodes"],
         ["rigforge_export_godot"]),
    ]


def _part_stages() -> List[Tuple[str, str, str, List[str], List[str]]]:
    """The parametric print lane: five stages, and the script is the model."""
    return [
        ("design",
         "Design — requirements, concept diagram, settings sheet, sign-off",
         "A functional, wearable, multi-component or novel part gets the design "
         "phase first. A trivially-shaped part skips straight to author.",
         ["sign_off", "settings_sheet"],
         ["save_design_doc", "task_config_init"]),
        ("author",
         "Author — write the part script",
         "partforge_new_part is the only tool that writes source, and it "
         "validates the PARAMS block through the service before anything "
         "touches disk.",
         ["params_parse"],
         ["partforge_new_part", "partforge_parse_params"]),
        ("generate",
         "Generate — the solid builds and lands in Blender",
         "Build the solid and push it into the scene in one call, then look at "
         "it. A part that will not build is a script bug, not a design fault.",
         ["watertight", "bounding_box_mm"],
         ["partforge_generate", "partforge_open_in_panel", "render_preview"]),
        ("check",
         "Check — will this print",
         "Bed fit, minimum wall, overhangs, watertight. A bed_fit fail with a "
         "feasible split is print PLANNING and never justifies shrinking the "
         "part: the mode object it hands back goes verbatim to segment.",
         ["bed_fit", "min_wall", "overhangs", "watertight"],
         ["partforge_check", "partforge_segment"]),
        ("export",
         "Export — files for the slicer",
         "One file per format the sheet asks for, plus the packed plate when "
         "the part prints as pieces.",
         ["files_written"],
         ["partforge_export", "partforge_export_segments"]),
    ]


def _device_stages() -> List[Tuple[str, str, str, List[str], List[str]]]:
    """Maker mode: the part lane with the circuit decided before the geometry."""
    return [
        ("design",
         "Design — requirements, an ANIMATED mechanism.svg, sign-off",
         "A functional design's diagram moves: the press stroke, the latch, the "
         "LED on and off, with the plan's real numbers labelled.",
         ["sign_off", "settings_sheet"],
         ["save_design_doc", "task_config_init"]),
        ("circuit",
         "Circuit — the components, the voltage and the resistor",
         "Pick the real catalog parts and compute the series resistor from the "
         "LED's own forward voltage. The pockets the geometry needs come from "
         "the components, not the other way round.",
         ["voltage_v", "resistor_ohm", "components_fit"],
         ["maker_components", "circuit_plan", "wiring_guide", "plunger_plan"]),
        ("author",
         "Author — write the part script with the pockets in it",
         "The cell pocket, the switch cutout and the light pipe are part of the "
         "solid, sized from the catalog record.",
         ["params_parse"],
         ["partforge_new_part", "partforge_parse_params"]),
        ("generate",
         "Generate — the solid builds and lands in Blender",
         "Build it, look at it, and check the components actually drop in.",
         ["watertight", "bounding_box_mm"],
         ["partforge_generate", "render_preview"]),
        ("check",
         "Check — will this print, and does the mechanism move",
         "The print gates, plus the demo film of the thing doing what it does.",
         ["bed_fit", "min_wall", "overhangs", "watertight"],
         ["partforge_check", "animate_object", "render_animation"]),
        ("export",
         "Export — files for the slicer, and the wiring the artist follows",
         "The parts to print and the wiring guide to solder. Forge never buys "
         "anything: say what they still have to.",
         ["files_written"],
         ["partforge_export", "wiring_guide"]),
    ]


def _floorplan_stages() -> List[Tuple[str, str, str, List[str], List[str]]]:
    """Phase 19: the drawing becomes a plan file, and the plan file IS the model."""
    return [
        ("extract",
         "Extract — the drawing becomes numbers",
         "Deterministic extraction, never eyeballed coordinates. A room read "
         "off a picture by guess is a wall in the wrong place with nothing to "
         "blame.",
         ["rooms", "openings", "fixtures"],
         ["floorplan_extract"]),
        ("validate",
         "Validate — the plan reads clean",
         "Every optional number resolved, every fixture label matched against "
         "the appliance table, every refusal a sentence naming the entry.",
         ["plan_reads_clean", "defaults_resolved"],
         ["floorplan_validate"]),
        ("echo",
         "Echo back — the artist corrects the diagram, not the mesh",
         "Render the READING as design/floorplan.svg — rooms coloured, doors "
         "with swing arcs, labelled footprints at their intended real sizes — "
         "and build nothing until they say yes. Mandatory gate.",
         ["sign_off"],
         ["save_design_doc"]),
        ("build",
         "Build — walls, openings and fixtures in Blender",
         "Incremental by id: diff first so the reply can say which ids rebuild "
         "and which keep the objects they already have, hand edits included.",
         ["objects_built"],
         ["floorplan_diff", "floorplan_build"]),
        ("reconcile",
         "Reconcile — the scene and the plan still agree",
         "What moved in Blender that the plan does not know about, and what the "
         "plan says that the scene does not show.",
         ["drift"],
         ["floorplan_reconcile"]),
    ]


def _mold_stages() -> List[Tuple[str, str, str, List[str], List[str]]]:
    """Casting: undercuts decide the mode, so they are measured before the mold."""
    return [
        ("design",
         "Design — requirements, settings sheet, sign-off",
         "Mode, shell, draft, registration keys and which silicone. Shore "
         "hardness decides how much undercut will still lift out.",
         ["sign_off", "settings_sheet"],
         ["save_design_doc", "task_config_init"]),
        ("source",
         "Source — the figure that is going to be moulded",
         "A parametric part, a generated figure or an imported mesh. It has to "
         "be watertight before anything is cut from it.",
         ["watertight", "source_on_disk"],
         ["partforge_generate", "check_model", "generate_3d"]),
        ("undercut",
         "Undercut check — will it come out of a mould at all",
         "FIRST, before make_mold, because its verdict is what picks the mode. "
         "A figure that cannot demould is a design answer, not a mould bug.",
         ["undercut_verdict", "parting_z_mm"],
         ["undercut_check"]),
        ("mold",
         "Mould — two watertight halves with draft, keys, spout and vents",
         "Both halves re-verified watertight; non-manifold output is a refusal, "
         "not a warning.",
         ["halves_watertight", "draft_deg", "registration_keys"],
         ["make_mold"]),
        ("export",
         "Export — the mould files and the pour instructions",
         "Name every file the mould wrote, and say what silicone and resin they "
         "still have to buy.",
         ["files_written"],
         ["make_mold"]),
    ]


#: `task -> the function that builds its chain`. Data-driven exactly as
#: `task_config.TEMPLATES` is: a new task is one entry here and one there.
STAGE_TEMPLATES: Dict[str, Callable[[], List[Tuple[str, str, str, List[str],
                                                   List[str]]]]] = {
    "character": _character_stages,
    "part": _part_stages,
    "device": _device_stages,
    "floorplan": _floorplan_stages,
    "mold": _mold_stages,
}


def stage_template(task: str) -> List[Dict[str, Any]]:
    """The task's whole chain, every stage pending and nothing measured yet."""
    key = task_config.normalize_task(task)
    out: List[Dict[str, Any]] = []
    for ident, title, does, gate, tools in STAGE_TEMPLATES[key]():
        out.append({
            "id": ident,
            "title": title,
            "status": "pending",
            "gate": list(gate),
            "artifacts": [],
            "numbers": {},
            "history": [],
            "does": does,
            "tools": list(tools),
        })
    return out


def chain_ids(task: str) -> Tuple[str, ...]:
    """Just the stage ids, in order. What "the next stage" is measured against."""
    return tuple(str(stage["id"]) for stage in stage_template(task))


# ---------------------------------------------------------------------------
# The plan file
# ---------------------------------------------------------------------------


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def plan_path(slug: str) -> Path:
    """``projects/<slug>/design/build-plan.json``."""
    _design, path = design_paths(slug, PLAN_FILENAME)
    return path


def exists(project: Any) -> bool:
    """Is there a build plan for this project? Never raises for a bad name."""
    try:
        return plan_path(project_slug(project)).is_file()
    except ForgeError:
        return False


def new_plan(slug: str, task: str) -> Dict[str, Any]:
    """A whole plan, materialised: every stage this task has, all pending."""
    key = task_config.normalize_task(task)
    stages = stage_template(key)
    return {
        "version": PLAN_VERSION,
        "project": slug,
        "task": key,
        "stages": stages,
        "history": [{"date": _stamp(), "note":
                     f"materialised the {key} chain — {len(stages)} stages, "
                     "all pending"}],
    }


def read(slug: str) -> Dict[str, Any]:
    """The plan as it stands on disk, checked, or a refusal that says why."""
    path = plan_path(slug)
    if not path.is_file():
        raise ForgeError(
            f"{slug} has no build plan yet ({path} does not exist). "
            "pipeline_status(project) materialises the stage board for the "
            "project's task and shows it; pipeline_advance(project, stage) "
            "starts the first stage and writes the plan."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ForgeError(f"Could not read {path}: {exc}") from exc
    try:
        plan = json.loads(text)
    except ValueError as exc:
        raise ForgeError(
            f"{path} is not valid JSON ({exc}). Nothing was changed. Fix it by "
            "hand — this file records what has already been built, so it is "
            "never rebuilt from a template behind your back."
        ) from None
    return validate(plan, path)


def validate(plan: Any, path: Optional[Path] = None) -> Dict[str, Any]:
    """The plan, checked to be a plan. Returns it; never rewrites it.

    Deliberately permissive about everything it does not own. ``notes``,
    ``components`` and anything else a real plan grew over a dozen sessions are
    passed through untouched — this module added the stages block, it does not
    get to decide what else may live beside it.
    """
    where = f"{path}: " if path is not None else ""
    if not isinstance(plan, dict):
        raise ForgeError(f"{where}a build plan is a JSON object, not a "
                         f"{type(plan).__name__}.")

    components = plan.get("components")
    if components is not None and not isinstance(components, list):
        raise ForgeError(
            f"{where}\"components\" is a list of what the build is made of, not "
            f"a {type(components).__name__}. Nothing was changed."
        )

    task = plan.get("task")
    if task is not None and task not in task_config.TASKS:
        raise ForgeError(
            f"{where}\"task\" is {task!r}, which is not one of "
            + ", ".join(task_config.TASKS)
            + ". It decides which stages this build has, so it cannot be a "
            "word nobody has a chain for."
        )

    stages = plan.get("stages")
    if stages is not None:
        if not isinstance(stages, list) or not stages:
            raise ForgeError(
                f"{where}\"stages\" is missing its stages. A plan either has no "
                "stages block at all (and gets one materialised from its task's "
                "template) or has a real chain in it — an empty one is neither."
            )
        seen: Dict[str, int] = {}
        for index, stage in enumerate(stages):
            _validate_stage(stage, index, where, seen)

    if not isinstance(plan.get("history"), list):
        plan["history"] = []
    return plan


def _validate_stage(stage: Any, index: int, where: str,
                    seen: Dict[str, int]) -> None:
    if not isinstance(stage, dict):
        raise ForgeError(
            f"{where}stage {index + 1} is a {type(stage).__name__}, not a stage "
            'object — every one is {"id", "title", "status", "gate", '
            '"artifacts", "numbers", "history"}.'
        )
    ident = stage.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise ForgeError(
            f"{where}stage {index + 1} has no id. The id is how a stage is "
            "named to advance it, so a stage without one cannot be driven."
        )
    if ident in seen:
        raise ForgeError(
            f"{where}two stages are both called {ident!r} (positions "
            f"{seen[ident] + 1} and {index + 1}). A duplicate id makes "
            '"advance to it" mean two different things.'
        )
    seen[ident] = index
    status = stage.get("status")
    if status not in STATUSES:
        raise ForgeError(
            f"{where}stage {ident!r} has status {status!r}, which is not one of "
            + ", ".join(STATUSES) + "."
        )
    for field, kind in (("gate", list), ("artifacts", list),
                        ("history", list), ("numbers", dict)):
        value = stage.get(field)
        if value is None:
            stage[field] = [] if kind is list else {}
        elif not isinstance(value, kind):
            raise ForgeError(
                f"{where}stage {ident!r}'s {field!r} is a "
                f"{type(value).__name__}; it is a "
                + ("list" if kind is list else "mapping of name to number")
                + "."
            )


def write(slug: str, plan: Mapping[str, Any]) -> Path:
    """Write the plan through ``save_design_doc``'s own rules.

    Not its own writer, for the same reason ``task_config`` is not: the same
    slug check, the same resolved-path check and the same "valid JSON before
    anything touches disk" check guard this file as guard every other design
    document.
    """
    design, path = design_paths(slug, PLAN_FILENAME)
    text = normalize_design_content(
        json.dumps(validate(dict(plan)), indent=2, ensure_ascii=False),
        PLAN_FILENAME,
    )
    try:
        design.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ForgeError(f"Could not write {path}: {exc}") from exc
    return path


# ---------------------------------------------------------------------------
# Settling the plan at read time
# ---------------------------------------------------------------------------


def resolve_task(slug: str, plan: Optional[Mapping[str, Any]] = None) -> str:
    """Which chain this project builds on. Plan first, then the settings sheet.

    The same precedence ``floorplan_validate`` uses, and for the same reason: a
    task written into *this* plan is a decision about this plan and always wins.
    A project with neither is a refusal naming ``task_config_init``, never a
    guess — inventing a chain would invent the gates that block the build.
    """
    if plan is not None:
        task = plan.get("task")
        if isinstance(task, str) and task.strip():
            return task_config.normalize_task(task)
    sheet_task = None
    try:
        sheet_task = task_config.read(slug).get("task")
    except ForgeError:
        sheet_task = None
    if isinstance(sheet_task, str) and sheet_task.strip():
        return task_config.normalize_task(sheet_task)
    raise ForgeError(
        f"{slug} has no task, so there is no chain of stages to drive. The task "
        "decides which stages a build has — a character has ten, a floor plan "
        "has five — and guessing it would invent the gates that block the "
        "build. task_config_init(\"" + slug + "\", task) settles it (character, "
        + ", ".join(t for t in task_config.TASKS if t != "character")
        + "), and materialises the settings sheet at the same time."
    )


def load(project: Any) -> Tuple[str, Dict[str, Any], Path, bool]:
    """**The settle-at-read entry point.** ``(slug, plan, path, fresh)``.

    Goes to disk every call. A project with no plan yet gets one built in
    memory from its task's template — so the board can be *shown* before
    anything is written — and *fresh* says so. A plan that predates the stages
    block (the werewolf's does) gets the chain materialised in memory beside
    everything it already carries, losing nothing.
    """
    slug = project_slug(project)
    path = plan_path(slug)
    fresh = not path.is_file()
    if fresh:
        task = resolve_task(slug)
        return slug, new_plan(slug, task), path, True

    plan = read(slug)
    task = resolve_task(slug, plan)
    plan["task"] = task
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        plan["stages"] = stage_template(task)
        plan.setdefault("history", []).append({
            "date": _stamp(),
            "note": f"materialised the {task} chain over an existing plan — "
                    f"{len(plan['stages'])} stages, all pending; "
                    f"{len(plan.get('components') or [])} components already on "
                    "the plan were left exactly as they were",
        })
        fresh = True
    return slug, plan, path, fresh


def find_stage(plan: Mapping[str, Any], stage: Any) -> Tuple[int, Dict[str, Any]]:
    """``(index, entry)`` for a stage id, or a refusal listing the chain."""
    wanted = "" if stage is None else str(stage).strip().strip('"').strip()
    stages: List[Dict[str, Any]] = list(plan.get("stages") or [])
    if not wanted:
        raise ForgeError(
            "No stage named. The stages on this plan are "
            + ", ".join(str(item.get("id")) for item in stages) + "."
        )
    for index, entry in enumerate(stages):
        if str(entry.get("id")) == wanted:
            return index, entry
    lowered = {str(entry.get("id")).lower(): index
               for index, entry in enumerate(stages)}
    if wanted.lower() in lowered:
        index = lowered[wanted.lower()]
        return index, stages[index]
    raise ForgeError(
        f"{stage!r} is not a stage on this {plan.get('task')} plan. The stages, "
        "in order, are " + ", ".join(str(item.get("id")) for item in stages)
        + ". Nothing was changed."
    )


def next_stage(plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The first stage that is not green — what "next" means. ``None`` if done."""
    for entry in plan.get("stages") or []:
        if entry.get("status") not in GREEN:
            return entry
    return None


def blocked(project: Any) -> Optional[str]:
    """**A consumer API.** The id of the first red stage, or ``None``.

    For anything that has to refuse to present a build over a failed gate. Reads
    off disk like everything else here, and a project with no plan is not an
    error: it answers ``None``.
    """
    try:
        _slug, plan, _path, _fresh = load(project)
    except ForgeError:
        return None
    for entry in plan.get("stages") or []:
        if entry.get("status") == "failed":
            return str(entry.get("id"))
    return None


def stage_status(project: Any, stage: Any, fallback: Any = None) -> Any:
    """**A consumer API.** One stage's status, read off disk right now."""
    try:
        _slug, plan, _path, _fresh = load(project)
        _index, entry = find_stage(plan, stage)
    except ForgeError:
        return fallback
    return entry.get("status", fallback)


# ---------------------------------------------------------------------------
# Advancing — the ordering rules
# ---------------------------------------------------------------------------


def advance(
    plan: Dict[str, Any],
    stage: Any,
    *,
    override: bool = False,
    who: str = "",
    why: str = "",
) -> Dict[str, Any]:
    """Start a stage. Refuses a skip; an override is signed and never silent.

    Returns a small report of what happened: the entry, what it was, whether
    anything was stepped over and whether this re-opened work already done.
    """
    index, entry = find_stage(plan, stage)
    ident = str(entry.get("id"))
    stages: List[Dict[str, Any]] = list(plan.get("stages") or [])
    earlier = stages[:index]

    red = [item for item in earlier if item.get("status") == "failed"]
    unfinished = [item for item in earlier
                  if item.get("status") in ("pending", "in_progress")]

    if (red or unfinished) and not override:
        raise _skip_refusal(plan, ident, red, unfinished)

    signature = None
    if red or unfinished:
        signature = _signature(who, why, ident, red, unfinished)

    stepped_over: List[str] = []
    if signature is not None:
        for item in red:
            item["status"] = "overridden"
            item.setdefault("history", []).append({
                "date": signature["date"],
                "action": "overridden",
                "who": signature["who"],
                "why": signature["why"],
                "by": ident,
            })
            stepped_over.append(str(item.get("id")))
        for item in unfinished:
            item.setdefault("history", []).append({
                "date": signature["date"],
                "action": "skipped",
                "who": signature["who"],
                "why": signature["why"],
                "by": ident,
            })

    before = entry.get("status")
    reopened = before in GREEN
    entry["status"] = "in_progress"
    record_entry: Dict[str, Any] = {
        "date": _stamp(), "action": "advance",
        "from": before, "to": "in_progress",
    }
    if signature is not None:
        record_entry["override"] = {
            "who": signature["who"], "why": signature["why"],
            "stepped_over": stepped_over,
            "skipped": [str(item.get("id")) for item in unfinished],
        }
    entry.setdefault("history", []).append(record_entry)

    return {
        "stage": entry,
        "before": before,
        "reopened": reopened,
        "overridden": stepped_over,
        "skipped": [str(item.get("id")) for item in unfinished],
        "signature": signature,
    }


def _skip_refusal(plan: Mapping[str, Any], ident: str,
                  red: Sequence[Mapping[str, Any]],
                  unfinished: Sequence[Mapping[str, Any]]) -> ForgeError:
    """The refusal that makes this a pipeline rather than a checklist."""
    lines: List[str] = []
    if red:
        names = ", ".join(str(item.get("id")) for item in red)
        worst = red[0]
        measured = _numbers_line(worst.get("numbers") or {})
        lines.append(
            f"{ident} is blocked: {names} failed its gate"
            + (f" ({measured})" if measured else "")
            + ". A red gate blocks the next stage — that is what the stages are "
            "for. Fix it and pipeline_record it passed."
        )
    if unfinished:
        names = ", ".join(
            f"{item.get('id')} ({item.get('status')})" for item in unfinished)
        lines.append(
            f"{ident} would skip over {names}. A build is staged on purpose: "
            "each stage ends with its gate verdict and its artifacts, and the "
            "next one reads what the last one wrote."
        )
    lines.append(
        "If the artist really wants to go on anyway, pass override=true with "
        "who and why — the stepped-over stage is then marked `overridden`, not "
        "passed, and both stages carry who signed it and what they said."
    )
    return ForgeError(" ".join(lines))


def _signature(who: Any, why: Any, ident: str,
               red: Sequence[Mapping[str, Any]],
               unfinished: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    """An override with no name and no reason is an unsigned waiver."""
    name = "" if who is None else str(who).strip().strip('"').strip()
    reason = "" if why is None else str(why).strip().strip('"').strip()
    over = ", ".join(str(item.get("id")) for item in list(red) + list(unfinished))
    if not name or not reason:
        missing = []
        if not name:
            missing.append("who")
        if not reason:
            missing.append("why")
        raise ForgeError(
            "An override needs " + " and ".join(missing)
            + f". Stepping {ident} past {over} is a decision somebody made, and "
            "a decision with no name and no reason on it is one nobody can "
            "argue with three sessions later — which is exactly when it "
            "matters. Nothing was changed."
        )
    return {"date": _stamp(), "who": name, "why": reason}


# ---------------------------------------------------------------------------
# Recording — how a build turn writes its result in
# ---------------------------------------------------------------------------


def coerce_status(status: Any) -> str:
    """One of :data:`RECORDABLE`, or a refusal that says why not."""
    raw = "" if status is None else str(status).strip().strip('"').strip().lower()
    if not raw:
        raise ForgeError(
            "No status given. A stage ends with a verdict: "
            + ", ".join(RECORDABLE) + "."
        )
    if raw == "overridden":
        raise ForgeError(
            "`overridden` is not a verdict anybody measures, so it is not one "
            "you record. It is what pipeline_advance writes onto a stage "
            "somebody signed their way past, with their name and their reason "
            "beside it. Record what you actually measured: "
            + ", ".join(RECORDABLE) + "."
        )
    if raw not in RECORDABLE:
        raise ForgeError(
            f"{status!r} is not a stage verdict. They are "
            + ", ".join(RECORDABLE)
            + " — `passed` and `failed` are the gate's answer, `in_progress` is "
            "work still going, `pending` puts a stage back."
        )
    return raw


def coerce_numbers(numbers: Any) -> Dict[str, Any]:
    """The gate values, checked to be values. ``{}`` for nothing recorded."""
    if numbers is None:
        return {}
    if not isinstance(numbers, Mapping):
        raise ForgeError(
            "`numbers` is the gate's measurements as {name: value} — "
            f"{{\"worst_drift_mm\": 1.1, \"gate\": \"ok\"}} — not a "
            f"{type(numbers).__name__}. Nothing was changed."
        )
    out: Dict[str, Any] = {}
    for name, value in numbers.items():
        key = str(name).strip()
        if not key:
            raise ForgeError(
                "A measurement with no name is a number nobody can read back. "
                "Nothing was changed."
            )
        out[key] = _checked_value(key, value)
    return out


def _checked_value(name: str, value: Any, *, nested: bool = False) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        if nested:
            raise _too_deep(name)
        return [_checked_value(name, item, nested=True) for item in value]
    if isinstance(value, Mapping):
        if nested:
            raise _too_deep(name)
        return {str(key): _checked_value(name, item, nested=True)
                for key, item in value.items()}
    raise ForgeError(
        f"{name} is a {type(value).__name__}, which is not a measurement. A "
        "gate value is a number, a word, a yes/no, or a flat list or table of "
        "those. Nothing was changed."
    )


def _too_deep(name: str) -> ForgeError:
    return ForgeError(
        f"{name} nests deeper than a table of numbers. Keep a gate value flat — "
        "a stage board that has to be unfolded to be read is a board nobody "
        "reads. Put the deep detail in a design document and record its path as "
        "an artifact. Nothing was changed."
    )


def coerce_artifacts(artifacts: Any) -> List[str]:
    """The paths this stage produced. One string or a list of them."""
    if artifacts is None:
        return []
    items = artifacts if isinstance(artifacts, (list, tuple)) else [artifacts]
    out: List[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ForgeError(
                "`artifacts` are paths to what the stage produced — a .blend, a "
                "render, a .glb, an exported STL — as strings. "
                f"{item!r} is not one. Nothing was changed."
            )
        text = item.strip()
        if text not in out:
            out.append(text)
    return out


def record(
    plan: Dict[str, Any],
    stage: Any,
    status: Any,
    numbers: Any = None,
    artifacts: Any = None,
    *,
    replace: bool = False,
) -> Dict[str, Any]:
    """Write a stage's result in. Returns a report of what changed.

    *numbers* and *artifacts* merge into what the stage already carries unless
    *replace*, because a stage is usually measured by more than one tool and the
    second call should not erase the first one's number.
    """
    _index, entry = find_stage(plan, stage)
    ident = str(entry.get("id"))
    verdict = coerce_status(status)
    measured = coerce_numbers(numbers)
    produced = coerce_artifacts(artifacts)

    if verdict == "passed" and not measured and not (entry.get("numbers") or {}):
        raise ForgeError(
            f"{ident} cannot pass with nothing measured. A green verdict with no "
            "number beside it is an opinion, and the whole point of a gate is "
            "that the next stage can read what the last one actually got. Its "
            "gate is " + (", ".join(str(item) for item in entry.get("gate") or [])
                          or "not written down on this plan")
            + " — record at least one of those, or {\"sign_off\": true} if this "
            "stage really is the artist's yes. Nothing was changed."
        )

    before = entry.get("status")
    if replace:
        entry["numbers"] = measured
        entry["artifacts"] = produced
    else:
        merged = dict(entry.get("numbers") or {})
        merged.update(measured)
        entry["numbers"] = merged
        existing = list(entry.get("artifacts") or [])
        for path in produced:
            if path not in existing:
                existing.append(path)
        entry["artifacts"] = existing
    entry["status"] = verdict

    history: Dict[str, Any] = {
        "date": _stamp(), "action": "record",
        "from": before, "to": verdict,
    }
    if measured:
        history["numbers"] = measured
    if produced:
        history["artifacts"] = produced
    if replace:
        history["replace"] = True
    entry.setdefault("history", []).append(history)

    uncovered = [name for name in (entry.get("gate") or [])
                 if str(name) not in (entry.get("numbers") or {})]
    return {
        "stage": entry,
        "before": before,
        "status": verdict,
        "numbers": measured,
        "artifacts": produced,
        "uncovered": uncovered,
        "missing_files": missing_files(entry.get("artifacts") or []),
    }


def missing_files(artifacts: Sequence[str]) -> List[str]:
    """Which recorded paths are not on disk. A path is a claim; this checks it."""
    out: List[str] = []
    for item in artifacts:
        try:
            if not Path(item).exists():
                out.append(item)
        except OSError:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


def _short(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, list):
        return "[" + ", ".join(_short(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key} {_short(item)}"
                               for key, item in value.items()) + "}"
    if value is None:
        return "-"
    return str(value)


def _numbers_line(numbers: Mapping[str, Any]) -> str:
    return ", ".join(f"{name} {_short(value)}"
                     for name, value in numbers.items())


def tally(plan: Mapping[str, Any]) -> Dict[str, int]:
    """How many stages sit at each status."""
    counts = {status: 0 for status in STATUSES}
    for entry in plan.get("stages") or []:
        status = str(entry.get("status"))
        if status in counts:
            counts[status] += 1
    return counts


def fmt_board(
    *,
    plan: Mapping[str, Any],
    path: Path,
    slug: str,
    head: str,
    fresh: bool = False,
    documents: Optional[List[Dict[str, Any]]] = None,
    note: str = "",
) -> str:
    """**The stage board.** Every stage, its verdict, its numbers, its files.

    Written to be quoted at the artist. The whole chain is printed even though
    most of it has not happened yet, because the thing the artist is being asked
    to drive is the chain — a board that showed only finished work would answer
    "what did you do" and never "what happens next", which is the question a
    staged build exists to answer.
    """
    stages: List[Mapping[str, Any]] = list(plan.get("stages") or [])
    task = str(plan.get("task") or "")
    counts = tally(plan)
    width = max((len(str(entry.get("id"))) for entry in stages), default=0)

    lines = [head, f"  {path}"]
    if fresh:
        lines.append(
            "  NOT ON DISK YET — this is the chain for this task, materialised "
            "to be looked at. pipeline_advance(project, stage) starts the first "
            "stage and writes the plan."
        )
    lines.append(
        f"  task: {task} — {task_config.TASK_BLURB.get(task, '')}".rstrip(" —"))

    # A ten-stage chain has a two-digit number in it, so the id column is set
    # from the widest number as well as the widest id — a board whose rows do
    # not line up is a board that is read one row at a time.
    numbers_width = len(str(len(stages)))
    gutter = 3 + 1 + numbers_width + 2  # "[x]" + " " + "10" + ". "
    for number, entry in enumerate(stages, start=1):
        status = str(entry.get("status"))
        marker = MARKERS.get(status, "[?]")
        ident = str(entry.get("id"))
        label = f"{marker} {number}. {ident}"
        lines.append(
            f"  {label.ljust(gutter + width)}  {status}"
            f"   {entry.get('title', '')}"
        )
        pad = " " * gutter
        measured = entry.get("numbers") or {}
        if measured:
            lines.append(f"  {pad}numbers: {_numbers_line(measured)}")
        elif status in ("passed", "failed", "overridden"):
            lines.append(f"  {pad}numbers: none recorded")
        artifacts = list(entry.get("artifacts") or [])
        if artifacts:
            gone = set(missing_files(artifacts))
            for item in artifacts:
                suffix = "   (NOT ON DISK)" if item in gone else ""
                lines.append(f"  {pad}artifact: {item}{suffix}")
        if status in ("pending", "in_progress"):
            gate = ", ".join(str(item) for item in entry.get("gate") or [])
            if gate:
                lines.append(f"  {pad}gate: {gate}")
            tools = ", ".join(str(item) for item in entry.get("tools") or [])
            if tools:
                lines.append(f"  {pad}tools: {tools}")
        if status == "overridden":
            signed = _last_override(entry)
            if signed:
                lines.append(
                    f"  {pad}OVERRIDDEN by {signed.get('who')} to start "
                    f"{signed.get('by')}: {signed.get('why')}"
                )

    lines.append(
        f"  {len(stages)} stages — "
        + ", ".join(f"{counts[status]} {status}" for status in STATUSES
                    if counts[status])
    )
    lines.append("  next: " + _next_line(plan))

    components = plan.get("components")
    if isinstance(components, list) and components:
        built = sum(1 for item in components
                    if isinstance(item, dict) and item.get("status") == "built")
        lines.append(
            f"  this plan also carries {len(components)} components "
            f"({built} built) — what the build is MADE of, beside the stages, "
            "which are how it gets made."
        )
    if note:
        lines.append(f"  {note}")
    if documents:
        lines.append(f"  design sheet for {slug}: "
                     + ", ".join(item["file"] for item in documents))
    lines.append(
        "  A STAGE IS NOT A ONE-SHOT. Do the work for ONE stage, record its "
        "verdict and its artifacts, show the artist, and let them say go on. A "
        "red gate blocks the next stage."
    )
    return "\n".join(lines)


def _last_override(entry: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    for item in reversed(list(entry.get("history") or [])):
        if isinstance(item, dict) and item.get("action") == "overridden":
            return item
    return None


def _next_line(plan: Mapping[str, Any]) -> str:
    """What "next" is, in one sentence that names the call to make."""
    slug = plan.get("project") or "<project>"
    for entry in plan.get("stages") or []:
        status = entry.get("status")
        ident = entry.get("id")
        if status == "failed":
            measured = _numbers_line(entry.get("numbers") or {})
            return (
                f"fix {ident} — its gate is RED"
                + (f" ({measured})" if measured else "")
                + ". Every stage after it is blocked until "
                f"pipeline_record(\"{slug}\", \"{ident}\", \"passed\", ...) says "
                "otherwise, or somebody signs an override."
            )
        if status == "in_progress":
            gate = ", ".join(str(item) for item in entry.get("gate") or [])
            return (
                f"finish {ident} — it is in progress. Measure "
                + (gate or "its gate")
                + f" and pipeline_record(\"{slug}\", \"{ident}\", "
                "\"passed\"|\"failed\", numbers, artifacts)."
            )
        if status == "pending":
            return (
                f"start {ident} — pipeline_advance(\"{slug}\", \"{ident}\"), do "
                "that stage's work, then record what its gate measured."
            )
    return ("nothing — every stage is green. The build is done as far as this "
            "plan knows; a new pass starts by advancing back to the stage that "
            "has to change.")


def fmt_advance(*, plan: Mapping[str, Any], path: Path, slug: str,
                report: Mapping[str, Any]) -> str:
    entry = report["stage"]
    ident = entry.get("id")
    if report.get("reopened"):
        head = (f"Re-opened {ident} on {slug}'s build plan — it was "
                f"{report.get('before')}, it is in progress again")
    else:
        head = f"Started {ident} on {slug}'s build plan"
    note = ""
    signature = report.get("signature")
    if signature:
        parts = []
        if report.get("overridden"):
            parts.append("marked " + ", ".join(report["overridden"])
                         + " OVERRIDDEN (a failed gate, stepped over — not "
                           "passed)")
        if report.get("skipped"):
            parts.append("skipped " + ", ".join(report["skipped"]))
        note = (f"OVERRIDE signed by {signature['who']}: {signature['why']} — "
                + "; ".join(parts)
                + ". It is on both stages' history for good.")
    body = fmt_board(plan=plan, path=path, slug=slug, head=head, note=note)
    gate = ", ".join(str(item) for item in entry.get("gate") or [])
    return body + (
        f"\n  {ident} is what you are doing NOW, and only {ident}. End it by "
        "recording " + (gate or "what its gate measured")
        + " and the files it produced."
    )


def fmt_record(*, plan: Mapping[str, Any], path: Path, slug: str,
               report: Mapping[str, Any]) -> str:
    entry = report["stage"]
    ident = str(entry.get("id"))
    verdict = str(report["status"])
    measured = _numbers_line(report.get("numbers") or {})
    head = (f"{ident}: {report.get('before')} -> {verdict} on {slug}'s build plan"
            + (f" — {measured}" if measured else ""))
    notes: List[str] = []
    if report.get("uncovered"):
        notes.append(
            "gate not fully covered: nothing recorded for "
            + ", ".join(str(item) for item in report["uncovered"])
            + " — a verdict is only as good as what was actually measured."
        )
    if report.get("missing_files"):
        notes.append(
            "recorded but NOT on disk: "
            + ", ".join(report["missing_files"])
            + " — a path is a claim, and this one does not check out."
        )
    body = fmt_board(plan=plan, path=path, slug=slug, head=head,
                     note=" ".join(notes))
    if verdict == "failed":
        body += (
            f"\n  {ident} is RED, so the stages after it are blocked. Work the "
            "fix, record it passed, and only then advance — or tell the artist "
            "it cannot be fixed and let THEM decide whether to sign an override."
        )
    return body


def mention(project: Any) -> str:
    """One line for another tool's report: is there a build plan, and where is it?

    The design-phase reports print this so the stage board is never a thing the
    artist has to already know about — the same reason ``task_config.mention``
    exists. Never raises: a report is not the place to discover that a project
    name was odd.
    """
    try:
        slug = project_slug(project)
    except ForgeError:
        return ""
    path = plan_path(slug)
    if not path.is_file():
        return ("  no build plan yet: pipeline_status(\"" + str(slug) + "\") "
                "shows the stage board for this kind of work — a build is "
                "staged (make the mesh, check it, then rig it), never one shot.")
    try:
        plan = read(slug)
    except ForgeError:
        return (f"  build plan: {path} — it does not currently read as a plan; "
                f"pipeline_status(\"{slug}\") says why.")
    stages = list(plan.get("stages") or [])
    if not stages:
        return (f"  build plan: {path} — no stages on it yet; "
                f"pipeline_status(\"{slug}\") materialises the chain for this "
                "task and shows the board.")
    counts = tally(plan)
    done = counts["passed"] + counts["overridden"]
    upcoming = next_stage(plan)
    where = (f"next {upcoming.get('id')}" if upcoming
             else "every stage green")
    red = [str(item.get("id")) for item in stages
           if item.get("status") == "failed"]
    line = (f"  build plan: {path} — {done}/{len(stages)} stages green, {where}"
            + (f"; RED: {', '.join(red)}" if red else "")
            + f". pipeline_status(\"{slug}\") shows the board.")
    return line


def fmt_status(*, plan: Mapping[str, Any], path: Path, slug: str,
               fresh: bool) -> str:
    head = (f"Build plan for {slug} — the stages, and which one is next"
            if not fresh
            else f"Build plan for {slug} — the chain this kind of build has")
    return fmt_board(plan=plan, path=path, slug=slug, head=head, fresh=fresh,
                     documents=design_documents(slug))


__all__ = [
    "GREEN",
    "MARKERS",
    "PLAN_FILENAME",
    "PLAN_VERSION",
    "RECORDABLE",
    "STAGE_KEYS",
    "STAGE_TEMPLATES",
    "STATUSES",
    "advance",
    "blocked",
    "chain_ids",
    "coerce_artifacts",
    "coerce_numbers",
    "coerce_status",
    "exists",
    "find_stage",
    "fmt_advance",
    "fmt_board",
    "fmt_record",
    "fmt_status",
    "load",
    "mention",
    "missing_files",
    "new_plan",
    "next_stage",
    "plan_path",
    "read",
    "record",
    "resolve_task",
    "stage_status",
    "stage_template",
    "tally",
    "validate",
    "write",
]
