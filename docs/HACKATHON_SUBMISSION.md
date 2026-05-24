# Bright Data Web Data UNLOCKED 2026 - Submission Draft

Hackathon: Web Data UNLOCKED (May 25 to 31, 2026)
Host: lablab.ai (Bright Data sponsor track)
Submission URL (expected): https://lablab.ai/event/web-data-unlocked-hackathon/submit
GitHub: https://github.com/MukundaKatta/birddog

## Title
birddog: a leash for AI scraping agents

## Tagline (one line)
Audited Bright Data egress for AI agents: allowlist, rate caps, JSONL audit, Streamlit dashboard, opt-in Web Unlocker.

## Description (under 500 words, plain English, no em dashes)

Modern AI agents are great at deciding what to fetch and bad at deciding how often. Give a research bot a list of seed URLs and it will follow links into spammy subdomains, hit the same shop 40 times in a minute, and burn a Bright Data quota in one run. The fix is not "tell the agent to be careful." The fix is a leash on the egress side.

birddog is that leash. It is one Python context manager that wraps any scraping code an agent runs. Inside the block, every outbound HTTP request goes through four gates:

1. Domain allowlist. You declare which hosts the agent is allowed to touch, with wildcard subdomain support. Anything else raises DomainDeniedError and is logged.
2. Per-domain rate cap. A simple token bucket per host, configurable QPS and burst. Too many fetches to one site raise RateLimitedError before the request goes out.
3. JSONL audit log. One line per event: fetch_ok, fetch_failed, domain_denied, rate_limited, session_open, session_close. Every line carries url, host, status, bytes, elapsed_ms, and the session id.
4. Optional Bright Data Web Unlocker proxy. Pass a host, username, and password and birddog routes the httpx client through the Unlocker for you. No SDK to learn. The audit log records a via_brightdata flag on every fetch so you can prove which traffic went through the proxy.

birddog ships a bundled Streamlit dashboard that reads the JSONL log and shows total fetches, denial counts, total bytes, and a per-host breakdown of fetches plus bytes plus p50 latency. Point it at any run and you can answer "what did this agent do" in 10 seconds.

Why this is different from a stock scraper or a logging wrapper:

- A stock scraper does not stop the agent. birddog raises before the request fires.
- A logging wrapper records what happened. birddog enforces policy and records the enforcement.
- The audit log is structured, append-only, and dashboard-ready out of the box.
- The Bright Data integration is one optional dict, not a fork of the library. Without it, birddog still works as a local rate-limited fetcher with audit.

The repo includes a real demo: a small watchdog price tracker that polls a list of product pages, extracts prices, and alerts on changes above a per-product threshold. Three passes show allowlist denials on a rogue mirror URL, the per-domain rate cap kicking in, threshold alerts, and a JSONL audit log you can dashboard immediately. Set BIRDDOG_USE_BRIGHTDATA=1 plus the standard env vars to flip the same demo onto a real Web Unlocker zone.

For reviewers who prefer a guided tour, `examples/birddog_walkthrough.ipynb` is an executed Jupyter notebook that walks through allowlist denial, rate-limit denial, audit log aggregation in pandas, a matplotlib bar chart, and a commented Bright Data wiring cell. The notebook runs offline through httpx.MockTransport so it is reproducible without a live Bright Data zone.

13 tests, MIT licensed, Python 3.10 and up, single runtime dependency (httpx). pip install birddog and you get the core library. pip install "birddog[dashboard]" adds the Streamlit dashboard.

Repo: https://github.com/MukundaKatta/birddog

## Built with
Python, httpx, Bright Data Web Unlocker, Streamlit, pandas, matplotlib, Jupyter

## How does it use Bright Data
Optional Web Unlocker proxy routing. Pass host + username + password to the Birddog config and every fetch in the session goes through Bright Data's proxy. The audit log records a `via_brightdata` flag per fetch so downstream tooling can prove which traffic went through the Unlocker.

## Demo video script (do not record yet)
Length target: under 3 minutes. Show:

1. README open in browser, scroll first screen, read the 5 bullet value list out loud. (15 seconds)
2. Terminal: `python examples/watchdog_agent.py`. Point out the denial line on the rogue mirror URL. Point out the rate-limit line on pass 3. Point out the threshold alert. (60 seconds)
3. Terminal: `streamlit run -m birddog.dashboard -- --audit runs/watchdog.jsonl`. Switch to browser. Show the four metric boxes at the top, the per-host table, the recent events list. (45 seconds)
4. Editor: open `src/birddog/birddog.py` to the `fetch` method. Read the three-gate flow out loud (allowlist, bucket, fetch). (30 seconds)
5. Closing line: "One context manager, four gates, one audit log. Drop it around any agent that hits Bright Data."

## Cover image
`docs/dashboard.png` - captured headless screenshot of the Streamlit dashboard showing the four metric boxes (Fetches OK 24, Denied domain 9, Denied rate 3, Total bytes 3732), the per-host table, and the recent events feed against the bundled `runs/watchdog.jsonl` sample.

## Tags (lablab submission)
ai-agents, bright-data, web-scraping, observability, rate-limiting, python, llm-tooling

## Word count for the description above
Verified under 500 words.
