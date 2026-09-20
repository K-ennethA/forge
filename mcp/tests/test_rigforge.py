"""The Phase 3/4 (RigForge) tools: request shaping and report formatting.

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
import re
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


def test_method_stays_off_the_wire_when_auto(blender) -> None:
    """Same rule as bake_resolution: the add-on's own default is not resent."""
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    server.rigforge_retopo(object="goblin")

    assert "method" not in sent(fake, "rigforge_retopo")


def test_the_deterministic_route_is_asked_for_by_name(blender) -> None:
    fake = blender({"rigforge_retopo": RETOPO_RESULT})
    report = server.rigforge_retopo(method="decimate", object="goblin")

    assert sent(fake, "rigforge_retopo")["method"] == "decimate"
    assert "deterministic decimate route" in report


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


# ===========================================================================
# Phase 4 — metarig, rig generation, weights, Godot export
# ===========================================================================

METARIG_RESULT = {
    "metarig": "goblin_metarig",
    "bone_count": 64,
    "mapping": {
        "Head": ["spine.006", "spine.005"],
        "Torso": ["spine", "spine.001"],
        "Ear.L": ["ear.L", "ear.L.001"],
    },
    "warnings": [],
}

RIG_RESULT = {
    "rig": "goblin_rig",
    "weighted": ["goblin_retopo"],
    "cleanup_report": {
        "vertices_cleared": 812,
        "groups_removed": 3,
        "normalized_vertices": 7600,
    },
    "warnings": [],
}

WEIGHTS_REPORT = {
    "report": {
        "vertices": 7600,
        "max_influences": 4,
        "over_limit": 118,
        "unnormalized": 3,
        "unweighted": 0,
    }
}

EXPORT_RESULT = {
    "actions": ["idle-loop", "walk-loop"],
    "deform_bones": 31,
    "files": [],
}


# --- rigforge_metarig -------------------------------------------------------


def test_metarig_sends_the_contract_command_with_the_default_archetype(blender) -> None:
    fake = blender({"rigforge_metarig": METARIG_RESULT})
    server.rigforge_metarig(object="goblin_retopo")

    assert fake.requests[0]["type"] == "rigforge_metarig"
    params = sent(fake, "rigforge_metarig")
    assert params == {"object": "goblin_retopo", "archetype": "auto"}
    assert "modules" not in params, "omitted modules let the archetype decide"


@pytest.mark.parametrize("archetype", ["auto", "biped", "quadruped", "custom"])
def test_every_archetype_reaches_the_wire(blender, archetype: str) -> None:
    fake = blender({"rigforge_metarig": METARIG_RESULT})
    report = server.rigforge_metarig(archetype=archetype)

    assert sent(fake, "rigforge_metarig")["archetype"] == archetype
    assert f"archetype {archetype}" in report


def test_modules_cross_the_wire_verbatim(blender) -> None:
    """The add-on owns the module vocabulary, so nothing here rewrites an entry."""
    fake = blender({"rigforge_metarig": METARIG_RESULT})
    modules = [
        {"kind": "tail", "tag": "Tail", "segments": 5},  # an unknown key survives
        {"kind": "chain", "tag": "Ear.L"},
    ]
    report = server.rigforge_metarig(archetype="custom", modules=modules)

    assert sent(fake, "rigforge_metarig")["modules"] == modules
    assert "2 extra module(s)" in report


def test_a_single_module_object_is_accepted_as_a_list_of_one(blender) -> None:
    fake = blender({"rigforge_metarig": METARIG_RESULT})
    server.rigforge_metarig(modules={"kind": "tail", "tag": "Tail"})
    assert sent(fake, "rigforge_metarig")["modules"] == [{"kind": "tail", "tag": "Tail"}]


@pytest.mark.parametrize(
    ("modules", "fragment"),
    [
        ("Tail", "must be a list of module objects"),
        (["Tail"], "Each module must be an object"),
        ([], "was empty"),
        (7, "must be a list of module objects"),
    ],
)
def test_bad_modules_are_refused_before_the_socket(modules: Any, fragment: str) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_metarig(modules=modules)


def test_metarig_report_names_the_rig_the_bones_and_the_mapping(blender) -> None:
    blender({"rigforge_metarig": METARIG_RESULT})
    report = server.rigforge_metarig(object="goblin_retopo")

    assert "Metarig placed for 'goblin_retopo'" in report
    assert "metarig: goblin_metarig" in report
    assert "bones: 64" in report
    assert "tag -> bones (3)" in report
    ear = next(line for line in report.splitlines() if "Ear.L" in line)
    assert "ear.L.001" in ear
    assert "rigforge_generate_rig" in report, "the report teaches the next step"


def test_metarig_warnings_are_loud_and_come_before_the_mapping(blender) -> None:
    blender(
        {
            "rigforge_metarig": {
                "metarig": "goblin_metarig",
                "bone_count": 58,
                "mapping": {"Head": ["spine.006"]},
                "warnings": [
                    "no Hand.R tag: the right wrist was placed from the arm bounds",
                    {"level": "error", "message": "no Foot.L tag; the leg chain is guessed"},
                ],
            }
        }
    )
    report = server.rigforge_metarig()
    lines = report.splitlines()

    assert "WARNINGS (2):" in report
    assert "! no Hand.R tag" in report
    assert "[ERROR] no Foot.L tag" in report
    warned = next(i for i, line in enumerate(lines) if "WARNINGS" in line)
    mapped = next(i for i, line in enumerate(lines) if "tag -> bones" in line)
    assert warned < mapped, "a warning must not hide under the mapping table"


def test_a_minimal_metarig_result_still_renders(blender) -> None:
    """The add-on is being written in parallel; a bare result must not crash."""
    blender({"rigforge_metarig": {}})
    report = server.rigforge_metarig()

    assert "(unnamed)" in report
    assert "bones: ?" in report
    assert "WARNINGS" not in report


