"""Forge Assistant bridge — the thin local layer between Blender and Claude Code.

The Blender add-on cannot spawn a long-running subprocess and poll it without
blocking the UI, and it must stay dependency-free.  So this process sits in the
middle: plain HTTP on ``127.0.0.1:8901``, one job at a time, each job a headless
``claude -p`` run in the Forge repo with the Forge MCP tools allowed and the
product philosophy appended to the system prompt.

Standard library only, on purpose.  It has to run under whatever Python the
artist happens to have (or under the geometry service's venv) with no install
step, because the whole point of ``start_forge.cmd`` is that it is a
double-click.

Endpoints
---------
``GET  /health``        -> ``{"status", "claude_cli": {"found", "path", "version"},
                            "busy", "queued", "session_cost_usd", "last_auth_error",
                            "build": {"sha", "pid", "started", "uptime_s"},
                            "config": {"projects_dir", "models_dirs", "model",
                                       "timeout_s", "stall_timeout_s",
                                       "blender": {"host", "port"},
                                       ..., "env_set": [names only]}}``
``POST /ask``           -> ``{"job_id", "state": "running"|"queued"}``
                           (409 only when a message is ALREADY waiting;
                            optional ``"model": "haiku"|"sonnet"|"opus"``)
``GET  /job/<id>``      -> ``{"state", "activity", "session_cost_usd", "reply"?, ...}``
``POST /cancel/<id>``   -> ``{"state": "cancelled"}``

Live context (the copilot's eyes)
---------------------------------
``/ask`` — and only ``/ask``, because the other routes are not conversations —
opens every turn with one glance at the add-on socket: ``get_activity`` for what
the artist has done since this conversation last looked, plus the mode, the
active object, the selection and the 3D cursor that come back in the same call.
It becomes one ``[Blender now] ...`` line in the prompt, capped at
:data:`LIVE_CONTEXT_BUDGET` characters, newest first.  It is bounded in time
(:data:`LIVE_CONTEXT_TIMEOUT`), it says "Blender is not running" out loud rather
than silently losing the scene, it degrades to ``get_scene_info`` alone on an
add-on that predates the feed, and **it can never fail a turn**: every exception
on that path is swallowed and costs the turn nothing but the block.

Phase 9 — the web UI's routes (all additive; the panel never calls them)::

``GET  /``                 the single-page app in ``assistant/webui/``
``GET  /webui/<asset>``    its css/js, served from that folder and nowhere else
``GET  /jobs``             every job still in memory, so a page load can draw
                           the conversation it missed
``GET  /file/<token>``     one file this bridge itself recorded on a job
``GET  /services/health``  the other localhost services, probed server-side
``POST /upload``           an image from the browser -> a path on this machine
``POST /services/start``   shells ``start_forge.ps1`` (probe-first, hidden)
``POST /flows``            passthrough to Blender's ``flow_list``
``POST /flows/run``        passthrough to Blender's ``flow_run``

Phase 11 — the workbench's routes (also additive; also model-free)::

``GET  /projects``                     every folder in ``FORGE_PROJECTS_DIR``,
                                       with its ``spec.json`` and its part script
``GET  /projects/<name>/schema``       that script's ``PARAMS``, resolved by the
                                       geometry service's ``/parse_params`` and
                                       cached against the file's mtime
``POST /projects/<name>/set_params``   ``/generate`` with the artist's values,
                                       then ``load_mesh`` with ``replace`` — the
                                       same chain as ``partforge_generate``
``POST /preview``                      ``render_preview`` to a path *this* bridge
                                       chooses, handed back as a ``/file`` token
``GET/POST /scene``                    ``get_scene_info``, verbatim
``POST /scene/delete``                 ``delete_object`` (undoable in Blender)

Phase 13 — the library's routes (additive again, and model-free again)::

``GET  /library``                      every project on disk as a card — spec
                                       description, parameter count, components,
                                       exports with sizes — plus a ``scene``
                                       block of what Blender is holding right
                                       now, so works in progress show up beside
                                       the saved parts
``GET  /projects/<name>/thumbnail``    the cached PNG for one project (404 when
                                       there is none; the page draws a
                                       placeholder)
``POST /projects/<name>/thumbnail``    photograph the project *as it stands in
                                       the scene* and cache the PNG.  409 when
                                       the part is not in the scene — a
                                       thumbnail is a picture of the artist's
                                       work, never a reason to build something
                                       behind their back

Phase 15 — the project's own ``.blend`` (additive, and model-free again)::

``POST /projects/<name>/save``         ``save_project_blend`` in the running
                                       Blender: the scene goes to
                                       ``projects/<name>/<name>.blend`` as a
                                       **copy**, so where the artist's own
                                       Ctrl+S goes never moves
``POST /projects/<name>/open``         "clicking a model opens its Blender
                                       file", in three routes: Blender running
                                       -> ``open_project_blend`` with the
                                       confirmation round trip surfaced;
                                       Blender closed and a ``.blend`` on disk
                                       -> spawn a windowed Blender on it; no
                                       ``.blend`` -> say so, and the page falls
                                       back to Open-in-Studio plus an offer to
                                       save.  A running instance **always**
                                       wins: two Blenders would fight over port
                                       9876

Phase 13, revisited — the library's models (the artist: "library is still not
showing all my actual 3d models")::

``GET  /library``                      gains a ``models`` section: the ``.glb``
                                       files the meshgen backends wrote (the
                                       output folder ``meshgen/config.json``
                                       names, plus anything in
                                       ``FORGE_MODELS_DIRS``) and every mesh in
                                       ``projects/<name>/models/``.  A ``stat``
                                       each and nothing more, so this row draws
                                       with every service on the machine stopped
``POST /models/import``                ``import_generated`` in the running
                                       Blender, repaired — the generated mesh
                                       lands in the scene.  503 with the
                                       panel's own sentence when Blender is down
``POST /models/file``                  copy one indexed model into
                                       ``projects/<slug>/models/``, so a mesh
                                       that was born in a scratch folder gets a
                                       home beside the part it belongs to.  The
                                       source must be a file ``GET /library``
                                       already indexes: this route files the
                                       artist's own models, it is not a way to
                                       copy arbitrary paths into the repo
Phase 18 — the workspace's routes (additive, and four of the five are pure
reads of files that already exist).  The artist: "we should see status updates,
deliverables, check progress, different versions, make decisions with UI rather
than chatting ... right now we have two separate windows forge and blender and
we can't really follow along."  Every one answers on ``/projects/<name>/...``
and on ``/project/<name>/...``::

``GET  /projects/<name>/versions``     the numbered ``.blend`` chain in
                                       ``models/`` — ``werewolf-wip.blend`` is
                                       version 1, ``werewolf-wip-10.blend`` is
                                       version 10 and sorts AFTER version 9,
                                       each with its size, its date and a
                                       still out of ``renders/`` when one is
                                       named after it
``POST /projects/<name>/versions/restore``
                                       ``{"file": "werewolf-wip-7.blend"}`` ->
                                       a COPY at the end of the chain
                                       (``werewolf-wip-11.blend``).  Nothing is
                                       overwritten and nothing is deleted:
                                       "restore" means "make this one the
                                       newest", and the only way to do that
                                       without losing work is a copy
``GET  /projects/<name>/pipeline``     ``design/build-plan.json`` as a stage
                                       board — the chain, each stage's verdict,
                                       the gate's actual measurements and the
                                       id of the first red one.  A READER, and
                                       permanently: ``forge_mcp.pipeline`` owns
                                       that file, it refuses a skip and it will
                                       not take an override without a name and
                                       a reason on it.  The board's buttons
                                       send a sentence to ``/ask`` and the
                                       assistant advances the plan through the
                                       tool that knows those rules
``GET  /projects/<name>/deliverables`` everything in ``renders/`` worth looking
                                       at — stills and films together, newest
                                       first, each as a ``/file`` token
``POST /projects/<name>/snapshot``     the live Blender scene exported to one
                                       ``.glb`` in the previews folder and
                                       handed back as a token, so the page can
                                       orbit what Blender is holding.  503 with
                                       the usual sentence when Blender is down

``POST /models/open``                  what a click on the card means — the
                                       model, in Blender, whichever state the
                                       machine is in.  Blender running:
                                       ``import_generated`` into the scene they
                                       are looking at (a running instance always
                                       wins — two would fight over port 9876).
                                       Blender closed: a windowed Blender is
                                       started with the mesh imported into an
                                       empty scene, through ``--python-expr``
                                       because a ``.glb`` is not a ``.blend``
                                       and cannot be opened as one.  No Blender
                                       on the machine: a 501 naming the menu
                                       (File > Import, not File > Open)

Phase 16 — the design phase (no new routes; two existing ones grew)::

``GET  /file/<token>``                 also serves ``.svg`` as
                                       ``image/svg+xml``, so the hand-written
                                       concept diagram a design turn produced
                                       draws inline in the conversation rather
                                       than arriving as a path.  Markup is the
                                       one servable type that can contain code,
                                       so an SVG response carries a
                                       ``Content-Security-Policy`` that
                                       sandboxes it — these diagrams are
                                       self-authored, and this is belt and
                                       braces on top of that
``GET  /library``                      cards gain ``design`` (the requirements
                                       sheet, the concept diagram, the
                                       components list, in reading order) and
                                       ``design_only``.  A project that is a
                                       design sheet with no ``part.py`` yet is a
                                       card: that is exactly the state the
                                       sign-off gate holds, and a shelf that
                                       hid it would hide the thing the artist is
                                       being asked to approve

Phase 17 — mechanism demos (no new routes; the same two grew again)::

``GET  /file/<token>``                 also serves ``.mp4`` as ``video/mp4``,
                                       so the two-second film ``render_animation``
                                       wrote — the plunger pressing, the LED
                                       coming on — plays inline in the
                                       conversation.  A video is its own display
                                       class (``DISPLAY_VIDEO_EXTENSIONS``), never
                                       an attachment: it travels out of a job and
                                       never into one
``GET  /library``                      cards gain ``demos`` — the ``.mp4`` files
                                       in ``projects/<name>/renders/``, which is
                                       where the MCP mirror of ``render_animation``
                                       writes them.  Tokens, like the Models row,
                                       because a demo nobody can press play on is
                                       a filename

None of these spends a model turn: the workbench tab is the artist editing a
part directly, and a slider that costs money per drag is a slider nobody drags.
The bridge never *executes* the artist's script — that is the geometry service's
job, in its own process, with its own venv.

One waiting message (Phase 6e)
------------------------------
Exactly one turn ever runs at a time — two in flight would fight over
``--resume`` — but a turn takes tens of seconds and an artist who has thought of
the next thing should not have to sit on their hands.  So a second ``/ask``
while a job is running is *queued* rather than refused: it gets a real job id
immediately (``state: "queued"``, pollable at once) and starts by itself when
the running turn ends, however it ended.  A *third* is the 409, because a queue
of one is a courtesy and a queue of ten is a way to lose track of what you asked
for.

The queued message carries the prompt built when the artist pressed Send — the
scene context they were looking at — but resolves the session id at pickup time,
so it continues the conversation the turn ahead of it produced.

Activity (Phase 6b)
-------------------
The CLI runs with ``--output-format stream-json --verbose
--include-partial-messages``, so its stdout is newline-delimited JSON events
rather than one object at the end.  Those events are parsed as they arrive into
``job["activity"]`` — ``[{"t": epoch, "kind": "tool"|"text"|"status", "label"}]``
— which ``GET /job/<id>`` returns on every poll.  The panel draws the last few
lines under the busy indicator so the artist can see what the AI is doing
instead of watching a spinner.

Choosing the model per message
------------------------------
``POST /ask`` may carry ``"model": "haiku"|"sonnet"|"opus"`` — the artist's
speed-versus-depth choice, made in the panel rather than in an environment
variable they will never find.  Anything else is a 400 before a turn is spent.
The order is: the request's model, then ``FORGE_ASSISTANT_MODEL``, then no
``--model`` flag at all (the CLI's own default).

Switching model mid-conversation is fine and needs no special handling: the next
turn still carries ``--resume``, and the CLI continues the same conversation
under the newly named model.  So an artist can ask Haiku for four quick exports
and then hand the same thread to Opus for the tricky bit.

Reference images (Phase 6c)
---------------------------
``POST /ask`` may carry ``context.image_path`` — an absolute path to a sketch or
photo the artist attached in the panel.  The path is validated here (it exists,
it is one of :data:`IMAGE_EXTENSIONS`) and appended to the message body as its
own block telling the model to Read it before answering.  No allow-list change:
``Read`` is already permitted and Claude Code's Read tool renders images.  The
image itself never passes through this process — only its path.

Parsing is deliberately forgiving: an event shape this bridge does not
recognise is skipped, never fatal, and a run that never prints a ``result``
event but exits 0 is salvaged from the last parseable line (falling back to the
text that streamed past).

How a turn ends
---------------
``job["state"]`` is one of four, and all four are TERMINAL — a client that polls
``GET /job/<id>`` must stop on every one of them::

    done       it answered
    error      it could not
    cancelled  the artist pressed Stop
    timeout    it ran out of clock, and here is how far it got

``timeout`` is the Phase-17 addition and it is deliberately not ``error``.  The
dogfood run (docs/dogfood-litwick-2026-09-16.md, B-1) lost a fifteen-minute turn
that had already written a 441-line ``part.py``, generated it, taken a
printability refusal, fixed it, re-checked and rendered it twice: the artist was
shown one sentence saying it "was stopped", no reply, and a $0.00 bill.  Now the
whole stream is accumulated as it arrives (:class:`ActivityRecorder`), so a turn
that is killed still lands its text, the steps it completed, the files it wrote
and whatever cost the stream mentioned, followed by one honest line saying what
stopped it — see :func:`checkpoint_reply`.

There are two clocks behind it.  ``FORGE_ASSISTANT_TIMEOUT`` is the turn budget;
``FORGE_ASSISTANT_STALL_TIMEOUT`` is how long the CLI may say *nothing* before it
is treated as hung.  They produce different sentences because they are different
diagnoses: a turn killed while working may genuinely be too big, and is told so;
a turn killed for going silent would have stalled at any size, and telling that
artist to ask for less would be blaming them for a hang.

Environment
-----------
``FORGE_ASSISTANT_PORT``     listen port (default 8901)
``FORGE_ASSISTANT_CLAUDE``   full path to the claude executable; skips discovery.
                             The test suite points this at a fake CLI.
``FORGE_ASSISTANT_MODEL``    fallback ``--model`` value, used only when the
                             request named none; unset = the CLI's own default
``FORGE_ASSISTANT_TIMEOUT``  seconds per turn (default 600).  Reaching it is no
                             longer a failure: the turn lands as ``timeout``
                             carrying everything the model said and did
``FORGE_ASSISTANT_STALL_TIMEOUT``  seconds of TOTAL silence from the CLI before
                             the turn is stopped as hung (default 300; 0 off).
                             A separate clock from the one above, because a
                             process that has said nothing for five minutes is
                             not a big request, it is a hang
``FORGE_FORCE_START``        ``1`` skips the "something is already answering on
                             this port" guard and binds anyway
``FORGE_ASSISTANT_TOOLS``    ``--allowedTools`` value; unset = the Forge default.
                             Set it to the empty string for a no-tools run.
``FORGE_ASSISTANT_CWD``      working directory for the CLI (default: the repo root)
``FORGE_ASSISTANT_TEXT_INTERVAL``  seconds between text activity markers
                             (default 2.0; 0 = every chunk, for the tests)

Web UI environment (Phase 9)
----------------------------
``FORGE_ASSISTANT_UPLOADS``  where ``POST /upload`` writes (default
                             ``assistant/uploads``)
``FORGE_START_SCRIPT``       the script ``POST /services/start`` shells
                             (default ``start_forge.ps1`` at the repo root; a
                             ``.py`` path runs under this interpreter, which is
                             how the tests avoid needing PowerShell)
``FORGE_BLENDER_HOST`` / ``FORGE_BLENDER_PORT``   the add-on socket the flows
                             passthrough — and the per-turn live glance — talk
                             to (default 127.0.0.1:9876)
``FORGE_ASSISTANT_LIVE_CONTEXT``  ``0`` switches the per-turn glance off
                             entirely (default on).  For a harness that must
                             open no connection to the add-on's port, and for a
                             machine where Blender is slow enough that the wait
                             is worse than the blindness
``FORGE_SERVICE_URL``        geometry service, for ``/services/health``
                             (default http://127.0.0.1:8765)
``FORGE_MESHGEN_URL``        meshgen service, likewise (default 8902)
``FORGE_PROJECTS_DIR``       the parametric projects the workbench lists
                             (default ``<repo>/projects``; same variable the MCP
                             server's ``partforge_new_part`` writes into)
``FORGE_ASSISTANT_PREVIEWS`` where ``POST /preview`` writes its PNGs (default a
                             ``forge-webui-previews`` folder in the temp dir)
``FORGE_GENERATE_TIMEOUT``   seconds the workbench waits for one ``/generate``
                             (default 300)
``FORGE_ASSISTANT_THUMBS``   the library's thumbnail cache (default
                             ``assistant/thumbs``, beside ``uploads``)
``FORGE_BLENDER_EXE``        full path to ``blender.exe``, for the one case
                             where this bridge *starts* Blender rather than
                             talking to it (``POST /projects/<name>/open`` with
                             nothing listening).  Unset = PATH, then the
                             official installer's own folders
``FORGE_MESHGEN_OUTPUT_DIR`` where the meshgen backends write their ``.glb``.
                             Unset (the normal case) means read it out of
                             ``meshgen/config.json``, which is the one file that
                             has to be edited when the 19 GB model install moves
                             — the same variable meshgen's own config honours, so
                             the two halves cannot disagree
``FORGE_MODELS_DIRS``        extra folders the library's models row indexes,
                             separated by ``;``.  For meshes that came from
                             somewhere else entirely — a download, another
                             machine, a scratch export folder
"""

import base64
import binascii
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
SYSTEM_PROMPT_PATH = os.path.join(HERE, "system_prompt.md")


# ---------------------------------------------------------------------------
# version truth — what is actually running, decided once at process start
# ---------------------------------------------------------------------------
#
# A long-lived localhost service is a ghost waiting to happen: the artist pulls,
# edits, restarts two of the three processes and the third keeps answering with
# last week's code.  Nothing in a health payload used to distinguish that from a
# working machine.  So every Forge service now states, on ``/health``, the git
# short-SHA of the tree it was STARTED from, its pid, and when it started, and
# the add-on compares that SHA against the repo's HEAD.  Three identical keys,
# one meaning, documented in docs/architecture.md.
#
# The SHA is read ONCE, here, at import — not lazily on the first request.  That
# is the whole point: a checkout made after the process started must NOT change
# what the process says about itself, or a stale service would quietly relabel
# itself current the moment anybody pulled.

#: ``CREATE_NO_WINDOW`` where it exists (Windows), 0 elsewhere.  Law of the
#: house: nothing Forge runs may flash a console window.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git_short_sha(root, timeout=5.0):
    """``git rev-parse --short HEAD`` for *root*, or ``"unknown"``.

    Never raises and never fails a startup.  A zip install, a machine without
    git, a detached worktree, a git that hangs on a network drive — every one of
    those is "unknown", which the add-on reads as "cannot judge" rather than as
    "stale".  Silence would be worse than an honest unknown.
    """
    try:
        proc = subprocess.Popen(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root or None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW)
    except Exception:  # noqa: BLE001 - no git, no repo, no permission
        return "unknown"
    try:
        out, _err = proc.communicate(timeout=timeout)
    except Exception:  # noqa: BLE001 - includes TimeoutExpired
        try:
            proc.kill()
            proc.communicate(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    text = (out or b"").decode("utf-8", "replace").strip()
    return text or "unknown"


#: Captured at import, before anything can be edited underneath us.
BUILD_SHA = git_short_sha(REPO_ROOT)
BUILD_STARTED_AT = time.time()


def _iso_utc(stamp):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def build_info():
    """The ``build`` block every Forge ``/health`` carries. Same keys everywhere."""
    return {
        "sha": BUILD_SHA,
        "pid": os.getpid(),
        "started": _iso_utc(BUILD_STARTED_AT),
        "uptime_s": round(max(0.0, time.time() - BUILD_STARTED_AT), 3),
    }


DEFAULT_PORT = 8901
DEFAULT_TIMEOUT = 600.0
#: How long the CLI may say NOTHING — no event, no byte — before the turn is
#: stopped.  The dogfood run's turn 3 spent 595 of its 900 seconds in dead air
#: and then did the whole job in the five minutes that were left, so the turn
#: budget was never the constraint: a hung process was.  A stall is killed on
#: its own clock so the rest of the budget is still there to work in.
DEFAULT_STALL_TIMEOUT = 300.0

#: Read-only repo access plus every Forge MCP tool.  The server name comes from
#: ``.mcp.json`` at the repo root ("forge"), so the wildcard is ``mcp__forge__*``.
MCP_SERVER = "forge"
DEFAULT_ALLOWED_TOOLS = "Read,Glob,Grep,mcp__%s__*" % MCP_SERVER

#: Permission modes tried in order.  Older CLI builds reject "auto"; rather than
#: sniffing the version we let the process tell us and step down.
PERMISSION_MODES = ("auto", "acceptEdits", None)

CONTEXT_DIVIDER = "--- Current Blender context ---"

#: Live context (Phase 19b).  The panel's context block says what the artist was
#: looking at when they pressed Send; this one says what Blender *is*, read off
#: the add-on socket a moment before the turn starts, and what has changed since
#: the assistant last looked.  It is one marked line rather than a divider block
#: on purpose: it is an observation, not part of the artist's message, and it
#: should read as a glance rather than as an instruction.
LIVE_CONTEXT_MARKER = "[Blender now]"

#: Hard ceiling on the whole block, in characters.  Every turn pays for this, in
#: tokens and in the model's attention, so the budget is the design: newest first
#: and the older half dropped with a count, rather than a scrolling log that
#: crowds out the thing the artist actually typed.
LIVE_CONTEXT_BUDGET = 600

#: How many events to ask the add-on for.  More than fits the budget, so the
#: trimming decision is made here against real text rather than guessed at.
LIVE_CONTEXT_LIMIT = 16

#: The probe's whole time allowance, per socket call.  A copilot's awareness is
#: not worth a second of the artist's wait, and the turn must start whether or
#: not Blender answered — so this is short and every failure is swallowed.
LIVE_CONTEXT_TIMEOUT = 1.0

#: How long to wait for the port to accept a connection before giving up on the
#: whole probe.  Deliberately shorter than the command timeout: "nothing is
#: listening" is the common case when Blender is closed, and it should be cheap.
LIVE_CONTEXT_CONNECT_TIMEOUT = 0.4

#: What the block says when the socket is down.  Said out loud rather than
#: omitted: "Blender is not running" is itself context, and an assistant that
#: silently loses the scene will happily talk about objects that are not there.
LIVE_CONTEXT_DOWN = "Blender is not running, so there is no live scene to see."

#: ...and when the port accepts but the add-on says nothing in time — a busy
#: main thread, usually.  "I could not see" beats a scene read off nothing.
LIVE_CONTEXT_UNREADABLE = ("Blender is listening but did not answer in time, "
                           "so nothing below is live.")

#: ...and when the add-on answers but has no feed, which means an add-on built
#: before ``get_activity`` existed.  Named as an upgrade rather than a fault.
LIVE_CONTEXT_NO_FEED = ("no activity feed in this add-on build "
                        "(restart Blender with the current Forge add-on to see "
                        "what the artist has been doing)")

#: Blender's own mode strings, in the words an artist uses for them.  Anything
#: not in the table is passed through unchanged rather than guessed at.
MODE_WORDS = {
    "OBJECT": "Object",
    "EDIT_MESH": "Edit",
    "EDIT_CURVE": "Edit (curve)",
    "EDIT_SURFACE": "Edit (surface)",
    "EDIT_TEXT": "Edit (text)",
    "EDIT_ARMATURE": "Edit (armature)",
    "EDIT_METABALL": "Edit (metaball)",
    "EDIT_LATTICE": "Edit (lattice)",
    "EDIT_GREASE_PENCIL": "Edit (grease pencil)",
    "POSE": "Pose",
    "SCULPT": "Sculpt",
    "PAINT_WEIGHT": "Weight Paint",
    "PAINT_VERTEX": "Vertex Paint",
    "PAINT_TEXTURE": "Texture Paint",
    "PARTICLE": "Particle Edit",
}

#: An event ``kind`` in the words an artist would use for it.  The verb is the
#: whole point: "transformed" is a depsgraph flag, "moved" is what happened.
ACTIVITY_VERBS = {
    "transformed": "moved",
    "geometry": "edited",
    "added": "added",
    "removed": "deleted",
    "mode": "switched to",
    "undo": "undid a step",
    "redo": "redid a step",
}

#: Phase 6c.  An attached reference image rides in ``context.image_path`` and is
#: appended to the message body as its own block, at the very end, so it is the
#: last instruction the model reads before it starts working.
IMAGE_DIVIDER = "--- Attached reference image ---"
IMAGE_INSTRUCTION = "View this image with the Read tool BEFORE answering."

#: The three models the panel offers, fastest first.  Deliberately the CLI's own
#: aliases rather than pinned version ids: the artist is choosing "quick" or
#: "careful", and the CLI is the right place for that to mean a specific model.
MODELS = ("haiku", "sonnet", "opus")

#: What the Read tool can actually render.  The panel checks the same list, so a
#: bad attachment is refused in the sidebar rather than a turn later; this is the
#: server-side half of that, because the bridge is a public localhost endpoint.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

MAX_JOBS = 20

# ---------------------------------------------------------------------------
# Phase 9 — the web UI's constants
# ---------------------------------------------------------------------------

#: The single-page app.  Everything served under ``/webui/`` comes from here and
#: from nowhere else; see :func:`webui_asset`.
WEBUI_DIR = os.path.join(HERE, "webui")

#: What ``GET /webui/<asset>`` will serve, and as what.  A file type that is not
#: in this table is a 404 even if it sits in the folder — the browser has no use
#: for it and an unknown type is not worth guessing at.
ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

#: What a minted token may point at.  Images because the artist attached or the
#: assistant rendered them; ``.glb`` because that is what meshgen and the Godot
#: exporter write, and a browser that cannot show one can still download it;
#: ``.svg`` (Phase 16) because a concept diagram is written by the assistant
#: itself and is the one deliverable of the design phase that has to be LOOKED
#: at rather than read; ``.mp4`` (Phase 17) for the same reason one step further
#: on — a mechanism demo is a moving picture of the thing working, and a path to
#: it is homework.
SERVABLE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    # The file an artist actually prints.  The dogfood run ended with four
    # print STLs and two mold halves on disk and an EMPTY files list on both
    # jobs — the one deliverable the whole pipeline exists to produce was the
    # one thing the panel could not offer.  A download link, never a preview.
    ".stl": "model/stl",
}

#: Servable types that the page draws as a picture rather than offering as a
#: download.  Deliberately NOT :data:`IMAGE_EXTENSIONS`: that list is what the
#: artist may *attach* and what Claude Code's Read tool renders, and an SVG is
#: neither.  It travels the other way — out of a job, into the conversation.
DISPLAY_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS + (".svg",)

#: Servable types the page PLAYS.  Its own class rather than another entry on
#: the list above, because a ``<video>`` is not an ``<img>`` and — the reason
#: this is a class and not a special case — a video can never be an attachment:
#: Claude Code's Read tool renders bitmaps, so a film attached to a message
#: would be a turn spent watching the model fail to look at it.  ``.svg``'s
#: display-only precedent (Phase 16), one medium further along.
DISPLAY_VIDEO_EXTENSIONS = (".mp4",)

#: An SVG is markup, and markup served from this origin at its natural type is
#: a document a browser will happily run scripts in.  These diagrams are written
#: by the assistant, not uploaded by anyone, so this is not a hole anyone is
#: standing at — it is one sentence of belt and braces on the one servable type
#: that can contain code.  ``sandbox`` alone puts the response in an opaque
#: origin with scripts off; the rest says it may not fetch anything either.
SVG_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
           "sandbox")

#: How many path tokens stay live.  Ten per job for the last twenty jobs, with
#: room to spare; past this the oldest is forgotten and its ``/file`` 404s.
MAX_FILE_TOKENS = 400

#: A browser hands JavaScript a file's *content*, never its path, so an attached
#: image has to be uploaded before it can ride ``context.image_path`` the way
#: the panel's attachment does.  20 MB is far more than a reference photo needs
#: and far less than a way to fill someone's disk from a tab.
DEFAULT_MAX_UPLOAD_MB = 20
#: Uploads older than this many files are deleted when a new one arrives.
MAX_UPLOADS_KEPT = 200

#: The first bytes of each image type we accept.  Checked because an extension
#: is a claim and this writes a file to the artist's disk; a ``.png`` that is
#: not a PNG is refused here rather than discovered by the model a turn later.
IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}

#: The extensions a path scan will recognise — the keys of :data:`SERVABLE_TYPES`
#: spelled as a regex alternation, so the two can never drift apart.
_PATH_EXTENSIONS = r"png|jpe?g|webp|bmp|svg|mp4|glb|gltf|stl"

#: Absolute paths of servable files, wherever they appear in a reply, a tool
#: argument or a tool result.  Windows drive letters and UNC/POSIX roots both;
#: quotes, brackets and backticks end a path because markdown wraps them.
_FILE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"'`<>|*?\r\n]*?"
    r"\.(?:%s)\b" % _PATH_EXTENSIONS,
    re.IGNORECASE)

#: The same file named the way a model actually writes it in prose:
#: ``molds/litwick-flame-mold_mold_bottom.stl``, relative to the project it is
#: working in.  Deliberately a SEPARATE pattern rather than a loosening of the
#: one above, because a relative path is only a path if it resolves to a real
#: file — the existence check against a known base directory is what keeps
#: ``a.out/b.png``-shaped prose out of the gallery.  See
#: :func:`find_file_paths`.
_REL_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.:\\/-])"
    r"[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)*"
    r"\.(?:%s)\b" % _PATH_EXTENSIONS,
    re.IGNORECASE)

#: A path that carries its own root with it: a drive letter or a UNC share.
#: Everything else the absolute pattern can match starts with a single slash,
#: which on Windows is far more often the tail of a relative path than a root.
_ROOTED_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

#: How many base directories a relative path is tried against before the scan
#: gives up.  A bound, not a tuning knob: this runs on every tool result.
MAX_PATH_BASES = 24

#: How deep into a tool's arguments the path scan walks.
_SCAN_DEPTH = 4

