"""Validated user input for a selected video fragment."""

import math


def _seconds(text: str) -> float:
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("invalid timestamp")
    values = [float(part) for part in parts]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid timestamp")
    if len(parts) > 1 and any(v >= 60 for v in values[1:]):
        raise ValueError("invalid timestamp")
    if len(parts) > 1 and any(not v.is_integer() for v in values[:-1]):
        raise ValueError("invalid timestamp")
    result = 0.0
    for value in values:
        result = result * 60 + value
    return result


def parse_fragment(text: str, max_duration: int = 60) -> tuple[float, float] | None:
    """Accept 'start duration' as seconds, mm:ss or hh:mm:ss."""
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        start, duration = (_seconds(part) for part in parts)
    except (ValueError, OverflowError):
        return None
    if start > 86400 or not 0 < duration <= max_duration:
        return None
    return start, duration
