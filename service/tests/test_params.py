"""Tests for the PARAMS contract: schema validation, coercion, ranges, units.

Nothing here needs build123d -- :mod:`service.params` is pure Python -- so these
run the moment pytest is installed.
"""

from __future__ import annotations

import pytest

from service import params
from service.errors import ParamError, ScriptError

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

GOOD_SCHEMA = {
    "width": {
        "value": 10.0,
        "unit": "mm",
        "min": 1.0,
        "max": 100.0,
        "step": 0.1,
        "description": "Width",
    },
    "thickness_in": {"value": 0.5, "unit": "in", "min": 0.1, "max": 2.0},
    "twist": {"value": 15.0, "unit": "deg", "min": -90.0, "max": 90.0},
    "segments": {"value": 4, "unit": "count", "min": 3, "max": 12, "step": 1},
    "fill": {"value": 0.35, "unit": "ratio", "min": 0.0, "max": 1.0},
    "hollow": {"value": True, "unit": "bool"},
}

SCRIPT = '''
PARAMS = {
    "size": {"value": 10.0, "unit": "mm", "min": 1.0, "max": 50.0},
    "holes": {"value": 2, "unit": "count", "min": 0, "max": 8},
}

def build(p):
    return ("built", p["size"], p["holes"])
'''


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def test_valid_schema_is_normalised_and_copied():
    schema = params.validate_schema(GOOD_SCHEMA)
    assert set(schema) == set(GOOD_SCHEMA)
    assert schema["width"]["description"] == "Width"
    # It is a copy: mutating the result must not touch the script's dict.
    schema["width"]["value"] = 99.0
    assert GOOD_SCHEMA["width"]["value"] == 10.0


def test_unknown_spec_keys_are_preserved():
    schema = params.validate_schema(
        {"a": {"value": 1.0, "unit": "mm", "group": "Body", "label": "A"}}
    )
    assert schema["a"]["group"] == "Body"
    assert schema["a"]["label"] == "A"


def test_missing_value_is_rejected():
    with pytest.raises(ParamError, match="'value' is required"):
        params.validate_schema({"a": {"unit": "mm"}})


def test_missing_unit_is_rejected():
    with pytest.raises(ParamError, match="'unit' is required"):
        params.validate_schema({"a": {"value": 1.0}})


def test_unsupported_unit_is_rejected():
    with pytest.raises(ParamError, match="not supported"):
        params.validate_schema({"a": {"value": 1.0, "unit": "furlong"}})


def test_non_identifier_name_is_rejected():
    with pytest.raises(ParamError, match="not a valid Python identifier"):
        params.validate_schema({"wall thickness": {"value": 1.0, "unit": "mm"}})


def test_min_greater_than_max_is_rejected():
    with pytest.raises(ParamError, match="greater than max"):
        params.validate_schema({"a": {"value": 1.0, "unit": "mm", "min": 5, "max": 2}})


def test_non_positive_step_is_rejected():
    with pytest.raises(ParamError, match="step must be greater than zero"):
        params.validate_schema({"a": {"value": 1.0, "unit": "mm", "step": 0}})


def test_declared_default_outside_its_own_range_is_rejected():
    with pytest.raises(ParamError, match="PARAMS default"):
        params.validate_schema(
            {"a": {"value": 0.5, "unit": "mm", "min": 1.0, "max": 5.0}}
        )


def test_non_finite_value_is_rejected():
    with pytest.raises(ParamError, match="finite"):
        params.validate_schema({"a": {"value": float("inf"), "unit": "mm"}})


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------


def test_numeric_strings_are_accepted():
    schema, values = params.resolve(GOOD_SCHEMA, {"width": "12.5"})
    assert schema["width"]["value"] == 12.5
    assert values["width"] == 12.5


def test_int_override_on_float_param_becomes_float():
    _schema, values = params.resolve(GOOD_SCHEMA, {"width": 12})
    assert isinstance(values["width"], float)


def test_bool_is_rejected_for_a_numeric_param():
    with pytest.raises(ParamError, match="got a bool"):
        params.resolve(GOOD_SCHEMA, {"width": True})


def test_count_accepts_integral_float_and_rejects_fractional():
    _schema, values = params.resolve(GOOD_SCHEMA, {"segments": 6.0})
    assert values["segments"] == 6
    assert isinstance(values["segments"], int)

    with pytest.raises(ParamError, match="whole number"):
        params.resolve(GOOD_SCHEMA, {"segments": 6.5})


