"""Dispatch a chore to a local coding worker, gate it, retry it, log everything.

The doctrine this implements is ``docs/automation-thesis.md``'s weak generator /
strong verifier split: a small local model is allowed to be wrong as often as it
likes, because nothing it produces survives a deterministic gate it cannot argue
with.  The orchestrator's judgment budget is spent writing the brief and the
gate, not typing the diff.

    task = {
        "id":             "printer-merge-tests",     # run directory name
        "brief":          "<markdown>",              # what to do
        "files_scope":    ["service/tests/x.py"],    # the ONLY files that may change
        "acceptance_cmd": "python -m pytest ...",    # exit 0 == pass
        "cwd":            "<abs dir the worker works in>",
        "max_attempts":   2,
    }

    from worker.dispatch import dispatch
    report = dispatch(task)          # -> dict, also written to runs/<id>/result.json

No git.  Rollback is a byte-for-byte snapshot of ``files_scope`` taken before the
first attempt and restored after every failure, so a failed attempt leaves the
tree exactly as it was found -- including deleting files the worker created that
did not exist before.

Everything lands under ``worker/runs/<id>/`` as plain files while the run is
still going, so the orchestrator monitors a dispatch by *reading files*, not by
holding the process open:

    runs/<id>/task.json          the task as resolved (defaults filled in)
    runs/<id>/brief.md           the original brief
    runs/<id>/status.txt         RUNNING -> PASS | FAILED  (single word, first line)
    runs/<id>/attempt-N/brief.md         what the worker was actually told
    runs/<id>/attempt-N/worker.log       worker stdout+stderr
    runs/<id>/attempt-N/changes.diff     unified diff vs the snapshot
    runs/<id>/attempt-N/gate.log         acceptance_cmd output
    runs/<id>/attempt-N/result.json      per-attempt outcome
    runs/<id>/result.json        the final report

Stdlib only: it runs under any Python in the repo (3.11 or 3.14), and the aider
venv it drives is a subprocess, never an import.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

WORKER_DIR = Path(__file__).resolve().parent
REPO_ROOT = WORKER_DIR.parent
RUNS_DIR = WORKER_DIR / "runs"
CONFIG_PATH = WORKER_DIR / "config.json"

#: Directories never counted when looking for files the worker touched off-scope.
IGNORED_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".venv",
    ".aider.tags.cache.v4",
    ".ruff_cache",
    "node_modules",
    ".mypy_cache",
}


class DispatchError(RuntimeError):
    """The dispatch could not be run at all (bad task, no backend, no model)."""


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    """Worker defaults from ``worker/config.json``, with ``%VARS%`` expanded."""
    raw: Dict[str, Any] = {}
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        out[key] = os.path.expandvars(value) if isinstance(value, str) else value
    return out


# --------------------------------------------------------------------------
# snapshot / restore  (the no-git rollback)
# --------------------------------------------------------------------------


class Snapshot:
    """Byte-for-byte copy of the scoped files, plus a census of everything else.

    ``missing`` records paths in scope that did not exist yet -- a task may
    legitimately ask for a new file, and rolling that back means deleting it.
    """

    def __init__(self, cwd: Path, rel_paths: Sequence[str], store: Path) -> None:
        self.cwd = cwd
        self.rel_paths = list(rel_paths)
        self.store = store
        self.contents: Dict[str, Optional[bytes]] = {}
        self.census: Dict[str, float] = {}

    def take(self) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        for rel in self.rel_paths:
            src = self.cwd / rel
            if src.exists():
                data = src.read_bytes()
                self.contents[rel] = data
                dst = self.store / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            else:
                self.contents[rel] = None
        self.census = self._census()

    def restore(self) -> List[str]:
        """Put the scoped files back.  Returns what was reverted."""
        reverted: List[str] = []
        for rel, data in self.contents.items():
            target = self.cwd / rel
            if data is None:
                if target.exists():
                    target.unlink()
                    reverted.append(f"deleted {rel}")
                continue
            if not target.exists() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                reverted.append(f"restored {rel}")
        return reverted

    def _census(self) -> Dict[str, float]:
        seen: Dict[str, float] = {}
        for root, dirs, files in os.walk(self.cwd):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
            for name in files:
                path = Path(root) / name
                try:
                    # Forward slashes always: files_scope is written with them,
                    # and os.walk hands back backslashes on Windows -- comparing
                    # the two raw makes every scoped file look off-scope.
                    rel = str(path.relative_to(self.cwd)).replace(os.sep, "/")
                    seen[rel] = path.stat().st_mtime
                except OSError:
                    continue
        return seen

    def off_scope_changes(self) -> Dict[str, List[str]]:
        """Files the worker created/removed/edited that were NOT in ``files_scope``.

        This is calibration data, not a hard gate: a worker that scribbles
        outside its scope is a worker you supervise differently.
        """
        now = self._census()
        scoped = {p.replace("\\", "/") for p in self.rel_paths}
        added = sorted(p for p in now if p not in self.census and p not in scoped)
        removed = sorted(p for p in self.census if p not in now and p not in scoped)
        touched = sorted(
            p
            for p, mtime in now.items()
            if p in self.census and self.census[p] != mtime and p not in scoped
        )
        return {"added": added, "removed": removed, "modified": touched}

    def diff(self) -> str:
        """Unified diff of the scoped files, snapshot -> now."""
        chunks: List[str] = []
        for rel in self.rel_paths:
            before = self.contents.get(rel)
            target = self.cwd / rel
            after = target.read_bytes() if target.exists() else None
            if before == after:
                continue
            before_lines = _decode(before).splitlines(keepends=True) if before is not None else []
            after_lines = _decode(after).splitlines(keepends=True) if after is not None else []
            chunks.extend(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{rel}" if before is not None else "/dev/null",
                    tofile=f"b/{rel}" if after is not None else "/dev/null",
                    n=3,
                )
            )
        return "".join(chunks)


def _decode(data: Optional[bytes]) -> str:
    if data is None:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


def _run(cmd, cwd: Path, timeout: int, env: Dict[str, str]) -> Dict[str, Any]:
    """Run a child process with no console window, capturing merged output."""
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=isinstance(cmd, str),
            **kwargs,
        )
        return {
            "returncode": proc.returncode,
            "output": (proc.stdout or "") + (proc.stderr or ""),
            "duration_s": round(time.time() - started, 1),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        partial = ""
        for stream in (exc.stdout, exc.stderr):
            if stream:
                partial += stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
        return {
            "returncode": 124,
            "output": partial + f"\n[dispatch] TIMEOUT after {timeout}s\n",
            "duration_s": round(time.time() - started, 1),
            "timed_out": True,
        }


def run_aider(
    brief_path: Path, files: Sequence[str], cwd: Path, cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Drive aider headless against the local Ollama model.

    Flag notes (verified against aider 0.86.2 --help, 2026-09-07):
    ``--yes-always`` is the real flag (``--yes`` no longer exists); ``--no-git``
    is required because we roll back by snapshot and the sandboxes we work in
    are not repositories; ``--map-tokens 0`` turns off the repo map, which a 14B
    model spends its whole context on and reasons worse with.
    """
    exe = cfg.get("aider_exe")
    if not exe or not Path(exe).exists():
        raise DispatchError(f"aider not found at {exe!r} -- set aider_exe in worker/config.json")

    cmd = [
        exe,
        "--model", f"ollama_chat/{cfg['model']}",
        "--edit-format", str(cfg.get("aider_edit_format", "whole")),
        "--message-file", str(brief_path),
        "--yes-always",
        "--no-git",
        "--no-auto-commits",
        "--no-check-update",
        "--no-analytics",
        "--no-pretty",
        "--no-stream",
        "--no-detect-urls",
        "--no-show-model-warnings",
        "--map-tokens", "0",
        "--timeout", str(int(cfg.get("worker_timeout_s", 900))),
        # Pin aider's history files into the run directory. Left to itself aider
        # drops .aider.chat.history.md next to the *git root it detects* -- which
        # it still detects with --no-git -- so the first calibration run wrote its
        # transcript into the top of the Forge repo. Pinned here it stops being
        # litter and starts being part of the run log.
        "--chat-history-file", str(brief_path.parent / "aider.chat.history.md"),
        "--input-history-file", str(brief_path.parent / "aider.input.history"),
    ]
    cmd.extend(files)

    env = dict(os.environ)
    env["OLLAMA_API_BASE"] = cfg["ollama_url"]
    env["OLLAMA_CONTEXT_LENGTH"] = str(cfg.get("num_ctx", 16384))
    env["AIDER_ANALYTICS_DISABLE"] = "1"
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    result = _run(cmd, cwd, int(cfg.get("worker_timeout_s", 900)), env)
    result["backend"] = "aider"
    result["command"] = cmd
    return result


