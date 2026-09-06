"""Backend registry.

Built-in adapters are listed here.  Extra adapters can be injected without
touching this file via ``FORGE_MESHGEN_BACKEND_MODULES`` (comma-separated
import paths, each exposing ``BACKENDS = [cls, ...]`` or a single ``Backend``
subclass) - which is how the test suite supplies its fake backend without a
test-only branch living in production code.
"""

from __future__ import annotations

import importlib
import os

from .base import Backend, BackendError, Cancelled, NotReady
from .comfyui_pixal3d import Pixal3DBackend
from .comfyui_trellis2 import Trellis2Backend

#: adapters that talk to the shared ComfyUI host
COMFYUI_BACKENDS = [Trellis2Backend, Pixal3DBackend]


def _extra_classes():
    raw = os.environ.get("FORGE_MESHGEN_BACKEND_MODULES", "")
    classes = []
    for name in [part.strip() for part in raw.split(",") if part.strip()]:
        module = importlib.import_module(name)
        found = getattr(module, "BACKENDS", None)
        if found is None:
            found = [
                value for value in vars(module).values()
                if isinstance(value, type) and issubclass(value, Backend) and value is not Backend
            ]
        classes.extend(found)
    return classes


def build(config, client):
    """Instantiate every available backend.  Returns ``{name: backend}``.

    ``client`` is the shared :class:`~meshgen.comfyui_client.ComfyUIClient`;
    adapters that do not host on ComfyUI simply ignore it.
    """
    backends = {}
    for cls in COMFYUI_BACKENDS:
        backend = cls(config, client)
        backends[backend.name] = backend
    for cls in _extra_classes():
        try:
            backend = cls(config, client)
        except TypeError:
            backend = cls(config)
        backends[backend.name] = backend
    return backends


__all__ = [
    "Backend", "BackendError", "Cancelled", "NotReady",
    "Trellis2Backend", "Pixal3DBackend", "build",
]
