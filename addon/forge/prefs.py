"""Shared add-on identity + preference access.

This module lives directly inside the add-on package, so ``__package__`` here is
exactly the add-on/extension id that ``bpy.context.preferences.addons`` is keyed
by ("forge" for a legacy add-on, "bl_ext.<repo>.forge" for an extension).
Submodules must import ADDON_ID from here rather than computing it themselves.
"""

import os

import bpy

ADDON_ID = __package__

_HERE = os.path.dirname(os.path.abspath(__file__))


def default_printer_path():
    """The repo's ``templates/printer.json`` when this is a source checkout.

    Installed from a zip the add-on has no repo above it, so this comes back
    empty and the panel simply sends no printer profile — the geometry service
    then merges over its own built-in Elegoo Centauri Carbon defaults.
    """
    candidate = os.path.normpath(
        os.path.join(_HERE, os.pardir, os.pardir, "templates", "printer.json")
    )
    return candidate if os.path.isfile(candidate) else ""


def default_flows_dir():
    """The repo's ``flows/`` folder when this is a source checkout.

    Same reasoning as :func:`default_printer_path`: installed from a zip there
    is no repo above the add-on, so this comes back empty and the Flows box says
    so rather than guessing at a folder.  The path is derived from this file
    (``addon/forge/prefs.py`` -> ``<repo>/flows``), which is the same anchor the
    printer profile uses.
    """
    candidate = os.path.normpath(
        os.path.join(_HERE, os.pardir, os.pardir, "flows")
    )
    return candidate if os.path.isdir(candidate) else ""


# Values used when the add-on preferences are not available yet (during
# registration, in ``--background`` runs, or if the add-on entry was removed).
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9876,
    "autostart": False,
    "service_url": "http://127.0.0.1:8765",
    "assistant_url": "http://127.0.0.1:8901",
    "printer_path": default_printer_path(),
    "forge_flows_dir": default_flows_dir(),
    "request_timeout": 120.0,
    "job_timeout": 600.0,
    "verbose": False,
}


class _FallbackPrefs(object):
    """Read-only stand-in with the same attribute names as ForgePreferences."""

    def __init__(self):
        self.__dict__.update(DEFAULTS)


_FALLBACK = _FallbackPrefs()


def get_prefs():
    """Return the add-on preferences, or a defaults object that never raises."""
    try:
        prefs = bpy.context.preferences
    except AttributeError:
        return _FALLBACK
    if prefs is None:
        return _FALLBACK
    try:
        entry = prefs.addons.get(ADDON_ID)
    except (AttributeError, TypeError):
        entry = None
    if entry is None:
        return _FALLBACK
    try:
        if entry.preferences is None:
            return _FALLBACK
        return entry.preferences
    except AttributeError:
        return _FALLBACK


def pref(name):
    """Single preference value with a guaranteed fallback."""
    return getattr(get_prefs(), name, DEFAULTS.get(name))


def service_url(path=""):
    """Absolute geometry-service URL for ``path`` (e.g. ``/generate``)."""
    base = str(pref("service_url") or "").strip().rstrip("/")
    if not base:
        base = DEFAULTS["service_url"]
    if "://" not in base:
        base = "http://" + base
    if not path:
        return base
    return base + "/" + str(path).lstrip("/")
