"""Tests for birddog.Birddog using httpx.MockTransport.

This is the full integration suite and needs the third-party ``httpx`` and
``pytest`` dependencies (installed via the ``[dev]`` extra). It is written
for pytest. When those dependencies are missing the whole module is skipped
so that a plain ``python3 -m unittest discover -s tests`` run still passes
(see ``tests/test_core.py`` / ``tests/test_pure.py`` for the
dependency-free, stdlib-only suite that always runs)."""

from __future__ import annotations

import json
import time
import unittest

try:
    import httpx
    import pytest

    from birddog import Birddog, DomainDeniedError, RateLimitedError
    from birddog.birddog import BirddogSession
except ImportError as exc:  # pragma: no cover - exercised only without deps
    raise unittest.SkipTest(f"integration deps unavailable: {exc}") from exc


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=f"hello from {request.url.host}{request.url.path}")


def _make_session(bd: Birddog, handler=_ok_handler) -> BirddogSession:
    """Build a session whose httpx client uses a MockTransport."""
    cm = bd.session("test")
    # Open the CM, then swap the http client for one with the mock transport.
    s = cm.__enter__()
    s._http.close()
    s._http = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )

    # Register the CM for cleanup at test teardown.
    s._test_cm = cm  # type: ignore[attr-defined]
    return s


def _close(s: BirddogSession) -> None:
    s._test_cm.__exit__(None, None, None)  # type: ignore[attr-defined]


# ---- happy path ------------------------------------------------------------


def test_fetch_ok_records_audit(tmp_path):
    audit = tmp_path / "a.jsonl"
    bd = Birddog(
        allowed_domains={"example.com"}, per_domain_qps=None, audit_path=str(audit)
    )
    s = _make_session(bd)
    try:
        r = s.fetch("https://example.com/page")
        assert r.status == 200
        assert s.fetches_ok == 1
        assert s.bytes_total > 0
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "session_open" in kinds
    assert "fetch_ok" in kinds
    assert "session_close" in kinds


def test_no_audit_when_path_is_none():
    bd = Birddog(allowed_domains={"example.com"}, per_domain_qps=None)
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/")
        assert s.fetches_ok == 1
    finally:
        _close(s)


# ---- domain allowlist ------------------------------------------------------


def test_exact_host_allowed():
    bd = Birddog(allowed_domains={"docs.brightdata.com"}, per_domain_qps=None)
    s = _make_session(bd)
    try:
        s.fetch("https://docs.brightdata.com/api")
    finally:
        _close(s)


def test_wildcard_subdomain_allowed():
    bd = Birddog(allowed_domains={"*.example.com"}, per_domain_qps=None)
    s = _make_session(bd)
    try:
        s.fetch("https://shop.example.com/foo")
        s.fetch("https://api.example.com/bar")
    finally:
        _close(s)


def test_denied_host_raises_and_audits(tmp_path):
    audit = tmp_path / "a.jsonl"
    bd = Birddog(allowed_domains={"example.com"}, per_domain_qps=None, audit_path=str(audit))
    s = _make_session(bd)
    try:
        with pytest.raises(DomainDeniedError):
            s.fetch("https://evil.example/exfil")
        assert s.fetches_denied == 1
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    assert any(e["kind"] == "domain_denied" for e in events)


def test_empty_allowlist_denies_all():
    bd = Birddog(per_domain_qps=None)
    s = _make_session(bd)
    try:
        with pytest.raises(DomainDeniedError):
            s.fetch("https://example.com/")
    finally:
        _close(s)


# ---- per-domain rate limit -------------------------------------------------


def test_rate_limit_fires_after_burst():
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=1.0,
        per_domain_burst=2.0,
    )
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/a")  # token 1 -> 1 left
        s.fetch("https://example.com/b")  # token 2 -> 0 left
        with pytest.raises(RateLimitedError):
            s.fetch("https://example.com/c")  # empty bucket
    finally:
        _close(s)


def test_rate_limit_refills_over_time():
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=50.0,  # fast refill so we don't sleep long in CI
        per_domain_burst=1.0,
    )
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/a")  # drains bucket
        # without sleep, second call is denied
        with pytest.raises(RateLimitedError):
            s.fetch("https://example.com/b")
        time.sleep(0.05)  # 50 qps * 0.05 = 2.5 tokens replenished
        s.fetch("https://example.com/c")  # should succeed
    finally:
        _close(s)


# ---- per-host independence -------------------------------------------------


def test_rate_limit_is_per_host():
    bd = Birddog(
        allowed_domains={"*.example.com"},
        per_domain_qps=1.0,
        per_domain_burst=1.0,
    )
    s = _make_session(bd)
    try:
        s.fetch("https://a.example.com/")  # drain a.example.com bucket
        s.fetch("https://b.example.com/")  # b.example.com bucket independent
        with pytest.raises(RateLimitedError):
            s.fetch("https://a.example.com/")  # a still empty
    finally:
        _close(s)


# ---- failed fetch audits ---------------------------------------------------


def test_http_error_is_logged(tmp_path):
    audit = tmp_path / "a.jsonl"

    def boom_handler(request):
        raise httpx.ConnectError("simulated network failure")

    bd = Birddog(allowed_domains={"example.com"}, per_domain_qps=None, audit_path=str(audit))
    s = _make_session(bd, handler=boom_handler)
    try:
        with pytest.raises(httpx.ConnectError):
            s.fetch("https://example.com/")
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    assert any(e["kind"] == "fetch_failed" for e in events)


# ---- nimble proxy config ---------------------------------------------------


def test_nimble_config_sets_via_nimble(tmp_path):
    """Birddog.nimble config → FetchResult.via_nimble=True."""
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=None,
        nimble={"username": "account-test-pipeline-p", "password": "secret"},
    )
    s = _make_session(bd)
    try:
        r = s.fetch("https://example.com/page")
        assert r.via_nimble is True
        assert r.via_brightdata is False
    finally:
        _close(s)


def test_nimble_session_open_records_proxy(tmp_path):
    """session_open extra should carry via_nimble=True when nimble config present."""
    audit = tmp_path / "a.jsonl"
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=None,
        audit_path=str(audit),
        nimble={"username": "account-test-pipeline-p", "password": "secret"},
    )
    s = _make_session(bd)
    _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    open_ev = next(e for e in events if e["kind"] == "session_open")
    assert open_ev["extra"]["via_nimble"] is True
    assert open_ev["extra"]["proxy"] == "nimble"
