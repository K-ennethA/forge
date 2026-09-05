"""Forge geometry service.

Turns PartForge ``PARAMS`` scripts into printable Build123d solids and serves
them over HTTP on 127.0.0.1:8765.  See ``docs/architecture.md`` for the binding
wire contract.

The package is importable under two names:

* ``service``      -- when running straight from the repo (``python -m service.main``)
* ``forge_service`` -- when installed with ``pip install -e .`` from ``service/``

All intra-package imports are relative so both work unchanged.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
