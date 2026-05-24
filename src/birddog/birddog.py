"""Core Birddog session: domain allowlist + per-domain rate cap + audit
+ optional Bright Data Web Unlocker or Nimble proxy routing.

Synchronous (httpx.Client). Async (httpx.AsyncClient) would mirror this
straightforwardly; left out of v0.1 to keep the surface narrow."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx


# ---- Errors ----------------------------------------------------------------


class BirddogError(Exception):
    """Base class for every Birddog-raised failure."""


class DomainDeniedError(BirddogError):
    """Raised when a fetch targets a host outside the allowlist."""


class RateLimitedError(BirddogError):
    """Raised when a fetch would exceed the configured per-domain QPS cap."""


# ---- Audit + result --------------------------------------------------------


@dataclass
class AuditEvent:
    """One row in the audit log. JSONL-friendly."""

    ts: float
    session_id: str
    kind: str  # "fetch_ok" | "fetch_failed" | "domain_denied" | "rate_limited" | "session_open" | "session_close"
    url: str | None = None
    host: str | None = None
    status: int | None = None
    bytes: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + "\n"


@dataclass
class FetchResult:
    """What `session.fetch(...)` returns. Compact by design.

    `text` and `content` are the response body; `headers` is a copy of
    response headers; `via_brightdata` / `via_nimble` record which proxy
    (if any) routed the fetch."""

    url: str
    status: int
    text: str
    headers: dict[str, str]
    elapsed_ms: float
    via_brightdata: bool
    via_nimble: bool = False

    @property
    def bytes_len(self) -> int:
        return len(self.text.encode("utf-8", errors="ignore"))


# ---- Token bucket per host -------------------------------------------------


class _Bucket:
    """Tiny token-bucket. Capacity = burst; refill_per_sec = sustained QPS."""

    __slots__ = ("_capacity", "_refill", "_tokens", "_last")

    def __init__(self, capacity: float, refill_per_sec: float):
        self._capacity = capacity
        self._refill = refill_per_sec
        self._tokens = capacity
        self._last = time.monotonic()

    def try_take(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False


# ---- Birddog (config) ------------------------------------------------------


@dataclass
class Birddog:
    """Configuration. Create once per process, reuse across sessions."""

    allowed_domains: set[str] = field(default_factory=set)
    per_domain_qps: float | None = 1.0
    per_domain_burst: float = 3.0
    audit_path: str | None = None
    bright_data: dict[str, str] | None = None  # {"host": "brd.superproxy.io:33335", "username": "...", "password": "..."}
    nimble: dict[str, str] | None = None  # {"username": "account-X-pipeline-Y", "password": "..."}; routes via ip.nimbleway.com:7000
    timeout_seconds: float = 30.0
    attest: Any = None  # Optional birddog.attest.AttestConfig; on-close attestation via mantle-agent-attest

    def session(
        self, session_id: str | None = None
    ) -> "_SessionCM":
        """Open a Birddog session. Use as a `with` block."""
        import uuid as _uuid

        return _SessionCM(self, session_id or _uuid.uuid4().hex[:12])


# ---- BirddogSession --------------------------------------------------------


class BirddogSession:
    """Live session. Returned from `Birddog.session()`."""

    def __init__(self, bd: Birddog, session_id: str, audit_fp, http_client: httpx.Client):
        self.id = session_id
        self._bd = bd
        self._audit_fp = audit_fp
        self._http = http_client
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(bd.per_domain_burst, bd.per_domain_qps or 0.0)
        )
        self._fetches_total = 0
        self._fetches_denied = 0
        self._bytes_total = 0

    # ---- audit ----

    def _emit(self, event: AuditEvent) -> None:
        if self._audit_fp is not None:
            self._audit_fp.write(event.to_jsonl())
            self._audit_fp.flush()

    # ---- allowlist ----

    def _host_allowed(self, host: str) -> bool:
        for pat in self._bd.allowed_domains:
            if pat == host:
                return True
            if pat.startswith("*."):
                if host.endswith(pat[1:]):
                    return True
        return False

    # ---- fetch ----

    def fetch(self, url: str, *, method: str = "GET", **kwargs: Any) -> FetchResult:
        """GET (or other) a URL via httpx, optionally routed through Bright Data.

        Raises DomainDeniedError if host is outside the allowlist.
        Raises RateLimitedError if per-domain bucket is empty.
        Raises any httpx exception bubbling from the underlying request."""
        parsed = urlparse(url)
        host = parsed.hostname or ""

        # 1. domain allowlist
        if not self._host_allowed(host):
            self._fetches_denied += 1
            self._emit(
                AuditEvent(
                    ts=time.time(),
                    session_id=self.id,
                    kind="domain_denied",
                    url=url,
                    host=host,
                    error=f"host {host!r} not in allowlist",
                )
            )
            raise DomainDeniedError(f"host {host!r} not in allowlist")

        # 2. rate limit
        if self._bd.per_domain_qps is not None:
            if not self._buckets[host].try_take():
                self._fetches_denied += 1
                self._emit(
                    AuditEvent(
                        ts=time.time(),
                        session_id=self.id,
                        kind="rate_limited",
                        url=url,
                        host=host,
                        error=f"per-domain QPS cap reached for {host!r}",
                    )
                )
                raise RateLimitedError(f"per-domain QPS cap reached for {host!r}")

        # 3. fetch
        t0 = time.perf_counter()
        try:
            resp = self._http.request(method, url, timeout=self._bd.timeout_seconds, **kwargs)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            self._emit(
                AuditEvent(
                    ts=time.time(),
                    session_id=self.id,
                    kind="fetch_failed",
                    url=url,
                    host=host,
                    elapsed_ms=elapsed_ms,
                    error=str(e),
                )
            )
            raise

        text = resp.text
        body_bytes = len(text.encode("utf-8", errors="ignore"))
        self._fetches_total += 1
        self._bytes_total += body_bytes
        self._emit(
            AuditEvent(
                ts=time.time(),
                session_id=self.id,
                kind="fetch_ok",
                url=url,
                host=host,
                status=resp.status_code,
                bytes=body_bytes,
                elapsed_ms=elapsed_ms,
            )
        )
        return FetchResult(
            url=url,
            status=resp.status_code,
            text=text,
            headers=dict(resp.headers),
            elapsed_ms=elapsed_ms,
            via_brightdata=self._bd.bright_data is not None,
            via_nimble=self._bd.nimble is not None,
        )

    # ---- introspection ----

    @property
    def fetches_ok(self) -> int:
        return self._fetches_total

    @property
    def fetches_denied(self) -> int:
        return self._fetches_denied

    @property
    def bytes_total(self) -> int:
        return self._bytes_total


# ---- internal: context manager --------------------------------------------


class _SessionCM:
    def __init__(self, bd: Birddog, session_id: str):
        self._bd = bd
        self._session_id = session_id
        self._audit_fp = None
        self._http: httpx.Client | None = None
        self._session: BirddogSession | None = None

    def __enter__(self) -> BirddogSession:
        # audit log
        if self._bd.audit_path:
            os.makedirs(os.path.dirname(os.path.abspath(self._bd.audit_path)) or ".", exist_ok=True)
            self._audit_fp = open(self._bd.audit_path, "a", encoding="utf-8")

        # httpx client — optionally routed through Bright Data or Nimble proxy
        if self._bd.bright_data:
            bd = self._bd.bright_data
            proxy = f"http://{bd['username']}:{bd['password']}@{bd['host']}"
            self._http = httpx.Client(proxy=proxy, verify=False, follow_redirects=True)
        elif self._bd.nimble:
            nm = self._bd.nimble
            proxy = f"http://{nm['username']}:{nm['password']}@ip.nimbleway.com:7000"
            self._http = httpx.Client(proxy=proxy, verify=False, follow_redirects=True)
        else:
            self._http = httpx.Client(follow_redirects=True)

        self._session = BirddogSession(self._bd, self._session_id, self._audit_fp, self._http)
        proxy_label = (
            "bright_data" if self._bd.bright_data
            else "nimble" if self._bd.nimble
            else None
        )
        self._session._emit(
            AuditEvent(
                ts=time.time(),
                session_id=self._session.id,
                kind="session_open",
                extra={"via_brightdata": self._bd.bright_data is not None,
                       "via_nimble": self._bd.nimble is not None,
                       "proxy": proxy_label},
            )
        )
        return self._session

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self._session is not None and self._http is not None
        self._session._emit(
            AuditEvent(
                ts=time.time(),
                session_id=self._session.id,
                kind="session_close",
                bytes=self._session.bytes_total,
                extra={
                    "fetches_ok": self._session.fetches_ok,
                    "fetches_denied": self._session.fetches_denied,
                    "error": str(exc) if exc else None,
                },
            )
        )

        # Optional on-close attestation via mantle-agent-attest. Runs after
        # session_close so the attested JSONL covers the full run.
        if self._bd.attest is not None and self._bd.audit_path:
            from birddog.attest import attest_jsonl, emit_attestation_event

            # Flush + close the audit file before reading it back to attest.
            if self._audit_fp is not None:
                self._audit_fp.flush()
                self._audit_fp.close()
                self._audit_fp = None
            try:
                attestation = attest_jsonl(
                    self._bd.audit_path, self._bd.attest, self._session.id
                )
                # Reopen append for one final event so consumers see the root.
                fp = open(self._bd.audit_path, "a", encoding="utf-8")
                try:
                    emit_attestation_event(fp, self._session.id, attestation)
                finally:
                    fp.close()
            except Exception as e:
                # Don't let attestation failure kill the user's program.
                # Write a failure event so the log is honest.
                fp = open(self._bd.audit_path, "a", encoding="utf-8")
                try:
                    emit_attestation_event(
                        fp,
                        self._session.id,
                        {"attestation_error": str(e), "submitted_on_chain": False},
                    )
                finally:
                    fp.close()

        self._http.close()
        if self._audit_fp is not None:
            self._audit_fp.close()
        return False
