"""The two-destination shape, and the Workspace's beginner pass.

Run them the same way as the rest::

    service\\.venv\\Scripts\\python.exe -m pytest assistant\\tests -q

The harness is ``test_webui``'s, which is ``test_bridge``'s: a real bridge
process on an ephemeral port with ``fake_claude.py`` standing in for the CLI,
every folder it reads or writes pointed at ``tmp_path``.  **Nothing here
touches port 8901 and nothing here touches port 9876.**

The owner, looking at six tabs: *"why do we still have the old ui mixed in"*.
docs/ux-flow.md answers it — "Navigation is Home ↔ Studio.  The old top-level
tabs fold in: Flows become stage actions, the free-floating chat becomes the
docked one, Library IS the home screen."  What is pinned here, in order:

* the navigation is Home plus the two rooms of ONE project, and the three that
  died left no button behind;
* no tab's contents were thrown away with its button — the Library's grids and
  every one of their actions, the Flows list, the Studio's chat and its part
  sheet each have a named home, and the old names still resolve to it;
* every stage id in pipeline.py's templates has plain words on this page, read
  out of the module that OWNS the ids rather than out of a list retyped here;
* the big three compose the turns the flow doc names — evaluated for real
  under node, then sent through ``/ask`` and read back off the CLI's argv, so
  the sentence is checked where it actually lands;
* the beginner bar: Escape backs out, no shortcut takes a modifier, and one
  caption per section.

The phone-width claim (nothing wider than 375 px) is a LAYOUT fact and a
stylesheet cannot prove it on its own; what is pinned here is the mechanism —
the tab row that overflowed is three buttons, and every multi-column grid on
these screens has a floor that fits inside a phone.  The measurement itself is
the browser drive in the lane's report.
"""

import ast
import io
import os
import re
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ASSISTANT_DIR, os.pardir))
WEBUI_DIR = os.path.join(ASSISTANT_DIR, "webui")

for _path in (TESTS_DIR, ASSISTANT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from test_webui import (  # noqa: E402,F401
    bridges, client, fetch_text, raw_get, last_prompt,
)


# ---------------------------------------------------------------------------
# reading the two sources: the page, and the module that owns the stage ids
# ---------------------------------------------------------------------------

#: The narrowest phone this page is claimed to work on.  375 px is the iPhone
#: SE/12-mini viewport and the width the owner reported the tab row
#: overflowing at.
PHONE_PX = 375


def app_js():
    with io.open(os.path.join(WEBUI_DIR, "app.js"), encoding="utf-8") as handle:
        return handle.read()


def app_css():
    with io.open(os.path.join(WEBUI_DIR, "app.css"), encoding="utf-8") as handle:
        return handle.read()


def page_html():
    with io.open(os.path.join(WEBUI_DIR, "index.html"), encoding="utf-8") as handle:
        return handle.read()


def template_stage_ids():
    """Every stage id in every one of ``pipeline.py``'s chains, by task.

    Parsed rather than imported, for ``test_planning.task_config_source``'s
    reason: ``forge_mcp`` has its own venv and this suite runs in the
    assistant's.  The ids are literals in the tuple each ``_*_stages()``
    returns, and ``ast`` can read them without importing anything.

    Returns ``{task: [id, ...]}`` keyed by the name in ``STAGE_TEMPLATES``, so
    a chain that is renamed or a task that is added shows up here rather than
    quietly going unchecked.
    """
    path = os.path.join(REPO_ROOT, "mcp", "forge_mcp", "pipeline.py")
    with io.open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    # `_character_stages` -> ["reference", "design", ...]
    by_function = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        ids = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return):
                continue
            if not isinstance(inner.value, ast.List):
                continue
            for item in inner.value.elts:
                if isinstance(item, ast.Tuple) and item.elts:
                    first = item.elts[0]
                    if isinstance(first, ast.Constant) and \
                            isinstance(first.value, str):
                        ids.append(first.value)
        if ids:
            by_function[node.name] = ids

    # `STAGE_TEMPLATES = {"character": _character_stages, ...}`
    mapping = None
    for node in tree.body:
        value = None
        if isinstance(node, ast.AnnAssign) and \
                isinstance(node.target, ast.Name) and \
                node.target.id == "STAGE_TEMPLATES":
            value = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and \
                        target.id == "STAGE_TEMPLATES":
                    value = node.value
        if value is not None and isinstance(value, ast.Dict):
            mapping = value
    assert mapping is not None, "STAGE_TEMPLATES is no longer a dict literal"

    out = {}
    for key, fn in zip(mapping.keys, mapping.values):
        assert isinstance(key, ast.Constant), ast.dump(key)
        assert isinstance(fn, ast.Name), ast.dump(fn)
        assert fn.id in by_function, \
            "%s builds its chain somewhere this test cannot read" % fn.id
        out[key.value] = by_function[fn.id]
    return out


