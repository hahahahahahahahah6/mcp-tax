"""v3 tests for mcp-tax (per-turn re-entry ledger, per-page wire counter).

Stdlib unittest; run with:  python3 -m unittest discover -s tests

The wire-counter tests replay the fake stdio MCP server fixture and assert
exact byte counts against independently reconstructed response lines.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_tax import cli, client, ledger  # noqa: E402

FAKE_SERVER = os.path.join(os.path.dirname(__file__), "fake_mcp_server.py")
PY = sys.executable


def _load_fixture_tools():
    fu = importlib.util.spec_from_file_location("fake_mcp_server_fixture",
                                                FAKE_SERVER)
    mod = importlib.util.module_from_spec(fu)
    fu.loader.exec_module(mod)
    return mod.TOOLS


TOOLS = _load_fixture_tools()


def _spec(*env_pairs):
    spec = {"command": PY, "args": [FAKE_SERVER]}
    if env_pairs:
        spec["env"] = dict(env_pairs)
    return spec


def _wire_line(obj):
    """Exact bytes the fixture writes for one JSON-RPC response line."""
    return len((json.dumps(obj) + "\n").encode("utf-8"))


def _rows(turns=5, cache_hit_rate=0.0):
    return ledger.build_ledger([
        {"name": "a", "ok": True, "tool_count": 10, "est_tokens": 100,
         "wire_bytes": 500, "wire_bytes_per_page": [500]},
        {"name": "b", "ok": True, "tool_count": 20, "est_tokens": 300,
         "wire_bytes": 900, "wire_bytes_per_page": [400, 500]},
        {"name": "dead", "ok": False, "error": "boom"},
    ], turns, cache_hit_rate)


class WireCounterTest(unittest.TestCase):
    def test_single_page_exact_bytes(self):
        res = client.audit_server("fake", _spec(), timeout=10)
        expect = _wire_line({"jsonrpc": "2.0", "id": 2,
                             "result": {"tools": TOOLS}})
        self.assertEqual(res["wire_bytes_per_page"], [expect], res)
        self.assertEqual(res["wire_bytes"], expect, res)
        self.assertEqual(res["tool_count"], 2)
        print("wire_single_page ok: %d bytes" % expect)

    def test_paged_per_page_breakdown(self):
        res = client.audit_server("paged", _spec(("FAKE_PAGES", "1")),
                                  timeout=10)
        page1 = _wire_line({"jsonrpc": "2.0", "id": 2,
                            "result": {"tools": [TOOLS[0]],
                                       "nextCursor": "page2"}})
        page2 = _wire_line({"jsonrpc": "2.0", "id": 3,
                            "result": {"tools": [TOOLS[1]]}})
        self.assertEqual(res["wire_bytes_per_page"], [page1, page2], res)
        self.assertEqual(res["wire_bytes"], page1 + page2, res)
        self.assertEqual(res["tool_count"], 2)
        print("wire_paged ok: pages=%r total=%d"
              % (res["wire_bytes_per_page"], res["wire_bytes"]))

    def test_wire_excludes_handshake_bytes(self):
        """The initialize response crossed the pipe but is not discovery
        wire: wire_bytes must be smaller than everything the server sent."""
        res = client.audit_server("fake", _spec(), timeout=10)
        init = _wire_line({"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp", "version": "0.0.1"}}})
        # one tools/list line only; the init line is not counted
        self.assertEqual(len(res["wire_bytes_per_page"]), 1)
        self.assertLess(res["wire_bytes"], res["wire_bytes"] + init)
        expect = _wire_line({"jsonrpc": "2.0", "id": 2,
                             "result": {"tools": TOOLS}})
        self.assertEqual(res["wire_bytes"], expect)
        print("wire_excludes_handshake ok")

    def test_startup_noise_not_billed_to_tools_list(self):
        """Stdout noise printed at server startup is consumed during the
        initialize read, so the tools/list wire counter is unaffected."""
        noisy = client.audit_server("noisy", _spec(("FAKE_NOISE", "1")),
                                    timeout=10)
        clean = client.audit_server("clean", _spec(), timeout=10)
        self.assertEqual(noisy["wire_bytes"], clean["wire_bytes"])
        self.assertEqual(noisy["wire_bytes_per_page"],
                         clean["wire_bytes_per_page"])
        print("wire_noise ok: %d bytes either way" % noisy["wire_bytes"])


class LedgerTest(unittest.TestCase):
    def test_per_turn_math(self):
        rep = _rows(turns=5)
        by = {s["name"]: s for s in rep["servers"]}
        self.assertEqual(by["a"]["preamble_tokens_per_turn"], 100)
        self.assertEqual(by["a"]["cumulative_window_tokens"], 500)
        self.assertEqual(by["b"]["cumulative_window_tokens"], 1500)
        t = rep["totals"]
        self.assertEqual(t["preamble_tokens_per_turn"], 400)
        self.assertEqual(t["cumulative_window_tokens"], 2000)
        self.assertEqual(t["wire_bytes"], 1400)
        self.assertEqual(t["tool_count"], 30)
        print("ledger_math ok: %r" % t)

    def test_old_model_undercounts_by_turns(self):
        """The correction: per-session accounting was the true cost / turns."""
        rep = _rows(turns=7)
        t = rep["totals"]
        self.assertEqual(t["cumulative_window_tokens"],
                         t["old_model_session_tokens"] * 7)
        print("ledger_correction ok: old=%d new=%d (7 turns)"
              % (t["old_model_session_tokens"], t["cumulative_window_tokens"]))

    def test_cache_discounts_billable_not_window(self):
        no_cache = _rows(turns=4, cache_hit_rate=0.0)
        cached = _rows(turns=4, cache_hit_rate=0.5)
        # window occupancy is identical: caching never shrinks the window
        self.assertEqual(cached["totals"]["cumulative_window_tokens"],
                         no_cache["totals"]["cumulative_window_tokens"])
        self.assertEqual(cached["totals"]["cacheable_tokens"],
                         cached["totals"]["cumulative_window_tokens"])
        # but the billable side is discounted: 50% hit x 90% discount
        window = no_cache["totals"]["cumulative_window_tokens"]
        expect_billable = int(round(window * (1.0 - 0.5 * 0.9)))
        self.assertEqual(cached["totals"]["billable_tokens"], expect_billable)
        self.assertLess(cached["totals"]["billable_tokens"], window)
        print("ledger_cache ok: billable=%d window=%d"
              % (cached["totals"]["billable_tokens"], window))

    def test_per_server_preamble_split(self):
        """Each server is an independent preamble billing line."""
        rep = _rows(turns=3)
        self.assertEqual([s["name"] for s in rep["servers"]], ["a", "b"])
        for s in rep["servers"]:
            self.assertEqual(s["cumulative_window_tokens"],
                             s["preamble_tokens_per_turn"] * 3)
        self.assertEqual(rep["totals"]["preamble_tokens_per_turn"],
                         sum(s["preamble_tokens_per_turn"]
                             for s in rep["servers"]))
        print("ledger_split ok")

    def test_failed_servers_excluded(self):
        rep = _rows()
        self.assertNotIn("dead", [s["name"] for s in rep["servers"]])
        print("ledger_failed_excluded ok")

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            ledger.build_ledger([], turns=0)
        with self.assertRaises(ValueError):
            ledger.build_ledger([], turns=2, cache_hit_rate=1.5)
        with self.assertRaises(ValueError):
            ledger.build_ledger([], turns=2, cache_hit_rate=-0.1)
        print("ledger_validation ok")


class AuditCliV3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        claude_json = os.path.join(self.tmp, "claude.json")
        with open(claude_json, "w") as f:
            json.dump({"mcpServers": {
                "fake": {"command": PY, "args": [FAKE_SERVER]}}}, f)
        self.env = {
            "MCP_TAX_CLAUDE_JSON": claude_json,
            "MCP_TAX_PROJECT_JSON": os.path.join(self.tmp, "no-mcp.json"),
            "MCP_TAX_STATE_DIR": os.path.join(self.tmp, "state"),
        }

    def _main(self, argv):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(argv)
            return rc, buf.getvalue()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_audit_json_has_ledger(self):
        rc, out = self._main(["audit", "--turns", "3", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["turns"], 3)
        self.assertEqual(data["window_tokens"], 200_000)
        row = data["rows"][0]
        for key in ("wire_bytes", "wire_bytes_per_page",
                    "preamble_tokens_per_turn", "cumulative_window_tokens",
                    "cacheable_tokens", "billable_tokens",
                    "old_model_session_tokens"):
            self.assertIn(key, row, key)
        self.assertEqual(row["cumulative_window_tokens"],
                         row["preamble_tokens_per_turn"] * 3)
        self.assertEqual(row["wire_bytes"],
                         sum(row["wire_bytes_per_page"]))
        print("cli_audit_json ok")

    def test_audit_text_shows_per_turn_reentry(self):
        rc, out = self._main(["audit", "--turns", "3"])
        self.assertEqual(rc, 0)
        self.assertIn("fake", out)
        self.assertIn("EVERY turn", out)
        self.assertIn("3 turns", out)
        print("cli_audit_text ok")

    def test_audit_cache_flag(self):
        rc, out = self._main(["audit", "--turns", "2",
                              "--cache-hit-rate", "0.5"])
        self.assertEqual(rc, 0)
        self.assertIn("caching cuts price", out)
        print("cli_audit_cache ok")

    def test_audit_rejects_zero_turns(self):
        rc, out = self._main(["audit", "--turns", "0"])
        self.assertEqual(rc, 2)
        print("cli_audit_turns_validation ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
