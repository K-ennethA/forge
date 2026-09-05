"""Shared add-on identity + preference access.

This module lives directly inside the add-on package, so ``__package__`` here is
exactly the add-on/extension id that ``bpy.context.preferences.addons`` is keyed
by ("forge" for a legacy add-on, "bl_ext.<repo>.forge" for an extension).
Submodules must import ADDON_ID from here rather than computing it themselves.
"""

import bpy

ADDON_ID = __package__

# Values used when the add-on preferences are not available yet (during
# registration, in ``--background`` runs, or if the add-on entry was removed).
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9876,
    "autostart": False,
    "service_url": "http://127.0.0.1:8765",
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
