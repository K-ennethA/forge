"""The task config map — `projects/<slug>/design/task-config.json`.

THE PROBLEM IT SOLVES, IN THE OWNER'S WORDS
===========================================
*"Our bipeds should be symmetric at least for what Forge does, unless the user
specifies it shouldn't be. Honestly we should have some config map for the user
to select values depending on the task, rather than assuming the user will tell
the LLM all needed fields and values."*

Two failures, one file. The first is a **wrong default nobody stated**: a biped
came out asymmetric because symmetry was never a setting, only a thing somebody
might have thought to ask for. The second is **the artist being asked to know
the control surface**: a person who has never seen a poly budget cannot mention
one, and a model that never hears one invents it.

So the design phase **materialises the whole sheet with every knob pre-filled at
its default**, and the artist edits values rather than having to guess which
fields exist. This is the vendor echo-back pattern (Sloyd's: the control surface
teaches itself by arriving already filled in) applied to a settings file instead
of a template.

THE TWO LAWS
============
* **Values settle at read time.** Any tool that consumes a setting calls
  :func:`setting` at the moment it runs and reads the file. Nothing carries a
  value in conversation memory — a number remembered from three turns ago is a
  number the artist has since changed.
* **Templates are data, not prose.** A task's knobs are one table in this module
  and the floor-plan block is generated **from** ``service.floorplan.DEFAULTS``,
  so a ceiling height cannot be 2400 here and 2700 in the builder. The maker
  choices come off the real component catalog for the same reason.

WHAT THIS MODULE IS NOT
=======================
It is not a second writer. The sheet is written through ``save_design_doc``'s
own rules — :func:`~forge_mcp.util.design_paths`,
:func:`~forge_mcp.util.normalize_design_content` — so there is exactly one set of
slug/traversal/validation checks guarding ``projects/<slug>/design/``, and this
file adds no new door onto it.

``task-config.json`` is deliberately **not** in
:data:`~forge_mcp.util.DESIGN_READING_ORDER`. That tuple is the order a person
*reads a sheet* in, and it is mirrored byte for byte in ``assistant/bridge.py``;
the settings file is the machine's copy of what was decided, so it files
alphabetically exactly as ``floorplan.json`` does.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import floorplan as floorplan_module
from . import maker
from .errors import ForgeError
from .util import (
    design_documents,
    design_paths,
    normalize_design_content,
    project_slug,
)

#: The one filename. It is a design document like any other, so it passes
#: `design_filename`'s alphabet unchanged.
CONFIG_FILENAME = "task-config.json"

#: Bumped only when the shape below changes in a way a reader must notice.
CONFIG_VERSION = 1

#: Every task that has a template. The design phase picks one of these.
TASKS: Tuple[str, ...] = ("character", "part", "device", "floorplan", "mold")

#: One line per task, printed at the head of the sheet's report so the artist
#: knows what they are looking at without opening the file.
TASK_BLURB: Dict[str, str] = {
    "character": "a rigged, animated body headed for a game engine",
    "part": "a parametric part headed for a printer",
    "device": "a part with a circuit in it — a switch, a cell, a light",
    "floorplan": "a drawing of rooms headed for a greybox level",
    "mold": "a figure headed for silicone and resin",
}

#: What a setting entry may carry. `value` and `default` are always there; the
#: rest are present when they mean something.
SETTING_KEYS: Tuple[str, ...] = (
    "value", "default", "unit", "choices", "min", "max", "why",
)


# ---------------------------------------------------------------------------
# The templates
# ---------------------------------------------------------------------------
#
# Each entry is `(name, default, {extras})`. A list, not a dict, because the
# ORDER is the order the artist reads the knobs in and the order the echo report
# prints them: the decision that changes the most comes first.


def _character_template() -> List[Tuple[str, Any, Dict[str, Any]]]:
    """The owner's directive, encoded: symmetry leads and it defaults to true."""
    return [
        ("symmetry", "mirror_left", {
            "choices": ["mirror_left", "mirror_right", "as_designed"],
            "why": "bipeds are symmetric unless you say otherwise: mirror_* "
                   "SPLITS the mesh at the midplane, keeps that side and "
                   "flips it -- exact 100% symmetry, never averaging (a "
                   "tolerance snap spares big one-sided features like a "
                   "generated leg crease); the artist adds asymmetric "
                   "touches afterwards. as_designed follows the reference "
                   "faithfully, asymmetries included, and rigs with "
                   "symmetry measured honestly instead of mirrored.",
        }),
        ("target_engine", "godot", {
            "choices": ["godot", "unity", "unreal", "gltf"],
            "why": "decides the export's axis convention and LOD metadata",
        }),
        ("rest_stance", "flexed", {
            "choices": ["flexed", "straight"],
            "why": "flexed rests the knees so hip->ankle spans 0.97 of the "
                   "leg's own chain (~28 deg) -- the headroom IK needs to "
                   "compress in a walk and extend in a jump; a rig that "
                   "rests above 0.98 of reach shipped a +32% leg stretch "
                   "and a jump with zero launch. straight keeps the "
                   "modeled pose for characters whose design demands it, "
                   "and the reach-headroom gate then says what that costs.",
        }),
        ("poly_budget_desktop", 15000, {
            "unit": "triangles",
            "min": 500,
            "max": 200000,
            "why": "LOD0's ceiling; the platform cap is 50000, this is the "
                   "budget a character of this kind should actually cost",
        }),
        ("lod_chain", "auto", {
            "choices": ["auto", "none"],
            "why": "auto cuts a quarter per level from the unwrapped LOD0",
        }),
        ("texture_res", 2048, {
            "unit": "px",
            "choices": [512, 1024, 2048, 4096],
            "why": "the baked atlas the whole LOD chain shares",
        }),
        ("rig", "biped_ik", {
            "choices": ["biped_ik", "biped_fk", "none"],
            "why": "legs IK, arms FK is the convention; FK legs measured 189 mm "
                   "of foot slide against 1.1 mm for IK",
        }),
        ("correctives", True, {
            "why": "a corrective shape key per bent joint; without one a knee "
                   "loses 30-50% of its volume at 90 degrees",
        }),
        ("face_detail_pass", False, {
            "why": "off by default — it costs a second retopo and most "
                   "characters are seen from further away than that",
        }),
    ]


