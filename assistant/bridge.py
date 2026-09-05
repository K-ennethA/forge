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
``GET  /health``        -> ``{"status", "claude_cli": {"found", "path", "version"}}``
``POST /ask``           -> ``{"job_id"}``  (409 while another job is running)
``GET  /job/<id>``      -> ``{"state", "activity", "reply"?, "session_id"?, ...}``
``POST /cancel/<id>``   -> ``{"state": "cancelled"}``

Activity (Phase 6b)
-------------------
The CLI runs with ``--output-format stream-json --verbose
--include-partial-messages``, so its stdout is newline-delimited JSON events
rather than one object at the end.  Those events are parsed as they arrive into
``job["activity"]`` — ``[{"t": epoch, "kind": "tool"|"text"|"status", "label"}]``
— which ``GET /job/<id>`` returns on every poll.  The panel draws the last few
lines under the busy indicator so the artist can see what the AI is doing
instead of watching a spinner.

Parsing is deliberately forgiving: an event shape this bridge does not
recognise is skipped, never fatal, and a run that never prints a ``result``
event but exits 0 is salvaged from the last parseable line (falling back to the
text that streamed past).

Environment
-----------
``FORGE_ASSISTANT_PORT``     listen port (default 8901)
``FORGE_ASSISTANT_CLAUDE``   full path to the claude executable; skips discovery.
                             The test suite points this at a fake CLI.
``FORGE_ASSISTANT_MODEL``    ``--model`` value; unset = the user's default model
``FORGE_ASSISTANT_TIMEOUT``  seconds per turn (default 600)
``FORGE_ASSISTANT_TOOLS``    ``--allowedTools`` value; unset = the Forge default.
                             Set it to the empty string for a no-tools run.
``FORGE_ASSISTANT_CWD``      working directory for the CLI (default: the repo root)
``FORGE_ASSISTANT_TEXT_INTERVAL``  seconds between text activity markers
                             (default 2.0; 0 = every chunk, for the tests)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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

MAX_JOBS = 20

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


def build_prompt(message, context):
    return str(message or "").strip() + format_context(context)


def build_argv(claude_path, prompt, session_id=None, permission_mode="auto"):
    """The exact command line a turn runs.

    Kept as one pure function so the tests can assert on it without spawning
    anything, and so the argv that ships is the argv that was tested.

    ``stream-json`` (not ``json``) is what makes the activity list possible: the
    CLI prints one JSON event per line as it works instead of a single object
    when it is done.  ``--verbose`` is required by the CLI for stream-json in
    ``-p`` mode, and ``--include-partial-messages`` is what turns text into
    token deltas we can show as progress.  The final ``type: "result"`` line
    carries exactly the fields ``--output-format json`` used to.
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

    model = _env("FORGE_ASSISTANT_MODEL")
    if model and model.strip():
        argv += ["--model", model.strip()]

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

    # -- pieces ----------------------------------------------------------
    def _tool(self, tool_id, name, args):
        if not name:
            return
        tool_id = tool_id or "tool-%d" % len(self._tool_entries)
        self._seen_tool = True
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
# jobs
# ---------------------------------------------------------------------------

class JobStore(object):
    """Every turn this bridge has run, newest last, capped at ``MAX_JOBS``.

    Also owns the one-at-a-time rule and the session id, because they are the
    same piece of state: a second turn started while the first is mid-flight
    would fight over ``--resume``.
    """

    def __init__(self, limit=MAX_JOBS):
        self._lock = threading.RLock()
        self._jobs = OrderedDict()
        self._limit = limit
        self._active = None
        self.session_id = None

    # -- session ---------------------------------------------------------
    def reset_session(self):
        with self._lock:
            previous = self.session_id
            self.session_id = None
            return previous

    def remember_session(self, session_id):
        with self._lock:
            if session_id:
                self.session_id = str(session_id)

    # -- lifecycle -------------------------------------------------------
    def start(self, message):
        """Claim the single slot. Returns ``(job, None)`` or ``(None, busy_job)``."""
        with self._lock:
            active = self._jobs.get(self._active) if self._active else None
            if active is not None and active["state"] == "running":
                return None, active
            job_id = uuid.uuid4().hex[:12]
            job = {
                "job_id": job_id,
                "state": "running",
                "message": message,
                "started_at": time.time(),
                "proc": None,
                "cancelled": False,
                "activity": [],
                "activity_dropped": 0,
            }
            self._jobs[job_id] = job
            self._active = job_id
            while len(self._jobs) > self._limit:
                oldest, _value = next(iter(self._jobs.items()))
                if oldest == self._active:
                    break
                self._jobs.pop(oldest, None)
            return job, None

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def is_busy(self):
        with self._lock:
            active = self._jobs.get(self._active) if self._active else None
            return bool(active is not None and active["state"] == "running")

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
            return public_job(job)

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

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
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


def public_job(job):
    """The subset of a job the panel is allowed to see."""
    if job is None:
        return None
    out = {"job_id": job["job_id"], "state": job["state"]}
    for key in ("reply", "session_id", "cost_usd", "duration_ms", "error",
                "model", "usage", "num_turns"):
        if job.get(key) is not None:
            out[key] = job[key]
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


def run_turn(job_id, prompt, session_id):
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
        argv = build_argv(claude_path, prompt, session_id=session_id, permission_mode=mode)
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

    def _read_json(self):
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
        self._send(404, {"error": "Unknown path %s" % path})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/ask":
            self._ask()
            return
        if path.startswith("/cancel/"):
            job = JOBS.cancel(path[len("/cancel/"):])
            if job is None:
                self._send(404, {"error": "No such job."})
                return
            self._send(200, JOBS.snapshot(job["job_id"]))
            return
        if path == "/new":
            JOBS.reset_session()
            self._send(200, {"status": "ok", "session": None})
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

        info = claude_info()
        if not info.get("found"):
            self._send(503, {"error": INSTALL_HINT})
            return

        if str(payload.get("conversation") or "continue").lower() == "new":
            JOBS.reset_session()

        job, busy = JOBS.start(message)
        if job is None:
            self._send(409, {
                "error": "The assistant is still working on your last message. "
                         "Give it a moment, or press Stop.",
                "job_id": busy["job_id"],
            })
            return

        prompt = build_prompt(message, payload.get("context"))
        session_id = JOBS.session_id
        thread = threading.Thread(
            target=run_turn, args=(job["job_id"], prompt, session_id),
            name="ForgeAssistantTurn", daemon=True)
        thread.start()
        self._send(200, {"job_id": job["job_id"], "state": "running"})


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
