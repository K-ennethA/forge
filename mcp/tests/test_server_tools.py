"""The tool surface, driven through the SDK's in-memory client.

`Client(server)` is the mcp SDK's own testing pattern: it wires a real
ClientSession to the server over in-memory streams, so these exercise the actual
initialize / tools/list / tools/call path without a subprocess.

Both backends are pointed at closed ports, so every call here takes the
"backend is down" path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client

from forge_mcp import server, util

#: The tool surface fixed by mcp/README.md and docs/architecture.md.
EXPECTED_TOOLS = {
    # health
    "forge_status",
    "blender_ping",
    # scene
    "get_scene_info",
    "execute_blender_python",
    # common mesh operations
    "symmetrize",
    "mirror",
    "remesh",
    "decimate",
    "shade",
    "apply_transforms",
    "set_origin",
    "boolean",
    "merge_by_distance",
    "separate_loose",
    # object management
    "select_object",
    "rename_object",
    "delete_object",
    "export_stl",
    # Reference images (Phase 6c)
    "load_reference",
    # PartForge
    "partforge_parse_params",
    "partforge_generate",
    "partforge_export",
    # PartForge authoring (Phase 7 — the assistant makes new parts)
    "partforge_new_part",
    "partforge_open_in_panel",
    # PartForge print readiness (Phase 2)
    "partforge_check",
    "partforge_segment",
    "partforge_load_segments",
    "partforge_export_segments",
    # Imported models (Phase 6d) — a downloaded mesh, no script
    "check_model",
    "segment_model",
    # meshgen (Phase 7) — a picture becomes a mesh
    "generate_3d",
    "meshgen_status",
    # RigForge (Phase 3)
    "rigforge_list_tags",
    "rigforge_tag",
    "rigforge_untag",
    "rigforge_manifest",
    "rigforge_retopo",
    "rigforge_auto_uv",
    "rigforge_status",
    # RigForge rig + Godot export (Phase 4)
    "rigforge_metarig",
    "rigforge_generate_rig",
    "rigforge_weights",
    "rigforge_export_godot",
    # RigForge cloth + animation (Phase 5)
    "rigforge_cloth",
    "rigforge_action",
    "rigforge_keyframe",
    "rigforge_retarget",
    # Flows (Phase 6b) — saved sequences that replay with no model in the loop
    "flow_list",
    "flow_run",
    "flow_save",
}


def call(name: str, arguments: dict[str, Any] | None = None):
    """One tools/call over an in-memory session; returns the CallToolResult."""

    async def run():
        async with Client(server.app) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def list_tools():
    async def run():
        async with Client(server.app) as client:
            return (await client.list_tools()).tools

    return asyncio.run(run())


def text_of(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


# --- handshake and catalog --------------------------------------------------


def test_initialize_reports_the_server_identity() -> None:
    async def run():
        async with Client(server.app) as client:
            return client.server_info, client.instructions

    info, instructions = asyncio.run(run())
    assert info.name == "forge"
    assert info.version
    assert instructions and "PartForge" in instructions


def test_exactly_the_contract_tools_are_exposed() -> None:
    names = {tool.name for tool in list_tools()}
    assert names == EXPECTED_TOOLS
    assert len(names) == 50


def test_every_tool_is_documented() -> None:
    for tool in list_tools():
        assert tool.description and tool.description.strip(), tool.name


def test_every_tool_has_an_object_schema() -> None:
    for tool in list_tools():
        assert tool.input_schema.get("type") == "object", tool.name


@pytest.mark.parametrize(
    ("tool_name", "required"),
    [
        ("forge_status", []),
        ("blender_ping", []),
        ("get_scene_info", []),
        ("execute_blender_python", ["code"]),
        ("symmetrize", []),
        ("decimate", ["ratio"]),
        ("boolean", ["operand"]),
        ("select_object", ["name"]),
        ("rename_object", ["name", "new_name"]),
        ("delete_object", ["name"]),
        ("export_stl", ["path"]),
        ("load_reference", ["path"]),
        ("partforge_parse_params", ["script_path"]),
        ("partforge_generate", ["script_path"]),
        ("partforge_export", ["script_path", "output_path"]),
        ("partforge_new_part", ["name", "script_source"]),
        ("partforge_open_in_panel", ["script_path"]),
        ("partforge_check", ["script_path"]),
        ("partforge_segment", ["script_path"]),
        ("partforge_load_segments", ["script_path"]),
        ("partforge_export_segments", ["script_path", "directory"]),
        # Phase 6d: the mesh is already in Blender, so nothing is mandatory
        ("check_model", []),
        ("segment_model", []),
        # RigForge: only the tag name is ever mandatory
        ("rigforge_list_tags", []),
        ("rigforge_tag", ["tag"]),
        ("rigforge_untag", ["tag"]),
        ("rigforge_manifest", []),
        ("rigforge_retopo", []),
        ("rigforge_auto_uv", []),
        ("rigforge_status", []),
        # Phase 4: only the export needs somewhere to write
        ("rigforge_metarig", []),
        ("rigforge_generate_rig", []),
        ("rigforge_weights", []),
        ("rigforge_export_godot", ["path"]),
        # Phase 5: the keys to set and the clip to read are the only musts
        ("rigforge_cloth", []),
        ("rigforge_action", []),
        ("rigforge_keyframe", ["keys"]),
        ("rigforge_retarget", ["source_path"]),
    ],
)
def test_required_parameters_match_the_contract(tool_name: str, required: list[str]) -> None:
    tool = next(t for t in list_tools() if t.name == tool_name)
    assert sorted(tool.input_schema.get("required", [])) == sorted(required)


@pytest.mark.parametrize(
    ("tool_name", "param", "values"),
    [
        ("symmetrize", "direction", ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]),
        ("mirror", "axis", ["X", "Y", "Z"]),
        ("remesh", "mode", ["voxel", "quad"]),
        ("shade", "mode", ["smooth", "flat", "auto"]),
        ("set_origin", "type", ["geometry", "bottom", "cursor"]),
        ("boolean", "operation", ["UNION", "DIFFERENCE", "INTERSECT"]),
        ("load_reference", "view", ["front", "side", "top"]),
        ("partforge_export", "format", ["stl", "step", "3mf"]),
        ("partforge_export_segments", "format", ["stl", "step", "3mf"]),
        ("partforge_segment", "joint_type", ["dovetail", "pin", "magnet", "none"]),
        ("partforge_load_segments", "joint_type", ["dovetail", "pin", "magnet", "none"]),
        (
            "partforge_export_segments",
            "joint_type",
            ["dovetail", "pin", "magnet", "none"],
        ),
        ("segment_model", "joint_type", ["dovetail", "pin", "magnet", "none"]),
        ("rigforge_manifest", "action", ["save", "load", "get"]),
        ("rigforge_retopo", "platform", ["desktop", "mobile"]),
        ("rigforge_metarig", "archetype", ["auto", "biped", "quadruped", "custom"]),
        ("rigforge_weights", "action", ["report", "cleanup", "normalize"]),
        ("rigforge_cloth", "preset", ["cotton", "leather", "heavy"]),
        ("rigforge_cloth", "output", ["skin_tight", "shapekeys", "bones"]),
        (
            "rigforge_action",
            "action",
            ["new", "list", "delete", "duplicate", "rename", "push_nla"],
        ),
        ("rigforge_keyframe", "interpolation", ["BEZIER", "LINEAR"]),
    ],
)
def test_enum_parameters_match_the_contract(
    tool_name: str, param: str, values: list[str]
) -> None:
    tool = next(t for t in list_tools() if t.name == tool_name)
    assert tool.input_schema["properties"][param]["enum"] == values


@pytest.mark.parametrize(
    "tool_name",
    [
        "partforge_segment",
        "partforge_load_segments",
        "partforge_export_segments",
        "segment_model",
    ],
)
def test_mode_accepts_an_object_an_int_and_a_list(tool_name: str) -> None:
    """`mode` must swallow partforge_check's suggestion object unchanged."""
    tool = next(t for t in list_tools() if t.name == tool_name)
    schema = tool.input_schema["properties"]["mode"]
    kinds = {branch.get("type") for branch in schema["anyOf"]}
    assert {"object", "integer", "array", "string"} <= kinds
    assert schema["default"] == "auto"


