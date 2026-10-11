"""Minimal MCP stdio client for `mcp-tax audit`.

Speaks newline-delimited JSON-RPC 2.0 over stdio (the framing MCP stdio
servers use): initialize -> notifications/initialized -> tools/list.
Then it measures the tool schemas and estimates their context cost.

Token estimate: schema_chars / 4, where schema_chars is the length of the
compact JSON encoding of the full tools array. Crude on purpose — see README.
"""

import json
import os
import select
import shutil
import subprocess
import time

from . import __version__

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "mcp-tax"
DEFAULT_TIMEOUT = 15


class AuditError(Exception):
    """Raised when a server cannot be audited (spawn, handshake, timeout)."""


def estimate_tokens(chars):
    """Rough token estimate for schema text. Documented in README."""
    return int(round(chars / 4.0))


def _send(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _read_response(proc, want_id, deadline):
    """Read stdout until the JSON-RPC response with want_id arrives.

    Skips blank lines and non-JSON noise (some servers log to stdout).
    Returns (response dict, wire_bytes), or (None, wire_bytes) on
    timeout / EOF. wire_bytes counts every byte consumed while waiting
    for this response — including skipped noise lines, which also
    crossed the pipe.
    """
    buf = ""
    wire_bytes = 0
    fd = proc.stdout.fileno()
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([fd], [], [], max(0.0, remaining))
        if not ready:
            return None, wire_bytes  # timeout
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return None, wire_bytes
        if not chunk:
            return None, wire_bytes  # EOF
        buf += chunk.decode("utf-8", errors="replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            # one wire byte per char plus the newline that terminated it
            wire_bytes += len(line.encode("utf-8", errors="replace")) + 1
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # stdout noise, not a protocol message
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg, wire_bytes
    return None, wire_bytes


def audit_server(name, spec, timeout=DEFAULT_TIMEOUT):
    """Handshake one server and measure its tools.

    Returns {"name", "tool_count", "schema_chars", "est_tokens"}.
    Raises AuditError on any failure; the process is always reaped.
    """
    command = spec.get("command")
    args = list(spec.get("args") or [])
    if not command:
        raise AuditError("no 'command' in server config")
    if shutil.which(command) is None and not os.path.exists(command):
        raise AuditError("command not found: %s" % command)

    env = dict(os.environ)
    extra_env = spec.get("env") or {}
    if isinstance(extra_env, dict):
        env.update({k: str(v) for k, v in extra_env.items()})

    deadline = time.monotonic() + timeout
    try:
        proc = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        raise AuditError("failed to spawn: %s" % exc)

    try:
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
            },
        })
        init_resp, _init_wire = _read_response(proc, 1, deadline)
        if init_resp is None:
            raise AuditError("no initialize response within %ss" % timeout)
        if init_resp.get("error"):
            raise AuditError("initialize error: %s"
                             % (init_resp["error"] or {}).get("message", "?"))

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = []
        wire_pages = []  # wire bytes per tools/list page, in request order
        cursor = None
        req_id = 2
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            _send(proc, {"jsonrpc": "2.0", "id": req_id, "method": "tools/list",
                         "params": params})
            list_resp, page_wire = _read_response(proc, req_id, deadline)
            if list_resp is None:
                raise AuditError("no tools/list response within %ss" % timeout)
            if list_resp.get("error"):
                raise AuditError("tools/list error: %s"
                                 % (list_resp["error"] or {}).get("message", "?"))
            wire_pages.append(page_wire)
            page = (list_resp.get("result") or {}).get("tools") or []
            if not isinstance(page, list):
                raise AuditError("tools/list returned non-list tools")
            tools.extend(page)
            cursor = (list_resp.get("result") or {}).get("nextCursor")
            if not cursor:
                break
            req_id += 1

        schema = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        schema_chars = len(schema)
        return {
            "name": name,
            "tool_count": len(tools),
            "schema_chars": schema_chars,
            "est_tokens": estimate_tokens(schema_chars),
            # Wire dimension: the tools/list discovery exchange, paid once
            # per session. Distinct from the context tax, which is re-paid
            # into the window on every turn (see mcp_tax.ledger).
            "wire_bytes": sum(wire_pages),
            "wire_bytes_per_page": wire_pages,
        }
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
