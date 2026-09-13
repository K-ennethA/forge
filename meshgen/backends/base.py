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


#: ``options.ensemble`` - the seed-ensemble request surface.  Two independent
#: tiers, both deterministic; the full argument is under "Seed ensemble" in
#: meshgen/README.md.
#:
#: * ``structure_n`` - how many times to run the CHEAP head (structure sampler +
#:   structure VAE decode) at varied seeds before committing to the expensive
#:   tail.  1 disables it.  The cap is 9 because the consensus benefit plateaus
#:   there (arXiv 2608.09706) and paying past a plateau is just paying.
#: * ``best_of`` - how many FULL generations to run and then choose between.
#:   Linear in wall time, hence the cap of 5.
#:
#: The default is 1/1, stated here rather than inferred.  The measured cost of
#: each tier is in the README; a default that multiplies a caller's GPU time has
#: to be asked for.
ENSEMBLE_DEFAULTS = {"structure_n": 1, "best_of": 1}
ENSEMBLE_LIMITS = {"structure_n": (1, 9), "best_of": (1, 5)}


def ensemble_seeds(base_seed: int, count: int):
    """``base, base+1, ...`` - the candidate seeds, never random.

    Two runs of the same request therefore compare the same candidates, which is
    the property that lets a picker be believed without a human looking at the
    meshes.
    """
    return [int(base_seed) + offset for offset in range(int(count))]


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

    def resolved_ensemble(self, options: dict) -> dict:
        """Validate and fill in ``options.ensemble``.  Refuses, never clamps.

        A clamped ensemble is the same trap a clamped node input is: the caller
        asked for 20 candidates, got 9, was told nothing, and now reasons about a
        run that never happened.
        """
        raw = (options or {}).get("ensemble")
        if raw is None:
            return dict(ENSEMBLE_DEFAULTS)
        if not isinstance(raw, dict):
            raise BackendError(
                'option ensemble must be an object, e.g. {"ensemble": '
                '{"structure_n": 5}} - got ' + type(raw).__name__)
        unknown = sorted(set(raw) - set(ENSEMBLE_DEFAULTS))
        if unknown:
            raise BackendError(
                f"unknown ensemble key(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(ENSEMBLE_DEFAULTS))}")
        merged = dict(ENSEMBLE_DEFAULTS)
        for key, value in raw.items():
            # bool is an int in Python and {"best_of": true} means nothing here
            if isinstance(value, bool) or not isinstance(value, int):
                raise BackendError(f"ensemble.{key}={value!r} is not an integer")
            low, high = ENSEMBLE_LIMITS[key]
            if not (low <= value <= high):
                raise BackendError(
                    f"ensemble.{key}={value} is outside the supported range "
                    f"{low} to {high}")
            merged[key] = int(value)
        if merged["structure_n"] > 1 and merged["best_of"] > 1:
            raise BackendError(
                "ensemble.structure_n and ensemble.best_of cannot both be above "
                "1. best_of already draws N independent structures - running "
                "structure consensus inside each one would hand every candidate "
                "the same grid, and you would pay N full generations for N "
                "copies of one mesh. Pick a tier.")
        return merged

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
