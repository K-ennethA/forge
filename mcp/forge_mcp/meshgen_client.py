"""HTTP client for the meshgen image-to-3D service (docs/architecture.md, :8902).

Endpoints (meshgen/README.md):

    GET  /health        -> backend name/model/licence/loaded + available_backends
    POST /generate3d    {"image_path", "backend"?, "options"?, "output"?} -> 202 {"job_id"}
    GET  /job/<id>      queued | running | done | error | cancelled (+ progress, stage)
    POST /cancel/<id>   best-effort interrupt

Two facts shape this module:

* **A job is minutes long.** ``/generate3d`` returns immediately and the caller
  polls, so no single HTTP read is ever allowed to be slow — a long read timeout
  here would turn a working job into a mystery hang.
* **``progress`` is per-stage, not per-job.** It is the fraction through the
  node currently running and it resets every time the pipeline moves on, so
  ``stage`` is the number that means something and this module keeps the ORDER
  the stages arrived in for the report.

Same httpx-with-urllib-fallback shape as ``service_client``, for the same
reason: a half-installed venv should still yield working tools.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config
from .errors import MESHGEN_DOWN, BackendError, BackendUnavailable

try:  # pragma: no cover - import-time branch
    import httpx  # type: ignore

    _HAVE_HTTPX = True
except ImportError:  # pragma: no cover - fallback path
    httpx = None  # type: ignore[assignment]
    _HAVE_HTTPX = False

import urllib.error
import urllib.request

#: Job states meshgen reports (meshgen/jobs.py).
QUEUED, RUNNING, DONE, ERROR, CANCELLED = (
    "queued", "running", "done", "error", "cancelled",
)
FINISHED_STATES = (DONE, ERROR, CANCELLED)


def _unavailable(detail: str = "") -> BackendUnavailable:
    message = MESHGEN_DOWN.format(address=config.meshgen_address())
    if detail:
        message = f"{message} ({detail})"
    return BackendUnavailable(message)


def _decode(body: bytes, url: str) -> Dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise BackendError(
            f"meshgen returned non-JSON from {url}: {text[:400]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BackendError(
            f"meshgen returned a non-object body from {url}: {text[:400]!r}"
        )
    return parsed


def _error_for_status(status: int, body: bytes, url: str) -> BackendError:
    try:
        payload = _decode(body, url)
        detail = str(payload.get("error") or payload)
        available = payload.get("available_backends")
        if available:
            detail = f"{detail} (available backends: {', '.join(map(str, available))})"
    except BackendError:
        detail = body.decode("utf-8", errors="replace")[:400] or f"HTTP {status}"
    return BackendError(f"meshgen error (HTTP {status}) from {url}: {detail}")


def _send(method: str, url: str, payload: Optional[bytes],
          read_timeout: float) -> Tuple[int, bytes]:
    if _HAVE_HTTPX:
        timeout = httpx.Timeout(
            read_timeout,
            connect=config.MESHGEN_CONNECT_TIMEOUT,
            write=read_timeout,
            pool=config.MESHGEN_CONNECT_TIMEOUT,
        )
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(
                    method,
                    url,
                    content=payload,
                    headers={"Content-Type": "application/json"} if payload else None,
                )
        except httpx.ConnectError as exc:
            raise _unavailable(str(exc)) from exc
        except httpx.ConnectTimeout as exc:
            raise _unavailable("connection timed out") from exc
        except httpx.TimeoutException as exc:
            raise BackendError(
                f"meshgen did not answer {url} within {read_timeout:.0f}s. The "
                "generation itself is polled, so this is the service being stuck, "
                "not a slow job."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"HTTP failure talking to {url}: {exc}") from exc
        return response.status_code, response.content

    request = urllib.request.Request(url, data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=read_timeout) as response:
            return int(response.status or 200), response.read()
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read()
        except Exception:  # noqa: BLE001 - body already consumed/unreadable
            return int(exc.code), b""
    except urllib.error.URLError as exc:
        raise _unavailable(str(getattr(exc, "reason", exc))) from exc
    except TimeoutError as exc:
        raise BackendError(f"meshgen did not answer {url} within {read_timeout:.0f}s.") from exc
    except OSError as exc:
        raise _unavailable(str(exc)) from exc


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None,
             *, timeout: Optional[float] = None) -> Dict[str, Any]:
    url = f"{config.MESHGEN_URL}{path}"
    read_timeout = timeout if timeout is not None else config.MESHGEN_READ_TIMEOUT
    try:
        payload = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
    except (TypeError, ValueError) as exc:
        raise BackendError(f"Could not encode the request to {path} as JSON: {exc}") from exc

    status, raw = _send(method, url, payload, read_timeout)
    if status >= 400:
        raise _error_for_status(status, raw, url)
    return _decode(raw, url)


# --- endpoints --------------------------------------------------------------


def health() -> Dict[str, Any]:
    return _request("GET", "/health", timeout=config.MESHGEN_CONNECT_TIMEOUT + 3.0)


def generate3d(image_path: str, backend: Optional[str] = None,
               options: Optional[Dict[str, Any]] = None,
               output: Optional[str] = None) -> Dict[str, Any]:
    """POST /generate3d — returns the 202 body ``{job_id, state, backend, output}``."""
    body: Dict[str, Any] = {"image_path": image_path}
    if backend:
        body["backend"] = backend
    if options:
        body["options"] = options
    if output:
        body["output"] = output
    return _request("POST", "/generate3d", body)


def job(job_id: str) -> Dict[str, Any]:
    return _request("GET", f"/job/{job_id}")


def cancel(job_id: str) -> Dict[str, Any]:
    return _request("POST", f"/cancel/{job_id}", {})


def wait_for_job(
    job_id: str,
    *,
    timeout: Optional[float] = None,
    interval: Optional[float] = None,
    on_poll: Optional[Callable[[Dict[str, Any]], None]] = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[Dict[str, Any], List[str]]:
    """Poll /job until it finishes. Returns ``(final job, stages in order)``.

    The stage list is the whole point of following a job rather than sleeping
    through it: it is the only honest account of what those five minutes were
    spent on, and it goes into the report verbatim.

    A job that is still running when the budget runs out is NOT an error here —
    it is handed back as-is, with its state still ``running``, so the caller can
    say "still going, poll it with meshgen_status" instead of pretending it died.
    """
    timeout = config.MESHGEN_JOB_TIMEOUT if timeout is None else timeout
    interval = config.MESHGEN_POLL_INTERVAL if interval is None else interval
    deadline = now() + float(timeout)
    stages: List[str] = []
    payload: Dict[str, Any] = {}

    while True:
        payload = job(job_id)
        stage = str(payload.get("stage") or "").strip()
        if stage and (not stages or stages[-1] != stage):
            stages.append(stage)
        if on_poll is not None:
            on_poll(payload)
        if str(payload.get("state") or "").lower() in FINISHED_STATES:
            return payload, stages
        if now() >= deadline:
            return payload, stages
        sleep(float(interval))


def is_available() -> Tuple[bool, str]:
    """(reachable, detail) — never raises, for forge_status and meshgen_status."""
    try:
        payload = health()
    except BackendUnavailable:
        return False, "not running"
    except BackendError as exc:
        return False, str(exc)
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    status = str(payload.get("status") or "?")
    name = str(backend.get("name") or "no backend")
    if status == "models_missing":
        missing = payload.get("missing") or []
        return True, f"status=models_missing, backend={name}, {len(missing)} file(s) to download"
    return True, f"status={status}, backend={name}"
