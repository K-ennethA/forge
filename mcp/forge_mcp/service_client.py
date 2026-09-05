"""HTTP client for the Build123d geometry service (docs/architecture.md, :8765).

Endpoints:
    GET  /health        -> {"status": "ok", "build123d": "<version>"}
    POST /parse_params  -> {"params": {...}}
    POST /generate      -> {"params": {...}, "mesh": {...}, "stats": {...}}
    POST /export        -> {"path": "..."}

Phase 2 (print readiness):

    POST /check           -> {"overall", "checks": [...], "printer", "params", "stats"}
    POST /segment         -> {"mode", "joint", "cuts", "segments": [...], "plate"}
    POST /export_segments -> {"directory", "files": [...], "plate", "plate_path"}

Errors come back as HTTP 400 (script/param problems) or 500 (service bugs) with
``{"error": "...", "traceback": "..."}``.

httpx is the declared dependency, but the module falls back to stdlib urllib if
it is missing so a half-installed venv still yields working tools instead of an
import crash at startup.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from . import config
from .errors import SERVICE_DOWN, BackendError, BackendUnavailable

try:  # pragma: no cover - import-time branch
    import httpx  # type: ignore

    _HAVE_HTTPX = True
except ImportError:  # pragma: no cover - fallback path
    httpx = None  # type: ignore[assignment]
    _HAVE_HTTPX = False

import urllib.error
import urllib.request


def _unavailable(detail: str = "") -> BackendUnavailable:
    message = SERVICE_DOWN.format(address=config.service_address())
    if detail:
        message = f"{message} ({detail})"
    return BackendUnavailable(message)


def _decode(body: bytes, url: str) -> Dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise BackendError(
            f"The geometry service returned non-JSON from {url}: {text[:400]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BackendError(
            f"The geometry service returned a non-object body from {url}: {text[:400]!r}"
        )
    return parsed


def _error_for_status(status: int, body: bytes, url: str) -> BackendError:
    """Turn a non-2xx response into a BackendError carrying the service's message."""
    detail: str
    traceback_tail = ""
    try:
        payload = _decode(body, url)
        detail = str(payload.get("error") or payload)
        raw_tb = payload.get("traceback")
        if isinstance(raw_tb, str) and raw_tb.strip():
            lines = raw_tb.strip().splitlines()
            traceback_tail = "\n".join(lines[-12:])
    except BackendError:
        detail = body.decode("utf-8", errors="replace")[:400] or f"HTTP {status}"

    message = f"Geometry service error (HTTP {status}) from {url}: {detail}"
    if traceback_tail:
        message = f"{message}\n--- service traceback (tail) ---\n{traceback_tail}"
    return BackendError(message)


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None,
             *, timeout: Optional[float] = None) -> Dict[str, Any]:
    url = f"{config.SERVICE_URL}{path}"
    read_timeout = timeout if timeout is not None else config.SERVICE_READ_TIMEOUT

    try:
        payload = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
    except (TypeError, ValueError) as exc:
        raise BackendError(f"Could not encode the request to {path} as JSON: {exc}") from exc

    status, raw = _send(method, url, payload, read_timeout)

    if status >= 400:
        raise _error_for_status(status, raw, url)
    return _decode(raw, url)


def _send(method: str, url: str, payload: Optional[bytes],
          read_timeout: float) -> Tuple[int, bytes]:
    if _HAVE_HTTPX:
        timeout = httpx.Timeout(
            read_timeout,
            connect=config.SERVICE_CONNECT_TIMEOUT,
            write=read_timeout,
            pool=config.SERVICE_CONNECT_TIMEOUT,
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
                f"The geometry service did not answer within {read_timeout:.0f}s "
                f"({url}). Heavy scripts can be slow — raise FORGE_SERVICE_READ_TIMEOUT, "
                "or check the service console for a hung build()."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"HTTP failure talking to {url}: {exc}") from exc
        return response.status_code, response.content

    # stdlib fallback -------------------------------------------------------
    request = urllib.request.Request(url, data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=read_timeout) as response:
            return int(response.status or 200), response.read()
    except urllib.error.HTTPError as exc:  # non-2xx still carries a body
        try:
            return int(exc.code), exc.read()
        except Exception:  # noqa: BLE001 - body already consumed/unreadable
            return int(exc.code), b""
    except urllib.error.URLError as exc:
        raise _unavailable(str(getattr(exc, "reason", exc))) from exc
    except TimeoutError as exc:
        raise BackendError(
            f"The geometry service did not answer within {read_timeout:.0f}s ({url})."
        ) from exc
    except OSError as exc:
        raise _unavailable(str(exc)) from exc


