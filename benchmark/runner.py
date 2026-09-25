"""Run one frozen benchmark task through the live bridge and write its report.

Usage (standalone)::

    python benchmark/runner.py <task_dir> <out.json> [--bridge URL] [--poll S]

What happens, in order:

1. ``task_dir/task.json`` is read (:func:`benchmark.quality.load_task`).
2. The prompt goes to the Forge Assistant bridge as ``POST /ask``
   ``{"message", "conversation": "new", "context"?, "model"?}`` — a fresh
   conversation every run, so a run never inherits the last one's context.
   ``context.image_path`` carries the task's frozen reference image, the same
   field the panel uses (``assistant/bridge.py`` "Reference images").
3. ``GET /job/<id>`` is polled until the job reaches one of the bridge's four
   terminal states (``done`` / ``error`` / ``timeout`` / ``cancelled``).  The
   runner keeps its own deadline too (``timeout_s`` + grace): past it the job
   is cancelled with ``POST /cancel/<id>`` and the run lands as ``timeout``.
4. ``wall_seconds`` is the runner's monotonic clock from just before ``/ask``
   to the poll that saw the terminal state (so it carries up to one poll
   interval of slack; the bridge's own ``duration_ms`` is recorded beside it).
5. The task's ``artifact`` is measured only if it was written during this run
   (mtime at or after the start): an artifact left over from an earlier run is
   a claim, not a result, and measures as missing.
6. :func:`benchmark.quality.evaluate` grades it; the report is validated
   against :mod:`benchmark.report` and written to ``out.json``.

Standard library only.  Nothing here starts the bridge, Blender's GUI or a
model: the bridge must already be running (``start_forge.cmd``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmark import quality  # noqa: E402
from benchmark.report import SCHEMA, TERMINAL_STATES, validate_report  # noqa: E402

DEFAULT_BRIDGE = "http://127.0.0.1:8901"
DEFAULT_POLL_S = 1.0
#: Past the task's own ``timeout_s`` the bridge's clock should have ended the
#: turn already (its ``timeout`` state); this is the runner's backstop.
DEADLINE_GRACE_S = 60.0
#: Consecutive failed polls before the run is called an ``error``: a bridge
#: that has gone away is not going to report a terminal state.
MAX_POLL_FAILURES = 10
#: An artifact's mtime may trail the start clock by filesystem granularity.
FRESH_SLACK_S = 2.0

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# the bridge, over plain HTTP
# ---------------------------------------------------------------------------

def default_token_file() -> str:
    """Where the bridge writes its request token (``FORGE_ASSISTANT_TOKEN_FILE``)."""
    return os.path.abspath(os.environ.get("FORGE_ASSISTANT_TOKEN_FILE")
                           or os.path.join(REPO_ROOT, "assistant", ".bridge-token"))


class BridgeClient:
    """``/ask``, ``/job/<id>`` and ``/cancel/<id>`` — nothing else.

    Every POST carries ``Authorization: Bearer <token>``: the bridge mints a
    token at start, writes it to ``assistant/.bridge-token`` and refuses a POST
    without it.  The file is re-read on every call, so a bridge restarted
    mid-run (a new token) does not strand the run.
    """

    def __init__(self, base_url: str = DEFAULT_BRIDGE, http_timeout: float = 30.0,
                 token_file: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.http_timeout = http_timeout
        self.token_file = token_file

    def token(self) -> Optional[str]:
        path = self.token_file or default_token_file()
        try:
            with open(path, "r", encoding="ascii") as handle:
                return handle.read().strip() or None
        except OSError:
            return None  # the bridge will say, by name, which file it wanted

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.token()
        if token:
            headers["Authorization"] = "Bearer %s" % token
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b"{}"
            status = exc.code
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {"error": raw[:500].decode("utf-8", "replace")}
        return status, payload if isinstance(payload, dict) else {"payload": payload}

    def ask(self, message: str, context: Optional[dict] = None,
            model: Optional[str] = None) -> Tuple[int, dict]:
        body: Dict[str, Any] = {"message": message, "conversation": "new"}
        if context:
            body["context"] = context
        if model:
            body["model"] = model
        return self._call("POST", "/ask", body)

    def job(self, job_id: str) -> Tuple[int, dict]:
        return self._call("GET", "/job/%s" % job_id)

    def cancel(self, job_id: str) -> Tuple[int, dict]:
        return self._call("POST", "/cancel/%s" % job_id, {})


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def _git(*args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git"] + list(args), cwd=REPO_ROOT, capture_output=True,
                              timeout=10, creationflags=_NO_WINDOW)
    except Exception:  # noqa: BLE001 - no git is "unknown", never a failed run
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip()


def git_state() -> Tuple[str, Optional[bool]]:
    sha = _git("rev-parse", "--short", "HEAD") or "unknown"
    status = _git("status", "--porcelain", "--untracked-files=no")
    return sha, (None if status is None else bool(status))


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------

def run_task(task_dir: str, out_path: str, bridge: Optional[BridgeClient] = None,
             poll_s: float = DEFAULT_POLL_S,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep,
             evaluate: Callable[..., Dict[str, Any]] = quality.evaluate) -> Dict[str, Any]:
    """Submit, poll, time, measure, write.  Returns the report it wrote."""
    task = quality.load_task(task_dir)
    bridge = bridge or BridgeClient(os.environ.get("FORGE_BENCH_BRIDGE", DEFAULT_BRIDGE))
    sha, dirty = git_state()

    context = {}
    if task.get("image"):
        context["image_path"] = os.path.join(task["_dir"], task["image"])

    started_epoch = time.time()
    t0 = clock()
    job_id = None
    snapshot: Dict[str, Any] = {}
    runner_deadline = False
    error = None

    try:
        status, reply = bridge.ask(task["prompt"], context or None, task.get("model"))
    except Exception as exc:  # noqa: BLE001 - an unreachable bridge is a refused run
        status, reply = None, {"error": "%s: %s" % (type(exc).__name__, exc)}
    if status != 200 or not reply.get("job_id"):
        final_state = "refused"
        error = reply.get("error") or "HTTP %s from /ask" % status
    else:
        job_id = reply["job_id"]
        final_state = None
        failures = 0
        deadline = float(task["timeout_s"]) + DEADLINE_GRACE_S
        while final_state is None:
            try:
                code, snap = bridge.job(job_id)
            except Exception as exc:  # noqa: BLE001
                code, snap = None, {"error": str(exc)}
            if code == 200:
                failures = 0
                snapshot = snap
                if snap.get("state") in TERMINAL_STATES:
                    final_state = snap["state"]
                    break
            else:
                failures += 1
                if failures >= MAX_POLL_FAILURES:
                    final_state = "error"
                    error = "lost the job after %d failed polls: %s" % (
                        failures, snap.get("error") or "HTTP %s" % code)
                    break
            if clock() - t0 > deadline:
                runner_deadline = True
                try:
                    bridge.cancel(job_id)
                except Exception:  # noqa: BLE001
                    pass
                final_state = "timeout"
                break
            sleep(poll_s)
    wall_seconds = clock() - t0

    # -- the artifact -------------------------------------------------------
    path = quality.artifact_path(task)
    exists = bool(path and os.path.isfile(path))
    mtime = os.path.getmtime(path) if exists else None
    fresh = bool(exists and mtime >= started_epoch - FRESH_SLACK_S)
    if fresh:
        graded = evaluate(task, path, log_path=out_path)
    else:
        reason = ("no artifact at %s" % path if not exists else
                  "stale artifact %s (written %s, run started %s)"
                  % (path, _iso(mtime), _iso(started_epoch)))
        graded = quality.grade(task, None, reason)
        graded.update(measure={"error": reason}, measure_seconds=0.0)

    duration_ms = snapshot.get("duration_ms")
    report = {
        "schema": SCHEMA,
        "task": task["name"],
        "task_version": task.get("version"),
        "mode": task["mode"],
        "git_sha": sha,
        "git_dirty": dirty,
        "started_at": _iso(started_epoch),
        "wall_seconds": round(wall_seconds, 3),
        "final_state": final_state,
        "job_id": job_id,
        "runner_deadline_hit": runner_deadline,
        "error": error or snapshot.get("error"),
        "bridge": {
            "duration_s": round(duration_ms / 1000.0, 3) if duration_ms is not None else None,
            "cost_usd": snapshot.get("cost_usd"),
            "model": snapshot.get("model") or snapshot.get("routed_model"),
            "num_turns": snapshot.get("num_turns"),
        },
        "artifact": {"path": path, "exists": exists, "fresh": fresh,
                     "mtime": _iso(mtime) if mtime else None},
        "gates": graded["gates"],
        "fidelity": graded["fidelity"],
        "score": graded["score"],
        "appearance_score": graded.get("appearance_score"),
        "measure_seconds": graded.get("measure_seconds"),
    }
    problems = validate_report(report)
    if problems:
        raise RuntimeError("the report this runner built is malformed: %s" % problems)

    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one forge benchmark task.")
    parser.add_argument("task_dir")
    parser.add_argument("out_json")
    parser.add_argument("--bridge", default=os.environ.get("FORGE_BENCH_BRIDGE", DEFAULT_BRIDGE))
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_S)
    args = parser.parse_args(argv)
    report = run_task(args.task_dir, args.out_json, bridge=BridgeClient(args.bridge),
                      poll_s=args.poll)
    print("%s: %s in %.1fs, gates %d/%d, fidelity %d/%d, score %.3f -> %s" % (
        report["task"], report["final_state"], report["wall_seconds"],
        report["gates"]["passed"], report["gates"]["total"],
        sum(1 for r in report["fidelity"].values() if r["pass"]), len(report["fidelity"]),
        report["score"], args.out_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
