"""The design phase (Phase 16): `save_design_doc`.

The third and last tool in this server that writes to disk, so the tests care
about the same three things `test_new_part` does, plus one that is new:

* **where the bytes land.** Only ever `projects/<slug>/design/<filename>`. The
  slug rules and the traversal refusals are `partforge_new_part`'s, tested again
  here rather than assumed, because this is a second door onto the same folder.
* **what may be written.** `.md`, `.svg`, `.json` and nothing else — a design
  document is prose, a picture, or numbers. Never something that runs.
* **validate before write.** An `.svg` that will not parse and a `.json` that
  will not parse leave nothing on disk. A diagram cut off mid-tag draws as an
  empty box in the artist's chat, which reads as a broken tool rather than a
  broken file, and that is worse than no diagram at all.
* **overwriting is normal**, unlike every other writer here. A design sheet
  iterates: they answer a question, a number changes, it is saved again.

`projects/` is redirected to a tmp_path for every test in this module, so the
real one is never touched. Nothing here binds a port or talks to a backend: the
design phase happens before any geometry exists, so it needs neither.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forge_mcp import config, server, util
from forge_mcp.errors import ForgeError

# --- what a real design phase writes ----------------------------------------

REQUIREMENTS = """# Ankle fan — requirements

| # | Requirement | Value | Source |
|---|---|---|---|
| 1 | Ankle circumference | 240 mm | ASSUMED (adult, mid-range) |
| 2 | Mass per ankle | <= 120 g | ASSUMED — above this it swings |
| 3 | Runtime | 45 min | your answer |
"""

CONCEPT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">
  <rect x="40" y="40" width="200" height="120" fill="none" stroke="#333"/>
  <text x="60" y="100">motor + guard</text>
  <line x1="40" y1="200" x2="240" y2="200" stroke="#333"/>
  <text x="60" y="220">64 mm</text>
</svg>
"""

COMPONENTS = """# Ankle fan — components

- 7 mm coin vibration motor — no, wrong part: no airflow at all.
- 7 mm brushed drone motor + 30 mm prop — ~2 m/s at 20 mm. Marginal.
"""


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect projects/ so no test can write into the real repo folder."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", str(root))
    return root


def design_of(projects_dir: Path, slug: str) -> Path:
    return projects_dir / slug / "design"


# --- filenames --------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "requirements.md",
        "concept.svg",
        "components.md",
        "concept-front.svg",
        "concept_side.svg",
        "numbers.json",
        "REQUIREMENTS.MD",
        "2nd-pass.md",
    ],
)
def test_a_plain_name_with_a_known_extension_is_a_filename(given: str) -> None:
    assert util.design_filename(given) == given


@pytest.mark.parametrize(
    "given",
    [
        "../evil.md",
        "..\\evil.md",
        "../../etc/passwd.md",
        "design/requirements.md",
        "C:\\Windows\\System32\\thing.md",
        "/etc/passwd.md",
        "~/notes.md",
        "%APPDATA%.md",
        "$HOME.md",
        "..",
    ],
)
def test_paths_are_refused_not_cleaned(given: str) -> None:
    with pytest.raises(ForgeError) as caught:
        util.design_filename(given)
    assert "filename" in str(caught.value)


@pytest.mark.parametrize(
    "given",
    [
        "concept diagram.svg",     # a space is not in the alphabet
        ".hidden.md",              # a leading dot is a hidden file, not a name
        "-dash.md",
        "requirements%2emd",       # percent-encoding fails on the alphabet
        "concept\u00e9.svg",
    ],
)
def test_a_name_that_is_not_a_plain_filename_is_refused(given: str) -> None:
    with pytest.raises(ForgeError) as caught:
        util.design_filename(given)
    assert "usable filename" in str(caught.value)


@pytest.mark.parametrize("given", ["", "   ", None])
def test_no_filename_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError) as caught:
        util.design_filename(given)
    assert "No filename" in str(caught.value)


