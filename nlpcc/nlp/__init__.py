"""Natural-language understanding of what the user is asking for."""

from .intent import ACTIONS, Intent, detect

__all__ = ["detect", "Intent", "ACTIONS"]