_FILE_BLOCK = re.compile(
    r"^[ \t]*(?:#+\s*)?(?:FILE|file)\s*:\s*(?P<path>[^\n`]+?)\s*$\s*```[a-zA-Z0-9_+-]*\s*\n"
    r"(?P<body>.*?)(?:^```\s*$)",
    re.MULTILINE | re.DOTALL,
)

_NATIVE_SYSTEM = (
    "You are a careful Python engineer working inside an existing codebase. "
    "You make the smallest change that satisfies the task. You never explain, "
    "apologise, or add commentary outside the required format."
)

_NATIVE_FORMAT = """\
Reply with the COMPLETE new contents of every file you changed, and nothing else.
Use exactly this format, once per file:

FILE: <relative/path/as/given/to/you>
```python
<the entire file, top to bottom, with your change applied>
```

Rules:
- Output whole files, never fragments, never diffs, never "... unchanged ...".
- Only files from the list above. Do not invent new paths.
- If a file does not need to change, leave it out entirely.
- No prose before the first FILE: line and none after the last closing fence.
"""


def run_native(
    brief_text: str, files: Sequence[str], cwd: Path, cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Fallback worker: one /api/chat round-trip, whole-file replies, applied here.

    Whole-file replacement rather than unified diffs on purpose -- a 14B model
    emits hunk headers with the wrong line numbers often enough that a patch
    applier becomes the thing that fails, and the failure looks like a model
    failure when it is a format failure.  The file is short; make it rewrite it.
    """
    parts = [brief_text, "", "## Files you may edit", ""]
    for rel in files:
        path = cwd / rel
        body = path.read_text(encoding="utf-8") if path.exists() else ""
        state = "" if path.exists() else "  (does not exist yet -- create it)"
        parts.append(f"FILE: {rel}{state}")
        parts.append("```python")
        parts.append(body.rstrip("\n"))
        parts.append("```")
        parts.append("")
    parts.append("## Required reply format")
    parts.append("")
    parts.append(_NATIVE_FORMAT)
    prompt = "\n".join(parts)

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _NATIVE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "keep_alive": cfg.get("keep_alive", "5m"),
        "options": {
            "num_ctx": int(cfg.get("num_ctx", 16384)),
            "temperature": float(cfg.get("temperature", 0.1)),
        },
    }

    started = time.time()
    request = urllib.request.Request(
        cfg["ollama_url"].rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(cfg.get("worker_timeout_s", 900))) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "backend": "native",
            "returncode": 1,
            "output": f"[dispatch] ollama call failed: {exc}",
            "duration_s": round(time.time() - started, 1),
            "timed_out": isinstance(exc, TimeoutError),
            "prompt": prompt,
        }

    reply = (body.get("message") or {}).get("content", "")
    applied, skipped = _apply_blocks(reply, files, cwd)
    log = [
        f"[dispatch] native backend, model={cfg['model']}",
        f"[dispatch] eval_count={body.get('eval_count')} "
        f"prompt_eval_count={body.get('prompt_eval_count')}",
        f"[dispatch] applied={applied} skipped={skipped}",
        "",
        "--- model reply ---",
        reply,
    ]
    return {
        "backend": "native",
        "returncode": 0 if applied else 1,
        "output": "\n".join(log),
        "duration_s": round(time.time() - started, 1),
        "timed_out": False,
        "applied": applied,
        "skipped": skipped,
        "prompt": prompt,
    }


def _apply_blocks(reply: str, files: Sequence[str], cwd: Path):
    """Write out every ``FILE: <path>`` fenced block that names a scoped file."""
    wanted = {}
    for rel in files:
        wanted[rel.replace("\\", "/").lstrip("./").lower()] = rel
    applied: List[str] = []
    skipped: List[str] = []
    for match in _FILE_BLOCK.finditer(reply):
        raw = match.group("path").strip().strip("`").strip()
        key = raw.replace("\\", "/").lstrip("./").lower()
        rel = wanted.get(key)
        if rel is None:
            # tolerate a bare basename when it is unambiguous
            matches = [v for k, v in wanted.items() if k.endswith("/" + key) or k == key]
            rel = matches[0] if len(matches) == 1 else None
        if rel is None:
            skipped.append(raw)
            continue
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = match.group("body")
        if not text.endswith("\n"):
            text += "\n"
        target.write_text(text, encoding="utf-8")
        applied.append(rel)
    return applied, skipped


# --------------------------------------------------------------------------
# the dispatch itself
# --------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _rmtree_resilient(path: Path, attempts: int = 5) -> None:
    """``shutil.rmtree`` that survives a sync client holding the directory.

    ``worker/runs/`` lives in the repo, which lives in OneDrive.  Measured twice
    during the first calibration: OneDrive takes a transient handle on files
    that were just written, and a plain ``rmtree`` of the previous run dies with
    ``WinError 5`` a second before the same call would have worked.  Losing a
    whole dispatch to that is absurd, so: clear read-only bits, retry with a
    short backoff, and if it still will not go, move the old run aside rather
    than refuse to start the new one.
    """
    if not path.exists():
        return
    for index in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            for child in path.rglob("*"):
                try:
                    child.chmod(0o700)
                except OSError:
                    pass
            time.sleep(0.4 * (index + 1))
    aside = path.with_name(f"{path.name}.stale-{int(time.time())}")
    try:
        path.rename(aside)
    except OSError as exc:  # pragma: no cover - the filesystem is genuinely stuck
        raise DispatchError(
            f"could not clear the previous run directory {path}: {exc}. "
            "Close anything holding it (an editor, a file explorer) and retry."
        ) from exc


def dispatch(task: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run *task* on a local worker until the gate passes or the attempts run out.

    Returns the report dict (also at ``runs/<id>/result.json``).  The tree is
    left changed ONLY on a pass; every failure path restores the snapshot.
    """
    cfg = dict(load_config())
    if config:
        cfg.update(config)
    for key in (
        "model", "backend", "num_ctx", "temperature", "aider_exe",
        "aider_edit_format", "worker_timeout_s", "gate_timeout_s", "ollama_url",
    ):
        if key in task:
            cfg[key] = task[key]

    task_id = str(task.get("id") or f"task-{int(time.time())}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise DispatchError(f"task id {task_id!r} must be a plain filename-safe token")

    brief = task.get("brief")
    if not brief or not str(brief).strip():
        raise DispatchError("task.brief is required")
    files_scope = list(task.get("files_scope") or [])
    if not files_scope:
        raise DispatchError("task.files_scope is required -- a worker with no scope has no rollback")
    acceptance_cmd = task.get("acceptance_cmd")
    if not acceptance_cmd:
        raise DispatchError("task.acceptance_cmd is required -- the gate IS the contract")

    cwd = Path(task.get("cwd") or REPO_ROOT).resolve()
    if not cwd.is_dir():
        raise DispatchError(f"task.cwd does not exist: {cwd}")
    for rel in files_scope:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise DispatchError(f"files_scope entries must be relative and inside cwd: {rel!r}")

    max_attempts = int(task.get("max_attempts") or cfg.get("max_attempts") or 2)

    run_dir = RUNS_DIR / task_id
    _rmtree_resilient(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    resolved = {
        "id": task_id,
        "cwd": str(cwd),
        "files_scope": files_scope,
        "acceptance_cmd": acceptance_cmd,
        "max_attempts": max_attempts,
        "backend": cfg.get("backend", "aider"),
        "model": cfg.get("model"),
        "num_ctx": cfg.get("num_ctx"),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write(run_dir / "task.json", json.dumps(resolved, indent=2))
    _write(run_dir / "brief.md", str(brief))
    _write(run_dir / "status.txt", "RUNNING\n")

    snapshot = Snapshot(cwd, files_scope, run_dir / "snapshot")
    snapshot.take()

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    wall_started = time.time()

    # Precondition: the gate must be RED before the worker starts.  A gate that
    # is already green is not measuring the task, and every downstream signal --
    # "passed on attempt 1", the diff, the wall time -- is then meaningless.
    # Cheaper to find out now than to read a run report that says nothing.
    if task.get("require_red_gate", True):
        precheck = _run(acceptance_cmd, cwd, int(cfg.get("gate_timeout_s", 300)), env)
        _write(run_dir / "gate-precheck.log", precheck.get("output", ""))
        if precheck.get("returncode") == 0:
            report = {
                "id": task_id,
                "status": "GATE_ALREADY_GREEN",
                "attempts": [],
                "attempts_used": 0,
                "wall_seconds": round(time.time() - wall_started, 1),
                "acceptance_cmd": acceptance_cmd,
                "cwd": str(cwd),
                "run_dir": str(run_dir),
                "escalate": True,
                "escalation_note": (
                    "The acceptance command already passes with no work done, so it "
                    "does not test this task. Fix the gate (or rebuild the sandbox -- "
                    "a previous PASS leaves its changes in place) before dispatching. "
                    "Set require_red_gate:false only if you genuinely mean to allow it."
                ),
            }
            _write(run_dir / "result.json", json.dumps(report, indent=2))
            _write(run_dir / "status.txt", report["status"] + "\n")
            return report

    attempts: List[Dict[str, Any]] = []
    failure_note = ""
    passed = False

    for attempt_no in range(1, max_attempts + 1):
        adir = run_dir / f"attempt-{attempt_no}"
        adir.mkdir(parents=True, exist_ok=True)

        attempt_brief = str(brief)
        if failure_note:
            attempt_brief += (
                "\n\n---\n\n## Your previous attempt FAILED the acceptance check\n\n"
                "The command that judges your work is:\n\n"
                f"```\n{acceptance_cmd}\n```\n\n"
                "It exited non-zero. Here is its output -- fix the actual cause, "
                "do not guess:\n\n```\n"
                + failure_note.strip()[-6000:]
                + "\n```\n"
            )
        _write(adir / "brief.md", attempt_brief)

        if cfg.get("backend", "aider") == "aider":
            worker_result = run_aider(adir / "brief.md", files_scope, cwd, cfg)
        else:
            worker_result = run_native(attempt_brief, files_scope, cwd, cfg)
            if worker_result.get("prompt"):
                _write(adir / "prompt.txt", worker_result.pop("prompt"))
        _write(adir / "worker.log", worker_result.get("output", ""))

        diff_text = snapshot.diff()
        _write(adir / "changes.diff", diff_text or "(no change to any scoped file)\n")
        off_scope = snapshot.off_scope_changes()
        if any(off_scope.values()):
            _write(adir / "off_scope.json", json.dumps(off_scope, indent=2))

        gate = _run(acceptance_cmd, cwd, int(cfg.get("gate_timeout_s", 300)), env)
        _write(adir / "gate.log", gate.get("output", ""))

        record = {
            "attempt": attempt_no,
            "worker_seconds": worker_result.get("duration_s"),
            "worker_returncode": worker_result.get("returncode"),
            "worker_timed_out": worker_result.get("timed_out"),
            "changed_files": _changed_files(snapshot),
            "diff_lines": diff_text.count("\n"),
            "off_scope": off_scope,
            "gate_returncode": gate.get("returncode"),
            "gate_seconds": gate.get("duration_s"),
            "passed": gate.get("returncode") == 0,
        }
        if cfg.get("backend") == "native":
            record["applied"] = worker_result.get("applied")
            record["skipped"] = worker_result.get("skipped")
        _write(adir / "result.json", json.dumps(record, indent=2))
        attempts.append(record)

        if record["passed"] and diff_text.strip():
            passed = True
            break
        if record["passed"] and not diff_text.strip():
            # The gate passed but nothing changed: the gate is not measuring the
            # task.  Say so loudly rather than banking a free "win".
            record["passed"] = False
            record["note"] = "gate passed with an empty diff -- the gate does not test the task"
            _write(adir / "result.json", json.dumps(record, indent=2))

        failure_note = gate.get("output", "") or worker_result.get("output", "")
        reverted = snapshot.restore()
        _write(adir / "rollback.txt", "\n".join(reverted) or "(nothing to revert)\n")

    report = {
        "id": task_id,
        "status": "PASS" if passed else "FAILED",
        "backend": cfg.get("backend"),
        "model": cfg.get("model"),
        "attempts": attempts,
        "attempts_used": len(attempts),
        "max_attempts": max_attempts,
        "wall_seconds": round(time.time() - wall_started, 1),
        "cwd": str(cwd),
        "files_scope": files_scope,
        "acceptance_cmd": acceptance_cmd,
        "run_dir": str(run_dir),
        "escalate": not passed,
        "escalation_note": (
            "" if passed else
            "Two strikes: hand this to a Claude agent. Read runs/%s/attempt-*/gate.log "
            "and changes.diff first -- the local worker's near-misses usually name the "
            "part of the brief that was underspecified." % task_id
        ),
    }
    _write(run_dir / "result.json", json.dumps(report, indent=2))
    _write(run_dir / "status.txt", report["status"] + "\n")
    return report


def _changed_files(snapshot: Snapshot) -> List[str]:
    changed = []
    for rel, before in snapshot.contents.items():
        target = snapshot.cwd / rel
        after = target.read_bytes() if target.exists() else None
        if after != before:
            changed.append(rel)
    return changed


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a task on the local worker tier.")
    parser.add_argument("task", help="path to a task .json file")
    parser.add_argument("--backend", choices=["aider", "native"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-attempts", type=int, default=None)
    args = parser.parse_args(argv)

    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    if task.get("brief_file"):
        base = Path(args.task).resolve().parent
        task["brief"] = (base / task["brief_file"]).read_text(encoding="utf-8")
    if args.backend:
        task["backend"] = args.backend
    if args.model:
        task["model"] = args.model
    if args.max_attempts:
        task["max_attempts"] = args.max_attempts

    report = dispatch(task)
    print(json.dumps(
        {k: v for k, v in report.items() if k != "attempts"}, indent=2
    ))
    for record in report["attempts"]:
        print(
            f"  attempt {record['attempt']}: gate rc={record['gate_returncode']} "
            f"worker {record['worker_seconds']}s  changed={record['changed_files']}"
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