@pytest.mark.parametrize(
    "given",
    ["part.py", "requirements.txt", "concept.png", "sheet", "notes.md.exe",
     "run.bat", "design.html"],
)
def test_only_three_extensions_are_design_documents(given: str) -> None:
    with pytest.raises(ForgeError) as caught:
        util.design_filename(given)
    assert "not a design document" in str(caught.value)
    # And it names the tool that DOES write source, so the refusal is a signpost.
    assert "partforge_new_part" in str(caught.value)


def test_a_very_long_filename_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ForgeError):
        util.design_filename("a" * (util.DESIGN_NAME_MAX + 1) + ".md")


def test_design_paths_stay_under_the_projects_design_folder(
    projects_dir: Path,
) -> None:
    design, path = util.design_paths("ankle-fan", "requirements.md")
    assert design == (projects_dir / "ankle-fan" / "design").resolve()
    assert path == design / "requirements.md"
    assert path.is_relative_to(projects_dir.resolve())


# --- content sanity ---------------------------------------------------------


def test_an_svg_that_will_not_parse_is_refused() -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_design_content(
            '<svg xmlns="http://www.w3.org/2000/svg"><rect x="1"',
            "concept.svg")
    assert "not valid XML" in str(caught.value)
    assert "Nothing was written" in str(caught.value)


def test_xml_that_is_not_an_svg_is_refused() -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_design_content("<html><body>hi</body></html>",
                                      "concept.svg")
    assert "not <svg>" in str(caught.value)


def test_a_namespaced_svg_root_is_accepted() -> None:
    text = util.normalize_design_content(CONCEPT_SVG, "concept.svg")
    assert text.endswith("\n")


def test_json_that_will_not_parse_is_refused() -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_design_content('{"runtime_min": 45,', "numbers.json")
    assert "not valid JSON" in str(caught.value)


def test_markdown_is_not_parsed_at_all() -> None:
    # Prose has no shape to check, and inventing one would refuse a valid sheet.
    assert util.normalize_design_content("<not xml & not json", "notes.md")


@pytest.mark.parametrize("given", ["", "   \n  ", None, 17])
def test_empty_content_is_refused(given: Any) -> None:
    with pytest.raises(ForgeError) as caught:
        util.normalize_design_content(given, "requirements.md")
    assert "No content" in str(caught.value)


def test_line_endings_are_normalised_and_a_newline_is_added() -> None:
    assert util.normalize_design_content("a\r\nb", "notes.md") == "a\nb\n"


# --- the tool ---------------------------------------------------------------


def test_a_design_doc_lands_only_in_the_projects_design_folder(
    projects_dir: Path,
) -> None:
    report = server.save_design_doc("ankle fan", "requirements.md", REQUIREMENTS)

    design = design_of(projects_dir, "ankle-fan")
    assert [p.name for p in projects_dir.iterdir()] == ["ankle-fan"]
    assert [p.name for p in (projects_dir / "ankle-fan").iterdir()] == ["design"]
    assert [p.name for p in design.iterdir()] == ["requirements.md"]
    assert (design / "requirements.md").read_text(encoding="utf-8") == REQUIREMENTS
    assert "Saved requirements.md" in report
    assert str(design / "requirements.md") in report


def test_the_project_folder_does_not_have_to_exist_first(projects_dir: Path) -> None:
    """The whole point of the phase: the sheet comes BEFORE the part."""
    assert not (projects_dir / "ankle-fan").exists()
    server.save_design_doc("ankle fan", "requirements.md", REQUIREMENTS)
    assert (projects_dir / "ankle-fan" / "design" / "requirements.md").is_file()
    # And still no part script, no spec — nothing was built.
    assert not (projects_dir / "ankle-fan" / "part.py").exists()
    assert not (projects_dir / "ankle-fan" / "spec.json").exists()


def test_the_name_is_slugged_the_way_a_part_name_is(projects_dir: Path) -> None:
    server.save_design_doc("  Ankle Fan!  ", "requirements.md", REQUIREMENTS)
    assert (projects_dir / "ankle-fan" / "design" / "requirements.md").is_file()


