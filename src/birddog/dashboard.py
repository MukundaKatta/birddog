"""Tiny Streamlit dashboard for Birddog audit logs.

Run:

    streamlit run -m birddog.dashboard -- --audit runs/scrape.jsonl

(`pip install "birddog[dashboard]"` first.)"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    # Streamlit is an optional dep; import inside main() so importing
    # birddog.dashboard doesn't pull it in for non-dashboard users.
    import streamlit as st
    import pandas as pd

    audit = "runs/scrape.jsonl"
    for i, arg in enumerate(sys.argv):
        if arg == "--audit" and i + 1 < len(sys.argv):
            audit = sys.argv[i + 1]

    st.set_page_config(page_title="Birddog", page_icon="🐦", layout="wide")
    st.title("🐦 Birddog audit dashboard")
    st.caption(f"Source: `{audit}`")

    p = Path(audit)
    if not p.exists():
        st.warning(f"No audit file at `{audit}`. Run a Birddog session first.")
        return

    rows = _read_jsonl(p)
    if not rows:
        st.info("Audit file is empty.")
        return

    df = pd.DataFrame(rows)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fetches OK", int((df["kind"] == "fetch_ok").sum()))
    c2.metric("Denied (domain)", int((df["kind"] == "domain_denied").sum()))
    c3.metric("Denied (rate)", int((df["kind"] == "rate_limited").sum()))
    c4.metric("Total bytes", int(df.get("bytes", 0).sum()))

    st.subheader("By host")
    if "host" in df.columns:
        by_host = (
            df[df["kind"] == "fetch_ok"]
            .groupby("host")
            .agg(fetches=("url", "count"), bytes=("bytes", "sum"), p50_ms=("elapsed_ms", "median"))
            .sort_values("fetches", ascending=False)
        )
        st.dataframe(by_host, use_container_width=True)

    st.subheader("Recent events")
    cols = ["ts", "kind", "host", "url", "status", "bytes", "elapsed_ms", "error"]
    st.dataframe(
        df[[c for c in cols if c in df.columns]].tail(50).iloc[::-1],
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
