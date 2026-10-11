"""Unified audit: one metric frame for the four always-on context sources.

Claude Code sessions always pay for four things, whether or not any MCP
server is configured:

  - rules:  CLAUDE.md directives (global ~/.claude/CLAUDE.md + ./CLAUDE.md)
  - memory: MEMORY.md files under ~/.claude
  - skills: ~/.claude/skills/*/SKILL.md
  - mcp:    actual MCP tool calls mined from transcripts (attribution.call_stats)

Each source reports (count, tokens, share%) so `mcp-tax audit --all` prints
one comparable table. `mcp-tax gate` turns the same numbers into a CI
pass/fail. Tokens are chars/4 everywhere, matching the schema estimate in
client.py — deliberately crude, documented in the README.
"""

import glob
import os

from . import attribution
from .client import estimate_tokens


def _home(home=None):
    return home or attribution.home_dir()


def audit_rules(home=None, cwd=None):
    """Count CLAUDE.md directives and their chars.

    A directive is a non-blank, non-heading line. Returns
    {"count": n, "chars": n}.
    """
    paths = [os.path.join(_home(home), ".claude", "CLAUDE.md")]
    if cwd:
        paths.append(os.path.join(cwd, "CLAUDE.md"))
    count, chars = 0, 0
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, ValueError):
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            count += 1
            chars += len(line)
    return {"count": count, "chars": chars}


def audit_memory(home=None):
    """Count memory files and their chars under ~/.claude.

    Files: ~/.claude/MEMORY.md, ~/.claude/projects/*/memory/MEMORY.md and
    ~/.claude/projects/*/MEMORY.md.
    """
    base = os.path.join(_home(home), ".claude")
    paths = [os.path.join(base, "MEMORY.md")]
    paths += glob.glob(os.path.join(base, "projects", "*", "memory",
                                    "MEMORY.md"))
    paths += glob.glob(os.path.join(base, "projects", "*", "MEMORY.md"))
    count, chars = 0, 0
    for path in sorted(set(paths)):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, ValueError):
            continue
        count += 1
        chars += len(text)
    return {"count": count, "chars": chars}


def audit_skills(home=None):
    """Count installed skills (SKILL.md) and their chars."""
    paths = glob.glob(os.path.join(_home(home), ".claude", "skills", "*",
                                   "SKILL.md"))
    count, chars = 0, 0
    for path in sorted(paths):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, ValueError):
            continue
        count += 1
        chars += len(text)
    return {"count": count, "chars": chars}


def unified_audit(home=None, cwd=None, projects=None, days=30):
    """Audit all four sources. Returns a report dict:

    {"rows": [{"source", "count", "tokens", "share"}], "total_tokens",
     "total_calls", "mcp_servers": {name: {"calls", "chars"}}}
    """
    rules = audit_rules(home=home, cwd=cwd)
    memory = audit_memory(home=home)
    skills = audit_skills(home=home)
    calls, mcp_chars, per_server = attribution.call_stats(projects=projects,
                                                          days=days)
    rows = [
        {"source": "rules", "count": rules["count"],
         "tokens": estimate_tokens(rules["chars"])},
        {"source": "memory", "count": memory["count"],
         "tokens": estimate_tokens(memory["chars"])},
        {"source": "skills", "count": skills["count"],
         "tokens": estimate_tokens(skills["chars"])},
        {"source": "mcp calls", "count": calls,
         "tokens": estimate_tokens(mcp_chars)},
    ]
    total = sum(r["tokens"] for r in rows)
    for r in rows:
        r["share"] = round(100.0 * r["tokens"] / total, 1) if total else 0.0
    return {"rows": rows, "total_tokens": total, "total_calls": calls,
            "mcp_servers": per_server}


def check_gate(max_tokens, max_calls=None, home=None, cwd=None,
               projects=None, days=30):
    """Evaluate a CI budget. Returns (report, failures).

    failures is a list of human-readable breach descriptions; empty = pass.
    """
    report = unified_audit(home=home, cwd=cwd, projects=projects, days=days)
    failures = []
    if report["total_tokens"] > max_tokens:
        failures.append("tokens %d > max %d"
                        % (report["total_tokens"], max_tokens))
    if max_calls is not None and report["total_calls"] > max_calls:
        failures.append("mcp calls %d > max %d"
                        % (report["total_calls"], max_calls))
    return report, failures