@pytest.mark.parametrize(
    "tool_name",
    [
        "symmetrize",
        "mirror",
        "remesh",
        "decimate",
        "shade",
        "apply_transforms",
        "set_origin",
        "boolean",
        "merge_by_distance",
        "separate_loose",
        "check_model",
        "segment_model",
        "rigforge_list_tags",
        "rigforge_tag",
        "rigforge_untag",
        "rigforge_manifest",
        "rigforge_retopo",
        "rigforge_auto_uv",
        "rigforge_status",
        "rigforge_metarig",
        "rigforge_weights",
        "rigforge_cloth",
    ],
)
def test_object_targeting_tools_take_an_optional_object(tool_name: str) -> None:
    """Omitted `object` means Blender's active object, so it is never required."""
    tool = next(t for t in list_tools() if t.name == tool_name)
    assert "object" in tool.input_schema["properties"]
    assert "object" not in tool.input_schema.get("required", [])


@pytest.mark.parametrize("tool_name", ["rigforge_tag", "rigforge_untag"])
def test_faces_and_use_selection_are_both_optional_in_the_schema(tool_name: str) -> None:
    """The either/or is enforced in the tool, not the schema — see test_rigforge."""
    schema = next(t for t in list_tools() if t.name == tool_name).input_schema
    required = schema.get("required", [])
    assert "faces" not in required and "use_selection" not in required
    branches = schema["properties"]["faces"].get("anyOf") or [schema["properties"]["faces"]]
    array = next(b for b in branches if b.get("type") == "array")
    assert array["items"]["type"] == "integer"
    assert schema["properties"]["use_selection"]["type"] == "boolean"


