"""Config loading and the disabled-server set.

Servers come from Claude Code's configs:
  - global:  ~/.claude.json            (key "mcpServers")
  - project: ./.mcp.json              (key "mcpServers")
  - local:   ~/.claude.json projects.<absolute cwd>.mcpServers
             (where `claude mcp add` stores servers by default)

On name clash, local wins over project, which wins over global.

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


def project_scope_key(cwd=None):
    """Absolute-path key Claude Code uses for this project's local scope."""
    return os.path.abspath(cwd if cwd is not None else os.getcwd())


def _merge_entries(servers, entries, source):
    if not isinstance(entries, dict):
        return
    for name, spec in entries.items():
        if isinstance(spec, dict):
            entry = dict(spec)
            entry["_source"] = source
            servers[name] = entry


def _local_scope_entries(claude_data, cwd=None):
    """mcpServers for this project's local scope, or None (fail-open).

    `claude mcp add` (the default scope) stores servers in ~/.claude.json
    under projects.<absolute cwd>.mcpServers. Anything unexpected in the
    shape of the file -> None, never a crash.
    """
    projects = claude_data.get("projects")
    if not isinstance(projects, dict):
        return None
    entry = projects.get(project_scope_key(cwd))
    if not isinstance(entry, dict):
        return None
    entries = entry.get("mcpServers")
    return entries if isinstance(entries, dict) else None


def load_servers(claude_json=None, project_json=None, cwd=None):
    """Return {name: {command, args, env, ...}} merged global + project + local.

    Priority on name clash: local > project > global. The source is recorded
    in the private "_source" key ("global" / "project" / "local").

    cwd is the directory whose local scope to read; defaults to the
    process's current working directory (as an absolute path, matching how
    Claude Code keys the projects section).
    """
    servers = {}

    try:
        with open(claude_json or claude_json_path(),
                   encoding="utf-8") as f:
            claude_data = json.load(f)
    except (OSError, ValueError):
        claude_data = None
    if isinstance(claude_data, dict):
        _merge_entries(servers, claude_data.get("mcpServers"), "global")
        local_entries = _local_scope_entries(claude_data, cwd)
    else:
        local_entries = None

    try:
        with open(project_json or project_json_path(),
                   encoding="utf-8") as f:
            project_data = json.load(f)
    except (OSError, ValueError):
        project_data = None
    if isinstance(project_data, dict):
        _merge_entries(servers, project_data.get("mcpServers"), "project")

    # local scope applied last so it wins on name clash
    _merge_entries(servers, local_entries, "local")
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
