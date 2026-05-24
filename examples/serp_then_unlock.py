"""Two-product Bright Data chain: SERP API + Web Unlocker, all leashed.

The watchdog example proves Web Unlocker works. This one proves the
deeper Bright Data integration: discover URLs via the **SERP API**,
then fetch each via **Web Unlocker**. Both stages go through the
birddog leash, so the audit log shows the full SERP-then-Unlock
chain with the right ``product`` tag on each row.

Run offline (no credits, no network — useful as a smoke test or for
populating the dashboard):

    python examples/serp_then_unlock.py

Run against real Bright Data zones (recommended once $250 hackathon
credits land):

    BIRDDOG_USE_BRIGHTDATA=1 \\
    BRIGHTDATA_HOST=brd.superproxy.io:33335 \\
    BRIGHTDATA_SERP_USERNAME=brd-customer-...-zone-serp_api \\
    BRIGHTDATA_SERP_PASSWORD=... \\
    BRIGHTDATA_UNLOCK_USERNAME=brd-customer-...-zone-web_unlocker \\
    BRIGHTDATA_UNLOCK_PASSWORD=... \\
    python examples/serp_then_unlock.py

Why two zones? Bright Data sells SERP and Web Unlocker as separate
products. Most agents only wire one. Wiring both — and proving the
audit shows each call tagged correctly — is the hackathon wedge.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from birddog import Birddog, BirddogError, FetchResult
from birddog import birddog as _birddog_mod


# ---- search queries to drive the chain ------------------------------------

QUERIES = [
    "site:linkedin.com staff software engineer remote 2026",
    "amazon.com bose qc35 best price",
    "indeed senior ml engineer hybrid san francisco",
]


# ---- offline mock so the demo runs without credits ------------------------


def _fake_serp_results(q: str) -> dict:
    """Pretend to be SERP API. Returns 3 organic results for any query."""
    norm = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:32]
    base_hosts = ["www.linkedin.com", "www.amazon.com", "www.indeed.com"]
    return {
        "query": q,
        "organic": [
            {
                "position": i + 1,
                "title": f"{q.title()} - result {i + 1}",
                "link": f"https://{host}/r/{norm}-{i + 1}",
                "snippet": f"This page is normally blocked by bots; "
                           f"Web Unlocker bypasses it cleanly.",
            }
            for i, host in enumerate(base_hosts)
        ],
    }


def _fake_unlock_html(url: str) -> str:
    return (
        "<html><body>"
        f"<h1>{url}</h1>"
        "<p>Fetched via Bright Data Web Unlocker. Original site blocks "
        "default scrapers; Bright Data renders a fresh page.</p>"
        "</body></html>"
    )


def _fake_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "serpapi.brightdata.com":
        q = request.url.params.get("q", "")
        return httpx.Response(200, json=_fake_serp_results(q))
    # Treat any other host as "fetched via Web Unlocker"
    return httpx.Response(200, html=_fake_unlock_html(str(request.url)))


def _install_mock_transport_on(SessionCM):
    real_enter = SessionCM.__enter__

    def patched_enter(self):
        session = real_enter(self)
        session._http.close()
        session._http = httpx.Client(
            transport=httpx.MockTransport(_fake_handler),
            follow_redirects=True,
        )
        return session

    SessionCM.__enter__ = patched_enter
    return real_enter


# ---- bright-data wiring ---------------------------------------------------


@dataclass
class BrightDataPair:
    """Two zone-credential pairs: one for SERP, one for Web Unlocker.

    Bright Data sells these as separate products. The hackathon wedge
    is proving birddog audits both correctly, with one ``product`` tag
    per row.
    """

    host: str
    serp_username: str
    serp_password: str
    unlock_username: str
    unlock_password: str


def _load_bright_data_pair() -> BrightDataPair | None:
    if os.environ.get("BIRDDOG_USE_BRIGHTDATA") != "1":
        return None
    return BrightDataPair(
        host=os.environ["BRIGHTDATA_HOST"],
        serp_username=os.environ["BRIGHTDATA_SERP_USERNAME"],
        serp_password=os.environ["BRIGHTDATA_SERP_PASSWORD"],
        unlock_username=os.environ["BRIGHTDATA_UNLOCK_USERNAME"],
        unlock_password=os.environ["BRIGHTDATA_UNLOCK_PASSWORD"],
    )


# ---- chain ----------------------------------------------------------------


def serp_search(session, query: str) -> list[str]:
    """Stage 1 — Bright Data SERP API call. Returns list of organic links."""
    url = f"https://serpapi.brightdata.com/search?q={httpx.QueryParams({'q': query})['q']}"
    try:
        r: FetchResult = session.fetch(url)
    except BirddogError as exc:
        print(f"  ! SERP refused by leash: {exc}")
        return []
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        print(f"  ? SERP returned non-JSON ({r.status})")
        return []
    return [hit["link"] for hit in data.get("organic", []) if hit.get("link")]


def unlock_fetch(session, url: str) -> FetchResult | None:
    """Stage 2 — Bright Data Web Unlocker call on one discovered URL."""
    try:
        return session.fetch(url)
    except BirddogError as exc:
        print(f"  ! Unlock refused by leash for {url}: {exc}")
        return None


def main() -> None:
    pair = _load_bright_data_pair()
    use_real = pair is not None

    # Two-zone wiring is one Birddog session per zone in production.
    # For demo simplicity we use one session and tag each fetch by URL
    # host: requests to serpapi.brightdata.com are SERP API, everything
    # else is Web Unlocker. The audit log preserves both stages.
    audit_dir = "runs"
    os.makedirs(audit_dir, exist_ok=True)
    audit_path = f"{audit_dir}/serp_then_unlock.jsonl"

    if use_real:
        bright_data_cfg = {
            "host": pair.host,
            "username": pair.unlock_username,
            "password": pair.unlock_password,
        }
    else:
        bright_data_cfg = None

    bd = Birddog(
        allowed_domains={
            "serpapi.brightdata.com",
            "www.linkedin.com",
            "www.amazon.com",
            "www.indeed.com",
        },
        per_domain_qps=2.0,
        per_domain_burst=3.0,
        audit_path=audit_path,
        bright_data=bright_data_cfg,
    )

    print("birddog SERP-then-Unlock chain")
    print(f"  mode:  {'real Bright Data zones' if use_real else 'offline MockTransport'}")
    print(f"  audit: {audit_path}")
    print()

    real_enter = None
    if not use_real:
        real_enter = _install_mock_transport_on(_birddog_mod._SessionCM)

    try:
        with bd.session("serp-unlock") as s:
            for q in QUERIES:
                print(f"== query: {q!r}")
                links = serp_search(s, q)
                for link in links:
                    r = unlock_fetch(s, link)
                    if r is not None:
                        print(f"  + {r.status} {link} ({len(r.text)} bytes)")
                time.sleep(0.4)  # pace between queries

            print()
            print(
                f"[summary] fetches_ok={s.fetches_ok} "
                f"fetches_denied={s.fetches_denied} "
                f"bytes={s.bytes_total}"
            )
            print(f"\naudit log: {audit_path}")
            print(
                "dashboard: streamlit run -m birddog.dashboard -- --audit",
                audit_path,
            )
    finally:
        if real_enter is not None:
            _birddog_mod._SessionCM.__enter__ = real_enter


if __name__ == "__main__":
    main()