def test_retopo_defaults_match_the_desktop_preset() -> None:
    """target_faces has no default of its own: the platform preset decides."""
    schema = next(t for t in list_tools() if t.name == "rigforge_retopo").input_schema
    properties = schema["properties"]
    assert properties["platform"]["default"] == "desktop"
    assert properties["target_faces"].get("default") is None
    assert properties["lods"]["default"] == 0
    assert properties["bake_normals"]["default"] is False
    assert properties["bake_resolution"]["default"] == 2048
    assert properties["keep_original"]["default"] is True


def test_export_godot_defaults_are_the_shipping_ones() -> None:
    """deform_only and the import helper are on unless the caller says otherwise."""
    schema = next(t for t in list_tools() if t.name == "rigforge_export_godot").input_schema
    properties = schema["properties"]
    assert properties["actions"]["default"] == "all"
    assert properties["root_motion"]["default"] is False
    assert properties["deform_only"]["default"] is True
    assert properties["godot_import_script"]["default"] is True
    assert "rig" not in schema.get("required", [])


def test_export_godot_actions_accepts_a_string_or_a_list() -> None:
    """"all" and ["idle-loop", ...] are the contract's two forms."""
    schema = next(t for t in list_tools() if t.name == "rigforge_export_godot").input_schema
    actions = schema["properties"]["actions"]
    kinds = {branch.get("type") for branch in actions["anyOf"]}
    assert {"string", "array"} <= kinds


# --- Phase 5 schema ---------------------------------------------------------


def test_cloth_defaults_are_the_cheap_deterministic_ones() -> None:
    """skin_tight and body collision: no sim to babysit unless it is asked for."""
    schema = next(t for t in list_tools() if t.name == "rigforge_cloth").input_schema
    properties = schema["properties"]
    assert properties["output"]["default"] == "skin_tight"
    assert properties["preset"]["default"] == "cotton"
    assert properties["collision"]["default"] is True
    assert properties["use_selection"]["default"] is False
    for optional in ("offset_mm", "thickness_mm", "frames", "name"):
        assert properties[optional].get("default") is None, optional
    assert schema.get("required", []) == []


def test_cloth_tags_and_use_selection_are_both_optional_in_the_schema() -> None:
    """The either/or lives in the tool, exactly as it does for rigforge_tag."""
    schema = next(t for t in list_tools() if t.name == "rigforge_cloth").input_schema
    required = schema.get("required", [])
    assert "tags" not in required and "use_selection" not in required
    branches = schema["properties"]["tags"].get("anyOf") or [schema["properties"]["tags"]]
    array = next(b for b in branches if b.get("type") == "array")
    assert array["items"]["type"] == "string"