# --- rigforge_generate_rig --------------------------------------------------


def test_generate_rig_defaults_send_both_flags_and_nothing_else(blender) -> None:
    fake = blender({"rigforge_generate_rig": RIG_RESULT})
    server.rigforge_generate_rig()

    params = sent(fake, "rigforge_generate_rig")
    assert params == {"parent_with_weights": True, "cleanup": True}
    assert "metarig" not in params and "mesh" not in params


def test_generate_rig_names_and_flags_reach_the_wire(blender) -> None:
    fake = blender({"rigforge_generate_rig": RIG_RESULT})
    report = server.rigforge_generate_rig(
        metarig="  goblin_metarig  ",
        mesh="goblin_retopo",
        parent_with_weights=False,
        cleanup=False,
    )

    assert sent(fake, "rigforge_generate_rig") == {
        "metarig": "goblin_metarig",
        "mesh": "goblin_retopo",
        "parent_with_weights": False,
        "cleanup": False,
    }
    assert "no skinning" in report and "raw weights kept" in report


def test_generate_rig_report_covers_the_rig_the_mesh_and_the_cleanup(blender) -> None:
    blender({"rigforge_generate_rig": RIG_RESULT})
    report = server.rigforge_generate_rig()

    assert "rig: goblin_rig" in report
    assert "weighted: goblin_retopo" in report
    assert "parented with automatic weights" in report
    assert "vertices cleared: 812" in report
    assert "groups removed: 3" in report
    assert "rigforge_export_godot" in report


def test_generate_rig_relays_warnings_and_a_bool_weighted(blender) -> None:
    blender(
        {
            "rigforge_generate_rig": {
                "rig": "goblin_rig",
                "weighted": False,
                "cleanup_report": ["dropped 4 head weights below the neck tag"],
                "warnings": "Ear.R came back unweighted",
            }
        }
    )
    report = server.rigforge_generate_rig(parent_with_weights=False)

    assert "NO — the mesh was not parented" in report
    assert "WARNINGS (1):" in report and "! Ear.R came back unweighted" in report
    assert "dropped 4 head weights" in report


def test_a_minimal_rig_result_still_renders(blender) -> None:
    blender({"rigforge_generate_rig": {}})
    report = server.rigforge_generate_rig()

    assert "rig: (unnamed)" in report
    assert "weighted: not reported" in report


# --- rigforge_weights -------------------------------------------------------


def test_weights_defaults_to_a_read_only_report(blender) -> None:
    fake = blender({"rigforge_weights": WEIGHTS_REPORT})
    server.rigforge_weights(object="goblin_retopo")

    params = sent(fake, "rigforge_weights")
    assert params == {"object": "goblin_retopo", "action": "report"}
    assert "max_influences" not in params, "omitted = the add-on's own default"


@pytest.mark.parametrize("action", ["report", "cleanup", "normalize"])
def test_every_weights_action_carries_max_influences_when_given(
    blender, action: str
) -> None:
    """The limit is what a report measures against, not just what cleanup enforces."""
    fake = blender({"rigforge_weights": {"changed": 0}})
    server.rigforge_weights(action=action, max_influences=8)

    assert sent(fake, "rigforge_weights") == {"action": action, "max_influences": 8}


@pytest.mark.parametrize("limit", [0, -1, 9, 64])
def test_an_impossible_influence_limit_is_refused(limit: int) -> None:
    with pytest.raises(ForgeError, match="between 1 and 8"):
        server.rigforge_weights(action="cleanup", max_influences=limit)


def test_weights_report_renders_the_numbers(blender) -> None:
    blender({"rigforge_weights": WEIGHTS_REPORT})
    report = server.rigforge_weights(object="goblin_retopo")

    assert "Weights on 'goblin_retopo'" in report
    assert "over limit: 118" in report
    assert "unnormalized: 3" in report


def test_cleanup_and_normalize_report_what_changed(blender) -> None:
    blender({"rigforge_weights": {"changed": 118, "warnings": ["3 vertices had no bone"]}})
    report = server.rigforge_weights(action="cleanup")

    assert "Cleaned up weights on the active object" in report
    assert "changed: 118" in report
    assert "WARNINGS (1):" in report and "3 vertices had no bone" in report


def test_a_weights_result_with_nothing_in_it_says_so(blender) -> None:
    blender({"rigforge_weights": {}})
    assert "(the add-on reported no detail)" in server.rigforge_weights(action="normalize")


# --- rigforge_export_godot --------------------------------------------------


def test_export_godot_resolves_the_path_and_creates_the_folder(
    blender, tmp_path: Path
) -> None:
    out = tmp_path / "godot" / "characters" / "goblin"  # no extension, no folder
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})

    server.rigforge_export_godot(path=str(out), rig="goblin_rig")

    params = sent(fake, "rigforge_export_godot")
    assert params["path"] == str(out.with_suffix(".glb")), "defaults to single-file glb"
    assert out.parent.is_dir(), "the folder exists before the add-on is asked"
    assert params["rig"] == "goblin_rig"


def test_an_explicit_gltf_extension_is_honoured(blender, tmp_path: Path) -> None:
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})
    out = tmp_path / "goblin.gltf"
    server.rigforge_export_godot(path=str(out))
    assert sent(fake, "rigforge_export_godot")["path"] == str(out)


def test_a_foreign_extension_becomes_glb(blender, tmp_path: Path) -> None:
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})
    server.rigforge_export_godot(path=str(tmp_path / "goblin.fbx"))
    assert sent(fake, "rigforge_export_godot")["path"] == str(tmp_path / "goblin.glb")