@pytest.mark.parametrize(
    "project",
    ["../evil", "..\\evil", "projects/thing", "C:\\Windows\\thing", "/etc",
     "~/thing", "%APPDATA%", "$HOME", ""],
)
def test_a_path_shaped_project_is_refused(project: str, projects_dir: Path) -> None:
    with pytest.raises(ForgeError):
        server.save_design_doc(project, "requirements.md", REQUIREMENTS)
    assert list(projects_dir.iterdir()) == []


@pytest.mark.parametrize(
    "filename",
    ["../part.py", "..\\..\\evil.md", "design/../../evil.md", "part.py",
     "concept.png", ".hidden.md"],
)
def test_a_path_shaped_or_wrong_typed_filename_writes_nothing(
    filename: str, projects_dir: Path,
) -> None:
    with pytest.raises(ForgeError):
        server.save_design_doc("ankle fan", filename, REQUIREMENTS)
    assert list(projects_dir.iterdir()) == []


def test_a_broken_diagram_leaves_nothing_on_disk(projects_dir: Path) -> None:
    with pytest.raises(ForgeError) as caught:
        server.save_design_doc("ankle fan", "concept.svg",
                               '<svg viewBox="0 0 8 5"><rect')
    assert "Nothing was written" in str(caught.value)
    assert list(projects_dir.iterdir()) == []


def test_a_broken_diagram_does_not_replace_a_good_one(projects_dir: Path) -> None:
    server.save_design_doc("ankle fan", "concept.svg", CONCEPT_SVG)
    with pytest.raises(ForgeError):
        server.save_design_doc("ankle fan", "concept.svg", "<svg><rect")
    path = design_of(projects_dir, "ankle-fan") / "concept.svg"
    assert path.read_text(encoding="utf-8") == CONCEPT_SVG


def test_overwriting_is_normal_and_says_so(projects_dir: Path) -> None:
    """A design sheet iterates — no `overwrite` flag to remember."""
    server.save_design_doc("ankle fan", "requirements.md", REQUIREMENTS)
    revised = REQUIREMENTS.replace("45 min", "90 min")
    report = server.save_design_doc("ankle fan", "requirements.md", revised)
    path = design_of(projects_dir, "ankle-fan") / "requirements.md"
    assert path.read_text(encoding="utf-8") == revised
    assert "Updated requirements.md" in report


def test_the_report_points_at_the_library_and_the_whole_sheet(
    projects_dir: Path,
) -> None:
    server.save_design_doc("ankle fan", "requirements.md", REQUIREMENTS)
    server.save_design_doc("ankle fan", "components.md", COMPONENTS)
    report = server.save_design_doc("ankle fan", "concept.svg", CONCEPT_SVG)

    # Every document, in reading order — sheet, diagram, components.
    assert ("design sheet for ankle-fan: requirements.md, concept.svg, "
            "components.md") in report
    assert "ankle-fan card in the Library" in report
    # A diagram is a picture: the reply has to name its path so it renders.
    assert "renders" in report and "inline" in report
    # And the gate, on every single save.
    assert "sign-off gate" in report and "build it" in report


def test_a_prose_document_is_not_told_to_render_itself(projects_dir: Path) -> None:
    report = server.save_design_doc("ankle fan", "requirements.md", REQUIREMENTS)
    assert "renders" not in report
    assert "sign-off gate" in report


def test_design_documents_reads_the_sheet_in_reading_order(
    projects_dir: Path,
) -> None:
    for name, body in (("zeta.md", "z"), ("components.md", COMPONENTS),
                       ("alpha.md", "a"), ("concept.svg", CONCEPT_SVG),
                       ("requirements.md", REQUIREMENTS)):
        server.save_design_doc("ankle fan", name, body)
    found = util.design_documents("ankle-fan")
    assert [item["file"] for item in found] == [
        "requirements.md", "concept.svg", "components.md", "alpha.md", "zeta.md"]
    assert all(item["size"] > 0 for item in found)
    assert found[0]["path"].endswith("requirements.md")


def test_design_documents_of_a_project_with_no_design_folder_is_empty() -> None:
    assert util.design_documents("never-designed") == []