def stage_words():
    """``STAGE_WORDS`` out of app.js as ``{id: {"name", "does"}}``.

    The literal is read, not the render function: the mapping being DATA
    beside the ids is the thing being pinned, and a table this can parse is
    the same table the page draws from.
    """
    script = app_js()
    start = script.index("var STAGE_WORDS = {")
    body = script[start + len("var STAGE_WORDS = "):]
    # Walk to the matching brace rather than guessing at a terminator.
    depth = 0
    end = None
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end, "STAGE_WORDS is not a brace-balanced object literal"
    literal = body[:end]
    found = {}
    for match in re.finditer(
            r'(\w+):\s*\{\s*name:\s*"([^"]*)",\s*\n?\s*does:\s*"([^"]*)"\s*\}',
            literal):
        found[match.group(1)] = {"name": match.group(2), "does": match.group(3)}
    assert found, "STAGE_WORDS stopped being {id: {name, does}} entries"
    return found


# ---------------------------------------------------------------------------
# 1 — the navigation: two destinations, three tabs, nothing orphaned
# ---------------------------------------------------------------------------

def test_the_navigation_is_home_and_one_projects_two_rooms(client):
    """docs/ux-flow.md: "Navigation is Home ↔ Studio"."""
    html = fetch_text(client, "/")
    script = fetch_text(client, "/webui/app.js")
    assert 'var TABS = ["home", "planning", "workspace"];' in script
    nav = html[html.index('<nav class="tabs"'):html.index("</nav>")]
    assert re.findall(r'id="(tab-[a-z]+)"', nav) == \
        ["tab-home", "tab-planning", "tab-workspace"]


def test_the_three_tabs_that_died_left_no_button_behind(client):
    """Studio, Library and Flows are not tabs and are not tab panels."""
    html = fetch_text(client, "/")
    for gone in ("tab-studio", "tab-library", "tab-flows",
                 "panel-library", "panel-flows"):
        assert ('id="%s"' % gone) not in html, gone
    # …and nothing on the page still claims to control or be labelled by one.
    for gone in ("tab-studio", "tab-library", "tab-flows"):
        assert ('aria-labelledby="%s"' % gone) not in html, gone
    assert 'aria-controls="panel-library"' not in html
    assert 'aria-controls="panel-flows"' not in html


def test_the_two_project_rooms_are_hidden_until_a_project_is_open(client):
    """"When no project is open only Home shows" — the two-destination shape.

    Both rooms ship hidden in the markup, so the very first paint is one tab,
    and ``refreshTabs`` is what brings them back.
    """
    html = fetch_text(client, "/")
    nav = html[html.index('<nav class="tabs"'):html.index("</nav>")]
    for room in ("tab-planning", "tab-workspace"):
        button = nav[nav.index('id="%s"' % room):]
        button = button[:button.index(">")]
        assert "hidden" in button, room
    script = fetch_text(client, "/webui/app.js")
    rule = script[script.index("function refreshTabs()"):]
    rule = rule[:rule.index("\n  }")]
    # Both rooms hang off the ONE open project (openProject()), not off
    # plan.project — Plan stays a tab for as long as a project is open, per
    # docs/ux-flow.md Screen 2 ("new project, or any time from the Studio").
    assert "var project = openProject();" in rule
    assert '$("tab-planning").hidden = !project;' in rule
    assert '$("tab-workspace").hidden = !project;' in rule


