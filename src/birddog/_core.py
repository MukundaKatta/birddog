"""Pure, dependency-free building blocks for Birddog.

This module deliberately imports nothing beyond the standard library so it
can be reused (and unit-tested) without pulling in ``httpx`` or any of the
optional extras. The two pieces of logic that actually enforce a Birddog
"leash" live here:

* :func:`host_allowed` - the domain allowlist matcher.
* :class:`TokenBucket` - the per-host rate limiter.

``birddog.birddog`` imports both, so the behaviour exercised by the
``httpx``-backed session is exactly what is tested here.
"""

from __future__ import annotations

import time
from typing import Iterable

__all__ = ["host_allowed", "TokenBucket"]


def host_allowed(allowed_domains: Iterable[str], host: str) -> bool:
    """Return ``True`` if ``host`` matches the allowlist.

    A pattern matches when it equals the host exactly, or when it is a
    ``*.`` wildcard whose suffix the host ends with. For example
    ``"*.example.com"`` matches ``"shop.example.com"`` but not the bare
    apex ``"example.com"`` (mirroring how most certificate/cookie wildcard
    rules treat a single label).

    Matching is **case-insensitive**: DNS hostnames are case-insensitive,
    so an allowlist entry of ``"Docs.BrightData.com"`` must still admit a
    request to ``"docs.brightdata.com"``. Both the patterns and the host
    are normalised to lowercase before comparison.

    An empty host never matches (URLs without a hostname are denied).
    """
    if not host:
        return False
    host = host.lower()
    for pat in allowed_domains:
        pat = pat.lower()
        if pat == host:
            return True
        if pat.startswith("*.") and host.endswith(pat[1:]):
            return True
    return False


class TokenBucket:
    """A minimal token bucket.

    ``capacity`` is the burst size (the most requests allowed back-to-back)
    and ``refill_per_sec`` is the sustained rate at which tokens are
    replenished. Each :meth:`try_take` call lazily refills based on the
    wall-clock time elapsed since the previous call, then spends ``cost``
    tokens if enough are available.
    """

    __slots__ = ("_capacity", "_refill", "_tokens", "_last")

    def __init__(self, capacity: float, refill_per_sec: float):
        self._capacity = capacity
        self._refill = refill_per_sec
        self._tokens = capacity
        self._last = time.monotonic()

    def try_take(self, cost: float = 1.0) -> bool:
        """Spend ``cost`` tokens if available; return whether it succeeded."""
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    @property
    def tokens(self) -> float:
        """Current token count (refilled to *now*). Useful for tests/introspection."""
        now = time.monotonic()
        return min(self._capacity, self._tokens + (now - self._last) * self._refill)
