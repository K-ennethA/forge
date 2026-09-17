"""Maker mode (Phase 10): the catalog, the circuit and the push mechanic.

These are the only tools in the server with no backend behind them, so unlike
every other suite here **nothing is faked**. `forge_mcp.maker` imports
`service/components.py`, `service/wiring.py` and the arithmetic half of
`service/maker_lib.py` straight off disk and calls them, and that cross-package
import is precisely the thing most likely to break silently — a repo split, a
rename, or a top-level build123d import creeping into a module that used to be
pure stdlib. So the first tests here assert the import ITSELF works from this
venv, and the rest assert the two circuit answers whose numbers a person can
check by hand:

* white LED + CR2032  -> **no resistor needed** (3.0 V of LED on a 3.0 V cell)
* red LED + 6 V       -> **220 ohm required** ((6 - 2) / 0.02 = 200, up to E12)

If the second one ever answers 200, the E12 rounding broke. If it answers 180,
it rounded the wrong way, which is the direction that cooks LEDs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.client.client import Client

from forge_mcp import config, maker, server, util
from forge_mcp.errors import ForgeError

REPO_ROOT = Path(__file__).resolve().parents[2]


def call(name: str, arguments: dict | None = None) -> str:
    """One tools/call over the SDK's in-memory session; returns the text."""

    async def run():
        async with Client(server.app) as client:
            return await client.call_tool(name, arguments or {})

    result = asyncio.run(run())
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


# --- the cross-package import ----------------------------------------------


def test_the_service_package_root_is_the_repo_root() -> None:
    """The coupling's one piece of configuration points where service/ lives."""
    root = Path(config.SERVICE_PACKAGE_ROOT)
    assert (root / "service" / "components.py").is_file()
    assert (root / "service" / "wiring.py").is_file()
    assert (root / "service" / "maker_lib.py").is_file()
    assert root == REPO_ROOT


def test_the_service_modules_import_into_this_venv() -> None:
    """The whole design rests on these three loading without build123d."""
    components, wiring, maker_lib = maker.modules()
    assert Path(components.__file__).parent.name == "service"
    assert callable(wiring.circuit_plan)
    assert callable(wiring.wiring_steps)
    assert callable(wiring.bill_of_materials)
    assert callable(maker_lib.plunger_plan)


def test_the_arithmetic_half_needs_no_geometry_kernel() -> None:
    """`plunger_plan` and `circuit_plan` run with no build123d in this process.

    The lazy imports inside maker_lib's geometry functions are what make that
    true; a top-level `from build123d import *` would fail this the day it lands.
    """
    import sys

    assert "build123d" not in sys.modules
    maker.circuit()
    maker.plunger(6.0)
    assert "build123d" not in sys.modules


def test_a_missing_service_folder_is_one_readable_sentence(monkeypatch, tmp_path) -> None:
    """A checkout without service/ answers with the fix, not a traceback.

    Both the module cache and the path entry an earlier successful import left
    behind have to go, or this would pass on a stale `service` in sys.modules
    and prove nothing.
    """
    import sys

    monkeypatch.setattr(maker, "_MODULES", None)
    monkeypatch.setattr(config, "SERVICE_PACKAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path if Path(p or ".") != REPO_ROOT]
    )
    for name in [n for n in sys.modules if n == "service" or n.startswith("service.")]:
        monkeypatch.delitem(sys.modules, name)

    with pytest.raises(ForgeError) as excinfo:
        maker.modules()
    message = str(excinfo.value)
    assert "FORGE_SERVICE_PACKAGE_ROOT" in message
    assert str(tmp_path) in message
    # And the failure is survivable: the next real call re-imports cleanly.
    maker._MODULES = None


# --- the catalog ------------------------------------------------------------


def test_the_catalog_is_the_whole_table_with_no_filter() -> None:
    result = maker.catalog()
    assert result["match"] == "all"
    assert len(result["records"]) == 18
    assert {rec["category"] for rec in result["records"]} == set(result["categories"])


@pytest.mark.parametrize(
    ("query", "match"),
    [
        ("switch", "category"),
        ("power", "category"),
        ("tactile_6x6_latching", "name"),
        ("tactile", "search"),
    ],
)
def test_a_filter_says_how_it_matched(query: str, match: str) -> None:
    assert maker.catalog(query)["match"] == match