#: The other localhost services the health strip draws.  The page cannot poll
#: them itself — a page served from 8901 asking 8765 is cross-origin — so the
#: bridge fans out server-side and answers with one object.
DEFAULT_SERVICE_URL = "http://127.0.0.1:8765"
DEFAULT_MESHGEN_URL = "http://127.0.0.1:8902"
DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 9876
#: A health probe is a dot on a strip: it must never make the page wait.
# 8.0, not 2.5: meshgen's /health measures ~2.83 s when ComfyUI is warm, and
# the dogfood run watched a healthy service reported "not running" 3/3 times.
HEALTH_TIMEOUT = 8.0
#: ``flow_list`` is a folder read; ``flow_run`` can be a five-minute segment.
FLOW_LIST_TIMEOUT = 20.0
DEFAULT_FLOW_RUN_TIMEOUT = 900.0
#: start_forge.ps1 probes three ports and waits up to 25 s for each.
START_SERVICES_TIMEOUT = 180.0

# ---------------------------------------------------------------------------
# Phase 11 — the workbench's constants
# ---------------------------------------------------------------------------

#: One plain folder name under ``projects/``.  Deliberately the same alphabet as
#: :data:`_ASSET_NAME_RE`: a percent sign is not in it, so an encoded traversal
#: fails on the alphabet rather than on path arithmetic.
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: A script stem this generic names the *folder*, not the object.  Verbatim from
#: docs/architecture.md's part-object naming convention, which both the add-on
#: and the MCP server implement; the workbench has to agree with them or Apply
#: replaces the mesh of an object nobody is looking at.
GENERIC_STEMS = ("part", "main", "model", "script", "build", "generate",
                 "__init__")
#: Blender's own object-name ceiling, in bytes.
MAX_OBJECT_NAME = 63

#: Which ``.py`` in a project folder is *the* part, when spec.json does not say.
PART_SCRIPT_PREFERENCE = ("part.py", "main.py", "model.py")

#: ``PARAMS = {`` at the top level — the PartForge script contract's one marker.
_PARAMS_RE = re.compile(r"^PARAMS\s*=", re.MULTILINE)

#: How long a parsed schema stays fresh.  Briefly on purpose: the artist edits
#: part.py in another window and presses Refresh, and waiting fifteen seconds
#: for their own edit to appear is the kind of bug nobody reports.  The file's
#: mtime invalidates it sooner anyway; this only stops a page that redraws twice
#: from parsing twice.
SCHEMA_CACHE_TTL = 15.0

#: ``/parse_params`` builds no geometry, so it is a read.  ``/generate`` runs the
#: artist's script through OCC and can be a minute on a fluted revolve.
PARSE_TIMEOUT = 60.0
DEFAULT_GENERATE_TIMEOUT = 300.0
#: A render is a render: fitted camera, Workbench clay, a PNG on disk.
PREVIEW_TIMEOUT = 180.0
#: ``get_scene_info`` / ``delete_object`` are one main-thread hop each.
SCENE_TIMEOUT = 30.0

#: How many preview PNGs stay on disk.  They are a cache of pictures, not work.
PREVIEWS_KEPT = 40

# ---------------------------------------------------------------------------
# Phase 13 — the library's constants
# ---------------------------------------------------------------------------

#: Where a project's finished files land, by the folder convention in
#: docs/architecture.md ("each project folder holds spec.json, part.py,
#: exports/ and renders").
EXPORTS_DIRNAME = "exports"
#: Where the design phase's own files land (Phase 16, and the MCP server's
#: `save_design_doc` — the two halves have to spell this folder the same way).
DESIGN_DIRNAME = "design"
#: The order a design sheet reads in, which is the order it is written in:
#: what it has to do, what it looks like, what it is made of.  Anything else
#: follows alphabetically.  Mirrors `forge_mcp.util.DESIGN_READING_ORDER`.
#: ``mechanism.svg`` (Phase 17) sits straight after the concept sketch: it is
#: the same drawing with the motion in it, so it is read second, not last.
DESIGN_READING_ORDER = ("requirements.md", "concept.svg", "mechanism.svg",
                        "floorplan.svg", "components.md")
#: How many design documents one card lists.  Same reason as the exports cap.
MAX_DESIGN_LISTED = 20

#: Where a project's renders live, by the same folder convention
#: (docs/architecture.md: "spec.json, part.py, exports/ and renders").  The MCP
#: mirror of ``render_animation`` picks ``projects/<name>/renders/`` for a demo
#: exactly so this row finds it without anybody moving a file.
DEMOS_DIRNAME = "renders"
#: What counts as a demo.  Only ``.mp4``: the still renders in the same folder
#: are the thumbnail's job, and a row of PNGs is a gallery nobody asked for.
DEMO_EXTENSIONS = (".mp4",)
#: How many demos one card plays.  A card is a card.
MAX_DEMOS_LISTED = 4
#: How many export files one card lists.  A card is a card; a folder with two
#: hundred STLs in it is a folder, and the count still tells the truth.
MAX_EXPORTS_LISTED = 40
#: How many components one card lists, for the same reason.
MAX_COMPONENTS_LISTED = 40

# ---------------------------------------------------------------------------
# Phase 13, revisited — the library's models row
# ---------------------------------------------------------------------------
#
# The gap, in the artist's words: "library is still not showing all my actual 3d
# models."  It was not: every generated mesh on this machine sat in the meshgen
# output folder, which has no project folder, no spec.json and therefore no card
# — four .glb files of real work, invisible to the one tab that exists to find
# work.  So the shelf indexes the FILES too, from the folders they are actually
# written to, and the two things an artist wants to do with one (put it in
# Blender, give it a home) are buttons rather than a paragraph about Explorer.

#: Where a project keeps the meshes it owns.  The MCP server spells this folder
#: the same way (``forge_mcp.util.PROJECT_MODELS_DIRNAME``) so a generation filed
#: at birth by ``generate_3d(project=...)`` lands where this row looks.
MODELS_DIRNAME = "models"

#: What counts as a model file.  ``.glb``/``.gltf`` are what the meshgen backends
#: and the Godot exporter write; ``.stl``/``.obj`` because a downloaded mesh the
#: artist dropped in ``projects/<name>/models/`` is their work too, and a shelf
#: that could only see the formats Forge itself writes would be back to hiding
#: things.
MODEL_EXTENSIONS = (".glb", ".gltf", ".stl", ".obj", ".blend")

#: A running Blender cannot be handed an arbitrary ``.blend`` safely from here:
#: opening one discards the scene the artist is looking at, and the guarded
#: confirmation flow (`open_project_blend`) is name-based, not path-based.  So a
#: click on a ``.blend`` card while Blender is up answers with the manual path
#: rather than pretending.  Blender closed, the file IS the positional argument
#: — the one case where a model file opens natively.
BLEND_RUNNING_HINT = (
    "Blender is already open. To protect any unsaved work there, open this "
    "file inside Blender itself: File > Open, then %s. Or close Blender and "
    "click the card again and it will launch straight into this file.")

#: ``meshgen/config.json`` — the one file that names every path into the 19 GB
#: model install ("relocating C:/forge-models means editing this file and nothing
#: else").  Read rather than hardcoded, so that promise stays true from here too.
MESHGEN_CONFIG_PATH = os.path.join(REPO_ROOT, "meshgen", "config.json")
MESHGEN_OUTPUT_KEY = "comfyui_output_dir"
#: Only if the file is missing or unreadable — the same default
#: ``meshgen/config.py`` carries, for the same reason.
DEFAULT_MESHGEN_OUTPUT_DIR = "C:/forge-models/comfyui-output"
#: The subfolder the backends actually write into: ComfyUI is launched with the
#: output directory above and every workflow's save node is prefixed
#: ``forge/<backend>`` (``meshgen/backends/comfyui_base.py``).  So the meshes are
#: one level down, which is precisely why nothing looking only at the output
#: root would have found them.
MESHGEN_OUTPUT_PREFIX = "forge"

#: How many models the row lists.  A cap for the same reason the exports one
#: exists, plus a second: every listed model mints a ``/file`` token, and the
#: token table is the bridge's own history of a job's files — a folder of three
#: hundred meshes must not evict the render somebody is looking at.
MAX_MODELS_LISTED = 60

#: How long ``POST /models/import`` waits.  Voxel-repairing a 200k-triangle mesh
#: blocks Blender's main thread for a while; the same budget the MCP server's
#: ``_import_generated`` gives it.
MODEL_IMPORT_TIMEOUT = 300.0

#: What to say when a client names a path this bridge does not index.  One
#: sentence for "no such file", "not a model" and "not in an indexed folder"
#: together, on purpose: a route that tells those apart is a route that can be
#: asked what exists on this machine.
MODEL_UNKNOWN_HINT = (
    "That is not a model this library indexes. The Models row lists what the "
    "picture-to-3D service wrote and what is in projects/<name>/models/ — press "
    "Refresh and use one of those.")

#: Which Blender importer opens which kind of model file, best first.
#:
#: The same operator names and the same fallback order the add-on's own
#: ``model.import_file`` uses, because they are answering the same question:
#: Blender moved its mesh importers from Python add-ons to C++ between 3.x and
#: 4.x (``import_mesh.stl`` -> ``wm.stl_import``), so a single hardcoded
#: operator name is a spawn that works on one artist's machine and silently
#: imports nothing on another's.  glTF never moved.
MODEL_IMPORT_OPERATORS = {
    ".glb": (("import_scene", "gltf"),),
    ".gltf": (("import_scene", "gltf"),),
    ".stl": (("wm", "stl_import"), ("import_mesh", "stl")),
    ".obj": (("wm", "obj_import"), ("import_scene", "obj")),
}

#: What a spawned Blender is told to do before it imports: get rid of the
#: startup **cube**.  "Open this model" that hands the artist their mesh *and* a
#: cube sitting inside it is a small lie about what they asked for, and on a
#: generated mesh at metre-ish scale the cube is not even visible until they
#: zoom — they find it later, in the export.  Only meshes go: the default camera
#: and lamp are useful and cost nothing, and ``read_homefile`` is not used at
#: all so the add-on registered at startup — the one they will press Start
#: Server in — is still there.
MODEL_OPEN_EXPRESSION = """\
import bpy
for _forge_ob in list(bpy.data.objects):
    if _forge_ob.type == "MESH":
        bpy.data.objects.remove(_forge_ob, do_unlink=True)
_forge_path = %(path)r
_forge_problem = "no importer in this Blender"
for _forge_module, _forge_name in %(ops)r:
    _forge_group = getattr(bpy.ops, _forge_module, None)
    if _forge_group is None or _forge_name not in dir(_forge_group):
        continue
    try:
        getattr(_forge_group, _forge_name)(filepath=_forge_path)
        _forge_problem = ""
        break
    except Exception as _forge_exc:
        _forge_problem = str(_forge_exc)
if _forge_problem:
    print("[forge] could not open %%s: %%s" %% (_forge_path, _forge_problem))
"""

#: What to say when there is no Blender to start.  The sibling of
#: :data:`BLENDER_MISSING_HINT`, and a separate sentence because the thing that
#: cannot be opened is a mesh file rather than a project: "open it yourself" is
#: File > Import here, not File > Open, and telling someone the wrong menu is
#: worse than telling them nothing.
MODEL_BLENDER_MISSING_HINT = (
    "Blender was not found on this machine, so %s cannot be opened from here. "
    "Start Blender yourself and use File > Import > glTF 2.0 (or set "
    "FORGE_BLENDER_EXE to the full path of blender.exe and restart the "
    "assistant).")

#: The alphabet a project name is slugged into before a model is filed there.
#: Mirrors ``forge_mcp.util.project_slug`` — the MCP server writes
#: ``projects/<slug>/`` and this route writes into the same folders, so the two
#: have to agree on what a name becomes or "file it into the bowl holder" makes
#: a second folder next to the one that is already there.
PROJECT_SLUG_MAX = 60
_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")
#: Characters that can only be an attempt to name a location rather than a
#: project.  Refused rather than slugged away, because a caller who wrote one
#: meant it, and quietly writing somewhere else is the worst outcome here.
_SLUG_TRAVERSAL_MARKERS = ("/", "\\", "..", ":", "\x00")

# ---------------------------------------------------------------------------
# Phase 18 — the version browser ("within forge we should see versions")
# ---------------------------------------------------------------------------
#
# Forge already saves every iteration of a model as its own numbered ``.blend``
# in ``projects/<name>/models/`` — ``werewolf-wip.blend``,
# ``werewolf-wip-2.blend`` … ``werewolf-wip-10.blend`` — precisely so any
# earlier version can be reopened.  Until now that was a fact about the
# filesystem and nothing else: the one place the artist looks (this page) had no
# idea the chain existed, so "go back two versions" meant Explorer.
#
# Two routes, both additive and both model-free, exactly like the Library's:
# one reads the chain, one copies a link of it to the end.

#: What a version file is.  Only ``.blend``: the numbered chain is the *scene*
#: history, and a ``.glb`` beside it is an export of one of them, not a version
#: of its own.  (The Models row already lists those.)
VERSION_EXTENSIONS = (".blend",)

#: A trailing ``-N`` on a file stem, which is what makes ``werewolf-wip-7`` the
#: seventh version of ``werewolf-wip``.  Greedy on the left so ``a-1-2`` is
#: version 2 of ``a-1``; the digit run is capped because a name ending in a
#: forty-digit number is not a version number, it is a hash.
_VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<number>\d{1,6})$")

#: The version a file with no suffix is.  ``werewolf-wip.blend`` came first and
#: ``werewolf-wip-2.blend`` is the one after it; nothing on disk says "1".
FIRST_VERSION = 1

#: How many versions one chain lists.  Same reason as :data:`MAX_MODELS_LISTED`
#: — every listed version can mint a ``/file`` token for its picture, and a
#: folder with three hundred saves in it must not evict the render the artist is
#: looking at.
MAX_VERSIONS_LISTED = 120

#: Where a version's picture comes from: the project's renders folder, the same
#: one :func:`project_demos` reads its films out of.  Stills there are named
#: after the save they are of, which is the whole matching rule below.
VERSION_THUMB_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

#: How far :func:`next_version_name` will count before giving up.  It only ever
#: counts past the end of the chain when the obvious name is taken, which means
#: something else is writing into the folder at the same time.
MAX_VERSION_SEARCH = 1000

#: Why a restore was refused.  One sentence with the thing to do in it, the same
#: shape as every other refusal on this bridge.
VERSION_UNKNOWN_HINT = (
    "%s is not one of this project's saved versions. Pass the file name exactly "
    "as GET /projects/<name>/versions listed it.")

# ---------------------------------------------------------------------------
# Phase 18 — the pipeline board's constants
# ---------------------------------------------------------------------------
#
# Every name here is READ OFF ``mcp/forge_mcp/pipeline.py``, which owns the
# file.  They are duplicated rather than imported for the same reason
# :func:`blender_command` duplicates ``blender_client``: this process is
# stdlib-only and cannot import the MCP package.  If ``pipeline.py``'s statuses
# ever change, these five lines are what has to change with them — which is why
# the board falls back to ``pending`` on a status it does not know instead of
# refusing to draw.

#: Both spellings of the project route prefix.  The workspace's five routes
#: were specified as ``/project/<name>/...`` while every route beside them is
#: ``/projects/<name>/...``; answering on both costs one loop in the router and
#: saves a 404 nobody could act on.
PROJECT_PREFIXES = ("/projects/", "/project/")

#: ``forge_mcp.pipeline.PLAN_FILENAME``, under ``design/``.
PLAN_FILENAME = "build-plan.json"
#: ``forge_mcp.pipeline.STATUSES``.
PLAN_STATUSES = ("pending", "in_progress", "passed", "failed", "overridden")
#: ``forge_mcp.pipeline.GREEN`` — green enough for the next stage to start.
PLAN_GREEN = ("passed", "overridden")
#: ``forge_mcp.pipeline.MARKERS``, so the board and the assistant's own prose
#: spell a stage the same way.
PLAN_MARKERS = {"pending": "[ ]", "in_progress": "[>]", "passed": "[x]",
                "failed": "[!]", "overridden": "[~]"}
#: How many measurements one stage card prints, how long one of them may be,
#: and how much of the plan's prose the panel carries.  A card is a card.
MAX_PLAN_NUMBERS = 24
MAX_PLAN_NUMBER_CHARS = 200
MAX_PLAN_ARTIFACTS = 12
MAX_PLAN_HISTORY = 6
MAX_PLAN_COMPONENTS = 40
MAX_PLAN_TEXT = 600

# ---------------------------------------------------------------------------
# Phase 18 — the deliverables gallery's constants
# ---------------------------------------------------------------------------

#: What the gallery shows out of ``renders/``: the stills AND the films, because
#: to the artist they are one thing — what this project has produced that can be
#: looked at.  (The Library's demos row is films only, on purpose: that row is
#: about mechanisms.)
DELIVERABLE_EXTENSIONS = VERSION_THUMB_EXTENSIONS + DEMO_EXTENSIONS
#: How many the gallery lists.  Same cap and the same reason as the models row:
#: every listed file mints a ``/file`` token.
MAX_DELIVERABLES_LISTED = 60

# ---------------------------------------------------------------------------
# Phase 18 — the live model view's constants
# ---------------------------------------------------------------------------

#: The line :data:`SNAPSHOT_SCRIPT` prints its report on.  A marker rather than
#: "the last line", because the artist's scene may print anything it likes.
SNAPSHOT_MARKER = "FORGE_SNAPSHOT "
#: How long a snapshot may take.  A glTF export of a rigged character with its
#: actions blocks Blender's main thread for a while, and the alternative to
#: waiting is a viewer that says "Blender did not answer" on every real model.
SNAPSHOT_TIMEOUT = 180.0

#: Why a thumbnail cannot be taken.  A thumbnail is a photograph of the
#: artist's work as it stands — it is deliberately NOT allowed to build the
#: part first.  Generating a shape because a picture was missing is minutes of
#: someone's machine and a scene they did not ask to have changed, in service
#: of a 240-pixel square.
NEEDS_GENERATING_HINT = (
    "%s is not in the Blender scene, so there is nothing to photograph. Open it "
    "in the Workbench and press Apply & rebuild to generate it first, then press "
    "Preview again.")

#: What to tell an artist whose geometry service is not running.  Same shape as
#: :data:`BLENDER_DOWN_HINT`: one sentence, one button to press.
SERVICE_DOWN_HINT = (
    "The shape service is not running, so parameters cannot be read or rebuilt. "
    "Press Start services (it runs start_forge.ps1), then try again. (Expected "
    "an HTTP server on %s.)")

#: How many activity entries a job keeps.  Past this the MIDDLE is dropped: the
#: first few say how the turn started, the last few say what it is doing now,
#: and the 140th identical file read in between says nothing at all.
ACTIVITY_LIMIT = 200
#: How many of the oldest entries survive trimming.
ACTIVITY_HEAD = 20
#: Longest argument summary on a tool label (the contract's "≤60 chars").
TOOL_ARG_LIMIT = 60
#: Longest label of any kind, as a backstop against a pathological tool name.
LABEL_LIMIT = 160
#: Seconds between text markers.  Text streams a token at a time; one line every
#: couple of seconds is a progress indicator, one per token is a firehose.
DEFAULT_TEXT_INTERVAL = 2.0
#: How much streamed text is kept to stand in for a missing result event, and
#: to be handed back as the checkpoint of a turn that ran out of time.  Far
#: larger than it was: this is a whole design explanation now, not a salvage
#: scrap, and 8000 characters would have clipped the dogfood run's turn 2.
MAX_SALVAGE_TEXT = 60000

#: How many "and then it did X" lines a checkpoint reply may carry.  The point
#: is to prove the work happened, not to reprint the activity feed.
MAX_CHECKPOINT_STEPS = 40

#: Only meaningful on Windows; kept as 0 elsewhere so the same call site works.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------------------
# Phase 15 — the project's own .blend
# ---------------------------------------------------------------------------

#: The scene file a project keeps beside its script, by the folder convention:
#: ``projects/<name>/<name>.blend``.  One per project, named for it, so the
#: library can ask "is there one?" with a ``stat`` and no guessing.
BLEND_SUFFIX = ".blend"

#: Writing a project file is a sculpt going to disk, and opening one is that
#: plus a whole world being rebuilt.  Both are minutes on a heavy scene, and
#: both are the artist waiting on purpose rather than something hanging.
BLEND_SAVE_TIMEOUT = 300.0
BLEND_OPEN_TIMEOUT = 300.0

#: Windows flags for a Blender **the artist asked for by clicking Open**.
#:
#: ``DETACHED_PROCESS`` because this bridge may be restarted (or stopped) while
#: they are still working, and their Blender must not go with it;
#: ``CREATE_NEW_PROCESS_GROUP`` so a Ctrl+C in the bridge's console is not also
#: a Ctrl+C in theirs.  And emphatically **not** ``CREATE_NO_WINDOW``, which is
#: what every *other* spawn in this file uses: those are background helpers
#: nobody should have to look at, and this one is an application the artist is
#: about to work in.  (The two flags are mutually exclusive to ``CreateProcess``
#: anyway, so getting this wrong is not a subtle cosmetic bug — it is a Blender
#: that never appears.)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: Well-known Blender install roots on Windows, newest version first.
_BLENDER_DIR_HINTS = ("Blender Foundation",)

BLENDER_MISSING_HINT = (
    "Blender was not found on this machine, so the project file cannot be "
    "opened from here. Open %s in Blender yourself, or set FORGE_BLENDER_EXE "
    "to the full path of blender.exe and restart the assistant.")

#: What the page does when a project has no scene file yet.  Not an error: it is
#: the ordinary state of every project until someone saves one, and the answer
#: is a button, not an apology.
NO_BLEND_HINT = (
    "%s has no scene file yet. Open it in the Studio to edit its dimensions, "
    "or press Save scene to project with the part in Blender and one is made.")

INSTALL_HINT = (
    "The Claude Code CLI was not found. Install it from "
    "https://claude.com/claude-code (or set FORGE_ASSISTANT_CLAUDE to the full "
    "path of claude.exe) and restart the assistant."
)


# ---------------------------------------------------------------------------
# environment helpers
# ---------------------------------------------------------------------------

def log(message):
    """Write a line of diagnostics, if there is anywhere to write it.

    Under ``pythonw.exe`` — which is how ``start_forge`` launches this so no
    console window appears — ``sys.stderr`` is ``None``.  Writing to it there is
    an ``AttributeError`` that kills the process on its first line, so every
    diagnostic in this file goes through here.
    """
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        stream.write(message if message.endswith("\n") else message + "\n")
        stream.flush()
    except Exception:  # noqa: BLE001 - diagnostics never take the server down
        pass