def test_a_room_asked_for_with_no_project_lands_on_home(client):
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function showTab(which)"):]
    body = body[:body.index("function tabFromHash()")]
    assert 'if (which === "planning" && !plan.project) { which = "home"; }' in body
    assert 'if (which === "workspace" && !openProject()) { which = "home"; }' in body


def test_the_old_tab_names_still_resolve_to_where_their_contents_went(client):
    """A #library bookmark, or "studio" left in localStorage, is not a 404.

    Each alias points at the screen that SWALLOWED that tab, which is the only
    honest answer: the library is on Home, the flows are in the Workspace, and
    the Studio is the project's screen.
    """
    script = fetch_text(client, "/webui/app.js")
    block = script[script.index("var TAB_ALIASES = {"):]
    block = block[:block.index("};") + 2]
    for name, lands in (("chat", "workspace"), ("workbench", "workspace"),
                        ("studio", "workspace"), ("library", "home"),
                        ("flows", "home")):
        assert ('%s: "%s"' % (name, lands)) in block, name


# ---------------------------------------------------------------------------
# 2 — nothing was thrown away with the buttons
# ---------------------------------------------------------------------------

def test_the_librarys_grids_and_every_action_on_them_live_on_home(client):
    """"Library IS the home screen" — the shelf, plus one drawer under it.

    The drawer holds the three grids the tab held, so every button on their
    cards is still a button somebody can press: Open and Save scene on a
    project, Open in Studio, and the models row's own Import, File into, and
    open-in-Blender.
    """
    html = fetch_text(client, "/")
    home = html[html.index('id="panel-home"'):html.index('id="panel-planning"')]
    drawer = home[home.index('id="home-files"'):]
    for grid in ("library", "library-models", "library-scene"):
        assert ('id="%s"' % grid) in drawer, grid
    assert "<details" in home[:home.index('id="home-files"') + 40]

    script = fetch_text(client, "/webui/app.js")
    for action in ("function openProject(", "function saveProject(",
                   "function openInStudio(", "function importModel(",
                   "function fileModel(", "function openModel(",
                   "function modelCard(", "function libraryCard(",
                   "function sceneCard("):
        assert action in script, action
    # The models row's three buttons, by the words on them.
    assert '"Import"' in script or "Import" in script
    assert "function renderModels(" in script


def test_the_files_drawer_is_read_when_it_is_opened(client):
    """It was a tab that refetched on every open; it is a drawer that does."""
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index('$("home-files").addEventListener("toggle"'):]
    body = body[:body.index("});") + 3]
    assert "loadLibrary();" in body


def test_open_in_studio_now_opens_the_project_not_a_tab(client):
    """The Studio is a project's screen now, so the button opens THAT.

    It still pins the part sheet to the part it was pressed on, because
    somebody who went looking for this one should not lose it to the next
    turn — that half did not change, only where it lands.
    """
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function openInStudio("):]
    body = body[:body.index("var TASK_CONFIG_FILE")]
    assert 'showTab("workspace")' in body
    assert "ws.project = name;" in body
    assert "ws.pipeline = null;" in body        # or the last plan is redrawn
    assert "pinProject(name)" in body
    assert "selectProject(name)" in body
    assert 'showTab("studio")' not in script


def test_the_flows_list_is_a_workspace_drawer_now(client):
    """"The three flow buttons already sit above every screen."

    So what the tab held besides them — the saved sequences, with their
    parameters and their Run button — is a drawer beside the renders and the
    versions, and it is fetched when it is first opened the way the tab was.
    """
    html = fetch_text(client, "/")
    workspace = html.split('id="panel-workspace"', 1)[1].split("</section>", 1)[0]
    drawer = workspace[workspace.index('id="ws-flows"'):]
    assert 'id="flows"' in drawer
    assert 'id="flows-refresh"' in drawer
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index('$("ws-flows").addEventListener("toggle"'):]
    body = body[:body.index("});") + 3]
    assert "loadFlows();" in body
    # The three canned buttons are still above every screen, not in the drawer.
    assert html.index('id="flow-buttons"') < html.index("<main>")