# --- endpoints --------------------------------------------------------------


def health() -> Dict[str, Any]:
    return _request("GET", "/health", timeout=config.SERVICE_CONNECT_TIMEOUT + 3.0)


def parse_params(script_source: str) -> Dict[str, Any]:
    return _request("POST", "/parse_params", {"script": script_source})


def generate(script_source: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _request(
        "POST", "/generate", {"script": script_source, "overrides": overrides or {}}
    )


def export(script_source: str, overrides: Optional[Dict[str, Any]],
           fmt: str, path: str) -> Dict[str, Any]:
    return _request(
        "POST",
        "/export",
        {
            "script": script_source,
            "overrides": overrides or {},
            "format": fmt,
            "path": path,
        },
    )


# --- Phase 2: print readiness ----------------------------------------------


def _print_body(
    script_source: str,
    overrides: Optional[Dict[str, Any]],
    printer: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """The three fields every Phase 2 endpoint shares.

    ``printer`` is omitted entirely when None so the service falls back to its
    built-in profile rather than being handed an empty object to merge.
    """
    body: Dict[str, Any] = {"script": script_source, "overrides": overrides or {}}
    if printer:
        body["printer"] = printer
    return body


def check(
    script_source: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """POST /check — bed fit, min wall, overhangs, watertight. Builds no files."""
    return _request(
        "POST",
        "/check",
        _print_body(script_source, overrides, printer),
        timeout=config.SERVICE_CHECK_TIMEOUT,
    )


def segment(
    script_source: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer: Optional[Dict[str, Any]] = None,
    *,
    mode: Any = "auto",
    joint: Optional[Dict[str, Any]] = None,
    include_mesh: bool = False,
) -> Dict[str, Any]:
    """POST /segment — cut the part into printable segments with mating joints."""
    body = _print_body(script_source, overrides, printer)
    body["mode"] = mode
    body["include_mesh"] = bool(include_mesh)
    if joint:
        body["joint"] = joint
    return _request("POST", "/segment", body, timeout=config.SERVICE_SEGMENT_TIMEOUT)


def export_segments(
    script_source: str,
    overrides: Optional[Dict[str, Any]] = None,
    printer: Optional[Dict[str, Any]] = None,
    *,
    mode: Any = "auto",
    joint: Optional[Dict[str, Any]] = None,
    directory: str = "",
    basename: Optional[str] = None,
    fmt: str = "stl",
) -> Dict[str, Any]:
    """POST /export_segments — one file per segment plus a packed plate 3MF."""
    body = _print_body(script_source, overrides, printer)
    body["mode"] = mode
    body["include_mesh"] = False
    body["directory"] = directory
    body["format"] = fmt
    if joint:
        body["joint"] = joint
    if basename:
        body["basename"] = basename
    return _request(
        "POST", "/export_segments", body, timeout=config.SERVICE_SEGMENT_TIMEOUT
    )


def is_available() -> Tuple[bool, str]:
    """(reachable, detail) — never raises, for the forge_status health tool."""
    try:
        payload = health()
    except BackendUnavailable as exc:
        return False, str(exc)
    except BackendError as exc:
        # Reachable, but unhappy: still useful to know it answered.
        return False, str(exc)
    version = payload.get("build123d") or "unknown"
    status = payload.get("status") or "?"
    return True, f"status={status}, build123d={version}"
