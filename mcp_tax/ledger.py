"""Per-turn re-entry billing ledger for mcp-tax.

The corrected billing model (thanks to dev.to reader MCPulse): a server's
tool schemas re-enter the model context on EVERY API call (turn), not once
per session. Counting them once per session underestimated the true context
cost by a factor of `turns`.

Two orthogonal dimensions (thanks to dev.to reader Rudratosh Shastri):
  - wire bytes: the tools/list discovery exchange, paid once per session.
    The per-page breakdown (wire_bytes_per_page) is a transport metric —
    how chatty the server's pagination is.
  - context tax: the schema tokens, re-paid into the window every turn.
    This is the per-server "preamble" billing unit.

Prompt caching only reduces price, never window occupancy: cached tokens
still sit in the context window at full size. So `cumulative_window_tokens`
ignores the cache hit rate; only `billable_tokens` is discounted.

Token estimate stays deliberately crude (schema chars / 4) — see README.
"""

# Cached prompt reads are billed at roughly 10% of the base token price
# (Anthropic-style pricing). Window occupancy is unaffected regardless.
CACHE_BILLABLE_FRACTION = 0.1


def preamble_per_turn(est_tokens):
    """Window tokens one server re-injects into context on every turn."""
    return est_tokens


def build_ledger(rows, turns, cache_hit_rate=0.0):
    """Build the session ledger from audit rows.

    rows: audit result dicts with name/tool_count/est_tokens/wire_bytes/
    wire_bytes_per_page (and ok/disabled flags). turns: number of API calls
    in the session. cache_hit_rate: fraction of preamble tokens served
    from prompt cache, 0..1 — affects price only, never window occupancy.

    Returns {"turns", "cache_hit_rate", "servers", "totals"}. Each server
    is an independent billing line (per-server preamble split).
    """
    if turns < 1:
        raise ValueError("turns must be >= 1")
    if not 0.0 <= cache_hit_rate <= 1.0:
        raise ValueError("cache_hit_rate must be in [0, 1]")

    lines = []
    for r in rows:
        if not r.get("ok"):
            continue
        preamble = preamble_per_turn(r["est_tokens"])
        window = preamble * turns
        billable = int(round(
            window * (1.0 - cache_hit_rate * (1.0 - CACHE_BILLABLE_FRACTION))))
        pages = list(r.get("wire_bytes_per_page") or [])
        lines.append({
            "name": r["name"],
            "tool_count": r.get("tool_count", 0),
            "disabled": bool(r.get("disabled")),
            # per-server preamble billing unit
            "preamble_tokens_per_turn": preamble,
            "cumulative_window_tokens": window,
            # caching never shrinks the window
            "cacheable_tokens": window,
            "billable_tokens": billable,
            # wire dimension: paid once per session, not per turn
            "wire_bytes": r.get("wire_bytes", 0),
            "wire_bytes_per_page": pages,
            # the old accounting, kept for the correction comparison
            "old_model_session_tokens": r["est_tokens"],
        })

    def _sum(key):
        return sum(line[key] for line in lines)

    totals = {
        "tool_count": _sum("tool_count"),
        "preamble_tokens_per_turn": _sum("preamble_tokens_per_turn"),
        "cumulative_window_tokens": _sum("cumulative_window_tokens"),
        "cacheable_tokens": _sum("cacheable_tokens"),
        "billable_tokens": _sum("billable_tokens"),
        "wire_bytes": _sum("wire_bytes"),
        "old_model_session_tokens": _sum("old_model_session_tokens"),
    }
    return {
        "turns": turns,
        "cache_hit_rate": cache_hit_rate,
        "servers": lines,
        "totals": totals,
    }
