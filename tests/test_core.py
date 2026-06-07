"""Dependency-free unit tests for ``birddog._core``.

These exercise the real shipping logic that enforces a Birddog leash - the
domain allowlist matcher and the per-host token bucket - without importing
``httpx`` or any optional extra. The module is loaded directly from its
file path so that running it never triggers ``birddog/__init__.py`` (which
imports ``httpx``).

Run with the standard library only::

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.util
import pathlib
import time
import unittest

# Load src/birddog/_core.py directly, bypassing the package __init__ so that
# no third-party dependency is required to run this suite.
_CORE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src"
    / "birddog"
    / "_core.py"
)
_spec = importlib.util.spec_from_file_location("birddog_core_under_test", _CORE_PATH)
assert _spec is not None and _spec.loader is not None
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

host_allowed = _core.host_allowed
TokenBucket = _core.TokenBucket


class HostAllowedTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(host_allowed({"docs.brightdata.com"}, "docs.brightdata.com"))

    def test_exact_non_match(self):
        self.assertFalse(host_allowed({"docs.brightdata.com"}, "api.brightdata.com"))

    def test_wildcard_matches_subdomain(self):
        self.assertTrue(host_allowed({"*.example.com"}, "shop.example.com"))
        self.assertTrue(host_allowed({"*.example.com"}, "a.b.example.com"))

    def test_wildcard_does_not_match_apex(self):
        # "*.example.com" should not admit the bare apex "example.com".
        self.assertFalse(host_allowed({"*.example.com"}, "example.com"))

    def test_wildcard_does_not_match_suffix_confusable(self):
        # A naive endswith without the leading dot would wrongly allow these.
        self.assertFalse(host_allowed({"*.example.com"}, "evilexample.com"))
        self.assertFalse(host_allowed({"*.example.com"}, "notexample.com"))

    def test_empty_host_denied(self):
        self.assertFalse(host_allowed({"example.com"}, ""))

    def test_empty_allowlist_denies_everything(self):
        self.assertFalse(host_allowed(set(), "example.com"))

    def test_multiple_patterns(self):
        allow = {"example.com", "*.brightdata.com"}
        self.assertTrue(host_allowed(allow, "example.com"))
        self.assertTrue(host_allowed(allow, "docs.brightdata.com"))
        self.assertFalse(host_allowed(allow, "evil.test"))

    # ---- regression: case-insensitive matching --------------------------

    def test_uppercase_pattern_matches_lowercase_host(self):
        # DNS is case-insensitive: a mixed-case allowlist entry must still
        # admit the lowercased host produced by urlparse(...).hostname.
        self.assertTrue(host_allowed({"Docs.BrightData.com"}, "docs.brightdata.com"))

    def test_uppercase_host_matches_lowercase_pattern(self):
        self.assertTrue(host_allowed({"example.com"}, "EXAMPLE.com"))

    def test_case_insensitive_wildcard(self):
        self.assertTrue(host_allowed({"*.Example.COM"}, "shop.example.com"))


class TokenBucketTests(unittest.TestCase):
    def test_burst_then_empty(self):
        b = TokenBucket(capacity=2.0, refill_per_sec=0.0)
        self.assertTrue(b.try_take())   # 2 -> 1
        self.assertTrue(b.try_take())   # 1 -> 0
        self.assertFalse(b.try_take())  # empty

    def test_refill_over_time(self):
        # Fast refill so the test stays quick.
        b = TokenBucket(capacity=1.0, refill_per_sec=100.0)
        self.assertTrue(b.try_take())   # drain
        self.assertFalse(b.try_take())  # immediately empty
        time.sleep(0.05)                # ~5 tokens worth of refill
        self.assertTrue(b.try_take())   # refilled

    def test_capacity_caps_refill(self):
        b = TokenBucket(capacity=3.0, refill_per_sec=1000.0)
        time.sleep(0.05)  # would add 50 tokens uncapped
        self.assertLessEqual(b.tokens, 3.0)

    def test_cost_greater_than_one(self):
        b = TokenBucket(capacity=5.0, refill_per_sec=0.0)
        self.assertTrue(b.try_take(cost=3.0))   # 5 -> 2
        self.assertFalse(b.try_take(cost=3.0))  # only 2 left
        self.assertTrue(b.try_take(cost=2.0))   # 2 -> 0

    def test_tokens_property_does_not_spend(self):
        b = TokenBucket(capacity=2.0, refill_per_sec=0.0)
        _ = b.tokens
        _ = b.tokens
        self.assertTrue(b.try_take())
        self.assertTrue(b.try_take())
        self.assertFalse(b.try_take())


if __name__ == "__main__":
    unittest.main()
