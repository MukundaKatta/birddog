"""Tests for the optional `attest` hook on Birddog._SessionCM.

Requires `mantle-agent-attest`, `httpx` and `pytest` (pulled in by the
[dev] extra). When those are missing the module is skipped so a plain
``python3 -m unittest discover -s tests`` run still passes; the
dependency-free attestation helpers are covered by
``tests/test_pure.py``."""

from __future__ import annotations

import json
import unittest

try:
    import httpx
    import pytest

    from birddog import AttestConfig, Birddog
except ImportError as exc:  # pragma: no cover - exercised only without deps
    raise unittest.SkipTest(f"attestation deps unavailable: {exc}") from exc


# Same MockTransport trick used by tests/test_birddog.py
def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=f"hello from {request.url.host}{request.url.path}")


def _make_session(bd: Birddog, handler=_ok_handler):
    cm = bd.session("attest-test")
    s = cm.__enter__()
    s._http.close()
    s._http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    s._test_cm = cm  # type: ignore[attr-defined]
    return s


def _close(s) -> None:
    s._test_cm.__exit__(None, None, None)


# Deterministic test key — DO NOT use this anywhere real.
TEST_KEY = "0x" + "ab" * 32


def test_attest_hook_writes_session_attested_event(tmp_path):
    audit = tmp_path / "a.jsonl"
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=None,
        audit_path=str(audit),
        attest=AttestConfig(signer_key=TEST_KEY, run_id="run-test"),
    )
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/a")
        s.fetch("https://example.com/b")
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    kinds = [e["kind"] for e in events]
    # session_open + 2x fetch_ok + session_close + session_attested
    assert "session_open" in kinds
    assert kinds.count("fetch_ok") == 2
    assert "session_close" in kinds
    assert "session_attested" in kinds
    attested = next(e for e in events if e["kind"] == "session_attested")
    extra = attested["extra"]
    assert extra["run_id"] == "run-test"
    assert extra["root_hex"].startswith("0x")
    assert extra["signature"]
    assert extra["n_events"] == kinds.count("session_open") + kinds.count("fetch_ok") + kinds.count("session_close")
    assert extra["submitted_on_chain"] is False
    assert extra["signer"].startswith("0x")


def test_attest_failure_lands_in_error_event(tmp_path):
    audit = tmp_path / "a.jsonl"
    # Pass a bogus key to force the signer to throw on session-close.
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=None,
        audit_path=str(audit),
        attest=AttestConfig(signer_key="0xnot-a-real-key"),
    )
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/")
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    attested = [e for e in events if e["kind"] == "session_attested"]
    assert len(attested) == 1
    # The hook caught the failure, audit log is honest about it.
    assert "attestation_error" in attested[0]["extra"]


def test_no_attest_means_no_attestation_event(tmp_path):
    audit = tmp_path / "a.jsonl"
    bd = Birddog(
        allowed_domains={"example.com"},
        per_domain_qps=None,
        audit_path=str(audit),
    )
    s = _make_session(bd)
    try:
        s.fetch("https://example.com/")
    finally:
        _close(s)

    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    assert all(e["kind"] != "session_attested" for e in events)
