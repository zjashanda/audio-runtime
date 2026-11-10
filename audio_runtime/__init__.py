"""audio-runtime: one stable interface for audio endpoints, inspection and I/O.

The package is deliberately dependency-free. External programs (ffmpeg,
ffprobe, aplay, paplay, ffplay, OmniPickup) are optional capabilities detected
at runtime, never bundled binaries.
"""

from __future__ import annotations

__version__ = "0.1.0"
SCHEMA_PREFIX = "ark."

__all__ = ["__version__", "SCHEMA_PREFIX"]