def _part_template() -> List[Tuple[str, Any, Dict[str, Any]]]:
    from . import config  # noqa: PLC0415 — late, so tests can redirect it

    return [
        ("printer_profile", str(config.DEFAULT_PRINTER_PATH), {
            "why": "every tolerance and the bed size come from this file; the "
                   "built-in fallback is an Elegoo Centauri Carbon",
        }),
        ("wall_mm", 2.0, {
            "unit": "mm",
            "min": 0.4,
            "max": 20.0,
            "why": "five 0.4 mm perimeters. The profile's 0.8 mm minimum is a "
                   "floor, not a target",
        }),
        ("bed_fit", "design_full_size", {
            "choices": ["design_full_size", "must_fit_bed"],
            "why": "a part is designed at the size it SHOULD be — a bed_fit "
                   "fail with a feasible split is print planning, not a "
                   "design fault",
        }),
        ("export_formats", ["stl"], {
            "choices": ["stl", "step", "3mf"],
            "why": "stl for the slicer, step for anyone who has to edit it in "
                   "CAD, 3mf to carry the plate layout",
        }),
    ]


def _device_template() -> List[Tuple[str, Any, Dict[str, Any]]]:
    return [
        ("battery", "cr2032_cell", {
            "choices": _catalog_names("power", ("aaa_pair_box", "cr2032_cell",
                                                "cr2032_holder")),
            "why": "a coin cell is the smallest thing that lights an LED; "
                   "aaa_pair_box when it has to run for hours",
        }),
        ("voltage_v", 3.0, {
            "unit": "V",
            "min": 1.0,
            "max": 12.0,
            "why": "a CR2032 is 3 V and so is a pair of AAAs — it is what the "
                   "series resistor is computed from",
        }),
        ("switch", "tactile_6x6_latching", {
            "choices": _catalog_names(
                "switch", ("push_latching_12mm", "slide_switch_sk12",
                           "tactile_6x6_h43", "tactile_6x6_h73",
                           "tactile_6x6_h95", "tactile_6x6_latching")),
            "why": "self-locking: press on, press off, and nothing has to hold "
                   "it down",
        }),
        ("led", "led_5mm", {
            "choices": _catalog_names("light", ("led_3mm", "led_5mm",
                                                "led_10mm")),
            "why": "its forward voltage and current set the resistor",
        }),
    ]


