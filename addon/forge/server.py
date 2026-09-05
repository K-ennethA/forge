"""Newline-delimited JSON socket server for the Forge add-on.

Wire contract (``docs/architecture.md``)::

    request  {"id": "<optional>", "type": "<command>", "params": {...}}\\n
    response {"id": "<echoed>", "status": "success"|"error",
              "result": <object|null>, "message": "<human readable>"}\\n

Threading model
---------------
A daemon thread owns the listening socket and one more daemon thread per client
connection.  Those threads never touch ``bpy``: they parse a line, push a job
onto a queue and block on an :class:`threading.Event`.  A ``bpy.app.timers``
pump runs on Blender's main thread, drains the queue, executes the handlers and
sets the events, at which point the connection thread writes the response back.

Everything is defensive by design: malformed JSON, unknown commands and handler
exceptions all come back as ``status: "error"`` and the server keeps listening.
"""

import json
import queue
import socket
import sys
import threading
import time
import traceback

import bpy

from .prefs import get_prefs
from .tools import registry

# Largest single request line we will buffer (load_mesh payloads can be big).
MAX_LINE_BYTES = 256 * 1024 * 1024
RECV_BYTES = 65536
ACCEPT_TIMEOUT = 0.5
CLIENT_TIMEOUT = 0.5
PUMP_INTERVAL = 0.05
# Never spend longer than this inside one timer tick, so the UI stays alive.
PUMP_BUDGET = 0.25

_server = None  # module level singleton


def _log(*args):
    print("[Forge]", *args)
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


class _Job(object):
    __slots__ = ("request", "response", "event")

    def __init__(self, request):
        self.request = request
        self.response = None
        self.event = threading.Event()


def _error_response(request_id, message):
    return {"id": request_id, "status": "error", "result": None, "message": message}


def _execute(request):
    """Run one request on the main thread. Returns a response dict."""
    if not isinstance(request, dict):
        return _error_response(None, "Each line must be a JSON object, got %s." % type(request).__name__)
    request_id = request.get("id")
    if not isinstance(request_id, (str, int, float, type(None))):
        request_id = str(request_id)
    command = request.get("type")
    params = request.get("params")
    status, result, message = registry.dispatch(command, params)
    return {"id": request_id, "status": status, "result": result, "message": message}


class ForgeServer(object):
    """TCP listener + main-thread command pump."""

    def __init__(self, host="127.0.0.1", port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self._accept_thread = None
        self._queue = queue.Queue()
        self._connections = set()
        self._lock = threading.Lock()
        self.started_at = None
        self.total_connections = 0
        self.commands_handled = 0
        self.last_command = ""
        self.last_error = ""

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self.running:
            raise RuntimeError("Forge server is already running on %s:%d" % (self.host, self.port))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not sys.platform.startswith("win"):
            # On Windows SO_REUSEADDR lets a second process steal the port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(8)
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            raise RuntimeError(
                "Could not listen on %s:%d - %s. Another Blender instance or process "
                "may already own that port." % (self.host, self.port, exc)
            )
        sock.settimeout(ACCEPT_TIMEOUT)
        self.socket = sock
        self.running = True
        self.started_at = time.time()
        self.last_error = ""
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="ForgeServerAccept", daemon=True
        )
        self._accept_thread.start()
        _ensure_pump()
        _log("listening on %s:%d" % (self.host, self.port))

    def stop(self):
        if not self.running:
            return
        self.running = False
        sock, self.socket = self.socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        thread, self._accept_thread = self._accept_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=ACCEPT_TIMEOUT * 3)
        # Release anything still queued so blocked clients unwind immediately.
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            job.response = _error_response(
                job.request.get("id") if isinstance(job.request, dict) else None,
                "Forge server stopped before this command could run.",
            )
            job.event.set()
        _remove_pump()
        _log("stopped")

    # -- socket side (background threads) ----------------------------------

    def _accept_loop(self):
        while self.running:
            sock = self.socket
            if sock is None:
                break
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:  # noqa: BLE001
                self.last_error = traceback.format_exc()
                break
            self.total_connections += 1
            with self._lock:
                self._connections.add(conn)
            threading.Thread(
                target=self._client_loop, args=(conn, addr), name="ForgeClient", daemon=True
            ).start()

    def _client_loop(self, conn, addr):
        conn.settimeout(CLIENT_TIMEOUT)
        buffer = b""
        try:
            while self.running:
                try:
                    chunk = conn.recv(RECV_BYTES)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES:
                    self._send(conn, _error_response(None, "Request exceeded %d bytes." % MAX_LINE_BYTES))
                    break
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_line(conn, line)
                    if not self.running:
                        break
        except Exception:  # noqa: BLE001 - a client must never kill the server
            self.last_error = traceback.format_exc()
            _log("client error from %r:\n%s" % (addr, self.last_error))
        finally:
            with self._lock:
                self._connections.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, conn, line):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._send(conn, _error_response(None, "Request was not valid UTF-8: %s" % exc))
            return
        try:
            request = json.loads(text)
        except ValueError as exc:
            preview = text[:200] + ("..." if len(text) > 200 else "")
            self._send(conn, _error_response(None, "Malformed JSON: %s (received %r)" % (exc, preview)))
            return

        job = _Job(request)
        self._queue.put(job)

        timeout = float(getattr(get_prefs(), "job_timeout", 600.0) or 600.0)
        deadline = time.monotonic() + timeout
        while True:
            if job.event.wait(0.25):
                break
            if not self.running:
                job.response = _error_response(
                    request.get("id") if isinstance(request, dict) else None,
                    "Forge server stopped while the command was queued.",
                )
                break
            if time.monotonic() > deadline:
                job.response = _error_response(
                    request.get("id") if isinstance(request, dict) else None,
                    "Command timed out after %.0fs waiting for Blender's main thread. "
                    "Blender may be busy (modal operator, render, or a modal dialog is open)."
                    % timeout,
                )
                break
        self._send(conn, job.response)

    def _send(self, conn, response):
        if response is None:
            response = _error_response(None, "Internal error: no response produced.")
        try:
            payload = json.dumps(response, default=_json_fallback)
        except (TypeError, ValueError) as exc:
            payload = json.dumps(
                _error_response(
                    response.get("id"),
                    "Result was not JSON-serialisable: %s" % exc,
                )
            )
        try:
            conn.sendall(payload.encode("utf-8") + b"\n")
        except OSError:
            pass

    # -- main thread -------------------------------------------------------

    def drain(self):
        """Execute queued commands. Called from the bpy timer (main thread)."""
        deadline = time.monotonic() + PUMP_BUDGET
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                job.response = _execute(job.request)
                if isinstance(job.request, dict):
                    self.last_command = str(job.request.get("type", ""))
                if job.response.get("status") == "error":
                    self.last_error = str(job.response.get("message", ""))[:400]
            except Exception:  # noqa: BLE001
                self.last_error = traceback.format_exc()
                job.response = _error_response(None, "Internal server error:\n%s" % self.last_error)
            finally:
                self.commands_handled += 1
                job.event.set()
            if time.monotonic() > deadline:
                return

    # -- introspection -----------------------------------------------------

    def status(self):
        with self._lock:
            active = len(self._connections)
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "active_connections": active,
            "total_connections": self.total_connections,
            "commands_handled": self.commands_handled,
            "queued": self._queue.qsize(),
            "last_command": self.last_command,
            "last_error": self.last_error,
            "uptime": (time.time() - self.started_at) if self.started_at else 0.0,
        }


