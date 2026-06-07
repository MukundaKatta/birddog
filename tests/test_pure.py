"""Dependency-free unit tests for the stdlib-only parts of birddog.

Covers:

* ``birddog.dashboard._read_jsonl`` - the JSONL reader the Streamlit
  dashboard is built on (parses without needing streamlit/pandas).
* ``birddog.attest.emit_attestation_event`` - the pure helper that appends
  a ``session_attested`` row to an audit log (no mantle-agent-attest needed).
* ``birddog.attest.AttestConfig`` - field defaults.

Each module is loaded directly from its file path (and registered in
``sys.modules`` so dataclasses resolve) so that importing the package
``__init__`` - which pulls in ``httpx`` - is never triggered.

Run with the standard library only::

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "birddog"


def _load(filename: str, modname: str):
    spec = importlib.util.spec_from_file_location(modname, _SRC / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses defined in the module resolve
    # their owning module via sys.modules.
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_dashboard = _load("dashboard.py", "birddog_dashboard_under_test")
_attest = _load("attest.py", "birddog_attest_under_test")


class ReadJsonlTests(unittest.TestCase):
    def test_parses_lines_and_skips_blanks(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "audit.jsonl"
            p.write_text(
                '{"kind":"session_open"}\n'
                "\n"  # blank line should be skipped
                '{"kind":"fetch_ok","bytes":42}\n'
                "   \n"  # whitespace-only line should be skipped
            )
            rows = _dashboard._read_jsonl(p)
        self.assertEqual([r["kind"] for r in rows], ["session_open", "fetch_ok"])
        self.assertEqual(rows[1]["bytes"], 42)

    def test_empty_file_yields_no_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "empty.jsonl"
            p.write_text("")
            self.assertEqual(_dashboard._read_jsonl(p), [])


class EmitAttestationEventTests(unittest.TestCase):
    def test_writes_well_formed_session_attested_row(self):
        buf = io.StringIO()
        extra = {"run_id": "run-1", "root_hex": "0xdead", "submitted_on_chain": False}
        _attest.emit_attestation_event(buf, "sess-1", extra)

        line = buf.getvalue().strip()
        event = json.loads(line)
        self.assertEqual(event["kind"], "session_attested")
        self.assertEqual(event["session_id"], "sess-1")
        self.assertEqual(event["extra"], extra)
        # Schema parity with other audit rows.
        for key in ("ts", "url", "host", "status", "bytes", "elapsed_ms", "error"):
            self.assertIn(key, event)

    def test_round_trips_through_read_jsonl(self):
        # The dashboard reader must be able to parse what attest emits.
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "audit.jsonl"
            with open(p, "w", encoding="utf-8") as fp:
                _attest.emit_attestation_event(fp, "sess-2", {"n_events": 3})
            rows = _dashboard._read_jsonl(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "session_attested")
        self.assertEqual(rows[0]["extra"]["n_events"], 3)

    def test_none_fp_is_a_noop(self):
        # Should not raise when there is no audit file.
        self.assertIsNone(_attest.emit_attestation_event(None, "sess", {"x": 1}))


class AttestConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = _attest.AttestConfig(signer_key="0xabc")
        self.assertEqual(cfg.signer_key, "0xabc")
        self.assertIsNone(cfg.run_id)
        self.assertFalse(cfg.submit_on_chain)
        self.assertIsNone(cfg.registry_address)


if __name__ == "__main__":
    unittest.main()
