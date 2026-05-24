"""Cloud Run entrypoint for the birddog dashboard.

Wraps `birddog.dashboard:main` with a bundled sample audit log so
judges hitting the hosted URL see a populated dashboard immediately,
without having to run a Birddog session locally first.
"""

from __future__ import annotations

import os
import sys

# Point the dashboard at the bundled sample log. The dashboard reads
# from --audit so we hand it argv directly.
DEFAULT_AUDIT = os.environ.get("BIRDDOG_AUDIT", "runs/scrape_demo.jsonl")
sys.argv = [sys.argv[0], "--audit", DEFAULT_AUDIT]

from birddog.dashboard import main  # noqa: E402

if __name__ == "__main__":
    main()