def test_an_unknown_filter_names_the_categories_and_the_parts() -> None:
    with pytest.raises(ForgeError) as excinfo:
        maker.catalog("relay")
    message = str(excinfo.value)
    assert "switch" in message and "tactile_6x6_latching" in message


def test_the_catalog_tool_leads_with_the_design_around_components_law() -> None:
    text = call("maker_components")
    assert "PICK THE PART FIRST" in text
    # Purchase notes are the whole point of "what should I buy".
    assert "self-locking" in text
    # Clone honesty, in millimetres, unprompted.
    assert "0.3 mm" in text


def test_one_named_component_is_the_full_card() -> None:
    text = call("maker_components", {"filter": "tactile_6x6_latching"})
    assert "BUY:" in text
    assert "VERIFY AGAINST YOUR PART" in text
    assert "where Z = 0 is" in text
    assert "max overtravel 0.3 mm" in text


def test_a_category_filter_lists_only_that_category() -> None:
    text = call("maker_components", {"filter": "magnet"})
    assert "MAGNET (" in text
    assert "SWITCH (" not in text


# --- the circuit ------------------------------------------------------------


def test_white_led_on_a_coin_cell_needs_no_resistor() -> None:
    plan = maker.circuit("led_5mm", color="white", cell="cr2032_cell")
    assert plan["verdict"] == "no resistor needed"
    assert plan["resistor_ohms"] == 0.0
    assert plan["headroom_v"] <= 0.0
    # The cell's own internal resistance is the real limiter, and the plan has
    # to say so rather than leaving "no resistor" looking like an oversight.
    assert plan["reason"]


def test_a_red_led_on_six_volts_wants_220_ohm() -> None:
    plan = maker.circuit("led_5mm", color="red", cell="aaa_pair_box", cells=2)
    assert plan["verdict"] == "resistor required"
    assert plan["supply"]["voltage_v"] == 6.0
    # (6.0 - 2.0) / 0.020 = 200 exactly, rounded UP to the next E12 value.
    assert plan["resistor_exact_ohms"] == pytest.approx(200.0)
    assert plan["resistor_ohms"] == 220.0
    assert plan["resistor_rating_w"] >= plan["resistor_power_w"]


def test_the_same_led_on_one_coin_cell_only_makes_it_optional() -> None:
    """The cell, not the LED, is what moves the verdict — worth pinning."""
    plan = maker.circuit("led_5mm", color="red", cell="cr2032_cell")
    assert plan["verdict"] == "resistor optional"


def test_the_circuit_tool_renders_the_verdict_in_capitals() -> None:
    text = call("circuit_plan", {"color": "white"})
    assert "VERDICT: NO RESISTOR NEEDED" in text
    assert "wiring_guide" in text


def test_the_circuit_tool_shows_the_arithmetic_when_a_resistor_is_needed() -> None:
    text = call(
        "circuit_plan",
        {"led": "led_5mm", "color": "red", "cell": "aaa_pair_box", "cells": 2},
    )
    assert "220 ohm" in text
    assert "E12" in text


def test_an_unknown_component_is_refused_with_the_catalog(dead_backends) -> None:
    """A service refusal reaches the model as its own sentence, not a traceback."""
    with pytest.raises(ForgeError) as excinfo:
        maker.circuit(cell="aa_cell")
    assert "cr2032_cell" in str(excinfo.value)


# --- the wiring guide -------------------------------------------------------


def test_the_guide_is_thirteen_steps_and_a_shopping_list() -> None:
    guide = maker.wiring_guide("led_5mm", color="white", cell="cr2032_cell")
    assert len(guide["steps"]) == 13
    assert guide["bom"]
    assert all({"item", "quantity", "note"} <= set(row) for row in guide["bom"])


def test_the_guide_keeps_polarity_and_test_before_glue() -> None:
    text = call("wiring_guide", {"color": "white"})
    assert "longer leg = +" in text
    assert "before a single drop of glue" in text.lower()
    assert "SHOPPING LIST" in text


def test_the_shopping_list_carries_the_purchase_notes() -> None:
    text = call(
        "wiring_guide",
        {"led": "led_5mm", "color": "red", "cell": "aaa_pair_box", "cells": 2},
    )
    # The resistor only appears on the list when the verdict asked for one.
    assert "resistor 220 ohm" in text
    # Every line is a thing to search for, not a bare part number.
    assert "Search '" in text
    assert "heat-shrink" in text