def _floorplan_template() -> List[Tuple[str, Any, Dict[str, Any]]]:
    """Mirrored from ``service.floorplan.DEFAULTS``, never retyped.

    The resolved plan is what the add-on's ``build_floorplan`` eats, so a
    default spelled differently here is a level built at a ceiling height
    nobody typed. If ``service/`` cannot be imported the block still
    materialises, from the same numbers written down as a fallback, and the
    sheet says which it used.
    """
    why = {
        "ceiling_mm": "8 ft, the residential default everywhere",
        "wall_mm": "a 2x4 stud wall with board both sides, near enough",
        "door_w_mm": "a 32 in leaf: the narrowest an accessible route allows",
        "door_h_mm": "a standard 6 ft 8 in door",
        "window_w_mm": "an ordinary double-hung pair",
        "window_h_mm": "the same pair, as tall as it is wide",
        "sill_mm": "counter height, which is where windows usually start",
        "label_h_mm": "a washer is 850 mm tall, and so is most casework",
        "floor_mm": "the room slab, built BELOW z = 0",
        "label_anchor": "what a fixture's footprint [x, y] measures from",
    }
    out: List[Tuple[str, Any, Dict[str, Any]]] = []
    for name, default in _plan_defaults().items():
        extras: Dict[str, Any] = {}
        if name in why:
            extras["why"] = why[name]
        if isinstance(default, str):
            extras["choices"] = list(_plan_anchors())
        else:
            extras["unit"] = "mm"
            extras["min"] = 1.0
            extras["max"] = 100000.0
        out.append((name, default, extras))
    return out


def _mold_template() -> List[Tuple[str, Any, Dict[str, Any]]]:
    return [
        ("mode", "printed_negative", {
            "choices": ["printed_negative", "master_box"],
            "why": "printed_negative prints the cavity itself; master_box "
                   "prints a box round the figure for you to pour silicone in",
        }),
        ("shell_mm", 4.0, {
            "unit": "mm",
            "min": 1.0,
            "max": 50.0,
            "why": "wall per side of the mold; thinner flexes on demolding",
        }),
        ("draft_deg", 2.0, {
            "unit": "deg",
            "min": 0.0,
            "max": 20.0,
            "why": "the taper that lets a half lift straight off — run "
                   "undercut_check before trusting it",
        }),
        ("registration_keys", 4, {
            "min": 0,
            "max": 24,
            "why": "0 means the halves line up by eye, and a mold that shifts "
                   "casts a seam",
        }),
        ("silicone", "tin-cure, shore 15-30", {
            "why": "shore hardness decides how much undercut will still lift "
                   "out; tin cures cheap, platinum cures tougher and tolerates "
                   "resin better",
        }),
    ]


#: `task -> the function that builds its rows`. Data-driven on purpose: a new
#: task is one entry here and one line in `TASKS`.
TEMPLATES = {
    "character": _character_template,
    "part": _part_template,
    "device": _device_template,
    "floorplan": _floorplan_template,
    "mold": _mold_template,
}


# ---------------------------------------------------------------------------
# Where the choices come from
# ---------------------------------------------------------------------------


def _catalog_names(category: str, fallback: Sequence[str]) -> List[str]:
    """Real component names for a category, or the written-down ones.

    The catalog is the truth and this asks it first. It is also an import of
    ``service/``, which is lazy and guarded everywhere else in this server for
    the same reason: a checkout without ``service/`` must still materialise a
    sheet. The fallback is a copy, so it is kept honest by a test that compares
    the two.
    """
    try:
        records = maker.catalog(category)["records"]
    except Exception:  # noqa: BLE001 — a missing service/ is not a refusal here
        return list(fallback)
    names = sorted(str(record["name"]) for record in records)
    return names or list(fallback)


#: ``service.floorplan.DEFAULTS``, written down for a checkout without
#: ``service/``. A test asserts the two are identical when it IS importable.
PLAN_DEFAULTS_FALLBACK: Dict[str, Any] = {
    "ceiling_mm": 2400.0,
    "wall_mm": 100.0,
    "door_w_mm": 820.0,
    "door_h_mm": 2040.0,
    "window_w_mm": 1200.0,
    "window_h_mm": 1200.0,
    "sill_mm": 900.0,
    "label_h_mm": 850.0,
    "floor_mm": 50.0,
    "label_anchor": "center",
}