def test_bool_accepts_strings_and_ignores_range():
    _schema, values = params.resolve(GOOD_SCHEMA, {"hollow": "false"})
    assert values["hollow"] is False

    _schema, values = params.resolve(GOOD_SCHEMA, {"hollow": 1})
    assert values["hollow"] is True


def test_garbage_value_is_rejected_with_the_param_name():
    with pytest.raises(ParamError, match="width"):
        params.resolve(GOOD_SCHEMA, {"width": "wide"})


# --------------------------------------------------------------------------
# Range enforcement
# --------------------------------------------------------------------------


def test_override_below_min_is_rejected_and_names_the_param():
    with pytest.raises(ParamError) as excinfo:
        params.resolve(GOOD_SCHEMA, {"width": 0.5})
    message = str(excinfo.value)
    assert "width" in message and "minimum" in message and "1.0" in message


def test_override_above_max_is_rejected():
    with pytest.raises(ParamError, match="maximum"):
        params.resolve(GOOD_SCHEMA, {"width": 1000.0})


def test_clamp_mode_pulls_values_to_the_bound():
    schema, values = params.resolve(GOOD_SCHEMA, {"width": 1000.0}, clamp=True)
    assert schema["width"]["value"] == 100.0
    assert values["width"] == 100.0

    schema, values = params.resolve(GOOD_SCHEMA, {"segments": 99}, clamp=True)
    assert values["segments"] == 12
    assert isinstance(values["segments"], int)


def test_unknown_override_is_rejected():
    with pytest.raises(ParamError, match="unknown parameter 'widht'"):
        params.resolve(GOOD_SCHEMA, {"widht": 10.0})


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def test_inches_are_converted_to_mm_for_build_only():
    schema, values = params.resolve(GOOD_SCHEMA, {"thickness_in": 1.0})
    # The panel keeps inches...
    assert schema["thickness_in"]["value"] == 1.0
    assert schema["thickness_in"]["unit"] == "in"
    # ...build() gets millimetres.
    assert values["thickness_in"] == pytest.approx(25.4)


def test_default_inch_value_is_converted_too():
    _schema, values = params.resolve(GOOD_SCHEMA)
    assert values["thickness_in"] == pytest.approx(12.7)


def test_inch_ranges_are_checked_in_inches():
    # 3.0 in is over the 2.0 in maximum even though 3.0 mm would be fine.
    with pytest.raises(ParamError, match="maximum"):
        params.resolve(GOOD_SCHEMA, {"thickness_in": 3.0})


def test_other_units_pass_through_unchanged():
    _schema, values = params.resolve(GOOD_SCHEMA)
    assert values["width"] == 10.0          # mm stays mm
    assert values["twist"] == 15.0          # degrees stay degrees
    assert values["fill"] == pytest.approx(0.35)
    assert values["segments"] == 4
    assert values["hollow"] is True


# --------------------------------------------------------------------------
# Script execution
# --------------------------------------------------------------------------


def test_load_script_returns_schema_values_and_build():
    schema, values, build = params.load_script(SCRIPT, {"size": 20.0})
    assert schema["size"]["value"] == 20.0
    assert values == {"size": 20.0, "holes": 2}
    assert build(values) == ("built", 20.0, 2)


def test_syntax_error_becomes_a_script_error_with_traceback():
    with pytest.raises(ScriptError) as excinfo:
        params.exec_script("def build(p)\n    return None\n")
    assert "syntax error" in str(excinfo.value)
    assert excinfo.value.traceback_text


def test_script_that_raises_on_import_is_reported():
    with pytest.raises(ScriptError) as excinfo:
        params.exec_script("raise RuntimeError('boom')\n")
    assert "boom" in str(excinfo.value)
    assert "RuntimeError" in excinfo.value.traceback_text


def test_missing_params_block_is_reported():
    with pytest.raises(ParamError, match="no PARAMS block"):
        params.load_script("def build(p):\n    return None\n")


def test_missing_build_function_is_reported():
    with pytest.raises(ParamError, match="no build"):
        params.load_script('PARAMS = {"a": {"value": 1.0, "unit": "mm"}}\n')


def test_empty_script_is_reported():
    with pytest.raises(ParamError, match="empty"):
        params.exec_script("   \n")
