# birddog

[![PyPI](https://img.shields.io/pypi/v/birddog.svg)](https://pypi.org/project/birddog/)

![demo](docs/demo.gif)

**A leash for AI scraping agents.** One context manager wraps an agent
that hits the web and gives you:

1. **Domain allowlist** with wildcard subdomains, deny everything else
2. **Per-domain rate caps** via token bucket, one bucket per host
3. **JSONL audit log** with one line per fetch (url, status, bytes, ms)
4. **Bright Data Web Unlocker proxy** as an opt-in flag
5. **Streamlit dashboard** that reads the JSONL and shows per-host bytes,
   denials, and p50 latency

Built for research bots, price trackers, and RAG ingest jobs that hit
live sites. If you have watched an agent burn through a Bright Data
quota in 30 seconds because it wandered off-domain or hammered one
host, that is what birddog stops.

## Install

```bash
pip install birddog                    # core
pip install "birddog[dashboard]"       # + Streamlit dashboard
```

Python 3.10+.

## Why

LLM agents don't know what a sane scraping cadence looks like. They'll
hammer a site, ignore robots.txt, follow links into spammy subdomains,
and burn through a Bright Data quota in a single run.

`birddog` puts a leash on the egress side:

| Concern               | What birddog does                                  |
|-----------------------|----------------------------------------------------|
| Wandering off-domain  | Allowlist with `example.com` + `*.example.com`     |
| Burst scraping        | Token bucket per host (qps + burst)                |
| "What did it fetch?"  | JSONL audit log, one event per fetch               |
| Anti-bot blocks       | Optional Bright Data Web Unlocker proxy            |
| Post-run review       | Bundled Streamlit dashboard                        |

It does **not** parse HTML, manage cookies, render JS, or rotate user
agents. That's what Bright Data + your scraping code are for.

## Usage

```python
from birddog import Birddog

bd = Birddog(
    allowed_domains={"docs.brightdata.com", "*.example.com"},
    per_domain_qps=1.0,
    per_domain_burst=2.0,
    audit_path="runs/scrape.jsonl",
    # Optional — route through Bright Data Web Unlocker:
    bright_data={
        "host": "brd.superproxy.io:33335",
        "username": "brd-customer-...-zone-web_unlocker",
        "password": "...",
    },
)

with bd.session("research-bot") as s:
    r = s.fetch("https://docs.brightdata.com/api")
    print(r.status, r.bytes_len, "bytes")

    # second hit within 1s -> RateLimitedError (qps cap = 1)
    s.fetch("https://docs.brightdata.com/pricing")

    # off-allowlist -> DomainDeniedError, also logged
    s.fetch("https://evil.example/exfil")
```

`FetchResult` carries `url`, `status`, `text`, `headers`, `elapsed_ms`,
and a `via_brightdata` flag so downstream code can tell whether the
response came through the proxy.

## Audit log

One JSON object per line, e.g.:

```json
{"ts":1747779600.12,"session_id":"research-bot","kind":"fetch_ok",
 "url":"https://docs.brightdata.com/api","host":"docs.brightdata.com",
 "status":200,"bytes":4221,"elapsed_ms":312.4}
{"ts":1747779600.45,"session_id":"research-bot","kind":"domain_denied",
 "url":"https://evil.example/exfil","host":"evil.example",
 "error":"host 'evil.example' not in allowlist"}
```

Kinds: `session_open`, `fetch_ok`, `fetch_failed`, `domain_denied`,
`rate_limited`, `session_close`.

## Dashboard

```bash
pip install "birddog[dashboard]"
streamlit run -m birddog.dashboard -- --audit runs/scrape.jsonl
```

Shows total fetches, denials, bytes, and a per-host breakdown of
fetches + bytes + p50 latency.

## Demos

Two runnable examples in `examples/`:

**1. Smoke test — `scrape_demo.py`**

```bash
python examples/scrape_demo.py
```

Hits each feature once: happy path, domain denial, rate-limit burst,
summary. Offline via `httpx.MockTransport`.

**2. Realistic agent — `watchdog_agent.py`**

```bash
python examples/watchdog_agent.py
```

A small price-tracker agent. Polls a watchlist of product pages,
extracts prices, alerts when something moves more than a per-product
threshold. Three passes show:

- allowlist denials (off-domain mirror URL is dropped)
- per-domain rate cap kicking in on pass 3
- threshold alerts (`Δ -6.4% > 3.0%`)
- a `runs/watchdog.jsonl` audit log you can dashboard

Set `BIRDDOG_USE_BRIGHTDATA=1` + your Bright Data Web Unlocker env
vars to flip the demo to a real proxy.

## Companion libraries

`birddog` is the egress half of a small agent-stack:

- [agentleash](https://github.com/MukundaKatta/agentleash) — USD/call budget cap + tool-arg schema gate
- [agentvet](https://github.com/MukundaKatta/agentvet) — tool-arg validation with LLM-friendly retry hints
- [agentsnap](https://github.com/MukundaKatta/agentsnap) — snapshot tests for agent traces
- [agenttrace](https://github.com/MukundaKatta/agenttrace) — cost + latency aggregation per run

Pair `birddog` with `agentleash` and you have egress allowlist + budget
cap on the same agent.

## License

MIT
