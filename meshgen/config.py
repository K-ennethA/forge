"""Config resolution for the meshgen service.

One JSON file (``meshgen/config.json``) holds every path into the heavyweight
model install so relocating it is a one-file edit.  A handful of environment
variables override individual keys, which is what the tests use to point the
service at a scratch directory.

Stdlib only, on purpose: this module is imported by the add-on side too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = HERE / "config.json"

#: env var -> (config key, coercion)
_ENV_OVERRIDES = {
    "FORGE_MESHGEN_PORT": ("port", int),
    "FORGE_MESHGEN_BACKEND": ("default_backend", str),
    "FORGE_MESHGEN_COMFYUI_ROOT": ("comfyui_root", str),
    "FORGE_MESHGEN_COMFYUI_PYTHON": ("comfyui_python", str),
    "FORGE_MESHGEN_MODELS_ROOT": ("models_root", str),
    "FORGE_MESHGEN_COMFYUI_PORT": ("comfyui_port", int),
    "FORGE_MESHGEN_OUTPUT_DIR": ("comfyui_output_dir", str),
    "FORGE_MESHGEN_INPUT_DIR": ("comfyui_input_dir", str),
    "FORGE_MESHGEN_JOB_TIMEOUT": ("job_timeout_s", int),
    "FORGE_MESHGEN_STARTUP_TIMEOUT": ("comfyui_startup_timeout_s", int),
}

_DEFAULTS = {
    "comfyui_root": "C:/forge-models/comfyui",
    "comfyui_python": "C:/forge-models/comfyui/.venv/Scripts/python.exe",
    "models_root": "C:/forge-models/models",
    "comfyui_output_dir": "C:/forge-models/comfyui-output",
    "comfyui_input_dir": "C:/forge-models/comfyui-input",
    "port": 8902,
    "comfyui_port": 8188,
    "default_backend": "trellis2",
    "comfyui_startup_timeout_s": 420,
    "job_timeout_s": 1800,
    "comfyui_idle_shutdown_s": 0,
}


class Config:
    """Resolved configuration.  Attribute access, plus ``as_dict()``."""

    def __init__(self, data: dict, source: Path | None):
        self._data = data
        self.source = source

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name, default=None):
        return self._data.get(name, default)

    def as_dict(self) -> dict:
        return dict(self._data)

    # -- derived paths --------------------------------------------------
    @property
    def models_dir(self) -> Path:
        return Path(self.models_root)

    @property
    def comfyui_dir(self) -> Path:
        return Path(self.comfyui_root)

    @property
    def comfyui_main(self) -> Path:
        return self.comfyui_dir / "main.py"

    @property
    def comfyui_python_path(self) -> Path:
        return Path(self.comfyui_python)

    @property
    def output_dir(self) -> Path:
        return Path(self.comfyui_output_dir)

    @property
    def input_dir(self) -> Path:
        return Path(self.comfyui_input_dir)

    @property
    def workflows_dir(self) -> Path:
        return HERE / "workflows"

    def model_path(self, folder: str, filename: str) -> Path:
        return self.models_dir / folder / filename

    def comfyui_url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.comfyui_port}{path}"


def load(path=None) -> Config:
    """Load config.json (or ``FORGE_MESHGEN_CONFIG``), then apply env overrides."""
    if path is None:
        path = os.environ.get("FORGE_MESHGEN_CONFIG") or DEFAULT_CONFIG_PATH
    path = Path(path)

    data = dict(_DEFAULTS)
    source = None
    if path.is_file():
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for key, value in raw.items():
            if key.startswith("_"):
                continue
            data[key] = value
        source = path

    for env_name, (key, coerce) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            data[key] = coerce(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{env_name}={raw!r} is not a valid value for {key}") from None

    return Config(data, source)
