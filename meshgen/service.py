"""The meshgen service: one stable image-to-3D API in front of swappable models.

    GET  /health           backend name/model/licence/loaded + available_backends
                           + "build": {"sha", "pid", "started", "uptime_s"}
    POST /generate3d       {"image_path", "backend"?, "options"?, "output"?} -> {"job_id"}
    GET  /job/<id>         queued | running | done | error | cancelled (+ progress)
    POST /cancel/<id>      best-effort interrupt

127.0.0.1 only, stdlib only, port 8902.  The heavyweight half (ComfyUI, torch,
~15 GB of weights) lives out of the repo under C:\\forge-models and is started
lazily as a child process the first time a job actually needs it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import backends as backend_registry
from . import config as config_module
from . import jobs as jobs_module
from .backends import multiview
from .backends.base import BackendError, Cancelled, NotReady
from .comfyui_client import ComfyUIClient

SERVICE_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# version truth (see docs/architecture.md, "What is running")
# ---------------------------------------------------------------------------
#
# The same three facts under the same three key names as the bridge and the
# geometry service: the git short-SHA of the tree this process STARTED from, its
# pid, and when.  Read once at import, never lazily -- a checkout made after the
# process started must not let a stale service relabel itself as current.
#
# meshgen is the service most likely to go stale: it is the slowest to restart
# (ComfyUI cold start) and therefore the one an artist is most tempted to leave
# running across a pull.

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _git_short_sha(root, timeout=5.0):
    """``git rev-parse --short HEAD``, or ``"unknown"``.  Never fails a startup."""
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
    return (out or b"").decode("utf-8", "replace").strip() or "unknown"


BUILD_SHA = _git_short_sha(_REPO_ROOT)
BUILD_STARTED_AT = time.time()


def build_info():
    """The ``build`` block on ``/health``.  Identical keys in all three services."""
    return {
        "sha": BUILD_SHA,
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(BUILD_STARTED_AT)),
        "uptime_s": round(max(0.0, time.time() - BUILD_STARTED_AT), 3),
    }


def log(*parts):
    print("[meshgen]", *parts, flush=True)


class MeshgenApp:
    """Everything the HTTP layer needs, with no HTTP in it."""

    def __init__(self, config):
        self.config = config
        self.client = ComfyUIClient(config, log=lambda m: log(m))
        self.backends = backend_registry.build(config, self.client)
        self.store = jobs_module.JobStore()
        self.queue = queue.Queue()
        self.current = None
        self._lock = threading.RLock()
        self.worker = threading.Thread(target=self._run_worker, daemon=True, name="meshgen-worker")
        self.worker.start()

    # -- backends ---------------------------------------------------------
    @property
    def default_backend_name(self):
        return self.config.default_backend

    def resolve_backend(self, name):
        name = name or self.default_backend_name
        backend = self.backends.get(name)
        if backend is None:
            raise KeyError(name)
        return backend

    def health(self):
        default_name = self.default_backend_name
        default = self.backends.get(default_name)
        available = []
        for backend in self.backends.values():
            try:
                available.append(backend.info())
            except Exception as exc:  # an adapter must never break /health
                available.append({"name": backend.name, "error": str(exc)})

        payload = {
            "status": "ok",
            "service": "meshgen",
            "version": SERVICE_VERSION,
            "build": build_info(),
            "port": self.config.port,
            "config_file": str(self.config.source) if self.config.source else None,
            "models_root": str(self.config.models_dir),
            "comfyui_root": str(self.config.comfyui_dir),
            "comfyui_running": self.client.is_running(),
            "available_backends": available,
            "jobs": {
                "active": len(self.store.active()),
                "queued": self.queue.qsize(),
            },
        }
        if default is None:
            payload["status"] = "misconfigured"
            payload["backend"] = None
            payload["error"] = (
                f"FORGE_MESHGEN_BACKEND/default_backend is {default_name!r}, which is not "
                f"one of {sorted(self.backends)}"
            )
            return payload

        info = default.info()
        payload["backend"] = {
            "name": info["name"],
            "model": info["model"],
            "license": info["license"],
            "loaded": info["loaded"],
            "vram_gb": info["vram_gb"],
            "ready": info["ready"],
        }
        if not info["ready"]:
            payload["status"] = "models_missing"
            payload["missing"] = info["missing"]
            payload["hint"] = (
                "Each entry says what is missing, the exact path meshgen looks at, and the "
                "URL to fetch it from. Paths come from meshgen/config.json - edit that one "
                "file to point at a different model store."
            )
        return payload

    # -- jobs -------------------------------------------------------------
    def _resolve_views(self, backend, image_path, options, views):
        """Validate a multi-view request and settle which image leads it.

        Returns ``(image_path, options)``.  Everything here happens BEFORE a job
        is queued, so an unsatisfiable ``views`` block is an immediate 400 with
        the missing file named - not a job that fails minutes later.
        """
        if not getattr(backend, "supports_multiview", False):
            capable = sorted(
                name for name, other in self.backends.items()
                if getattr(other, "supports_multiview", False)
            )
            raise multiview.MultiviewError(
                f"backend {backend.name!r} does not accept views. "
                + (f"Backends that do: {', '.join(capable)}."
                   if capable else "No installed backend does.")
            )

        records = multiview.normalise_views(views)
        front = records[0]["path"]
        if image_path and Path(image_path) != Path(front):
            raise multiview.MultiviewError(
                "image_path and views.front disagree - give one or the other.\n"
                f"  image_path:  {image_path}\n"
                f"  views.front: {front}"
            )

        mode = options.get("on_unavailable", "error")
        if mode not in ("error", "front_only"):
            raise multiview.MultiviewError(
                f"on_unavailable must be 'error' or 'front_only', got {mode!r}"
            )

        options["views"] = records
        state = backend.multiview_readiness()
        if not state["available"] and mode != "front_only":
            raise multiview.MultiviewUnavailable(
                multiview.unavailable_message(state["missing"]), state["missing"]
            )
        return front, options

    def submit(self, image_path, backend_name, options, output, views=None):
        backend = self.resolve_backend(backend_name)
        options = dict(options or {})
        if views is None:
            views = options.get("views")

        if views:
            image_path, options = self._resolve_views(
                backend, image_path, options, views)
        elif not image_path:
            raise ValueError("image_path is required (or a views block)")

        if "ensemble" in options:
            # Settled here rather than minutes later inside the job, for the same
            # reason a views block is: a malformed request should cost nothing.
            resolve = getattr(backend, "resolved_ensemble", None)
            if resolve is None:
                raise ValueError(
                    f"backend {backend.name!r} has no seed ensemble; drop "
                    '"ensemble" from options')
            try:
                # normalised in place, so the job record and the 202 show what
                # will actually run rather than the shorthand that was sent
                options["ensemble"] = resolve(options)
            except BackendError as exc:
                raise ValueError(str(exc)) from None

        image = Path(image_path)
        if not image.is_absolute():
            raise ValueError("image_path must be absolute")
        if not image.is_file():
            raise FileNotFoundError(str(image))

        if output:
            out_path = Path(output)
            if not out_path.is_absolute():
                raise ValueError("output must be absolute")
            if out_path.suffix.lower() not in (".glb", ".gltf"):
                raise ValueError("output must end in .glb (the backends produce binary glTF)")
        else:
            out_path = image.with_name(image.stem + f"_{backend.name}.glb")

        job = self.store.create(str(image), backend.name, options, str(out_path))
        self.queue.put(job.id)
        log(f"queued job {job.id} backend={backend.name} image={image.name}")
        return job

    def cancel(self, job_id):
        job = self.store.get(job_id)
        if job is None:
            return None
        if job.state in (jobs_module.DONE, jobs_module.ERROR, jobs_module.CANCELLED):
            return {"job_id": job_id, "cancelled": False, "state": job.state,
                    "detail": "job had already finished"}
        job.cancel_event.set()
        interrupted = False
        with self._lock:
            if self.current is not None and self.current.id == job_id:
                backend = self.backends.get(job.backend)
                if backend is not None:
                    interrupted = bool(backend.cancel(job_id))
        if job.state == jobs_module.QUEUED:
            # never started, so the worker will skip it - close the record now
            job.state = jobs_module.CANCELLED
            job.finished = time.time()
        return {"job_id": job_id, "cancelled": True, "state": job.state,
                "interrupted": interrupted}

    # -- worker -----------------------------------------------------------
    def _run_worker(self):
        while True:
            job_id = self.queue.get()
            job = self.store.get(job_id)
            if job is None:
                continue
            if job.cancel_event.is_set() or job.state == jobs_module.CANCELLED:
                job.state = jobs_module.CANCELLED
                continue
            self._run_job(job)

    def _run_job(self, job):
        with self._lock:
            self.current = job
        job.state = jobs_module.RUNNING
        job.started = time.time()

        def progress(fraction, label):
            if fraction is not None:
                job.progress = round(float(fraction), 4)
            if label:
                job.stage = str(label)[:80]

        try:
            backend = self.resolve_backend(job.backend)
            result = backend.generate(
                job.image_path,
                job.options,
                job.output,
                progress=progress,
                cancel_event=job.cancel_event,
                job_id=job.id,
            )
            job.result = result
            job.state = jobs_module.DONE
            job.progress = 1.0
            job.stage = None
            log(f"job {job.id} done -> {result.get('mesh_path')} "
                f"({result.get('duration_ms')} ms)")
        except Cancelled as exc:
            job.state = jobs_module.CANCELLED
            job.error = str(exc)
            log(f"job {job.id} cancelled")
        except NotReady as exc:
            job.state = jobs_module.ERROR
            job.error = str(exc)
            job.result = {"missing": exc.missing}
            log(f"job {job.id} not ready: {exc}")
        except BackendError as exc:
            job.state = jobs_module.ERROR
            job.error = str(exc)
            log(f"job {job.id} failed: {exc}")
        except Exception as exc:
            job.state = jobs_module.ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            log(f"job {job.id} crashed:\n{traceback.format_exc()}")
        finally:
            job.finished = time.time()
            with self._lock:
                self.current = None

    def shutdown(self):
        self.client.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = f"forge-meshgen/{SERVICE_VERSION}"
    app: MeshgenApp = None

    def log_message(self, fmt, *args):
        pass  # the app logs what matters; the default access log is noise

    # -- plumbing ---------------------------------------------------------
    def _send(self, code, payload):
        body = json.dumps(payload, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ValueError(f"body is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/"):
            return self._send(200, self.app.health())
        if path == "/jobs":
            return self._send(200, {"jobs": [j.as_dict() for j in self.app.store.all()]})
        if path.startswith("/job/"):
            job = self.app.store.get(path[len("/job/"):])
            if job is None:
                return self._send(404, {"error": "no such job"})
            return self._send(200, job.as_dict())
        return self._send(404, {"error": f"no route for GET {path}"})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})

        if path == "/generate3d":
            options = body.get("options") or {}
            if not isinstance(options, dict):
                return self._send(400, {"error": "options must be an object"})
            views = body.get("views")
            if views is None:
                views = options.get("views")
            image_path = body.get("image_path")
            if not image_path and not views:
                return self._send(400, {"error": "image_path is required"})
            try:
                job = self.app.submit(image_path, body.get("backend"), options,
                                      body.get("output"), views=views)
            except KeyError as exc:
                return self._send(400, {
                    "error": f"unknown backend {exc.args[0]!r}",
                    "available_backends": sorted(self.app.backends),
                })
            except multiview.MultiviewUnavailable as exc:
                # Well-formed request, unrunnable machine: name the file, the
                # size and the URL rather than a bare "unsupported".
                return self._send(400, {
                    "error": str(exc),
                    "multiview": {"available": False, "missing": exc.missing},
                })
            except FileNotFoundError as exc:
                return self._send(400, {"error": f"image not found: {exc}"})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            payload = {"job_id": job.id, "state": job.state,
                       "backend": job.backend, "output": job.output}
            if job.options.get("views"):
                payload["views"] = [v["name"] for v in job.options["views"]]
            if job.options.get("ensemble"):
                payload["ensemble"] = job.options["ensemble"]
            return self._send(202, payload)

        if path.startswith("/cancel/"):
            result = self.app.cancel(path[len("/cancel/"):])
            if result is None:
                return self._send(404, {"error": "no such job"})
            return self._send(200, result)

        return self._send(404, {"error": f"no route for POST {path}"})


def build_server(config=None):
    config = config or config_module.load()
    app = MeshgenApp(config)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", config.port), handler)
    server.daemon_threads = True
    return server, app


def main(argv=None):
    config = config_module.load()
    try:
        server, app = build_server(config)
    except OSError as exc:
        log(f"could not bind 127.0.0.1:{config.port}: {exc}")
        return 1
    log(f"listening on http://127.0.0.1:{config.port}  "
        f"(default backend: {config.default_backend})")
    log(f"models: {config.models_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("stopping")
    finally:
        server.shutdown()
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
