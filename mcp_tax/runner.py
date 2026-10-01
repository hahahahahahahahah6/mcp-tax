"""Build the filtered MCP config and launch Claude Code with it.

`mcp-tax run -- <claude args>` writes {"mcpServers": {...}} minus the
disabled servers, then execs `claude --mcp-config <file>`.
"""

import json
import os
import shlex
import shutil

from . import config as config_mod


def strip_private(spec):
    """Drop mcp-tax's private bookkeeping keys before writing the config."""
    return {k: v for k, v in spec.items() if not k.startswith("_")}


def build_filtered_config(servers, disabled):
    """Return the {"mcpServers": ...} dict with disabled servers removed."""
    off = set(disabled)
    return {
        "mcpServers": {
            name: strip_private(spec)
            for name, spec in servers.items()
            if name not in off
        }
    }


def write_filtered_config(filtered, path=None):
    path = path or config_mod.filtered_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def build_command(filtered_path, claude_args):
    """The exact argv that `run` will exec. Pure function, easy to test.

    --strict-mcp-config is essential: --mcp-config alone only ADDS to the
    user's configured servers, so disabled servers would still load.
    """
    return ["claude", "--mcp-config", filtered_path,
            "--strict-mcp-config"] + list(claude_args)


def prepare(servers, disabled, claude_args, state=None):
    """Filter, write config, return (filtered_path, argv). No side effects
    beyond writing the filtered config file."""
    filtered = build_filtered_config(servers, disabled)
    path = write_filtered_config(
        filtered, config_mod.filtered_config_path(state))
    return path, build_command(path, claude_args)


def exec_claude(argv):
    """Replace this process with claude. Returns an exit code only when
    the claude CLI cannot be found (so callers can print the config path)."""
    if shutil.which("claude") is None:
        return None
    os.execvp("claude", argv)  # noqa: S606 - intentional, user's own CLI
    return 0  # unreachable


def format_command(argv):
    return " ".join(shlex.quote(a) for a in argv)
