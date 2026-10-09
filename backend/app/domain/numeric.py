"""Float comparison helpers. Never compare counters with == (CODE_STYLE §2.2)."""

from __future__ import annotations

import math

EPS = 1e-6


def lt(a: float, b: float) -> bool:
    return a < b - EPS


def le(a: float, b: float) -> bool:
    return a <= b + EPS


def gt(a: float, b: float) -> bool:
    return a > b + EPS


def ge(a: float, b: float) -> bool:
    return a >= b - EPS


def r2(value: float) -> float:
    """Round for presentation in params (2 decimals)."""
    return round(value + 0.0, 2)


def floor2(value: float) -> float:
    """Floor to 2 decimals — used for 'max allowed' hints so they are never over-optimistic."""
    return math.floor(value * 100 + EPS) / 100