def test_export_defaults_are_all_actions_deform_only_with_a_helper(
    blender, tmp_path: Path
) -> None:
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})
    report = server.rigforge_export_godot(path=str(tmp_path / "goblin.glb"))

    params = sent(fake, "rigforge_export_godot")
    assert params["actions"] == "all"
    assert params["root_motion"] is False
    assert params["deform_only"] is True
    assert params["godot_import_script"] is True
    assert "rig" not in params and "meshes" not in params
    assert "every action" in report and "baked in place" in report
    assert "deform bones only" in report


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("all", "all"),
        ("ALL", "all"),
        (None, "all"),
        (["idle-loop", "walk-loop"], ["idle-loop", "walk-loop"]),
        ("idle-loop, walk-loop", ["idle-loop", "walk-loop"]),
        ("idle-loop", ["idle-loop"]),
        (["idle-loop", "idle-loop"], ["idle-loop"]),
        ('["idle-loop", "walk-loop"]', ["idle-loop", "walk-loop"]),
    ],
)
def test_actions_is_all_or_an_explicit_list(
    blender, tmp_path: Path, given: Any, expected: Any
) -> None:
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})
    server.rigforge_export_godot(path=str(tmp_path / "goblin.glb"), actions=given)
    assert sent(fake, "rigforge_export_godot")["actions"] == expected


