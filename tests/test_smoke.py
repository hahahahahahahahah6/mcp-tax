"""Smoke tests for mcp-tax (stdlib only, pytest or plain python).

Run:  python3 -m pytest tests/ -q   # or: python3 tests/test_smoke.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_tax import cli, client, config, runner  # noqa: E402

FAKE_SERVER = os.path.join(os.path.dirname(__file__), "fake_mcp_server.py")
PY = sys.executable


def _spec(*env_pairs):
    spec = {"command": PY, "args": [FAKE_SERVER]}
    if env_pairs:
        spec["env"] = dict(env_pairs)
    return spec


def test_audit_math(tmp_path=None):
    """Handshake the fake server; assert exact tool count / chars / tokens."""
    res = client.audit_server("fake", _spec(), timeout=10)
    # Recompute the expected schema independently from the fixture's TOOLS.
    import importlib.util
    fu = importlib.util.spec_from_file_location("fake_mcp_server", FAKE_SERVER)
    mod = importlib.util.module_from_spec(fu)
    fu.loader.exec_module(mod)
    schema = json.dumps(mod.TOOLS, ensure_ascii=False, separators=(",", ":"))
    assert res["tool_count"] == len(mod.TOOLS) == 2, res
    assert res["schema_chars"] == len(schema), res
    assert res["est_tokens"] == client.estimate_tokens(len(schema)), res
    assert res["est_tokens"] == round(len(schema) / 4), res
    print("audit_math ok: tools=%d chars=%d tokens~%d"
          % (res["tool_count"], res["schema_chars"], res["est_tokens"]))


def test_audit_skips_stdout_noise():
    res = client.audit_server("noisy", _spec(("FAKE_NOISE", "1")), timeout=10)
    assert res["tool_count"] == 2, res
    print("audit_noise ok")


def test_audit_follows_next_cursor():
    """tools/list pagination: both pages must be counted."""
    res = client.audit_server("paged", _spec(("FAKE_PAGES", "1")), timeout=10)
    assert res["tool_count"] == 2, res
    print("audit_pagination ok")


def test_audit_timeout():
    try:
        client.audit_server("hang", _spec(("FAKE_HANG", "1")), timeout=2)
    except client.AuditError as exc:
        assert "within 2" in str(exc), exc
        print("audit_timeout ok: %s" % exc)
        return
    raise AssertionError("expected AuditError on hang")


def test_audit_missing_command():
    try:
        client.audit_server("bad", {"command": "definitely-not-a-real-bin-xyz"})
    except client.AuditError as exc:
        print("audit_missing_command ok: %s" % exc)
        return
    raise AssertionError("expected AuditError for missing command")


def test_on_off_filtering(tmp_path):
    state = str(tmp_path / "state") if tmp_path else None
    servers = {
        "a": {"command": "x", "_source": "global"},
        "b": {"command": "y", "_source": "project"},
    }
    config.save_disabled({"b"}, path=config.disabled_path(state))
    assert config.load_disabled(path=config.disabled_path(state)) == {"b"}
    filtered = runner.build_filtered_config(servers, {"b"})
    assert set(filtered["mcpServers"]) == {"a"}, filtered
    # private keys are stripped from the written config
    assert "_source" not in filtered["mcpServers"]["a"]
    # re-enable
    config.save_disabled(set(), path=config.disabled_path(state))
    assert config.load_disabled(path=config.disabled_path(state)) == set()
    filtered = runner.build_filtered_config(servers, set())
    assert set(filtered["mcpServers"]) == {"a", "b"}
    print("on_off_filtering ok")


def test_config_merge(tmp_path):
    base = getattr(tmp_path, "_base", None) or str(tmp_path)
    g = os.path.join(base, "claude.json")
    p = os.path.join(base, "mcp.json")
    with open(g, "w") as f:
        json.dump({"mcpServers": {
            "keep": {"command": "k"},
            "over": {"command": "old"}}}, f)
    with open(p, "w") as f:
        json.dump({"mcpServers": {"over": {"command": "new"}}}, f)
    servers = config.load_servers(claude_json=g, project_json=p)
    assert servers["keep"]["command"] == "k"
    assert servers["over"]["command"] == "new"  # project wins
    assert servers["over"]["_source"] == "project"
    # missing files -> empty, no crash
    assert config.load_servers(claude_json="/nope/x.json",
                               project_json="/nope/y.json") == {}
    print("config_merge ok")


def test_local_scope_merge(tmp_path):
    """projects.<cwd>.mcpServers (claude mcp add's default scope) is loaded,
    and local > project > global on name clash."""
    base = getattr(tmp_path, "_base", None) or str(tmp_path)
    g = os.path.join(base, "claude.json")
    p = os.path.join(base, "mcp.json")
    projdir = os.path.join(base, "proj")
    os.makedirs(projdir)
    with open(g, "w") as f:
        json.dump({
            "mcpServers": {
                "glob": {"command": "g"},
                "over": {"command": "global-old"},
            },
            "projects": {
                projdir: {"mcpServers": {
                    "loc": {"command": "l"},
                    "over": {"command": "local-new"},
                }},
                "/some/other/dir": {"mcpServers": {
                    "other": {"command": "o"},
                }},
            },
        }, f)
    with open(p, "w") as f:
        json.dump({"mcpServers": {
            "over": {"command": "project-mid"},
            "proj": {"command": "p"},
        }}, f)
    servers = config.load_servers(claude_json=g, project_json=p, cwd=projdir)
    # local-scope server shows up
    assert servers["loc"]["command"] == "l", servers
    assert servers["loc"]["_source"] == "local", servers
    # priority: local > project > global
    assert servers["over"]["command"] == "local-new", servers
    assert servers["over"]["_source"] == "local", servers
    # global and project still merge as before
    assert servers["glob"]["_source"] == "global", servers
    assert servers["proj"]["_source"] == "project", servers
    # a different project's local scope is ignored
    assert "other" not in servers, servers
    # cwd with no local entry: no crash, project still beats global
    servers2 = config.load_servers(claude_json=g, project_json=p,
                                   cwd=os.path.join(base, "nowhere"))
    assert "loc" not in servers2, servers2
    assert servers2["over"]["command"] == "project-mid", servers2
    assert servers2["over"]["_source"] == "project", servers2
    # malformed projects key: fail open, global still loads
    with open(g, "w") as f:
        json.dump({"mcpServers": {"glob": {"command": "g"}},
                   "projects": ["not", "a", "dict"]}, f)
    servers3 = config.load_servers(claude_json=g, project_json=p, cwd=projdir)
    assert servers3["glob"]["command"] == "g", servers3
    print("local_scope_merge ok")


def test_run_prepare(tmp_path):
    state = str(tmp_path / "s")
    servers = {"a": {"command": "ca", "args": ["--x"]},
               "b": {"command": "cb"}}
    path, argv = runner.prepare(servers, {"b"}, ["-p", "hello"], state=state,
                                strict=True)
    assert argv[:4] == [
        "claude", "--mcp-config", path, "--strict-mcp-config"
    ], argv
    assert argv[4:] == ["-p", "hello"], argv
    with open(path) as f:
        data = json.load(f)
    assert set(data["mcpServers"]) == {"a"}, data
    assert data["mcpServers"]["a"]["args"] == ["--x"]
    print("run_prepare ok: %s" % runner.format_command(argv))


def test_run_prepare_without_strict(tmp_path):
    """Strict isolation is opt-in and must not alter forwarded arguments."""
    state = str(tmp_path / "non-strict")
    path, argv = runner.prepare({}, set(), ["-p", "hello"], state=state)
    assert argv[:3] == ["claude", "--mcp-config", path], argv
    assert argv[3:] == ["-p", "hello"], argv
    assert "--strict-mcp-config" not in argv, argv


def test_run_strict_cli_flag():
    args = cli.build_parser().parse_args(
        ["run", "--strict", "--", "-p", "hello"])
    assert args.strict is True
    assert args.claude_args == ["--", "-p", "hello"]


class _Tmp:
    """Minimal stand-in for pytest's tmp_path (only `/` is used)."""
    def __init__(self, base):
        self._base = base

    def __truediv__(self, name):
        return os.path.join(self._base, name)


def _run_all():
    import tempfile
    tmp = _Tmp(tempfile.mkdtemp())
    test_audit_math()
    test_audit_skips_stdout_noise()
    test_audit_follows_next_cursor()
    test_audit_timeout()
    test_audit_missing_command()
    test_on_off_filtering(tmp)
    test_config_merge(tmp)
    test_local_scope_merge(tmp)
    test_run_prepare(tmp)
    test_run_prepare_without_strict(tmp)
    test_run_strict_cli_flag()
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
