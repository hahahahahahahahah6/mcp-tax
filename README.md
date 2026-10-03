# mcp-tax

Audit the **context tax** of your MCP servers — and turn them off per session.

Claude Code loads every configured MCP server's tool schemas into context at
session start, with no UI to temporarily disable a server. Users have measured
41k tokens of pure schema; one blog estimates 6 mid-size servers eat 10–15% of
the window before the conversation even starts. As the complaint goes:
*"Claude Code has no way to temporarily disable a configured server."*

mcp-tax measures that tax, then lets you launch Claude Code with the expensive
servers you don't need right now switched off.

Zero dependencies. Python standard library only.

## Install

```bash
pip install mcp-tax
# or with pipx:
pipx install mcp-tax
```

Requires Python 3.9+. No other packages.

## Usage

**See what you have configured** (reads `~/.claude.json` — including the
local project scope `projects.<cwd>.mcpServers` where `claude mcp add`
stores servers by default — plus `./.mcp.json` when present):

```bash
$ mcp-tax list
github
    npx -y @modelcontextprotocol/server-github
postgres  [off]
    uvx mcp-server-postgres --db-url ...
2 server(s), 1 disabled
```

**Measure the tax** — handshakes each server over stdio (`initialize`, then
`tools/list`), counts tools, and estimates tokens:

```bash
$ mcp-tax audit
server    tools  schema chars  est. tokens
------  -------  ------------  -----------
github       51       118,203        29,551
postgres [off]    9        12,440         3,110
------  -------  ------------  -----------
TOTAL        60       130,643        32,661
~16.3% of a 200k context window (est. tokens = schema chars / 4)
```

Servers that fail (bad command, timeout, handshake error) get a `FAILED` row
and a warning instead of killing the audit. Per-server timeout is 15s
(`--timeout` to change).

**Toggle servers off/on** (persisted in `~/.config/mcp-tax/disabled.json`):

```bash
$ mcp-tax off postgres
postgres disabled (affects `mcp-tax run`)
$ mcp-tax on postgres
postgres enabled (affects `mcp-tax run`)
```

**Launch Claude Code without the disabled servers:**

```bash
$ mcp-tax run -- -p "summarize this repo"
mcp-tax: 2 server(s), 1 disabled -> ~/.config/mcp-tax/mcp-config.filtered.json
```

This writes a filtered `{"mcpServers": ...}` config (disabled servers removed)
and execs `claude --mcp-config <file> --strict-mcp-config` with your args
forwarded. The "without the disabled servers" part comes from
`--strict-mcp-config`, which mcp-tax always passes: `--mcp-config` alone only
*layers* the file onto MCP servers from your other config sources
(`~/.claude.json`, `.mcp.json`), so a disabled server listed there would still
load. Your real `~/.claude.json` is never modified.

All commands also accept `--json` (`list`, `audit`) for scripting.

## How the token estimate works

For each server, mcp-tax JSON-encodes the full `tools/list` result (compact,
no whitespace) and counts characters. Estimated tokens = `round(chars / 4)`.

This is deliberately crude: it approximates the Anthropic tokenizer's
~4-chars-per-token rule of thumb on English/JSON text. Real token counts vary
with the tokenizer and with how Claude Code wraps schemas, so treat the number
as an order-of-magnitude gauge — good enough to answer "which server is eating
my window?", not a billing meter.

## Limitations

- **stdio servers only.** Servers using SSE or streamable HTTP transports are
  not audited (they'd show a connection failure row).
- **The `--mcp-config` / `--strict-mcp-config` flags** for `run` come from Claude
  Code's documented CLI options; they could not be verified on the machine
  where this was built (no Claude Code CLI installed). `--strict-mcp-config`
  matters: `--mcp-config` alone only *adds* servers on top of your configured
  ones, so without the strict flag disabled servers would still load. If the
  flag names ever change, `run` prints the filtered config path so you can
  pass it manually.
- **Audit uses `select(2)`** on the server's stdout pipe: fine on Linux/macOS,
  not on Windows.
- The estimate ignores tools' runtime behavior — a server with 2 tools can
  still be expensive if its tool *results* are huge. This tool measures schema
  cost only.
- `mcp-tax run` writes the filtered config to
  `~/.config/mcp-tax/mcp-config.filtered.json` (overwritten each run).

## Development

```bash
python3 tests/test_smoke.py   # 9 smoke tests, incl. a fake stdio MCP server
```

The test fixture `tests/fake_mcp_server.py` speaks the same newline-delimited
JSON-RPC 2.0 framing real MCP stdio servers use, so the audit math is tested
against a realistic handshake (including stdout noise and hang/timeout cases).

## License

MIT — see [LICENSE](LICENSE).