def test_keyframe_keys_are_a_list_of_objects() -> None:
    schema = next(t for t in list_tools() if t.name == "rigforge_keyframe").input_schema
    keys = schema["properties"]["keys"]
    assert keys["type"] == "array"
    assert keys["items"]["type"] == "object"
    assert schema["properties"]["clear"]["default"] is False


def test_retarget_mapping_and_scale_accept_both_contract_forms() -> None:
    """`mapping` is "auto" or {src: dst}; `scale` is "auto" or a number."""
    schema = next(t for t in list_tools() if t.name == "rigforge_retarget").input_schema
    mapping = schema["properties"]["mapping"]
    assert {branch.get("type") for branch in mapping["anyOf"]} >= {"string", "object"}
    assert mapping["default"] == "auto"
    scale = schema["properties"]["scale"]
    assert {branch.get("type") for branch in scale["anyOf"]} >= {"string", "number"}
    assert scale["default"] == "auto"


# --- Phase 6c schema: reference images ---------------------------------------


def test_load_reference_defaults_to_the_front_view_and_no_size() -> None:
    """`size_mm` and `name` are the add-on's defaults (200 mm, Ref-<view>)."""
    schema = next(t for t in list_tools() if t.name == "load_reference").input_schema
    properties = schema["properties"]
    assert properties["view"]["default"] == "front"
    assert properties["size_mm"].get("default") is None
    assert properties["name"].get("default") is None
    assert schema.get("required", []) == ["path"]


def test_load_reference_says_it_is_not_for_tracing() -> None:
    """The philosophy is in the tool's own description, not only the prompt."""
    tool = next(t for t in list_tools() if t.name == "load_reference")
    description = tool.description.lower()
    assert "never trace" in description or "not trace" in description
    assert "parameters" in description


def test_load_reference_checks_the_file_before_touching_blender(
    dead_backends, tmp_path: Path
) -> None:
    missing = tmp_path / "sketch.png"
    text = text_of(call("load_reference", {"path": str(missing)}))
    assert "No file at" in text
    assert "Blender is not running" not in text  # the path failed first


def test_load_reference_rejects_a_file_that_is_not_an_image(
    dead_backends, tmp_path: Path
) -> None:
    part = tmp_path / "part.stl"
    part.write_bytes(b"solid\n")
    text = text_of(call("load_reference", {"path": str(part)}))
    assert "not an image" in text
    assert ".webp" in text  # it says what IS accepted


def test_load_reference_rejects_a_folder(dead_backends, tmp_path: Path) -> None:
    text = text_of(call("load_reference", {"path": str(tmp_path)}))
    assert "folder" in text


@pytest.mark.parametrize("size", [0, -50.0])
def test_load_reference_rejects_a_size_that_is_not_a_size(
    dead_backends, tmp_path: Path, size: float
) -> None:
    image = tmp_path / "sketch.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = text_of(call("load_reference", {"path": str(image), "size_mm": size}))
    assert "positive number of millimetres" in text


def test_load_reference_reaches_blender_with_the_resolved_path(
    dead_backends, tmp_path: Path
) -> None:
    """A valid image gets as far as the (absent) add-on, not a path complaint."""
    image = tmp_path / "sketch.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = text_of(call("load_reference", {"path": str(image), "view": "side"}))
    assert "Blender is not running" in text


def test_load_reference_report_names_the_object_and_what_to_do_with_it() -> None:
    report = util.fmt_reference_report(
        Path(r"C:\refs\bowl sketch.png"),
        "front",
        {"object": "Ref-front", "width_mm": 200.0, "height_mm": 125.0},
    )
    assert "bowl sketch.png" in report
    assert "Ref-front" in report
    assert "200 x 125 mm" in report
    assert "Numpad 1" in report
    assert "move, scale or hide" in report
    assert "never end up in an export" in report
    assert "not something to trace" in report


def test_load_reference_report_says_when_it_replaced_one() -> None:
    report = util.fmt_reference_report(
        Path("sketch.png"), "top",
        {"object": "Ref-top", "width_mm": 200.0, "height_mm": 200.0,
         "replaced": True},
    )
    assert "replaced" in report
    assert "Numpad 7" in report


