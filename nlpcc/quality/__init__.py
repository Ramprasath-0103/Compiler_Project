"""Step 6: code quality analysis across the five dimensions."""

from .rules import analyze
from .score import Score, compute

__all__ = ["analyze", "compute", "Score"]