#: The one plan default that is a word.
PLAN_ANCHORS_FALLBACK: Tuple[str, ...] = ("center", "corner")


def _plan_defaults() -> Dict[str, Any]:
    try:
        return dict(floorplan_module.defaults())
    except Exception:  # noqa: BLE001
        return dict(PLAN_DEFAULTS_FALLBACK)


def _plan_anchors() -> Tuple[str, ...]:
    try:
        plan_module, _appliance = floorplan_module.modules()
        return tuple(str(item) for item in plan_module.ANCHORS)
    except Exception:  # noqa: BLE001
        return PLAN_ANCHORS_FALLBACK


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


def normalize_task(task: Any) -> str:
    """One of :data:`TASKS`, or a refusal that lists them."""
    raw = "" if task is None else str(task).strip().strip('"').strip().lower()
    if not raw:
        raise ForgeError(
            "No task given. A task config sheet is scoped to one kind of work: "
            + ", ".join(TASKS)
            + ". Pick the one the artist is actually doing — it decides which "
            "knobs exist."
        )
    if raw not in TASKS:
        raise ForgeError(
            f"{task!r} is not a task Forge has a settings template for. The "
            "tasks are " + ", ".join(f"{name} ({TASK_BLURB[name]})"
                                     for name in TASKS) + "."
        )
    return raw


def template(task: str) -> Dict[str, Dict[str, Any]]:
    """The task's settings, every one filled in at its default."""
    key = normalize_task(task)
    settings: Dict[str, Dict[str, Any]] = {}
    for name, default, extras in TEMPLATES[key]():
        entry: Dict[str, Any] = {"value": _copy(default),
                                 "default": _copy(default)}
        for field in SETTING_KEYS:
            if field in ("value", "default"):
                continue
            if field in extras:
                entry[field] = _copy(extras[field])
        settings[name] = entry
    return settings


def _copy(value: Any) -> Any:
    """A value nobody upstream can mutate through us."""
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    return value


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_sheet(slug: str, task: str) -> Dict[str, Any]:
    """A whole sheet, materialised: every knob this task has, at its default."""
    key = normalize_task(task)
    return {
        "version": CONFIG_VERSION,
        "task": key,
        "project": slug,
        "settings": template(key),
        "history": [{"date": _stamp(), "note":
                     f"materialised from the {key} template — "
                     f"{len(TEMPLATES[key]())} settings, all at their default"}],
    }


def config_path(slug: str) -> Path:
    """``projects/<slug>/design/task-config.json``."""
    _design, path = design_paths(slug, CONFIG_FILENAME)
    return path


def exists(project: Any) -> bool:
    """Is there a sheet for this project? Never raises for a missing folder."""
    try:
        return config_path(project_slug(project)).is_file()
    except ForgeError:
        return False


