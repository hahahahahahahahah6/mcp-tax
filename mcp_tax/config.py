"""Config loading and the disabled-server set.

Servers come from Claude Code's configs:
  - global:  ~/.claude.json            (key "mcpServers")
  - project: ./.mcp.json              (key "mcpServers", wins on name clash)

The disabled set lives in ~/.config/mcp-tax/disabled.json and is honored
by `mcp-tax run` (and shown by `list` / `audit`).
"""

import json
import os

STATE_DIR_ENV = "MCP_TAX_STATE_DIR"
CLAUDE_JSON_ENV = "MCP_TAX_CLAUDE_JSON"
PROJECT_JSON_ENV = "MCP_TAX_PROJECT_JSON"


def state_dir():
    return os.environ.get(STATE_DIR_ENV, os.path.expanduser("~/.config/mcp-tax"))


def disabled_path(state=None):
    return os.path.join(state or state_dir(), "disabled.json")


def filtered_config_path(state=None):
    return os.path.join(state or state_dir(), "mcp-config.filtered.json")


def claude_json_path():
    return os.environ.get(CLAUDE_JSON_ENV, os.path.expanduser("~/.claude.json"))


def project_json_path():
    return os.environ.get(PROJECT_JSON_ENV, os.path.abspath("./.mcp.json"))


def load_servers(claude_json=None, project_json=None):
    """Return {name: {command, args, env, ...}} merged global + project.

    Project config wins when a name exists in both. The source is recorded
    in the private "_source" key ("global" / "project").
    """
    servers = {}
    for path, source in (
        (claude_json or claude_json_path(), "global"),
        (project_json or project_json_path(), "project"),
    ):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        entries = data.get("mcpServers") or {}
        if not isinstance(entries, dict):
            continue
        for name, spec in entries.items():
            if isinstance(spec, dict):
                entry = dict(spec)
                entry["_source"] = source
                servers[name] = entry
    return servers


def load_disabled(path=None):
    """Return the set of disabled server names (empty set if none)."""
    path = path or disabled_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    names = data.get("disabled") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return set()
    return {n for n in names if isinstance(n, str)}


def save_disabled(names, path=None):
    """Persist the disabled set (sorted for stable diffs)."""
    path = path or disabled_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"disabled": sorted(set(names))}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
