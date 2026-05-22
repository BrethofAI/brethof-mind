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
    """Last few user/assistant messages from the most recent PRIOR session."""
    chat = f"{table}_chat"
    res = query(
        f"SELECT line_type, role, text, timestamp FROM {chat} "
        f"WHERE line_type IN ['user', 'assistant'] "
        f"AND session_id != '{current_session}' "
        f"ORDER BY timestamp DESC LIMIT 20;",
        cfg,
    )
    recs = result_rows(res[0]) if res else []
    kept = []
    for r in recs:
        txt = " ".join((r.get("text") or "").split())
        if txt:
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

    # Tier 1 — pinned + feedback (golden rules): always surfaced, any age.
    q1 = (
        f"SELECT id, type, title, content, updated_at FROM {table} "
        f"WHERE obsolete != true AND (pin = true OR type = 'feedback') "
        f"ORDER BY updated_at DESC LIMIT 15;"
    )
    if table != default:
        q1 += (
            f" SELECT id, type, title, content, updated_at FROM {default} "
            f"WHERE obsolete != true AND (pin = true OR type = 'feedback') "
            f"ORDER BY updated_at DESC LIMIT 10;"
        )
    t1 = query(q1, cfg)

    # Tier 2 — most recent records.
    q2 = (
        f"SELECT id, type, title, content, updated_at FROM {table} "
        f"WHERE obsolete != true ORDER BY updated_at DESC LIMIT 12;"
    )
    if table != default:
        q2 += (
            f" SELECT id, type, title, content, updated_at FROM {default} "
            f"WHERE obsolete != true ORDER BY updated_at DESC LIMIT 8;"
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

    blocks = [OPERATING_MANUAL]
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