def _env(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    return value


def port():
    try:
        return int(str(_env("FORGE_ASSISTANT_PORT", DEFAULT_PORT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_PORT


def timeout_s():
    try:
        value = float(str(_env("FORGE_ASSISTANT_TIMEOUT", DEFAULT_TIMEOUT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(5.0, value)


def stall_timeout_s():
    """Seconds of total silence that count as a hang (0 disables the check).

    Separate from :func:`timeout_s` because they answer different questions:
    the turn budget asks "is this taking too long?", the stall clock asks "is
    anything happening at all?".  Only the second one can be answered without
    knowing how big the job was.
    """
    try:
        value = float(str(_env("FORGE_ASSISTANT_STALL_TIMEOUT",
                               DEFAULT_STALL_TIMEOUT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_STALL_TIMEOUT
    return max(0.0, value)


def working_dir():
    """Where the CLI runs.  Must be the repo root so ``.mcp.json`` is found.

    Claude Code scopes both its project MCP servers and its resumable sessions
    to the working directory, so this is not cosmetic: run it elsewhere and the
    Forge tools disappear and ``--resume`` stops finding the conversation.
    """
    return os.path.abspath(str(_env("FORGE_ASSISTANT_CWD", REPO_ROOT)))


def allowed_tools():
    value = _env("FORGE_ASSISTANT_TOOLS")
    if value is None:
        return DEFAULT_ALLOWED_TOOLS
    return value


def text_interval():
    """Seconds between text activity markers (0 disables the throttle)."""
    try:
        value = float(str(_env("FORGE_ASSISTANT_TEXT_INTERVAL",
                               DEFAULT_TEXT_INTERVAL)).strip())
    except (TypeError, ValueError):
        return DEFAULT_TEXT_INTERVAL
    return max(0.0, value)


def uploads_dir():
    """Where ``POST /upload`` puts what the browser handed us.

    Beside this file by default, so it travels with the bridge and is obvious
    to find (and to delete) rather than hidden in a temp folder the artist will
    never think to look in.
    """
    return os.path.abspath(str(_env("FORGE_ASSISTANT_UPLOADS",
                                    os.path.join(HERE, "uploads"))))


def max_upload_bytes():
    """The ``POST /upload`` size cap, in bytes."""
    try:
        megabytes = float(str(_env("FORGE_ASSISTANT_MAX_UPLOAD_MB",
                                   DEFAULT_MAX_UPLOAD_MB)).strip())
    except (TypeError, ValueError):
        megabytes = DEFAULT_MAX_UPLOAD_MB
    return int(max(0.01, megabytes) * 1024 * 1024)


def start_script():
    """The script ``POST /services/start`` runs."""
    return os.path.abspath(str(_env("FORGE_START_SCRIPT",
                                    os.path.join(REPO_ROOT, "start_forge.ps1"))))


def blender_address():
    host = str(_env("FORGE_BLENDER_HOST", DEFAULT_BLENDER_HOST)).strip() \
        or DEFAULT_BLENDER_HOST
    try:
        blender_port = int(str(_env("FORGE_BLENDER_PORT", DEFAULT_BLENDER_PORT)).strip())
    except (TypeError, ValueError):
        blender_port = DEFAULT_BLENDER_PORT
    return host, blender_port


def flow_run_timeout():
    try:
        value = float(str(_env("FORGE_FLOW_RUN_TIMEOUT",
                               DEFAULT_FLOW_RUN_TIMEOUT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_FLOW_RUN_TIMEOUT
    return max(5.0, value)


def projects_dir():
    """Where the parametric projects live.

    The *same* variable the MCP server's ``partforge_new_part`` writes into, so
    a part the assistant wrote one minute ago is in the workbench's picker the
    next time it is opened.  Two names for one folder would be a bug that only
    shows up on the machine that set the variable.
    """
    return os.path.abspath(str(_env("FORGE_PROJECTS_DIR",
                                    os.path.join(REPO_ROOT, "projects"))))


def previews_dir():
    """Where ``POST /preview`` writes.  A cache of pictures, not the artist's work.

    The temp dir rather than the repo: these are regenerated on every click, and
    a folder of four hundred PNGs beside ``projects/`` is somebody's next
    confused bug report about disk space.
    """
    return os.path.abspath(str(_env(
        "FORGE_ASSISTANT_PREVIEWS",
        os.path.join(tempfile.gettempdir(), "forge-webui-previews"))))


def generate_timeout():
    try:
        value = float(str(_env("FORGE_GENERATE_TIMEOUT",
                               DEFAULT_GENERATE_TIMEOUT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_GENERATE_TIMEOUT
    return max(5.0, value)


def thumbs_dir():
    """The library's thumbnail cache — one PNG per project.

    Beside ``uploads`` rather than in the temp folder, unlike
    :func:`previews_dir`: a preview is regenerated on every click and a
    thumbnail is what the library draws *before* anything is running, so it has
    to survive a reboot.  One file per project, overwritten in place, so the
    folder can never grow past the number of parts the artist has.
    """
    return os.path.abspath(str(_env("FORGE_ASSISTANT_THUMBS",
                                    os.path.join(HERE, "thumbs"))))


# ---------------------------------------------------------------------------
# locating the claude executable
# ---------------------------------------------------------------------------

def _candidate_paths():
    """Well-known Windows install locations, newest version first.

    ``shutil.which`` is the right answer when the CLI is on PATH (an npm install
    puts ``claude.cmd`` there).  The native Windows installer does not touch a
    user PATH that Blender will inherit, so we also look where it actually
    unpacks: ``%APPDATA%\\Claude\\claude-code\\<version>\\claude.exe``.
    """
    out = []
    appdata = os.environ.get("APPDATA") or ""
    localapp = os.environ.get("LOCALAPPDATA") or ""
    home = os.path.expanduser("~")

    versions_dir = os.path.join(appdata, "Claude", "claude-code")
    if os.path.isdir(versions_dir):
        try:
            entries = os.listdir(versions_dir)
        except OSError:
            entries = []
        for name in sorted(entries, key=_version_key, reverse=True):
            for exe in ("claude.exe", "claude"):
                out.append(os.path.join(versions_dir, name, exe))

    out.extend([
        os.path.join(appdata, "npm", "claude.cmd"),
        os.path.join(appdata, "npm", "claude"),
        os.path.join(localapp, "Programs", "claude", "claude.exe"),
        os.path.join(home, ".local", "bin", "claude.exe"),
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, ".claude", "local", "claude"),
        "/usr/local/bin/claude",
    ])
    return out


def _version_key(name):
    parts = re.findall(r"\d+", str(name))
    return tuple(int(p) for p in parts[:4]) or (0,)


def resolve_claude():
    """Absolute path to the CLI, or ``None``.

    Order: the explicit override, then PATH, then the known install locations.
    """
    override = _env("FORGE_ASSISTANT_CLAUDE")
    if override:
        override = override.strip().strip('"')
        return override if os.path.isfile(override) else (shutil.which(override) or None)

    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found

    for candidate in _candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Phase 15 — locating Blender itself
# ---------------------------------------------------------------------------

def _blender_candidates():
    """Where Blender installs itself, newest version first.

    Deliberately the same shape as :func:`_candidate_paths`: an override, then
    ``PATH``, then the places the official installer actually unpacks to.  The
    Windows installer does not put Blender on a PATH this process will inherit,
    so ``shutil.which`` alone would find nothing on the machine this product is
    built for.
    """
    out = []
    if os.name == "nt":
        roots = []
        for base in (os.environ.get("PROGRAMFILES"),
                     os.environ.get("ProgramW6432"),
                     r"C:\Program Files",
                     os.environ.get("PROGRAMFILES(X86)")):
            if not base:
                continue
            for hint in _BLENDER_DIR_HINTS:
                root = os.path.join(base, hint)
                if os.path.isdir(root) and root not in roots:
                    roots.append(root)
        for root in roots:
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for name in sorted(entries, key=_version_key, reverse=True):
                out.append(os.path.join(root, name, "blender.exe"))
        for base in (os.environ.get("PROGRAMFILES(X86)"), r"C:\Program Files (x86)"):
            if base:
                out.append(os.path.join(base, "Steam", "steamapps", "common",
                                        "Blender", "blender.exe"))
    else:
        out.extend([
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "/usr/local/bin/blender",
            "/usr/bin/blender",
            "/snap/bin/blender",
        ])
    return out


def resolve_blender():
    """Absolute path to a Blender executable, or ``None``.

    ``FORGE_BLENDER_EXE`` first (which is also how the tests point this at a
    stand-in), then ``PATH``, then the install locations.
    """
    override = _env("FORGE_BLENDER_EXE")
    if override:
        override = override.strip().strip('"')
        if os.path.isfile(override):
            return override
        return shutil.which(override) or None

    for name in ("blender", "blender.exe"):
        found = shutil.which(name)
        if found:
            return found

    for candidate in _blender_candidates():
        if os.path.isfile(candidate):
            return candidate
    return None


def blender_launch_argv(exe, blend_path):
    """The argv that opens ``blend_path`` in a windowed Blender.

    A ``.py`` executable runs under this interpreter — the same trick
    :func:`launcher` and :func:`start_services_command` play, and what lets the
    spawn be tested without a real Blender appearing on somebody's screen.
    """
    if str(exe).lower().endswith(".py"):
        return [sys.executable, str(exe), str(blend_path)]
    return [str(exe), str(blend_path)]


def spawn_creationflags():
    """Windows creation flags for the Blender the artist just asked for.

    See :data:`DETACHED_PROCESS`: detached so it outlives this bridge, its own
    process group so a Ctrl+C here is not a Ctrl+C there, and **never**
    ``CREATE_NO_WINDOW`` — the whole point of this spawn is a window.
    """
    if os.name != "nt":
        return 0
    return DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def spawn_detached(argv):
    """Start a windowed Blender, however it was asked for.  Returns the process.

    The artist clicked, so this is *them* launching Blender — the
    no-windowed-Blender law binds agents and verification runs, not the person
    whose machine it is.  Nothing is piped: a detached GUI application with a
    pipe nobody reads is a GUI application that eventually blocks on its own
    stdout.

    One function for both spawns (a project file, a model file) so the flags
    above are decided in exactly one place: a second copy is a second chance to
    write ``CREATE_NO_WINDOW`` and ship a Blender that never appears.
    """
    kwargs = {
        "cwd": REPO_ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = spawn_creationflags()
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def spawn_blender(exe, blend_path):
    """Start a windowed Blender on ``blend_path`` (Phase 15's project file)."""
    return spawn_detached(blender_launch_argv(exe, blend_path))


def model_open_expression(path):
    """The Python a spawned Blender runs to have the model on screen.

    A ``.glb`` is not a ``.blend``, so it cannot be a positional argument the
    way :func:`blender_launch_argv` passes a project file — Blender would try to
    *open* it as a scene and fail.  It has to be **imported**, which means code,
    which is what ``--python-expr`` is for.

    The path is embedded with ``repr`` rather than pasted: a Windows path is
    full of backslashes, and a mesh called ``dog's bowl.glb`` would otherwise
    end the string literal in the middle of the artist's filename.
    """
    extension = os.path.splitext(str(path))[1].lower()
    operators = MODEL_IMPORT_OPERATORS.get(extension, MODEL_IMPORT_OPERATORS[".glb"])
    return MODEL_OPEN_EXPRESSION % {"path": str(path), "ops": tuple(operators)}


def blender_model_argv(exe, model_path):
    """The argv that starts a windowed Blender with ``model_path`` imported.

    A ``.py`` executable runs under this interpreter, the same stand-in trick
    :func:`blender_launch_argv` plays — which is what lets the spawn be proved
    without a Blender window appearing on somebody's screen.
    """
    argv = [sys.executable, str(exe)] if str(exe).lower().endswith(".py") \
        else [str(exe)]
    return argv + ["--python-expr", model_open_expression(model_path)]


def spawn_blender_with_model(exe, model_path):
    """Start a windowed Blender with one model file imported, cube and all gone."""
    return spawn_detached(blender_model_argv(exe, model_path))


def launcher(path):
    """The argv prefix that actually runs ``path``.

    Normally just ``[path]``.  A ``.py`` override runs under this bridge's own
    interpreter — that exists so the test suite can point
    ``FORGE_ASSISTANT_CLAUDE`` at a fake CLI written in Python without going
    through a ``.cmd`` wrapper, which on Windows would mangle the multi-line
    prompt (cmd.exe cannot carry a newline inside one argument).
    """
    if str(path).lower().endswith(".py"):
        return [sys.executable, path]
    return [path]


_VERSION_CACHE = {}


def claude_info(refresh=False):
    """``{"found", "path", "version"?}`` — the version is probed once and cached."""
    path = resolve_claude()
    if not path:
        return {"found": False, "path": None, "hint": INSTALL_HINT}
    if refresh or path not in _VERSION_CACHE:
        _VERSION_CACHE[path] = _probe_version(path)
    info = {"found": True, "path": path}
    version = _VERSION_CACHE.get(path)
    if version:
        info["version"] = version
    return info


def _probe_version(path):
    try:
        proc = subprocess.run(
            launcher(path) + ["--version"],
            cwd=working_dir(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001 - a missing/unusable CLI is not fatal here
        return None
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0].strip() if text else None


# ---------------------------------------------------------------------------
# message assembly
# ---------------------------------------------------------------------------

_CONTEXT_LABELS = OrderedDict((
    ("active_object", "Active object"),
    ("objects", "Objects in the scene"),
    ("script_path", "Current PartForge script"),
    ("mode", "Blender mode"),
    ("blender_version", "Blender version"),
    ("selection", "Selected objects"),
))


def _context_value(value):
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "(none)"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if value is None or value == "":
        return "(none)"
    return str(value)


def format_context(context):
    """The context block appended to the user's message, or ``""``.

    It rides in the message rather than the system prompt on purpose: the
    scene changes every turn, and the system prompt is fixed for the session.
    """
    if not isinstance(context, dict) or not context:
        return ""
    lines = []
    for key, label in _CONTEXT_LABELS.items():
        if key in context:
            lines.append("%s: %s" % (label, _context_value(context.get(key))))
    for key in sorted(context):
        if key in _CONTEXT_LABELS:
            continue
        label = str(key).replace("_", " ").strip().capitalize()
        lines.append("%s: %s" % (label, _context_value(context[key])))
    if not lines:
        return ""
    return "\n\n%s\n%s" % (CONTEXT_DIVIDER, "\n".join(lines))


def normalize_image_path(path):
    """An attachment path as an absolute path, or ``""`` when there is none."""
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    text = os.path.expandvars(os.path.expanduser(text))
    return os.path.abspath(os.path.normpath(text))


def image_error(path):
    """Why this attachment cannot be sent, or ``""`` when it is fine.

    Checked here as well as in the panel on purpose: the panel is one client of
    a localhost HTTP endpoint, and a path that does not exist would otherwise
    become a turn spent watching the model fail to Read it.
    """
    resolved = normalize_image_path(path)
    if not resolved:
        return ""
    # Folder first: a directory is a directory whatever it happens to be
    # called, and "sketches is not an image" would send them looking for a
    # typo that is not there.
    if os.path.isdir(resolved):
        return "%s is a folder, not an image file." % resolved
    extension = os.path.splitext(resolved)[1].lower()
    if extension not in IMAGE_EXTENSIONS:
        return ("%s is not an image the assistant can read. Attach a %s file."
                % (os.path.basename(resolved) or resolved,
                   " or ".join(IMAGE_EXTENSIONS)))
    if not os.path.isfile(resolved):
        return "There is no file at %s." % resolved
    return ""


def split_image(context):
    """``(context without the attachment, absolute image path)``.

    The attachment is pulled out of the context dict before it is formatted so
    it appears once — as its own block with the instruction attached — instead
    of twice, once as an unexplained "Image path:" line.
    """
    if not isinstance(context, dict) or "image_path" not in context:
        return context, ""
    # The key comes out whether or not it holds anything: a panel that sends
    # image_path="" must not produce a bare "Image path:" line in the context.
    rest = {key: value for key, value in context.items() if key != "image_path"}
    return rest, normalize_image_path(context.get("image_path"))


def format_image(path):
    """The attachment block appended to the message, or ``""``."""
    path = normalize_image_path(path)
    if not path:
        return ""
    return "\n\n%s\n%s\n%s" % (IMAGE_DIVIDER, path, IMAGE_INSTRUCTION)


def format_live(live):
    """The live-context block as it appears in the prompt, or ``""``."""
    text = str(live or "").strip()
    if not text:
        return ""
    return "\n\n%s" % text


def build_prompt(message, context, live=""):
    """The artist's message, what they were looking at, what Blender IS, the image.

    ``live`` sits between the panel's context block and the attachment for a
    reason of reading order: the context block is what the artist told us, the
    live block is what we went and looked at, and the attachment carries an
    instruction ("Read this first") that has to be the last thing on the page.
    """
    rest, image_path = split_image(context)
    return (str(message or "").strip()
            + format_context(rest)
            + format_live(live)
            + format_image(image_path))


# ---------------------------------------------------------------------------
# which model this turn runs on
# ---------------------------------------------------------------------------

def normalize_model(value):
    """A requested model as a bare lowercase name, or ``""`` for "not asked".

    Whitespace and case are forgiven because this arrives over HTTP from a
    panel; anything that is not one of :data:`MODELS` comes back unchanged for
    :func:`model_error` to refuse by name.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def model_error(value):
    """Why this model choice cannot be used, or ``""`` when it is fine.

    Checked here, not only in the panel: the bridge is a localhost HTTP
    endpoint, and a typo'd model would otherwise be a turn spent watching the
    CLI reject a flag.
    """
    text = normalize_model(value)
    if not text or text in MODELS:
        return ""
    return ("%s is not a model the assistant offers. Choose one of %s."
            % (str(value)[:60], ", ".join(MODELS)))


def resolve_model(requested=None):
    """The ``--model`` value for a turn, or ``""`` to leave the flag off.

    Request first, then ``FORGE_ASSISTANT_MODEL``, then nothing — the artist's
    choice in the panel beats the environment the bridge happened to start in,
    and with neither set the CLI uses whatever the user configured for it.
    """
    text = normalize_model(requested)
    if text:
        return text
    return str(_env("FORGE_ASSISTANT_MODEL") or "").strip()


def build_argv(claude_path, prompt, session_id=None, permission_mode="auto",
               model=None):
    """The exact command line a turn runs.

    Kept as one pure function so the tests can assert on it without spawning
    anything, and so the argv that ships is the argv that was tested.

    ``stream-json`` (not ``json``) is what makes the activity list possible: the
    CLI prints one JSON event per line as it works instead of a single object
    when it is done.  ``--verbose`` is required by the CLI for stream-json in
    ``-p`` mode, and ``--include-partial-messages`` is what turns text into
    token deltas we can show as progress.  The final ``type: "result"`` line
    carries exactly the fields ``--output-format json`` used to.

    ``model`` is the artist's per-message choice; see :func:`resolve_model` for
    what wins.  Naming a different model on a turn that also carries
    ``--resume`` is deliberate and needs nothing special — the conversation
    continues, the next reply is written by the model just named.
    """
    argv = launcher(claude_path) + [
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]

    if os.path.isfile(SYSTEM_PROMPT_PATH):
        argv += ["--append-system-prompt-file", SYSTEM_PROMPT_PATH]

    argv += ["--allowedTools", allowed_tools()]

    if permission_mode:
        argv += ["--permission-mode", permission_mode]

    effective_model = resolve_model(model)
    if effective_model:
        argv += ["--model", effective_model]

    if session_id:
        argv += ["--resume", str(session_id)]

    return argv


# ---------------------------------------------------------------------------
# reading what the CLI printed
# ---------------------------------------------------------------------------

def salvage_json(stdout):
    """The CLI's JSON payload, even with noise printed around it.

    The stream reader below handles the normal path line by line; this is the
    last resort for a build that printed one ``--output-format json`` object
    with node warnings, update notices or a shell wrapper's chatter around it.
    Try the whole blob, then every line from the last backwards, then any
    brace-balanced region.  Returns ``None`` when nothing parses.
    """
    text = (stdout or "").strip()
    if not text:
        return None

    parsed = _try_object(text)
    if parsed is not None:
        return parsed

    for line in reversed(text.splitlines()):
        parsed = _try_object(line.strip())
        if parsed is not None:
            return parsed

    start = text.find("{")
    while start != -1:
        parsed = _try_object(text[start:])
        if parsed is not None:
            return parsed
        start = text.find("{", start + 1)
    return None


def _try_object(chunk):
    if not chunk or not chunk.startswith("{"):
        return None
    try:
        value = json.loads(chunk)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _tail(text, limit=1200):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "... " + text[-limit:]


def _looks_like_bad_flag(stderr, flag):
    text = (stderr or "").lower()
    if flag.lower() not in text and "unknown option" not in text:
        return False
    return any(marker in text for marker in
               ("unknown option", "unknown argument", "unrecognized", "invalid option",
                "not a valid", "error: option"))


#: The two failures an artist will actually hit, rewritten as something they can
#: act on.  Everything else is passed through untouched — a vague message is
#: worse than an honest one.
_FRIENDLY = (
    (("not logged in", "/login", "please run /login", "invalid api key",
      "authentication_error"),
     "The Claude CLI is not signed in on this computer. Open a terminal, type "
     "claude, press Enter, then run /login and follow the prompts. You only "
     "have to do this once."),
    (("rate limit", "rate_limit", "usage limit", "429"),
     "Claude's usage limit has been hit for now. Wait for the reset shown in "
     "the terminal, or try again later — nothing in your scene was changed."),
)


def friendly_error(message):
    lowered = (message or "").lower()
    for needles, replacement in _FRIENDLY:
        if any(needle in lowered for needle in needles):
            return "%s\n(the CLI said: %s)" % (replacement, message.strip())
    return message


#: The first ``_FRIENDLY`` entry is the sign-in class, and it is the one worth
#: reporting on ``/health``: the panel can say "not signed in" in its status row
#: instead of the artist discovering it a turn later.
AUTH_NEEDLES = _FRIENDLY[0][0]


def looks_like_auth_error(message):
    """Was this failure a sign-in problem?

    Read off an error we already have, never by running the CLI to find out —
    a probe turn costs money and seconds, and this answer is only ever used to
    colour a status line.
    """
    lowered = (message or "").lower()
    return any(needle in lowered for needle in AUTH_NEEDLES)


def extract_reply(payload):
    """The assistant's text out of the CLI's JSON, whatever shape it took."""
    for key in ("result", "text", "response", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(payload.get("result"), dict):
        nested = payload["result"].get("text") or payload["result"].get("content")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def compose_reply(blocks, final=""):
    """The artist's reply: every text block the model wrote, in order.

    The CLI's ``result`` field is the model's LAST message, which is why the
    dogfood run's turn 2 delivered four lines of summary and dropped the four
    paragraphs of reasoning that came before the tool calls — the explanation
    is the product in a design tool, and it was the part being thrown away
    (friction F-1).  So the reply is assembled from the stream instead, and the
    result string is only appended when it says something the stream did not.

    Joining with a blank line is not cosmetic: the blocks are paragraphs the
    model wrote around its tool calls, and run together they read as one
    sentence that changes subject halfway through.
    """
    ordered = [str(block).strip() for block in (blocks or []) if str(block).strip()]
    final = str(final or "").strip()
    if not ordered:
        return final
    joined = "\n\n".join(ordered)
    if final and final not in joined:
        joined = "%s\n\n%s" % (joined, final)
    return joined


def checkpoint_reply(recorder, limit, stalled=False, silent_for=0.0):
    """What a turn that ran out of time still has to say for itself.

    B-1 in one function.  The dogfood run's turn 3 wrote a 441-line ``part.py``,
    generated it, hit a printability refusal, fixed it, re-checked and rendered
    twice — and then died at the timeout reporting ``state: error``, no reply,
    no cost, and the sentence "Try a smaller request".  Every one of those four
    was wrong: the work was on disk, it had been described, it had been paid
    for, and the request was never the problem.

    So the reply is whatever the model actually said and did, followed by one
    honest sentence naming what stopped it.  The advice to ask for less appears
    only when it is true — a turn killed while it was working might genuinely
    be too big; a turn killed because the CLI went silent would have stalled at
    any size, and telling the artist to type less is blaming them for a hang.
    """
    parts = []
    text = compose_reply(recorder.text_blocks()) if recorder is not None else ""
    if text:
        parts.append(text)
    steps = recorder.steps() if recorder is not None else []
    if steps:
        parts.append("Steps it completed before it was stopped:\n"
                     + "\n".join("- %s" % step for step in steps))
    if stalled:
        parts.append(
            "— the assistant went silent for %.0f seconds (no text, no tool, "
            "nothing) and was stopped there rather than left to eat the rest "
            "of the %.0fs budget. Anything above is real and on disk. Ask "
            "\"what got built?\" and it will pick up where this left off; the "
            "conversation survived."
            % (max(0.0, float(silent_for)), float(limit)))
    else:
        parts.append(
            "— the turn hit the %.0fs limit — above is everything it said and "
            "did before that; the work it completed is real and on disk. Ask "
            "\"what got built?\" to carry on from here (the conversation "
            "survives), or try a smaller request, or raise "
            "FORGE_ASSISTANT_TIMEOUT." % float(limit))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# the NDJSON stream: events in, activity out
# ---------------------------------------------------------------------------

def parse_stream_line(line):
    """One line of the CLI's stdout as a dict, or ``None`` if it is not one.

    Node warnings, blank lines and half-written objects all turn up here.  None
    of them is an error: an unreadable line is skipped and the stream carries on.
    """
    text = (line or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def looks_like_result(payload):
    """Is this event the final result object?

    ``type: "result"`` is the CLI's own marker.  The rest is for a build that
    prints the plain ``--output-format json`` object instead — the same fields,
    without the tag.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("type") == "result":
        return True
    if payload.get("type"):  # some other tagged event: not the result
        return False
    return any(key in payload for key in ("result", "session_id", "is_error"))


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _sort_key(index):
    """Order stream block indices without assuming they are numbers."""
    try:
        return (0, float(index))
    except (TypeError, ValueError):
        return (1, str(index))


def _first_text(value, limit=400):
    """The readable text of a tool result, whatever shape the CLI wrapped it in."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        for key in ("text", "content", "result", "output"):
            found = _first_text(value.get(key), limit)
            if found:
                return found
        return ""
    if isinstance(value, (list, tuple)):
        for item in list(value)[:8]:
            found = _first_text(item, limit)
            if found:
                return found
    return ""


#: Argument names worth showing, most identifying first.  A tool call reads
#: "partforge_check: part.py", not a dump of its whole input object.
_ARG_KEYS = (
    "script_path", "path", "file_path", "name", "object", "new_name", "tag",
    "directory", "source_path", "rig", "action", "operand", "mode", "format",
    "pattern", "command", "url", "code", "message",
)

#: Everything the assistant can call is prefixed by the MCP server name; the
#: artist does not need to read "mcp__forge__" 30 times.
_TOOL_PREFIXES = ("mcp__%s__" % MCP_SERVER, "mcp__forge__")


def short_tool_name(name):
    text = str(name or "").strip() or "tool"
    for prefix in _TOOL_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):] or text
    return text


def _short_value(value):
    if isinstance(value, str):
        text = value.strip()
        if ("\\" in text or "/" in text) and not text.startswith("http"):
            base = os.path.basename(text.replace("\\", "/").rstrip("/"))
            if base:
                return base
        return text
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def summarize_args(args):
    """A ≤60 character sketch of a tool's input, or ``""`` when there is none."""
    if not isinstance(args, dict) or not args:
        return ""
    for key in _ARG_KEYS:
        value = args.get(key)
        if value in (None, "", [], {}):
            continue
        summary = _short_value(value)
        if summary:
            return _clip(summary, TOOL_ARG_LIMIT)
    parts = []
    for key in list(args)[:3]:
        value = args.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append("%s=%s" % (key, _short_value(value)))
    return _clip(", ".join(parts), TOOL_ARG_LIMIT)


def tool_label(name, args=None):
    """``"partforge_check: part.py"`` — the label a tool event gets."""
    label = short_tool_name(name)
    summary = summarize_args(args)
    return "%s: %s" % (label, summary) if summary else label


class ActivityRecorder(object):
    """Turns a stream of CLI events into the job's activity list.

    It is also the turn's black box.  Everything that goes past — every text
    block the model wrote, every tool it ran and what each one answered, and
    any cost the stream mentioned — is kept here as it arrives, so that a turn
    which never reaches its result event still has something true to say.  That
    is the whole of the B-1 fix: the work was always real, only the telling of
    it was lost.

    Every method is defensive on purpose.  This runs on the worker thread while
    a real turn is in flight; a shape we have not seen before must cost the
    artist a missing line, never the answer.
    """

    def __init__(self, store, job_id, interval=None):
        self.store = store
        self.job_id = job_id
        self.interval = text_interval() if interval is None else float(interval)
        self._tool_entries = {}     # tool_use id -> the activity entry dict
        self._tool_names = {}       # tool_use id -> full tool name
        self._tool_json = {}        # tool_use id -> accumulated partial JSON
        self._tool_detailed = set() # ids whose label already carries arguments
        self._block_tools = {}      # stream block index -> tool_use id
        self._seen_tool = False
        self._said_thinking = False
        self._saw_text_delta = False
        self._pending_text = ""
        self._last_text_at = 0.0
        #: Assistant text, one entry per content block, in the order written.
        #: A list rather than one string because the artist's reply is the
        #: model's WHOLE side of the turn: the paragraph before a tool call is
        #: the reasoning, and joining the blocks back up is F-1's fix.
        self._blocks = []
        self._open_blocks = {}      # stream block index -> text so far
        self._text_len = 0
        #: ``(tool label, result summary)`` for each tool, in order.
        self._steps = []
        self._step_index = {}       # tool_use id -> position in _steps
        #: The last cost the stream mentioned, if any build volunteers one
        #: before the result event.
        self._cost = None
        self._usage = None

    # -- output ----------------------------------------------------------
    def text_blocks(self):
        """Every assistant text block, in order, including the unfinished one."""
        blocks = list(self._blocks)
        for _index, text in sorted(self._open_blocks.items(),
                                   key=lambda item: _sort_key(item[0])):
            if text.strip():
                blocks.append(text.strip())
        return [block for block in blocks if block]

    def text(self):
        """Everything that streamed past as assistant text, blocks rejoined."""
        return "\n\n".join(self.text_blocks()).strip()

    def steps(self):
        """``["partforge_check: part.py → overall: warn", …]`` — what it did."""
        out = []
        for label, result in self._steps:
            out.append("%s → %s" % (label, result) if result else label)
        return out

    def cost(self):
        """The cost the stream volunteered, or ``None`` if it never did."""
        return self._cost

    def usage(self):
        return self._usage if isinstance(self._usage, dict) else None

    def push(self, kind, label):
        return self.store.add_activity(self.job_id, kind, label)

    # -- input -----------------------------------------------------------
    def feed(self, payload):
        """One parsed NDJSON object. Never raises."""
        try:
            self._feed(payload)
        except Exception:  # noqa: BLE001 - visibility must not break the turn
            pass

    def _feed(self, payload):
        if not isinstance(payload, dict):
            return
        self._money(payload)
        kind = payload.get("type")
        if kind == "stream_event":
            self._event(payload.get("event"))
        elif kind == "assistant":
            self._message(payload.get("message"))
        elif kind == "user":
            # A tool's *result*: where a render's path usually first appears,
            # and what the tool actually ANSWERED.  Both are read here — the
            # path for its token, the answer so a checkpoint can say "it ran
            # partforge_check and the check passed" rather than only "it ran".
            self._files(payload.get("message"))
            self._results(payload.get("message"))
        elif kind in ("content_block_start", "content_block_delta",
                      "content_block_stop"):
            # a build that emits the raw Anthropic events without the wrapper
            self._event(payload)

    def _money(self, payload):
        """Keep any cost or usage the stream mentions, wherever it appears.

        A turn that is killed never reaches its result event, and the dogfood
        run's fifteen-minute turn 3 was therefore billed at $0.00.  Whatever
        the stream said before the kill is a truer number than nothing.
        """
        cost = payload.get("total_cost_usd")
        if cost is None:
            cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self._cost = float(cost)
        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            self._usage = usage
        message = payload.get("message")
        if isinstance(message, dict):
            nested = message.get("usage")
            if isinstance(nested, dict) and nested:
                self._usage = nested

    def _event(self, event):
        if not isinstance(event, dict):
            return
        self._money(event)
        etype = event.get("type")
        index = event.get("index")
        if etype == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "block-%s" % index)
                self._block_tools[index] = tool_id
                self._tool(tool_id, block.get("name"), block.get("input"))
            elif isinstance(block, dict) and block.get("type") == "text":
                # A new paragraph starts here.  Opening it explicitly is what
                # keeps block two from being glued to the end of block one.
                self._open_blocks.setdefault(index, "")
                self._append_block(index, block.get("text"))
            return
        if etype == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return
            dtype = delta.get("type")
            if dtype == "text_delta":
                self._saw_text_delta = True
                chunk = delta.get("text")
                self._append_block(index, chunk)
                self._marker(chunk)
            elif dtype == "input_json_delta":
                tool_id = self._block_tools.get(index)
                if tool_id:
                    chunk = str(delta.get("partial_json") or "")
                    self._tool_json[tool_id] = (
                        self._tool_json.get(tool_id, "") + chunk)[:8000]
            return
        if etype == "content_block_stop":
            self._close_block(index)
            tool_id = self._block_tools.pop(index, None)
            if tool_id:
                self._finish_tool(tool_id)

    def _message(self, message):
        """An assembled assistant message — the tool inputs arrive complete here."""
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self._tool(str(block.get("id") or ""), block.get("name"),
                           block.get("input"))
            elif block.get("type") == "text" and not self._saw_text_delta:
                # No partial messages on this build: the whole block at once.
                self._whole_block(block.get("text"))
                self._marker(block.get("text"))

    def _files(self, value):
        """Record any servable path in ``value`` against this job."""
        try:
            self.store.note_files_in(self.job_id, value, "activity")
        except Exception:  # noqa: BLE001 - a thumbnail is never worth the turn
            pass

    # -- pieces ----------------------------------------------------------
    def _results(self, message):
        """Keep each tool result as one short line of "and this is what it said"."""
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id") or "")
            summary = _clip(_first_text(block.get("content")), TOOL_ARG_LIMIT)
            if not summary:
                continue
            position = self._step_index.get(tool_id)
            if position is None:
                continue
            label, _old = self._steps[position]
            self._steps[position] = (label, summary)

    def _note_step(self, tool_id, label):
        if tool_id in self._step_index:
            if len(self._steps) <= MAX_CHECKPOINT_STEPS:
                position = self._step_index[tool_id]
                self._steps[position] = (label, self._steps[position][1])
            return
        if len(self._steps) >= MAX_CHECKPOINT_STEPS:
            return
        self._step_index[tool_id] = len(self._steps)
        self._steps.append((label, ""))

    def _tool(self, tool_id, name, args):
        if not name:
            return
        tool_id = tool_id or "tool-%d" % len(self._tool_entries)
        self._seen_tool = True
        # Before the label is clipped to a basename: the token needs the whole
        # path, and this is the last place it exists in full.
        self._files(args)
        self._note_step(tool_id, tool_label(name, args))
        entry = self._tool_entries.get(tool_id)
        if entry is None:
            self._tool_names[tool_id] = name
            entry = self.push("tool", tool_label(name, args))
            if entry is None:
                return
            self._tool_entries[tool_id] = entry
            if summarize_args(args):
                self._tool_detailed.add(tool_id)
            return
        # Seen already (streamed start, now the complete input): fill the
        # arguments in on the SAME line rather than logging the call twice.
        if tool_id not in self._tool_detailed and summarize_args(args):
            self.store.relabel_activity(self.job_id, entry, tool_label(name, args))
            self._tool_detailed.add(tool_id)

    def _finish_tool(self, tool_id):
        if tool_id in self._tool_detailed:
            return
        raw = self._tool_json.pop(tool_id, "")
        if not raw:
            return
        try:
            args = json.loads(raw)
        except ValueError:
            return
        self._files(args)
        entry = self._tool_entries.get(tool_id)
        if entry is None or not summarize_args(args):
            return
        label = tool_label(self._tool_names.get(tool_id), args)
        self._note_step(tool_id, label)
        self.store.relabel_activity(self.job_id, entry, label)
        self._tool_detailed.add(tool_id)

    # -- the model's own words -------------------------------------------
    def _append_block(self, index, chunk):
        """Add streamed text to the block it belongs to, capped in total."""
        chunk = str(chunk or "")
        if not chunk or self._text_len >= MAX_SALVAGE_TEXT:
            return
        room = MAX_SALVAGE_TEXT - self._text_len
        chunk = chunk[:room]
        self._text_len += len(chunk)
        self._open_blocks[index] = self._open_blocks.get(index, "") + chunk

    def _close_block(self, index):
        text = self._open_blocks.pop(index, None)
        if text is None:
            return
        text = text.strip()
        if text:
            self._blocks.append(text)

    def _whole_block(self, text):
        """A complete text block from a build that does not stream deltas."""
        text = str(text or "").strip()
        if not text or self._text_len >= MAX_SALVAGE_TEXT:
            return
        text = text[:MAX_SALVAGE_TEXT - self._text_len]
        self._text_len += len(text)
        self._blocks.append(text)

    def _marker(self, chunk):
        """The throttled 60-character ticker in the activity feed.

        Deliberately separate from the accumulation above: this is a progress
        indicator that may be clipped mid-word, and the reply must never be
        assembled out of it.  That confusion is exactly friction F-1.
        """
        chunk = str(chunk or "")
        if not chunk:
            return
        if self._seen_tool and not self._said_thinking:
            # The tools are done and words are coming: say so once.
            self.push("status", "thinking…")
            self._said_thinking = True
        self._pending_text += chunk

        now = time.time()
        if self.interval and (now - self._last_text_at) < self.interval:
            return
        snippet = _clip(self._pending_text, TOOL_ARG_LIMIT)
        self._pending_text = ""
        if not snippet:
            return
        self._last_text_at = now
        self.push("text", snippet)


# ---------------------------------------------------------------------------
# Phase 9 — file tokens: the only paths a browser may ask this bridge for
# ---------------------------------------------------------------------------

class FileTokens(object):
    """Opaque names for paths this bridge itself recorded on a job.

    The web UI has to show two kinds of file that live on the artist's disk:
    the reference image they attached, and the renders and ``.glb`` files the
    assistant produced.  A browser can only fetch a URL, so something has to
    serve those bytes — and "serve any path a query string names" is a hole
    straight through the machine, on a port every program on it can reach.

    So paths are never accepted from the client.  A token is minted *inbound*,
    when a path enters a job through this process: an attachment on ``/ask``, a
    path in a tool's arguments, a path in a tool result, a path in the reply.
    ``GET /file/<token>`` maps a token back to exactly the path that was minted
    for it and refuses everything else, including a path that exists and is
    perfectly readable.  The allow-list is therefore the bridge's own history,
    which is the smallest one that can still show the artist their work.
    """

    def __init__(self, limit=MAX_FILE_TOKENS):
        self._lock = threading.RLock()
        self._by_token = OrderedDict()   # token -> absolute path
        self._by_path = {}               # absolute path -> token
        self._limit = limit

    def mint(self, path):
        """A token for ``path``, or ``None`` if it may not be served.

        Same path, same token: a render mentioned in both the activity and the
        reply is one entry, and a page that reloads gets the URLs it had.
        """
        resolved = normalize_image_path(path)
        if not resolved:
            return None
        if os.path.splitext(resolved)[1].lower() not in SERVABLE_TYPES:
            return None
        # Existence is checked at mint time on purpose: a path the model
        # *mentioned* but never wrote would otherwise become a broken image in
        # the conversation, which reads as a bug in the UI rather than as an
        # answer that named a file it did not make.
        try:
            if not os.path.isfile(resolved):
                return None
        except OSError:
            return None
        with self._lock:
            token = self._by_path.get(resolved)
            if token is not None:
                self._by_token.move_to_end(token)
                return token
            token = uuid.uuid4().hex[:16]
            self._by_token[token] = resolved
            self._by_path[resolved] = token
            while len(self._by_token) > self._limit:
                old_token, old_path = self._by_token.popitem(last=False)
                if self._by_path.get(old_path) == old_token:
                    self._by_path.pop(old_path, None)
            return token

    def resolve(self, token):
        """The path a token stands for, or ``None``."""
        text = str(token or "").strip()
        if not text:
            return None
        with self._lock:
            return self._by_token.get(text)

    def count(self):
        with self._lock:
            return len(self._by_token)


FILES = FileTokens()


def path_bases(text=""):
    """Directories a relative path in a reply might be relative TO.

    The working directory first, because that is where the CLI ran; then the
    project folders, because that is what the model is usually talking about.
    A project whose name appears in the text comes first among those — turn 8
    of the dogfood run named ``molds/litwick-flame-mold_mold_bottom.stl`` and
    the project was ``litwick-lamp``, so the filename itself says which folder
    to look in, and trying that one first is both faster and less ambiguous
    than trusting alphabetical order.
    """
    bases = [working_dir()]
    root = projects_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    lowered = str(text or "").lower()
    named, others = [], []
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        (named if name.lower() in lowered else others).append(folder)
    bases.extend(named)
    bases.extend(others)
    return bases[:MAX_PATH_BASES]


def resolve_relative(candidate, bases):
    """``candidate`` as an absolute path to a real file, or ``""``.

    Existence is the whole test.  A relative path has no drive letter to prove
    it is a path at all, so ``docs/plan.svg`` in a sentence is only treated as
    a file when one of the base directories actually holds it — which is what
    stops prose from becoming a broken thumbnail in the gallery.
    """
    text = str(candidate or "").strip().strip('"').replace("\\", "/")
    if not text or text.startswith("/") or ":" in text:
        return ""
    if ".." in text.split("/"):
        return ""
    for base in bases or ():
        try:
            resolved = os.path.abspath(os.path.join(base, text))
            if os.path.isfile(resolved):
                return resolved
        except (OSError, ValueError):
            continue
    return ""


def find_file_paths(text, limit=12, bases=None):
    """Paths of servable files mentioned in ``text``, resolved to absolute.

    Used on replies and tool results, where a render's path arrives as prose
    ("saved to C:\\forge\\projects\\cup\\render.png") rather than as a field.

    Absolute paths are taken as written.  Relative ones — ``prints/body.stl``,
    which is how a model naturally names a file it just wrote inside a project
    — are resolved against ``bases`` and kept only if they exist.  Before that,
    turns 8 and 11 of the dogfood run reported ``files: []`` while six finished
    STLs sat on disk: the panel showed the artist nothing, because the only
    paths it recognised were the ones it was never given.
    """
    if not text:
        return []
    text = str(text)
    out = []
    for match in _FILE_PATH_RE.finditer(text):
        candidate = match.group(0)
        if not _ROOTED_RE.match(candidate):
            # A bare leading slash.  On POSIX that is a real root; on Windows
            # it is almost always the tail of ``molds/flame.stl`` being read as
            # ``/flame.stl``, which is how the relative path went missing in
            # the first place.  Keep it only if it is a file.
            try:
                if not os.path.isfile(candidate):
                    continue
            except (OSError, ValueError):
                continue
        if candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            return out

    if bases is None:
        bases = path_bases(text)
    if not bases:
        return out
    # No masking of what the pass above claimed is needed: the relative
    # pattern's lookbehind refuses to start after a slash, a backslash or a
    # colon, so the tail of ``C:\forge\cup\render.png`` can never be re-read as
    # ``cup\render.png``.
    for match in _REL_FILE_PATH_RE.finditer(text):
        resolved = resolve_relative(match.group(0), bases)
        if resolved and resolved not in out:
            out.append(resolved)
        if len(out) >= limit:
            break
    return out


def walk_strings(value, depth=_SCAN_DEPTH):
    """Every string inside a JSON-ish value, without recursing forever."""
    if depth < 0:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in list(value.values())[:40]:
            for found in walk_strings(item, depth - 1):
                yield found
    elif isinstance(value, (list, tuple)):
        for item in list(value)[:40]:
            for found in walk_strings(item, depth - 1):
                yield found


def file_kind(path):
    """``"image"``, ``"video"`` or ``"model"`` — how the page should draw it.

    Three classes rather than two, because the page does three different things:
    an ``<img>``, a ``<video controls>`` and a download link.
    """
    extension = os.path.splitext(str(path or ""))[1].lower()
    if extension in DISPLAY_VIDEO_EXTENSIONS:
        return "video"
    if extension in DISPLAY_IMAGE_EXTENSIONS:
        return "image"
    return "model"


def file_entry(token, path, source):
    """The public shape of one recorded file."""
    extension = os.path.splitext(path)[1].lower()
    return {
        "token": token,
        "path": path,
        "name": os.path.basename(path) or path,
        "ext": extension,
        "kind": file_kind(path),
        "source": source,
        "url": "/file/%s" % token,
    }


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

class JobStore(object):
    """Every turn this bridge has run, newest last, capped at ``MAX_JOBS``.

    Also owns the one-at-a-time rule, the single waiting place behind it, and
    the session id, because they are all the same piece of state: two turns in
    flight would fight over ``--resume``, and the message waiting its turn has
    to resume whatever the turn ahead of it produced.
    """

    def __init__(self, limit=MAX_JOBS):
        self._lock = threading.RLock()
        self._jobs = OrderedDict()
        self._limit = limit
        self._active = None
        self._pending = None
        self.session_id = None
        #: What this conversation has cost so far, in dollars.  Reset with the
        #: session, because "this conversation" is what the number means.
        self.session_cost_usd = 0.0
        #: Did the most recently finished turn fail because nobody is signed in?
        self.last_auth_error = False
        #: BLENDER's clock at the last live glance — the ``since`` of the next
        #: one.  It lives here rather than in a module global because it is
        #: per-conversation state exactly as the session id is: a new
        #: conversation has never looked at the scene, and should be told
        #: everything the feed still remembers rather than nothing.
        self.blender_seen_at = None

    # -- session ---------------------------------------------------------
    def reset_session(self):
        with self._lock:
            previous = self.session_id
            self.session_id = None
            # The running total is per-conversation: a fresh conversation has
            # not cost anything yet, so the panel's status row starts at zero.
            self.session_cost_usd = 0.0
            # ...and neither has it looked at Blender yet.
            self.blender_seen_at = None
            return previous

    # -- the live glance -------------------------------------------------
    def blender_since(self):
        """When this conversation last looked at Blender, or ``None``."""
        with self._lock:
            return self.blender_seen_at

    def note_blender_seen(self, when):
        """Remember the add-on's own clock as of this glance."""
        if when is None:
            return
        try:
            when = float(when)
        except (TypeError, ValueError):
            return
        with self._lock:
            self.blender_seen_at = when

    def forget_blender_seen(self):
        with self._lock:
            self.blender_seen_at = None

    def remember_session(self, session_id):
        with self._lock:
            if session_id:
                self.session_id = str(session_id)

    # -- lifecycle -------------------------------------------------------
    def _new_job(self, message, state):
        """A job record in the store. Caller holds the lock."""
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "state": state,
            "message": message,
            # ``started_at`` moves when a queued job is picked up, so the
            # duration means the same thing on every job.  ``created_at`` is
            # when the artist pressed Send, and never moves: it is what the web
            # UI sorts the conversation by.
            "created_at": time.time(),
            "started_at": time.time(),
            "proc": None,
            "cancelled": False,
            "activity": [],
            "activity_dropped": 0,
            #: Files this bridge recorded on the job — see :class:`FileTokens`.
            "files": [],
        }
        self._jobs[job_id] = job
        while len(self._jobs) > self._limit:
            oldest, _value = next(iter(self._jobs.items()))
            # Never drop the turn that is running, the one waiting behind it,
            # or the one being created: those three are the live conversation.
            if oldest in (self._active, self._pending, job_id):
                break
            self._jobs.pop(oldest, None)
        return job

    def submit(self, message, prompt, new_conversation=False, model=""):
        """Take the running slot, or the one waiting place behind it.

        Returns ``(job, disposition)``:

        ``("running")``  the turn started now;
        ``("queued")``   nothing was waiting, so this message is;
        ``("rejected")`` a message is already waiting — ``job`` is *that* one,
                         so the caller can name it in the refusal.

        The prompt is built by the caller and stored here rather than rebuilt at
        pickup: the artist pressed Send while looking at a particular scene, and
        that is the scene the message is about.  The model choice rides with the
        job for the same reason — a message queued as "Fast" runs as Fast even
        if the panel's selector moved while it waited.
        """
        with self._lock:
            active = self._jobs.get(self._active) if self._active else None
            busy = active is not None and active["state"] == "running"

            if busy:
                waiting = self._jobs.get(self._pending) if self._pending else None
                if waiting is not None and waiting["state"] == "queued":
                    return waiting, "rejected"

            job = self._new_job(message, "queued" if busy else "running")
            job["prompt"] = prompt
            job["new_conversation"] = bool(new_conversation)
            job["requested_model"] = normalize_model(model)

            if busy:
                self._pending = job["job_id"]
                # Under the same lock as the enqueue, so a turn that finishes
                # this instant cannot land the line on a job it just started.
                self.add_activity(job["job_id"], "status",
                                  "waiting for the current message to finish…")
                return job, "queued"

            # Starting now, so a "new conversation" ask drops the session now.
            # A queued one carries the flag instead and drops it at pickup —
            # the turn ahead of it must still finish in its own session.
            if new_conversation:
                self.session_id = None
                self.session_cost_usd = 0.0
            self._active = job["job_id"]
            return job, "running"

    def take_pending(self):
        """Promote the waiting message into the running slot, or ``None``.

        Called after every turn ends — done, errored or cancelled — because the
        artist's next message should not be held hostage by how the last one
        turned out.
        """
        with self._lock:
            active = self._jobs.get(self._active) if self._active else None
            if active is not None and active["state"] == "running":
                return None  # still busy: nothing to promote into
            job = self._jobs.get(self._pending) if self._pending else None
            self._pending = None
            if job is None or job["state"] != "queued" or job.get("cancelled"):
                return None  # cancelled while it waited, or already gone
            if job.get("new_conversation"):
                self.session_id = None
                self.session_cost_usd = 0.0
            job["state"] = "running"
            # The wait was not work: time it from the moment it actually starts,
            # so duration_ms means what it means on every other job.
            job["started_at"] = time.time()
            job["activity"] = []
            job["activity_dropped"] = 0
            self._active = job["job_id"]
            return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def is_busy(self):
        with self._lock:
            active = self._jobs.get(self._active) if self._active else None
            return bool(active is not None and active["state"] == "running")

    def is_queued(self):
        with self._lock:
            waiting = self._jobs.get(self._pending) if self._pending else None
            return bool(waiting is not None and waiting["state"] == "queued")

    # -- activity --------------------------------------------------------
    def add_activity(self, job_id, kind, label):
        """Append one activity entry and return it (``None`` if the job is gone).

        The returned dict is live: :meth:`relabel_activity` edits it in place so
        a tool call whose arguments arrive after its name does not log twice.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            entry = {"t": round(time.time(), 3),
                     "kind": str(kind or "status"),
                     "label": str(label or "")[:LABEL_LIMIT]}
            entries = job.setdefault("activity", [])
            entries.append(entry)
            # Trim from the middle: keep how it started and what it is doing now.
            while len(entries) > ACTIVITY_LIMIT - 1:
                del entries[ACTIVITY_HEAD]
                job["activity_dropped"] = int(job.get("activity_dropped") or 0) + 1
            return entry

    def relabel_activity(self, job_id, entry, label):
        with self._lock:
            if isinstance(entry, dict):
                entry["label"] = str(label or "")[:LABEL_LIMIT]

    # -- files -----------------------------------------------------------
    def note_file(self, job_id, path, source="activity"):
        """Mint a token for a path that just entered this job, if it may be.

        Returns the entry, or ``None`` when the path is not something this
        bridge will serve (wrong type, or not on disk).  Idempotent: the same
        path noted twice, once from a tool argument and once from the reply, is
        one entry with one token.
        """
        token = FILES.mint(path)
        if token is None:
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            entries = job.setdefault("files", [])
            for entry in entries:
                if entry["token"] == token:
                    return entry
            entry = file_entry(token, FILES.resolve(token) or path, source)
            entries.append(entry)
            return entry

    def note_files_in(self, job_id, value, source="activity"):
        """Note every servable path inside a value (a reply, a tool's args…).

        The base directories a relative path could be relative to are worked
        out ONCE per value rather than once per string: it is a folder listing,
        and this runs on every tool result of every turn.
        """
        found = []
        texts = [text for text in walk_strings(value) if text]
        if not texts:
            return found
        bases = path_bases(" ".join(texts)[:20000])
        for text in texts:
            for path in find_file_paths(text, bases=bases):
                entry = self.note_file(job_id, path, source)
                if entry is not None:
                    found.append(entry)
        return found

    def reset_activity(self, job_id):
        """Forget an attempt's activity — used when a turn is retried."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["activity"] = []
                job["activity_dropped"] = 0

    def snapshot(self, job_id):
        """The public view of a job, copied under the lock."""
        with self._lock:
            job = self._jobs.get(job_id)
            return public_job(job, self.session_cost_usd)

    def snapshot_all(self):
        """Every job still in memory, oldest first.

        The web UI is a page that can be opened, closed and reloaded at any
        point in a conversation, so it needs the whole history in one request
        rather than a job id it was never told about.
        """
        with self._lock:
            return [public_job(job, self.session_cost_usd)
                    for job in self._jobs.values()]

    def attach_proc(self, job_id, proc):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["proc"] = proc
                if job.get("cancelled"):
                    _terminate(proc)

    def finish(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.get("cancelled") and fields.get("state") != "cancelled":
                fields = dict(fields)
                fields["state"] = "cancelled"
                fields.setdefault("error", "Cancelled.")
            job.update(fields)
            job["proc"] = None
            job["duration_ms"] = int((time.time() - job["started_at"]) * 1000)
            job["finished_at"] = time.time()
            self._record_outcome(job)
        # Outside the lock only in spirit — RLock, same thread — but written as
        # its own step because it touches the disk: a render named in the reply
        # gets a token so the web UI can show it under the answer.
        reply = fields.get("reply")
        if reply:
            self.note_files_in(job_id, reply, "reply")

    def _record_outcome(self, job):
        """Fold a finished turn into the two session-wide signals. Lock held.

        A cancelled turn deliberately touches neither: it neither cost anything
        worth counting nor proved anything about whether we are signed in.

        A timed-out turn counts exactly like a finished one.  It was fifteen
        minutes of a paid model doing real work; the dogfood run's total read
        $7.29 and was not $7.29, because the only turn that hit the timeout was
        silently billed at zero.
        """
        state = job.get("state")
        if state in ("done", "timeout"):
            try:
                self.session_cost_usd += float(job.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass  # a build that reported cost as something odd: skip it
            # It answered, so whatever went wrong before is over.
            self.last_auth_error = False
        elif state == "error":
            self.last_auth_error = looks_like_auth_error(job.get("error"))

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["state"] == "queued":
                # It never started, so there is no process to stop: drop it out
                # of the waiting place and land it cancelled, so the panel stops
                # polling and take_pending() can never resurrect it.
                if self._pending == job_id:
                    self._pending = None
                job["cancelled"] = True
                job["state"] = "cancelled"
                job["error"] = "Cancelled before it started."
                job["duration_ms"] = 0
                return job
            if job["state"] != "running":
                return job
            job["cancelled"] = True
            proc = job.get("proc")
        if proc is not None:
            _terminate(proc)
        return job


def _terminate(proc):
    """SIGINT is unreliable on Windows; terminate, then kill if it lingers."""
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        return
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


JOBS = JobStore()


def public_activity(job):
    """A copy of the job's activity, with the trimmed middle marked.

    The marker is synthesised here rather than stored so it can never be
    trimmed away itself, and so the count stays right as more is dropped.
    """
    entries = [dict(entry) for entry in (job.get("activity") or [])]
    dropped = int(job.get("activity_dropped") or 0)
    if dropped > 0:
        anchor = entries[ACTIVITY_HEAD]["t"] if len(entries) > ACTIVITY_HEAD else time.time()
        entries.insert(ACTIVITY_HEAD, {
            "t": anchor,
            "kind": "status",
            "label": "… %d earlier steps not shown …" % dropped,
        })
    return entries


def public_job(job, session_cost_usd=0.0):
    """The subset of a job the panel is allowed to see."""
    if job is None:
        return None
    out = {"job_id": job["job_id"], "state": job["state"]}
    # ``stalled`` / ``silent_for`` ride along on a timed-out turn so a client
    # can tell "it was working and ran out of clock" from "it hung": the same
    # distinction the reply's last sentence makes in words.
    for key in ("reply", "session_id", "cost_usd", "duration_ms", "error",
                "model", "usage", "num_turns", "stalled", "silent_for"):
        if job.get(key) is not None:
            out[key] = job[key]
    # What was asked, and when.  The panel ignores both; the web UI draws the
    # conversation from them after a page reload, when nothing else remembers
    # what the artist typed.
    out["message"] = str(job.get("message") or "")[:8000]
    out["created_at"] = round(float(job.get("created_at")
                                    or job.get("started_at") or 0.0), 3)
    out["started_at"] = round(float(job.get("started_at") or 0.0), 3)
    if job.get("finished_at"):
        out["finished_at"] = round(float(job["finished_at"]), 3)
    # Attachments in, renders out — each as a token, never as a path the client
    # could have chosen (the path is shown because it is worth reading, not
    # because it is what gets fetched).
    out["files"] = [dict(entry) for entry in (job.get("files") or [])]
    # What was ASKED for, alongside "model" (what the CLI says it ran).  The
    # panel can then say "you asked for Deepest" on a job that is still running,
    # before there is any result to read a model off.
    if job.get("requested_model"):
        out["requested_model"] = job["requested_model"]
    # The conversation's running total rides on every job snapshot as well as
    # on /health: the panel already polls the job, and a second request just to
    # redraw one number would be a request per second for nothing.
    out["session_cost_usd"] = round(float(session_cost_usd or 0.0), 6)
    # Always present, running or finished: the panel draws it live and the
    # finished job keeps it so the artist can still read what was done.
    out["activity"] = public_activity(job)
    dropped = int(job.get("activity_dropped") or 0)
    if dropped:
        out["activity_dropped"] = dropped
    return out


# ---------------------------------------------------------------------------
# running a turn
# ---------------------------------------------------------------------------

def _drain(stream, sink):
    """Read a pipe to EOF into ``sink``. Keeps stderr from filling and blocking."""
    try:
        for chunk in iter(stream.readline, b""):
            sink.append(chunk)
    except Exception:  # noqa: BLE001 - a closed pipe is the normal ending
        pass


def read_stream(proc, limit, recorder, stall_limit=None):
    """Consume the CLI's NDJSON stdout, feeding ``recorder`` as it goes.

    Returns ``{"result", "last", "stdout_tail", "stderr", "code", "timed_out",
    "stalled", "silent_for"}``.

    The watchdog is a polling thread rather than a one-shot timer because it
    now answers two questions at once: has the turn used its whole budget, and
    has the CLI said *anything* recently.  ``readline`` blocks, so neither can
    be a read deadline; when either fires the process is terminated, the pipe
    closes and this loop ends on its own.

    The second clock is the point.  In the dogfood run 595 of turn 3's 900
    seconds were dead air with the work still ahead of it — a hung process ate
    the budget the turn needed.  Killing a stall on its own clock turns a lost
    quarter of an hour into a five-minute "it went quiet, here is where we
    were", with the rest of the budget still unspent.
    """
    stderr_chunks = []
    stderr_thread = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_chunks),
        name="ForgeAssistantStderr", daemon=True)
    stderr_thread.start()

    if stall_limit is None:
        stall_limit = stall_timeout_s()
    stall_limit = max(0.0, float(stall_limit or 0.0))
    started = time.time()
    state = {"timed_out": False, "stalled": False, "last_event": started,
             "silent_for": 0.0}
    stop = threading.Event()

    # Fine enough that a five-second stall limit in a test is honoured within a
    # tick, coarse enough that a fifteen-minute turn is not a busy loop.
    tick = max(0.05, min(1.0, float(limit) / 20.0,
                         (stall_limit or float(limit)) / 20.0))

    def watch():
        while not stop.wait(tick):
            now = time.time()
            if now - started >= limit:
                state["timed_out"] = True
                state["silent_for"] = now - state["last_event"]
                _terminate(proc)
                return
            silent = now - state["last_event"]
            if stall_limit and silent >= stall_limit:
                state["timed_out"] = True
                state["stalled"] = True
                state["silent_for"] = silent
                _terminate(proc)
                return

    watchdog = threading.Thread(target=watch, name="ForgeAssistantWatchdog",
                                daemon=True)
    watchdog.start()

    result = None
    last = None
    tail = deque(maxlen=20)
    try:
        for raw in iter(proc.stdout.readline, b""):
            # ANY byte counts as life, parseable or not: the stall clock asks
            # whether the process is alive, not whether it is making sense.
            state["last_event"] = time.time()
            line = raw.decode("utf-8", "replace")
            tail.append(line.rstrip("\r\n"))
            payload = parse_stream_line(line)
            if payload is None:
                continue  # noise, a half-line, an event shape we do not know
            last = payload
            if looks_like_result(payload):
                result = payload
            recorder.feed(payload)
    except Exception as exc:  # noqa: BLE001 - a broken pipe ends the turn, not the server
        log("[assistant] stream read failed: %s" % exc)
    finally:
        stop.set()
        watchdog.join(timeout=2.0)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            _terminate(proc)
        stderr_thread.join(timeout=2.0)

    return {
        "result": result,
        "last": last,
        "stdout_tail": "\n".join(tail),
        "stderr": b"".join(stderr_chunks).decode("utf-8", "replace"),
        "code": proc.returncode,
        "timed_out": state["timed_out"],
        "stalled": state["stalled"],
        "silent_for": round(float(state["silent_for"]), 3),
    }


def run_turn(job_id, prompt, session_id, model=""):
    """Spawn the CLI, read its event stream, land the result. Never raises."""
    claude_path = resolve_claude()
    if not claude_path:
        JOBS.finish(job_id, state="error", error=INSTALL_HINT)
        return

    cwd = working_dir()
    limit = timeout_s()
    stall = stall_timeout_s()
    modes = list(PERMISSION_MODES)
    last_error = None

    while modes:
        mode = modes.pop(0)
        # A retry under a different flag starts its activity list over; the
        # rejected attempt did nothing worth showing.
        JOBS.reset_activity(job_id)
        argv = build_argv(claude_path, prompt, session_id=session_id,
                          permission_mode=mode, model=model)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            JOBS.finish(job_id, state="error",
                        error="Could not start the Claude CLI (%s): %s" % (claude_path, exc))
            return

        JOBS.attach_proc(job_id, proc)
        recorder = ActivityRecorder(JOBS, job_id)
        try:
            outcome = read_stream(proc, limit, recorder, stall_limit=stall)
        except Exception as exc:  # noqa: BLE001
            JOBS.finish(job_id, state="error", error="Claude CLI failed: %s" % exc)
            return

        if outcome["timed_out"]:
            # NOT an error, and not empty.  The turn ran out of clock; what it
            # already said and did is the answer, and it is checkpointed here
            # rather than thrown away.  See :func:`checkpoint_reply` (B-1).
            stalled = bool(outcome.get("stalled"))
            JOBS.finish(
                job_id,
                state="timeout",
                reply=checkpoint_reply(recorder, limit, stalled=stalled,
                                       silent_for=outcome.get("silent_for") or 0.0),
                cost_usd=recorder.cost(),
                usage=recorder.usage(),
                session_id=JOBS.session_id,
                stalled=stalled,
                silent_for=outcome.get("silent_for") or 0.0)
            return

        job = JOBS.get(job_id)
        if job is not None and job.get("cancelled"):
            JOBS.finish(job_id, state="cancelled", error="Cancelled.")
            return

        stderr = outcome["stderr"]
        code = outcome["code"]

        # Step down through the permission-mode flags an older build rejects.
        if code not in (0, 2) and mode and _looks_like_bad_flag(stderr, "--permission-mode"):
            last_error = _tail(stderr)
            continue

        payload = outcome["result"]
        streamed = recorder.text_blocks()
        if payload is None and code == 0:
            # Exited clean but never printed a result event. The last line that
            # parsed is the best answer available; the text that streamed past
            # is the reply itself.
            payload = outcome["last"]
            if not isinstance(payload, dict):
                payload = salvage_json(outcome["stdout_tail"]) or {}
        if payload is None:
            detail = _tail(stderr) or _tail(outcome["stdout_tail"]) or "no output"
            JOBS.finish(
                job_id, state="error",
                error="The Claude CLI did not return readable JSON (exit %s).\n%s"
                      % (code, detail))
            return

        if payload.get("is_error") or payload.get("subtype") in ("error", "error_max_turns"):
            message = extract_reply(payload) or payload.get("error") or "The assistant errored."
            message = friendly_error(str(message))
            JOBS.finish(job_id, state="error", error=message[:4000],
                        session_id=payload.get("session_id"))
            JOBS.remember_session(payload.get("session_id"))
            return

        # Every text block, not just the CLI's final one: the reasoning the
        # model wrote before its tool calls is the part an artist most wants
        # and the part the old one-block reply dropped (F-1).
        reply = compose_reply(streamed, extract_reply(payload))
        if not reply and code not in (0, 2):
            JOBS.finish(job_id, state="error",
                        error="The Claude CLI exited %s with no reply.\n%s"
                              % (code, _tail(stderr)))
            return

        new_session = payload.get("session_id")
        JOBS.remember_session(new_session)
        JOBS.finish(
            job_id,
            state="done",
            reply=reply or "(the assistant returned an empty reply)",
            session_id=new_session,
            cost_usd=payload.get("total_cost_usd"),
            model=payload.get("model"),
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            num_turns=payload.get("num_turns"),
        )
        return

    JOBS.finish(job_id, state="error",
                error="The Claude CLI rejected every permission-mode flag this bridge "
                      "knows about.\n%s" % (last_error or ""))


def _run_and_continue(job_id, prompt, session_id, model=""):
    """Run one turn, then start whatever was waiting behind it.

    The pickup lives in a ``finally`` on purpose: done, errored, cancelled or
    crashed, the message the artist queued has to get its turn.  Nothing here
    may raise — this is the top of a worker thread, and an exception would
    silently strand the queue.
    """
    try:
        run_turn(job_id, prompt, session_id, model)
    except Exception as exc:  # noqa: BLE001 - a dead thread must not stall the queue
        log("[assistant] turn %s failed: %s" % (job_id, exc))
        JOBS.finish(job_id, state="error",
                    error="The assistant stopped unexpectedly: %s" % exc)
    finally:
        job = JOBS.get(job_id)
        if job is not None and job.get("state") == "running":
            # run_turn always finishes its job; if some path ever does not, the
            # running slot would stay claimed forever and the panel would spin.
            JOBS.finish(job_id, state="error",
                        error="The assistant stopped without answering.")
        try:
            start_next()
        except Exception as exc:  # noqa: BLE001
            log("[assistant] could not start the queued message: %s" % exc)


def start_turn(job):
    """Spawn the worker thread for a job that already holds the running slot.

    The session id is resolved *here*, not when the message was submitted: a
    queued message must resume the conversation the turn ahead of it produced.
    The model, by contrast, is the one the job was submitted with — it is the
    artist's choice for *this* message, not for whenever it reached the front.
    """
    thread = threading.Thread(
        target=_run_and_continue,
        args=(job["job_id"], job.get("prompt") or job.get("message") or "",
              JOBS.session_id, job.get("requested_model") or ""),
        name="ForgeAssistantTurn", daemon=True)
    thread.start()
    return thread


def start_next():
    """Promote and start the waiting message, if there is one."""
    job = JOBS.take_pending()
    if job is not None:
        start_turn(job)
    return job


# ---------------------------------------------------------------------------
# Phase 9 — serving the page
# ---------------------------------------------------------------------------

#: One path segment of a static asset: letters, digits, dot, dash, underscore.
#: Deliberately no slash and no ``..``, so traversal is refused by the shape of
#: the name before any path arithmetic happens.  It is the check that cannot be
#: got past by encoding, because it is an allow-list of characters: ``../`` does
#: not match it, and neither does ``%2e%2e%2f`` (this handler never decodes the
#: path, so a percent sign is simply not one of the characters allowed).
_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def webui_asset(name):
    """``(bytes, content type)`` for a file in ``assistant/webui/``, or ``None``.

    Three gates, all of which must pass: the name is one plain segment, the
    extension is one a browser has a use for, and the resolved path is still
    inside the folder.  The last is belt-and-braces against a symlink; the
    first two are what actually refuse ``../../system_prompt.md``.
    """
    text = str(name or "").strip()
    if not text or not _ASSET_NAME_RE.match(text) or text.startswith("."):
        return None
    extension = os.path.splitext(text)[1].lower()
    content_type = ASSET_TYPES.get(extension)
    if content_type is None:
        return None
    root = os.path.abspath(WEBUI_DIR)
    path = os.path.abspath(os.path.join(root, text))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(), content_type
    except OSError:
        return None


def read_token_file(token):
    """``(bytes, content type, filename)`` for a minted token, or ``None``."""
    path = FILES.resolve(token)
    if not path:
        return None
    content_type = SERVABLE_TYPES.get(os.path.splitext(path)[1].lower())
    if content_type is None:
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(), content_type, os.path.basename(path)
    except OSError:
        # Minted, then moved or deleted: the token was real, the file is gone.
        return None


# ---------------------------------------------------------------------------
# Phase 9 — uploads: browser bytes in, a path on this machine out
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_upload_name(name):
    """A filename that is only ever a filename.

    Whatever the browser called it, what lands on disk is a sanitised stem, the
    original extension, and a random prefix so two sketches called ``ref.png``
    are two files.
    """
    base = os.path.basename(str(name or "").replace("\\", "/").rstrip("/"))
    stem, extension = os.path.splitext(base)
    stem = _SAFE_NAME_RE.sub("-", stem).strip("-.") or "image"
    extension = _SAFE_NAME_RE.sub("", extension).lower()
    return "%s-%s%s" % (uuid.uuid4().hex[:8], stem[:60], extension)


def upload_error(name, data):
    """Why these bytes cannot be saved, or ``""``.

    Type by extension *and* by the bytes themselves: an extension is a claim,
    and this writes a file to the artist's disk that the model is then told to
    open.  A ``.png`` that does not start like a PNG is refused here.
    """
    extension = os.path.splitext(str(name or ""))[1].lower()
    if extension not in IMAGE_EXTENSIONS:
        return ("Only images can be attached (%s). %s is not one."
                % (" ".join(IMAGE_EXTENSIONS), os.path.basename(str(name)) or "that file"))
    if not data:
        return "That file is empty."
    cap = max_upload_bytes()
    if len(data) > cap:
        return ("That image is %.1f MB. The limit is %d MB — resize it, or point "
                "the assistant at the file with a message instead."
                % (len(data) / 1048576.0, cap // 1048576))
    magic = IMAGE_MAGIC.get(extension)
    if magic and not any(data.startswith(prefix) for prefix in magic):
        return ("That file is named %s but its contents are not a %s image."
                % (extension, extension.lstrip(".")))
    return ""


def prune_uploads(directory, keep=MAX_UPLOADS_KEPT):
    """Delete all but the newest ``keep`` uploads. Best effort, never fatal."""
    try:
        names = [os.path.join(directory, n) for n in os.listdir(directory)]
    except OSError:
        return 0
    # Dotfiles are the folder's own housekeeping (its .gitignore), never an
    # upload, so they are not candidates for eviction.
    files = [p for p in names
             if os.path.isfile(p) and not os.path.basename(p).startswith(".")]
    if len(files) <= keep:
        return 0
    try:
        files.sort(key=os.path.getmtime)
    except OSError:
        return 0
    removed = 0
    for path in files[:len(files) - keep]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def save_upload(name, data):
    """Write an accepted upload; returns its absolute path."""
    directory = uploads_dir()
    existed = os.path.isdir(directory)
    os.makedirs(directory, exist_ok=True)
    if not existed:
        # The default folder is inside the repo, so that the artist can find
        # (and empty) it.  It ignores itself rather than making every clone
        # edit .gitignore: sketches somebody dropped on a page are not source.
        try:
            with open(os.path.join(directory, ".gitignore"), "w") as handle:
                handle.write("*\n")
        except OSError:
            pass
    path = os.path.join(directory, safe_upload_name(name))
    with open(path, "wb") as handle:
        handle.write(data)
    prune_uploads(directory)
    return path


def decode_base64(text):
    """Bytes from a base64 string or a ``data:`` URL, or ``None``.

    Strict on purpose (``validate=True``): the lenient decoder silently drops
    every character outside the alphabet, so a body of pure garbage comes back
    as zero bytes and the artist is told their image is "empty" when the truth
    is that it never decoded.  Whitespace is stripped first, because that is
    the one bit of noise a real base64 payload legitimately carries.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma == -1:
            return None
        raw = raw[comma + 1:]
    raw = "".join(raw.split())
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


def parse_multipart(body, content_type):
    """``(filename, bytes)`` from a simple multipart body, or ``(None, None)``.

    A deliberately small parser: one file part, the first one found.  ``cgi``
    is gone from the standard library in 3.13 and the alternative is a
    dependency, which this process does not get to have.  The page itself
    posts base64 JSON; this exists so ``curl -F`` works too.
    """
    marker = "boundary="
    index = str(content_type or "").find(marker)
    if index == -1:
        return None, None
    boundary = content_type[index + len(marker):].split(";")[0].strip().strip('"')
    if not boundary:
        return None, None
    separator = b"--" + boundary.encode("utf-8", "replace")
    for part in body.split(separator):
        split = part.find(b"\r\n\r\n")
        if split == -1:
            continue
        headers = part[:split].decode("utf-8", "replace")
        if "filename=" not in headers:
            continue
        filename = headers.split("filename=", 1)[1]
        filename = filename.split("\r\n")[0].strip().strip(";").strip().strip('"')
        payload = part[split + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        return filename, payload
    return None, None


# ---------------------------------------------------------------------------
# Phase 9 — the Blender socket, from here (flows passthrough)
# ---------------------------------------------------------------------------

class BlenderDown(Exception):
    """Nothing is listening on the add-on's port."""


class BlenderRefused(Exception):
    """The add-on answered, and said no."""


#: What to tell an artist whose Blender is not listening.  Same words the MCP
#: server uses, because it is the same situation and they will hit it twice.
BLENDER_DOWN_HINT = (
    "Blender is not running, or the Forge add-on's server is stopped. Open "
    "Blender, press N in the 3D view, click the Forge tab, and press Start "
    "Server — then try again. (Expected a listener on %s.)")


def blender_command(command, params=None, timeout=FLOW_LIST_TIMEOUT):
    """Send one newline-delimited JSON command to the add-on and read its reply.

    A minimal reimplementation of ``mcp/forge_mcp/blender_client.py`` — same
    wire format, same one-connection-per-command rule — because this process is
    stdlib-only and cannot import the MCP package.  Kept to what the two flow
    passthroughs need: connect, send a line, read a line, close.
    """
    host, blender_port = blender_address()
    address = "%s:%d" % (host, blender_port)
    request = {"id": uuid.uuid4().hex[:8], "type": str(command),
               "params": params or {}}
    try:
        payload = json.dumps(request, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BlenderRefused("Those parameters are not valid JSON: %s" % exc)

    try:
        sock = socket.create_connection((host, blender_port), timeout=3.0)
    except OSError:
        raise BlenderDown(BLENDER_DOWN_HINT % address)

    chunks = []
    try:
        sock.settimeout(timeout)
        sock.sendall(payload.encode("utf-8") + b"\n")
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                raise BlenderRefused(
                    "Blender did not answer within %.0f seconds. The step may "
                    "still be running on its main thread — check the Blender "
                    "window." % timeout)
            except OSError as exc:
                raise BlenderDown("%s (connection dropped: %s)"
                                  % (BLENDER_DOWN_HINT % address, exc))
            if not chunk:
                if chunks:
                    raise BlenderRefused(
                        "Blender closed the connection before finishing its "
                        "answer. Check Blender's system console.")
                raise BlenderDown(BLENDER_DOWN_HINT % address)
            newline = chunk.find(b"\n")
            if newline != -1:
                chunks.append(chunk[:newline])
                break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > 64 * 1024 * 1024:
                raise BlenderRefused("Blender's answer was too large to read.")
    finally:
        try:
            sock.close()
        except OSError:
            pass

    line = b"".join(chunks).decode("utf-8", "replace")
    try:
        response = json.loads(line)
    except ValueError:
        raise BlenderRefused("Blender sent something that is not JSON: %s"
                             % line[:300])
    if not isinstance(response, dict):
        raise BlenderRefused("Blender sent a %s, not a response object."
                             % type(response).__name__)
    if response.get("status") == "error":
        raise BlenderRefused(str(response.get("message") or "no reason given"))
    result = response.get("result")
    return result if isinstance(result, dict) else {}


def blender_listening(timeout=1.0):
    """Is something accepting connections on the add-on's port?"""
    host, blender_port = blender_address()
    try:
        with socket.create_connection((host, blender_port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# live context — what Blender IS, read a moment before the turn starts
# ---------------------------------------------------------------------------
#
# The ask, verbatim: "it should have live context awareness of blender otherwise
# its useless as a copilot."  Before this, the assistant saw the scene only when
# it chose to call a tool, which meant it saw the thing it asked about and
# nothing else; an artist who moved a wall and dropped into sculpt mode between
# two messages was invisible.  So every chat turn now opens with a glance:
# one socket call to ``get_activity``, one short line in the prompt.
#
# Four rules, and all four are about not making the artist pay for it:
#
# * it is capped at :data:`LIVE_CONTEXT_BUDGET` characters, newest first;
# * it costs at most :data:`LIVE_CONTEXT_TIMEOUT` seconds, and the connect probe
#   is shorter still, because "Blender is closed" is the common case;
# * it NEVER fails a turn — every exception on this path is swallowed, and a
#   probe that blew up simply produces no block;
# * it goes into the chat route and nowhere else.  The library, the workbench and
#   the flow passthroughs are not conversations and have nothing to be aware of.

def relative_age(seconds):
    """``2 min ago`` — how long ago, in the words a person would use."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "just now"
    if seconds < 5:
        return "just now"
    if seconds < 90:
        return "%ds ago" % int(round(seconds))
    if seconds < 5400:
        return "%d min ago" % int(round(seconds / 60.0))
    return "%d h ago" % int(round(seconds / 3600.0))


def mode_word(mode):
    """``EDIT_MESH`` -> ``Edit``. An unknown mode is passed through, not guessed."""
    text = str(mode or "").strip()
    if not text:
        return "unknown"
    return MODE_WORDS.get(text.upper(), text)


def _mm_text(value):
    try:
        return "%g" % float(value)
    except (TypeError, ValueError):
        return str(value)


def describe_activity_event(event):
    """One event as a clause: ``artist moved FP:wall-x0-garage (2 min ago)``."""
    if not isinstance(event, dict):
        return ""
    source = "Forge" if str(event.get("source")) == "forge" else "artist"
    kind = str(event.get("kind") or "")
    verb = ACTIVITY_VERBS.get(kind, kind or "touched")
    name = str(event.get("object") or "")
    if kind in ("undo", "redo"):
        text = "%s %s" % (source, verb)
    elif kind == "mode":
        text = "%s switched to %s" % (source, mode_word(event.get("detail")))
    else:
        text = "%s %s %s" % (source, verb, name or "something")
    try:
        count = int(event.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    if count > 1:
        text += " x%d" % count
    return "%s (%s)" % (text, relative_age(event.get("ago")))


def live_headline(info):
    """Where the artist is standing: mode, active, selection, cursor."""
    parts = ["mode %s" % mode_word(info.get("mode"))]
    active = str(info.get("active") or "")
    parts.append("active %s" % active if active else "nothing active")
    selected = [str(name) for name in (info.get("selected") or []) if str(name)]
    if not selected:
        parts.append("selected nothing")
    elif len(selected) <= 2:
        parts.append("selected %d (%s)" % (len(selected), ", ".join(selected)))
    else:
        parts.append("selected %d (%s, +%d)"
                     % (len(selected), ", ".join(selected[:2]), len(selected) - 2))
    cursor = info.get("cursor_mm")
    if isinstance(cursor, (list, tuple)) and len(cursor) == 3:
        parts.append("cursor_mm [%s]" % ", ".join(_mm_text(v) for v in cursor))
    return "; ".join(parts)


def _clip_live(text, budget):
    """The budget, enforced on the finished sentence rather than trusted.

    Named apart from :func:`_clip` (the activity sidebar's, with its ellipsis
    character and its own limit) because the two are different rules for
    different readers, and one shadowing the other is a bug that shows up three
    tests away from where it was written.
    """
    if len(text) <= budget:
        return text
    return text[:max(0, budget - 3)].rstrip(" ,;") + "..."


def format_live_context(info, budget=LIVE_CONTEXT_BUDGET):
    """The one-line block, or ``""`` when there is nothing honest to say."""
    if not isinstance(info, dict) or not info:
        return ""
    if not info.get("up"):
        return "%s %s" % (LIVE_CONTEXT_MARKER, LIVE_CONTEXT_DOWN)
    if not info.get("state"):
        # Listening, but it told us nothing — busy main thread, dropped
        # connection.  Said plainly: an assistant that reports a scene it could
        # not read is worse than one that admits it did not get a look.
        return "%s %s" % (LIVE_CONTEXT_MARKER, LIVE_CONTEXT_UNREADABLE)

    head = live_headline(info)
    if not info.get("feed"):
        return _clip_live("%s %s; %s." % (LIVE_CONTEXT_MARKER, head,
                                     LIVE_CONTEXT_NO_FEED), budget)

    events = [event for event in (info.get("events") or [])
              if isinstance(event, dict)]
    if not events:
        tail = ("this is your first look at the scene this session"
                if info.get("since") is None
                else "nothing has changed since your last look")
        return _clip_live("%s %s; %s." % (LIVE_CONTEXT_MARKER, head, tail), budget)

    prefix = "%s %s; since your last look: " % (LIVE_CONTEXT_MARKER, head)
    phrases = [text for text in (describe_activity_event(event)
                                 for event in events) if text]
    try:
        unsent = max(0, int(info.get("more") or 0))
    except (TypeError, ValueError):
        unsent = 0

    kept = []
    for index, phrase in enumerate(phrases):
        remaining = len(phrases) - index - 1 + unsent
        # Reserve room for the ", +N more" note whenever there is anything left
        # to be more than: a block that spent its last characters on half a
        # clause would hide the fact that it was trimmed at all.
        reserve = len(", +%d more" % remaining) if remaining else 0
        candidate = prefix + ", ".join(kept + [phrase]) + "."
        if len(candidate) + reserve > budget and kept:
            break
        if len(candidate) > budget:
            break
        kept.append(phrase)

    dropped = len(phrases) - len(kept) + unsent
    if not kept:
        return _clip_live("%s %s; %d changes since your last look."
                     % (LIVE_CONTEXT_MARKER, head, len(phrases) + unsent), budget)
    body = ", ".join(kept)
    if dropped > 0:
        body += ", +%d more" % dropped
    return _clip_live(prefix + body + ".", budget)


def probe_blender_live(since=None, timeout=LIVE_CONTEXT_TIMEOUT):
    """Read the add-on's live state for one turn. Never raises.

    ``get_activity`` already carries the mode, the active object, the selection
    and the 3D cursor, so the happy path is ONE socket round trip rather than two
    — the fields are the same fields ``get_scene_info`` reports, and asking twice
    for them would double the wait for nothing.  ``get_scene_info`` is the
    fallback, for an add-on old enough not to have the feed.
    """
    info = {"up": False, "feed": False, "state": False, "since": since,
            "events": [], "more": 0, "now": None, "error": ""}
    try:
        if not blender_listening(timeout=LIVE_CONTEXT_CONNECT_TIMEOUT):
            return info
        info["up"] = True

        params = {"limit": LIVE_CONTEXT_LIMIT}
        if since is not None:
            params["since"] = since
        result = None
        try:
            result = blender_command("get_activity", params, timeout=timeout)
        except BlenderRefused as exc:
            # "Unknown command 'get_activity'" is an add-on built before the
            # feed, not a failure: degrade to the state half of the question.
            info["error"] = str(exc)[:200]

        if isinstance(result, dict) and result:
            info["feed"] = True
            info["state"] = True
            info["events"] = result.get("events") or []
            info["mode"] = result.get("mode")
            info["active"] = result.get("active")
            info["selected"] = result.get("selected") or []
            info["cursor_mm"] = result.get("cursor_mm")
            info["now"] = result.get("now")
            info["collections_touched"] = result.get("collections_touched") or []
            try:
                info["more"] = max(0, int(result.get("matched") or 0)
                                   - len(info["events"]))
            except (TypeError, ValueError):
                info["more"] = 0
            return info

        if info["error"] and "unknown command" not in info["error"].lower():
            # It is listening and it did not answer — a hung main thread, a
            # dropped connection, a refusal about something else.  A second
            # call would only buy a second helping of the same wait, and the
            # artist is sitting in front of a Send button.
            return info

        scene = blender_command("get_scene_info", {}, timeout=timeout)
        if isinstance(scene, dict):
            info["state"] = True
            info["mode"] = scene.get("mode")
            info["active"] = scene.get("active")
            info["selected"] = scene.get("selected") or []
            info["cursor_mm"] = scene.get("cursor_mm")
    except BlenderDown as exc:
        info["up"] = False
        info["error"] = str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 - awareness must never cost a turn
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def live_context_enabled():
    """Is the glance switched on?  ``FORGE_ASSISTANT_LIVE_CONTEXT=0`` turns it off.

    On by default, because a copilot that cannot see the scene is the problem
    this exists to fix.  The switch is for the two cases where a probe is the
    wrong thing: a test harness that must never open a connection to whatever is
    on the add-on's port, and an artist who wants the turn to start the instant
    they press Send on a machine where Blender is slow to answer.
    """
    value = str(_env("FORGE_ASSISTANT_LIVE_CONTEXT", "1")).strip().lower()
    return value not in ("0", "off", "false", "no")


def live_context_for_turn(since=None):
    """``(block text, the add-on's clock at the moment of the glance)``.

    The clock comes back so the caller can remember it as "when I last looked":
    it is BLENDER's own ``time.time()``, not this process's, which is what makes
    ``since`` filtering exact across two processes with no clock-skew reasoning
    anywhere.
    """
    if not live_context_enabled():
        return "", None
    try:
        info = probe_blender_live(since=since)
        return format_live_context(info), info.get("now")
    except Exception:  # noqa: BLE001 - see probe_blender_live
        return "", None


# ---------------------------------------------------------------------------
# Phase 11 — the workbench: the geometry service, from here
# ---------------------------------------------------------------------------

class ServiceDown(Exception):
    """Nothing is listening on the geometry service's port."""


class ServiceRefused(Exception):
    """The service answered, and said no — usually about the artist's script."""


def service_post(endpoint, payload, timeout=PARSE_TIMEOUT):
    """One JSON POST to the geometry service.

    A minimal reimplementation of ``mcp/forge_mcp/service_client.py`` for the
    same reason :func:`blender_command` reimplements the socket client: this
    process is stdlib-only and cannot import the MCP package.  The two failure
    modes are kept apart deliberately — "not running" is a button to press and
    "your script raised" is a message to read, and merging them produces the
    worst error text in the product.
    """
    base = service_urls()["geometry"]
    try:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ServiceRefused("Those values are not valid JSON: %s" % exc)
    request = urllib.request.Request(
        base + endpoint, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(256 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        # 400 is the service's own way of saying "this script/these values are
        # wrong", and its message is the useful half of that answer.
        detail = b""
        try:
            detail = exc.read(200000)
        except OSError:
            pass
        text = detail.decode("utf-8", "replace")
        message = None
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            message = parsed.get("error") or parsed.get("message")
        raise ServiceRefused(str(message or text or "HTTP %s" % exc.code)[:4000])
    except urllib.error.URLError:
        raise ServiceDown(SERVICE_DOWN_HINT % base)
    except socket.timeout:
        raise ServiceRefused(
            "The shape service did not finish within %.0f seconds. The script "
            "may be building something very heavy." % timeout)
    except OSError:
        raise ServiceDown(SERVICE_DOWN_HINT % base)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ServiceRefused("The shape service sent something that is not JSON.")
    if not isinstance(data, dict):
        raise ServiceRefused("The shape service sent a %s, not an answer object."
                             % type(data).__name__)
    return data


# ---------------------------------------------------------------------------
# Phase 11 — the workbench: projects on disk
# ---------------------------------------------------------------------------

def object_name_for_script(path):
    """The Blender object a part script builds into.

    docs/architecture.md's part-object naming convention, third implementation:
    the script's stem, or its *folder* when the stem is generic, capped at 63
    bytes.  ``projects/eevee-bowl-holder/part.py`` is "eevee-bowl-holder", not
    "part" — which is the whole reason the rule exists.
    """
    base = os.path.basename(str(path or ""))
    stem = os.path.splitext(base)[0]
    parent = os.path.basename(os.path.dirname(str(path or "")))
    if stem.lower() in GENERIC_STEMS and parent:
        stem = parent
    stem = stem.strip() or "Part"
    encoded = stem.encode("utf-8")[:MAX_OBJECT_NAME]
    return encoded.decode("utf-8", "ignore") or "Part"


def project_dir(name):
    """The absolute path of one project folder, or ``None``.

    Same three gates as :func:`webui_asset`, and for the same reason: this name
    arrives in a URL from a browser, and ``/projects/..%2F..%2Fsystem_prompt/``
    must fail on the *shape* of the name rather than on path arithmetic.
    """
    text = str(name or "").strip()
    if not text or not _PROJECT_NAME_RE.match(text):
        return None
    # Not just ``.`` and ``..``: Windows strips trailing dots and spaces off a
    # path component when it opens one, so ``projects/.../models`` is opened as
    # ``projects/models`` while every check above still sees a plain name.  The
    # containment check below does not catch it either — ``projects/...`` really
    # is directly under ``projects/``.  Anything that is only dots, or that ends
    # in one, is refused instead, which costs no real project anything: a folder
    # cannot be called that on the platform this runs on.
    if not text.strip(".") or text != text.rstrip(". "):
        return None
    root = projects_dir()
    path = os.path.abspath(os.path.join(root, text))
    if os.path.dirname(path) != root or not os.path.isdir(path):
        return None
    return path


def read_spec(folder):
    """``spec.json`` from a project folder, or ``None`` if it has none.

    A spec that will not parse is not an error either: the parameters still work
    without it, and a project the artist can no longer open because they left a
    trailing comma in a comment file is a worse outcome than a missing sheet.
    """
    try:
        with open(os.path.join(folder, "spec.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def project_scripts(folder):
    """Every ``.py`` in a project folder, the likeliest part first."""
    try:
        names = sorted(n for n in os.listdir(folder)
                       if n.lower().endswith(".py")
                       and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return []

    def rank(name):
        lowered = name.lower()
        if lowered in PART_SCRIPT_PREFERENCE:
            return (0, PART_SCRIPT_PREFERENCE.index(lowered), lowered)
        if lowered.startswith("part"):
            return (1, 0, lowered)
        return (2, 0, lowered)

    return sorted(names, key=rank)


def script_has_params(path):
    """Does this file carry a top-level ``PARAMS`` block?

    Read rather than imported: the bridge never executes the artist's script —
    that is the geometry service's job, in its own process, with its own venv.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return bool(_PARAMS_RE.search(handle.read(400000)))
    except OSError:
        return False


def read_script(path):
    """The source of one part script, or ``None``."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def project_entry(folder):
    """One project as the picker sees it, or ``None`` if it is not one."""
    name = os.path.basename(folder)
    spec = read_spec(folder)
    scripts = project_scripts(folder)
    wanted = str((spec or {}).get("script") or "").strip()
    primary = ""
    if wanted and wanted in scripts:
        primary = wanted
    elif scripts:
        primary = scripts[0]
    if not primary and spec is None:
        # A folder with no script and no spec is somebody's notes, not a part.
        return None
    script_path = os.path.join(folder, primary) if primary else ""
    return {
        "name": name,
        "path": folder,
        "script": primary,
        "script_path": script_path,
        "scripts": scripts,
        "spec": spec,
        "has_params": bool(script_path) and script_has_params(script_path),
        "object": object_name_for_script(script_path) if script_path else "",
    }


def scan_projects():
    """Every project folder, with its spec, for the workbench's picker."""
    root = projects_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {"dir": root, "projects": [], "count": 0,
                "note": "There is no projects folder at %s yet. Ask the "
                        "assistant for a part and one appears." % root}
    out = []
    for name in names:
        folder = os.path.join(root, name)
        if not _PROJECT_NAME_RE.match(name) or not os.path.isdir(folder):
            continue
        entry = project_entry(folder)
        if entry is not None:
            out.append(entry)
    return {"dir": root, "projects": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Phase 11 — the workbench: the parameter schema, cached briefly
# ---------------------------------------------------------------------------

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_CACHE = {}


def project_schema(script_path, refresh=False):
    """``(params, cached)`` for one script, via the service's ``/parse_params``.

    Cached against the file's mtime as well as the clock, so an edit made in
    another window shows up on the next Refresh rather than fifteen seconds
    later.  The cache exists only so that a page which draws itself twice does
    not parse twice.
    """
    key = os.path.abspath(script_path)
    try:
        mtime = os.path.getmtime(key)
    except OSError:
        mtime = 0.0
    now = time.time()
    if not refresh:
        with _SCHEMA_LOCK:
            cached = _SCHEMA_CACHE.get(key)
        if (cached and cached["mtime"] == mtime
                and now - cached["at"] < SCHEMA_CACHE_TTL):
            return cached["params"], True
    source = read_script(key)
    if source is None:
        raise ServiceRefused("Could not read %s." % key)
    data = service_post("/parse_params", {"script": source}, PARSE_TIMEOUT)
    params = data.get("params")
    params = params if isinstance(params, dict) else {}
    with _SCHEMA_LOCK:
        _SCHEMA_CACHE[key] = {"mtime": mtime, "at": now, "params": params}
    return params, False


def forget_schema(script_path=None):
    """Drop one cached schema, or all of them."""
    with _SCHEMA_LOCK:
        if script_path is None:
            _SCHEMA_CACHE.clear()
        else:
            _SCHEMA_CACHE.pop(os.path.abspath(script_path), None)


# ---------------------------------------------------------------------------
# Phase 11 — the workbench: previews
# ---------------------------------------------------------------------------

def prune_previews(directory, keep=PREVIEWS_KEPT):
    """Delete all but the newest ``keep`` previews. Best effort, never fatal."""
    try:
        names = [os.path.join(directory, n) for n in os.listdir(directory)]
    except OSError:
        return 0
    files = [p for p in names
             if os.path.isfile(p) and p.lower().endswith(".png")]
    if len(files) <= keep:
        return 0
    try:
        files.sort(key=os.path.getmtime)
    except OSError:
        return 0
    removed = 0
    for path in files[:len(files) - keep]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def new_preview_path():
    """A fresh PNG path for one render, in a folder this bridge owns.

    A new name every time, on purpose: the page shows the picture in an ``img``
    and a browser that reuses a cached URL shows the artist the *previous*
    shape, which is the single most misleading thing this feature could do.
    """
    directory = previews_dir()
    os.makedirs(directory, exist_ok=True)
    prune_previews(directory)
    return os.path.join(directory, "preview-%s.png" % uuid.uuid4().hex[:12])


# ---------------------------------------------------------------------------
# Phase 13 — the library: thumbnails
# ---------------------------------------------------------------------------

def thumbnail_path(name):
    """Where one project's cached PNG lives, or ``None`` for a bad name.

    Same alphabet gate as :func:`project_dir` and :func:`webui_asset`, because
    this name arrives in a URL from a browser and the answer must not depend on
    path arithmetic.  The file is *always* ``<project>.png``: one per project,
    overwritten, so the cache cannot grow and a stale picture cannot outlive the
    part it is of.
    """
    text = str(name or "").strip()
    if not text or text in (".", "..") or not _PROJECT_NAME_RE.match(text):
        return None
    root = thumbs_dir()
    path = os.path.abspath(os.path.join(root, "%s.png" % text))
    if os.path.dirname(path) != root:
        return None
    return path


def thumbnail_stat(name):
    """``(path, mtime)`` for a cached thumbnail, or ``(path, 0.0)``/``(None, 0)``."""
    path = thumbnail_path(name)
    if not path:
        return None, 0.0
    try:
        return path, round(os.path.getmtime(path), 3)
    except OSError:
        return path, 0.0


def save_thumbnail(name, source_path):
    """Copy a freshly rendered PNG into the cache. Best effort, never fatal.

    Returns the cached path, or ``None``.  A failure here costs the artist a
    placeholder card and nothing else, so it must never take a render down with
    it — the picture they asked for has already been made.
    """
    target = thumbnail_path(name)
    source = str(source_path or "")
    if not target or not source:
        return None
    try:
        directory = os.path.dirname(target)
        existed = os.path.isdir(directory)
        os.makedirs(directory, exist_ok=True)
        if not existed:
            # The default folder is inside the repo, like uploads/: a cache of
            # pictures is not source, and it says so itself rather than making
            # every clone edit .gitignore.
            try:
                with open(os.path.join(directory, ".gitignore"), "w") as handle:
                    handle.write("*\n")
            except OSError:
                pass
        shutil.copyfile(source, target)
    except OSError:
        return None
    return target


def read_thumbnail(name):
    """``(bytes, content type)`` for a cached thumbnail, or ``None``."""
    path = thumbnail_path(name)
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(), "image/png"
    except OSError:
        return None


def project_for_object(object_name):
    """The project whose part script builds into this object, or ``None``.

    The naming convention read backwards.  It is what lets the workbench's
    existing render double as a thumbnail: the artist presses Render, the PNG
    is already on disk, and the library gets its picture for free instead of
    asking Blender to draw the same shape twice.
    """
    text = str(object_name or "").strip()
    if not text:
        return None
    for entry in scan_projects().get("projects", []):
        if entry.get("object") == text:
            return entry
    return None


def remember_preview(objects, path):
    """Cache a preview PNG as a project's thumbnail, when it is a picture of one.

    Only when the render was of *exactly one* named object that is some
    project's part object.  A whole-scene render is not a thumbnail for any one
    project, and a picture captioned with the wrong part is worse than no
    picture: the library is meant to be how the artist finds their work.
    """
    names = [str(item).strip() for item in (objects or []) if str(item).strip()]
    if len(names) != 1:
        return None
    entry = project_for_object(names[0])
    if entry is None:
        return None
    return save_thumbnail(entry["name"], path)


# ---------------------------------------------------------------------------
# Phase 13 — the library: what one card is made of
# ---------------------------------------------------------------------------

def _component_entry(item, key=None, role=""):
    """One component, whichever of the three shapes it was written in."""
    if isinstance(item, str):
        text = item.strip()
        if not text and not key:
            return None
        # A map of name -> sentence: the KEY is the name and the string is what
        # it is.  A bare list entry is the name, with nothing said about it.
        return {"name": str(key or text), "role": str(role or ""),
                "description": text if key else ""}
    if not isinstance(item, dict):
        return None
    name = (item.get("name") or item.get("object") or key
            or item.get("script") or "component")
    return {
        "name": str(name),
        "role": str(item.get("kind") or item.get("role") or item.get("type")
                    or role or ""),
        "description": str(item.get("description") or item.get("note") or ""),
    }


def component_list(value, role=""):
    """A components block as a flat list, read liberally.

    ``spec.json`` is the artist's file and the component tree is still growing,
    so every shape it has been written in is read rather than one: a list of
    names, a list of objects, or a map keyed by name.  The web UI does exactly
    this in JavaScript for the workbench sheet; the library needs it server-side
    so a card can carry chips without shipping the spec to the browser twice.
    """
    out = []
    if isinstance(value, (list, tuple)):
        for item in value:
            entry = _component_entry(item, None, role)
            if entry is not None:
                out.append(entry)
    elif isinstance(value, dict):
        for key in value:
            if str(key).startswith("_"):
                continue
            entry = _component_entry(value[key], key, role)
            if entry is not None:
                out.append(entry)
    return out


#: The keys of the component tree the MCP server actually writes —
#: ``{"collection": <slug>, "core": <slug>, "proposals": [names]}``
#: (``forge_mcp.util.component_block``).  Named here because that block is a
#: *tree*, not a map of components: read as a map it yields a chip called
#: "collection", a chip called "core", and silently drops every proposal — the
#: one thing on the card that says what the part is made of.
_TREE_KEYS = frozenset(("collection", "core", "proposals"))


def _names_in(value):
    """The strings in a name, or in a list of names.  Nothing else."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value
                if isinstance(item, str) and item.strip()]
    return []


def component_tree(block):
    """The MCP's own component tree as a flat list, or ``None`` if it is not one.

    ``collection`` is skipped on purpose: it is the Blender collection the
    pieces live in, not a piece.  ``core`` is the dimensioned part and
    ``proposals`` are the ones the artist is invited to scrap, which is the
    whole distinction the chips exist to draw.
    """
    if not isinstance(block, dict):
        return None
    keys = {str(key) for key in block if not str(key).startswith("_")}
    # Only when the block is *that* tree and nothing else. A map of
    # ``{"core": "the ring", "collar": "scrap me"}`` is a map keyed by name and
    # is read as one.
    if not keys or not keys <= _TREE_KEYS or not keys & {"core", "proposals"}:
        return None
    out = [{"name": name, "role": "core", "description": ""}
           for name in _names_in(block.get("core"))]
    out += [{"name": name, "role": "proposal", "description": ""}
            for name in _names_in(block.get("proposals"))]
    return out


def spec_components(spec):
    """Every component named in a spec — ``components``, core, proposals, parts."""
    if not isinstance(spec, dict):
        return []
    block = spec.get("components")
    tree = component_tree(block)
    out = tree if tree is not None else component_list(block)
    out += component_list(spec.get("core"), "core")
    out += component_list(spec.get("proposals"), "proposal")
    assembly = spec.get("assembly")
    if isinstance(assembly, dict):
        out += component_list(assembly.get("parts"))
    return out[:MAX_COMPONENTS_LISTED]


def project_exports(folder, limit=MAX_EXPORTS_LISTED):
    """``projects/<name>/exports/*`` as ``{file, path, size, mtime}``, newest first.

    Paths are shown, never linked: these are files on the artist's own machine
    and the browser is on the same machine.  A download route would be a second
    way to read the filesystem for no gain over the path they can paste into
    Explorer.
    """
    directory = os.path.join(folder, EXPORTS_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"file": name, "path": path, "size": int(info.st_size),
                    "mtime": round(info.st_mtime, 3)})
    out.sort(key=lambda item: item["mtime"], reverse=True)
    return out[:limit]


def project_design(folder, limit=MAX_DESIGN_LISTED):
    """``projects/<name>/design/*`` as ``{file, path, size, mtime}``, in reading
    order.

    The design phase (Phase 16) writes here BEFORE any geometry exists, so this
    is routinely the only thing in a project folder — a requirements sheet, a
    concept diagram and a components list, with no ``part.py`` yet and maybe
    never.  Newest-first would be the wrong order for a sheet somebody reads:
    what it has to do comes before what it looks like.
    """
    directory = os.path.join(folder, DESIGN_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"file": name, "path": path, "size": int(info.st_size),
                    "mtime": round(info.st_mtime, 3)})

    def rank(item):
        lowered = item["file"].lower()
        try:
            return (DESIGN_READING_ORDER.index(lowered), lowered)
        except ValueError:
            return (len(DESIGN_READING_ORDER), lowered)

    out.sort(key=rank)
    return out[:limit]


def project_demos(folder, limit=MAX_DEMOS_LISTED):
    """``projects/<name>/renders/*.mp4`` as ``{file, path, size, mtime, url}``.

    Newest first, because a demo is a *take*: the one that was just rendered is
    the one the artist wants to watch, and the one before it is the version they
    are comparing it against.

    Tokens, unlike the design sheet next door — that list is paths on purpose
    (they are documents on this machine and the browser is on this machine), but
    a film is not a document.  A demo the page cannot press play on is a
    filename, which is exactly the state Phase 17 exists to leave behind.  The
    Models row already mints for the same reason, so this adds no new power: a
    token is minted here for a file the shelf already indexes and nowhere else.
    """
    directory = os.path.join(folder, DEMOS_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in sorted(names):
        if os.path.splitext(name)[1].lower() not in DEMO_EXTENSIONS:
            continue
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        found.append((path, name, info))
    found.sort(key=lambda item: item[2].st_mtime, reverse=True)

    out = []
    for path, name, info in found[:limit]:
        token = FILES.mint(path)
        out.append({"file": name, "path": path, "size": int(info.st_size),
                    "mtime": round(info.st_mtime, 3), "token": token,
                    "url": ("/file/%s" % token) if token else None})
    return out


def param_count_for(entry):
    """``(count, source)`` — how many dimensions this part has, if we can tell.

    Without running the artist's script and **without calling the geometry
    service**: the library is a folder read, and a page that lists twenty parts
    must not become twenty ``/parse_params`` round trips (or twenty error cards
    when the service is stopped).  So: the spec's own ``parameters`` block,
    else a schema the workbench already parsed this session, else ``None`` —
    which the card says as "parameters not read yet" rather than as a zero.
    """
    spec = entry.get("spec")
    if isinstance(spec, dict):
        params = spec.get("parameters")
        if isinstance(params, dict) and params:
            return len(params), "spec"
    script_path = entry.get("script_path")
    if script_path:
        with _SCHEMA_LOCK:
            cached = _SCHEMA_CACHE.get(os.path.abspath(script_path))
        if cached is not None:
            return len(cached.get("params") or {}), "service"
    return None, "unread"


def project_blend(folder):
    """``{"path", "exists", "size", "mtime"}`` for ``<folder>/<name>.blend``.

    A ``stat`` and nothing else.  The library must draw with Blender closed, so
    "does this project have a scene file" is answered off the disk — never by
    asking Blender, which is exactly the thing that may not be running.
    """
    name = os.path.basename(os.path.normpath(folder))
    path = os.path.join(folder, "%s%s" % (name, BLEND_SUFFIX))
    try:
        info = os.stat(path)
    except OSError:
        return {"path": path, "exists": False, "size": 0, "mtime": 0.0}
    if not os.path.isfile(path):
        return {"path": path, "exists": False, "size": 0, "mtime": 0.0}
    return {"path": path, "exists": True, "size": int(info.st_size),
            "mtime": round(info.st_mtime, 3)}


def project_mtime(folder, entry, exports, blend=None, design=(), demos=()):
    """When this project was last touched — the folder, its spec, its script,
    its scene file, its exports, its design sheet, its demos, whichever moved
    last."""
    stamps = []
    if blend and blend.get("mtime"):
        stamps.append(float(blend["mtime"]))
    candidates = [folder, os.path.join(folder, "spec.json"),
                  entry.get("script_path")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            stamps.append(os.path.getmtime(candidate))
        except OSError:
            pass
    stamps.extend(item["mtime"] for item in exports)
    stamps.extend(item["mtime"] for item in design)
    stamps.extend(item["mtime"] for item in demos)
    return round(max(stamps), 3) if stamps else 0.0


def design_only_entry(folder):
    """A project that is a design sheet and nothing else, as the shelf sees it.

    :func:`project_entry` answers ``None`` for a folder with no script and no
    spec — "somebody's notes, not a part" — and that is the right answer for the
    workbench's picker, which exists to open dimensions.  It is the wrong answer
    for the Library: after the design phase writes a requirements sheet and a
    concept diagram, a project has real work in it and no geometry yet, and a
    shelf that hides it until somebody presses build has hidden the one thing
    the artist is being asked to sign off on.
    """
    return {
        "name": os.path.basename(os.path.normpath(folder)),
        "path": folder,
        "script": "",
        "script_path": "",
        "scripts": [],
        "spec": None,
        "has_params": False,
        "object": "",
    }


def library_entry(folder):
    """One project as a library card, or ``None`` if the folder is not one."""
    design = project_design(folder)
    entry = project_entry(folder)
    if entry is None:
        if not design:
            return None
        entry = design_only_entry(folder)
    spec = entry.get("spec") if isinstance(entry.get("spec"), dict) else {}
    exports = project_exports(folder)
    demos = project_demos(folder)
    count, source = param_count_for(entry)
    features = spec.get("features")
    _path, thumb_mtime = thumbnail_stat(entry["name"])
    blend = project_blend(folder)
    return {
        "name": entry["name"],
        "path": entry["path"],
        "script": entry["script"],
        "script_path": entry["script_path"],
        "object": entry["object"],
        "has_params": entry["has_params"],
        "description": str(spec.get("description") or "").strip(),
        "param_count": count,
        "param_source": source,
        "components": spec_components(spec),
        "features": ([str(item) for item in features[:20]]
                     if isinstance(features, list) else []),
        "exports": exports,
        "export_count": len(exports),
        # Phase 16: the design phase's own files — the requirements sheet, the
        # concept diagram, the components list.  Paths, never links: the design
        # folder is on this machine and so is the browser, and a download route
        # would be a second way to read the filesystem for no gain.
        "design": design,
        "design_count": len(design),
        # A project that has a sheet and no script has not been built yet, which
        # is the state the sign-off gate exists to hold.  The card says so
        # rather than drawing a part that is not there.
        "design_only": bool(design) and not entry.get("script_path"),
        # Phase 17: the mechanism demos, newest take first.  Tokens, not paths —
        # see project_demos: this is the one thing on a card that is watched
        # rather than read.
        "demos": demos,
        "demo_count": len(demos),
        "mtime": project_mtime(folder, entry, exports, blend, design, demos),
        # Phase 15: does this project have a scene of its own to open?  A stat,
        # so the answer is the same whether Blender is running or not.
        "has_blend": blend["exists"],
        "blend_path": blend["path"],
        "blend_size": blend["size"],
        "blend_mtime": blend["mtime"],
        "has_thumbnail": bool(thumb_mtime),
        "thumbnail_mtime": thumb_mtime,
        "thumbnail_url": "/projects/%s/thumbnail" % entry["name"],
        "spec": entry["spec"],
    }


def scene_section():
    """What Blender is holding right now — best effort, never an error.

    Works in progress belong in the library beside the saved parts: a generated
    mesh, a sculpt, the pieces a segment produced.  None of them has a folder in
    ``projects/`` and all of them are the artist's work.  Blender being closed
    is one sentence in this block rather than a failed request, because the rest
    of the page is a folder read and must still draw.
    """
    try:
        result = blender_command("get_scene_info", {}, SCENE_TIMEOUT)
    except BlenderDown as exc:
        return {"ok": False, "blender": False, "error": str(exc), "objects": [],
                "count": 0}
    except BlenderRefused as exc:
        return {"ok": False, "blender": True, "error": str(exc), "objects": [],
                "count": 0}
    except Exception as exc:  # noqa: BLE001 - the library draws without Blender
        return {"ok": False, "blender": False, "error": str(exc)[:300],
                "objects": [], "count": 0}
    objects = result.get("objects")
    objects = objects if isinstance(objects, list) else []
    return {"ok": True, "blender": True, "objects": objects,
            "count": len(objects), "active": result.get("active"),
            "unit_scale": result.get("unit_scale")}


# ---------------------------------------------------------------------------
# Phase 13, revisited — the library's models: the files, not the folders
# ---------------------------------------------------------------------------

def meshgen_output_dir():
    """Where meshgen writes, read out of ``meshgen/config.json``.

    Not hardcoded, and not guessed: that file's own first line promises that
    relocating the model install is a one-file edit, and a second copy of
    ``C:/forge-models`` in this module would quietly break that promise on the
    day somebody moves it.  ``FORGE_MESHGEN_OUTPUT_DIR`` wins first — the same
    variable ``meshgen/config.py`` honours for the same key, so pointing the
    service at a scratch folder points this row at it too.
    """
    override = str(_env("FORGE_MESHGEN_OUTPUT_DIR", "") or "").strip()
    if override:
        return os.path.abspath(override)
    try:
        with open(MESHGEN_CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = None
    value = ""
    if isinstance(data, dict):
        value = str(data.get(MESHGEN_OUTPUT_KEY) or "").strip()
    return os.path.abspath(value or DEFAULT_MESHGEN_OUTPUT_DIR)


def extra_model_dirs():
    """``FORGE_MODELS_DIRS`` as a list — semicolon-separated, Windows-style."""
    raw = str(_env("FORGE_MODELS_DIRS", "") or "")
    out = []
    for piece in raw.split(";"):
        text = piece.strip().strip('"')
        if text:
            out.append(os.path.abspath(os.path.expandvars(os.path.expanduser(text))))
    return out


def _dedupe_dirs(paths):
    """Absolute paths, in order, without repeats — case-insensitively on Windows."""
    out = []
    seen = set()
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def generated_model_dirs():
    """Every folder a *generated* mesh can be sitting in, likeliest first.

    Two from meshgen — the ``forge/`` prefix the backends write into and the
    output root itself, because a workflow saved with a different prefix (or a
    file the artist dropped there) is still their mesh — plus whatever
    ``FORGE_MODELS_DIRS`` names.
    """
    root = meshgen_output_dir()
    return _dedupe_dirs([os.path.join(root, MESHGEN_OUTPUT_PREFIX), root]
                        + extra_model_dirs())


def project_model_dirs():
    """``projects/<name>/models`` for every project folder on disk."""
    root = projects_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names:
        folder = os.path.join(root, name)
        if not _PROJECT_NAME_RE.match(name) or not os.path.isdir(folder):
            continue
        out.append(os.path.join(folder, MODELS_DIRNAME))
    return out


def indexed_model_dirs():
    """Every folder ``GET /library`` reads models from.

    This list is also the allow-list ``POST /models/import`` and
    ``POST /models/file`` check a client's path against.  One function, so a
    folder the shelf can show is exactly a folder those two will act on, and a
    path the shelf never showed is one they refuse.
    """
    return _dedupe_dirs(generated_model_dirs() + project_model_dirs())


def model_project_for(directory):
    """``"cup"`` for ``projects/cup/models``, or ``""`` for anywhere else."""
    parent = os.path.dirname(os.path.normpath(directory))
    if os.path.basename(os.path.normpath(directory)).lower() != MODELS_DIRNAME:
        return ""
    if os.path.normcase(os.path.dirname(parent)) != os.path.normcase(projects_dir()):
        return ""
    return os.path.basename(parent)


def model_entry(path, info=None, project=None):
    """One model file as the row draws it, or ``None`` if it is not one.

    A ``stat`` and a token, and nothing else: no mesh is opened, no header is
    read and Blender is never asked.  The Models row has to draw with everything
    on the machine stopped, exactly like the cards above it.
    """
    directory = os.path.dirname(path)
    extension = os.path.splitext(path)[1].lower()
    if extension not in MODEL_EXTENSIONS:
        return None
    if info is None:
        try:
            info = os.stat(path)
        except OSError:
            return None
    owner = model_project_for(directory) if project is None else str(project or "")
    # A token so the browser can fetch the bytes at all — .glb and .gltf are
    # servable and .stl/.obj are not, which is the honest answer rather than a
    # dead link.  There is no 3D preview here: a viewer is a library this page
    # would have to fetch from a CDN, and this page works offline.
    token = FILES.mint(path)
    return {
        "file": os.path.basename(path),
        "path": path,
        "dir": directory,
        "dir_kind": "project" if owner else "generated",
        "project": owner,
        "ext": extension,
        "size": int(info.st_size),
        "mtime": round(info.st_mtime, 3),
        "token": token,
        "url": ("/file/%s" % token) if token else None,
    }


def scan_model_dir(directory, project=None):
    """Every model file directly in one folder.  Never recursive.

    A walk would follow the artist's own folders — a ComfyUI output tree has
    every render in it — and turn a card row into a filesystem crawl.  The
    folders that matter are named, and one of them is the ``forge/`` subfolder
    precisely because that is where the backends write.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(directory, name)
        if os.path.splitext(name)[1].lower() not in MODEL_EXTENSIONS:
            continue
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        entry = model_entry(path, info, project=project)
        if entry is not None:
            out.append(entry)
    return out


def scan_models(limit=MAX_MODELS_LISTED):
    """The library's models section: every mesh on disk that Forge can see.

    Newest first across *both* kinds, not grouped: an artist who generated
    something four minutes ago wants it at the front of the row, and which
    folder it happens to be sitting in is the badge on the card rather than the
    sort order.
    """
    generated = generated_model_dirs()
    models = []
    for directory in generated:
        models.extend(scan_model_dir(directory, project=""))
    for directory in project_model_dirs():
        models.extend(scan_model_dir(directory))
    # Same path through two indexed folders is one card.
    seen = set()
    unique = []
    for entry in models:
        key = os.path.normcase(os.path.normpath(entry["path"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    unique.sort(key=lambda item: item["mtime"], reverse=True)
    total = len(unique)
    listed = unique[:limit]
    section = {
        "models": listed,
        "count": len(listed),
        "total": total,
        "dirs": indexed_model_dirs(),
        "generated_dirs": generated,
        "projects_dir": projects_dir(),
    }
    if not listed:
        section["note"] = (
            "No model files yet. Generated meshes land in %s, and anything you "
            "put in projects/<name>/models/ shows up here too."
            % (generated[0] if generated else meshgen_output_dir()))
    return section


def project_slug(name):
    """``"A small Cup!"`` -> ``"a-small-cup"``, or ``""`` when it is not a name.

    :data:`_SLUG_TRAVERSAL_MARKERS` are refused rather than collapsed: a caller
    who wrote a separator meant a location, and slugging it away would write
    somewhere they did not ask for.  The mirror of the MCP server's
    ``project_slug``, which is the writer this route has to agree with.
    """
    raw = str(name or "").strip().strip('"').strip()
    if not raw:
        return ""
    for marker in _SLUG_TRAVERSAL_MARKERS:
        if marker in raw:
            return ""
    if raw[:1] in ("~", "%", "$"):
        return ""
    slug = _SLUG_SEPARATORS.sub("-", raw.lower()).strip("-")
    if len(slug) > PROJECT_SLUG_MAX:
        slug = slug[:PROJECT_SLUG_MAX].rstrip("-")
    # Belt and braces on top of the alphabet above: whatever came out of the
    # slugger still has to be one plain folder name, or nothing is written.
    if not slug or slug in (".", "..") or not _PROJECT_NAME_RE.match(slug):
        return ""
    return slug


def project_models_dir(slug, create=False):
    """``projects/<slug>/models``, or ``None`` if that is not inside ``projects/``.

    The containment check is arithmetic on top of the alphabet gate in
    :func:`project_slug`, the same two-independent-guards rule the MCP server's
    ``project_paths`` uses for the only other place in Forge that writes files
    the artist did not name.
    """
    if not slug:
        return None
    root = projects_dir()
    folder = os.path.abspath(os.path.join(root, slug, MODELS_DIRNAME))
    if os.path.dirname(os.path.dirname(folder)) != root:
        return None
    if create:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return None
    return folder


def resolve_indexed_model(path):
    """An absolute path this bridge indexes, or ``None``.

    Paths are never taken on trust: what a client may name is exactly what
    ``GET /library`` already showed it.  The check is on the file's *directory*,
    matched against :func:`indexed_model_dirs` — not a prefix test, so a folder
    that merely starts with the same characters is not inside anything.
    """
    resolved = normalize_image_path(path)
    if not resolved:
        return None
    if os.path.splitext(resolved)[1].lower() not in MODEL_EXTENSIONS:
        return None
    try:
        if not os.path.isfile(resolved):
            return None
    except OSError:
        return None
    directory = os.path.normcase(os.path.dirname(resolved))
    for candidate in indexed_model_dirs():
        if os.path.normcase(os.path.normpath(candidate)) == directory:
            return resolved
    return None


def model_object_name(path):
    """What the imported object is called: the file's own stem, Blender-capped.

    The mirror of ``forge_mcp.util.generated_object_name`` — a ``.glb`` names its
    mesh whatever the exporter felt like (``Mesh_0`` in practice), which tells
    the artist nothing and collides with the next import.
    """
    stem = os.path.splitext(os.path.basename(str(path or "")))[0].strip()
    if not stem:
        return ""
    return stem.encode("utf-8")[:MAX_OBJECT_NAME].decode("utf-8", "ignore")


def free_model_path(folder, filename):
    """``(path, renamed)`` — ``filename`` in ``folder``, never over something else.

    Filing is a copy, and a copy that silently replaced a mesh the artist put
    there last week would be the one unrecoverable thing this route could do.
    """
    stem, extension = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    counter = 2
    while os.path.exists(candidate) and counter < 1000:
        candidate = os.path.join(folder, "%s-%d%s" % (stem, counter, extension))
        counter += 1
    return candidate, os.path.basename(candidate) != filename


# ---------------------------------------------------------------------------
# Phase 18 — the version browser: reading the chain
# ---------------------------------------------------------------------------

def split_version(name):
    """``("werewolf-wip", 7)`` for ``werewolf-wip-7.blend``.

    A file with no trailing ``-N`` is version :data:`FIRST_VERSION`, because
    that one was saved before anybody thought to number anything.  The split is
    on the STEM, so an extension can never be mistaken for part of a number,
    and the base is greedy so ``a-1-2`` is version 2 of ``a-1`` rather than
    version 12 of ``a``.
    """
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    match = _VERSION_SUFFIX_RE.match(stem)
    if not match:
        return stem, FIRST_VERSION
    base = match.group("base")
    if not base:
        return stem, FIRST_VERSION
    return base, int(match.group("number"))


def version_renders(folder):
    """Every still in ``projects/<name>/renders/``, as candidate pictures.

    The films in the same folder are :func:`project_demos`' job; a version's
    picture is a still.  Nothing here is minted or opened — that happens only
    for the versions that survive the listing cap.
    """
    directory = os.path.join(folder, DEMOS_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        stem, extension = os.path.splitext(name)
        if extension.lower() not in VERSION_THUMB_EXTENSIONS:
            continue
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"file": name, "stem": stem, "extension": extension.lower(),
                    "path": path, "mtime": round(info.st_mtime, 3)})
    return out


#: What may follow a version's stem in the name of a picture OF that version:
#: a separator and then something that is not a digit.  The rule exists for one
#: case that a plain "contains" test gets wrong every time — ``werewolf-wip`` is
#: version 1, and ``werewolf-wip-7.png`` starts with exactly those characters,
#: so without the boundary version 7's picture hangs on version 1's row.
_VERSION_DECORATION_RE = re.compile(r"^[-_. ]\D")


def render_matches_version(render_stem, version_stem):
    """Is this still a picture of that exact version, and not of its neighbour?"""
    render_stem = str(render_stem or "")
    version_stem = str(version_stem or "")
    if not render_stem or not version_stem:
        return False
    if render_stem.lower() == version_stem.lower():
        return True
    # Same base and same number however the render spelled it, so a still
    # called ``werewolf-wip-07.png`` is version 7's rather than a version of
    # its own.
    render_base, render_number = split_version(render_stem)
    version_base, version_number = split_version(version_stem)
    if (render_base.lower() == version_base.lower()
            and render_number == version_number):
        return True
    if not render_stem.lower().startswith(version_stem.lower()):
        return False
    return bool(_VERSION_DECORATION_RE.match(render_stem[len(version_stem):]))


def version_thumbnail(renders, version_stem):
    """The best still for one version, or ``None``.

    ``.png`` first, because that is what every renderer in Forge writes, and
    then the newest — a version rendered twice shows the take that was made
    last, which is the rule the demos row already plays by.
    """
    matches = [item for item in renders
               if render_matches_version(item["stem"], version_stem)]
    if not matches:
        return None
    matches.sort(key=lambda item: (0 if item["extension"] == ".png" else 1,
                                   -item["mtime"], item["file"]))
    return matches[0]


def project_versions(folder, limit=MAX_VERSIONS_LISTED):
    """The numbered ``.blend`` saves in ``projects/<name>/models/``, chained.

    Grouped by the stem they share, sorted by version NUMBER rather than by
    name — ``wip-10`` comes after ``wip-9``, which string sorting gets exactly
    backwards and which is the whole reason this is a parser and not a
    ``sorted()`` call.  The newest link of each chain is flagged ``current``.
    """
    directory = os.path.join(folder, MODELS_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return {"dir": directory, "chains": [], "count": 0, "chain_count": 0,
                "note": "There is no models folder at %s yet. Saves made by "
                        "the assistant land there and appear here." % directory}
    renders = version_renders(folder)
    chains = {}
    order = []
    total = 0
    for name in sorted(names):
        if os.path.splitext(name)[1].lower() not in VERSION_EXTENSIONS:
            continue
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        base, number = split_version(name)
        key = base.lower()
        if key not in chains:
            chains[key] = {"stem": base, "versions": []}
            order.append(key)
        total += 1
        chains[key]["versions"].append({
            "file": name,
            "path": path,
            "version": number,
            "size": int(info.st_size),
            "mtime": round(info.st_mtime, 3),
            "modified": _iso_utc(info.st_mtime),
            "current": False,
            "thumbnail": None,
            "thumbnail_path": None,
            "thumbnail_url": None,
        })

    out = []
    for key in order:
        chain = chains[key]
        chain["versions"].sort(key=lambda item: (item["version"], item["file"]))
        kept = chain["versions"]
        trimmed = 0
        if len(kept) > limit:
            # The newest end is the end anybody is looking at.
            trimmed = len(kept) - limit
            kept = kept[-limit:]
        for entry in kept:
            picture = version_thumbnail(renders,
                                        os.path.splitext(entry["file"])[0])
            if picture is None:
                continue
            token = FILES.mint(picture["path"])
            entry["thumbnail"] = picture["file"]
            entry["thumbnail_path"] = picture["path"]
            entry["thumbnail_url"] = ("/file/%s" % token) if token else None
        if kept:
            kept[-1]["current"] = True
        newest = kept[-1] if kept else None
        out.append({
            "stem": chain["stem"],
            "versions": kept,
            "count": len(chain["versions"]),
            "trimmed": trimmed,
            "latest": newest["version"] if newest else 0,
            "latest_file": newest["file"] if newest else "",
            "mtime": max([item["mtime"] for item in kept] or [0.0]),
        })
    # The chain being worked on right now reads first.
    out.sort(key=lambda chain: (-chain["mtime"], chain["stem"].lower()))
    return {"dir": directory, "chains": out, "count": total,
            "chain_count": len(out)}


def next_version_name(directory, base, highest):
    """``("werewolf-wip-11.blend", 11)`` — the first free name past the chain.

    Never a name that already exists: a restore is a copy FORWARD, and a copy
    that landed on top of a save the artist made last week would be the one
    unrecoverable thing this route could do.  Same rule and the same loop as
    :func:`free_model_path`.
    """
    number = max(int(highest or FIRST_VERSION), FIRST_VERSION) + 1
    ceiling = number + MAX_VERSION_SEARCH
    while number < ceiling:
        name = "%s-%d%s" % (base, number, VERSION_EXTENSIONS[0])
        if not os.path.exists(os.path.join(directory, name)):
            return name, number
        number += 1
    return None, 0


def resolve_version_file(folder, filename):
    """The absolute path of one version file in this project, or ``None``.

    The same three-gate shape as :func:`webui_asset` and :func:`project_dir`,
    because this name arrives in a request body from a browser: one plain
    segment out of the asset alphabet (so ``..\\..\\system_prompt.md`` fails on
    the SHAPE of the name before any path arithmetic), an extension this route
    has a use for, and a resolved path whose parent is still this project's own
    models folder.
    """
    text = str(filename or "").strip()
    if not text or text.startswith(".") or not _ASSET_NAME_RE.match(text):
        return None
    if os.path.splitext(text)[1].lower() not in VERSION_EXTENSIONS:
        return None
    directory = os.path.abspath(os.path.join(folder, MODELS_DIRNAME))
    path = os.path.abspath(os.path.join(directory, text))
    if os.path.dirname(path) != directory or not os.path.isfile(path):
        return None
    return path


def highest_version(directory, base):
    """The largest version number of ``base`` on disk, or 0."""
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    highest = 0
    for name in names:
        if os.path.splitext(name)[1].lower() not in VERSION_EXTENSIONS:
            continue
        other_base, other_number = split_version(name)
        if other_base.lower() == str(base or "").lower():
            highest = max(highest, other_number)
    return highest


# ---------------------------------------------------------------------------
# Phase 18 — the pipeline board: build-plan.json, read and never written
# ---------------------------------------------------------------------------
#
# The artist: "we should see status updates ... check progress ... make
# decisions with UI rather than chatting."  The staged build already keeps all
# of that in ``projects/<name>/design/build-plan.json`` — the MCP server's
# ``forge_mcp.pipeline`` writes it, one stage at a time, with the gate's actual
# measurements on every entry.  Until now the only way to see it was to ask.
#
# This is a READER, deliberately and permanently.  ``pipeline.py`` owns that
# file: it validates every stage, it refuses a skip, and it will not let an
# override past without a name and a reason on it.  A second writer on a
# different port would be a way around all three, so the board's buttons send a
# SENTENCE to /ask and the assistant advances the plan through the tool that
# knows the rules.  The bridge never edits the plan.

def build_plan_path(folder):
    """``projects/<name>/design/build-plan.json`` for a resolved project folder."""
    return os.path.join(folder, DESIGN_DIRNAME, PLAN_FILENAME)


def read_build_plan(folder):
    """The plan as a dict, or ``None`` when there is not one (or it is broken).

    A plan that will not parse is not an error for the same reason a spec that
    will not parse is not: the artist still has a project, and a page that
    refuses to draw because of a trailing comma is a worse outcome than a panel
    that says it could not read the file.
    """
    try:
        with open(build_plan_path(folder), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plan_numbers(value):
    """A stage's ``numbers`` block as ``[{name, value, text}]``, in file order.

    Kept as the gate wrote them.  ``pipeline.coerce_numbers`` allows numbers,
    booleans and strings ("attention overall, 376 holes / 20064 region verts"),
    so the board prints the value it was given rather than a number it made up.
    """
    if not isinstance(value, dict):
        return []
    out = []
    for name in list(value.keys())[:MAX_PLAN_NUMBERS]:
        measured = value[name]
        if isinstance(measured, bool):
            text = "yes" if measured else "no"
        elif isinstance(measured, (int, float)):
            text = ("%g" % measured)
        else:
            text = str(measured)
        out.append({"name": str(name), "value": measured,
                    "text": _clip(text, MAX_PLAN_NUMBER_CHARS)})
    return out


def _plan_history(value):
    """The last few entries of a stage's history, newest last (as written)."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value[-MAX_PLAN_HISTORY:]:
        if not isinstance(item, dict):
            continue
        entry = {"date": str(item.get("date") or ""),
                 "action": str(item.get("action") or ""),
                 "from": str(item.get("from") or ""),
                 "to": str(item.get("to") or "")}
        override = item.get("override")
        if isinstance(override, dict):
            entry["override"] = {"who": str(override.get("who") or ""),
                                 "why": str(override.get("why") or "")}
        if str(item.get("action") or "") == "overridden":
            entry["override"] = {"who": str(item.get("who") or ""),
                                 "why": str(item.get("why") or "")}
        out.append(entry)
    return out


def pipeline_board(folder, name=""):
    """``build-plan.json`` as the stage board draws it.

    Every field here comes off the file.  Nothing is inferred, nothing is
    filled in and a plan that is missing is *said* to be missing, because a
    board that invented a green stage would be worse than no board: the whole
    point of the gates is that they are measured.
    """
    path = build_plan_path(folder)
    plan = read_build_plan(folder)
    if plan is None:
        return {
            "project": name or os.path.basename(folder),
            "path": path,
            "has_plan": False,
            "exists": os.path.isfile(path),
            "stages": [],
            "components": [],
            "counts": {},
            "note": ("%s could not be read as a build plan." % path)
                    if os.path.isfile(path) else
                    ("There is no build plan for this project yet. Ask the "
                     "assistant to start one and the stages appear here."),
        }

    stages = []
    counts = dict((status, 0) for status in PLAN_STATUSES)
    raw_stages = plan.get("stages")
    raw_stages = raw_stages if isinstance(raw_stages, list) else []
    for index, item in enumerate(raw_stages):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status not in PLAN_STATUSES:
            status = "pending"
        counts[status] = counts.get(status, 0) + 1
        artifacts = item.get("artifacts")
        artifacts = [str(one) for one in artifacts] if isinstance(artifacts, list) else []
        gate = item.get("gate")
        gate = [str(one) for one in gate] if isinstance(gate, list) else []
        tools = item.get("tools")
        tools = [str(one) for one in tools] if isinstance(tools, list) else []
        stages.append({
            "index": index,
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "status": status,
            "marker": PLAN_MARKERS.get(status, "[ ]"),
            "green": status in PLAN_GREEN,
            "red": status == "failed",
            "gate": gate,
            "does": str(item.get("does") or ""),
            "tools": tools,
            "artifacts": artifacts[:MAX_PLAN_ARTIFACTS],
            "artifact_count": len(artifacts),
            "numbers": _plan_numbers(item.get("numbers")),
            "history": _plan_history(item.get("history")),
        })

    # The same two answers ``pipeline.blocked`` and ``pipeline.next_stage``
    # give, computed the same way, so this page and the assistant never
    # disagree about where the build is.
    blocked = None
    for stage in stages:
        if stage["red"]:
            blocked = stage
            break
    following = None
    for stage in stages:
        if not stage["green"]:
            following = stage
            break

    components = []
    raw_components = plan.get("components")
    if isinstance(raw_components, list):
        for item in raw_components[:MAX_PLAN_COMPONENTS]:
            if not isinstance(item, dict):
                continue
            components.append({
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or item.get("id") or ""),
                "status": str(item.get("status") or ""),
                "description": _clip(str(item.get("description") or ""),
                                     MAX_PLAN_TEXT),
            })

    return {
        "project": name or str(plan.get("project") or os.path.basename(folder)),
        "path": path,
        "has_plan": True,
        "exists": True,
        "version": plan.get("version"),
        "task": str(plan.get("task") or ""),
        "notes": _clip(str(plan.get("notes") or ""), MAX_PLAN_TEXT),
        "stages": stages,
        "counts": counts,
        "total": len(stages),
        "done": counts.get("passed", 0) + counts.get("overridden", 0),
        "blocked": blocked["id"] if blocked else None,
        "blocked_stage": blocked,
        "next": following["id"] if following else None,
        "next_stage": following,
        "components": components,
        "component_count": len(raw_components) if isinstance(raw_components, list) else 0,
    }


# ---------------------------------------------------------------------------
# Phase 18 — the deliverables gallery
# ---------------------------------------------------------------------------

def project_deliverables(folder, limit=MAX_DELIVERABLES_LISTED):
    """Everything in ``projects/<name>/renders/`` worth looking at, newest first.

    The stills and the films together, because to the artist they are one
    thing: the output of this project that can be LOOKED at.  Tokens, for the
    same reason :func:`project_demos` mints them — the browser is on this
    machine but it still cannot open a path, and a gallery of filenames is the
    state this panel exists to leave behind.
    """
    directory = os.path.join(folder, DEMOS_DIRNAME)
    try:
        names = os.listdir(directory)
    except OSError:
        return {"dir": directory, "files": [], "count": 0, "total": 0,
                "note": "There is no renders folder for this project yet. "
                        "Renders, turntables and mechanism demos land there."}
    found = []
    for name in sorted(names):
        extension = os.path.splitext(name)[1].lower()
        if extension not in DELIVERABLE_EXTENSIONS:
            continue
        path = os.path.join(directory, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        found.append((path, name, extension, info))
    found.sort(key=lambda item: item[3].st_mtime, reverse=True)

    out = []
    for path, name, extension, info in found[:limit]:
        token = FILES.mint(path)
        out.append({
            "file": name,
            "path": path,
            "kind": "video" if extension in DISPLAY_VIDEO_EXTENSIONS else "image",
            "extension": extension,
            "size": int(info.st_size),
            "mtime": round(info.st_mtime, 3),
            "modified": _iso_utc(info.st_mtime),
            "token": token,
            "url": ("/file/%s" % token) if token else None,
        })
    return {"dir": directory, "files": out, "count": len(out),
            "total": len(found)}


# ---------------------------------------------------------------------------
# Phase 18 — the live model view: one .glb out of the running Blender
# ---------------------------------------------------------------------------
#
# "It's almost like we should have a simplified blender window open within
# forge."  This is that, in the only shape a browser can have it: the scene as
# it stands, exported to a ``.glb`` in the previews folder, served through the
# same ``/file/<token>`` gate as every other picture, and drawn by a viewer
# vendored into ``webui/`` (no CDN — this page works with the machine offline).
#
# The export runs through ``execute_python`` because the add-on has no generic
# glTF command: ``rigforge_export_godot`` wants an armature and bakes a whole
# LOD chain, which is not what "show me what is in the scene" means.  The code
# below is a CONSTANT in this file with exactly one value substituted into it —
# a path this bridge chose, JSON-encoded — so nothing a client sends ever
# reaches Blender as code.

#: What the snapshot runs in Blender.  Selection and the active object are put
#: back in a ``finally``: looking at the scene must never move the artist's own
#: selection out from under them.  The last line of stdout is the report.
SNAPSHOT_SCRIPT = '''
import bpy, json, os
target = %s
only = %s
def _report(payload):
    print("FORGE_SNAPSHOT " + json.dumps(payload))
view = bpy.context.view_layer
before = [o for o in bpy.data.objects if o.select_get()]
active = view.objects.active
kinds = {"MESH", "ARMATURE", "CURVE", "SURFACE", "META", "FONT"}
wanted = []
for obj in bpy.context.scene.objects:
    if obj.type not in kinds:
        continue
    if obj.hide_render or not obj.visible_get():
        continue
    if only and obj.name != only:
        continue
    wanted.append(obj)
if only and not wanted:
    _report({"ok": False, "error": "No visible object called %%r is in the scene." %% only})
elif not wanted:
    _report({"ok": False, "error": "The Blender scene has nothing visible to export."})
else:
    animations = sorted({a.name for a in bpy.data.actions})
    try:
        for obj in bpy.data.objects:
            obj.select_set(False)
        for obj in wanted:
            obj.select_set(True)
        view.objects.active = wanted[0]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        bpy.ops.export_scene.gltf(
            filepath=target, export_format="GLB", use_selection=True,
            export_apply=True, export_animations=True, export_yup=True,
            export_cameras=False, export_lights=False)
        _report({"ok": True, "path": target,
                 "objects": [o.name for o in wanted],
                 "meshes": [o.name for o in wanted if o.type == "MESH"],
                 "armatures": [o.name for o in wanted if o.type == "ARMATURE"],
                 "animations": animations})
    except Exception as exc:
        _report({"ok": False, "error": "%%s: %%s" %% (type(exc).__name__, exc)})
    finally:
        for obj in bpy.data.objects:
            obj.select_set(False)
        for obj in before:
            try:
                obj.select_set(True)
            except Exception:
                pass
        view.objects.active = active
'''


def snapshot_report(output):
    """The report line out of ``execute_python``'s captured stdout, or ``None``.

    Looked for by marker rather than by position: the artist's own scene can
    print anything it likes during an export, and the last thing printed is not
    reliably ours.
    """
    for line in reversed(str(output or "").splitlines()):
        line = line.strip()
        if not line.startswith(SNAPSHOT_MARKER):
            continue
        try:
            payload = json.loads(line[len(SNAPSHOT_MARKER):].strip())
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def new_snapshot_path(name):
    """Where one project's live snapshot is written.

    One file per project, overwritten, in the previews folder this bridge
    already owns and already prunes — a snapshot is a picture of *now*, and a
    folder of every "now" there has ever been is a leak, not a history.  The
    chain in ``models/`` is the history, and it has its own panel.
    """
    directory = previews_dir()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return None
    root = os.path.abspath(directory)
    path = os.path.abspath(os.path.join(root, "snapshot-%s.glb" % name))
    if os.path.dirname(path) != root:
        return None
    return path


def blender_snapshot(name, obj=""):
    """``(payload, status)`` — export the live scene to a ``.glb`` and mint it.

    Raises :class:`BlenderDown` / :class:`BlenderRefused` like every other
    passthrough on this bridge, so the one sentence the artist reads when
    Blender is closed is the same sentence everywhere.
    """
    target = new_snapshot_path(name)
    if target is None:
        return {"error": "The previews folder (%s) could not be made, so there "
                         "is nowhere to put the snapshot." % previews_dir()}, 500
    code = SNAPSHOT_SCRIPT % (json.dumps(target), json.dumps(str(obj or "")))
    result = blender_command("execute_python", {"code": code},
                             timeout=SNAPSHOT_TIMEOUT)
    report = snapshot_report(result.get("output"))
    if report is None:
        return {"error": "Blender ran the export but said nothing this bridge "
                         "could read back. Check Blender's system console.",
                "output": _tail(str(result.get("output") or ""), 600)}, 502
    if not report.get("ok"):
        return {"error": str(report.get("error")
                             or "The snapshot could not be exported."),
                "blender": True}, 409
    try:
        info = os.stat(target)
    except OSError:
        return {"error": "Blender reported the snapshot written, but %s is not "
                         "there." % target}, 502
    token = FILES.mint(target)
    return {
        "project": name,
        "path": target,
        "size": int(info.st_size),
        "mtime": round(info.st_mtime, 3),
        "modified": _iso_utc(info.st_mtime),
        "token": token,
        "url": ("/file/%s" % token) if token else None,
        "objects": [str(one) for one in (report.get("objects") or [])],
        "meshes": [str(one) for one in (report.get("meshes") or [])],
        "armatures": [str(one) for one in (report.get("armatures") or [])],
        "animations": [str(one) for one in (report.get("animations") or [])],
    }, 200


def scan_library(scene=True):
    """Every project as a card, plus what is in the scene right now."""
    root = projects_dir()
    scene_block = scene_section() if scene else None
    # The models row is read whatever happens to the projects folder: the
    # generated meshes live outside the repo entirely, and "there is no
    # projects/ yet" must not also hide them.
    models = scan_models()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {"dir": root, "projects": [], "count": 0, "scene": scene_block,
                "models": models,
                "note": "There is no projects folder at %s yet. Ask the "
                        "assistant for a part and one appears." % root}
    out = []
    for name in names:
        folder = os.path.join(root, name)
        if not _PROJECT_NAME_RE.match(name) or not os.path.isdir(folder):
            continue
        entry = library_entry(folder)
        if entry is not None:
            out.append(entry)
    return {"dir": root, "projects": out, "count": len(out), "scene": scene_block,
            "models": models}


# ---------------------------------------------------------------------------
# Phase 9 — the health strip, fanned out from here
# ---------------------------------------------------------------------------

def service_urls():
    return {
        "geometry": str(_env("FORGE_SERVICE_URL", DEFAULT_SERVICE_URL)).rstrip("/"),
        "meshgen": str(_env("FORGE_MESHGEN_URL", DEFAULT_MESHGEN_URL)).rstrip("/"),
    }


#: Every FORGE_* name this repo reads is listed in docs/env-registry.md; a test
#: greps the codebase and diffs it against that file, so a new one cannot ship
#: undocumented.
ENV_PREFIX = "FORGE_"


def env_names_set():
    """The ``FORGE_*`` variables present in this process's environment. NAMES ONLY.

    Values are deliberately not echoed: this list is diagnostic ("the bridge you
    are talking to was started with FORGE_ASSISTANT_TOOLS set, which is why the
    tool list looks wrong"), and a value here could be a path the artist would
    rather not publish on an HTTP endpoint the browser can read.  The handful of
    values that DO matter for diagnosing a diverged process are echoed in
    :func:`resolved_config` — resolved, not raw, which is the useful form.
    """
    return sorted(name for name in os.environ if name.startswith(ENV_PREFIX))


def resolved_config():
    """What this bridge is ACTUALLY using — resolved, never the raw environment.

    The ghost this closes: two processes with the same code and different
    ``FORGE_PROJECTS_DIR`` look identical on every other field, and the artist
    spends an afternoon wondering why the part they just generated is not in the
    library.  Raw env would not answer it either — the interesting value is the
    one after defaults, ``abspath`` and the meshgen config file have had their
    say, which is exactly what these functions return.
    """
    host, blender_port = blender_address()
    return {
        "projects_dir": projects_dir(),
        "models_dirs": generated_model_dirs(),
        "model": resolve_model() or "(cli default)",
        "timeout_s": timeout_s(),
        "stall_timeout_s": stall_timeout_s(),
        "blender": {"host": host, "port": blender_port},
        "cwd": working_dir(),
        "uploads_dir": uploads_dir(),
        "previews_dir": previews_dir(),
        "thumbs_dir": thumbs_dir(),
        "services": service_urls(),
        "env_set": env_names_set(),
    }


def probe_http(url, timeout=HEALTH_TIMEOUT):
    """``{"ok", "detail", "data"?}`` for one service's ``/health``."""
    request = urllib.request.Request(url + "/health",
                                     headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(200000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "detail": "answered HTTP %s" % exc.code}
    except urllib.error.URLError as exc:
        return {"ok": False, "detail": "not running (%s)" % (exc.reason,)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "detail": "not running (%s)" % (exc,)}
    try:
        data = json.loads(body)
    except ValueError:
        return {"ok": True, "detail": "running"}
    if not isinstance(data, dict):
        return {"ok": True, "detail": "running"}
    return {"ok": str(data.get("status") or "ok").lower() in ("ok", "ready", "idle"),
            "detail": str(data.get("status") or "ok"),
            "data": data}


def services_health(timeout=HEALTH_TIMEOUT):
    """Every dot on the health strip, probed in parallel.

    In parallel because a stopped service is a connection that has to time out,
    and three of those in a row is a page that looks broken for eight seconds.
    """
    urls = service_urls()
    host, blender_port = blender_address()
    results = {}

    def probe(key, fn):
        try:
            results[key] = fn()
        except Exception as exc:  # noqa: BLE001 - a dot is never worth a 500
            results[key] = {"ok": False, "detail": str(exc)[:200]}

    def probe_blender():
        up = blender_listening(timeout)
        return {"ok": up, "detail": "listening" if up else "not running"}

    threads = [
        threading.Thread(target=probe, args=(
            "geometry", lambda: probe_http(urls["geometry"], timeout)), daemon=True),
        threading.Thread(target=probe, args=(
            "meshgen", lambda: probe_http(urls["meshgen"], timeout)), daemon=True),
        threading.Thread(target=probe, args=("blender", probe_blender), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 1.5)

    cli = claude_info()
    services = [
        {"key": "bridge", "label": "Assistant", "ok": True,
         "detail": "signed in" if not JOBS.last_auth_error else "not signed in",
         "address": "127.0.0.1:%d" % port(),
         "warn": bool(JOBS.last_auth_error) or not cli.get("found"),
         "claude_cli": cli},
        {"key": "geometry", "label": "Shape service", "url": urls["geometry"],
         "optional": False},
        {"key": "meshgen", "label": "Image to 3D", "url": urls["meshgen"],
         # The only row allowed to be down on a working machine: the models
         # behind it are an optional 18.5 GB download.
         "optional": True},
        {"key": "blender", "label": "Blender", "address": "%s:%d" % (host, blender_port),
         "optional": True},
    ]
    for entry in services[1:]:
        found = results.get(entry["key"]) or {"ok": False, "detail": "not probed"}
        entry.update(found)
    return {
        "services": services,
        "busy": JOBS.is_busy(),
        "queued": JOBS.is_queued(),
        "session_cost_usd": round(float(JOBS.session_cost_usd or 0.0), 6),
        "session": bool(JOBS.session_id),
        "last_auth_error": bool(JOBS.last_auth_error),
        "claude_cli": cli,
    }


# ---------------------------------------------------------------------------
# Phase 9 — starting the services from the page
# ---------------------------------------------------------------------------

_START_LOCK = threading.Lock()


def start_services_command(path):
    """The argv that runs the start script.

    A ``.py`` path runs under this interpreter — the same trick
    :func:`launcher` plays for the fake CLI, and what lets the tests exercise
    this route without PowerShell.
    """
    if str(path).lower().endswith(".py"):
        return [sys.executable, path]
    powershell = (shutil.which("powershell.exe") or shutil.which("powershell")
                  or shutil.which("pwsh"))
    if not powershell:
        return None
    return [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "Bypass", "-File", path]


def start_services():
    """Run the start script and report what it said.

    The script probes each port first and starts only what is not already
    running, so pressing the button twice is harmless — that behaviour lives
    there, not here, and this deliberately does not second-guess it.
    """
    path = start_script()
    if not os.path.isfile(path):
        return 404, {"error": "The start script is missing (%s)." % path}
    argv = start_services_command(path)
    if argv is None:
        return 501, {"error": "PowerShell was not found, so the services cannot "
                              "be started from here. Run start_forge.cmd in the "
                              "repo root instead."}
    if not _START_LOCK.acquire(blocking=False):
        return 409, {"error": "The services are already being started. Give it "
                              "a few seconds."}
    try:
        try:
            proc = subprocess.run(
                argv, cwd=REPO_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=START_SERVICES_TIMEOUT,
                creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return 504, {"error": "The start script did not finish within %d "
                                  "seconds." % START_SERVICES_TIMEOUT}
        except OSError as exc:
            return 500, {"error": "Could not run %s: %s" % (path, exc)}
    finally:
        _START_LOCK.release()

    text = (proc.stdout or b"").decode("utf-8", "replace")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return 200, {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "script": path,
        "output": lines[-60:],
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ForgeAssistant/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt, *args):  # quieter than the default access log
        if os.environ.get("FORGE_ASSISTANT_VERBOSE"):
            log("[assistant] %s" % (fmt % args))

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _send_bytes(self, status, body, content_type, headers=None):
        """A binary/text response — the page, its assets, a token's file."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page is a local file the artist may be editing; a cached copy of
        # yesterday's app.js is a bug report that cannot be reproduced.
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _read_body(self, limit=None):
        """Raw request body, or ``None`` if it is longer than ``limit``.

        The length is checked before a byte is read: an oversized upload is
        refused by its header rather than by first accepting all of it.
        """
        limit = max_upload_bytes() if limit is None else limit
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        if length > limit:
            # Drain enough to keep the connection sane, then give up on it:
            # HTTP/1.1 keep-alive with an unread body is a poisoned socket.
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def _read_json(self):
        """The request body as an object, ``{}`` if there was none, ``None`` if
        it was not JSON.

        **Every** POST handler must call this, even one that ignores the body.
        HTTP/1.1 here is keep-alive, so a body left unread stays in the socket
        and is parsed as the start of the *next* request on that connection —
        which surfaces as a 501 "Unsupported method ('{}GET')" on some later,
        innocent route.  Draining is the whole fix.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    # -- routes ----------------------------------------------------------
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._send(200, {
                "status": "ok",
                "claude_cli": claude_info(),
                "cwd": working_dir(),
                "session": bool(JOBS.session_id),
                "busy": self._busy(),
                "queued": JOBS.is_queued(),
                "session_cost_usd": round(float(JOBS.session_cost_usd or 0.0), 6),
                # Read off the last failure, never by spending a turn to find
                # out: the panel only wants to know whether to say "sign in".
                "last_auth_error": bool(JOBS.last_auth_error),
                # Which code is running, and with which settings.  The add-on
                # compares "build".sha against the repo's HEAD and says STALE
                # when they differ; "config" is what makes two processes with
                # the same SHA and different environments tell themselves apart.
                "build": build_info(),
                "config": resolved_config(),
            })
            return
        if path.startswith("/job/"):
            view = JOBS.snapshot(path[len("/job/"):])
            if view is None:
                self._send(404, {"error": "No such job. It may have scrolled out of "
                                          "the last %d." % MAX_JOBS})
                return
            self._send(200, view)
            return

        # -- the web UI (Phase 9) ----------------------------------------
        if path == "/":
            self._index()
            return
        if path == "/jobs":
            self._send(200, {
                "jobs": JOBS.snapshot_all(),
                "session_cost_usd": round(float(JOBS.session_cost_usd or 0.0), 6),
                "busy": JOBS.is_busy(),
                "queued": JOBS.is_queued(),
                "limit": MAX_JOBS,
            })
            return
        if path.startswith("/webui/"):
            asset = webui_asset(path[len("/webui/"):])
            if asset is None:
                self._send(404, {"error": "No such asset."})
                return
            self._send_bytes(200, asset[0], asset[1])
            return
        if path.startswith("/file/"):
            found = read_token_file(path[len("/file/"):])
            if found is None:
                # Deliberately the same answer for "never minted", "expired"
                # and "gone from disk": a client that can tell those apart can
                # use this endpoint to ask questions about the filesystem.
                self._send(404, {"error": "No such file."})
                return
            body, content_type, filename = found
            headers = {"Content-Disposition": 'inline; filename="%s"'
                                              % filename.replace('"', "")}
            if content_type == "image/svg+xml":
                headers["Content-Security-Policy"] = SVG_CSP
            self._send_bytes(200, body, content_type, headers)
            return
        if path == "/services/health":
            self._send(200, services_health())
            return

        # -- the workbench (Phase 11) ------------------------------------
        if path == "/projects":
            self._send(200, scan_projects())
            return
        if path.startswith("/projects/") and path.endswith("/schema"):
            self._schema(path[len("/projects/"):-len("/schema")],
                         refresh="refresh=1" in self.path)
            return
        if path == "/scene":
            self._scene()
            return

        # -- the library (Phase 13) --------------------------------------
        if path == "/library":
            # ``?scene=0`` skips the Blender probe, which makes this a pure
            # folder read that answers instantly with everything on the machine
            # stopped.  The Workspace's picker asks that way: it wants the list
            # of project FOLDERS — which is this route and not ``/projects``,
            # because a character or a floorplan has no part script and would
            # otherwise be missing from the one screen built to follow it.
            # The same query-string shape as ``/projects/<name>/schema?refresh=1``.
            self._send(200, scan_library(scene="scene=0" not in self.path))
            return
        if path.startswith("/projects/") and path.endswith("/thumbnail"):
            self._get_thumbnail(path[len("/projects/"):-len("/thumbnail")])
            return

        # -- the workspace (Phase 18) ------------------------------------
        #
        # Both spellings of the prefix, because the workspace's routes were
        # specified as ``/project/<name>/...`` and every route beside them is
        # ``/projects/<name>/...``.  One line each rather than a redirect: a
        # 404 on a route that exists under the other spelling is a bug report
        # nobody can act on.
        for prefix in PROJECT_PREFIXES:
            if not path.startswith(prefix):
                continue
            if path.endswith("/versions"):
                self._versions(path[len(prefix):-len("/versions")])
                return
            if path.endswith("/pipeline"):
                self._pipeline(path[len(prefix):-len("/pipeline")])
                return
            if path.endswith("/deliverables"):
                self._deliverables(path[len(prefix):-len("/deliverables")])
                return
        self._send(404, {"error": "Unknown path %s" % path})

    def _index(self):
        asset = webui_asset("index.html")
        if asset is None:
            self._send_bytes(
                404,
                b"<h1>Forge</h1><p>The web UI is not installed: "
                b"assistant/webui/index.html is missing.</p>",
                "text/html; charset=utf-8")
            return
        self._send_bytes(200, asset[0], asset[1])

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/ask":
            self._ask()
            return
        if path.startswith("/cancel/"):
            self._read_json()   # drain, even though there is nothing to read
            job = JOBS.cancel(path[len("/cancel/"):])
            if job is None:
                self._send(404, {"error": "No such job."})
                return
            self._send(200, JOBS.snapshot(job["job_id"]))
            return
        if path == "/new":
            self._read_json()
            JOBS.reset_session()
            self._send(200, {"status": "ok", "session": None})
            return

        # -- the web UI (Phase 9) ----------------------------------------
        if path == "/upload":
            self._upload()
            return
        if path == "/services/start":
            self._read_json()
            status, payload = start_services()
            self._send(status, payload)
            return
        if path == "/flows":
            self._flows()
            return
        if path == "/flows/run":
            self._flows_run()
            return

        # -- the workbench (Phase 11) ------------------------------------
        if path.startswith("/projects/") and path.endswith("/set_params"):
            self._set_params(path[len("/projects/"):-len("/set_params")])
            return
        if path == "/preview":
            self._preview()
            return
        if path == "/scene":
            self._scene()
            return
        if path == "/scene/delete":
            self._scene_delete()
            return

        # -- the library (Phase 13) --------------------------------------
        if path.startswith("/projects/") and path.endswith("/thumbnail"):
            self._make_thumbnail(path[len("/projects/"):-len("/thumbnail")])
            return

        # -- the library's models (Phase 13, revisited) ------------------
        if path == "/models/import":
            self._model_import()
            return
        if path == "/models/file":
            self._model_file()
            return
        if path == "/models/open":
            self._model_open()
            return

        # -- project scene files (Phase 15) ------------------------------
        if path.startswith("/projects/") and path.endswith("/open"):
            self._open_project(path[len("/projects/"):-len("/open")])
            return
        if path.startswith("/projects/") and path.endswith("/save"):
            self._save_project(path[len("/projects/"):-len("/save")])
            return

        # -- the workspace (Phase 18) ------------------------------------
        for prefix in PROJECT_PREFIXES:
            if not path.startswith(prefix):
                continue
            if path.endswith("/versions/restore"):
                self._restore_version(
                    path[len(prefix):-len("/versions/restore")])
                return
            if path.endswith("/snapshot"):
                self._snapshot(path[len(prefix):-len("/snapshot")])
                return
        self._send(404, {"error": "Unknown path %s" % path})

    # -- handlers --------------------------------------------------------
    def _busy(self):
        return JOBS.is_busy()

    def _ask(self):
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return

        message = str(payload.get("message") or "").strip()
        if not message:
            self._send(400, {"error": "Type something first."})
            return

        # The artist's speed-versus-depth choice, refused before a turn is spent
        # if it is not one this bridge offers.
        problem = model_error(payload.get("model"))
        if problem:
            self._send(400, {"error": problem})
            return
        model = normalize_model(payload.get("model"))

        context = payload.get("context")
        if isinstance(context, dict):
            problem = image_error(context.get("image_path"))
            if problem:
                # Refuse before spending a turn: the model cannot Read a file
                # that is not there, and "I couldn't see your image" three
                # minutes later is the worst possible way to learn that.
                self._send(400, {"error": problem})
                return

        info = claude_info()
        if not info.get("found"):
            self._send(503, {"error": INSTALL_HINT})
            return

        new_conversation = (
            str(payload.get("conversation") or "continue").lower() == "new")

        # The glance.  One socket call, a second at most, and wrapped twice over
        # because an assistant that cannot answer at all is worse than one that
        # cannot see: `live_context_for_turn` swallows everything already, and
        # this catches even a failure to call it.
        live = ""
        try:
            if new_conversation:
                # A fresh conversation has never looked, so it is owed the whole
                # feed rather than "nothing since a glance it does not remember".
                JOBS.forget_blender_seen()
            live, seen_at = live_context_for_turn(JOBS.blender_since())
            JOBS.note_blender_seen(seen_at)
        except Exception:  # noqa: BLE001 - never fail a turn over awareness
            live = ""

        # Built now, not at pickup: this is the scene the artist was looking at
        # when they pressed Send, and it is what their message is about.
        prompt = build_prompt(message, payload.get("context"), live=live)

        job, disposition = JOBS.submit(message, prompt,
                                       new_conversation=new_conversation,
                                       model=model)

        if disposition == "rejected":
            self._send(409, {
                "error": "One message is already waiting its turn. Wait for the "
                         "assistant to get to it, or press Stop.",
                "job_id": job["job_id"],
                "state": "queued",
            })
            return

        # The attachment becomes a token on the job, so the surface that sent
        # it can draw it back (the web UI has only ever seen bytes, not a path)
        # and so the conversation still shows it after a reload.  Only once the
        # message has a job of its own: a rejected one is somebody else's.
        if isinstance(context, dict) and context.get("image_path"):
            JOBS.note_file(job["job_id"], context.get("image_path"), "attachment")

        if disposition == "queued":
            # A real job id straight away, so the panel can poll it like any
            # other and show the artist their message is not lost.
            self._send(200, {"job_id": job["job_id"], "state": "queued",
                             "queued": True})
            return

        start_turn(job)
        self._send(200, {"job_id": job["job_id"], "state": "running",
                         "queued": False})

    # -- the web UI's own handlers (Phase 9) -----------------------------
    def _upload(self):
        """Browser bytes in, a path on this machine out.

        This is the whole reason the route exists: a file input hands
        JavaScript the file's *content*, never its path, so there is nothing to
        put in ``context.image_path`` until the bytes have been written down
        somewhere.  Once they have, the attachment rides exactly the same road
        as the panel's — a path in the context, a block in the prompt, a Read
        by the model.
        """
        content_type = str(self.headers.get("Content-Type") or "")
        body = self._read_body(max_upload_bytes() + 65536)
        if body is None:
            self._send(413, {"error": "That image is larger than the %d MB limit."
                                      % (max_upload_bytes() // 1048576)})
            return

        if "multipart/form-data" in content_type.lower():
            name, data = parse_multipart(body, content_type)
            if data is None:
                self._send(400, {"error": "No file was found in that upload."})
                return
        else:
            try:
                payload = json.loads(body.decode("utf-8")) if body else None
            except (ValueError, UnicodeDecodeError):
                payload = None
            if not isinstance(payload, dict):
                self._send(400, {"error": "Send {\"name\": ..., \"data\": "
                                          "\"<base64>\"} or a multipart form."})
                return
            name = payload.get("name")
            encoded = payload.get("data")
            if isinstance(encoded, str) and not encoded.strip():
                # A zero-byte file encodes to an empty string.  That is not a
                # decoding failure, and saying so would send the artist looking
                # for a bug in the page instead of at the file they picked.
                data = b""
            else:
                data = decode_base64(encoded)
                if data is None:
                    self._send(400, {"error": "That image did not decode. Expected "
                                              "base64 (a data: URL is fine)."})
                    return

        problem = upload_error(name, data)
        if problem:
            self._send(413 if len(data) > max_upload_bytes() else 400,
                       {"error": problem})
            return

        try:
            path = save_upload(name, data)
        except OSError as exc:
            self._send(500, {"error": "Could not save the image to %s: %s"
                                      % (uploads_dir(), exc)})
            return

        token = FILES.mint(path)
        self._send(200, {
            # The path is what rides in context.image_path on the next /ask —
            # the browser could not have known it, and cannot choose it.
            "path": path,
            "name": os.path.basename(path),
            "bytes": len(data),
            "token": token,
            "url": ("/file/%s" % token) if token else None,
        })

    def _flows(self):
        """``flow_list`` from Blender, verbatim."""
        self._read_json()   # drain: see _read_json's note about keep-alive
        try:
            result = blender_command("flow_list", {}, FLOW_LIST_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True})
            return
        self._send(200, result)

    def _flows_run(self):
        """``flow_run`` on Blender — no model in the loop, by design."""
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        name = str(payload.get("name") or "").strip()
        if not name:
            self._send(400, {"error": "Which flow? Pass {\"name\": ...}."})
            return
        params = payload.get("params")
        if params is not None and not isinstance(params, dict):
            self._send(400, {"error": "'params' must be an object of "
                                      "{name: value}."})
            return
        try:
            result = blender_command("flow_run",
                                     {"name": name, "params": params or {}},
                                     flow_run_timeout())
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            # The add-on's own message: it names the step that failed and what
            # had already run, which is the useful half of a flow failure.
            self._send(502, {"error": str(exc), "blender": True})
            return
        self._send(200, result)

    # -- the workbench's own handlers (Phase 11) -------------------------
    def _project(self, name):
        """``(entry, folder)`` for a project named in a URL, or ``(None, None)``.

        Answers the client itself on a miss, with the same words for "that is
        not a name" and "there is no such project": a route that can tell those
        apart is a route that can be asked what folders exist.
        """
        folder = project_dir(name)
        entry = project_entry(folder) if folder else None
        if entry is None:
            self._send(404, {"error": "No project called %r in %s."
                                      % (str(name), projects_dir())})
            return None, None
        return entry, folder

    def _schema(self, name, refresh=False):
        """The part's ``PARAMS``, resolved by the service — the component sheet."""
        entry, _folder = self._project(name)
        if entry is None:
            return
        if not entry["script_path"]:
            self._send(422, {"error": "%s has no part script to read parameters "
                                      "from." % entry["name"],
                             "project": entry["name"]})
            return
        try:
            params, cached = project_schema(entry["script_path"], refresh=refresh)
        except ServiceDown as exc:
            # 503 and `service: false` together are what lets the page grey the
            # sliders out and print one sentence instead of a stack trace.
            self._send(503, {"error": str(exc), "service": False,
                             "project": entry["name"]})
            return
        except ServiceRefused as exc:
            self._send(502, {"error": str(exc), "service": True,
                             "project": entry["name"]})
            return
        self._send(200, {
            "project": entry["name"],
            "script": entry["script"],
            "script_path": entry["script_path"],
            "object": entry["object"],
            "params": params,
            "count": len(params),
            "cached": cached,
            "spec": entry["spec"],
        })

    def _set_params(self, name):
        """Rebuild the part with these values and put it back in the scene.

        The order is not arbitrary.  ``/generate`` runs first so that a stopped
        service is reported as a stopped service rather than as Blender
        refusing a command it never got; ``partforge_open`` runs next so the
        artist's panel is looking at the same script the page is; ``load_mesh``
        with ``replace`` runs last, which is what keeps the object's transform,
        its place in the outliner and the artist's selection.
        """
        entry, _folder = self._project(name)
        if entry is None:
            return
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        overrides = payload.get("overrides")
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            self._send(400, {"error": "'overrides' must be an object of "
                                      "{parameter: value}."})
            return
        if not entry["script_path"]:
            self._send(422, {"error": "%s has no part script to build."
                                      % entry["name"]})
            return
        source = read_script(entry["script_path"])
        if source is None:
            self._send(422, {"error": "Could not read %s." % entry["script_path"]})
            return

        try:
            built = service_post("/generate",
                                 {"script": source, "overrides": overrides},
                                 generate_timeout())
        except ServiceDown as exc:
            self._send(503, {"error": str(exc), "service": False})
            return
        except ServiceRefused as exc:
            # The service's own words: "wall_mm must be between 1 and 6", which
            # is the answer, not a symptom of one.
            self._send(502, {"error": str(exc), "service": True})
            return

        stats = built.get("stats") if isinstance(built.get("stats"), dict) else {}
        params = built.get("params") if isinstance(built.get("params"), dict) else {}
        mesh = built.get("mesh") if isinstance(built.get("mesh"), dict) else {}
        vertices = mesh.get("vertices") or []
        faces = mesh.get("faces") or []
        object_name = str(payload.get("object") or "").strip() or entry["object"]
        answer = {
            "project": entry["name"],
            "script": entry["script"],
            "object": object_name,
            "overrides": overrides,
            "params": params,
            "stats": stats,
            "loaded": False,
            "notes": [],
        }

        if not vertices:
            answer["notes"].append(
                "The shape service returned an empty mesh, so there was nothing "
                "to load — check what build() returns.")
            self._send(200, answer)
            return

        try:
            opened = blender_command(
                "partforge_open",
                {"script_path": entry["script_path"], "object": object_name,
                 "keep_values": True},
                FLOW_LIST_TIMEOUT)
        except BlenderDown as exc:
            # Built but not shown: the numbers are still worth having, and
            # saying "Blender is not running" while hiding them is a lie of
            # omission.
            answer["error"] = str(exc)
            answer["blender"] = False
            self._send(503, answer)
            return
        except BlenderRefused as exc:
            # Not fatal: the panel not following along is a smaller problem
            # than the mesh not arriving, so the load is still attempted.
            answer["notes"].append("The Forge panel did not follow along (%s)."
                                   % exc)
        else:
            answer["panel"] = opened

        try:
            loaded = blender_command(
                "load_mesh",
                {"name": object_name, "vertices": vertices, "faces": faces,
                 "replace": True},
                generate_timeout())
        except BlenderDown as exc:
            answer["error"] = str(exc)
            answer["blender"] = False
            self._send(503, answer)
            return
        except BlenderRefused as exc:
            answer["error"] = str(exc)
            answer["blender"] = True
            self._send(502, answer)
            return

        answer["loaded"] = True
        answer["blender"] = True
        answer["mesh"] = loaded
        answer["object"] = str(loaded.get("object") or object_name)
        self._send(200, answer)

    def _preview(self):
        """Render the scene (or some objects) and hand back a token for the PNG.

        This is how the page *shows* components.  The path is chosen here and
        never by the client — a route that renders to a path a browser names is
        a route that writes files wherever it is told.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        objects = payload.get("objects")
        if objects is None:
            objects = []
        if isinstance(objects, str):
            objects = [objects]
        if not isinstance(objects, list) or not all(
                isinstance(item, str) for item in objects):
            self._send(400, {"error": "'objects' must be a list of object names."})
            return

        params = {"path": new_preview_path()}
        if objects:
            params["objects"] = objects
        for key in ("view", "shading"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                params[key] = value.strip()
        resolution = payload.get("resolution")
        if isinstance(resolution, (int, float)) and not isinstance(resolution, bool):
            params["resolution"] = int(resolution)

        try:
            result = blender_command("render_preview", params, PREVIEW_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True})
            return

        path = str(result.get("path") or params["path"])
        # The picture the artist just asked for is also the library's thumbnail,
        # when it is a picture of exactly one part.  Free: the PNG is already on
        # disk, and asking Blender to draw the same shape a second time for a
        # 240-pixel square would be work nobody asked for.
        rendered = result.get("objects") or objects
        cached = remember_preview(rendered, path)
        token = FILES.mint(path)
        if not token:
            # The add-on said it rendered and there is nothing there.  Said
            # plainly, because the alternative is a broken image icon.
            self._send(502, {
                "error": "Blender reported a render but there is no readable "
                         "PNG at %s." % path,
                "blender": True})
            return
        self._send(200, {
            "token": token,
            "url": "/file/%s" % token,
            "path": path,
            "objects": rendered,
            "view": result.get("view") or params.get("view") or "iso",
            "framed_all_visible": bool(result.get("framed_all_visible")),
            "bounds_mm": result.get("bounds_mm"),
            "resolution": result.get("resolution"),
            # Which project's library card just got a new picture, if any.
            "thumbnail_for": os.path.splitext(os.path.basename(cached))[0]
                             if cached else None,
        })

    def _scene(self):
        """``get_scene_info``, verbatim — the component sheet with no spec."""
        self._read_json()   # drain: see _read_json's note about keep-alive
        try:
            result = blender_command("get_scene_info", {}, SCENE_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True})
            return
        self._send(200, result)

    def _scene_delete(self):
        """Scrap one object.  Undoable in Blender, and the answer says so."""
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        name = str(payload.get("object") or payload.get("name") or "").strip()
        if not name:
            self._send(400, {"error": "Which object? Pass {\"object\": ...}."})
            return
        try:
            result = blender_command("delete_object", {"name": name}, SCENE_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True})
            return
        self._send(200, {
            "deleted": name,
            "result": result,
            # Every state-changing socket command pushes its own undo step, so
            # this is true rather than reassuring.
            "undo": "Ctrl+Z in Blender puts %s back." % name,
        })

    # -- the library's own handlers (Phase 13) ---------------------------
    def _get_thumbnail(self, name):
        """The cached PNG for one project, or a 404 the page draws around.

        Served straight off the cache rather than through ``/file/<token>``:
        the path is one this bridge chose, in a folder this bridge owns, named
        by a project name that has already passed the alphabet gate — there is
        nothing for a token to add.  A 404 here is not an error; it is a
        placeholder card with the project's initial in it.
        """
        found = read_thumbnail(name)
        if found is None:
            self._send(404, {
                "error": "No picture of %s yet. Press Preview on its card (or "
                         "Render in the Workbench) and one is kept."
                         % (str(name)[:60] or "that project"),
                "project": str(name)[:60],
            })
            return
        body, content_type = found
        self._send_bytes(200, body, content_type)

    def _make_thumbnail(self, name):
        """Photograph the project as it stands in the scene, and cache the PNG.

        Deliberately *only* a photograph.  It would be easy to make this open
        the script and generate the part when it is missing — and then a page
        that draws twelve cards would rebuild twelve parts, spend minutes of
        the artist's machine and change a scene they were looking at, all for
        pictures.  So a part that is not in the scene is a 409 naming the
        button that builds it, and the artist stays the one who decides when
        geometry happens.
        """
        # Drained BEFORE the project is resolved, not after: a 404 that returns
        # early would otherwise leave the body in the socket, and keep-alive
        # would read it as the start of the next request. See _read_json.
        self._read_json()
        entry, _folder = self._project(name)
        if entry is None:
            return
        object_name = entry["object"]
        if not object_name:
            self._send(422, {"error": "%s has no part script, so there is no "
                                      "object to photograph." % entry["name"],
                             "project": entry["name"]})
            return

        try:
            scene = blender_command("get_scene_info", {}, SCENE_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False,
                             "project": entry["name"]})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True,
                             "project": entry["name"]})
            return

        present = [str(item.get("name")) for item in (scene.get("objects") or [])
                   if isinstance(item, dict) and item.get("name")]
        if object_name not in present:
            self._send(409, {
                "error": NEEDS_GENERATING_HINT % object_name,
                "blender": True,
                "project": entry["name"],
                "object": object_name,
                "scene_objects": present[:40],
            })
            return

        params = {"path": new_preview_path(), "objects": [object_name]}
        try:
            result = blender_command("render_preview", params, PREVIEW_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False,
                             "project": entry["name"]})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True,
                             "project": entry["name"]})
            return

        path = str(result.get("path") or params["path"])
        cached = save_thumbnail(entry["name"], path)
        token = FILES.mint(path)
        if not token:
            self._send(502, {
                "error": "Blender reported a render but there is no readable "
                         "PNG at %s." % path,
                "blender": True, "project": entry["name"]})
            return
        _cached_path, mtime = thumbnail_stat(entry["name"])
        self._send(200, {
            "project": entry["name"],
            "object": object_name,
            # The render itself, by token — what the page swaps straight into
            # the card, since a fresh name per render can never be a browser
            # showing the previous shape out of its cache.
            "token": token,
            "url": "/file/%s" % token,
            "path": path,
            "cached": bool(cached),
            "thumbnail_url": "/projects/%s/thumbnail" % entry["name"],
            "thumbnail_mtime": mtime,
            "bounds_mm": result.get("bounds_mm"),
        })

    # -- the library's models (Phase 13, revisited) ----------------------
    def _model(self, payload):
        """The indexed model a request names, or ``None`` — answered on a miss.

        One sentence for every kind of miss, deliberately: "no such file", "not
        a mesh" and "not somewhere this library looks" told apart would make
        this route a way to ask what is on the machine.
        """
        resolved = resolve_indexed_model(payload.get("path"))
        if resolved is None:
            self._send(404, {"error": MODEL_UNKNOWN_HINT,
                             "path": str(payload.get("path") or "")})
            return None
        return resolved

    def _model_into_scene(self, resolved, payload, route=""):
        """``import_generated`` in the running Blender.  Answers, either way.

        Straight through to the add-on's ``import_generated``, which is the
        command the MCP server's ``generate_3d`` already ends on — same
        parameters, same mandatory repair.  The repair is the default and stays
        the default: raw image-to-3D output is never manifold, and nothing
        downstream (print checks, segmenting, retopo) works until it is one
        closed shell.

        Shared by ``POST /models/import`` (the button) and the *running* branch
        of ``POST /models/open`` (the card click), because those are one action:
        with Blender already up, "open this model" is the mesh arriving in the
        scene the artist is looking at.  Two copies of this would be two
        answers that could drift, and one of them would eventually forget the
        repair.
        """
        wanted = payload.get("repair")
        params = {"path": resolved, "repair": True if wanted is None else bool(wanted)}
        name = model_object_name(resolved)
        if name:
            params["name"] = name
        base = {"path": resolved, "file": os.path.basename(resolved)}
        if route:
            base["route"] = route
        try:
            result = blender_command("import_generated", params,
                                     MODEL_IMPORT_TIMEOUT)
        except BlenderDown as exc:
            # The file is on disk and safe; this is Blender being closed, which
            # is one sentence with one button in it — the panel's own.
            self._send(503, dict(base, error=str(exc), blender=False,
                                 imported=False, opened=False))
            return
        except BlenderRefused as exc:
            self._send(502, dict(base, error=str(exc), blender=True,
                                 imported=False, opened=False))
            return
        answer = dict(base)
        answer.update({
            "imported": True,
            "opened": True,
            "blender": True,
            "object": result.get("object"),
            "vertex_count": result.get("vertex_count"),
            "face_count": result.get("face_count"),
            "repaired": bool(result.get("repaired")),
            "dimensions_mm": result.get("dimensions_mm"),
            "result": result,
        })
        self._send(200, answer)

    def _model_import(self):
        """Put one indexed model into the Blender scene, repaired."""
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        resolved = self._model(payload)
        if resolved is None:
            return
        self._model_into_scene(resolved, payload)

    def _model_open(self):
        """Open one indexed model in Blender — three routes, the same one rule.

        The artist clicked the card, and what they mean by that does not depend
        on whether Blender happens to be running.  What *has* to depend on it is
        how: a running Blender always wins, exactly as it does for a project's
        own scene file (``POST /projects/<name>/open``), because two instances
        would fight over port 9876 and the add-on would end up talking to
        whichever won the race.

        1. ``running`` — the mesh is imported into the scene they are looking
           at, voxel-repaired on the way in.  Identical to pressing Import,
           which is the point: one click, one meaning.
        2. ``spawned`` — nothing is listening, so a windowed Blender starts with
           the model imported and the startup cube gone.  A ``.glb`` is not a
           ``.blend`` and cannot be a positional argument, so this goes through
           ``--python-expr`` (:func:`model_open_expression`) rather than the
           project-file spawn.
        3. ``no_blender`` — there is no Blender on this machine to start.  A
           501 naming the menu they would use by hand, which for a mesh file is
           File > Import, not File > Open.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        resolved = self._model(payload)
        if resolved is None:
            return
        base = {"path": resolved, "file": os.path.basename(resolved)}
        is_blend = resolved.lower().endswith(BLEND_SUFFIX)

        if blender_listening():
            if is_blend:
                # An arbitrary .blend into a live session would discard the
                # scene they are looking at; the safe flows are manual.
                self._send(200, dict(base, route="running", opened=False,
                                     imported=False, manual=True,
                                     note=BLEND_RUNNING_HINT % resolved))
                return
            self._model_into_scene(resolved, payload, route="running")
            return

        exe = resolve_blender()
        if not exe:
            hint = ("Install Blender, then click again — a .blend opens "
                    "directly." if is_blend
                    else MODEL_BLENDER_MISSING_HINT % resolved)
            self._send(501, dict(base, route="no_blender", opened=False,
                                 imported=False, blender=False, error=hint))
            return
        try:
            if is_blend:
                proc = spawn_detached([exe, resolved])
            else:
                proc = spawn_blender_with_model(exe, resolved)
        except OSError as exc:
            self._send(500, dict(base, route="no_blender", opened=False,
                                 imported=False, blender=False,
                                 error="Could not start %s: %s" % (exe, exc)))
            return

        self._send(200, dict(base, **{
            "route": "spawned",
            "opened": True,
            # Nothing has been imported into a *listening* Blender: the mesh is
            # being imported by a Blender that is still starting, and the add-on
            # is not answering yet.  Saying "imported" here would have the page
            # claim something it cannot see.
            "imported": False,
            "blender": False,
            "pid": proc.pid,
            "executable": exe,
            "note": "Blender is starting with %s imported. Give it a few "
                    "seconds — then press N in the 3D view, open the Forge tab "
                    "and Start Server so Forge can work on it."
                    % os.path.basename(resolved),
        }))

    def _model_file(self):
        """Copy one indexed model into ``projects/<slug>/models/``.

        A **copy**, not a move, and for the same reason ``save_project_blend``
        saves a copy: the meshgen output folder is where the service will look
        for its own output next time, and a library button that moved files out
        from under another service would be a bug nobody would connect to the
        button they pressed.

        Two independent gates, because this writes into the repo: the source has
        to be a file :func:`indexed_model_dirs` already lists, and the
        destination has to be one plain slug directly under ``projects/``.
        Neither is derived from the other.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        resolved = self._model(payload)
        if resolved is None:
            return
        raw = payload.get("project")
        slug = project_slug(raw)
        if not slug:
            self._send(400, {
                "error": "%r is not a project name. Pass the name of a project — "
                         '"bowl holder" becomes projects/bowl-holder/models/.'
                         % (str(raw or ""),),
                "path": resolved})
            return
        folder = project_models_dir(slug, create=True)
        if folder is None:
            self._send(400, {
                "error": "%s could not be made under %s." % (slug, projects_dir()),
                "path": resolved, "project": slug})
            return

        if os.path.normcase(os.path.dirname(resolved)) == os.path.normcase(folder):
            # Already filed there.  Not an error and not a second copy: the card
            # the page wanted back is the one it already had.
            entry = model_entry(resolved)
            self._send(200, {"project": slug, "filed": False, "copied": False,
                             "renamed": False, "folder": folder,
                             "path": resolved, "source": resolved,
                             "model": entry,
                             "note": "%s is already in %s."
                                     % (os.path.basename(resolved), folder)})
            return

        destination, renamed = free_model_path(folder, os.path.basename(resolved))
        try:
            shutil.copy2(resolved, destination)
        except OSError as exc:
            self._send(500, {"error": "Could not copy %s into %s: %s"
                                      % (resolved, folder, exc),
                             "path": resolved, "project": slug})
            return
        entry = model_entry(destination, project=slug)
        self._send(200, {
            "project": slug,
            "filed": True,
            "copied": True,
            "renamed": renamed,
            "folder": folder,
            "path": destination,
            "source": resolved,
            "model": entry,
            "note": "Copied into %s. The original is still in %s."
                    % (folder, os.path.dirname(resolved)),
        })

    # -- the workspace (Phase 18) ----------------------------------------
    #
    # Five routes, all additive, and four of the five are pure reads of files
    # that already exist.  The one that is not — the snapshot — writes a .glb
    # into the previews folder this bridge already owns and changes nothing in
    # the artist's scene.  None of the five goes through the model.

    def _project_folder(self, name, send=True):
        """``project_dir`` with this bridge's own 404 on it, or ``None``."""
        folder = project_dir(name)
        if folder is None and send:
            self._send(404, {
                "error": "There is no project called %r under %s."
                         % (str(name)[:60], projects_dir()),
                "project": str(name)[:60]})
        return folder

    def _versions(self, name):
        """The numbered ``.blend`` chain for one project."""
        folder = self._project_folder(name)
        if folder is None:
            return
        payload = project_versions(folder)
        payload["project"] = os.path.basename(folder)
        self._send(200, payload)

    def _restore_version(self, name):
        """Copy one saved version forward to the end of its chain.

        Non-destructive by construction, which is the whole design: nothing is
        overwritten, nothing is deleted and the version that was restored is
        still exactly where it was.  "Restore" here means "make this one the
        newest", and the way to do that without losing anything is a copy.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        folder = self._project_folder(name)
        if folder is None:
            return
        raw = payload.get("file")
        source = resolve_version_file(folder, raw)
        if source is None:
            # Deliberately one answer for "not a name", "wrong folder" and "not
            # there": a client that can tell those apart can use this route to
            # ask questions about the filesystem.
            self._send(400, {
                "error": VERSION_UNKNOWN_HINT % (str(raw or "")[:80] or "That"),
                "project": os.path.basename(folder),
                "file": str(raw or "")[:80]})
            return
        directory = os.path.dirname(source)
        base, number = split_version(os.path.basename(source))
        target_name, target_number = next_version_name(
            directory, base, max(number, highest_version(directory, base)))
        if not target_name:
            self._send(409, {
                "error": "Every name from %s-%d onwards is taken, so there is "
                         "nowhere to copy this version to."
                         % (base, number + 1),
                "project": os.path.basename(folder)})
            return
        destination = os.path.join(directory, target_name)
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            self._send(500, {
                "error": "Could not copy %s to %s: %s"
                         % (os.path.basename(source), target_name, exc),
                "project": os.path.basename(folder)})
            return
        try:
            info = os.stat(destination)
            size, mtime = int(info.st_size), round(info.st_mtime, 3)
        except OSError:
            size, mtime = 0, 0.0
        self._send(200, {
            "project": os.path.basename(folder),
            "restored": True,
            "stem": base,
            "file": target_name,
            "path": destination,
            "version": target_number,
            "source": os.path.basename(source),
            "source_path": source,
            "source_version": number,
            "size": size,
            "mtime": mtime,
            "modified": _iso_utc(mtime),
            "note": "Copied %s to %s. Nothing was overwritten and nothing was "
                    "deleted — %s is still version %d, exactly where it was."
                    % (os.path.basename(source), target_name,
                       os.path.basename(source), number),
        })

    def _pipeline(self, name):
        """``design/build-plan.json`` as the stage board, read and never written."""
        folder = self._project_folder(name)
        if folder is None:
            return
        self._send(200, pipeline_board(folder, os.path.basename(folder)))

    def _deliverables(self, name):
        """Everything in ``renders/`` worth looking at, newest first."""
        folder = self._project_folder(name)
        if folder is None:
            return
        payload = project_deliverables(folder)
        payload["project"] = os.path.basename(folder)
        self._send(200, payload)

    def _snapshot(self, name):
        """The live Blender scene as a ``.glb`` the page can orbit."""
        payload = self._read_json() or {}
        folder = self._project_folder(name)
        if folder is None:
            return
        obj = payload.get("object")
        obj = str(obj or "").strip()[:MAX_OBJECT_NAME]
        try:
            body, status = blender_snapshot(os.path.basename(folder), obj)
        except BlenderDown as exc:
            # The same sentence the rail, the panel and the flows use, and the
            # same 503: Blender being closed is the normal state of this
            # machine, not a failure of this route.
            self._send(503, {"error": str(exc), "blender": False})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True})
            return
        self._send(status, body)

    # -- project scene files (Phase 15) ----------------------------------
    def _save_project(self, name):
        """Write the Blender scene into ``projects/<name>/<name>.blend``.

        Straight through to the add-on's ``save_project_blend``, which saves a
        *copy* — so this route can never move where the artist's own Ctrl+S
        goes, no matter what the browser sends it.  The answer carries the card
        fields back so the page can redraw one card instead of the library.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        entry, folder = self._project(name)
        if entry is None:
            return
        try:
            result = blender_command("save_project_blend",
                                     {"project": entry["name"]},
                                     BLEND_SAVE_TIMEOUT)
        except BlenderDown as exc:
            self._send(503, {"error": str(exc), "blender": False,
                             "project": entry["name"]})
            return
        except BlenderRefused as exc:
            self._send(502, {"error": str(exc), "blender": True,
                             "project": entry["name"]})
            return

        blend = project_blend(folder)
        self._send(200, {
            "project": entry["name"],
            "saved": True,
            "blender": True,
            "path": result.get("path") or blend["path"],
            "object_count": result.get("object_count"),
            "replaced": bool(result.get("replaced")),
            # The one thing an artist would want checked, handed over rather
            # than promised: where *their* file still saves to.
            "session_file": result.get("session_file"),
            "has_blend": blend["exists"],
            "blend_size": blend["size"],
            "blend_mtime": blend["mtime"],
            "result": result,
        })

    def _open_project(self, name):
        """Open the project's own scene file — three routes, one rule.

        The rule is that **a running Blender always wins**.  Two instances would
        fight over port 9876 and the artist would end up with the add-on talking
        to whichever one won the race, so this never spawns while something is
        listening: it drives the running session through ``open_project_blend``
        instead, confirmation round trip and all.

        1. ``running`` — Blender is up.  The add-on may answer
           ``needs_confirmation``, which is a **200**, not an error: it is a
           question, and the page asks it and comes back with ``confirm``.
        2. ``spawned`` — Blender is not up and there is a ``.blend``.  A
           windowed Blender is started on it.  The artist clicked Open, so this
           is them launching Blender; the no-windowed-Blender law binds agents,
           not the person whose machine it is.
        3. ``no_blend`` — there is nothing to open yet.  Also a 200: it is the
           ordinary state of every project until someone saves one, and the page
           answers it by opening the part in the Studio and offering to save.
        """
        payload = self._read_json()
        if payload is None:
            self._send(400, {"error": "The request body was not a JSON object."})
            return
        entry, folder = self._project(name)
        if entry is None:
            return
        confirm = bool(payload.get("confirm"))
        blend = project_blend(folder)
        base = {"project": entry["name"], "blend": blend["path"],
                "has_blend": blend["exists"]}

        if not blend["exists"]:
            answer = dict(base)
            answer.update({
                "route": "no_blend",
                "opened": False,
                "hint": NO_BLEND_HINT % entry["name"],
                "object": entry["object"],
                "script_path": entry["script_path"],
            })
            self._send(200, answer)
            return

        if blender_listening():
            try:
                result = blender_command("open_project_blend",
                                         {"name": entry["name"],
                                          "confirm": confirm},
                                         BLEND_OPEN_TIMEOUT)
            except BlenderDown as exc:
                # It was listening a moment ago and is not now: say so rather
                # than racing to spawn a second one on top of a closing first.
                self._send(503, {"error": str(exc), "blender": False, **base})
                return
            except BlenderRefused as exc:
                self._send(502, {"error": str(exc), "blender": True, **base})
                return
            answer = dict(base)
            answer.update({
                "route": "running",
                "blender": True,
                "opened": bool(result.get("opened")),
                "needs_confirmation": bool(result.get("needs_confirmation")),
                "would_lose": result.get("would_lose") or "",
                "hint": result.get("hint") or "",
                "object_count": result.get("object_count"),
                "session_file": result.get("session_file"),
                "result": result,
            })
            self._send(200, answer)
            return

        exe = resolve_blender()
        if not exe:
            answer = dict(base)
            answer.update({"route": "no_blender", "opened": False,
                           "error": BLENDER_MISSING_HINT % blend["path"]})
            self._send(501, answer)
            return
        try:
            proc = spawn_blender(exe, blend["path"])
        except OSError as exc:
            answer = dict(base)
            answer.update({"route": "no_blender", "opened": False,
                           "error": "Could not start %s: %s" % (exe, exc)})
            self._send(500, answer)
            return

        answer = dict(base)
        answer.update({
            "route": "spawned",
            "opened": True,
            "blender": False,
            "pid": proc.pid,
            "executable": exe,
            "note": "Blender is starting with %s. Give it a few seconds — then "
                    "press N in the 3D view, open the Forge tab and Start "
                    "Server if it is not already listening."
                    % os.path.basename(blend["path"]),
        })
        self._send(200, answer)


#: How long the startup guard waits for whatever is already on the port.  Short
#: on purpose: this runs before anything is bound, and a slow answer is still
#: an answer — something is there, and that is the whole question.
GUARD_TIMEOUT = 3.0


def force_start():
    """Is ``FORGE_FORCE_START`` set?  Then bind anyway and let it fail loudly."""
    return str(_env("FORGE_FORCE_START", "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def already_serving(listen_port, host="127.0.0.1", timeout=GUARD_TIMEOUT):
    """One line describing the process already on this port, or ``""``.

    B-6: every Forge service sets ``allow_reuse_address``, so a second start
    does not fail — it loses the port race and stays resident.  The dogfood run
    found two of everything, including a second meshgen holding a model loader
    on a 12 GB card.  So the question is asked before binding, over HTTP rather
    than by looking at the port, because "is a Forge service answering here?"
    is the thing worth knowing and ``/health`` is where it says so.

    Failure to reach anything returns ``""`` — an unreachable port is a free
    port as far as this guard is concerned, and a guard that refuses to start
    on a bad probe would be worse than the duplicate it prevents.
    """
    url = "http://%s:%d/health" % (host, int(listen_port))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(200000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - nothing there, or not speaking HTTP
        return ""
    build = {}
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get("build"), dict):
            build = data["build"]
    except ValueError:
        pass
    return ("something is already answering on %s (pid %s, sha %s, up %ss). "
            "Not starting a second one — set FORGE_FORCE_START=1 to override."
            % (url, build.get("pid", "?"), build.get("sha", "?"),
               build.get("uptime_s", "?")))


def serve(host="127.0.0.1", listen_port=None, ready=None):
    """Run the bridge until interrupted.  ``ready`` is called with the server."""
    listen_port = int(listen_port or port())
    httpd = ThreadingHTTPServer((host, listen_port), Handler)
    httpd.daemon_threads = True
    info = claude_info()
    log("[assistant] listening on http://%s:%d  (claude: %s)"
        % (host, httpd.server_address[1],
           info.get("path") if info.get("found") else "NOT FOUND"))
    if ready is not None:
        ready(httpd)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    listen_port = port()
    if argv and argv[0] in ("--port", "-p"):
        try:
            listen_port = int(argv[1])
        except (IndexError, ValueError):
            log("usage: bridge.py [--port N]")
            return 2
    if not force_start():
        existing = already_serving(listen_port)
        if existing:
            # Exit 0: "it is already running" is the state the caller wanted,
            # not a failure.  start_forge.ps1 runs this on every launch.
            log("[assistant] %s" % existing)
            return 0
    serve(listen_port=listen_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
