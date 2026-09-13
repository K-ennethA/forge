"""Headless ComfyUI as a child process, plus the bits of its HTTP API we use.

Both adapters share this.  Responsibilities:

* start ComfyUI lazily - nothing spawns until the first job actually needs it,
  and it starts with CREATE_NO_WINDOW so no console flashes at the artist;
* submit an API-format prompt graph (we hand ComfyUI our own job UUID as its
  ``prompt_id`` so one id addresses both services);
* follow progress - real per-step fractions come off ComfyUI's websocket, with
  the HTTP job API as the authority on final state;
* retrieve the produced .glb out of the ComfyUI output tree into wherever the
  caller asked for it;
* interrupt a running job and shut the child down again.

Stdlib only.  Nothing here binds a port; ComfyUI listens on 127.0.0.1 only.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .backends.base import BackendError, Cancelled, NotReady

# Windows: keep the child completely windowless.
CREATE_NO_WINDOW = 0x08000000


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ComfyUIClient:
    """One per service process.  Thread-safe for the single-job-at-a-time use."""

    def __init__(self, config, log=None):
        self.config = config
        self.log = log or (lambda *a: None)
        self.client_id = uuid.uuid4().hex
        self._proc = None
        self._lock = threading.RLock()
        self._started_by_us = False
        self._log_path = Path(config.comfyui_output_dir).parent / "comfyui-server.log"

    # -- installation -----------------------------------------------------
    def runtime_missing(self) -> list:
        """Missing pieces of the ComfyUI install itself (not the weights)."""
        missing = []
        main = self.config.comfyui_main
        python = self.config.comfyui_python_path
        if not main.is_file():
            missing.append({
                "what": "ComfyUI checkout",
                "path": str(main),
                # v0.35.0+ is required for multi-view (Pixal3DMultiViewConditioning);
                # single-view works from v0.34.0.
                "source": "git clone --branch v0.35.1 https://github.com/Comfy-Org/ComfyUI.git",
            })
        if not python.is_file():
            missing.append({
                "what": "ComfyUI virtualenv interpreter",
                "path": str(python),
                "source": (
                    "python -m venv .venv in the ComfyUI checkout, then "
                    "pip install torch torchvision torchaudio "
                    "--extra-index-url https://download.pytorch.org/whl/cu130 "
                    "and pip install -r requirements.txt"
                ),
            })
        return missing

    # -- process ----------------------------------------------------------
    def is_running(self) -> bool:
        return port_is_open(self.config.comfyui_port)

    def owns_child(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_running(self, cancel_event=None):
        """Start ComfyUI if the port is cold.  Idempotent and cheap when warm."""
        with self._lock:
            if self.is_running():
                return
            missing = self.runtime_missing()
            if missing:
                raise NotReady("ComfyUI is not installed", missing)

            out_dir = self.config.output_dir
            in_dir = self.config.input_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            in_dir.mkdir(parents=True, exist_ok=True)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                str(self.config.comfyui_python_path),
                str(self.config.comfyui_main),
                "--listen", "127.0.0.1",
                "--port", str(self.config.comfyui_port),
                "--disable-auto-launch",
                "--output-directory", str(out_dir),
                "--input-directory", str(in_dir),
            ]
            self.log(f"starting ComfyUI: {' '.join(cmd)}")

            flags = CREATE_NO_WINDOW if os.name == "nt" else 0
            logfile = open(self._log_path, "ab", buffering=0)
            logfile.write(f"\n=== meshgen start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.config.comfyui_dir),
                stdout=logfile,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
            )
            self._started_by_us = True

            deadline = time.time() + self.config.comfyui_startup_timeout_s
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled("cancelled while ComfyUI was starting")
                if self._proc.poll() is not None:
                    raise BackendError(
                        f"ComfyUI exited during startup (code {self._proc.returncode}). "
                        f"See {self._log_path}"
                    )
                if self.is_running():
                    self.log("ComfyUI is up")
                    return
                time.sleep(1.0)
            raise BackendError(
                f"ComfyUI did not answer on port {self.config.comfyui_port} within "
                f"{self.config.comfyui_startup_timeout_s}s. See {self._log_path}"
            )

    def stop(self, timeout: float = 20.0):
        """Stop the child we started.  A ComfyUI we merely found is left alone.

        ComfyUI **re-execs itself** on Windows (main.py relaunches with the CUDA
        allocator env set), so the process we spawned is only a launcher and the
        grandchild is what actually holds port 8188.  Terminating the parent
        alone leaves that grandchild running and the port occupied - hence the
        tree kill.  Verified on this machine: our pid 7452 spawned 17256, and
        17256 was the listener.
        """
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._proc = None
                # the launcher may already have exited while its re-exec lives on
                if self.is_running():
                    self._kill_tree(getattr(proc, "pid", None), timeout)
                return False

            self.log("stopping ComfyUI child (and its re-exec)")
            self._kill_tree(proc.pid, timeout)
            try:
                proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            self._proc = None
            return True

    def _kill_tree(self, pid, timeout=20.0):
        """Kill a process and everything it spawned, then wait for the port."""
        if pid is not None and os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        elif pid is not None:
            try:
                os.kill(pid, 15)
            except OSError:
                pass

        deadline = time.time() + min(timeout, 15)
        while time.time() < deadline:
            if not self.is_running():
                return True
            time.sleep(0.5)
        if self.is_running():
            self.log(f"WARNING: port {self.config.comfyui_port} is still held after stopping")
            return False
        return True

    # -- HTTP -------------------------------------------------------------
    def _request(self, path, data=None, method=None, timeout=30):
        url = self.config.comfyui_url(path)
        body = None
        headers = {}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return raw

    def system_stats(self):
        return self._request("/system_stats")

    def object_info(self, node_class=None):
        path = "/object_info" + (f"/{node_class}" if node_class else "")
        return self._request(path)

    def vram_report(self):
        """Best-effort VRAM numbers straight from ComfyUI's own device report."""
        try:
            stats = self.system_stats() or {}
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        devices = stats.get("devices") or []
        if not devices:
            return None
        dev = devices[0]
        total = dev.get("vram_total")
        free = dev.get("vram_free")
        out = {"name": dev.get("name"), "type": dev.get("type")}
        if isinstance(total, (int, float)):
            out["vram_total_gb"] = round(total / (1024 ** 3), 2)
        if isinstance(total, (int, float)) and isinstance(free, (int, float)):
            out["vram_used_gb"] = round((total - free) / (1024 ** 3), 2)
        return out

    # -- input images -----------------------------------------------------
    def stage_image(self, image_path) -> str:
        """Copy the artist's image into ComfyUI's input dir; return the name.

        LoadImage resolves names against the input directory, so the graph
        never carries an absolute path and the source file is never touched.
        """
        src = Path(image_path)
        if not src.is_file():
            raise BackendError(f"image not found: {src}")
        in_dir = self.config.input_dir
        in_dir.mkdir(parents=True, exist_ok=True)
        name = f"forge_{uuid.uuid4().hex[:12]}{src.suffix.lower() or '.png'}"
        shutil.copy2(src, in_dir / name)
        return name

    def unstage_image(self, name):
        """Drop a staged copy once the job is done, so the input dir stays small."""
        if not name:
            return
        try:
            (self.config.input_dir / name).unlink(missing_ok=True)
        except OSError:
            pass

    # -- prompts ----------------------------------------------------------
    def submit(self, graph: dict, prompt_id: str) -> str:
        payload = {
            "prompt": graph,
            "client_id": self.client_id,
            "prompt_id": prompt_id,
            "extra_data": {"extra_pnginfo": {"forge": {"source": "meshgen"}}},
        }
        try:
            result = self._request("/prompt", payload, method="POST", timeout=120)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:4000]
            raise BackendError(f"ComfyUI rejected the workflow ({exc.code}): {detail}") from None
        returned = (result or {}).get("prompt_id", prompt_id)
        node_errors = (result or {}).get("node_errors") or {}
        if node_errors:
            raise BackendError(f"ComfyUI reported node errors: {json.dumps(node_errors)[:2000]}")
        return returned

    def job_state(self, prompt_id: str):
        try:
            return self._request(f"/api/jobs/{prompt_id}", timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def history(self, prompt_id: str):
        data = self._request(f"/history/{prompt_id}", timeout=60) or {}
        return data.get(prompt_id)

    def interrupt(self):
        try:
            self._request("/interrupt", data={}, method="POST", timeout=15)
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def cancel_prompt(self, prompt_id: str) -> bool:
        """Cancel whether the job is queued or running."""
        ok = False
        try:
            self._request(f"/api/jobs/{prompt_id}/cancel", data={}, method="POST", timeout=20)
            ok = True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            pass
        # /interrupt is the one that actually stops a sampler mid-step.
        if self.interrupt():
            ok = True
        return ok

    def free_memory(self):
        try:
            self._request("/free", data={"unload_models": True, "free_memory": True},
                          method="POST", timeout=60)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return False

    # -- progress ---------------------------------------------------------
    @staticmethod
    def node_labels(graph: dict) -> dict:
        """``{node_id: readable name}`` so progress reads as a stage, not a number.

        The pipeline runs four separate KSamplers (structure, shape, detail,
        texture), all titled "KSampler" in the template - so a bare title makes
        a moving job look frozen.  Duplicated names get their node id appended.
        """
        raw = {}
        for node_id, node in (graph or {}).items():
            meta = node.get("_meta") or {}
            raw[str(node_id)] = meta.get("title") or node.get("class_type") or str(node_id)

        counts = {}
        for name in raw.values():
            counts[name] = counts.get(name, 0) + 1
        return {
            node_id: (f"{name} #{node_id}" if counts[name] > 1 else name)
            for node_id, name in raw.items()
        }

    def watch_progress(self, prompt_id, progress, stop_event, labels=None):
        """Background thread body: relay ComfyUI websocket progress to ``progress``.

        Entirely best-effort - if the socket refuses or dies, the job carries on
        with state-only progress.
        """
        from .wsclient import WebSocket, WebSocketError

        labels = labels or {}

        path = f"/ws?clientId={urllib.parse.quote(self.client_id)}"
        try:
            ws = WebSocket("127.0.0.1", self.config.comfyui_port, path, timeout=5.0).connect()
        except (OSError, WebSocketError):
            return
        try:
            while not stop_event.is_set():
                try:
                    message = ws.recv_json()
                except socket.timeout:
                    continue
                except (OSError, WebSocketError):
                    return
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                data = message.get("data") or {}
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if kind == "progress":
                    value, maximum = data.get("value"), data.get("max")
                    node = data.get("node")
                    label = labels.get(str(node), str(node)) if node is not None else None
                    if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum:
                        progress(max(0.0, min(1.0, value / maximum)), label)
                elif kind == "executing":
                    node = data.get("node")
                    if node is not None:
                        progress(None, labels.get(str(node), str(node)))
                elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                    return
        finally:
            ws.close()

    # -- running a graph end to end ---------------------------------------
    def run_graph(self, graph, prompt_id, cancel_event=None, progress=None,
                  timeout_s=None, telemetry=None):
        """Submit, wait, and return the finished history entry.

        ``telemetry``, if given, is filled in with ``peak_vram_gb`` sampled off
        ComfyUI's own device report while the job runs - the before/after
        snapshots miss the peak, which is the number that decides whether a
        setting fits on a 12 GB card.

        Raises :class:`Cancelled` if ``cancel_event`` fires, :class:`BackendError`
        on execution failure or timeout.
        """
        timeout_s = timeout_s or self.config.job_timeout_s
        progress = progress or (lambda *a: None)
        # Every poll below addresses the job by the id WE hold, so a missing one
        # means polling /api/jobs/None forever on a job that has already
        # finished - the run fails on the timeout instead of returning its mesh.
        # An ensemble submits several graphs per /generate3d job and only one of
        # them can carry the job's own id, so this is now the ordinary case.
        #
        # It must be a CANONICAL uuid: ComfyUI v0.35 refuses anything else with
        # `invalid_prompt_id`, which also rules out the obvious
        # f"{job_id}-structure-2" scheme for naming an ensemble's sub-runs.
        prompt_id = prompt_id or str(uuid.uuid4())
        self.submit(graph, prompt_id)

        def sample_vram():
            if telemetry is None:
                return
            report = self.vram_report()
            used = (report or {}).get("vram_used_gb")
            if used is None:
                return
            if used > telemetry.get("peak_vram_gb", 0):
                telemetry["peak_vram_gb"] = used
                telemetry["vram_total_gb"] = report.get("vram_total_gb")

        stop_event = threading.Event()
        watcher = threading.Thread(
            target=self.watch_progress,
            args=(prompt_id, progress, stop_event, self.node_labels(graph)),
            daemon=True,
            name="meshgen-ws",
        )
        watcher.start()
        try:
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel_prompt(prompt_id)
                    raise Cancelled("cancelled")
                try:
                    state = self.job_state(prompt_id)
                except (urllib.error.URLError, OSError, TimeoutError):
                    state = None
                    time.sleep(2.0)
                    continue

                status = (state or {}).get("status")
                if status == "completed":
                    entry = self.history(prompt_id)
                    if entry is None:
                        raise BackendError("ComfyUI reported success but produced no history entry")
                    return entry
                if status == "failed":
                    err = (state or {}).get("execution_error") or {}
                    raise BackendError(self._format_error(err))
                if status == "cancelled":
                    raise Cancelled("ComfyUI reported the job cancelled")
                if state is None and self._proc is not None and self._proc.poll() is not None:
                    raise BackendError(
                        f"ComfyUI exited while the job was running (code {self._proc.returncode}). "
                        f"See {self._log_path}"
                    )
                sample_vram()
                time.sleep(1.5)
            self.cancel_prompt(prompt_id)
            raise BackendError(f"generation exceeded the {timeout_s}s budget and was cancelled")
        finally:
            stop_event.set()

    @staticmethod
    def _format_error(err: dict) -> str:
        if not err:
            return "ComfyUI reported a failure with no detail (see the ComfyUI log)"
        parts = []
        if err.get("node_type"):
            parts.append(f"node {err.get('node_type')} (#{err.get('node_id')})")
        if err.get("exception_type"):
            parts.append(str(err["exception_type"]))
        if err.get("exception_message"):
            parts.append(str(err["exception_message"]))
        if not parts:
            return "ComfyUI execution failed"
        return "ComfyUI execution failed: " + " - ".join(parts)

    # -- outputs ----------------------------------------------------------
    def find_mesh_outputs(self, entry: dict, suffixes=(".glb", ".gltf", ".obj", ".ply")):
        """Every mesh file a finished workflow reported, as absolute paths.

        Two shapes appear in ComfyUI history and both are real:

        * the SaveImage-style dict, ``{"filename", "subfolder", "type"}`` - what
          image and most save nodes emit;
        * a bare **relative path string**, which is what ``Save3DAdvanced``
          emits: ``{"result": ["forge/trellis2_00001.glb", null, []]}``.

        Handling only the first silently loses the mesh at the very last step,
        after minutes of GPU work, so both are parsed here.
        """
        found = []
        for node_output in (entry.get("outputs") or {}).values():
            if not isinstance(node_output, dict):
                continue
            for items in node_output.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and "filename" in item:
                        name = str(item["filename"])
                        if not name.lower().endswith(suffixes):
                            continue
                        base = self.config.output_dir
                        if item.get("type") == "temp":
                            base = self.config.comfyui_dir / "temp"
                        found.append(base / (item.get("subfolder") or "") / name)
                    elif isinstance(item, str) and item.lower().endswith(suffixes):
                        # relative to the output directory
                        found.append(self.config.output_dir / item)
        return found

    def collect_output(self, entry: dict, out_path, suffixes=(".glb", ".gltf", ".obj", ".ply")):
        """Move the mesh a finished workflow produced to ``out_path``."""
        candidates = self.find_mesh_outputs(entry, suffixes)
        if not candidates:
            raise BackendError(
                "the workflow finished but produced no mesh file - check that the "
                "graph still ends in a Save3DAdvanced node"
            )

        produced = candidates[-1]
        if not produced.is_file():
            raise BackendError(f"ComfyUI reported {produced} but it is not on disk")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, out_path)
        return {"mesh_path": str(out_path), "comfyui_path": str(produced)}