def test_the_studios_chat_is_the_one_in_both_rooms_moved_not_copied(client):
    """One thread, one composer, one session — now in three possible places.

    The Workspace used to carry a proxy textarea whose whole job was to set
    this composer's value and press its Send.  That box is gone: the real
    column is hosted in the dock, so an answer is readable where the question
    was asked.
    """
    html = fetch_text(client, "/")
    script = fetch_text(client, "/webui/app.js")
    assert html.count('id="thread"') == 1
    assert html.count('id="composer"') == 1
    workspace = html.split('id="panel-workspace"', 1)[1].split("</section>", 1)[0]
    assert 'id="ws-chat"' in workspace
    assert 'id="ws-message"' not in workspace and 'id="ws-ask"' not in workspace
    body = script[script.index("function hostChat(where)"):]
    body = body[:body.index("\n  //: The Studio's part sheet")]
    assert 'where === "planning" ? $("plan-chat")' in body
    assert 'where === "workspace" ? $("ws-chat")' in body


def test_the_part_sheet_is_the_let_me_adjust_tier_for_a_part(client):
    """"Its PartForge part-parameter panel moves into Let me adjust."

    Moved, not rebuilt: the same rail element, with the same ids, hosted in
    the Workspace's tools strip — and only for the tasks that actually author
    a part script, because a rail with nothing in it would be a panel that
    lies.
    """
    html = fetch_text(client, "/")
    workspace = html.split('id="panel-workspace"', 1)[1].split("</section>", 1)[0]
    strip = workspace[workspace.index('id="ws-stage-tools"'):]
    assert 'id="ws-adjust-rail"' in strip

    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function hostRail(where)"):]
    body = body[:body.index("\n  }\n") + 4]
    assert 'slot.appendChild(rail)' in body
    assert 'var PART_TASKS = ["part", "device"];' in script
    assert "hostRail(isPartTask() ? \"workspace\" : \"studio\")" in script
    # The rail still lives on the off-stage host when nothing is holding it,
    # so a move is a move home and back rather than a rebuild.
    assert 'id="panel-studio"' in html
    assert 'id="studio-rail"' in html


# ---------------------------------------------------------------------------
# 3 — the plain words, checked against the module that owns the ids
# ---------------------------------------------------------------------------

def test_every_stage_id_in_the_character_and_part_chains_has_plain_words():
    """The brief's pin: the build-plan ids stay canonical, the words sit beside.

    Read off ``forge_mcp/pipeline.py`` — the one writer of build-plan.json —
    so a stage added or renamed there fails here instead of drawing its raw id
    on the beginner's screen.
    """
    chains = template_stage_ids()
    words = stage_words()
    for task in ("character", "part"):
        assert task in chains, task
        missing = [ident for ident in chains[task] if ident not in words]
        assert not missing, "%s stages with no plain words: %s" % (task, missing)


def test_the_other_three_chains_are_covered_too():
    """Device, floor plan and mould are real tasks on Home's five cards."""
    chains = template_stage_ids()
    words = stage_words()
    missing = {}
    for task in ("device", "floorplan", "mold"):
        gap = [ident for ident in chains[task] if ident not in words]
        if gap:
            missing[task] = gap
    assert not missing, missing


def test_the_words_table_invents_no_stage_that_does_not_exist():
    """A mapping for an id no chain has is a word for nothing."""
    chains = template_stage_ids()
    every = set()
    for ids in chains.values():
        every.update(ids)
    extra = sorted(set(stage_words()) - every)
    assert not extra, "STAGE_WORDS names stages no template has: %s" % extra


def test_the_plain_words_are_actually_plain():
    """A name a beginner reads, and ONE line about what happens there."""
    for ident, entry in stage_words().items():
        name, does = entry["name"], entry["does"]
        assert name and does, ident
        assert "_" not in name, "%s is still an id, not a name" % ident
        assert name != ident, ident
        assert len(name) <= 22, "%s: %r is not a chip label" % (ident, name)
        # One line: one sentence, ending in a full stop, and short enough to
        # sit under a chip without becoming a paragraph.
        assert does.endswith("."), ident
        assert len(does) <= 120, "%s: the one-liner is %d chars" % (ident,
                                                                   len(does))


