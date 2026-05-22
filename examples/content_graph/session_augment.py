"""Paste graph_context() into hooks/load_memory.py to surface a domain graph
at session start. See this folder's README. Uses the hook's _sm helpers, so
it stays stdlib-only and never blocks the session.
"""
from _sm import query, result_rows  # already imported by load_memory.py


def graph_context(cfg) -> str:
    """Render the content graph (nodes + active claims) as a context block."""
    res = query(
        "SELECT number, title, status FROM episode ORDER BY number; "
        "SELECT claim, stance, topic, hot_take FROM claim WHERE active = true LIMIT 60;",
        cfg,
    )
    if not isinstance(res, list) or len(res) < 2:
        return ""
    episodes = result_rows(res[0])
    claims = result_rows(res[1])
    if not episodes and not claims:
        return ""

    out = ["", "=== Content graph (SurrealDB) — use these for domain work ==="]
    if episodes:
        out.append(f"-- episodes ({len(episodes)}):")
        for e in episodes:
            out.append(f"   #{e.get('number')} [{e.get('status', '?')}] {e.get('title', '?')}")
    if claims:
        out.append(f"-- on-record claims ({len(claims)} active):")
        for c in claims:
            hot = " [HOT TAKE]" if c.get("hot_take") else ""
            out.append(f"   ({c.get('stance', '?')}/{c.get('topic', '?')})"
                       f"{hot} {(c.get('claim') or '')[:200]}")
    return "\n".join(out)
