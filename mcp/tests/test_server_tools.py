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

from forge_mcp import server

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
    # PartForge
    "partforge_parse_params",
    "partforge_generate",
    "partforge_export",
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
    assert len(names) == 21


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
        ("partforge_parse_params", ["script_path"]),
        ("partforge_generate", ["script_path"]),
        ("partforge_export", ["script_path", "output_path"]),
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
        ("partforge_export", "format", ["stl", "step", "3mf"]),
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
    ],
)
def test_object_targeting_tools_take_an_optional_object(tool_name: str) -> None:
    """Omitted `object` means Blender's active object, so it is never required."""
    tool = next(t for t in list_tools() if t.name == tool_name)
    assert "object" in tool.input_schema["properties"]
    assert "object" not in tool.input_schema.get("required", [])


# --- error paths with no backends running -----------------------------------


def test_forge_status_never_fails_and_reports_both_backends(dead_backends) -> None:
    blender_port, service_port = dead_backends
    result = call("forge_status")
    text = text_of(result)

    assert result.is_error is False, text
    assert text.count("[DOWN]") == 2
    assert f"127.0.0.1:{blender_port}" in text
    assert f"127.0.0.1:{service_port}" in text
    assert "Blender is not running" in text
    assert "geometry service is not running" in text
    assert "Traceback" not in text


@pytest.mark.parametrize("tool_name", ["blender_ping", "get_scene_info", "separate_loose"])
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