def test_the_chips_say_the_plain_name_and_the_hover_says_the_id(client):
    """The id is never hidden — it is what the plan is written in."""
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function stepChip("):]
    body = body[:body.index("function renderPipeline(")]
    assert 'el("span", "ws-step-name", words.name)' in body
    assert "stage.id" in body          # still on the hover, and on the dataset


# ---------------------------------------------------------------------------
# 4 — the big three, composed for real
# ---------------------------------------------------------------------------

def wssend_argument(marker):
    """The expression app.js hands ``wsSend`` for one button, as JS source.

    Sliced rather than retyped: what is evaluated below is the page's own
    sentence, so a reworded button is a failure here instead of a turn nobody
    checked.
    """
    script = app_js()
    at = script.index(marker)
    start = script.index("wsSend(", at) + len("wsSend(")
    depth = 1
    for index in range(start, len(script)):
        char = script[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return script[start:index]
    raise AssertionError("unbalanced wsSend( after %r" % marker)


def run_js(tmp_path, source):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to evaluate the page's own source")
    harness = tmp_path / "compose.js"
    harness.write_text(source, encoding="utf-8")
    done = subprocess.run([node, str(harness)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def compose(tmp_path, marker, extra=""):
    """Evaluate one button's composed sentence with the board's own values."""
    expression = wssend_argument(marker)
    # The sentence's free variables, bound to what the board would hand it.
    return run_js(tmp_path, (
        'var stage = { id: "skin", status: "passed" };\n'
        'var words = { name: "Attach skin" };\n'
        'var project = "werewolf";\n'
        '%s\n'
        'var parts = [%s];\n'
        'process.stdout.write(String(parts[0]));\n'
    ) % (extra, expression))


def test_looks_good_composes_a_pipeline_advance_turn(client, tmp_path):
    """"Looks good" advances the build the way the decision cards do.

    The bridge does NOT write build-plan.json — pipeline.py owns it, refuses a
    skip, and will not take an override unsigned — so the button composes the
    sentence and sends it down the same ``/ask`` the composer uses.  Evaluated
    here, then sent, then read back off the CLI's argv: the sentence reaches
    the model exactly as the page wrote it.
    """
    sentence = compose(tmp_path, 'var good = el("button", "btn ws-big-act is-good"')
    assert "pipeline_advance" in sentence
    assert "skin" in sentence and "Attach skin" in sentence
    assert "werewolf" in sentence
    assert "Do not record a pass you did not measure." in sentence

    status, body = client.request("/ask", {"message": sentence})
    assert status == 200, body
    assert sentence in last_prompt(client)


def test_the_scoped_change_box_names_the_stage_and_its_scope(client, tmp_path):
    """"Make the boots chunkier" typed at the rig stage is a stage-scoped turn.

    So the sentence says which stage it is about, says the other stages are
    not to be touched, and asks for that stage's gate to be re-measured and
    recorded — never for a pass nobody measured.
    """
    sentence = compose(tmp_path, "function scopeBox(stage, data)",
                       extra='var said = "make the boots chunkier";')
    assert "make the boots chunkier" in sentence
    assert "at the skin stage (Attach skin)" in sentence
    assert "do not touch the other stages" in sentence.lower()
    assert "re-measure the skin gate" in sentence
    assert "pipeline_record" in sentence

    status, body = client.request("/ask", {"message": sentence})
    assert status == 200, body
    assert sentence in last_prompt(client)


def test_fix_these_runs_this_stages_own_check_and_counts_its_pins(client):
    """"Fix these (N pins)" — the existing inspect, led by a plain button.

    N is this stage's own findings and only while the strip is still on this
    stage: a count carried over from another stage would be a number about the
    wrong thing.  A stage with no check of its own asks instead, and says so.
    """
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function bigThree(stage, data)"):]
    body = body[:body.index("function scopeBox(")]
    assert "var pins = (tools.stage === stage.id) ? tools.findings.length : 0;" \
        in body
    assert '" pin)" : " pins)"' in body
    assert "wsInspect();" in body
    assert "var checkable = !!TOOL_STAGES[stage.id];" in body
    # …and the label is redrawn once the check has run, or the button would
    # still say "Fix these" over three pins it just put on the model.
    inspect = script[script.index("function wsInspect()"):]
    inspect = inspect[:inspect.index("function toolsNoteText(")]
    assert "refreshFocus();" in inspect
    assert "function refreshFocus()" in script
    # …and the three are in the flow doc's order.
    assert body.index('"Looks good"') < body.index('"Fix these"')
    assert body.index('"Fix these"') < body.index('"Let me adjust"')


def test_let_me_adjust_opens_the_tier_that_already_existed(client):
    """Nudge, brush, sliders and the part sheet — re-led, not rebuilt.

    The strip used to appear on its own for the four stages that have a check.
    Now a button opens it, and it stays open while there are pins to work
    through, so nothing that was reachable stopped being reachable.
    """
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function renderStageTools()"):]
    body = body[:body.index("\n  //: Called whenever the stepper's focus moves")]
    assert "if (!stage || !(tools.adjust || tools.findings.length))" in body
    for kept in ("renderAnimateTools(host)", "renderSkinTools(host)",
                 "renderFindings()"):
        assert kept in body, kept
    assert "function wsOpenAdjust()" in script
    assert "function wsCloseAdjust()" in script
    # Moving the stepper closes it: nobody arrives at a new step with the last
    # one's controls up.
    follow = script[script.index("function stageToolsFollow(stage)"):]
    follow = follow[:follow.index("\n  // -- loading")]
    assert "tools.adjust = false;" in follow


def test_the_focused_stage_leads_with_one_plain_sentence(client):
    """What was measured, in words, BEFORE the numbers and before the actions.

    And every number in that sentence is a count of what the board holds — the
    honesty rule this screen has had since it was built: nothing here is
    invented, and a stage with nothing recorded says so.
    """
    script = fetch_text(client, "/webui/app.js")
    body = script[script.index("function stageSummary(stage, dirty)"):]
    body = body[:body.index("function stepChip(")]
    assert "(stage.numbers || []).length" in body
    assert "has not happened yet" in body
    assert "nothing recorded against it" in body
    # The dirty flag rides it: numbers about a model that no longer exists
    # must not be read as numbers about this one.
    assert "no longer exists" in body

    panel = script[script.index("function stagePanel(stage, data)"):]
    panel = panel[:panel.index("\n  // -- decisions")]
    assert panel.index("stageSummary(stage, dirty)") < panel.index("bigThree(")
    assert panel.index("bigThree(") < panel.index("scopeBox(")


def test_nothing_the_stage_panel_showed_was_deleted(client):
    """"Nothing is deleted from the workspace, only re-led."

    The plan's own title, the stage id, its `does`, its gate names and every
    measurement are all still on the panel — one line down, behind a summary,
    rather than leading it.
    """
    script = fetch_text(client, "/webui/app.js")
    panel = script[script.index("function stagePanel(stage, data)"):]
    panel = panel[:panel.index("\n  // -- decisions")]
    for kept in ("stage.title || stage.id", '"Stage id: " + stage.id',
                 '"Gate: " + stage.gate.join(", ")', "numberList(stage, true)",
                 "decisionBlock(stage, data)"):
        assert kept in panel, kept
    # The drawers, the nudge and the heatmaps are untouched elsewhere.
    for kept in ("function wsToggleNudge(", "function renderVersions(",
                 "function renderDeliverables(", "function wsSnapshot("):
        assert kept in script, kept


# ---------------------------------------------------------------------------
# 5 — the beginner bar
# ---------------------------------------------------------------------------

def test_escape_backs_out_of_every_screen_and_takes_no_modifier(client):
    """Out of the controls, out of the room, out of a half-typed prompt.

    And never out of a box somebody is typing in: Escape leaves the field
    first, which is what every other text field on this machine does.
    """
    script = fetch_text(client, "/webui/app.js")
    body = script[script.rindex('if (event.key !== "Escape") { return; }'):]
    assert "event.ctrlKey || event.altKey || event.metaKey || event.shiftKey" \
        in body
    assert "wsCloseAdjust();" in body
    assert body.index('$("panel-workspace").hidden') < \
        body.index('$("panel-planning").hidden')
    assert 'showTab("home")' in body
    # A placement in progress owns Escape; one keypress does one thing.
    assert 'if (!$("ws-nudge-bar").hidden) { return; }' in body


def test_no_shortcut_on_these_screens_needs_a_modifier(client):
    """The beginner bar: no Ctrl, no Alt, no Cmd anywhere in the key handling."""
    script = fetch_text(client, "/webui/app.js")
    for handler in re.findall(r'addEventListener\("keydown"[\s\S]{0,900}?\n    \}\);',
                              script):
        if "Escape" in handler:
            continue
        # Enter-to-send is the only other gesture, and its modifiers are the
        # ones it REFUSES, never ones it requires.
        for guard in ("event.ctrlKey", "event.altKey", "event.metaKey"):
            if guard in handler:
                assert "!" + guard in handler or "&& !" in handler, handler


def test_one_caption_per_section_on_the_screens_that_changed(client):
    """The beginner bar's other rule, held through the fold-ins."""
    html = fetch_text(client, "/")
    home = html[html.index('id="panel-home"'):html.index('id="panel-planning"')]
    drawer = home[home.index('id="home-files"'):]
    assert drawer.count('class="muted small"') == 1
    shelf = home[home.index('id="home-library-block"'):home.index('id="home-files"')]
    assert shelf.count('class="muted small"') == 1


# ---------------------------------------------------------------------------
# 6 — the phone: the overflow the owner reported, and what caused it
# ---------------------------------------------------------------------------

def test_the_tab_row_that_overflowed_a_phone_is_three_buttons(client):
    """Six tabs did not fit 375 px.  Three do, and there is no scroller.

    The fix is the shape, not a horizontal scrollbar bolted onto the old one —
    so the row is pinned at three and the stylesheet is pinned as having no
    overflow-x escape hatch on it.
    """
    html = fetch_text(client, "/")
    nav = html[html.index('<nav class="tabs"'):html.index("</nav>")]
    assert len(re.findall(r'<button\b', nav)) == 3
    css = fetch_text(client, "/webui/app.css")
    block = css[css.index(".tabs {"):]
    block = block[:block.index("}")]
    assert "overflow-x" not in block


def test_every_grid_on_these_screens_fits_inside_a_phone(client):
    """A grid whose narrowest column is wider than the screen IS the overflow.

    So every ``minmax(<floor>, …)`` that can be in force at phone width has a
    floor that fits inside 375 px minus the 20 px gutters — and the only rule
    that breaks that (the Workspace's two desktop columns) is inside a
    max-width query that has already collapsed it to one column by then.
    """
    css = fetch_text(client, "/webui/app.css")
    gutters = 40
    wide = []
    for match in re.finditer(r"minmax\((\d+)px", css):
        floor = int(match.group(1))
        if floor <= PHONE_PX - gutters:
            continue
        # Which rule is it in?  The selector is what sits between the previous
        # closing brace and this rule's opening one.
        head = css.rfind("{", 0, match.start())
        selector = css[css.rfind("}", 0, head) + 1:head].strip().splitlines()[-1]
        wide.append((selector.strip(), floor))
    assert sorted(wide) == [(".ws-body", 340), (".ws-body", 360)], wide
    # …and it is one column well before a phone.  The Studio's own two columns
    # have a 330 px floor, which fits, and stack at 1100 px anyway.
    block = css[css.index("@media (max-width: 1000px)"):]
    block = block[:block.index("\n}\n") + 3]
    assert ".ws-body" in block and "grid-template-columns: 1fr;" in block
    stacked = css[css.index("@media (max-width: 1100px)"):]
    stacked = stacked[:stacked.index("\n}\n") + 3]
    assert ".studio { display: flex; flex-direction: column; }" in stacked


def test_the_stepper_and_the_flow_row_wrap_rather_than_run_off_the_side(client):
    """Ten stage chips on a phone are ten rows, not one row somewhere to the
    right of the screen."""
    css = fetch_text(client, "/webui/app.css")
    for selector in (".ws-stepper {", ".flowbar {", ".ws-big {"):
        block = css[css.index(selector):]
        block = block[:block.index("}")]
        assert "flex-wrap: wrap" in block, selector