@pytest.mark.parametrize(
    ("actions", "fragment"),
    [
        ([], "No action names given"),
        ([{"name": "idle"}], "not an action name"),
        (12, "Could not read actions"),
        (["  "], "No action names given"),
    ],
)
def test_bad_action_lists_are_refused_before_the_socket(
    tmp_path: Path, actions: Any, fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_export_godot(path=str(tmp_path / "goblin.glb"), actions=actions)


def test_export_forwards_the_meshes_and_the_toggles(blender, tmp_path: Path) -> None:
    fake = blender({"rigforge_export_godot": EXPORT_RESULT})
    report = server.rigforge_export_godot(
        path=str(tmp_path / "goblin.glb"),
        rig="goblin_rig",
        meshes=["goblin_retopo", "  goblin_lod1  ", ""],
        actions=["idle-loop"],
        root_motion=True,
        deform_only=False,
        godot_import_script=False,
    )

    params = sent(fake, "rigforge_export_godot")
    assert params["meshes"] == ["goblin_retopo", "goblin_lod1"], "blanks dropped"
    assert params["root_motion"] is True
    assert params["deform_only"] is False
    assert params["godot_import_script"] is False
    assert "1 action(s)" in report
    assert "root motion" in report
    assert "CONTROL BONES KEPT" in report, "a debug export must be obvious"


def test_export_report_lists_the_files_with_their_sizes(blender, tmp_path: Path) -> None:
    glb = tmp_path / "goblin.glb"
    helper = tmp_path / "goblin.gd"
    glb.write_bytes(b"x" * 2048)
    helper.write_text("# import helper\n", encoding="utf-8")
    blender(
        {
            "rigforge_export_godot": {
                "path": str(glb),
                "actions": ["idle-loop", "walk-loop", "attack"],
                "deform_bones": 31,
                "files": [
                    {"path": str(glb), "kind": "gltf"},
                    {"path": str(helper), "kind": "import"},
                ],
            }
        }
    )
    report = server.rigforge_export_godot(path=str(glb), rig="goblin_rig")

    assert f"Exported 'goblin_rig' to Godot — {glb}" in report
    assert "deform bones: 31" in report
    assert "actions (3): idle-loop, walk-loop, attack" in report
    assert "files (2)" in report
    assert "2.0 KB" in report
    assert str(helper) in report


def test_export_warnings_come_before_the_file_list(blender, tmp_path: Path) -> None:
    blender(
        {
            "rigforge_export_godot": {
                "path": str(tmp_path / "goblin.glb"),
                "actions": ["idle-loop"],
                "deform_bones": ["DEF-spine", "DEF-head", "DEF-arm.L", "DEF-arm.R"],
                "files": [str(tmp_path / "goblin.glb")],
                "warnings": [
                    "action 'attack' has no keyframes and was skipped",
                    "goblin_lod2 is not skinned to the rig and was not exported",
                ],
            }
        }
    )
    report = server.rigforge_export_godot(path=str(tmp_path / "goblin.glb"))
    lines = report.splitlines()

    assert "WARNINGS (2):" in report
    assert "! action 'attack' has no keyframes" in report
    warned = next(i for i, line in enumerate(lines) if "WARNINGS" in line)
    filed = next(i for i, line in enumerate(lines) if line.strip().startswith("files"))
    assert warned < filed, "warnings are the point of reading an export report"
    assert "deform bones: 4 (DEF-spine, DEF-head" in report, "a bone list counts too"


def test_a_minimal_export_result_still_says_where_it_went(blender, tmp_path: Path) -> None:
    out = tmp_path / "goblin.glb"
    blender({"rigforge_export_godot": {}})
    report = server.rigforge_export_godot(path=str(out))

    assert str(out) in report
    assert "deform bones: ?" in report
    assert "actions (?): not reported" in report
    assert "files: (none reported)" in report


# --- rigforge_status: the Phase 4 stages of the nudge chain ------------------


def scene_with(*extra: dict[str, Any]) -> dict[str, Any]:
    """SCENE plus the given armature objects."""
    return {"objects": [*SCENE["objects"], *extra], "active": "goblin"}


METARIG_OBJECT = {
    "name": "goblin_metarig",
    "type": "ARMATURE",
    "location": [0.0, 0.0, 0.0],
    "dimensions": [0.6, 0.4, 1.7],
    "vertex_count": 0,
    "modifiers": [],
}

RIG_OBJECT = {
    "name": "goblin_rig",
    "type": "ARMATURE",
    "location": [0.0, 0.0, 0.0],
    "dimensions": [0.6, 0.4, 1.7],
    "vertex_count": 0,
    "modifiers": [],
}


def test_status_nudges_to_the_metarig_once_a_retopo_exists(blender) -> None:
    blender({"get_scene_info": SCENE, "rigforge_list_tags": {"tags": TAGS}}, connections=2)
    report = server.rigforge_status("goblin")

    assert "armatures: none" in report
    assert "next: rigforge_metarig" in report


def test_status_nudges_to_generate_once_a_metarig_exists(blender) -> None:
    blender(
        {"get_scene_info": scene_with(METARIG_OBJECT), "rigforge_list_tags": {"tags": TAGS}},
        connections=2,
    )
    report = server.rigforge_status("goblin")

    assert "metarig goblin_metarig" in report
    assert "no generated rig" in report
    assert "next: rigforge_generate_rig" in report


def test_status_nudges_to_the_export_once_a_rig_has_actions(blender) -> None:
    fake = blender(
        {
            "get_scene_info": scene_with(METARIG_OBJECT, RIG_OBJECT),
            "rigforge_list_tags": {"tags": TAGS},
            "rigforge_action": {"actions": ["idle-loop", "walk-loop"]},
        },
        connections=3,
    )
    report = server.rigforge_status("goblin")

    assert sent(fake, "rigforge_action") == {"action": "list", "rig": "goblin_rig"}
    assert "rig goblin_rig" in report
    assert "actions (2): idle-loop, walk-loop" in report
    assert "next: rigforge_export_godot" in report


def test_rigifys_own_default_names_count_as_this_characters_armatures(blender) -> None:
    """Rigify calls them "metarig" and "RIG-metarig" — neither carries the name."""
    generic_meta = dict(METARIG_OBJECT, name="metarig")
    generic_rig = dict(RIG_OBJECT, name="RIG-metarig")
    blender(
        {
            "get_scene_info": scene_with(generic_meta, generic_rig),
            "rigforge_list_tags": {"tags": TAGS},
        },
        connections=3,  # the action probe is made and refused
    )
    report = server.rigforge_status("goblin")

    assert "metarig metarig" in report
    assert "rig RIG-metarig" in report, "RIG- is the generated rig, not a metarig"
    assert "actions: unavailable" in report
    assert "next: rigforge_export_godot" in report, (
        "an add-on without the Phase 5 commands is unknown, not empty — the "
        "export nudge must survive"
    )


def test_the_chain_starts_at_tagging_when_nothing_is_done(blender) -> None:
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

    assert "next: rigforge_tag" in report


def test_tags_but_no_retopo_nudges_to_retopo(blender) -> None:
    tagged = {"objects": [SCENE["objects"][0]], "active": "goblin"}
    blender({"get_scene_info": tagged, "rigforge_list_tags": {"tags": TAGS}}, connections=2)
    report = server.rigforge_status()

    assert "next: rigforge_retopo" in report


# ===========================================================================
# Phase 5 — cloth and animation
# ===========================================================================

CLOTH_RESULT = {
    "garment": "goblin_coat",
    "output": "shapekeys",
    "shape_keys": ["coat_settled"],
    "warnings": [],
}

ACTION_LIST = {
    "actions": [
        {"name": "idle-loop", "loop": True, "frames": [1, 60], "nla": "base"},
        {"name": "attack", "loop": False, "frames": [1, 18]},
    ]
}

KEYFRAME_RESULT = {"action": "wave", "keys_set": 4, "frame_range": [1, 24]}

RETARGET_RESULT = {
    "action": "run-loop",
    "mapped": {"mixamorig:Hips": "torso", "mixamorig:Spine": "spine_fk.001"},
    "unmapped": ["mixamorig:LeftToeBase"],
    "frames": 240,
}

WAVE = [
    {"bone": "hand_ik.L", "frame": 1, "location": [0.0, 0.0, 0.0]},
    {"bone": "hand_ik.L", "frame": 12, "location": [0.0, 0.0, 0.15]},
    {"bone": "hand_ik.L", "frame": 24, "location": [0.0, 0.0, 0.0]},
]


# --- rigforge_cloth ---------------------------------------------------------


def test_cloth_from_tags_sends_the_contract_parameters(blender) -> None:
    fake = blender({"rigforge_cloth": CLOTH_RESULT})
    server.rigforge_cloth(tags=["Torso", "Arm.L"], object="goblin_retopo")

    assert fake.requests[0]["type"] == "rigforge_cloth"
    params = sent(fake, "rigforge_cloth")
    assert params == {
        "object": "goblin_retopo",
        "tags": ["Torso", "Arm.L"],
        "preset": "cotton",
        "output": "skin_tight",
        "collision": True,
    }
    assert "use_selection" not in params, "the two are alternatives on the wire"


def test_cloth_from_the_selection_sends_no_tag_list(blender) -> None:
    fake = blender({"rigforge_cloth": CLOTH_RESULT})
    report = server.rigforge_cloth(use_selection=True)

    params = sent(fake, "rigforge_cloth")
    assert params["use_selection"] is True
    assert "tags" not in params
    assert "the current selection" in report


def test_cloth_tag_names_are_cleaned_like_every_other_tag(blender) -> None:
    """"tag_Torso" is the add-on's spelling; both forms reach the wire as one."""
    fake = blender({"rigforge_cloth": CLOTH_RESULT})
    server.rigforge_cloth(tags=["tag_Torso", "Torso", " Arm.L ", ""])
    assert sent(fake, "rigforge_cloth")["tags"] == ["Torso", "Arm.L"]


def test_cloth_forwards_every_optional_setting(blender) -> None:
    fake = blender({"rigforge_cloth": CLOTH_RESULT})
    report = server.rigforge_cloth(
        tags=["Torso"],
        name="goblin_coat",
        preset="heavy",
        output="shapekeys",
        offset_mm=4.0,
        thickness_mm=1.5,
        frames=90,
        collision=False,
        object="goblin_retopo",
    )

    assert sent(fake, "rigforge_cloth") == {
        "object": "goblin_retopo",
        "tags": ["Torso"],
        "preset": "heavy",
        "output": "shapekeys",
        "collision": False,
        "name": "goblin_coat",
        "offset_mm": 4.0,
        "thickness_mm": 1.5,
        "frames": 90,
    }
    assert "heavy, shapekeys" in report
    assert "no body collision" in report


def test_omitted_cloth_settings_stay_off_the_wire(blender) -> None:
    """Same rule as bake_resolution: the add-on's own default is not restated."""
    fake = blender({"rigforge_cloth": CLOTH_RESULT})
    server.rigforge_cloth(tags=["Torso"])

    params = sent(fake, "rigforge_cloth")
    for key in ("offset_mm", "thickness_mm", "frames", "name"):
        assert key not in params, key


def test_cloth_tags_and_use_selection_together_is_an_explicit_error() -> None:
    with pytest.raises(ForgeError, match="not both"):
        server.rigforge_cloth(tags=["Torso"], use_selection=True)


def test_cloth_with_neither_says_how_to_name_the_area() -> None:
    with pytest.raises(ForgeError) as exc:
        server.rigforge_cloth()
    message = str(exc.value)
    assert "No garment area given" in message
    assert "rigforge_list_tags" in message
    assert "use_selection" in message


@pytest.mark.parametrize(
    ("tags", "fragment"),
    [
        ([], "was empty"),
        (["   "], "was empty"),
        ([{"tag": "Torso"}], "is not a tag name"),
        (7, "must be a list of tag names"),
    ],
)
def test_bad_cloth_tag_lists_are_refused_before_the_socket(
    tags: Any, fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_cloth(tags=tags)


def test_frames_with_a_skin_tight_garment_is_an_error_not_a_no_op() -> None:
    """skin_tight copies weights and never simulates, so `frames` cannot apply."""
    with pytest.raises(ForgeError, match="only meaningful when a simulation runs"):
        server.rigforge_cloth(tags=["Torso"], output="skin_tight", frames=60)


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"offset_mm": -1.0}, "between 0 and 100 mm"),
        ({"thickness_mm": 500.0}, "between 0 and 100 mm"),
        ({"output": "shapekeys", "frames": 0}, "between 1 and 1000"),
        ({"output": "shapekeys", "frames": 5000}, "between 1 and 1000"),
    ],
)
def test_out_of_range_cloth_settings_are_refused(
    kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_cloth(tags=["Torso"], **kwargs)


def test_the_bones_output_is_flagged_as_degraded_in_the_report(blender) -> None:
    """v1 may fall back; the summary must say so before the user trusts it."""
    fake = blender(
        {
            "rigforge_cloth": {
                "garment": "goblin_cloak",
                "output": "shapekeys",
                "warnings": ["bone output is not implemented in v1; baked shape keys instead"],
            }
        }
    )
    report = server.rigforge_cloth(tags=["Torso"], output="bones")

    assert sent(fake, "rigforge_cloth")["output"] == "bones"
    assert "v1-degraded" in report
    assert "WARNINGS (1):" in report
    assert "! bone output is not implemented in v1" in report
    assert "output: shapekeys" in report, "the report shows what actually happened"


def test_cloth_report_names_the_garment_and_its_shape_keys(blender) -> None:
    blender({"rigforge_cloth": CLOTH_RESULT})
    report = server.rigforge_cloth(
        tags=["Torso", "Arm.L"], output="shapekeys", object="goblin_retopo"
    )

    assert "Garment made from 'goblin_retopo'" in report
    assert "covering Torso, Arm.L" in report
    assert "garment: goblin_coat" in report
    assert "shape keys (1): coat_settled" in report
    assert "rigforge_keyframe" in report, "the report teaches the next step"


def test_a_minimal_cloth_result_still_renders(blender) -> None:
    blender({"rigforge_cloth": {}})
    report = server.rigforge_cloth(use_selection=True)

    assert "garment: (unnamed)" in report
    assert "output: (not reported)" in report
    assert "shape keys" not in report
    assert "WARNINGS" not in report


# --- rigforge_action --------------------------------------------------------


def test_action_defaults_to_listing_the_library(blender) -> None:
    fake = blender({"rigforge_action": ACTION_LIST})
    report = server.rigforge_action()

    assert fake.requests[0]["type"] == "rigforge_action"
    assert sent(fake, "rigforge_action") == {"action": "list"}
    assert "Action library" in report
    assert "2 action(s)" in report


def test_the_action_table_carries_loop_badges_and_frame_ranges(blender) -> None:
    fake = blender({"rigforge_action": ACTION_LIST})
    report = server.rigforge_action(rig="goblin_rig")

    assert sent(fake, "rigforge_action") == {"action": "list", "rig": "goblin_rig"}
    idle = next(line for line in report.splitlines() if "idle-loop" in line)
    assert "yes" in idle and "1-60" in idle and "base" in idle
    attack = next(line for line in report.splitlines() if "attack" in line)
    assert "1-18" in attack


def test_a_bare_action_name_still_gets_a_loop_badge_from_the_suffix(blender) -> None:
    """The `-loop` suffix IS the convention, so a plain name list still reads."""
    blender({"rigforge_action": {"actions": ["idle-loop", "jump"]}})
    report = server.rigforge_action()

    idle = next(line for line in report.splitlines() if "idle-loop" in line)
    jump = next(line for line in report.splitlines() if "jump" in line)
    assert "yes" in idle
    assert "yes" not in jump


def test_new_sends_the_name_and_the_loop_flag(blender) -> None:
    fake = blender({"rigforge_action": {"actions": ["idle-loop"]}})
    report = server.rigforge_action("new", name="idle-loop", loop=True, rig="goblin_rig")

    assert sent(fake, "rigforge_action") == {
        "action": "new",
        "name": "idle-loop",
        "rig": "goblin_rig",
        "loop": True,
    }
    assert "Action created" in report
    assert "looping (-loop)" in report


def test_the_loop_suffix_is_the_addons_to_enforce(blender) -> None:
    """A thin tool does not rewrite the name; the add-on applies the convention."""
    fake = blender({"rigforge_action": {"actions": ["idle-loop"]}})
    server.rigforge_action("new", name="idle", loop=True)

    assert sent(fake, "rigforge_action")["name"] == "idle", "sent as typed"


@pytest.mark.parametrize(
    ("action", "kwargs", "expected"),
    [
        ("delete", {"name": "attack"}, {"action": "delete", "name": "attack"}),
        (
            "duplicate",
            {"source": "walk-loop", "name": "run-loop", "loop": True},
            {
                "action": "duplicate",
                "source": "walk-loop",
                "name": "run-loop",
                "loop": True,
            },
        ),
        (
            "rename",
            {"source": "idle", "name": "idle-loop", "loop": True},
            {"action": "rename", "source": "idle", "name": "idle-loop", "loop": True},
        ),
        ("push_nla", {}, {"action": "push_nla", "loop": False}),
        ("push_nla", {"name": "idle-loop"}, {"action": "push_nla", "name": "idle-loop", "loop": False}),
    ],
)
def test_each_action_puts_its_own_arguments_on_the_wire(
    blender, action: str, kwargs: dict[str, Any], expected: dict[str, Any]
) -> None:
    fake = blender({"rigforge_action": ACTION_LIST})
    server.rigforge_action(action, **kwargs)
    assert sent(fake, "rigforge_action") == expected


def test_loop_is_omitted_where_it_cannot_mean_anything(blender) -> None:
    """`list` and `delete` neither create nor name a clip, so no loop flag."""
    fake = blender({"rigforge_action": ACTION_LIST})
    server.rigforge_action("delete", name="attack", loop=True)
    assert "loop" not in sent(fake, "rigforge_action")


@pytest.mark.parametrize(
    ("action", "kwargs", "fragment"),
    [
        ("new", {}, "needs `name`"),
        ("delete", {}, "needs `name`"),
        ("rename", {"name": "idle-loop"}, "needs `source`"),
        ("duplicate", {"name": "run-loop"}, "needs `source`"),
        ("list", {"name": "idle"}, "`name` means nothing to action 'list'"),
        ("list", {"source": "idle"}, "`source` means nothing"),
        ("new", {"name": "idle", "source": "walk"}, "`source` means nothing"),
        ("delete", {"name": "idle", "source": "walk"}, "`source` means nothing"),
    ],
)
def test_arguments_that_cannot_apply_are_refused_with_their_real_meaning(
    action: str, kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ForgeError, match=re.escape(fragment)):
        server.rigforge_action(action, **kwargs)


def test_an_empty_library_teaches_the_two_ways_to_fill_it(blender) -> None:
    blender({"rigforge_action": {"actions": []}})
    report = server.rigforge_action("list")

    assert "no actions yet" in report
    assert "rigforge_keyframe" in report and "rigforge_retarget" in report


def test_action_warnings_are_relayed(blender) -> None:
    blender(
        {
            "rigforge_action": {
                "actions": ["idle-loop"],
                "warnings": ["'walk' had no keyframes and was not pushed"],
            }
        }
    )
    report = server.rigforge_action("push_nla", name="walk")

    assert "Action pushed to an NLA track" in report
    assert "WARNINGS (1):" in report
    assert "! 'walk' had no keyframes" in report


def test_a_minimal_action_result_still_renders(blender) -> None:
    blender({"rigforge_action": {}})
    report = server.rigforge_action("new", name="jump")

    assert "Action created" in report
    assert "(the add-on reported no action library)" in report


# --- rigforge_keyframe ------------------------------------------------------


def test_keyframe_sends_the_keys_and_the_contract_defaults(blender) -> None:
    fake = blender({"rigforge_keyframe": KEYFRAME_RESULT})
    server.rigforge_keyframe(keys=WAVE, action="wave", rig="goblin_rig")

    assert fake.requests[0]["type"] == "rigforge_keyframe"
    assert sent(fake, "rigforge_keyframe") == {
        "keys": WAVE,
        "interpolation": "BEZIER",
        "clear": False,
        "action": "wave",
        "rig": "goblin_rig",
    }


def test_keyframe_without_a_rig_or_action_targets_the_current_ones(blender) -> None:
    """No names on the wire, and the report says which action it landed in."""
    fake = blender({"rigforge_keyframe": {"keys_set": 3, "frame_range": [1, 24]}})
    report = server.rigforge_keyframe(keys=WAVE)

    params = sent(fake, "rigforge_keyframe")
    assert "rig" not in params and "action" not in params
    assert "the rig's current action" in report, "nothing was named, so say so"


def test_the_action_the_addon_reports_wins_over_the_one_asked_for(blender) -> None:
    """Blender may have created 'wave.001'; the report shows what actually holds."""
    blender({"rigforge_keyframe": {"action": "wave.001", "keys_set": 3}})
    report = server.rigforge_keyframe(keys=WAVE, action="wave")
    assert "Keyframed 'wave.001'" in report


def test_keys_keep_their_order_and_their_extra_fields(blender) -> None:
    """Keys are self-describing, so nothing here sorts or prunes them."""
    fake = blender({"rigforge_keyframe": KEYFRAME_RESULT})
    keys = [
        {"bone": "spine.003", "frame": 24, "rotation_euler_deg": [0, 0, -10],
         "easing": "EASE_OUT"},  # an unknown field survives, as `modules` do
        {"bone": "spine.003", "frame": 1, "rotation_euler_deg": [0, 0, 0]},
    ]
    server.rigforge_keyframe(keys=keys)

    sent_keys = sent(fake, "rigforge_keyframe")["keys"]
    assert [k["frame"] for k in sent_keys] == [24, 1]
    assert sent_keys[0]["easing"] == "EASE_OUT"


def test_a_single_key_object_is_accepted_as_a_list_of_one(blender) -> None:
    fake = blender({"rigforge_keyframe": KEYFRAME_RESULT})
    server.rigforge_keyframe(keys={"bone": "head", "frame": 1, "scale": [1, 1, 1]})
    assert sent(fake, "rigforge_keyframe")["keys"] == [
        {"bone": "head", "frame": 1, "scale": [1.0, 1.0, 1.0]}
    ]


def test_channels_are_normalised_to_three_floats(blender) -> None:
    fake = blender({"rigforge_keyframe": KEYFRAME_RESULT})
    server.rigforge_keyframe(
        keys=[{"bone": "head", "frame": "5", "rotation_euler_deg": [0, "15", 30]}]
    )
    assert sent(fake, "rigforge_keyframe")["keys"] == [
        {"bone": "head", "frame": 5, "rotation_euler_deg": [0.0, 15.0, 30.0]}
    ]


def test_an_empty_key_list_teaches_the_described_motion_shape() -> None:
    with pytest.raises(ForgeError) as exc:
        server.rigforge_keyframe(keys=[])
    message = str(exc.value)
    assert "`keys` was empty" in message
    assert '"bone"' in message and '"frame"' in message
    assert "interpolation fills in" in message


def test_a_key_with_no_channel_is_refused_because_it_would_key_nothing() -> None:
    with pytest.raises(ForgeError) as exc:
        server.rigforge_keyframe(
            keys=[
                {"bone": "hand_ik.L", "frame": 1, "location": [0, 0, 0]},
                {"bone": "hand_ik.L", "frame": 12},
            ]
        )
    message = str(exc.value)
    assert "keys[1]" in message, "the failing entry is named"
    assert "hand_ik.L" in message and "frame 12" in message
    assert "rotation_euler_deg" in message and "location" in message


@pytest.mark.parametrize(
    ("keys", "fragment"),
    [
        ([{"frame": 1, "location": [0, 0, 0]}], "has no `bone`"),
        ([{"bone": "head", "location": [0, 0, 0]}], "has no `frame`"),
        ([{"bone": "head", "frame": 1.5, "scale": [1, 1, 1]}], "whole frame number"),
        ([{"bone": "head", "frame": "soon", "scale": [1, 1, 1]}], "whole frame number"),
        ([{"bone": "head", "frame": 1, "scale": [1, 1]}], "exactly three numbers"),
        ([{"bone": "head", "frame": 1, "location": 3}], "three numbers"),
        ([{"bone": "head", "frame": 1, "location": [0, "up", 0]}], "is not one"),
        (["head"], "must be an object"),
        ("head", "must be a list of keyframe objects"),
    ],
)
def test_malformed_keys_are_refused_before_the_socket(
    keys: Any, fragment: str
) -> None:
    with pytest.raises(ForgeError, match=re.escape(fragment)):
        server.rigforge_keyframe(keys=keys)


def test_clear_and_linear_reach_the_wire_and_the_summary(blender) -> None:
    fake = blender({"rigforge_keyframe": KEYFRAME_RESULT})
    report = server.rigforge_keyframe(
        keys=WAVE, action="wave", interpolation="LINEAR", clear=True
    )

    params = sent(fake, "rigforge_keyframe")
    assert params["interpolation"] == "LINEAR"
    assert params["clear"] is True
    assert "LINEAR" in report
    assert "existing keys cleared" in report


def test_keyframe_report_counts_the_keys_the_bones_and_the_span(blender) -> None:
    blender({"rigforge_keyframe": KEYFRAME_RESULT})
    report = server.rigforge_keyframe(keys=WAVE, action="wave")

    assert "Keyframed 'wave'" in report
    assert "3 key(s) on 1 bone(s) over frames 1-24" in report
    assert "keys set: 4" in report
    assert "frames 1-24" in report
    assert "rigforge_export_godot" in report


def test_a_minimal_keyframe_result_still_renders(blender) -> None:
    blender({"rigforge_keyframe": {}})
    report = server.rigforge_keyframe(keys=WAVE)

    assert "keys set: not reported" in report
    assert "frames ?" in report


# --- rigforge_retarget ------------------------------------------------------


def clip(tmp_path: Path, name: str = "run.bvh") -> Path:
    path = tmp_path / name
    path.write_text("HIERARCHY\n", encoding="utf-8")
    return path


def test_retarget_resolves_the_clip_and_sends_the_contract_parameters(
    blender, tmp_path: Path
) -> None:
    fake = blender({"rigforge_retarget": RETARGET_RESULT})
    source = clip(tmp_path)

    server.rigforge_retarget(str(source), action_name="run-loop", target_rig="goblin_rig")

    assert fake.requests[0]["type"] == "rigforge_retarget"
    assert sent(fake, "rigforge_retarget") == {
        "source_path": str(source),
        "action_name": "run-loop",
        "mapping": "auto",
        "loop": False,
        "scale": "auto",
        "target_rig": "goblin_rig",
    }


def test_the_action_name_defaults_to_the_clips_own_name(blender, tmp_path: Path) -> None:
    fake = blender({"rigforge_retarget": RETARGET_RESULT})
    server.rigforge_retarget(str(clip(tmp_path, "zombie_walk.fbx")))
    assert sent(fake, "rigforge_retarget")["action_name"] == "zombie_walk"


def test_a_missing_clip_fails_before_the_socket(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="No file at"):
        server.rigforge_retarget(str(tmp_path / "gone.bvh"))


def test_a_clip_in_the_wrong_format_is_refused_not_corrected(tmp_path: Path) -> None:
    """Unlike an export path, the extension here describes a file that exists."""
    source = clip(tmp_path, "run.glb")
    with pytest.raises(ForgeError) as exc:
        server.rigforge_retarget(str(source))
    message = str(exc.value)
    assert ".bvh or .fbx only" in message
    assert "nothing is downloaded" in message


@pytest.mark.parametrize("suffix", [".bvh", ".BVH", ".fbx", ".Fbx"])
def test_both_import_formats_are_accepted_in_any_case(
    blender, tmp_path: Path, suffix: str
) -> None:
    fake = blender({"rigforge_retarget": RETARGET_RESULT})
    source = clip(tmp_path, f"run{suffix}")
    server.rigforge_retarget(str(source))
    assert sent(fake, "rigforge_retarget")["source_path"] == str(source)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("auto", "auto"),
        ("AUTO", "auto"),
        (None, "auto"),
        ({"mixamorig:Hips": "torso"}, {"mixamorig:Hips": "torso"}),
        ('{"mixamorig:Hips": "torso"}', {"mixamorig:Hips": "torso"}),
    ],
)
def test_mapping_is_auto_or_an_explicit_bone_dict(
    blender, tmp_path: Path, given: Any, expected: Any
) -> None:
    fake = blender({"rigforge_retarget": RETARGET_RESULT})
    server.rigforge_retarget(str(clip(tmp_path)), mapping=given)
    assert sent(fake, "rigforge_retarget")["mapping"] == expected


