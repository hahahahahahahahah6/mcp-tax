"""mcp-tax command line interface."""

import argparse
import json
import sys

from . import __version__
from . import client
from . import config as config_mod
from . import runner

WINDOW_TOKENS = 200_000  # Claude Code's default context window


def _spec_cmdline(spec):
    parts = [spec.get("command") or "?"] + list(spec.get("args") or [])
    return " ".join(parts)


def cmd_list(args):
    servers = config_mod.load_servers()
    disabled = config_mod.load_disabled()
    if args.json:
        out = {
            name: {
                "command": spec.get("command"),
                "args": list(spec.get("args") or []),
                "source": spec.get("_source"),
                "disabled": name in disabled,
            }
            for name, spec in servers.items()
        }
        print(json.dumps(out, indent=2))
        return 0
    if not servers:
        print("no MCP servers found in %s or ./.mcp.json"
              % config_mod.claude_json_path())
        return 0
    for name in sorted(servers):
        spec = servers[name]
        flag = "  [off]" if name in disabled else ""
        print("%s%s\n    %s" % (name, flag, _spec_cmdline(spec)))
    n_off = sum(1 for n in servers if n in disabled)
    print("%d server(s), %d disabled" % (len(servers), n_off))
    return 0


def _fmt_row(cols, widths):
    return "  ".join(str(c).rjust(w) if i else str(c).ljust(w)
                     for i, (c, w) in enumerate(zip(cols, widths)))


def cmd_audit(args):
    servers = config_mod.load_servers()
    disabled = config_mod.load_disabled()
    if not servers:
        print("no MCP servers found in %s or ./.mcp.json"
              % config_mod.claude_json_path())
        return 0

    rows = []
    for name in sorted(servers):
        spec = servers[name]
        try:
            res = client.audit_server(name, spec, timeout=args.timeout)
            rows.append({"name": name, "ok": True,
                         "disabled": name in disabled, **res})
        except client.AuditError as exc:
            rows.append({"name": name, "ok": False, "error": str(exc),
                         "disabled": name in disabled})
        except Exception as exc:  # never let one server kill the audit
            rows.append({"name": name, "ok": False,
                         "error": "unexpected: %s" % exc,
                         "disabled": name in disabled})

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0 if all(r["ok"] for r in rows) else 1

    ok_rows = [r for r in rows if r["ok"]]
    name_w = max([len(r["name"]) for r in rows] + [6])
    print(_fmt_row(["server", "tools", "schema chars", "est. tokens"],
                   [name_w, 7, 12, 11]))
    print(_fmt_row(["-" * name_w, "-" * 7, "-" * 12, "-" * 11],
                   [name_w, 7, 12, 11]))
    for r in rows:
        mark = " [off]" if r["disabled"] else ""
        if r["ok"]:
            print(_fmt_row(
                [r["name"] + mark, r["tool_count"],
                 "{:,}".format(r["schema_chars"]),
                 "{:,}".format(r["est_tokens"])],
                [name_w, 7, 12, 11]))
        else:
            print("%s%s  FAILED: %s" % (r["name"].ljust(name_w), mark, r["error"]))

    total_tools = sum(r["tool_count"] for r in ok_rows)
    total_chars = sum(r["schema_chars"] for r in ok_rows)
    total_tokens = sum(r["est_tokens"] for r in ok_rows)
    print(_fmt_row(["-" * name_w, "-" * 7, "-" * 12, "-" * 11],
                   [name_w, 7, 12, 11]))
    print(_fmt_row(
        ["TOTAL", total_tools, "{:,}".format(total_chars),
         "{:,}".format(total_tokens)],
        [name_w, 7, 12, 11]))
    print("~%.1f%% of a %dk context window (est. tokens = schema chars / 4)"
          % (100.0 * total_tokens / WINDOW_TOKENS, WINDOW_TOKENS // 1000))
    failed = [r for r in rows if not r["ok"]]
    if failed:
        print("warning: %d server(s) failed to audit: %s"
              % (len(failed), ", ".join(r["name"] for r in failed)))
        return 1
    return 0


def _toggle(args, enable):
    servers = config_mod.load_servers()
    name = args.name
    if name not in servers:
        print("error: unknown server '%s'" % name, file=sys.stderr)
        if servers:
            print("known servers: %s" % ", ".join(sorted(servers)),
                  file=sys.stderr)
        return 1
    disabled = config_mod.load_disabled()
    if enable:
        disabled.discard(name)
        verb = "enabled"
    else:
        disabled.add(name)
        verb = "disabled"
    config_mod.save_disabled(disabled)
    print("%s %s (affects `mcp-tax run`)" % (name, verb))
    return 0


def cmd_run(args):
    servers = config_mod.load_servers()
    disabled = config_mod.load_disabled()
    path, argv = runner.prepare(servers, disabled, args.claude_args,
                                strict=args.strict)
    n_off = sum(1 for n in servers if n in disabled)
    print("mcp-tax: %d server(s), %d disabled -> %s"
          % (len(servers), n_off, path))
    if runner.exec_claude(argv) is None:
        print("mcp-tax: 'claude' not found on PATH; not launching.", file=sys.stderr)
        print("filtered config is at: %s" % path, file=sys.stderr)
        print("would run: %s" % runner.format_command(argv), file=sys.stderr)
        return 1
    return 0  # unreachable on success (exec replaces the process)


def build_parser():
    p = argparse.ArgumentParser(
        prog="mcp-tax",
        description="Audit the context tax of your MCP servers; "
                    "toggle them off per session.")
    p.add_argument("--version", action="version", version="mcp-tax " + __version__)

    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("list", help="list configured MCP servers")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_list)

    pa = sub.add_parser("audit",
                        help="measure each server's tool-schema context cost")
    pa.add_argument("--json", action="store_true")
    pa.add_argument("--timeout", type=float, default=client.DEFAULT_TIMEOUT,
                    help="per-server timeout in seconds (default %(default)s)")
    pa.set_defaults(func=cmd_audit)

    poff = sub.add_parser("off", help="disable a server for `mcp-tax run`")
    poff.add_argument("name")
    poff.set_defaults(func=lambda a: _toggle(a, enable=False))

    pon = sub.add_parser("on", help="re-enable a server for `mcp-tax run`")
    pon.add_argument("name")
    pon.set_defaults(func=lambda a: _toggle(a, enable=True))

    pr = sub.add_parser(
        "run", help="launch claude with disabled servers removed "
                    "(usage: mcp-tax run -- [claude args...])")
    pr.add_argument(
        "--strict", action="store_true",
        help="isolate the filtered config with Claude's --strict-mcp-config")
    pr.add_argument("claude_args", nargs=argparse.REMAINDER,
                    help="arguments forwarded to claude")
    pr.set_defaults(func=cmd_run)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # argparse REMAINDER keeps a leading "--"; drop it.
    if getattr(args, "claude_args", None) and args.claude_args[:1] == ["--"]:
        args.claude_args = args.claude_args[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
