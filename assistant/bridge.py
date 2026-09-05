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
``GET  /job/<id>``      -> ``{"state", "reply"?, "session_id"?, "cost_usd"?, ...}``
``POST /cancel/<id>``   -> ``{"state": "cancelled"}``

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
from collections import OrderedDict
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
    """
    argv = launcher(claude_path) + ["-p", prompt, "--output-format", "json"]

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

    ``--output-format json`` writes one object, but node warnings, update
    notices and shell wrappers all like to print a line first.  Try the whole
    blob, then every line from the last backwards, then any brace-balanced
    region.  Returns ``None`` when nothing parses.
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


def public_job(job):
    """The subset of a job the panel is allowed to see."""
    if job is None:
        return None
    out = {"job_id": job["job_id"], "state": job["state"]}
    for key in ("reply", "session_id", "cost_usd", "duration_ms", "error",
                "model", "usage", "num_turns"):
        if job.get(key) is not None:
            out[key] = job[key]
    return out


# ---------------------------------------------------------------------------
# running a turn
# ---------------------------------------------------------------------------

def run_turn(job_id, prompt, session_id):
    """Spawn the CLI, read its JSON, land the result on the job. Never raises."""
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
        try:
            raw_out, raw_err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            JOBS.finish(job_id, state="error",
                        error="The assistant took longer than %.0f seconds and was stopped. "
                              "Try a smaller request, or raise FORGE_ASSISTANT_TIMEOUT."
                              % limit)
            return
        except Exception as exc:  # noqa: BLE001
            JOBS.finish(job_id, state="error", error="Claude CLI failed: %s" % exc)
            return

        job = JOBS.get(job_id)
        if job is not None and job.get("cancelled"):
            JOBS.finish(job_id, state="cancelled", error="Cancelled.")
            return

        stdout = (raw_out or b"").decode("utf-8", "replace")
        stderr = (raw_err or b"").decode("utf-8", "replace")
        code = proc.returncode

        # Step down through the permission-mode flags an older build rejects.
        if code not in (0, 2) and mode and _looks_like_bad_flag(stderr, "--permission-mode"):
            last_error = _tail(stderr)
            continue

        payload = salvage_json(stdout)
        if payload is None:
            detail = _tail(stderr) or _tail(stdout) or "no output"
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

        reply = extract_reply(payload)
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
            job = JOBS.get(path[len("/job/"):])
            if job is None:
                self._send(404, {"error": "No such job. It may have scrolled out of "
                                          "the last %d." % MAX_JOBS})
                return
            self._send(200, public_job(job))
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
            self._send(200, public_job(JOBS.get(job["job_id"])))
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