@pytest.mark.parametrize(
    ("mapping", "fragment"),
    [
        ({}, "was empty"),
        ({"mixamorig:Hips": ""}, "maps one clip bone to one rig bone"),
        ({"mixamorig:Hips": ["torso"]}, "maps one clip bone to one rig bone"),
        ("hips->torso", "Could not read mapping"),
        (7, "Could not read mapping"),
    ],
)
def test_bad_mappings_are_refused_before_the_socket(
    tmp_path: Path, mapping: Any, fragment: str
) -> None:
    with pytest.raises(ForgeError, match=fragment):
        server.rigforge_retarget(str(clip(tmp_path)), mapping=mapping)


@pytest.mark.parametrize(
    ("given", "expected"), [("auto", "auto"), (0.01, 0.01), ("2", 2.0), (None, "auto")]
)
def test_scale_is_auto_or_a_positive_multiplier(
    blender, tmp_path: Path, given: Any, expected: Any
) -> None:
    fake = blender({"rigforge_retarget": RETARGET_RESULT})
    server.rigforge_retarget(str(clip(tmp_path)), scale=given)
    assert sent(fake, "rigforge_retarget")["scale"] == expected


@pytest.mark.parametrize("scale", [0, -1.0, "backwards"])
def test_an_impossible_scale_is_refused(tmp_path: Path, scale: Any) -> None:
    with pytest.raises(ForgeError):
        server.rigforge_retarget(str(clip(tmp_path)), scale=scale)