def _json_fallback(value):
    return repr(value)


# ---------------------------------------------------------------------------
# main-thread pump
# ---------------------------------------------------------------------------

def _pump():
    server = _server
    if server is None or not server.running:
        return None
    try:
        server.drain()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return PUMP_INTERVAL


def _ensure_pump():
    try:
        if not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=0.0, persistent=True)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _remove_pump():
    try:
        if bpy.app.timers.is_registered(_pump):
            bpy.app.timers.unregister(_pump)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------

def is_running():
    return _server is not None and _server.running


def get_status():
    if _server is None:
        prefs = get_prefs()
        return {
            "running": False,
            "host": getattr(prefs, "host", "127.0.0.1"),
            "port": int(getattr(prefs, "port", 9876)),
            "active_connections": 0,
            "total_connections": 0,
            "commands_handled": 0,
            "queued": 0,
            "last_command": "",
            "last_error": "",
            "uptime": 0.0,
        }
    return _server.status()


def start_server(host=None, port=None):
    """Start the singleton server. Raises RuntimeError with a usable message."""
    global _server
    prefs = get_prefs()
    host = host or getattr(prefs, "host", "127.0.0.1") or "127.0.0.1"
    port = int(port or getattr(prefs, "port", 9876) or 9876)
    if _server is not None and _server.running:
        if _server.host == host and _server.port == port:
            return _server
        _server.stop()
    _server = ForgeServer(host, port)
    _server.start()
    return _server


def stop_server():
    global _server
    if _server is not None:
        _server.stop()
    _remove_pump()
    _server = None


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class FORGE_OT_start_server(bpy.types.Operator):
    bl_idname = "forge.start_server"
    bl_label = "Start Forge Server"
    bl_description = "Start the Forge command socket so Claude (via the MCP server) can drive Blender"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return not is_running()

    def execute(self, context):
        try:
            server = start_server()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Forge server listening on %s:%d" % (server.host, server.port))
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_stop_server(bpy.types.Operator):
    bl_idname = "forge.stop_server"
    bl_label = "Stop Forge Server"
    bl_description = "Stop the Forge command socket and release the port"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return is_running()

    def execute(self, context):
        try:
            stop_server()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Forge server stopped")
        _tag_redraw()
        return {"FINISHED"}


class FORGE_OT_clear_last_error(bpy.types.Operator):
    bl_idname = "forge.clear_last_error"
    bl_label = "Clear Last Error"
    bl_description = "Forget the last error reported by the Forge server"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if _server is not None:
            _server.last_error = ""
        _tag_redraw()
        return {"FINISHED"}


def _tag_redraw():
    if bpy.app.background:
        return
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except (AttributeError, TypeError):
        pass


_CLASSES = (
    FORGE_OT_start_server,
    FORGE_OT_stop_server,
    FORGE_OT_clear_last_error,
)


def _autostart():
    prefs = get_prefs()
    if getattr(prefs, "autostart", False):
        try:
            start_server()
        except Exception as exc:  # noqa: BLE001
            _log("autostart failed:", exc)
    return None


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.app.timers.register(_autostart, first_interval=1.0)
    except Exception:  # noqa: BLE001
        pass


def unregister():
    try:
        stop_server()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    try:
        if bpy.app.timers.is_registered(_autostart):
            bpy.app.timers.unregister(_autostart)
    except Exception:  # noqa: BLE001
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
