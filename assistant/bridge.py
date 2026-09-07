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
                            "busy", "queued", "session_cost_usd", "last_auth_error"}``
``POST /ask``           -> ``{"job_id", "state": "running"|"queued"}``
                           (409 only when a message is ALREADY waiting;
                            optional ``"model": "haiku"|"sonnet"|"opus"``)
``GET  /job/<id>``      -> ``{"state", "activity", "session_cost_usd", "reply"?, ...}``
``POST /cancel/<id>``   -> ``{"state": "cancelled"}``

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

Environment
-----------
``FORGE_ASSISTANT_PORT``     listen port (default 8901)
``FORGE_ASSISTANT_CLAUDE``   full path to the claude executable; skips discovery.
                             The test suite points this at a fake CLI.
``FORGE_ASSISTANT_MODEL``    fallback ``--model`` value, used only when the
                             request named none; unset = the CLI's own default
``FORGE_ASSISTANT_TIMEOUT``  seconds per turn (default 600)
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
                             passthrough talks to (default 127.0.0.1:9876)
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

DEFAULT_PORT = 8901
DEFAULT_TIMEOUT = 600.0

#: Read-only repo access plus every Forge MCP tool.  The server name comes from
#: ``.mcp.json`` at the repo root ("forge"), so the wildcard is ``mcp__forge__*``.
MCP_SERVER = "forge"
DEFAULT_ALLOWED_TOOLS = "Read,Glob,Grep,mcp__%s__*" % MCP_SERVER

#: Permission modes tried in order.  Older CLI builds reject "auto"; rather than
#: sniffing the version we let the process tell us and step down.
PERMISSION_MODES = ("auto", "acceptEdits", None)

CONTEXT_DIVIDER = "--- Current Blender context ---"

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
#: exporter write, and a browser that cannot show one can still download it.
SERVABLE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}

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

#: Absolute paths of servable files, wherever they appear in a reply, a tool
#: argument or a tool result.  Windows drive letters and UNC/POSIX roots both;
#: quotes, brackets and backticks end a path because markdown wraps them.
_FILE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"'`<>|*?\r\n]*?"
    r"\.(?:png|jpe?g|webp|bmp|glb|gltf)\b",
    re.IGNORECASE)

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
HEALTH_TIMEOUT = 2.5
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
#: How much streamed text is kept to stand in for a missing result event.
MAX_SALVAGE_TEXT = 8000

#: Only meaningful on Windows; kept as 0 elsewhere so the same call site works.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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


def build_prompt(message, context):
    rest, image_path = split_image(context)
    return (str(message or "").strip()
            + format_context(rest)
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
        self._all_text = ""
        self._last_text_at = 0.0

    # -- output ----------------------------------------------------------
    def text(self):
        """Everything that streamed past as assistant text."""
        return self._all_text.strip()

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
        kind = payload.get("type")
        if kind == "stream_event":
            self._event(payload.get("event"))
        elif kind == "assistant":
            self._message(payload.get("message"))
        elif kind == "user":
            # A tool's *result* — where a render's path usually first appears.
            # Nothing is logged from here (the tool call already has a line);
            # it is read only for paths worth a token.
            self._files(payload.get("message"))
        elif kind in ("content_block_start", "content_block_delta",
                      "content_block_stop"):
            # a build that emits the raw Anthropic events without the wrapper
            self._event(payload)

    def _event(self, event):
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        index = event.get("index")
        if etype == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "block-%s" % index)
                self._block_tools[index] = tool_id
                self._tool(tool_id, block.get("name"), block.get("input"))
            return
        if etype == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return
            dtype = delta.get("type")
            if dtype == "text_delta":
                self._saw_text_delta = True
                self._text(delta.get("text"))
            elif dtype == "input_json_delta":
                tool_id = self._block_tools.get(index)
                if tool_id:
                    chunk = str(delta.get("partial_json") or "")
                    self._tool_json[tool_id] = (
                        self._tool_json.get(tool_id, "") + chunk)[:8000]
            return
        if etype == "content_block_stop":
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
                self._text(block.get("text"))

    def _files(self, value):
        """Record any servable path in ``value`` against this job."""
        try:
            self.store.note_files_in(self.job_id, value, "activity")
        except Exception:  # noqa: BLE001 - a thumbnail is never worth the turn
            pass

    # -- pieces ----------------------------------------------------------
    def _tool(self, tool_id, name, args):
        if not name:
            return
        tool_id = tool_id or "tool-%d" % len(self._tool_entries)
        self._seen_tool = True
        # Before the label is clipped to a basename: the token needs the whole
        # path, and this is the last place it exists in full.
        self._files(args)
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
        self.store.relabel_activity(
            self.job_id, entry, tool_label(self._tool_names.get(tool_id), args))
        self._tool_detailed.add(tool_id)

    def _text(self, chunk):
        chunk = str(chunk or "")
        if not chunk:
            return
        if self._seen_tool and not self._said_thinking:
            # The tools are done and words are coming: say so once.
            self.push("status", "thinking…")
            self._said_thinking = True
        self._pending_text += chunk
        if len(self._all_text) < MAX_SALVAGE_TEXT:
            self._all_text += chunk

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


