"""The Phase 3 (RigForge) tools: request shaping and report formatting.

Blender is the NDJSON fake from ``test_blender_client`` on an ephemeral port —
never 9876 — so these run with Blender closed and against the *contract* in
docs/architecture.md rather than a live add-on. Two things are pinned here:

* what goes on the wire (command name, and every parameter the contract lists),
* what the report says when a canned, contract-shaped result comes back.

Both matter for a backend being written in parallel: a change to either is a
change to the contract and should break a test here first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from forge_mcp import config, server
from forge_mcp.errors import ForgeError

from .test_blender_client import FakeBlender

# --- fakes ------------------------------------------------------------------


def router(results: dict[str, Any]) -> Callable[[dict[str, Any], Any], None]:
    """Answer each command with its canned `result`, keyed by command type."""

    def respond(request: dict[str, Any], conn: Any) -> None:
        command = request.get("type")
        payload = results.get(command)
        if payload is None:
            body = {
                "id": request.get("id"),
                "status": "error",
                "result": None,
                "message": f"unexpected command '{command}'",
            }
        else:
            if callable(payload):
                payload = payload(request)
            body = {
                "id": request.get("id"),
                "status": "success",
                "result": payload,
                "message": "",
            }
        conn.sendall(json.dumps(body).encode("utf-8") + b"\n")

    return respond


@pytest.fixture
def blender(monkeypatch):
    """Factory: start a fake add-on for these canned results and aim the client."""
    started: list[FakeBlender] = []

    def make(results: dict[str, Any], connections: int = 1) -> FakeBlender:
        fake = FakeBlender(router(results), connections=connections)
        fake.__enter__()
        started.append(fake)
        monkeypatch.setattr(config, "BLENDER_HOST", "127.0.0.1")
        monkeypatch.setattr(config, "BLENDER_PORT", fake.port)
        monkeypatch.setattr(config, "BLENDER_CONNECT_TIMEOUT", 2.0)
        monkeypatch.setattr(config, "BLENDER_READ_TIMEOUT", 10.0)
        return fake

    yield make
    for fake in started:
        fake.__exit__()


TAGS = [
    {"name": "Head", "vertex_count": 1204, "face_count": 2380},
    {"name": "Torso", "vertex_count": 3010, "face_count": 5980},
    {"name": "Ear.L", "vertex_count": 96, "face_count": 180},
]

MANIFEST = {
    "name": "goblin",
    "archetype": "biped",
    "custom_modules": [],
    "tags": ["Head", "Torso", "Ear.L"],
    "motion_notes": "ears are floppy and lag behind the head",
    "retopo": {"target_faces_desktop": 15000, "target_faces_mobile": 5000, "lods": 2},
    "actions": ["idle-loop", "walk-loop"],
    "godot": {"targets": ["desktop"], "root_motion": False, "y_up": True, "unit_scale": 1.0},
}

SCENE = {
    "objects": [
        {
            "name": "goblin",
            "type": "MESH",
            "location": [0.0, 0.0, 0.0],
            "dimensions": [0.6, 0.4, 1.7],
            "vertex_count": 240100,
            "face_count": 480000,
            "modifiers": [],
        },
        {
            "name": "goblin_retopo",
            "type": "MESH",
            "location": [0.0, 0.0, 0.0],
            "dimensions": [0.6, 0.4, 1.7],
            "vertex_count": 7600,
            "face_count": 15000,
            "modifiers": [],
        },
        {
            "name": "goblin_lod1",
            "type": "MESH",
            "location": [0.0, 0.0, 0.0],
            "dimensions": [0.6, 0.4, 1.7],
            "vertex_count": 3800,
            "face_count": 7500,
            "modifiers": [],
        },
        {
            "name": "goblin_hammer",  # a prop, not a derivative — must NOT be listed
            "type": "MESH",
            "location": [0.3, 0.0, 1.0],
            "dimensions": [0.1, 0.1, 0.5],
            "vertex_count": 400,
            "face_count": 780,
            "modifiers": [],
        },
    ],
    "active": "goblin",
}

RETOPO_RESULT = {
    "objects": ["goblin_retopo", "goblin_lod1", "goblin_lod2"],
    "face_counts": [15000, 7500, 3750],
}


def sent(fake: FakeBlender, command: str) -> dict[str, Any]:
    """The params of the first request of that type."""
    for request in fake.requests:
        if request.get("type") == command:
            return request["params"]
    raise AssertionError(
        f"{command} was never sent; saw {[r.get('type') for r in fake.requests]}"
    )


# --- rigforge_list_tags -----------------------------------------------------


def test_list_tags_sends_the_contract_command_and_the_object(blender) -> None:
    fake = blender({"rigforge_list_tags": {"tags": TAGS}})
    server.rigforge_list_tags("goblin")

    assert fake.requests[0]["type"] == "rigforge_list_tags"
    assert sent(fake, "rigforge_list_tags") == {"object": "goblin"}


def test_an_omitted_object_means_the_active_one(blender) -> None:
    """No `object` key at all — the add-on falls back to the active object."""
    fake = blender({"rigforge_list_tags": {"tags": TAGS}})
    report = server.rigforge_list_tags()

    assert sent(fake, "rigforge_list_tags") == {}
    assert "the active object" in report


def test_list_tags_report_is_a_table_with_counts_and_a_total(blender) -> None:
    blender({"rigforge_list_tags": {"tags": TAGS}})
    report = server.rigforge_list_tags("goblin")

    assert "3 tag(s) on 'goblin'" in report
    for name in ("Head", "Torso", "Ear.L"):
        assert name in report
    assert "2380" in report and "1204" in report
    assert "total" in report
    assert "4310" in report and "8540" in report  # summed verts / faces


def test_no_tags_says_so_and_points_at_the_next_step(blender) -> None:
    blender({"rigforge_list_tags": {"tags": []}})
    report = server.rigforge_list_tags("goblin")

    assert "No tags on 'goblin'" in report
    assert "rigforge_tag" in report


# --- rigforge_tag -----------------------------------------------------------


def test_tag_with_explicit_faces_sends_sorted_unique_indices(blender) -> None:
    fake = blender({"rigforge_tag": {"tag": "Ear.L", "vertex_count": 96}})
    server.rigforge_tag("Ear.L", faces=[14, 12, 13, 12], object="goblin")

    params = sent(fake, "rigforge_tag")
    assert params["object"] == "goblin"
    assert params["tag"] == "Ear.L"
    assert params["faces"] == [12, 13, 14]
    assert params["replace"] is False
    assert "use_selection" not in params, "the two are alternatives on the wire"


def test_tag_from_the_selection_sends_no_face_list(blender) -> None:
    fake = blender({"rigforge_tag": {"tag": "Ear.L", "vertex_count": 96}})
    report = server.rigforge_tag("Ear.L", use_selection=True, replace=True)

    params = sent(fake, "rigforge_tag")
    assert params == {"tag": "Ear.L", "use_selection": True, "replace": True}
    assert "the current selection" in report
    assert "96 vertices" in report and "replaced" in report


def test_the_tag_prefix_is_the_addons_and_is_stripped_here(blender) -> None:
    """A model that read the contract may say "tag_Head"; both forms must work."""
    fake = blender({"rigforge_tag": {"tag": "Head", "vertex_count": 1204}})
    server.rigforge_tag("tag_Head", use_selection=True)
    assert sent(fake, "rigforge_tag")["tag"] == "Head"


def test_faces_and_use_selection_together_is_an_explicit_error() -> None:
    with pytest.raises(ForgeError, match="not both"):
        server.rigforge_tag("Head", faces=[1, 2], use_selection=True)


def test_neither_faces_nor_selection_is_an_explicit_error_that_teaches_the_flow() -> None:
    with pytest.raises(ForgeError) as exc:
        server.rigforge_tag("Head")
    message = str(exc.value)
    assert "No faces given" in message
    assert "execute_blender_python" in message, "the positional-request workflow"
    assert "use_selection" in message


@pytest.mark.parametrize(
    ("faces", "fragment"),
    [
        ([], "empty"),
        ([-1], "cannot be negative"),
        ([1.5], "whole numbers"),
        (["banana"], "not a face index"),
        (12, "must be a list"),
    ],
)
def test_bad_face_lists_are_rejected_before_the_socket(faces: Any, fragment: str) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_tag("Head", faces=faces)


@pytest.mark.parametrize("tag", ["", "   ", "tag_", "\n"])
def test_a_blank_tag_name_is_rejected(tag: str) -> None:
    with pytest.raises(ForgeError):
        server.rigforge_tag(tag, use_selection=True)


# --- rigforge_untag ---------------------------------------------------------


def test_untag_with_no_scope_removes_the_whole_tag(blender) -> None:
    fake = blender({"rigforge_untag": {}})
    report = server.rigforge_untag("Ear.L", object="goblin")

    params = sent(fake, "rigforge_untag")
    assert params == {"object": "goblin", "tag": "Ear.L"}
    assert "faces" not in params and "use_selection" not in params
    assert "tag 'Ear.L' removed" in report


def test_untag_with_faces_narrows_to_those_faces(blender) -> None:
    fake = blender({"rigforge_untag": {}})
    report = server.rigforge_untag("Ear.L", faces=[9, 8])

    assert sent(fake, "rigforge_untag") == {"tag": "Ear.L", "faces": [8, 9]}
    assert "2 face(s) removed from tag 'Ear.L'" in report


def test_untag_from_the_selection(blender) -> None:
    fake = blender({"rigforge_untag": {}})
    server.rigforge_untag("Ear.L", use_selection=True)
    assert sent(fake, "rigforge_untag") == {"tag": "Ear.L", "use_selection": True}


def test_untag_rejects_both_scopes_too() -> None:
    with pytest.raises(ForgeError, match="not both"):
        server.rigforge_untag("Ear.L", faces=[1], use_selection=True)


# --- rigforge_manifest ------------------------------------------------------


def test_manifest_get_sends_only_the_action(blender) -> None:
    fake = blender({"rigforge_manifest": {"manifest": MANIFEST}})
    report = server.rigforge_manifest("get", object="goblin")

    assert sent(fake, "rigforge_manifest") == {"object": "goblin", "action": "get"}
    assert "Manifest for 'goblin'" in report
    assert "in memory; not written to disk" in report


def test_manifest_save_resolves_the_path_and_creates_the_folder(
    blender, tmp_path: Path
) -> None:
    out = tmp_path / "characters" / "goblin"  # no extension, no folder
    fake = blender({"rigforge_manifest": {"manifest": MANIFEST, "path": str(out) + ".json"}})

    report = server.rigforge_manifest(
        "save",
        path=str(out),
        archetype="biped",
        motion_notes="hops rather than walks",
        object="goblin",
    )

    params = sent(fake, "rigforge_manifest")
    assert params["action"] == "save"
    assert params["path"] == str(out.with_suffix(".json")), "extension corrected to .json"
    assert params["archetype"] == "biped"
    assert params["motion_notes"] == "hops rather than walks"
    assert out.parent.is_dir(), "the folder is created before the add-on is asked"

    assert "Saved the 'goblin' manifest" in report
    assert str(out.with_suffix(".json")) in report


def test_manifest_load_reads_an_existing_file(blender, tmp_path: Path) -> None:
    path = tmp_path / "goblin.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    fake = blender({"rigforge_manifest": {"manifest": MANIFEST, "path": str(path)}})

    report = server.rigforge_manifest("load", path=str(path))

    assert sent(fake, "rigforge_manifest") == {"action": "load", "path": str(path)}
    assert "Loaded a manifest onto the active object" in report


def test_loading_a_missing_manifest_fails_before_the_socket(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="No manifest at"):
        server.rigforge_manifest("load", path=str(tmp_path / "gone.json"))


def test_a_path_with_get_is_an_error_not_a_silent_no_op(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="only meaningful for action"):
        server.rigforge_manifest("get", path=str(tmp_path / "goblin.json"))


def test_manifest_report_summarises_the_character(blender) -> None:
    blender({"rigforge_manifest": {"manifest": MANIFEST}})
    report = server.rigforge_manifest("get", object="goblin")

    assert "name: goblin" in report
    assert "archetype: biped" in report
    assert "tags (3): Head, Torso, Ear.L" in report
    assert "desktop 15000 / mobile 5000 faces, 2 LOD(s)" in report
    assert "actions (2): idle-loop, walk-loop" in report
    assert "ears are floppy" in report


# --- rigforge_retopo --------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "expected"), [("desktop", 15000), ("mobile", 5000)]
)
def test_the_platform_preset_sets_the_face_budget(
    blender, platform: str, expected: int
) -> None:
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    report = server.rigforge_retopo(platform=platform, object="goblin")

    params = sent(fake, "rigforge_retopo")
    assert params["target_faces"] == expected
    assert params["platform"] == platform
    assert f"{expected} face target ({platform} preset)" in report


def test_an_explicit_target_faces_overrides_the_preset(blender) -> None:
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    report = server.rigforge_retopo(platform="mobile", target_faces=8000)

    assert sent(fake, "rigforge_retopo")["target_faces"] == 8000
    assert "8000 face target (explicit target)" in report


def test_retopo_sends_every_contract_parameter(blender) -> None:
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    server.rigforge_retopo(
        platform="mobile", lods=2, bake_normals=True, bake_resolution=1024, object="goblin"
    )

    assert sent(fake, "rigforge_retopo") == {
        "object": "goblin",
        "target_faces": 5000,
        "platform": "mobile",
        "lods": 2,
        "bake_normals": True,
        "bake_resolution": 1024,
        "keep_original": True,
    }


def test_bake_resolution_is_omitted_when_nothing_is_baked(blender) -> None:
    """Same rule as remesh's voxel_size: a setting for a mode you are not in
    does not go on the wire."""
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    server.rigforge_retopo(bake_normals=False, bake_resolution=4096)

    params = sent(fake, "rigforge_retopo")
    assert params["bake_normals"] is False
    assert "bake_resolution" not in params


def test_keep_original_is_sent_and_can_be_turned_off(blender) -> None:
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    report = server.rigforge_retopo(keep_original=False)

    assert sent(fake, "rigforge_retopo")["keep_original"] is False
    assert "original removed" in report


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"target_faces": 10}, "at least 100"),
        ({"lods": -1}, "between 0 and 8"),
        ({"lods": 99}, "between 0 and 8"),
        ({"bake_normals": True, "bake_resolution": 16}, "between 64 and 8192"),
    ],
)
def test_out_of_range_retopo_settings_are_refused(kwargs: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_retopo(**kwargs)


def test_retopo_report_tables_the_objects_and_their_face_counts(blender) -> None:
    blender({"rigforge_retopo": RETOPO_RESULT})
    report = server.rigforge_retopo(lods=2, object="goblin")

    assert "Retopologised 'goblin'" in report
    assert "2 extra LOD(s)" in report
    for name, faces in (
        ("goblin_retopo", "15000"),
        ("goblin_lod1", "7500"),
        ("goblin_lod2", "3750"),
    ):
        line = next(l for l in report.splitlines() if name in l)
        assert faces in line


@pytest.mark.parametrize(
    "result",
    [
        {"objects": ["goblin_retopo"], "face_counts": [15000]},
        {"objects": ["goblin_retopo"], "face_counts": {"goblin_retopo": 15000}},
        {"objects": [{"name": "goblin_retopo", "face_count": 15000}]},
    ],
)
def test_face_counts_are_read_in_any_of_the_shapes_the_sketch_allows(
    blender, result: dict[str, Any]
) -> None:
    """The contract does not pin `face_counts`; all three readings are accepted."""
    blender({"rigforge_retopo": result})
    report = server.rigforge_retopo()
    assert "goblin_retopo" in report and "15000" in report


def test_a_baked_normal_map_is_reported(blender) -> None:
    blender(
        {
            "rigforge_retopo": {
                "objects": ["goblin_retopo"],
                "face_counts": [15000],
                "baked": {
                    "image": "goblin_normal",
                    "resolution": 2048,
                    "path": "C:/tmp/goblin_normal.png",
                },
            }
        }
    )
    report = server.rigforge_retopo(bake_normals=True)

    assert "normal map baked: goblin_normal" in report
    assert "2048 px" in report
    assert "goblin_normal.png" in report


# --- rigforge_auto_uv -------------------------------------------------------


def test_auto_uv_sends_the_contract_parameters(blender) -> None:
    fake = blender({"rigforge_auto_uv": {"islands": 12, "uv_coverage": 0.784}})
    server.rigforge_auto_uv(object="goblin_retopo")

    assert sent(fake, "rigforge_auto_uv") == {
        "object": "goblin_retopo",
        "seams_from_tags": True,
        "margin": 0.003,
        "angle_limit": 66.0,
    }


def test_auto_uv_report_gives_islands_and_coverage(blender) -> None:
    blender({"rigforge_auto_uv": {"islands": 12, "uv_coverage": 0.784}})
    report = server.rigforge_auto_uv(object="goblin_retopo")

    assert "12 island(s)" in report
    assert "78.4% UV coverage" in report
    assert "seams at tag boundaries" in report


@pytest.mark.parametrize(
    ("given", "shown"),
    [(0.784, "78.4%"), (78.4, "78.4%"), (1.0, "100.0%"), (100.0, "100.0%"), (0.0, "0.0%")],
)
def test_coverage_is_read_as_a_fraction_or_a_percentage(
    blender, given: float, shown: str
) -> None:
    """0.784 and 78.4 mean the same thing; 1.0 reads as 100% either way."""
    blender({"rigforge_auto_uv": {"islands": 12, "uv_coverage": given}})
    assert f"{shown} UV coverage" in server.rigforge_auto_uv()


def test_turning_off_tag_seams_says_so(blender) -> None:
    fake = blender({"rigforge_auto_uv": {"islands": 40, "uv_coverage": 0.6}})
    report = server.rigforge_auto_uv(seams_from_tags=False, angle_limit=45.0, margin=0.01)

    params = sent(fake, "rigforge_auto_uv")
    assert params["seams_from_tags"] is False
    assert params["angle_limit"] == 45.0
    assert params["margin"] == 0.01
    assert "no tag seams" in report


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"margin": -0.1}, "between 0 and 0.5"),
        ({"margin": 0.9}, "between 0 and 0.5"),
        ({"angle_limit": 0.0}, "between 0 and 90"),
        ({"angle_limit": 120.0}, "between 0 and 90"),
    ],
)
def test_out_of_range_uv_settings_are_refused(kwargs: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_auto_uv(**kwargs)


# --- rigforge_status --------------------------------------------------------


def test_status_asks_the_scene_then_the_tags(blender) -> None:
    fake = blender(
        {"get_scene_info": SCENE, "rigforge_list_tags": {"tags": TAGS}}, connections=2
    )
    report = server.rigforge_status("goblin")

    assert [r["type"] for r in fake.requests] == ["get_scene_info", "rigforge_list_tags"]
    assert sent(fake, "rigforge_list_tags") == {"object": "goblin"}
    assert report.splitlines()[0] == "RigForge status — goblin (active)"


def test_status_defaults_to_the_active_object(blender) -> None:
    fake = blender(
        {"get_scene_info": SCENE, "rigforge_list_tags": {"tags": TAGS}}, connections=2
    )
    server.rigforge_status()
    assert sent(fake, "rigforge_list_tags") == {"object": "goblin"}


def test_status_summarises_counts_tags_and_derivatives(blender) -> None:
    blender({"get_scene_info": SCENE, "rigforge_list_tags": {"tags": TAGS}}, connections=2)
    report = server.rigforge_status("goblin")

    assert "240100 verts, 480000 faces" in report
    assert "tags (3)" in report
    assert "Head" in report and "2380 faces" in report
    assert "retopo/LOD siblings (2)" in report
    assert "goblin_retopo" in report and "goblin_lod1" in report
    assert "goblin_hammer" not in report, "a prop is not a LOD"


def test_status_nudges_when_nothing_has_been_done_yet(blender) -> None:
    bare = {
        "objects": [
            {
                "name": "goblin",
                "type": "MESH",
                "location": [0, 0, 0],
                "dimensions": [0.6, 0.4, 1.7],
                "vertex_count": 240100,
                "modifiers": [],
            }
        ],
        "active": "goblin",
    }
    blender({"get_scene_info": bare, "rigforge_list_tags": {"tags": []}}, connections=2)
    report = server.rigforge_status()

    assert "face count not reported" in report, "get_scene_info need not carry faces"
    assert "tags: none yet" in report and "rigforge_tag" in report
    assert "siblings: none" in report and "rigforge_retopo" in report


def test_status_names_an_object_that_is_not_there(blender) -> None:
    blender({"get_scene_info": SCENE})
    with pytest.raises(ForgeError) as exc:
        server.rigforge_status("gobln")
    assert "No object named 'gobln'" in str(exc.value)
    assert "goblin" in str(exc.value), "the message lists what IS there"


def test_status_with_nothing_active_says_what_to_do(blender) -> None:
    blender({"get_scene_info": {"objects": [], "active": None}})
    with pytest.raises(ForgeError, match="nothing is active"):
        server.rigforge_status()


def test_status_still_reports_the_mesh_when_tags_are_unavailable(blender) -> None:
    """An add-on without the Phase 3 commands must not blank the whole report."""
    blender({"get_scene_info": SCENE}, connections=2)  # rigforge_list_tags -> error
    report = server.rigforge_status("goblin")

    assert "240100 verts" in report
    assert "tags: unavailable" in report
    assert "unexpected command 'rigforge_list_tags'" in report
    assert "retopo/LOD siblings (2)" in report
