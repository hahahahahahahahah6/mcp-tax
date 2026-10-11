# mcp-tax

Audit the **context tax** of your MCP servers — and turn them off per session.

Claude Code loads every configured MCP server's tool schemas into context at
session start, with no UI to temporarily disable a server. Users have measured
41k tokens of pure schema; one blog estimates 6 mid-size servers eat 10–15% of
the window before the conversation even starts. As the complaint goes:
*"Claude Code has no way to temporarily disable a configured server."*

mcp-tax measures that tax, then lets you launch Claude Code with the expensive
servers you don't need right now switched off.

v2 adds what schema audits can't tell you: **who is calling this MCP, using
what agent, for what project** (`who-called`), a unified audit of all four
always-on context sources (`audit --all`), and a **CI gate** (`gate`) that
fails your build when the budget is blown.

v3 fixes the billing model itself: schemas **re-enter the window on every
turn** (not once per session), each server is billed as an independent
**preamble** line, and `tools/list` pagination gets its own **wire-bytes**
counter — transport cost, not context tax.

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
`tools/list`), counts tools, counts the discovery wire bytes, and estimates
tokens:

```bash
$ mcp-tax audit --turns 20
server      tools  wire bytes  preamble/turn   window toks (20 turns)
------    -------  ----------  -------------  --------------------
github         51     118,320         29,551               591,020
postgres [off]    9      12,455          3,110                62,200
------    -------  ----------  -------------  --------------------
TOTAL          60     130,775         32,661               653,220
~16.3% of a 200k context window re-entered EVERY turn (schemas are re-injected per API call)
wire bytes are a one-time-per-session transport cost, not context tax; est. tokens = schema chars / 4
```

Key idea: the per-turn **preamble** is what binds the window, and the
session's true cost is preamble × turns. `--turns` sets the session length
(default 10); `--cache-hit-rate 0.5` models prompt caching (cuts price,
never window occupancy — see "How billing works").

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

All commands also accept `--json` (`list`, `audit`, `who-called`, `gate`) for
scripting.

## Who is calling this MCP?

Schemas tell you what a server *could* cost. Transcripts tell you what it
*actually* costs — and who is spending it. `who-called` mines your local
Claude Code transcripts (`~/.claude/projects/*/*.jsonl`, read-only, nothing
leaves your machine) and attributes every MCP tool call to an agent, a
project, and a purpose:

```bash
$ mcp-tax who-called --server github
time              project    agent       tool                purpose
----------------  ---------  ----------  ------------------  -------------------
2026-10-10 09:03  proj       explorer    github__search_code  fix the github action workflow
2026-10-10 09:01  proj       main        github__get_workflo fix the github action workflow
2 call(s); attribution is heuristic, see README
```

- **agent**: `main` for the main agent, or the `subagent_type` when the call
  came from a subagent (sidechain records with no announced type show as
  `subagent`).
- **project**: from the transcript's `cwd`; falls back to a best-effort
  decode of the project-dir slug.
- **purpose**: the nearest preceding user/assistant text, truncated — a hint,
  not a ground truth.

Options: `--days N` (lookback window, default 30), `--limit N` (default 50),
`--projects-dir` to point at a different transcript root.

## One audit for all four context sources

MCP schemas are only one of the things your sessions always pay for. `audit
--all` puts the four always-on sources in one metric frame — counts, tokens,
and share:

```bash
$ mcp-tax audit --all
source        count  est. tokens   share
----------  -------  -----------  ------
rules             4          320    8.1%
memory            2          410   10.4%
skills            6        1,200   30.5%
mcp calls        38        2,010   51.0%
----------  -------  -----------  ------
TOTAL                       3,940  100%
tokens = chars / 4 everywhere; mcp calls cover the last 30 day(s)
```

- **rules**: directives in `~/.claude/CLAUDE.md` (+ `./CLAUDE.md` when
  present); count = non-blank, non-heading lines.
- **memory**: `MEMORY.md` files under `~/.claude`; count = files.
- **skills**: `~/.claude/skills/*/SKILL.md`; count = skills.
- **mcp calls**: tool calls mined from transcripts (`--days` bounds the
  window); tokens = input JSON chars + matched tool-result chars, / 4.

## CI gate

Turn the audit into a budget your CI can enforce:

```bash
$ mcp-tax gate --max-tokens 50000 --max-calls 200
...
total: 3,940 tokens, 38 mcp calls (last 30 day(s))
GATE PASS
```

Exit code 1 when tokens exceed `--max-tokens` or calls exceed `--max-calls`,
with the breach named (`GATE FAIL: tokens 60,120 > max 50000`). Drop it in a
workflow and your context budget stops drifting silently.

