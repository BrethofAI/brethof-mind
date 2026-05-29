#!/usr/bin/env python3
"""SessionStart hook — inject brethof-mind memory for the current project.

At the start of every Claude Code session this surfaces, as context:
  1. the memory operating manual (which MCP tool for which job),
  2. pinned / golden-rule records (always shown, any age),
  3. the most recent records,
  4. a recap of where the last session left off.

Stdlib only; never blocks — any failure exits 0 with no context injected.
The project is chosen by matching the cwd against projects.json (see _sm.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sm import load_config, detect_project, query, result_rows  # noqa: E402

OPERATING_MANUAL = """=== MEMORY — operating manual (do not skip) ===
brethof-mind (SurrealDB) memory is live. Tools — `memory` MCP server:
- semantic_search(query_text, project): curated memory by MEANING (vector). Use when unsure of exact words.
- search_memory(query_text, project): curated memory by KEYWORD.
- search_chat(query_text, project): the FULL archive of every past conversation. Recall what was discussed / decided / tried.
- query_raw(sql): raw SurrealQL — graph traversal (->edges, parent links), counts, custom queries.
- save_memory(project, record_id, memory_type, title, content): SAVE a record (embeds it). Always this tool — never raw curl.
RULES:
- SAVE the moment something locks: a decision, a user correction, a status change, a thing figured out.
- SEARCH before asking the user anything that may already be answered, and when a task resembles past work.
- 'project' is a key from your projects.json. Curated memory lives in <project>; the full chat archive in <project>_chat."""


def format_records(records, cap=200):
    lines = []
    for r in records:
        title = r.get("title") or r.get("id", "?")
        rtype = r.get("type", "")
        content = r.get("content", "") or ""
        if len(content) > cap:
            content = content[:cap] + "..."
        lines.append(f"- [{rtype}] {title}: {content}")
    return lines


def recap_context(table, current_session, cfg):
    """Last few user/assistant messages from the most recent PRIOR session.

    Two steps, deliberately: first resolve *which* session was most recent,
    then pull that one session's tail. The earlier single-query
    `ORDER BY timestamp DESC LIMIT 20` was wrong twice over — it mixed rows
    across sessions, and (since timestamps are ISO strings and absent
    timestamps sort to the top here) any row with no timestamp — e.g. leftover
    test rows — masqueraded as "the last session". Skipping `timestamp = NONE`
    rows and anchoring on a real session_id fixes both.
    """
    chat = f"{table}_chat"
    # 1. Most recent prior session. Timestamps are ISO-8601 strings, so
    #    lexicographic DESC is chronological DESC. NONE timestamps are excluded
    #    so untimestamped/test rows can't win the ordering.
    res = query(
        # `timestamp` MUST be in the projection: SurrealDB v3 rejects an
        # ORDER BY on a field that isn't selected ("Missing order idiom").
        f"SELECT session_id, timestamp FROM {chat} "
        f"WHERE line_type IN ['user', 'assistant'] "
        f"AND timestamp != NONE AND session_id != NONE "
        f"AND session_id != '{current_session}' "
        f"ORDER BY timestamp DESC LIMIT 1;",
        cfg,
        timeout=10.0,  # this ORDER BY can scan a large archive; the recap is
                       # worth a longer budget than the default 6s so it isn't
                       # silently dropped when the DB is briefly under load.
    )
    rows = result_rows(res[0]) if res else []
    last_session = rows[0].get("session_id") if rows else None
    if not last_session:
        return ""
    # 2. That session's tail — most recent user/assistant lines that have text.
    res2 = query(
        # `embedding != NONE` keeps only genuine dialogue: the chat_stop hook
        # embeds real user prompts and assistant text, but stores tool-result
        # lines (which arrive as line_type='user') unembedded — so this drops
        # the tool-result JSON that would otherwise pose as a user message.
        f"SELECT line_type, role, text, timestamp FROM {chat} "
        f"WHERE session_id = '{last_session}' "
        f"AND line_type IN ['user', 'assistant'] "
        f"AND text != NONE AND text != '' AND embedding != NONE "
        f"AND !string::starts_with(text, '[tool_use:') "
        f"ORDER BY timestamp DESC LIMIT 12;",
        cfg,
    )
    recs = result_rows(res2[0]) if res2 else []
    kept = []
    for r in recs:
        txt = " ".join((r.get("text") or "").split())
        # Skip assistant turns that are only a tool call — not human-readable.
        if not txt or txt.startswith("[tool_use:"):
            continue
        kept.append((r, txt))
        if len(kept) >= 8:
            break
    if not kept:
        return ""
    kept.reverse()
    out = ["", "=== Last session — where you left off ==="]
    for r, txt in kept:
        who = r.get("role") or r.get("line_type") or "?"
        if len(txt) > 240:
            txt = txt[:240] + "..."
        out.append(f"  {who}: {txt}")
    return "\n".join(out)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        hook_input = {}

    cfg = load_config()
    cwd = hook_input.get("cwd", "")
    session_id = hook_input.get("session_id", "")
    table = detect_project(cwd, cfg)
    default = cfg.get("default_project", "global")

    # Tier 1 — golden rules. Pinned records are ALWAYS surfaced (the documented
    # `pin` contract); feedback/corrections are surfaced newest-first. Queried
    # SEPARATELY: a shared `(pin OR feedback) ... LIMIT 15` let a burst of recent
    # feedback push the (often older) pinned rules out of the window — silently
    # breaking "pin = always shown". Pins get their own generous limit.
    def _pins(tbl):
        return (
            f"SELECT id, type, title, content, updated_at FROM {tbl} "
            f"WHERE obsolete != true AND pin = true "
            f"AND record::id(id) != 'memory_index' "
            f"ORDER BY updated_at DESC LIMIT 40;"
        )
    q1 = _pins(table)
    if table != default:
        q1 += " " + _pins(default)
    # Cross-cutting RULES now live in the `rules` table (migrated from the old
    # type='feedback' rows). Surface every area='all' rule; project-specific
    # rules load on demand when the agent focuses on that area.
    q1 += (" SELECT id, 'rule' AS type, title, content, updated_at FROM rules "
           "WHERE area = 'all' ORDER BY updated_at DESC LIMIT 30;")
    t1 = query(q1, cfg)

    # Tier 2 — most recent records.
    # Exclude type='commit': git commits are recorded into <project>_commit,
    # not the curated note store. Without this filter they dominate the
    # recent-memory window (they're created on every commit, so always the
    # freshest rows) and bury the decisions/status this tier is meant to show.
    q2 = (
        f"SELECT id, type, title, content, updated_at FROM {table} "
        f"WHERE obsolete != true AND type != 'commit' AND type != 'feedback'"
        f"ORDER BY updated_at DESC LIMIT 12;"
    )
    if table != default:
        q2 += (
            f" SELECT id, type, title, content, updated_at FROM {default} "
            f"WHERE obsolete != true AND type != 'commit' AND type != 'feedback'"
            f"ORDER BY updated_at DESC LIMIT 8;"
        )
    t2 = query(q2, cfg)

    seen = set()
    pinned, recent = [], []
    for rs in (t1 if isinstance(t1, list) else []):
        for r in result_rows(rs):
            if r.get("id") not in seen:
                seen.add(r.get("id"))
                pinned.append(r)
    for rs in (t2 if isinstance(t2, list) else []):
        for r in result_rows(rs):
            if r.get("id") not in seen:
                seen.add(r.get("id"))
                recent.append(r)

    # Tier 0 — the MEMORY INDEX (the map): full + untruncated, shown FIRST.
    # It tells the agent what tables exist, HOW to search each (vector/graph/
    # keyword), the areas, and the rules. Navigate from here.
    idx = query(f"SELECT content FROM {default}:memory_index;", cfg)
    index_rows = result_rows(idx[0]) if idx else []
    index_content = (index_rows[0].get("content") if index_rows else "") or ""

    # Dashboard — at-a-glance status of every front (the `state` table).
    dash = query("SELECT area, status FROM state ORDER BY area;", cfg)
    dash_rows = result_rows(dash[0]) if dash else []

    blocks = []
    if index_content:
        blocks.append(index_content)
    if dash_rows:
        dlines = "\n".join(
            f"  {(r.get('area') or '?'):<14} {(r.get('status') or '')[:110]}"
            for r in dash_rows)
        blocks.append("\n=== Where things stand (state dashboard) ===\n" + dlines)
    blocks.append(OPERATING_MANUAL)
    if pinned:
        blocks.append("\n=== Pinned / golden-rule memory ===\n"
                      + "\n".join(format_records(pinned)))
    if recent:
        blocks.append("\n=== Recent memory ===\n"
                      + "\n".join(format_records(recent)))
    recap = recap_context(table, session_id, cfg)
    if recap:
        blocks.append(recap)

    context = "\n".join(blocks)
    output = {
        "continue": True,
        "suppressOutput": False,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"brethof-mind memory — project: {table}\n\n{context}",
        },
    }
    json.dump(output, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
