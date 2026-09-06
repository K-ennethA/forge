"""A backend adapter that produces a real .glb without a GPU.

Injected with ``FORGE_MESHGEN_BACKEND_MODULES=meshgen.tests.fake_backend`` so
the job API, config resolution, cancellation and readiness reporting can all be
exercised on a machine with none of the 15 GB of weights present - and so no
test-only branch has to exist in the production registry.

It writes a genuine one-triangle binary glTF, which means meshgen.glb parses
the same file shape it will parse in production.
"""

from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

from meshgen.backends.base import Backend, BackendError, Cancelled


def write_triangle_glb(path):
    """Write a minimal valid .glb: one triangle, indexed, no material."""
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    indices = [0, 1, 2]

    index_bytes = struct.pack("<3H", *indices)
    index_bytes += b"\x00" * ((4 - len(index_bytes) % 4) % 4)
    position_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
    buffer = index_bytes + position_bytes

    doc = {
        "asset": {"version": "2.0", "generator": "forge-meshgen fake backend"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1}, "indices": 0, "mode": 4}]}],
        "buffers": [{"byteLength": len(buffer)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(index_bytes), "target": 34963},
            {"buffer": 0, "byteOffset": len(index_bytes),
             "byteLength": len(position_bytes), "target": 34962},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 0.0]},
        ],
    }

    json_bytes = json.dumps(doc).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)

    total = 12 + 8 + len(json_bytes) + 8 + len(buffer)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A)
    out += json_bytes
    out += struct.pack("<II", len(buffer), 0x004E4942)
    out += buffer

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


class FakeBackend(Backend):
    """Deterministic stand-in. Behaviour is steered entirely by env vars."""

    name = "fake"
    model = "fake-triangle-v1"
    license = "MIT (test fixture)"
    vram_gb = 0
    description = "Writes a one-triangle .glb. Test fixture, never a real model."

    def __init__(self, config, client=None):
        super().__init__(config)
        self.client = client

    # -- knobs ------------------------------------------------------------
    @staticmethod
    def _env(name, default=""):
        return os.environ.get(name, default)

    def readiness(self):
        # FORGE_MESHGEN_FAKE_MISSING=1 makes the backend report absent weights,
        # which is how the missing-models guidance in /health gets tested.
        if self._env("FORGE_MESHGEN_FAKE_MISSING") == "1":
            return {"ready": False, "missing": [{
                "what": "fake_weights.safetensors (0.0 GB)",
                "path": str(self.config.model_path("diffusion_models", "fake_weights.safetensors")),
                "source": "https://example.invalid/fake_weights.safetensors",
                "bytes": 1234,
            }]}
        return {"ready": True, "missing": []}

    def is_loaded(self):
        return self._env("FORGE_MESHGEN_FAKE_LOADED") == "1"

    def generate(self, image_path, options, out_path, progress=None, cancel_event=None, job_id=None):
        self.ensure_ready()
        progress = progress or (lambda *a: None)

        if self._env("FORGE_MESHGEN_FAKE_FAIL") == "1":
            raise BackendError("fake backend was told to fail")

        delay = float(self._env("FORGE_MESHGEN_FAKE_DELAY", "0") or 0)
        started = time.time()
        steps = 10
        for step in range(steps):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled("cancelled")
            progress((step + 1) / steps, f"fake step {step + 1}")
            if delay:
                time.sleep(delay / steps)

        write_triangle_glb(out_path)
        from meshgen import glb
        return {
            "mesh_path": str(out_path),
            "stats": glb.stats(out_path),
            "backend": self.name,
            "model": self.model,
            "duration_ms": int((time.time() - started) * 1000),
            # same shape as the real adapters report, with nothing to measure
            "vram": {"before_gb": None, "after_gb": None, "peak_gb": None,
                     "total_gb": None, "device": None},
            "options_seen": dict(options or {}),
        }

    def cancel(self, handle):
        return True


class MissingBackend(FakeBackend):
    """Always unready - exercises ensure_ready()/NotReady end to end."""

    name = "fake-missing"
    model = "fake-missing-v1"

    def readiness(self):
        return {"ready": False, "missing": [{
            "what": "absent_model.safetensors (9.9 GB)",
            "path": str(self.config.model_path("diffusion_models", "absent_model.safetensors")),
            "source": "https://huggingface.co/example/absent/resolve/main/absent_model.safetensors",
            "bytes": 999,
        }]}


BACKENDS = [FakeBackend, MissingBackend]