def test_the_shopping_list_hands_over_the_links_it_was_given() -> None:
    """The catalogue has carried a `purchase_link` per row all along.

    The 2026-09-16 dogfood asked for a shopping list WITH links and got search
    strings and an apology (finding G-2) — not because the data was missing but
    because this formatter read `item`, `quantity`, `source` and `note` and
    dropped the column beside them. A link the tool has and does not print is a
    link the artist does not get.
    """
    guide = maker.wiring_guide(
        "led_5mm", color="red", cell="aaa_pair_box", cells=2
    )
    links = [row.get("purchase_link") for row in guide["bom"]]
    assert all(links), "every BOM row carries a link at source"

    text = call(
        "wiring_guide",
        {"led": "led_5mm", "color": "red", "cell": "aaa_pair_box", "cells": 2},
    )
    for link in links:
        assert str(link) in text, "and every one of them reaches the report"
    assert f"{len(links)} of the {len(guide['bom'])} lines carry a link" in text
    # A search URL is not a promise about stock, a price or a seller, and this
    # lane never buys anything.
    assert "never promise a price" in text
    assert "NEVER buy anything" in text


def test_the_catalog_card_shows_the_link_as_well_as_the_search_term() -> None:
    text = call("maker_components", {"filter": "tactile_6x6_latching"})
    assert "https://" in text, "the full card carries the purchase link"


def test_a_switchless_circuit_still_wires(dead_backends) -> None:
    text = call("wiring_guide", {"color": "white", "switch": None})
    assert "SOLDER IT IN THIS ORDER" in text


# --- the push mechanic ------------------------------------------------------


def test_the_plunger_reports_travel_end_stop_and_the_return() -> None:
    plan = maker.plunger(6.0, switch="tactile_6x6_latching")
    # 1.5 mm of latch stroke + 0.3 mm overtravel + 0.3 mm free play.
    assert plan["travel_mm"] == pytest.approx(2.1)
    assert plan["end_stop_protects_switch"] is True
    assert plan["assembly"]


def test_overtravel_is_clamped_by_the_component_and_the_clamp_is_named() -> None:
    """THE load-bearing field: what the switch can take, never a constant."""
    plan = maker.plunger(6.0, switch="tactile_6x6_latching", overtravel=5.0)
    assert plan["clamped"]
    assert any("overtravel" in str(note) for note in plan["clamped"])
    text = call("plunger_plan", {"stem_diameter": 6.0, "overtravel": 5.0})
    assert "CLAMPED" in text


def test_a_slide_switch_refuses_a_plunger_and_says_what_to_use() -> None:
    with pytest.raises(ForgeError) as excinfo:
        maker.plunger(6.0, switch="slide_switch_sk12")
    message = str(excinfo.value)
    assert "SLIDE switch" in message
    assert "tactile_6x6_latching" in message


def test_a_panel_switch_refuses_a_plunger_because_it_already_is_one() -> None:
    with pytest.raises(ForgeError) as excinfo:
        maker.plunger(6.0, switch="push_latching_12mm")
    assert "already IS the plunger" in str(excinfo.value)


def test_the_plunger_tool_points_at_the_script_that_builds_the_solid() -> None:
    text = call("plunger_plan", {"stem_diameter": 6.0})
    assert "maker_lib.plunger()" in text
    assert "partforge_generate" in text


# --- the renderers, on canned data -----------------------------------------


def test_a_half_record_still_renders_the_half_it_has() -> None:
    """Renderers take dicts and never raise on a missing key, by design."""
    text = util.fmt_component_catalog(
        {"records": [{"name": "mystery", "category": "switch"}], "match": "all"}
    )
    assert "mystery" in text


def test_an_empty_plan_does_not_explode() -> None:
    assert util.fmt_circuit_plan({})
    assert util.fmt_wiring_guide({})
    assert util.fmt_plunger_plan({})


def test_nothing_in_the_rendered_catalog_runs_past_the_wrap_width() -> None:
    """The panel is narrow. Every prose line is folded, closing lines included."""
    for text in (
        call("maker_components"),
        call("maker_components", {"filter": "tactile_6x6_latching"}),
        call("circuit_plan", {"color": "white"}),
    ):
        for line in text.splitlines():
            assert len(line) <= 100, line
