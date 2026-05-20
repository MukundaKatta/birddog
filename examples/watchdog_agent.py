"""Birddog watchdog agent — realistic Bright Data demo.

A small price-tracker that polls a list of allowed product pages,
extracts a price with a basic regex, and yells when something
changes more than a threshold. Every fetch goes through the birddog
leash, so:

  - non-allowlisted hosts are dropped (denial logged)
  - per-domain QPS keeps the watchdog from hammering a single shop
  - the JSONL audit log records every fetch (real or denied)
  - if you set `BIRDDOG_USE_BRIGHTDATA=1` plus the standard
    `BRIGHTDATA_HOST / _USERNAME / _PASSWORD` env vars, traffic
    is routed through Bright Data's Web Unlocker proxy. Without
    those, the demo uses an httpx MockTransport so it runs offline.

Run:

    python examples/watchdog_agent.py

Or with a real Bright Data zone:

    BIRDDOG_USE_BRIGHTDATA=1 \\
    BRIGHTDATA_HOST=brd.superproxy.io:33335 \\
    BRIGHTDATA_USERNAME=brd-customer-...-zone-web_unlocker \\
    BRIGHTDATA_PASSWORD=... \\
    python examples/watchdog_agent.py
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field

import httpx

from birddog import Birddog, BirddogError, FetchResult
from birddog import birddog as _birddog_mod


# ---- watched product catalog ----------------------------------------------


@dataclass
class Product:
    url: str
    name: str
    threshold_pct: float = 5.0  # alert when |delta| > threshold_pct of last seen price
    last_price: float | None = None
    history: list[tuple[float, float]] = field(default_factory=list)  # (ts, price)


PRODUCTS = [
    Product("https://shop.example.com/p/widget-a", "Widget A", threshold_pct=3.0),
    Product("https://shop.example.com/p/widget-b", "Widget B", threshold_pct=5.0),
    Product("https://store.example.com/items/gizmo-9000", "Gizmo 9000", threshold_pct=10.0),
    # off-allowlist URL: the leash will drop this one
    Product("https://bargain-bin.example.io/widget-a", "Widget A (rogue mirror)"),
]


# ---- price extractor ------------------------------------------------------

PRICE_RE = re.compile(r"\$([0-9]+(?:\.[0-9]{1,2})?)")


def extract_price(text: str) -> float | None:
    m = PRICE_RE.search(text)
    return float(m.group(1)) if m else None


# ---- offline fake (MockTransport) -----------------------------------------


def _fake_handler(request: httpx.Request) -> httpx.Response:
    """Pretend to be the shop. Prices drift a little each call so we
    have something for the watchdog to alert on."""
    host = request.url.host
    path = request.url.path
    if host == "shop.example.com" and path.startswith("/p/"):
        sku = path.rsplit("/", 1)[-1]
        # deterministic-ish per-sku price with a small jitter
        base = {"widget-a": 19.99, "widget-b": 49.50}.get(sku, 12.34)
        drift = random.uniform(-0.10, 0.15) * base
        price = round(max(0.5, base + drift), 2)
        html = (
            f"<html><body><h1>{sku}</h1>"
            f"<div class='price'>${price}</div></body></html>"
        )
        return httpx.Response(200, html=html)
    if host == "store.example.com" and path.startswith("/items/"):
        sku = path.rsplit("/", 1)[-1]
        base = 89.00
        price = round(base + random.uniform(-15, 15), 2)
        return httpx.Response(
            200,
            json={"sku": sku, "price_usd": price, "currency": "USD",
                  "blurb": f"Best price: ${price}"},
        )
    return httpx.Response(404, text="not found")


def _install_mock_transport_on(SessionCM):
    """Monkey-patch _SessionCM.__enter__ to swap in MockTransport.
    Used for the offline demo path."""
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


# ---- watchdog loop --------------------------------------------------------


def run_one_pass(s, products: list[Product]) -> list[str]:
    """One pass over the watchlist. Returns alert strings for caller to print."""
    alerts: list[str] = []
    for p in products:
        try:
            r: FetchResult = s.fetch(p.url)
        except BirddogError as e:
            # domain denial or rate-limit — leash already audited it.
            alerts.append(f"  ! {p.name:<28}  blocked: {e}")
            continue
        except httpx.HTTPError as e:
            alerts.append(f"  ! {p.name:<28}  http error: {e}")
            continue

        price = extract_price(r.text)
        if price is None:
            alerts.append(f"  ? {p.name:<28}  no price found (status={r.status})")
            continue

        delta_pct = None
        if p.last_price is not None and p.last_price > 0:
            delta_pct = 100.0 * (price - p.last_price) / p.last_price

        flag = ""
        if delta_pct is not None and abs(delta_pct) >= p.threshold_pct:
            flag = f"  <-- ALERT (Δ {delta_pct:+.1f}% > {p.threshold_pct:.1f}%)"

        alerts.append(
            f"  + {p.name:<28}  ${price:>7.2f}"
            + (f"  (was ${p.last_price:.2f})" if p.last_price is not None else "")
            + flag
        )
        p.last_price = price
        p.history.append((time.time(), price))
    return alerts


def main() -> None:
    use_brightdata = os.environ.get("BIRDDOG_USE_BRIGHTDATA") == "1"
    bright_data_cfg = None
    if use_brightdata:
        bright_data_cfg = {
            "host": os.environ["BRIGHTDATA_HOST"],
            "username": os.environ["BRIGHTDATA_USERNAME"],
            "password": os.environ["BRIGHTDATA_PASSWORD"],
        }

    audit_dir = "runs"
    os.makedirs(audit_dir, exist_ok=True)
    audit_path = f"{audit_dir}/watchdog.jsonl"

    bd = Birddog(
        allowed_domains={"shop.example.com", "store.example.com"},
        per_domain_qps=2.0,        # at most 2 fetches/sec per host
        per_domain_burst=3.0,      # initial burst of 3
        audit_path=audit_path,
        bright_data=bright_data_cfg,
    )

    print("birddog watchdog agent")
    print(f"  proxy: {'Bright Data Web Unlocker' if use_brightdata else 'offline MockTransport'}")
    print(f"  audit: {audit_path}")
    print()

    real_enter = None
    if not use_brightdata:
        real_enter = _install_mock_transport_on(_birddog_mod._SessionCM)

    try:
        with bd.session("watchdog") as s:
            for pass_idx in range(3):
                print(f"--- pass {pass_idx + 1} ---")
                for line in run_one_pass(s, PRODUCTS):
                    print(line)
                time.sleep(0.6)  # pace passes; also lets the rate bucket refill

            print()
            print(
                f"[summary] fetches_ok={s.fetches_ok} "
                f"fetches_denied={s.fetches_denied} "
                f"bytes={s.bytes_total}"
            )
            print(f"\naudit log: {audit_path}")
            print("dashboard: streamlit run -m birddog.dashboard -- --audit", audit_path)
    finally:
        if real_enter is not None:
            _birddog_mod._SessionCM.__enter__ = real_enter


if __name__ == "__main__":
    random.seed(7)
    main()
