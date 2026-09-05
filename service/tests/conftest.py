"""Shared pytest fixtures.

The tests import the service straight out of the repo as the ``service``
package, so they run before anything is pip-installed as ``forge_service``.
Both names refer to the same directory; the relative imports inside the package
work either way.

Run them (once the venv exists) with::

    python -m pytest service/tests          # from the repo root
    python -m pytest                        # from service/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parent
SAMPLES_DIR = SERVICE_DIR / "samples"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture(scope="session")
def ring_band_source() -> str:
    """The reference sample script, as source text."""
    return (SAMPLES_DIR / "ring_band.py").read_text(encoding="utf-8")
