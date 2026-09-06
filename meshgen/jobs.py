"""In-memory job store.

Generation is minutes-long, so /generate3d returns a job id immediately and the
caller polls /job/<id>.  One job runs at a time - a 12 GB card cannot hold two
of these models at once, and pretending otherwise would just OOM later, so
extra submissions queue.

Job ids are canonical UUIDs because ComfyUI accepts a client-supplied
``prompt_id`` in that form: one id addresses the job in both services.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

MAX_KEPT = 100


class Job:
    def __init__(self, job_id, image_path, backend, options, output):
        self.id = job_id
        self.image_path = image_path
        self.backend = backend
        self.options = options or {}
        self.output = output
        self.state = QUEUED
        self.progress = None          # 0.0-1.0 when the backend reports it
        self.stage = None             # short label, e.g. the running node
        self.error = None
        self.result = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancel_event = threading.Event()

    # -- serialisation ---------------------------------------------------
    def as_dict(self) -> dict:
        payload = {
            "job_id": self.id,
            "state": self.state,
            "backend": self.backend,
            "image_path": self.image_path,
            "output": self.output,
            "progress": self.progress,
            "stage": self.stage,
            "created": self.created,
        }
        if self.started is not None:
            payload["started"] = self.started
        if self.finished is not None:
            payload["finished"] = self.finished
            payload["duration_ms"] = (
                int((self.finished - self.started) * 1000) if self.started else None
            )
        if self.state == ERROR:
            payload["error"] = self.error
        if self.state == DONE and self.result:
            payload.update({
                "mesh_path": self.result.get("mesh_path"),
                "stats": self.result.get("stats"),
                "duration_ms": self.result.get("duration_ms"),
                "vram": self.result.get("vram"),
                "model": self.result.get("model"),
            })
        return payload


class JobStore:
    def __init__(self, max_kept=MAX_KEPT):
        self._jobs = OrderedDict()
        self._lock = threading.RLock()
        self.max_kept = max_kept

    def create(self, image_path, backend, options, output) -> Job:
        job = Job(str(uuid.uuid4()), image_path, backend, options, output)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self.max_kept:
                oldest_id, oldest = next(iter(self._jobs.items()))
                if oldest.state in (QUEUED, RUNNING):
                    break
                self._jobs.pop(oldest_id)
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def all(self):
        with self._lock:
            return list(self._jobs.values())

    def active(self):
        with self._lock:
            return [j for j in self._jobs.values() if j.state in (QUEUED, RUNNING)]
