"""birddog - audited Bright Data agent runner.

Drop one context manager around an AI agent that consumes the live web,
and the agent's HTTP calls go through:

    1. domain allowlist  - deny everything outside it, log the attempt
    2. per-domain rate caps  - simple token bucket per host
    3. response audit log  - one JSONL line per fetch (url, status, bytes, ms)
    4. Bright Data proxy  - opt-in: route via Bright Data's Web Unlocker

Example:

    from birddog import Birddog

    bd = Birddog(
        allowed_domains={"docs.brightdata.com", "*.example.com"},
        per_domain_qps=1.0,
        bright_data={
            "host": "brd.superproxy.io:33335",
            "username": "brd-customer-...-zone-web_unlocker",
            "password": "...",
        },  # optional; omit to use plain httpx
        audit_path="runs/scrape.jsonl",
    )

    with bd.session() as s:
        html = s.fetch("https://docs.brightdata.com/api").text
        # too-fast follow-up hits the per-domain rate cap:
        s.fetch("https://docs.brightdata.com/pricing")  # RateLimitedError
        # disallowed:
        s.fetch("https://evil.example/exfil")  # DomainDeniedError

The session emits a JSONL audit log you can render in the bundled
Streamlit dashboard:

    streamlit run -m birddog.dashboard -- --audit runs/scrape.jsonl

`birddog` is the egress half of the @mukundakatta agent-stack. For
budget caps + tool-arg validation, pair with `agentleash`."""

from __future__ import annotations

from .birddog import (
    AuditEvent,
    Birddog,
    BirddogError,
    BirddogSession,
    DomainDeniedError,
    FetchResult,
    RateLimitedError,
)

# AttestConfig is re-exported as a convenience; the actual implementation
# only imports mantle-agent-attest lazily.
try:
    from .attest import AttestConfig
except Exception:  # pragma: no cover - import guard
    AttestConfig = None  # type: ignore[assignment]

__all__ = [
    "AttestConfig",
    "AuditEvent",
    "Birddog",
    "BirddogError",
    "BirddogSession",
    "DomainDeniedError",
    "FetchResult",
    "RateLimitedError",
]

__version__ = "0.2.1"
