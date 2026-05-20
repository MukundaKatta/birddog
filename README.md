# birddog

Audited Bright Data egress for AI agents. Drop one context manager
around an agent that scrapes the web and you get:

1. **Domain allowlist** — deny everything outside it, log the attempt
2. **Per-domain rate caps** — simple token bucket per host
3. **Response audit log** — one JSONL line per fetch (url, status, bytes, ms)
4. **Bright Data Web Unlocker proxy** — opt-in: route via Bright Data
5. **Streamlit dashboard** — point it at the JSONL, get per-host bytes,
   denial counts, latency p50

Built for the kind of agent that hits live sites: research bots, price
trackers, RAG ingest jobs. If you've ever watched an agent rip
through a sponsor's free tier in 30 seconds, this is for you.

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

## Demo

```bash
python examples/scrape_demo.py
```

Runs offline against an `httpx.MockTransport` fake of Bright Data, so
you can see the allowlist, rate limit, and audit log behavior without
a real account.

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
