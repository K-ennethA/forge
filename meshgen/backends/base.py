"""The backend adapter interface.

A meshgen backend is one class with three methods.  Everything model-specific
lives behind them, so swapping TRELLIS.2 for whatever ships next year is a new
file in this folder plus one config line - never a change to the service.

    info()          -> dict describing the backend (never touches the GPU)
    ensure_ready()  -> dict; report BEFORE fetching, raise NotReady if missing
    generate(...)   -> dict with at least {"mesh_path"}

``ensure_ready`` must be honest and cheap: it says what it *would* fetch and
where from, and it raises rather than silently downloading 19 GB behind the
artist's back.
"""

from __future__ import annotations

from pathlib import Path


class BackendError(RuntimeError):
    """Generation failed for a reason the artist can act on."""


class NotReady(BackendError):
    """Weights or the runtime are missing.  ``.missing`` says what and where."""

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = list(missing or [])


class Cancelled(BackendError):
    """The job was cancelled by /cancel."""


class Backend:
    #: short name used by FORGE_MESHGEN_BACKEND and the "backend" request field
    name = "base"
    #: human model name
    model = "n/a"
    #: SPDX-ish license string for the weights + code path actually used
    license = "n/a"
    #: rough VRAM the backend wants, in GB (None = unknown)
    vram_gb = None
    description = ""
    #: True when this adapter accepts a ``views`` block (front + side + back).
    #: Declaring it is not the same as being able to run it - see
    #: :meth:`multiview_readiness`, which is what decides a request.
    supports_multiview = False

    def __init__(self, config):
        self.config = config

    # -- interface -------------------------------------------------------
    def info(self) -> dict:
        """Cheap, side-effect-free description.  Never loads a model."""
        ready = self.readiness()
        return {
            "name": self.name,
            "model": self.model,
            "license": self.license,
            "vram_gb": self.vram_gb,
            "description": self.description,
            "loaded": self.is_loaded(),
            "ready": ready["ready"],
            "missing": ready["missing"],
        }

    def readiness(self) -> dict:
        """``{"ready": bool, "missing": [{"what", "path", "source", "bytes"}]}``."""
        return {"ready": True, "missing": []}

    def is_loaded(self) -> bool:
        """True when the weights are resident (a job has run and the host is up)."""
        return False

    def multiview_readiness(self, use_client: bool = False) -> dict:
        """``{"supported": bool, "available": bool, "missing": [...], "detail": str}``.

        Split deliberately from :meth:`readiness`: a backend can be perfectly
        ready for single-image work and still have no way to run multi-view, so
        the two must never collapse into one "ready" flag.
        """
        return {
            "supported": False,
            "available": False,
            "missing": [],
            "detail": f"backend {self.name!r} does not accept multi-view input",
        }

    def ensure_ready(self) -> dict:
        """Raise :class:`NotReady` listing what is missing, else return readiness."""
        state = self.readiness()
        if not state["ready"]:
            raise NotReady(self._missing_message(state["missing"]), state["missing"])
        return state

    def generate(self, image_path: str, options: dict, out_path: str, progress=None) -> dict:
        """Run the model.  ``progress(fraction, label)`` may be called repeatedly."""
        raise NotImplementedError

    def cancel(self, handle) -> bool:
        """Best-effort interrupt of an in-flight generate().  Returns True if sent."""
        return False

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _missing_message(missing) -> str:
        if not missing:
            return "backend is not ready"
        lines = [f"{len(missing)} item(s) missing for this backend:"]
        for item in missing:
            lines.append(
                "  - {what}\n      expected at: {path}\n      get it from: {source}".format(
                    what=item.get("what", "?"),
                    path=item.get("path", "?"),
                    source=item.get("source", "?"),
                )
            )
        return "\n".join(lines)

    def _check_files(self, entries) -> dict:
        """``entries`` = list of ``{"what","path","source","bytes"}``; filter to absent."""
        missing = []
        for entry in entries:
            path = Path(entry["path"])
            if not path.is_file():
                missing.append(dict(entry, present=False))
                continue
            want = entry.get("bytes")
            if want and path.stat().st_size != want:
                item = dict(entry, present=True)
                item["what"] = (
                    f"{entry['what']} (wrong size: {path.stat().st_size} bytes on disk, "
                    f"expected {want} - probably a truncated download)"
                )
                missing.append(item)
        return {"ready": not missing, "missing": missing}
