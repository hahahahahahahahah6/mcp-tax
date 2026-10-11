"""Who-called attribution: mine Claude Code transcripts for MCP tool calls.

Claude Code stores session transcripts as JSONL under
``~/.claude/projects/<project-slug>/*.jsonl``. Each line is a record with a
``type`` ("user" / "assistant" / ...), an API ``message`` carrying ``content``
blocks, and bookkeeping fields (``timestamp``, ``cwd``, ``isSidechain``,
``parentUuid``, ...). MCP tool calls show up as ``tool_use`` blocks whose
``name`` looks like ``mcp__<server>__<tool>``.

mcp-tax reads these files locally and never sends them anywhere. It answers
the question: "who is calling this MCP, using what agent, for what project,
and for what purpose?"

Attribution is heuristic, and the README says so:

- A subagent's work appears in sidechain records (``isSidechain: true``).
  When a ``Task`` tool call announces a ``subagent_type``, later sidechain
  records in the same session file are attributed to it. Sidechain records
  with no announced type are reported as ``subagent``; everything else is
  ``main``.
- The project comes from the record's ``cwd`` field when present, else from
  a best-effort decode of the project-dir slug (lossy: separators and dots
  both become dashes).
- The purpose is the nearest preceding user/assistant text, truncated.
  It is a hint, not a ground truth.

Env overrides (for tests and custom layouts):
  MCP_TAX_HOME          home dir (default ~)
  MCP_TAX_PROJECTS_DIR  transcript dir (default ~/.claude/projects)
"""

import glob
import json
import os
import time
from datetime import datetime

HOME_ENV = "MCP_TAX_HOME"
PROJECTS_DIR_ENV = "MCP_TAX_PROJECTS_DIR"

PURPOSE_CHARS = 160
INPUT_CHARS = 200
LOOKBACK = 8  # entries scanned back for a purpose snippet


def home_dir():
    return os.environ.get(HOME_ENV, os.path.expanduser("~"))


def projects_dir(home=None):
    base = home or home_dir()
    return os.environ.get(PROJECTS_DIR_ENV,
                          os.path.join(base, ".claude", "projects"))


def _decode_slug(slug):
    """Best-effort decode of a Claude Code project-dir slug to a path.

    Slugs replace separators (and dots) with dashes, so decoding is lossy;
    prefer the record's ``cwd`` field when present.
    """
    return "/" + slug.lstrip("-").replace("-", "/")


def _parse_ts(ts):
    """Parse a Claude Code ISO-8601 timestamp to epoch seconds."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _iter_records(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _text_of(content):
    """Concatenate text from API message content blocks."""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
    return " ".join(parts)


def _collapse(text):
    return " ".join(text.split())


def _split_mcp_name(name):
    """'mcp__server__tool' -> ('server', 'tool'); None if not an MCP tool."""
    if not isinstance(name, str) or not name.startswith("mcp__"):
        return None
    rest = name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


def _result_chars(block):
    """Estimate chars of a tool_result block (what the model had to read)."""
    content = block.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for part in content:
            if isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str):
                    n += len(t)
            elif isinstance(part, str):
                n += len(part)
        return n
    return 0


def _records_in(path, cutoff):
    for rec in _iter_records(path):
        if cutoff is not None:
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                try:
                    if _parse_ts(ts) < cutoff:
                        continue
                except ValueError:
                    pass
        yield rec


def who_called(server=None, projects=None, days=30, limit=None):
    """Attribute MCP tool calls from local transcripts, newest first.

    Returns a list of dicts: timestamp, project, agent ("main" / subagent
    type / "subagent"), server, tool, purpose (adjacent-text snippet),
    input (truncated tool input JSON). ``days`` bounds the lookback window
    (None = all history).
    """
    root = projects or projects_dir()
    cutoff = time.time() - days * 86400 if days else None
    calls = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        slug = os.path.basename(os.path.dirname(path))
        calls.extend(_calls_in_file(path, slug, cutoff, server))
    calls.sort(key=lambda c: c["timestamp"] or "", reverse=True)
    if limit is not None:
        calls = calls[:max(0, limit)]
    return calls


def _calls_in_file(path, slug, cutoff, server_filter):
    out = []
    subagent = None  # subagent_type announced by the latest Task tool call
    recent_texts = []  # ring buffer of recent text for purpose snippets
    for rec in _records_in(path, cutoff):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "Task"):
                    st = (block.get("input") or {}).get("subagent_type")
                    if isinstance(st, str) and st:
                        subagent = st
            for block in content:
                if not (isinstance(block, dict)
                        and block.get("type") == "tool_use"):
                    continue
                split = _split_mcp_name(block.get("name"))
                if not split:
                    continue
                srv, tool = split
                if server_filter and srv != server_filter:
                    continue
                if rec.get("isSidechain"):
                    agent = subagent or "subagent"
                else:
                    agent = "main"
                ts = rec.get("timestamp")
                out.append({
                    "timestamp": ts if isinstance(ts, str) else "",
                    "project": rec.get("cwd") or _decode_slug(slug),
                    "agent": agent,
                    "server": srv,
                    "tool": tool,
                    "purpose": _collapse(recent_texts[-1])[:PURPOSE_CHARS]
                    if recent_texts else "",
                    "input": _collapse(json.dumps(block.get("input") or {},
                                                  ensure_ascii=False,
                                                  sort_keys=True))[:INPUT_CHARS],
                })
        text = _text_of(content)
        if text.strip():
            recent_texts.append(text)
            del recent_texts[:-LOOKBACK]
    return out


def call_stats(projects=None, days=30):
    """Aggregate MCP call counts + char volumes per server from transcripts.

    chars = tool input JSON chars + matched tool_result text chars (what the
    model had to read back). Returns (total_calls, total_chars, per_server),
    where per_server maps server -> {"calls": n, "chars": n}.
    """
    root = projects or projects_dir()
    cutoff = time.time() - days * 86400 if days else None
    by_id = {}      # tool_use id -> server
    in_chars = {}   # tool_use id -> input chars
    out_chars = {}  # tool_use id -> result chars
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        for rec in _records_in(path, cutoff):
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            rtype = rec.get("type")
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if rtype == "assistant" and btype == "tool_use":
                    split = _split_mcp_name(block.get("name"))
                    if split and isinstance(block.get("id"), str):
                        by_id[block["id"]] = split[0]
                        in_chars[block["id"]] = len(
                            json.dumps(block.get("input") or {},
                                       ensure_ascii=False, sort_keys=True))
                elif rtype == "user" and btype == "tool_result":
                    tid = block.get("tool_use_id")
                    if isinstance(tid, str) and tid in by_id:
                        out_chars[tid] = out_chars.get(tid, 0) \
                            + _result_chars(block)
    per_server = {}
    for tid, srv in by_id.items():
        row = per_server.setdefault(srv, {"calls": 0, "chars": 0})
        row["calls"] += 1
        row["chars"] += in_chars.get(tid, 0) + out_chars.get(tid, 0)
    total_calls = sum(r["calls"] for r in per_server.values())
    total_chars = sum(r["chars"] for r in per_server.values())
    return total_calls, total_chars, per_server
