"""Pure logic: path resolution, object-name derivation, formatting."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forge_mcp.errors import ForgeError
from forge_mcp.util import (
    ensure_parent_dir,
    fmt_number,
    fmt_overrides,
    fmt_params,
    fmt_scene_info,
    fmt_stats,
    fmt_vector,
    object_name_for_script,
    ok,
    read_script,
    resolve_path,
)

# --- resolve_path -----------------------------------------------------------


def test_resolve_path_is_absolute_and_normalized(tmp_path: Path) -> None:
    resolved = resolve_path(str(tmp_path / "sub" / ".." / "out.stl"))
    assert resolved.is_absolute()
    assert ".." not in resolved.parts
    assert resolved.name == "out.stl"


def test_resolve_path_handles_spaces(tmp_path: Path) -> None:
    target = tmp_path / "My Parts" / "bowl holder.stl"
    resolved = resolve_path(str(target))
    assert resolved.name == "bowl holder.stl"
    assert resolved.parent.name == "My Parts"


def test_resolve_path_strips_surrounding_quotes(tmp_path: Path) -> None:
    target = tmp_path / "quoted part.stl"
    assert resolve_path(f'"{target}"') == resolve_path(str(target))


def test_resolve_path_expands_tilde() -> None:
    resolved = resolve_path("~/forge_test_output.stl")
    assert "~" not in str(resolved)
    assert resolved.is_absolute()
    assert resolved.parent == Path(os.path.expanduser("~")).resolve()


def test_resolve_path_expands_env_vars(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGE_TEST_DIR", str(tmp_path))
    resolved = resolve_path("%FORGE_TEST_DIR%/part.stl")
    assert "%" not in str(resolved)
    assert resolved.parent == tmp_path.resolve()


def test_resolve_path_forward_and_back_slashes_agree(tmp_path: Path) -> None:
    """Windows accepts both separators; they must resolve to the same path."""
    base = str(tmp_path)
    forward = resolve_path(base.replace("\\", "/") + "/nested/part.stl")
    backward = resolve_path(base + "\\nested\\part.stl")
    if os.name == "nt":
        assert forward == backward
    assert forward.name == "part.stl"


def test_resolve_path_relative_becomes_absolute() -> None:
    assert resolve_path("part.stl").is_absolute()


@pytest.mark.parametrize("raw", ["", "   ", '""'])
def test_resolve_path_rejects_empty(raw: str) -> None:
    with pytest.raises(ForgeError) as exc:
        resolve_path(raw, label="export path")
    assert "export path" in str(exc.value)


def test_resolve_path_must_exist_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="No file at"):
        resolve_path(str(tmp_path / "nope.py"), must_exist=True)


def test_resolve_path_must_exist_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="is a directory, not a file"):
        resolve_path(str(tmp_path), must_exist=True)


# --- read_script ------------------------------------------------------------


def test_read_script_returns_path_and_source(tmp_path: Path) -> None:
    script = tmp_path / "part.py"
    script.write_text("PARAMS = {}\n", encoding="utf-8")
    path, source = read_script(str(script))
    assert path == script.resolve()
    assert source == "PARAMS = {}\n"


def test_read_script_rejects_empty_file(tmp_path: Path) -> None:
    script = tmp_path / "empty.py"
    script.write_text("   \n", encoding="utf-8")
    with pytest.raises(ForgeError, match="is empty"):
        read_script(str(script))


# --- object_name_for_script -------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("projects/bowl_holder/part.py", "bowl_holder"),
        ("projects/bowl_holder/main.py", "bowl_holder"),
        ("projects/bowl_holder/model.py", "bowl_holder"),
        ("projects/bowl_holder/script.py", "bowl_holder"),
        ("projects/bowl_holder/build.py", "bowl_holder"),
        ("projects/bowl_holder/generate.py", "bowl_holder"),
        ("projects/bowl_holder/__init__.py", "bowl_holder"),
        # A meaningful filename wins over the folder.
        ("projects/bowl_holder/vase.py", "vase"),
        ("projects/anything/Bracket.py", "Bracket"),
    ],
)
def test_object_name_generic_stem_falls_back_to_folder(
    tmp_path: Path, relative: str, expected: str
) -> None:
    assert object_name_for_script(tmp_path / relative) == expected


def test_object_name_generic_stem_check_is_case_insensitive(tmp_path: Path) -> None:
    assert object_name_for_script(tmp_path / "widget" / "PART.py") == "widget"


def test_object_name_truncated_to_blender_limit(tmp_path: Path) -> None:
    name = object_name_for_script(tmp_path / ("x" * 200 + ".py"))
    assert len(name.encode("utf-8")) <= 63


def test_object_name_truncation_never_splits_a_character(tmp_path: Path) -> None:
    """Cutting mid-codepoint must drop the character, not emit mojibake."""
    name = object_name_for_script(tmp_path / ("é" * 100 + ".py"))
    assert len(name.encode("utf-8")) <= 63
    assert set(name) == {"é"}


# --- formatting -------------------------------------------------------------


def test_fmt_number_variants() -> None:
    assert fmt_number(True) == "yes"
    assert fmt_number(False) == "no"
    assert fmt_number(7) == "7"
    assert fmt_number(1.5) == "1.5"
    assert fmt_number(2.0) == "2"
    assert fmt_number(0.0001, 6) == "0.0001"


def test_fmt_vector() -> None:
    assert fmt_vector([1.0, 2.5, 0.0]) == "(1, 2.5, 0)"
    assert fmt_vector(None) == "None"


def test_fmt_scene_info_empty() -> None:
    assert fmt_scene_info({"objects": [], "active": None}) == "Scene is empty (no objects)."


def test_fmt_scene_info_marks_active_object() -> None:
    text = fmt_scene_info(
        {
            "objects": [
                {
                    "name": "Cube",
                    "type": "MESH",
                    "location": [0, 0, 0],
                    "dimensions": [2, 2, 2],
                    "vertex_count": 8,
                    "modifiers": ["Mirror"],
                }
            ],
            "active": "Cube",
        }
    )
    assert "1 object(s); active: Cube" in text
    assert "*Cube" in text
    assert "Mirror" in text


def test_fmt_params_renders_range_step_and_description() -> None:
    text = fmt_params(
        {
            "bowl_diameter": {
                "value": 152.4,
                "unit": "mm",
                "min": 50.0,
                "max": 300.0,
                "step": 1.0,
                "description": "Outer diameter",
            }
        }
    )
    assert "bowl_diameter = 152.4 mm" in text
    assert "[50..300]" in text
    assert "step 1" in text
    assert "Outer diameter" in text


def test_fmt_params_empty() -> None:
    assert fmt_params({}) == "(no parameters declared)"


def test_fmt_stats() -> None:
    text = fmt_stats(
        {
            "vertex_count": 120,
            "face_count": 236,
            "bounding_box_mm": [10.0, 20.0, 5.0],
            "watertight": False,
        }
    )
    assert "vertices 120" in text
    assert "faces 236" in text
    assert "mm" in text
    assert "NOT WATERTIGHT" in text


def test_fmt_overrides() -> None:
    assert fmt_overrides(None) == "none"
    assert fmt_overrides({}) == "none"
    assert fmt_overrides({"wall": 3.0, "feet": 4}) == "wall=3, feet=4"


def test_ok() -> None:
    assert ok("done") == "OK — done"
    assert ok("done", "detail") == "OK — done (detail)"


# --- ensure_parent_dir ------------------------------------------------------


def test_ensure_parent_dir_creates_missing_folders(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "part.stl"
    ensure_parent_dir(target)
    assert target.parent.is_dir()


def test_ensure_parent_dir_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "a" / "part.stl"
    ensure_parent_dir(target)
    ensure_parent_dir(target)
    assert target.parent.is_dir()