def test_retarget_report_leads_with_the_unmapped_bones(blender, tmp_path: Path) -> None:
    blender({"rigforge_retarget": RETARGET_RESULT})
    report = server.rigforge_retarget(
        str(clip(tmp_path)), action_name="run-loop", target_rig="goblin_rig", loop=True
    )

    assert "Retargeted run.bvh onto 'goblin_rig'" in report
    assert "action: run-loop" in report
    assert "frames: 240" in report
    assert "mapped (2): mixamorig:Hips -> torso" in report
    assert "UNMAPPED (1): mixamorig:LeftToeBase" in report
    assert "drive nothing" in report
    assert "looping (-loop)" in report


def test_a_clean_retarget_says_everything_mapped(blender, tmp_path: Path) -> None:
    blender(
        {
            "rigforge_retarget": {
                "action": "run-loop",
                "mapped": 22,
                "unmapped": [],
                "frames": [1, 240],
            }
        }
    )
    report = server.rigforge_retarget(str(clip(tmp_path)), mapping={"Hips": "torso"})

    assert "mapped: 22" in report
    assert "unmapped: none — every source bone found a home" in report
    assert "1 explicit bone mapping(s)" in report
    assert "frames: 1-240" in report


def test_retarget_warnings_come_before_the_mapping(blender, tmp_path: Path) -> None:
    blender(
        {
            "rigforge_retarget": {
                "action": "run-loop",
                "warnings": [
                    {"level": "warning", "message": "the clip is 24 fps, the scene is 30"}
                ],
            }
        }
    )
    report = server.rigforge_retarget(str(clip(tmp_path)), scale=0.01)
    lines = report.splitlines()

    assert "[WARNING] the clip is 24 fps" in report
    warned = next(i for i, line in enumerate(lines) if "WARNINGS" in line)
    mapped = next(i for i, line in enumerate(lines) if "mapped:" in line)
    assert warned < mapped
    assert "scale x0.01" in report


