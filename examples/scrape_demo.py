"""Birddog demo without a real Bright Data account.

Uses httpx's MockTransport to simulate Bright Data responses so the demo
runs offline and reviewers can exercise the leash deterministically.

Run:

    python examples/scrape_demo.py

Shows:
  1. happy path — fetch from an allowed domain
  2. domain denial — fetch a host outside the allowlist
  3. rate-limit denial — burst past the per-domain QPS cap
  4. audit log dump

Bytes + per-host metrics show up in the bundled Streamlit dashboard:

    streamlit run -m birddog.dashboard -- --audit runs/scrape_demo.jsonl
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager

import httpx

from birddog import Birddog, DomainDeniedError, RateLimitedError
from birddog import birddog as _birddog_mod


# ---- fake Bright Data backend (httpx MockTransport) ------------------------


def _fake_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    path = request.url.path
    if host == "docs.brightdata.com":
        html = f"<html><h1>Bright Data docs ({path})</h1><p>OK.</p></html>"
        return httpx.Response(200, html=html)
    if host == "shop.example.com":
        # mimic a product page response
        return httpx.Response(
            200,
            json={
                "url": str(request.url),
                "title": "Demo product",
                "price": 9.99,
                "currency": "USD",
            },
        )
    return httpx.Response(404, text="not found")


@contextmanager
def _mock_http(bd: Birddog):
    """Patch the session context-manager class so every Birddog session
    in the block uses an httpx.Client driven by MockTransport. Lets the
    demo run without any real network."""
    SessionCM = _birddog_mod._SessionCM
    real_enter = SessionCM.__enter__

    def patched_enter(self):
        session = real_enter(self)
        session._http.close()  # close the real httpx.Client
        session._http = httpx.Client(
            transport=httpx.MockTransport(_fake_handler),
            follow_redirects=True,
        )
        return session

    SessionCM.__enter__ = patched_enter
    try:
        yield
    finally:
        SessionCM.__enter__ = real_enter


# ---- demo ------------------------------------------------------------------


def main() -> None:
    audit_dir = "runs"
    if os.path.exists(audit_dir):
        shutil.rmtree(audit_dir)

    bd = Birddog(
        allowed_domains={"docs.brightdata.com", "*.example.com"},
        per_domain_qps=1.0,
        per_domain_burst=2.0,
        audit_path=f"{audit_dir}/scrape_demo.jsonl",
    )

    with _mock_http(bd):
        with bd.session("scrape-demo") as s:
            # 1. Happy path
            print("\n[1] Happy path — fetch docs.brightdata.com")
            r = s.fetch("https://docs.brightdata.com/api")
            print(f"    status={r.status} bytes={r.bytes_len} ms={r.elapsed_ms}")

            # 2. Domain denial
            print("\n[2] Domain denial — fetch evil.attacker.example")
            try:
                s.fetch("https://evil.attacker.example/exfil")
            except DomainDeniedError as e:
                print(f"    denied: {e}")

            # 3. Rate-limit denial: burst three fast hits on shop.example.com
            print("\n[3] Rate-limit denial — burst on shop.example.com (cap 1 qps, burst 2)")
            for i in range(4):
                try:
                    r = s.fetch(f"https://shop.example.com/products/{i}")
                    print(f"    [{i}] ok status={r.status}")
                except RateLimitedError as e:
                    print(f"    [{i}] denied: {e}")

            # 4. Summary
            print(
                f"\n[summary] fetches_ok={s.fetches_ok} fetches_denied={s.fetches_denied} bytes={s.bytes_total}"
            )

    print(f"\nAudit log written to: {audit_dir}/scrape_demo.jsonl")
    print("Open dashboard with:  streamlit run -m birddog.dashboard -- --audit runs/scrape_demo.jsonl")


if __name__ == "__main__":
    main()
