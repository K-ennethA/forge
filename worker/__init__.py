"""Forge local worker tier.

A dispatcher for local coding agents (Ollama-backed) that the Claude
orchestrator can hand chore-shaped work to, gate with a deterministic
verifier, and retry.  See ``worker/README.md`` for the protocol.
"""

__all__ = ["dispatch"]
