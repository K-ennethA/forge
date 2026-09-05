"""The slicer wrapper, exercised against a stub rather than a real install.

Nothing here needs build123d, fastapi or an installed slicer: detection is
filesystem probing, the command line is a list of strings, and the run itself is
:mod:`tests.fake_slicer` pretending to be ``orca-slicer.exe``.  The four cases
that matter are all here -- success, a missing executable, a non-zero exit with
something useful on stderr, and a hang that has to be killed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from service.errors import ParamError
from service.slicer import (
    CLI,
    SlicerFailedError,
    SlicerNotFoundError,
    build_argv,
    detect_slicer,
    health_detection,
    launcher,
    resolve_extra_args,
    resolve_input,
    resolve_profiles,
    resolve_slice_output,
    run_slice,
)

FAKE_SLICER = str(Path(__file__).resolve().parent / "fake_slicer.py")


@pytest.fixture
def model(tmp_path) -> str:
    """A file with the right name.  The stub never looks inside it."""
    path = tmp_path / "ring band.stl"  # a space, on purpose: Windows is the platform
    path.write_bytes(b"\0" * 84)
    return str(path)


@pytest.fixture
def no_installed_slicer(monkeypatch):
    """A machine with nothing installed, which is the state to handle first."""
    monkeypatch.delenv("FORGE_SLICER", raising=False)
    monkeypatch.setattr("service.slicer.INSTALL_CANDIDATES", ())
    monkeypatch.setattr("service.slicer.PATH_NAMES", ())


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def test_no_slicer_anywhere_is_a_report_not_an_exception(no_installed_slicer):
    result = detect_slicer()
    assert result["found"] is False
    assert result["path"] is None
    assert "FORGE_SLICER" in result["configure"]
    assert "slicer_path" in result["configure"]


def test_the_environment_variable_wins_over_the_install_locations(
    monkeypatch, tmp_path
):
    exe = tmp_path / "orca-slicer.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORGE_SLICER", str(exe))

    result = detect_slicer()
    assert result["found"] is True
    assert result["path"] == str(exe)
    assert result["source"] == "$FORGE_SLICER"
    assert result["flavor"] == "orcaslicer"


def test_an_explicit_path_wins_over_everything(monkeypatch, tmp_path):
    other = tmp_path / "orca-slicer.exe"
    other.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORGE_SLICER", str(other))

    result = detect_slicer(explicit=FAKE_SLICER)
    assert result["path"] == FAKE_SLICER
    assert result["source"] == "request.slicer_path"


def test_an_explicit_path_that_is_not_there_says_so(tmp_path):
    with pytest.raises(ParamError) as raised:
        detect_slicer(explicit=str(tmp_path / "nowhere" / "orca-slicer.exe"))
    assert "does not exist" in str(raised.value)


def test_a_profile_naming_its_slicer_promotes_that_flavour(monkeypatch, tmp_path):
    """Two installs on one machine: the profile decides which is probed first."""
    orca = tmp_path / "orca" / "orca-slicer.exe"
    elegoo = tmp_path / "elegoo" / "elegoo-slicer.exe"
    for path in (orca, elegoo):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    monkeypatch.delenv("FORGE_SLICER", raising=False)
    monkeypatch.setattr(
        "service.slicer.INSTALL_CANDIDATES",
        (
            {"flavor": "orcaslicer", "path": str(orca)},
            {"flavor": "elegooslicer", "path": str(elegoo)},
        ),
    )

    assert detect_slicer()["path"] == str(orca)
    assert detect_slicer(prefer="elegooslicer")["path"] == str(elegoo)


def test_health_detection_lists_what_it_probed_when_it_found_nothing(
    no_installed_slicer,
):
    summary = health_detection()
    assert summary["found"] is False
    assert summary["probed"] == []
    assert "configure" in summary


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


def test_the_input_must_be_an_absolute_existing_model(tmp_path, model):
    assert resolve_input(model) == Path(model)

    with pytest.raises(ParamError, match="absolute"):
        resolve_input("ring.stl")
    with pytest.raises(ParamError, match="does not exist"):
        resolve_input(str(tmp_path / "missing.stl"))

    other = tmp_path / "notes.txt"
    other.write_text("", encoding="utf-8")
    with pytest.raises(ParamError, match="not a model"):
        resolve_input(str(other))


def test_the_output_extension_picks_the_strategy(tmp_path):
    gcode, kind = resolve_slice_output(str(tmp_path / "out" / "band.gcode"))
    assert kind == "slice"
    assert gcode.parent.is_dir()  # parents are created, like /export does

    project, kind = resolve_slice_output(str(tmp_path / "band.3mf"))
    assert kind == "project"
    assert project.suffix == ".3mf"

    # No extension at all defaults to G-code rather than guessing.
    bare, kind = resolve_slice_output(str(tmp_path / "band"))
    assert bare.suffix == ".gcode" and kind == "slice"

    with pytest.raises(ParamError, match="does not produce"):
        resolve_slice_output(str(tmp_path / "band.stl"))


def test_profiles_must_exist_and_must_not_contain_the_separator(tmp_path):
    profile = tmp_path / "machine.json"
    profile.write_text("{}", encoding="utf-8")
    assert resolve_profiles(str(profile)) == [str(profile)]
    assert resolve_profiles([str(profile), str(profile)]) == [str(profile)] * 2
    assert resolve_profiles(None) == []

    with pytest.raises(ParamError, match="does not exist"):
        resolve_profiles(str(tmp_path / "gone.json"))


def test_extra_args_must_be_strings():
    assert resolve_extra_args(["--arrange", "1"]) == ["--arrange", "1"]
    assert resolve_extra_args(None) == []
    with pytest.raises(ParamError, match="strings"):
        resolve_extra_args([1])
    with pytest.raises(ParamError, match="list"):
        resolve_extra_args("--arrange")


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_gcode_output_asks_for_a_slice_and_never_for_a_3mf(tmp_path, model):
    """--slice and --export-3mf together crash OrcaSlicer 2.3.2; keep them apart."""
    argv = build_argv(
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
        Path(model),
        tmp_path,
        "band.gcode",
        "slice",
        [r"C:\profiles\machine.json", r"C:\profiles\process.json"],
    )
    assert argv[0].endswith("orca-slicer.exe")
    assert CLI["slice"] in argv
    assert CLI["export_3mf"] not in argv
    assert argv[argv.index(CLI["slice"]) + 1] == CLI["slice_all_plates"]
    assert argv[argv.index(CLI["load_settings"]) + 1] == (
        r"C:\profiles\machine.json;C:\profiles\process.json"
    )
    assert argv[argv.index(CLI["outputdir"]) + 1] == str(tmp_path)
    assert argv[-1] == model  # the model is always last


def test_project_output_asks_for_a_bare_file_name(tmp_path, model):
    """Orca joins --export-3mf onto --outputdir, so an absolute path fails."""
    argv = build_argv(
        "orca-slicer", Path(model), tmp_path, "band.3mf", "project", []
    )
    assert argv[argv.index(CLI["export_3mf"]) + 1] == "band.3mf"
    assert CLI["slice"] not in argv


def test_extra_args_land_before_the_model(tmp_path, model):
    argv = build_argv(
        "orca-slicer", Path(model), tmp_path, "band.gcode", "slice", [], (), ["--arrange", "1"]
    )
    assert argv[-3:] == ["--arrange", "1", model]


def test_a_python_slicer_path_runs_under_this_interpreter():
    import sys

    assert launcher(FAKE_SLICER) == [sys.executable, FAKE_SLICER]
    assert launcher(r"C:\x\orca-slicer.exe") == [r"C:\x\orca-slicer.exe"]


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def test_a_successful_slice_moves_the_output_where_it_was_asked_for(tmp_path, model):
    """The slicer names its own file; we are the ones who put it where asked."""
    target = tmp_path / "gcode out" / "band.gcode"
    result = run_slice(model, str(target), slicer_path=FAKE_SLICER, timeout_s=60)

    assert result["output"] == str(target)
    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith("; fake slicer output")
    # The stub wrote plate_1.gcode, exactly as OrcaSlicer does.
    assert result["produced_name"] == "plate_1.gcode"
    assert result["returncode"] == 0
    assert "fake slicer" in result["stdout_tail"]
    assert result["duration_ms"] > 0
    assert result["size_bytes"] > 0
    assert result["printer"]["name"] == "Elegoo Centauri Carbon"


def test_a_project_export_keeps_the_name_it_was_given(tmp_path, model):
    target = tmp_path / "band.3mf"
    result = run_slice(model, str(target), slicer_path=FAKE_SLICER, timeout_s=60)
    assert result["produced_name"] == "band.3mf"
    assert target.is_file()


def test_profiles_are_passed_through_and_checked_by_the_slicer(tmp_path, model):
    machine = tmp_path / "machine.json"
    machine.write_text("{}", encoding="utf-8")
    result = run_slice(
        model,
        str(tmp_path / "band.gcode"),
        profile=[str(machine)],
        slicer_path=FAKE_SLICER,
        timeout_s=60,
    )
    assert result["profiles"] == [str(machine)]


def test_no_slicer_installed_is_a_structured_400_not_a_crash(
    no_installed_slicer, tmp_path, model
):
    with pytest.raises(SlicerNotFoundError) as raised:
        run_slice(model, str(tmp_path / "band.gcode"))

    error = raised.value
    assert error.http_status == 400
    payload = error.to_payload()
    assert "error" in payload and "traceback" in payload  # the contract shape
    assert payload["slicer"]["found"] is False
    assert "FORGE_SLICER" in payload["slicer"]["configure"]


def test_a_missing_executable_names_the_path_it_was_given(tmp_path, model):
    with pytest.raises(ParamError, match="does not exist"):
        run_slice(
            model,
            str(tmp_path / "band.gcode"),
            slicer_path=str(tmp_path / "orca-slicer.exe"),
        )


def test_a_non_zero_exit_comes_back_with_the_slicer_own_stderr(tmp_path, model):
    with pytest.raises(SlicerFailedError) as raised:
        run_slice(
            model,
            str(tmp_path / "band.gcode"),
            slicer_path=FAKE_SLICER,
            extra_args=["--forge-test-mode", "fail"],
            timeout_s=60,
        )

    error = raised.value
    assert error.http_status == 400
    assert "outside the print area" in error.message
    detail = error.to_payload()["slicer"]
    assert detail["returncode"] == 13
    assert "found error" in detail["stderr_tail"]
    assert not (tmp_path / "band.gcode").exists()


def test_a_silent_run_that_writes_nothing_is_still_a_failure(tmp_path, model):
    with pytest.raises(SlicerFailedError, match="wrote no file"):
        run_slice(
            model,
            str(tmp_path / "band.gcode"),
            slicer_path=FAKE_SLICER,
            extra_args=["--forge-test-mode", "nofile"],
            timeout_s=60,
        )


def test_a_hanging_slicer_is_killed_at_the_timeout(tmp_path, model):
    with pytest.raises(SlicerFailedError) as raised:
        run_slice(
            model,
            str(tmp_path / "band.gcode"),
            slicer_path=FAKE_SLICER,
            extra_args=["--forge-test-mode", "hang"],
            timeout_s=2.0,
        )

    error = raised.value
    assert "time limit" in error.message
    assert "FORGE_SLICE_TIMEOUT" in error.message
    assert error.to_payload()["slicer"]["timed_out"] is True


def test_a_bad_model_path_never_reaches_the_slicer(tmp_path):
    with pytest.raises(ParamError, match="does not exist"):
        run_slice(
            str(tmp_path / "gone.stl"),
            str(tmp_path / "band.gcode"),
            slicer_path=FAKE_SLICER,
        )