def read(slug: str) -> Dict[str, Any]:
    """The sheet as it stands on disk, checked, or a refusal that says why."""
    path = config_path(slug)
    if not path.is_file():
        raise ForgeError(
            f"{slug} has no settings sheet yet ({path} does not exist). "
            "task_config_init(project, task) materialises one with every "
            "setting for that kind of work already filled in at its default, "
            "which is the point: the artist edits values instead of having to "
            "know which fields exist."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ForgeError(f"Could not read {path}: {exc}") from exc
    try:
        sheet = json.loads(text)
    except ValueError as exc:
        raise ForgeError(
            f"{path} is not valid JSON ({exc}). Nothing was changed. Fix it by "
            "hand, or task_config_init(project, task, force=true) to start the "
            "sheet again from the template."
        ) from None
    return validate(sheet, path)


def validate(sheet: Any, path: Optional[Path] = None) -> Dict[str, Any]:
    """The sheet, checked to be a sheet. Returns it; never rewrites it."""
    where = f"{path}: " if path is not None else ""
    if not isinstance(sheet, dict):
        raise ForgeError(f"{where}a task config sheet is a JSON object, not a "
                         f"{type(sheet).__name__}.")
    task = sheet.get("task")
    if task not in TASKS:
        raise ForgeError(
            f"{where}\"task\" is {task!r}, which is not one of "
            + ", ".join(TASKS) + "."
        )
    settings = sheet.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise ForgeError(
            f"{where}\"settings\" is missing or empty. A sheet with no settings "
            "in it teaches the artist nothing — task_config_init(project, task, "
            "force=true) rebuilds it from the template."
        )
    for name, entry in settings.items():
        if not isinstance(entry, dict) or "value" not in entry \
                or "default" not in entry:
            raise ForgeError(
                f"{where}setting {name!r} is not a settings entry — every one "
                'is {"value": ..., "default": ...} with optional "unit", '
                '"choices", "min", "max" and "why".'
            )
    if not isinstance(sheet.get("history"), list):
        sheet["history"] = []
    return sheet


def write(slug: str, sheet: Mapping[str, Any]) -> Path:
    """Write the sheet through ``save_design_doc``'s own rules.

    Deliberately not its own writer: the same slug check, the same resolved-path
    check and the same "valid JSON before anything touches disk" check guard
    this file as guard every other design document.
    """
    design, path = design_paths(slug, CONFIG_FILENAME)
    text = normalize_design_content(
        json.dumps(validate(dict(sheet)), indent=2, ensure_ascii=False),
        CONFIG_FILENAME,
    )
    try:
        design.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ForgeError(f"Could not write {path}: {exc}") from exc
    return path


# ---------------------------------------------------------------------------
# Settling a value at read time
# ---------------------------------------------------------------------------


def setting(project: Any, name: str, fallback: Any = None) -> Any:
    """**The consumer API.** One setting's value, read off disk right now.

    This is the law the whole file exists for: a tool that acts on a setting
    calls this at the moment it runs. It never takes the value from a tool
    argument the model filled in from memory, and it never caches — a number
    remembered from three turns ago is a number the artist has since changed.

    A project with no sheet is not an error here (most are not design-phase
    projects): *fallback* comes back instead.
    """
    try:
        sheet = read(project_slug(project))
    except ForgeError:
        return fallback
    entry = sheet["settings"].get(name)
    if not isinstance(entry, dict):
        return fallback
    return entry.get("value", fallback)


def plan_defaults(project: Any) -> Dict[str, Any]:
    """The project's floor-plan settings as a plan ``defaults`` block, or ``{}``.

    A real consumer of the law above, and the shape of every other one that
    follows: called at the moment ``floorplan_validate`` runs, off disk, with no
    argument the model could have filled in from memory. A project with no sheet
    — or one scoped to a different task — contributes nothing, so the plan's own
    numbers and then the service's own ``DEFAULTS`` still decide, in that order.
    """
    try:
        sheet = read(project_slug(project))
    except ForgeError:
        return {}
    if sheet.get("task") != "floorplan":
        return {}
    return {name: entry.get("value")
            for name, entry in sheet["settings"].items()}


def settled(project: Any) -> Dict[str, Any]:
    """Every setting's current value as a plain ``{name: value}`` mapping."""
    try:
        sheet = read(project_slug(project))
    except ForgeError:
        return {}
    return {name: entry.get("value")
            for name, entry in sheet["settings"].items()}


# ---------------------------------------------------------------------------
# Changing one
# ---------------------------------------------------------------------------


def coerce(entry: Mapping[str, Any], name: str, value: Any) -> Any:
    """*value* in the shape this setting holds, or a refusal in a sentence.

    The type to match is the DEFAULT's, because that is the one thing on the
    entry that cannot have been edited into something odd.
    """
    default = entry.get("default")

    if isinstance(default, bool):
        return _coerce_bool(name, value)
    if isinstance(default, list):
        return _coerce_list(entry, name, value)
    if isinstance(default, (int, float)):
        return _coerce_number(entry, name, value)
    return _coerce_choice(entry, name, value)


def _coerce_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in (
            "true", "false", "yes", "no", "on", "off"):
        return value.strip().lower() in ("true", "yes", "on")
    raise ForgeError(
        f"{name} is a yes/no setting, and {value!r} is neither. Pass true or "
        "false."
    )


def _coerce_number(entry: Mapping[str, Any], name: str, value: Any) -> Any:
    if isinstance(value, bool):
        raise ForgeError(f"{name} is a number, and {value!r} is a yes/no.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ForgeError(
            f"{name} is a number"
            + (f" in {entry['unit']}" if entry.get("unit") else "")
            + f", and {value!r} is not one."
        ) from None
    if isinstance(entry.get("default"), int) and not isinstance(entry["default"],
                                                               bool):
        if number != int(number):
            raise ForgeError(
                f"{name} is a whole number and {value!r} is not one."
            )
        number = int(number)

    choices = entry.get("choices")
    if isinstance(choices, list) and choices:
        if number not in choices:
            raise ForgeError(
                f"{name} can only be "
                + ", ".join(str(item) for item in choices)
                + f"; {value!r} is not one of them."
            )
        return number

    low, high = entry.get("min"), entry.get("max")
    if low is not None and number < low:
        raise ForgeError(
            f"{name} cannot be below {_short(low)}"
            + (f" {entry['unit']}" if entry.get("unit") else "")
            + f"; you asked for {_short(number)}."
        )
    if high is not None and number > high:
        raise ForgeError(
            f"{name} cannot be above {_short(high)}"
            + (f" {entry['unit']}" if entry.get("unit") else "")
            + f"; you asked for {_short(number)}."
        )
    return number


def _coerce_list(entry: Mapping[str, Any], name: str, value: Any) -> List[Any]:
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items:
        raise ForgeError(
            f"{name} cannot be empty — it is what the work actually produces."
        )
    choices = entry.get("choices")
    out: List[Any] = []
    for item in items:
        text = str(item).strip()
        if isinstance(choices, list) and choices and text not in [
                str(choice) for choice in choices]:
            raise ForgeError(
                f"{name} can only contain "
                + ", ".join(str(choice) for choice in choices)
                + f"; {item!r} is not one of them."
            )
        if text not in out:
            out.append(text)
    return out


def _coerce_choice(entry: Mapping[str, Any], name: str, value: Any) -> str:
    text = str(value).strip().strip('"').strip()
    if not text:
        raise ForgeError(f"{name} cannot be blank.")
    choices = entry.get("choices")
    if isinstance(choices, list) and choices:
        wanted = [str(choice) for choice in choices]
        if text not in wanted:
            lowered = {choice.lower(): choice for choice in wanted}
            if text.lower() in lowered:
                return lowered[text.lower()]
            raise ForgeError(
                f"{name} can only be " + ", ".join(wanted)
                + f"; {value!r} is not one of them."
            )
    return text


def apply(sheet: Dict[str, Any], name: Any, value: Any) -> Tuple[Any, Any]:
    """Set one setting on *sheet* in place. Returns ``(before, after)``."""
    key = "" if name is None else str(name).strip().strip('"').strip()
    settings = sheet["settings"]
    if key not in settings:
        match = {item.lower(): item for item in settings}
        if key.lower() in match:
            key = match[key.lower()]
        else:
            raise ForgeError(
                f"{name!r} is not a setting on the {sheet['task']} sheet. The "
                "settings are " + ", ".join(settings) + ". Nothing was changed."
            )
    entry = settings[key]
    before = entry.get("value")
    after = coerce(entry, key, value)
    entry["value"] = after
    sheet.setdefault("history", []).append({
        "date": _stamp(), "setting": key,
        "from": before, "to": after,
    })
    return before, after


# ---------------------------------------------------------------------------
# The echo-back report
# ---------------------------------------------------------------------------


def _short(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, list):
        return ", ".join(_short(item) for item in value)
    return str(value)


def changed(sheet: Mapping[str, Any]) -> List[str]:
    """The settings whose value is no longer their default, in sheet order."""
    return [name for name, entry in sheet["settings"].items()
            if entry.get("value") != entry.get("default")]


def fmt_sheet(
    *,
    sheet: Mapping[str, Any],
    path: Path,
    slug: str,
    head: str,
    documents: Optional[List[Dict[str, Any]]] = None,
    pipeline: str = "",
) -> str:
    """**The echo-back.** Every value, and which ones are no longer default.

    Every setting is printed even when nothing about it changed. That is the
    whole pattern: the artist learns the control surface by watching it filled
    in, and a report that listed only what somebody had already thought to
    mention would teach them exactly the things they already knew.

    *pipeline* is one line about the project's ``build-plan.json``, supplied by
    the caller rather than computed here for the same reason *settings* is on
    :func:`~forge_mcp.util.fmt_design_saved`: this is a formatter. A report that
    names the settings sheet and not the stage board tells the artist half of
    where their build's state lives.
    """
    task = str(sheet.get("task"))
    settings = sheet["settings"]
    edited = set(changed(sheet))

    width = max((len(name) for name in settings), default=0)
    lines = [head, f"  {path}",
             f"  task: {task} — {TASK_BLURB.get(task, '')}".rstrip(" —")]
    for name, entry in settings.items():
        value = _short(entry.get("value"))
        unit = entry.get("unit")
        shown = f"{value} {unit}" if unit else value
        mark = ("CHANGED (default " + _short(entry.get("default")) + ")"
                if name in edited else "default")
        lines.append(f"  {name.ljust(width)}  {shown}   [{mark}]")
        why = entry.get("why")
        if why:
            lines.append(f"  {' ' * width}    {why}")
        choices = entry.get("choices")
        if choices:
            lines.append(f"  {' ' * width}    one of: "
                         + ", ".join(_short(item) for item in choices))

    lines.append(
        f"  {len(settings)} settings, "
        + (f"{len(edited)} changed from default: " + ", ".join(sorted(edited))
           if edited else "none changed from their defaults yet")
    )
    lines.append(
        "  THESE ARE THE VALUES EVERY TOOL READS AT THE MOMENT IT RUNS. Change "
        "one with task_config_set(project, name, value) and say you did — never "
        "carry a value this sheet owns in conversation memory."
    )
    if documents:
        lines.append(f"  design sheet for {slug}: "
                     + ", ".join(item["file"] for item in documents))
    if pipeline:
        lines.append(pipeline)
    return "\n".join(lines)


def fmt_init(*, sheet: Mapping[str, Any], path: Path, slug: str,
             replaced: bool, pipeline: str = "") -> str:
    task = sheet.get("task")
    if replaced:
        head = f"Rebuilt the settings sheet for {slug} from the {task} template"
    else:
        head = (f"Materialised the settings sheet for {slug} — {task}, every "
                "knob already filled in")
    body = fmt_sheet(sheet=sheet, path=path, slug=slug, head=head,
                     documents=design_documents(slug), pipeline=pipeline)
    return body + (
        "\n  Show the artist the values that matter to them and ask which to "
        "change — that is the point of handing them a filled sheet rather than "
        "a blank question."
    )


def fmt_get(*, sheet: Mapping[str, Any], path: Path, slug: str,
            pipeline: str = "") -> str:
    return fmt_sheet(
        sheet=sheet, path=path, slug=slug,
        head=f"Settings sheet for {slug}, as it stands right now",
        documents=design_documents(slug),
        pipeline=pipeline,
    )


def fmt_set(*, sheet: Mapping[str, Any], path: Path, slug: str, name: str,
            before: Any, after: Any, pipeline: str = "") -> str:
    entry = sheet["settings"][name]
    unit = entry.get("unit")
    suffix = f" {unit}" if unit else ""
    if before == after:
        head = (f"{name} was already {_short(after)}{suffix} on {slug}'s sheet "
                "— nothing changed")
    else:
        head = (f"{name}: {_short(before)}{suffix} -> {_short(after)}{suffix} "
                f"on {slug}'s sheet")
    back_to_default = after == entry.get("default")
    body = fmt_sheet(sheet=sheet, path=path, slug=slug, head=head,
                     pipeline=pipeline)
    if back_to_default:
        body += f"\n  {name} is back at its default."
    return body


def mention(project: Any) -> str:
    """One line for another tool's report: does this project have a sheet?

    The design-phase tools print this so the settings file is never a thing the
    artist has to already know about. Never raises — a report is not the place
    to discover that a project name was odd.
    """
    try:
        slug = project_slug(project)
    except ForgeError:
        return ""
    path = config_path(slug)
    if path.is_file():
        return (f"  settings sheet: {path} — task_config_get(\"{slug}\") shows "
                "every value; a setting it owns is changed there, not in chat.")
    return (f"  no settings sheet yet: task_config_init(\"{slug}\", task) fills "
            "one in with every knob for that kind of work at its default, so "
            "the artist edits values instead of having to know the fields.")


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_VERSION",
    "PLAN_ANCHORS_FALLBACK",
    "PLAN_DEFAULTS_FALLBACK",
    "SETTING_KEYS",
    "TASKS",
    "TASK_BLURB",
    "TEMPLATES",
    "apply",
    "changed",
    "coerce",
    "config_path",
    "exists",
    "fmt_get",
    "fmt_init",
    "fmt_set",
    "fmt_sheet",
    "mention",
    "new_sheet",
    "normalize_task",
    "plan_defaults",
    "read",
    "setting",
    "settled",
    "template",
    "validate",
    "write",
]
