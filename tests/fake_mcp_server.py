#!/usr/bin/env python3
"""Fake MCP stdio server for mcp-tax smoke tests.

Speaks newline-delimited JSON-RPC 2.0: initialize -> tools/list.
Set FAKE_NOISE=1 to print a garbage line on stdout first (tests that the
client skips non-protocol noise). Set FAKE_HANG=1 to never answer.
"""

import json
import os
import sys
import time

TOOLS = [
    {
        "name": "alpha",
        "description": "first tool " * 10,
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "query"}},
            "required": ["q"],
        },
    },
    {
        "name": "beta",
        "description": "second tool",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def main():
    if os.environ.get("FAKE_NOISE") == "1":
        sys.stdout.write("this is not json, just a noisy log line\n")
        sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if os.environ.get("FAKE_HANG") == "1":
            time.sleep(60)
        req = json.loads(line)
        method = req.get("method")
        req_id = req.get("id")
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
            }}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"tools": TOOLS}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": "unknown"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