## How mcp-tax differs

There are interactive token-audit skills that walk you through your usage in
a chat. mcp-tax is the opposite kind of tool: a **deterministic CLI** —
same transcripts, same output, every run — with `--json` on everything and a
**CI gate** that fails builds. Skills are for conversations; this is for
automation.

## mcp-tax vs mcp-audit

Sibling tools, complementary axes:

- **mcp-tax** (this): **call attribution and cost audit** — who calls which
  MCP, from which agent and project, and how much context it costs.
- **mcp-audit** ([PyPI: mcp-runtime-audit](https://pypi.org/project/mcp-runtime-audit/)):
  **runtime security** — known-CVE checks and one-click fixes for your MCP
  servers.

Cheap is not the same as safe, and safe is not the same as cheap. Run both.

## What's new in 0.3.0

Two dev.to readers pressed on the billing model until it broke — and they
were right. v0.3 rebuilds it.

**Per-turn re-entry (thanks, MCPulse).** The old `audit` billed each
server's schemas once per session. That's wrong: the whole context —
including every tool schema — is re-sent on *every* API call. A 30k-token
schema in a 20-turn session isn't a 30k tax; it's a 600k tax that also has
to fit in the window 20 times over. The ledger now bills per turn, and
`--turns` scales the session total. The old once-per-session number is kept
in the JSON output as `old_model_session_tokens` so you can see the
undercount: it was the truth divided by your turn count.

**Per-server preamble split.** Each server is now an independent billing
line: its own preamble (tokens re-injected per turn), its own cumulative
window tokens, its own billable tokens. Disable the most expensive
preamble, watch the total move — that's the unit you actually decide on.

**Per-page wire counter (thanks, Rudratosh Shastri).** `tools/list`
pagination now reports **wire bytes per page** — a second, orthogonal
dimension. Wire bytes are the transport cost of discovery, paid once per
session; they are *not* context tax and they don't re-enter the window.
A server can be wire-chatty (many small pages) yet schema-cheap, or the
reverse. The audit shows both so you stop conflating them.

**Prompt caching, honestly modeled.** Caching cuts the *price* of repeated
tokens, not their *size in the window*. `--cache-hit-rate` discounts the
billable column only; the window column never moves.

## How billing works

For each server, mcp-tax JSON-encodes the full `tools/list` result (compact,
no whitespace) and counts characters. Estimated tokens = `round(chars / 4)` —
the Anthropic tokenizer's ~4-chars-per-token rule of thumb on English/JSON
text. Deliberately crude: good enough to answer "which server is eating my
window?", not a billing meter.

The v0.3 correction, in one paragraph: **schemas re-enter the window on
every turn.** The number that matters per turn is the server's **preamble**
(its schema tokens); the number that matters per session is preamble ×
turns. Wire bytes (`tools/list` traffic, per-page) are measured separately
because they're paid once per session and never touch the window again.
Prompt cache hits make tokens cheaper but exactly as large — the ledger
keeps `billable_tokens` and `cumulative_window_tokens` as separate columns
for that reason.

Thanks to dev.to readers **MCPulse** (per-turn re-entry) and **Rudratosh
Shastri** (the wire-bytes-vs-context-tax split) for the discussions that
shaped this model.

## Limitations

- **stdio servers only.** Servers using SSE or streamable HTTP transports are
  not audited (they'd show a connection failure row).
- **Attribution is heuristic.** `who-called` reads the transcript format as
  of Oct 2026; sidechain/agent fields are inferred, not contractual, and the
  purpose snippet is the nearest adjacent text. Treat it as an investigator's
  lead, not a log line. Nothing is uploaded — parsing is local.
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
- **The ledger needs a turn count.** `--turns` is your estimate of the
  session's API calls; the cumulative columns scale linearly with it. When
  in doubt, `who-called` on a past session tells you what your turn counts
  actually look like.
- `mcp-tax run` writes the filtered config to
  `~/.config/mcp-tax/mcp-config.filtered.json` (overwritten each run).

## Development

```bash
python3 -m unittest discover -s tests   # 25 tests: 11 v2 (who-called, audit --all, gate) + 14 v3 (wire counter, ledger, audit --turns)
python3 tests/test_smoke.py             # 9 smoke tests, incl. a fake stdio MCP server
```

The test fixture `tests/fake_mcp_server.py` speaks the same newline-delimited
JSON-RPC 2.0 framing real MCP stdio servers use, so the audit math is tested
against a realistic handshake (including stdout noise and hang/timeout cases).

## License

MIT — see [LICENSE](LICENSE).
