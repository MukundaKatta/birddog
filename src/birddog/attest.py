"""Optional on-session-close attestation for birddog audit logs, via
the `mantle-agent-attest` library.

When you pass an `attest=` config to `Birddog(...)`, birddog will:

  1. Read every event in the JSONL audit log when the session closes.
  2. Build a Merkle root over the events (canonical sha256 leaves).
  3. Sign the root with the agent's EVM private key (EIP-191 personal-sign).
  4. Emit a final `session_attested` event into the JSONL containing
     {run_id, signer, root_hex, signature, n_events}.
  5. Optionally POST the attestation on-chain to a deployed
     AgentAttestationRegistry on Mantle (if `submit_on_chain=True`).

This file imports mantle-agent-attest lazily so birddog stays usable
without the dep installed. Install with:

    pip install "birddog[attest]"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AttestConfig:
    """Configuration for on-session-close audit attestation.

    Fields:
        signer_key:  0x-prefixed EVM private key used to sign the Merkle root.
                     Read once at session-close time; never logged.
        run_id:      Optional. Defaults to session_id + start timestamp.
        submit_on_chain:  If True, post (runId, root, sig) to the
                          AgentAttestationRegistry contract on Mantle.
        registry_address: Contract address on Mantle (Sepolia or mainnet).
        rpc_url, chain_id:  Optional overrides for the RPC endpoint.
    """

    signer_key: str
    run_id: str | None = None
    submit_on_chain: bool = False
    registry_address: str | None = None
    rpc_url: str | None = None
    chain_id: int | None = None


def attest_jsonl(
    audit_path: str | Path,
    config: AttestConfig,
    session_id: str,
) -> dict[str, Any]:
    """Read every event from a birddog audit JSONL and attest the run.

    Returns a dict describing the attestation (suitable for embedding as
    the `extra` payload of a `session_attested` event). Raises if
    `mantle-agent-attest` isn't installed."""
    try:
        from mantle_agent_attest import build_attestation
    except Exception as e:  # pragma: no cover - import guard
        raise RuntimeError(
            'birddog attestation requires mantle-agent-attest. '
            'Install with: pip install "birddog[attest]" '
            'or: pip install mantle-agent-attest'
        ) from e

    path = Path(audit_path)
    if not path.exists():
        raise FileNotFoundError(f"birddog audit log not found at {path}")

    events: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # skip malformed lines rather than aborting the whole run
            continue

    run_id = config.run_id or f"{session_id}-{int(time.time())}"
    att = build_attestation(events, run_id=run_id, signer_key=config.signer_key)

    result: dict[str, Any] = {
        "run_id": att.run_id,
        "signer": att.signer,
        "root_hex": att.root_hex,
        "signature": att.signature,
        "n_events": att.n_events,
        "submitted_on_chain": False,
    }

    if config.submit_on_chain:
        try:
            from mantle_agent_attest.onchain import (
                MANTLE_SEPOLIA_CHAIN_ID,
                MANTLE_SEPOLIA_RPC,
                RegistryClient,
            )
        except Exception as e:
            raise RuntimeError(
                'on-chain submission requires the [onchain] extra: '
                'pip install "mantle-agent-attest[onchain]"'
            ) from e
        if not config.registry_address:
            raise ValueError(
                "AttestConfig.submit_on_chain=True requires registry_address."
            )
        client = RegistryClient(
            contract_address=config.registry_address,
            rpc_url=config.rpc_url or MANTLE_SEPOLIA_RPC,
            chain_id=config.chain_id or MANTLE_SEPOLIA_CHAIN_ID,
        )
        tx_hash = client.submit(att, sender_key=config.signer_key)
        result["submitted_on_chain"] = True
        result["registry_address"] = config.registry_address
        result["tx_hash"] = tx_hash
        result["chain_id"] = client.chain_id

    return result


def emit_attestation_event(
    audit_fp,
    session_id: str,
    attestation_extra: dict[str, Any],
) -> None:
    """Append a final `session_attested` event onto the audit JSONL.

    Pure helper; doesn't import anything from mantle-agent-attest, so
    even read-only consumers of birddog can parse the resulting JSONL."""
    event = {
        "ts": time.time(),
        "session_id": session_id,
        "kind": "session_attested",
        "url": None,
        "host": None,
        "status": None,
        "bytes": 0,
        "elapsed_ms": 0.0,
        "error": None,
        "extra": attestation_extra,
    }
    if audit_fp is not None:
        audit_fp.write(json.dumps(event, separators=(",", ":")) + "\n")
        audit_fp.flush()


# Env-driven helper for the demo script -------------------------------------


def attest_from_env(audit_path: str | Path, session_id: str) -> dict[str, Any] | None:
    """Build an attestation if `BIRDDOG_AGENT_KEY` is set in env.
    Returns None silently otherwise (so demos run unattested in CI)."""
    key = os.environ.get("BIRDDOG_AGENT_KEY")
    if not key:
        return None
    config = AttestConfig(
        signer_key=key,
        submit_on_chain=os.environ.get("BIRDDOG_SUBMIT_ON_CHAIN") == "1",
        registry_address=os.environ.get("MANTLE_REGISTRY"),
        rpc_url=os.environ.get("MANTLE_RPC_URL"),
        chain_id=int(os.environ["MANTLE_CHAIN_ID"]) if os.environ.get("MANTLE_CHAIN_ID") else None,
    )
    return attest_jsonl(audit_path, config, session_id)
