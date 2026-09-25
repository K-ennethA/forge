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

import time
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


# ---------------------------------------------------------------------------
# seeds
# ---------------------------------------------------------------------------
#: ComfyUI's ``KSampler.seed`` schema: INT, 0 to 0xffffffffffffffff.  A caller's
#: seed is checked against the node's own range, like every other option.
SEED_MAX = 2 ** 64 - 1

#: A seed meshgen draws for a caller who sent none lands in ``[0, 2**32)``.
#: Deliberately far inside ``SEED_MAX``: 2**32 is exact in every JSON client
#: (JavaScript loses integers above 2**53), so a drawn seed read back from a
#: job record and sent again is the same seed, not a rounded neighbour.
DRAWN_SEED_BOUND = 2 ** 32

#: ``seed_source`` values.  ``caller``: the request named the seed.  ``drawn``:
#: it did not, so meshgen drew one - and recorded it, so the run can be replayed.
SEED_CALLER = "caller"
SEED_DRAWN = "drawn"


def coerce_seed(value) -> int:
    """A seed is a non-negative integer in the sampler's range.  Nothing else.

    Stricter than ``int()`` on purpose: ``int(True)`` is 1 and ``int(7.9)`` is
    7, and a seed that silently became a different seed is exactly a run that
    cannot be replayed.  A decimal string is accepted because JSON clients that
    cannot hold 64-bit integers send them that way.
    """
    if isinstance(value, bool):
        raise ValueError(f"seed {value!r} is a boolean, not an integer")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise ValueError(f"seed {value!r} is not an integer")
    if not 0 <= value <= SEED_MAX:
        raise ValueError(f"seed {value} is outside the sampler's range 0 to {SEED_MAX}")
    return value


coerce_seed.__name__ = "seed"


def draw_seed() -> int:
    """A fresh seed from the OS entropy pool (never the ``random`` module state,
    which a test or a library could have seeded to a constant)."""
    import secrets
    return secrets.randbelow(DRAWN_SEED_BOUND)


def resolve_seed(options):
    """``(options with a concrete seed, source)``.  Never mutates its input.

    Absent seed -> one is drawn, and it is IN the returned options, so it rides
    into the graph, the job record and the result like a caller's seed would.
    That is the whole fix for "twelve regenerations are one sample": before it,
    an absent seed meant the template's fixed 56, every time.
    """
    options = dict(options or {})
    if options.get("seed") is None:
        options.pop("seed", None)
        options["seed"] = draw_seed()
        return options, SEED_DRAWN
    try:
        options["seed"] = coerce_seed(options["seed"])
    except ValueError as exc:
        raise BackendError(f"option {exc}") from None
    return options, SEED_CALLER


# ---------------------------------------------------------------------------
# unscored candidates (/generate_ensemble)
# ---------------------------------------------------------------------------
#: How many full generations one /generate_ensemble job may fan out to.  9 is
#: where the published consensus benefit plateaus (arXiv 2608.09706) - the same
#: cap ``structure_n`` carries - and each candidate is a FULL run (82-304 s
#: measured), so the ceiling is ~45 minutes of GPU on the slowest backend.
CANDIDATE_LIMITS = (1, 9)


def candidate_seeds(options, n=None, seeds=None):
    """Settle the seed list of an unscored fan-out.  Refuses, never clamps.

    Exactly one of ``n`` (seeds ``base, base+1, ...`` from ``options.seed``) or
    ``seeds`` (an explicit list).  An explicit list next to ``options.seed`` is
    refused rather than guessed between.
    """
    low, high = CANDIDATE_LIMITS
    if seeds is not None:
        if not isinstance(seeds, list) or not seeds:
            raise BackendError("seeds must be a non-empty list of integers")
        if n is not None and n != len(seeds):
            raise BackendError(
                f"n={n!r} disagrees with the {len(seeds)} seeds given - send one or "
                "the other")
        if (options or {}).get("seed") is not None:
            raise BackendError(
                "seeds and options.seed both name the seeds - give one or the other")
        try:
            out = [coerce_seed(s) for s in seeds]
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        if len(set(out)) != len(out):
            raise BackendError(
                f"seeds {out} repeat a seed - a repeated seed is the same graph "
                "submitted twice, i.e. one sample paid for twice")
        if not low <= len(out) <= high:
            raise BackendError(
                f"{len(out)} seeds is outside the supported range {low} to {high}")
        return out
    if n is None:
        raise BackendError(
            f"n (how many candidates, {low} to {high}) is required - a default that "
            "multiplies GPU time has to be asked for")
    if isinstance(n, bool) or not isinstance(n, int):
        raise BackendError(f"n={n!r} is not an integer")
    if not low <= n <= high:
        raise BackendError(f"n={n} is outside the supported range {low} to {high}")
    base = coerce_seed((options or {})["seed"])
    if base + n - 1 > SEED_MAX:
        raise BackendError(
            f"seed {base} + {n - 1} runs past the sampler's range (max {SEED_MAX})")
    return ensemble_seeds(base, n)


def candidate_path(out_path, seed) -> Path:
    """``<stem>_seed<N><suffix>`` beside the requested output - the same naming
    the best_of tier uses for the candidates it keeps."""
    out = Path(out_path)
    return out.with_name(f"{out.stem}_seed{int(seed)}{out.suffix}")