def test_a_minimal_retarget_result_still_renders(blender, tmp_path: Path) -> None:
    blender({"rigforge_retarget": {}})
    report = server.rigforge_retarget(str(clip(tmp_path)))

    assert "action: (unnamed)" in report
    assert "frames: ?" in report
    assert "mapped: not reported" in report


# --- rigforge_status: the Phase 5 stage of the nudge chain -------------------


def test_a_rig_with_no_actions_nudges_to_the_animation_tools(blender) -> None:
    fake = blender(
        {
            "get_scene_info": scene_with(METARIG_OBJECT, RIG_OBJECT),
            "rigforge_list_tags": {"tags": TAGS},
            "rigforge_action": {"actions": []},
        },
        connections=3,
    )
    report = server.rigforge_status("goblin")

    assert sent(fake, "rigforge_action") == {"action": "list", "rig": "goblin_rig"}
    assert "actions: none yet" in report
    assert "next: rigforge_action + rigforge_keyframe" in report
    assert "rigforge_retarget" in report
    assert "rigforge_cloth" in report, "cloth is optional, so it is a mention not a step"


def test_the_action_library_is_not_asked_for_before_a_rig_exists(blender) -> None:
    """No rig, no actions: status must not spend a round trip finding that out."""
    fake = blender(
        {"get_scene_info": scene_with(METARIG_OBJECT), "rigforge_list_tags": {"tags": TAGS}},
        connections=2,
    )
    report = server.rigforge_status("goblin")

    assert [r["type"] for r in fake.requests] == ["get_scene_info", "rigforge_list_tags"]
    assert "actions" not in report
    assert "next: rigforge_generate_rig" in report
