"""Per-stage cost recording on `projects/<slug>/design/build-plan.json`.

The assistant bridge already tracks per-job cost (USD + tokens) but nothing
attributes it to a pipeline stage, so nobody can tell whether skin, animate or
chat overhead dominates spending. This adds an optional `cost` a caller can
attach when it advances or records a stage, and asks the same five things of
it that `test_pipeline.py` asks of everything else:

* **A recording is checked, not trusted.** Unknown keys, negative amounts and
  a non-string model are refused the way an unnamed measurement is.
* **A stage worked more than once accumulates.** `pipeline_record` (and
  `pipeline_advance`) fold each call's spend onto the stage's running total
  instead of overwriting it — `replace=true` throws away numbers/artifacts but
  never the cost total.
* **Every increment stays auditable.** Each recording lands in that stage's
  own history, beside the verdict it came with.
* **The board never invents a number.** A stage with no cost recorded prints
  no cost line, and the plan-level total is absent until something has spent
  anything at all.
* **Backward compatibility is a gate.** A plan written before cost existed —
  the werewolf's real one — loads, advances and re-saves untouched except for
  what the operation itself changes.

`projects/` is redirected to a tmp_path for every test, exactly as
`test_pipeline.py` does it, so the real repo folder is never touched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client

from forge_mcp import config, pipeline, server, task_config
from forge_mcp.errors import ForgeError

# ---------------------------------------------------------------------------
# Shared helpers — mirrors test_pipeline.py's own so this file reads the same.
# ---------------------------------------------------------------------------


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


def plan_on_disk(projects_dir: Path, slug: str) -> dict:
    path = projects_dir / slug / "design" / "build-plan.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sheet(slug: str, task: str = "character") -> Path:
    return task_config.write(slug, task_config.new_sheet(slug, task))


def start(slug: str, task: str = "character") -> dict:
    """A project with a sheet and a written plan at its first stage."""
    sheet(slug, task)
    _slug, plan, _path, _fresh = pipeline.load(slug)
    pipeline.write(slug, plan)
    return plan


#: The werewolf's real plan, shortened but SHAPED exactly like it — the same
#: fixture test_pipeline.py uses for the migration tests: version 1, notes,
#: components, no `task` and no `stages` because it predates both.
WEREWOLF_PLAN: dict = {
    "version": 1,
    "project": "werewolf",
    "notes": "Three-form player character. Forms A/B share one mesh+skeleton.",
    "components": [
        {
            "id": "form-a-human-base",
            "label": "Form A — human base mesh",
            "status": "built",
            "scene_objects": {"sculpt_source": "werewolf-form-a",
                              "game_mesh": "werewolf-form-a_retopo"},
            "description": "REBUILT 2026-09-18 as the definitive Form A.",
        },
        {
            "id": "form-a-rig",
            "label": "Form A/B — shared biped skeleton",
            "status": "built",
            "rig_check_2026_09_18_full": {
                "gate": "FAIL overall",
                "rest_intersections": 608,
            },
        },
        {"id": "form-b-shape-keys", "label": "Form B", "status": "pending"},
    ],
}


def write_werewolf(projects_dir: Path) -> Path:
    design = projects_dir / "werewolf" / "design"
    design.mkdir(parents=True)
    path = design / "build-plan.json"
    path.write_text(json.dumps(WEREWOLF_PLAN, indent=2), encoding="utf-8")
    sheet("werewolf", "character")
    return path


# ---------------------------------------------------------------------------
# Validation: coerce_cost refuses politely, the way coerce_numbers does.
# ---------------------------------------------------------------------------


def test_coerce_cost_accepts_the_documented_shape() -> None:
    assert pipeline.coerce_cost(None) == {}
    assert pipeline.coerce_cost({}) == {}
    assert pipeline.coerce_cost({
        "usd": 1.23, "tokens_in": 4000, "tokens_out": 900, "model": "sonnet",
    }) == {"usd": 1.23, "tokens_in": 4000, "tokens_out": 900, "model": "sonnet"}


def test_coerce_cost_refuses_an_unknown_key() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_cost({"usd": 1.0, "currency": "usd"})
    assert "currency" in str(exc.value)
    assert "Nothing was changed" in str(exc.value)


def test_coerce_cost_refuses_a_negative_amount() -> None:
    for bad in ({"usd": -0.01}, {"tokens_in": -1}, {"tokens_out": -1}):
        with pytest.raises(ForgeError) as exc:
            pipeline.coerce_cost(bad)
        assert "negative" in str(exc.value)


def test_coerce_cost_refuses_a_non_number() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_cost({"usd": "1.23"})
    assert "not a number" in str(exc.value)
    with pytest.raises(ForgeError):
        pipeline.coerce_cost({"usd": True})


def test_coerce_cost_refuses_a_bad_model() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_cost({"model": ""})
    assert "model" in str(exc.value)
    with pytest.raises(ForgeError):
        pipeline.coerce_cost({"model": 7})


def test_coerce_cost_is_not_a_plan_it_is_a_mapping() -> None:
    with pytest.raises(ForgeError) as exc:
        pipeline.coerce_cost(["usd", 1.23])
    assert "not a" in str(exc.value)


# ---------------------------------------------------------------------------
# pipeline_record accumulates cost across multiple calls
# ---------------------------------------------------------------------------


def test_cost_accumulates_across_recordings(projects_dir: Path) -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "in_progress",
        "cost": {"usd": 1.00, "tokens_in": 1000, "tokens_out": 200,
                 "model": "sonnet"},
    })
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "cost": {"usd": 0.50, "tokens_in": 500, "tokens_out": 100,
                 "model": "opus"},
    })

    plan = plan_on_disk(projects_dir, "gecko")
    stage = next(item for item in plan["stages"] if item["id"] == "reference")
    assert stage["cost"] == {
        "usd": 1.50, "tokens_in": 1500, "tokens_out": 300,
        "models": ["sonnet", "opus"],
    }


def test_a_cost_only_recording_does_not_need_a_new_model_every_time(
        projects_dir: Path) -> None:
    """The same model recorded twice does not duplicate in `models`."""
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    for _ in range(3):
        call("pipeline_record", {
            "project": "gecko", "stage": "reference", "status": "in_progress",
            "cost": {"usd": 0.10, "model": "sonnet"},
        })
    plan = plan_on_disk(projects_dir, "gecko")
    stage = next(item for item in plan["stages"] if item["id"] == "reference")
    assert stage["cost"]["usd"] == pytest.approx(0.30)
    assert stage["cost"]["models"] == ["sonnet"]


def test_cost_accumulates_even_when_replace_is_asked_for() -> None:
    """`replace` throws away numbers/artifacts, never the cost total — a stage
    worked three times spent whatever those three turns cost, not just the
    last one."""
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "verify_mesh",
                              "override": True, "who": "kenneth",
                              "why": "testing"})
    call("pipeline_record", {
        "project": "gecko", "stage": "verify_mesh", "status": "in_progress",
        "numbers": {"a": 1}, "cost": {"usd": 1.0}})
    call("pipeline_record", {
        "project": "gecko", "stage": "verify_mesh", "status": "in_progress",
        "numbers": {"b": 2}, "cost": {"usd": 2.0}, "replace": True})

    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "verify_mesh")
    # replace threw away "a" (numbers are replaced)...
    assert stage["numbers"] == {"b": 2}
    # ...but the cost total still has both turns' spend in it.
    assert stage["cost"]["usd"] == pytest.approx(3.0)


def test_a_stage_with_no_cost_recorded_carries_no_cost_key() -> None:
    """Never invent a zero: a stage nobody spent anything on has no `cost`."""
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4}})
    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "reference")
    assert "cost" not in stage


# ---------------------------------------------------------------------------
# History: every increment stays auditable
# ---------------------------------------------------------------------------


def test_each_recording_leaves_its_own_cost_in_history(
        projects_dir: Path) -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "in_progress",
        "cost": {"usd": 1.0, "model": "sonnet"}})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "cost": {"usd": 2.0, "model": "opus"}})

    plan = plan_on_disk(projects_dir, "gecko")
    stage = next(item for item in plan["stages"] if item["id"] == "reference")
    cost_entries = [item["cost"] for item in stage["history"] if "cost" in item]
    assert cost_entries == [
        {"usd": 1.0, "model": "sonnet"},
        {"usd": 2.0, "model": "opus"},
    ]
    # The accumulated total on the stage is not what any single increment
    # says — it is their sum, which is why both increments have to stay.
    assert stage["cost"]["usd"] == 3.0


def test_pipeline_advance_can_also_attach_a_cost_and_it_is_audited() -> None:
    start("gecko")
    report = text_of(call("pipeline_advance", {
        "project": "gecko", "stage": "reference",
        "cost": {"usd": 0.05, "model": "haiku"}}))
    assert "spent $0.05" in report

    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "reference")
    assert stage["cost"] == {"usd": 0.05, "models": ["haiku"]}
    advance_entries = [item for item in stage["history"]
                       if item["action"] == "advance"]
    assert advance_entries[-1]["cost"] == {"usd": 0.05, "model": "haiku"}


# ---------------------------------------------------------------------------
# advance-over-red still refuses regardless of cost fields
# ---------------------------------------------------------------------------


def _through_verify(slug: str, verdict: str, numbers: dict) -> None:
    start(slug)
    for stage, measured in (
        ("reference", {"references_on_disk": 4}),
        ("design", {"sign_off": True}),
        ("generate", {"watertight": True}),
        ("clean", {"symmetry_residual_mm": 0.0}),
    ):
        call("pipeline_advance", {"project": slug, "stage": stage})
        call("pipeline_record", {"project": slug, "stage": stage,
                                 "status": "passed", "numbers": measured})
    call("pipeline_advance", {"project": slug, "stage": "verify_mesh"})
    call("pipeline_record", {"project": slug, "stage": "verify_mesh",
                             "status": verdict, "numbers": numbers})


def test_advance_over_a_red_gate_refuses_even_with_a_cost_attached() -> None:
    _through_verify("gecko", "failed", {"self_intersections": 608})

    result = call("pipeline_advance", {
        "project": "gecko", "stage": "rig",
        "cost": {"usd": 5.00, "model": "opus"}})
    assert result.is_error
    message = text_of(result)
    assert "verify_mesh" in message
    assert "override=true" in message
    # Nothing was spent, because nothing was allowed to happen.
    assert pipeline.stage_status("gecko", "rig") == "pending"
    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "rig")
    assert "cost" not in stage


def test_record_still_refuses_to_pass_with_nothing_measured_cost_or_not() -> None:
    """Cost is not a substitute for a measurement — the opinion refusal still
    holds even when a cost is attached to the same call."""
    start("gecko")
    result = call("pipeline_record", {
        "project": "gecko", "stage": "generate", "status": "passed",
        "cost": {"usd": 1.00}})
    assert result.is_error
    assert "opinion" in text_of(result)
    _slug, plan, _path, _fresh = pipeline.load("gecko")
    _index, stage = pipeline.find_stage(plan, "generate")
    assert "cost" not in stage


def test_an_invalid_cost_refuses_the_whole_record_call() -> None:
    """A malformed cost blocks the call the same way a malformed number does —
    nothing about the stage changes."""
    start("gecko")
    result = call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "cost": {"usd": -1.0}})
    assert result.is_error
    assert "negative" in text_of(result)
    assert pipeline.stage_status("gecko", "reference") == "pending"


# ---------------------------------------------------------------------------
# Status rendering, with and without cost
# ---------------------------------------------------------------------------


def test_board_shows_no_cost_lines_when_nothing_was_ever_recorded() -> None:
    start("gecko")
    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "cost:" not in board
    assert "cost total:" not in board


def test_board_shows_per_stage_cost_and_a_plan_total(projects_dir: Path) -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "cost": {"usd": 1.23, "tokens_in": 4000, "tokens_out": 900,
                 "model": "sonnet"}})
    call("pipeline_advance", {"project": "gecko", "stage": "design"})
    call("pipeline_record", {
        "project": "gecko", "stage": "design", "status": "passed",
        "numbers": {"sign_off": True, "settings_sheet": "materialised"},
        "cost": {"usd": 0.77}})

    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "cost: $1.23" in board
    assert "4000 in / 900 out" in board
    assert "sonnet" in board
    assert "cost: $0.77" in board
    assert "cost total: $2.00" in board

    # The untouched stages carry no cost line at all.
    lines = board.splitlines()
    generate_index = next(i for i, line in enumerate(lines)
                          if "3. generate" in line)
    # The next non-indented-detail line (either the next stage row or the
    # tally) proves nothing about cost was inserted for `generate`.
    following = "\n".join(lines[generate_index:generate_index + 4])
    assert "cost:" not in following


def test_a_recording_only_call_shows_up_on_the_board_too() -> None:
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "in_progress",
        "cost": {"usd": 0.42}})
    board = text_of(call("pipeline_status", {"project": "gecko"}))
    assert "cost: $0.42" in board
    assert "cost total: $0.42" in board


# ---------------------------------------------------------------------------
# Backward compatibility: a no-cost plan round-trips byte-identical apart
# from the intended mutation.
# ---------------------------------------------------------------------------


def test_a_plan_with_no_cost_round_trips_untouched_by_a_plain_write(
        projects_dir: Path) -> None:
    """Loading and re-writing a cost-free plan changes nothing about it."""
    start("gecko")
    before_text = (projects_dir / "gecko" / "design" /
                   "build-plan.json").read_text(encoding="utf-8")

    _slug, plan, _path, _fresh = pipeline.load("gecko")
    pipeline.write("gecko", plan)

    after_text = (projects_dir / "gecko" / "design" /
                  "build-plan.json").read_text(encoding="utf-8")
    assert after_text == before_text
    assert '"cost"' not in after_text


def test_advancing_and_recording_without_cost_never_introduces_a_cost_key(
        projects_dir: Path) -> None:
    """The intended mutation (advance + record) happens; nothing else does —
    in particular no stage grows a `cost` key nobody asked for."""
    start("gecko")
    call("pipeline_advance", {"project": "gecko", "stage": "reference"})
    call("pipeline_record", {
        "project": "gecko", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 4},
        "artifacts": ["projects/gecko/design/refs/front.png"]})

    plan = plan_on_disk(projects_dir, "gecko")
    for stage in plan["stages"]:
        assert "cost" not in stage, stage["id"]
    assert "cost_total" not in plan


def test_the_werewolf_plan_gains_no_cost_from_a_plain_migration(
        projects_dir: Path) -> None:
    """The real werewolf-shaped plan (predates stages entirely) migrates and
    round-trips with no cost anywhere, exactly like every other field it
    never had."""
    write_werewolf(projects_dir)
    slug, plan, _path, fresh = pipeline.load("werewolf")
    assert fresh is True
    pipeline.write(slug, plan)

    after = plan_on_disk(projects_dir, "werewolf")
    assert after["components"] == WEREWOLF_PLAN["components"]
    assert after["notes"] == WEREWOLF_PLAN["notes"]
    for stage in after["stages"]:
        assert "cost" not in stage

    board = text_of(call("pipeline_status", {"project": "werewolf"}))
    assert "cost:" not in board
    assert "cost total:" not in board


def test_driving_the_werewolf_plan_with_cost_keeps_its_components(
        projects_dir: Path) -> None:
    """Cost recording on a migrated plan behaves exactly as it does on a
    fresh one, and still leaves the pre-existing components untouched."""
    write_werewolf(projects_dir)
    call("pipeline_advance", {"project": "werewolf", "stage": "reference"})
    call("pipeline_record", {
        "project": "werewolf", "stage": "reference", "status": "passed",
        "numbers": {"references_on_disk": 6},
        "cost": {"usd": 3.14, "model": "sonnet"}})

    after = plan_on_disk(projects_dir, "werewolf")
    assert after["components"] == WEREWOLF_PLAN["components"]
    assert after["notes"] == WEREWOLF_PLAN["notes"]
    stage = next(item for item in after["stages"] if item["id"] == "reference")
    assert stage["status"] == "passed"
    assert stage["cost"] == {"usd": 3.14, "models": ["sonnet"]}


# ---------------------------------------------------------------------------
# Loading old on-disk data that already carries a hand-written `cost`
# ---------------------------------------------------------------------------


def test_a_stage_with_a_hand_written_cost_mapping_reads_fine() -> None:
    plan = pipeline.new_plan("gecko", "character")
    plan["stages"][0]["cost"] = {"usd": 2.5, "models": ["sonnet"]}
    validated = pipeline.validate(plan)
    assert validated["stages"][0]["cost"] == {"usd": 2.5, "models": ["sonnet"]}


def test_a_stage_whose_cost_is_not_a_mapping_is_refused() -> None:
    plan = pipeline.new_plan("gecko", "character")
    plan["stages"][0]["cost"] = "a lot"
    with pytest.raises(ForgeError) as exc:
        pipeline.validate(plan)
    assert "cost" in str(exc.value)


# ---------------------------------------------------------------------------
# The tools build nothing — a cost-carrying call is still a state-machine call
# ---------------------------------------------------------------------------


def test_cost_recording_tools_touch_no_backend(dead_backends) -> None:
    start("gecko")
    for name, arguments in (
        ("pipeline_advance", {"project": "gecko", "stage": "reference",
                              "cost": {"usd": 0.01}}),
        ("pipeline_record", {"project": "gecko", "stage": "reference",
                             "status": "passed",
                             "numbers": {"references_on_disk": 2},
                             "cost": {"usd": 0.02}}),
    ):
        result = call(name, arguments)
        assert not result.is_error, f"{name}: {text_of(result)}"