def test_a_reference_can_be_a_flow_step() -> None:
    """`load_reference` is a real socket command, so flow_save must accept it."""
    assert "load_reference" in util.KNOWN_BLENDER_OPS


# --- error paths with no backends running -----------------------------------


def test_forge_status_never_fails_and_reports_every_backend(dead_backends) -> None:
    blender_port, service_port, meshgen_port = dead_backends
    result = call("forge_status")
    text = text_of(result)

    assert result.is_error is False, text
    assert text.count("[DOWN]") == 3
    assert f"127.0.0.1:{blender_port}" in text
    assert f"127.0.0.1:{service_port}" in text
    assert f"127.0.0.1:{meshgen_port}" in text
    assert "Blender is not running" in text
    assert "geometry service is not running" in text
    # meshgen down is a fact, not a fault: the line says so rather than sending
    # the model off to fix an optional service.
    assert "Picture to 3D    [DOWN]" in text
    assert "optional" in text
    assert "Traceback" not in text


@pytest.mark.parametrize(
    "tool_name",
    [
        "blender_ping",
        "get_scene_info",
        "separate_loose",
        "check_model",
        "segment_model",
        "rigforge_list_tags",
        "rigforge_status",
    ],
)
def test_blender_tools_report_the_addon_is_down(dead_backends, tool_name: str) -> None:
    result = call(tool_name)
    text = text_of(result)

    assert result.is_error is True
    assert "Blender is not running or the Forge add-on server is stopped" in text
    assert "Start Server" in text  # tells the user how to fix it
    assert f"127.0.0.1:{dead_backends[0]}" in text
    assert "Traceback" not in text
    assert text.strip() != f"Error executing tool {tool_name}"


def test_service_tool_reports_the_service_is_down(dead_backends, tmp_path: Path) -> None:
    script = tmp_path / "part.py"
    script.write_text("PARAMS = {}\n\ndef build(p):\n    return None\n", encoding="utf-8")

    result = call("partforge_parse_params", {"script_path": str(script)})
    text = text_of(result)

    assert result.is_error is True
    assert "The Forge geometry service is not running" in text
    assert "service/README.md" in text  # tells the user how to fix it
    assert "Traceback" not in text


def test_partforge_generate_reports_the_service_first(dead_backends, tmp_path: Path) -> None:
    """With both backends down, the build must fail before the Blender load."""
    script = tmp_path / "widget" / "part.py"
    script.parent.mkdir()
    script.write_text("PARAMS = {}\n", encoding="utf-8")

    text = text_of(call("partforge_generate", {"script_path": str(script)}))
    assert "geometry service is not running" in text
    assert "Traceback" not in text


# --- argument validation (no backend involved) ------------------------------


@pytest.mark.parametrize("ratio", [0.0, -1.0, 1.5])
def test_decimate_rejects_a_ratio_outside_the_unit_interval(ratio: float) -> None:
    result = call("decimate", {"ratio": ratio})
    assert result.is_error is True
    assert "ratio must be in (0, 1]" in text_of(result)


def test_apply_transforms_rejects_an_empty_request() -> None:
    result = call(
        "apply_transforms", {"location": False, "rotation": False, "scale": False}
    )
    assert result.is_error is True
    assert "Nothing to apply" in text_of(result)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "fragment"),
    [
        ("select_object", {"name": "  "}, "requires an object name"),
        ("delete_object", {"name": ""}, "requires an object name"),
        ("rename_object", {"name": "a", "new_name": " "}, "requires both"),
        ("boolean", {"operand": " "}, "requires the name of the operand"),
    ],
)
def test_blank_names_are_rejected_with_a_readable_message(
    tool_name: str, arguments: dict[str, Any], fragment: str
) -> None:
    result = call(tool_name, arguments)
    assert result.is_error is True
    assert fragment in text_of(result)


def test_missing_script_is_reported_as_a_path_problem(dead_backends, tmp_path: Path) -> None:
    result = call("partforge_parse_params", {"script_path": str(tmp_path / "gone.py")})
    text = text_of(result)
    assert result.is_error is True
    assert "No file at" in text
    assert "geometry service" not in text  # the path failed first


def test_schema_violation_is_rejected_before_the_tool_runs() -> None:
    result = call("decimate", {"ratio": "not a number"})
    assert result.is_error is True