def _raise_if_all_failed(candidates):
    if candidates and all(c.get("error") for c in candidates):
        lines = "\n".join(f"  seed {c['seed']}: {c['error']}" for c in candidates)
        raise BackendError(f"every candidate failed:\n{lines}")


def candidates_payload(backend, candidates, seeds, options, started, vram=None,
                       multiview_note=None):
    """The finished result of an unscored fan-out.  ``mesh_path`` is None ON
    PURPOSE: nothing was picked, and a first-candidate default would be a pick
    the caller never asked meshgen to make."""
    failed = [c for c in candidates if c.get("error")]
    payload = {
        "mesh_path": None,
        "candidates": candidates,
        "backend": backend.name,
        "model": backend.model,
        "duration_ms": int((time.time() - started) * 1000),
        "options": options,
        "seed": {"value": seeds[0], "stages": None},
        "vram": vram or {"before_gb": None, "after_gb": None, "peak_gb": None,
                         "total_gb": None, "device": None},
        "ensemble": {
            "tier": "candidates",
            "n": len(seeds),
            "seeds": list(seeds),
            "seed_base": seeds[0],
            "scored": False,
            "winner": None,
            "kept": [c["mesh_path"] for c in candidates if c.get("mesh_path")],
            "failed": len(failed),
            "note": ("meshgen does not score or rank candidates - every one is "
                     "on disk and the caller picks (benchmark / silhouette "
                     "machinery). For a meshgen-side pick use options.ensemble "
                     "best_of / structure_n on /generate3d."),
        },
    }
    if multiview_note is not None:
        payload["multiview"] = multiview_note
    return payload


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

    # -- capabilities (GET /capabilities) ---------------------------------
    #: option keys this adapter accepts in ``options``; None = not declared
    option_names = None

    def seed_capabilities(self) -> dict:
        """How this adapter takes a seed.  Overridden where there is more to say."""
        return {
            "option": "seed",
            "range": [0, SEED_MAX],
            "when_absent": (f"drawn from [0, {DRAWN_SEED_BOUND}) and recorded in the "
                            "job and the result as seed_source 'drawn'"),
        }

    def capabilities(self) -> dict:
        """What a caller can ask of this backend, declared rather than guessed.

        Cheap and side-effect-free like :meth:`info`: the multi-view verdict is
        the source-scan one (no ComfyUI round-trip), so this never starts or
        queries the model host.
        """
        multi = self.multiview_readiness()
        return {
            "name": self.name,
            "model": self.model,
            "license": self.license,
            "description": self.description,
            "conditioning": {
                "single_image": {"supported": True, "request": "image_path"},
                "multi_view": {
                    "supported": bool(multi.get("supported")),
                    "available": bool(multi.get("available")),
                    "missing": list(multi.get("missing") or []),
                    "detail": multi.get("detail"),
                },
            },
            "seed": self.seed_capabilities(),
            "ensemble": {
                "picked": {
                    "request": 'POST /generate3d {"options": {"ensemble": {...}}}',
                    "tiers": {key: list(ENSEMBLE_LIMITS[key]) for key in ENSEMBLE_DEFAULTS},
                    "scored_by": "meshgen (deterministic consensus; see README)",
                },
                "candidates": {
                    "request": 'POST /generate_ensemble {"n": N} or {"seeds": [...]}',
                    "range": list(CANDIDATE_LIMITS),
                    "scored_by": "the caller - meshgen returns every candidate unranked",
                },
            },
            "options": (sorted(self.option_names) if self.option_names is not None
                        else None),
        }

    # -- unscored fan-out -------------------------------------------------
    def generate_candidates(self, image_path, options, out_path, seeds,
                            progress=None, cancel_event=None, job_id=None):
        """N seeds -> N full generations -> every candidate, unranked.

        The generic version is one :meth:`generate` per seed, which is correct
        for any adapter.  ComfyUI-hosted adapters override it to stage the input
        once (see :meth:`ComfyUIBackend.generate_candidates`).  A candidate that
        fails is recorded with its error and the fan-out goes on - the others
        were paid for; only cancellation and missing weights stop it.
        """
        progress = progress or (lambda *a: None)
        started = time.time()
        count = len(seeds)
        candidates = []
        for index, seed in enumerate(seeds, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled("cancelled during the candidate fan-out")
            path = candidate_path(out_path, seed)
            t0 = time.time()

            def sub_progress(fraction, label, i=index, s=seed):
                overall = None if fraction is None else ((i - 1) + float(fraction)) / count
                progress(overall, f"candidate {i}/{count} (seed {s})"
                         + (f": {label}" if label else ""))

            record = {"index": index, "seed": int(seed)}
            try:
                result = self.generate(image_path, {**(options or {}), "seed": seed},
                                       str(path), progress=sub_progress,
                                       cancel_event=cancel_event, job_id=None)
            except (Cancelled, NotReady):
                raise
            except BackendError as exc:
                record.update({"mesh_path": None, "error": str(exc),
                               "duration_ms": int((time.time() - t0) * 1000)})
                candidates.append(record)
                continue
            record.update({
                "mesh_path": result.get("mesh_path"),
                "stats": result.get("stats"),
                "duration_ms": int((time.time() - t0) * 1000),
                "stage_seeds": (result.get("seed") or {}).get("stages"),
                "peak_vram_gb": (result.get("vram") or {}).get("peak_gb"),
            })
            candidates.append(record)
        _raise_if_all_failed(candidates)
        return candidates_payload(self, candidates, seeds, dict(options or {}), started)

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