def find_file_paths(text, limit=12):
    """Absolute paths of servable files mentioned in ``text``.

    Used on replies and tool results, where a render's path arrives as prose
    ("saved to C:\\forge\\projects\\cup\\render.png") rather than as a field.
    """
    if not text:
        return []
    out = []
    for match in _FILE_PATH_RE.finditer(str(text)):
        candidate = match.group(0)
        if candidate not in out:
            out.append(candidate)
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


def file_entry(token, path, source):
    """The public shape of one recorded file."""
    extension = os.path.splitext(path)[1].lower()
    return {
        "token": token,
        "path": path,
        "name": os.path.basename(path) or path,
        "ext": extension,
        "kind": "image" if extension in IMAGE_EXTENSIONS else "model",
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

    # -- session ---------------------------------------------------------
    def reset_session(self):
        with self._lock:
            previous = self.session_id
            self.session_id = None
            # The running total is per-conversation: a fresh conversation has
            # not cost anything yet, so the panel's status row starts at zero.
            self.session_cost_usd = 0.0
            return previous

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
        """Note every servable path inside a value (a reply, a tool's args…)."""
        found = []
        for text in walk_strings(value):
            for path in find_file_paths(text):
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
        """
        state = job.get("state")
        if state == "done":
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
    for key in ("reply", "session_id", "cost_usd", "duration_ms", "error",
                "model", "usage", "num_turns"):
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


def read_stream(proc, limit, recorder):
    """Consume the CLI's NDJSON stdout, feeding ``recorder`` as it goes.

    Returns ``{"result", "last", "stdout_tail", "stderr", "code", "timed_out"}``.
    The timeout is a watchdog thread rather than a read deadline because
    ``readline`` blocks: when it fires the process is terminated, the pipe
    closes and this loop ends on its own.
    """
    stderr_chunks = []
    stderr_thread = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_chunks),
        name="ForgeAssistantStderr", daemon=True)
    stderr_thread.start()

    state = {"timed_out": False}

    def on_timeout():
        state["timed_out"] = True
        _terminate(proc)

    watchdog = threading.Timer(limit, on_timeout)
    watchdog.daemon = True
    watchdog.start()

    result = None
    last = None
    tail = deque(maxlen=20)
    try:
        for raw in iter(proc.stdout.readline, b""):
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
        watchdog.cancel()
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
    }


def run_turn(job_id, prompt, session_id, model=""):
    """Spawn the CLI, read its event stream, land the result. Never raises."""
    claude_path = resolve_claude()
    if not claude_path:
        JOBS.finish(job_id, state="error", error=INSTALL_HINT)
        return

    cwd = working_dir()
    limit = timeout_s()
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
            outcome = read_stream(proc, limit, recorder)
        except Exception as exc:  # noqa: BLE001
            JOBS.finish(job_id, state="error", error="Claude CLI failed: %s" % exc)
            return

        if outcome["timed_out"]:
            JOBS.finish(job_id, state="error",
                        error="The assistant took longer than %.0f seconds and was stopped. "
                              "Try a smaller request, or raise FORGE_ASSISTANT_TIMEOUT."
                              % limit)
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
        streamed = recorder.text()
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

        reply = extract_reply(payload) or streamed
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
    if not text or text in (".", "..") or not _PROJECT_NAME_RE.match(text):
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
# Phase 9 — the health strip, fanned out from here
# ---------------------------------------------------------------------------

def service_urls():
    return {
        "geometry": str(_env("FORGE_SERVICE_URL", DEFAULT_SERVICE_URL)).rstrip("/"),
        "meshgen": str(_env("FORGE_MESHGEN_URL", DEFAULT_MESHGEN_URL)).rstrip("/"),
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
            self._send_bytes(200, body, content_type, {
                "Content-Disposition": 'inline; filename="%s"'
                                       % filename.replace('"', "")})
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

        # Built now, not at pickup: this is the scene the artist was looking at
        # when they pressed Send, and it is what their message is about.
        prompt = build_prompt(message, payload.get("context"))
        new_conversation = (
            str(payload.get("conversation") or "continue").lower() == "new")

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
            "objects": result.get("objects") or objects,
            "view": result.get("view") or params.get("view") or "iso",
            "framed_all_visible": bool(result.get("framed_all_visible")),
            "bounds_mm": result.get("bounds_mm"),
            "resolution": result.get("resolution"),
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
    serve(listen_port=listen_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
