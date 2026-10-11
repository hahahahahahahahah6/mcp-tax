"""v2 tests for mcp-tax (who-called, audit --all, gate).

Stdlib unittest; run with:  python3 -m unittest discover -s tests

Transcripts and home layouts are fabricated in tmp dirs and selected via
MCP_TAX_HOME / MCP_TAX_PROJECTS_DIR; the real ~/.claude is never touched.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_tax import attribution, cli, unified  # noqa: E402


def _rec(rtype, content, ts="2026-10-10T12:00:00.000Z", cwd="/tmp/proj",
         **extra):
    rec = {"type": rtype, "message": {"role": "assistant" if rtype == "assistant"
                                     else "user", "content": content},
           "timestamp": ts, "cwd": cwd}
    rec.update(extra)
    return rec


def _tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _text(t):
    return {"type": "text", "text": t}


def _tool_result(tid, text):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class AttributionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proj = os.path.join(self.tmp, "proj-a")
        os.makedirs(self.proj)
        records = [
            _rec("user", [_text("please check the failing CI job")]),
            _rec("assistant", [_tool_use("t1", "mcp__github__get_issue",
                                         {"repo": "x"})]),
            _rec("assistant", [_tool_use("t2", "Task",
                                         {"subagent_type": "researcher",
                                          "prompt": "go dig"})]),
            _rec("assistant", [_tool_use("t3", "mcp__github__search_code",
                                         {"q": "bug"})],
                 isSidechain=True),
            _rec("assistant", [_text("not an mcp call")]),
        ]
        _write_jsonl(os.path.join(self.proj, "sess.jsonl"), records)
        self.env = {"MCP_TAX_PROJECTS_DIR": self.tmp}

    def _run(self, **kw):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            return attribution.who_called(days=None, **kw)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_main_and_subagent_attribution(self):
        calls = self._run()
        by_tool = {c["tool"]: c for c in calls}
        self.assertEqual(by_tool["get_issue"]["agent"], "main")
        self.assertEqual(by_tool["search_code"]["agent"], "researcher")
        for c in calls:
            self.assertEqual(c["server"], "github")
            self.assertEqual(c["project"], "/tmp/proj")
            self.assertTrue(c["timestamp"].startswith("2026-10-10"))
        print("attribution agents ok: %s" % sorted(by_tool))

    def test_purpose_snippet_from_adjacent_text(self):
        calls = self._run()
        by_tool = {c["tool"]: c for c in calls}
        self.assertIn("failing CI job", by_tool["get_issue"]["purpose"])
        print("purpose ok: %r" % by_tool["get_issue"]["purpose"])

    def test_server_filter_and_limit(self):
        self.assertEqual(len(self._run(server="github")), 2)
        self.assertEqual(self._run(server="nope"), [])
        self.assertEqual(len(self._run(limit=1)), 1)
        print("filter/limit ok")

    def test_project_falls_back_to_slug(self):
        recs = [_rec("assistant", [_tool_use("t9", "mcp__s__tool", {})])]
        recs[0].pop("cwd")
        slugdir = os.path.join(self.tmp, "-Users-x-proj")
        os.makedirs(slugdir)
        _write_jsonl(os.path.join(slugdir, "s.jsonl"), recs)
        calls = self._run()
        self.assertTrue(any(c["project"] == "/Users/x/proj" for c in calls),
                        calls)
        print("slug fallback ok")


class CallStatsTest(unittest.TestCase):
    def test_counts_and_chars(self):
        tmp = tempfile.mkdtemp()
        proj = os.path.join(tmp, "p")
        os.makedirs(proj)
        records = [
            _rec("assistant", [_tool_use("a1", "mcp__srv__tool1",
                                         {"x": 1})]),
            _rec("user", [_tool_result("a1", "result text here")]),
            _rec("assistant", [_tool_use("a2", "mcp__srv__tool2", {})]),
            _rec("assistant", [_text("plain thinking")]),
        ]
        _write_jsonl(os.path.join(proj, "s.jsonl"), records)
        old = dict(os.environ)
        os.environ["MCP_TAX_PROJECTS_DIR"] = tmp
        try:
            calls, chars, per = attribution.call_stats(days=None)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(calls, 2)
        self.assertEqual(per["srv"]["calls"], 2)
        # result text must be counted
        self.assertGreaterEqual(per["srv"]["chars"], len("result text here"))
        self.assertEqual(chars, per["srv"]["chars"])
        print("call_stats ok: %r" % (per,))


class UnifiedTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, ".claude", "projects", "p",
                                 "memory"))
        with open(os.path.join(self.home, ".claude", "CLAUDE.md"), "w") as f:
            f.write("# Rules\n\nAlways do X.\nNever do Y.\n")
        with open(os.path.join(self.home, ".claude", "projects", "p",
                               "memory", "MEMORY.md"), "w") as f:
            f.write("some memory\n")
        sk = os.path.join(self.home, ".claude", "skills", "s1")
        os.makedirs(sk)
        with open(os.path.join(sk, "SKILL.md"), "w") as f:
            f.write("skill body text\n")
        self.projects = os.path.join(self.home, ".claude", "projects")
        os.makedirs(os.path.join(self.projects, "q"))
        _write_jsonl(os.path.join(self.projects, "q", "s.jsonl"),
                     [_rec("assistant", [_tool_use("z1", "mcp__k__t",
                                                   {"a": "b"})]),
                      _rec("user", [_tool_result("z1", "r" * 40)])])
        self.env = {"MCP_TAX_HOME": self.home,
                    "MCP_TAX_PROJECTS_DIR": self.projects}

    def _report(self):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            return unified.unified_audit(days=None)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_four_sources(self):
        rep = self._report()
        rows = {r["source"]: r for r in rep["rows"]}
        self.assertEqual(set(rows), {"rules", "memory", "skills",
                                    "mcp calls"})
        self.assertEqual(rows["rules"]["count"], 2)     # 2 directives
        self.assertEqual(rows["memory"]["count"], 1)    # 1 file
        self.assertEqual(rows["skills"]["count"], 1)    # 1 skill
        self.assertEqual(rows["mcp calls"]["count"], 1)
        self.assertGreater(rows["mcp calls"]["tokens"], 0)
        total_share = sum(r["share"] for r in rep["rows"])
        self.assertAlmostEqual(total_share, 100.0, places=0)
        self.assertEqual(rep["total_calls"], 1)
        self.assertEqual(rep["mcp_servers"]["k"]["calls"], 1)
        print("unified ok: %r" % rows)

    def test_empty_home(self):
        old = dict(os.environ)
        os.environ["MCP_TAX_HOME"] = tempfile.mkdtemp()
        os.environ["MCP_TAX_PROJECTS_DIR"] = tempfile.mkdtemp()
        try:
            rep = unified.unified_audit(days=None)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rep["total_tokens"], 0)
        self.assertEqual(rep["total_calls"], 0)
        print("unified empty ok")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", "CLAUDE.md"), "w") as f:
            f.write("a directive line here\n" * 10)
        self.env = {"MCP_TAX_HOME": self.home,
                    "MCP_TAX_PROJECTS_DIR": tempfile.mkdtemp()}

    def _gate(self, **kw):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            return unified.check_gate(days=None, **kw)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_pass_and_fail(self):
        rep, failures = self._gate(max_tokens=10**9)
        self.assertEqual(failures, [])
        rep, failures = self._gate(max_tokens=0)
        self.assertTrue(any("tokens" in f for f in failures))
        rep, failures = self._gate(max_tokens=10**9, max_calls=0)
        self.assertEqual(failures, [])  # 0 calls is not a breach
        print("gate pass/fail ok")


class CliTest(unittest.TestCase):
    def test_who_called_cli_json(self):
        tmp = tempfile.mkdtemp()
        proj = os.path.join(tmp, "p")
        os.makedirs(proj)
        _write_jsonl(os.path.join(proj, "s.jsonl"),
                     [_rec("user", [_text("hello")]),
                      _rec("assistant", [_tool_use("c1", "mcp__gh__issues",
                                                   {})])])
        old = dict(os.environ)
        os.environ["MCP_TAX_PROJECTS_DIR"] = tmp
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["who-called", "--server", "gh", "--json"])
            data = json.loads(buf.getvalue())
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rc, 0)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["tool"], "issues")
        self.assertEqual(data[0]["agent"], "main")
        print("cli who-called ok")

    def test_gate_cli_exit_codes(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".claude"))
        with open(os.path.join(home, ".claude", "CLAUDE.md"), "w") as f:
            f.write("a directive line here\n" * 10)
        old = dict(os.environ)
        os.environ["MCP_TAX_HOME"] = home
        os.environ["MCP_TAX_PROJECTS_DIR"] = tempfile.mkdtemp()
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["gate", "--max-tokens", "1"]), 1)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["gate", "--max-tokens",
                                           str(10**9)]), 0)
        finally:
            os.environ.clear()
            os.environ.update(old)
        print("cli gate exit codes ok")

    def test_audit_all_cli(self):
        old = dict(os.environ)
        os.environ["MCP_TAX_HOME"] = tempfile.mkdtemp()
        os.environ["MCP_TAX_PROJECTS_DIR"] = tempfile.mkdtemp()
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["audit", "--all"])
            out = buf.getvalue()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rc, 0)
        for word in ("rules", "memory", "skills", "mcp calls", "TOTAL"):
            self.assertIn(word, out)
        print("cli audit --all ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
