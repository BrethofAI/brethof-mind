#!/usr/bin/env python3
"""brethof-mind — SurrealDB-backed long-term memory MCP server for Claude Code.

Exposes nine tools over stdio:
  load_project    — dump a project's recent memory at conversation start
  save_memory     — UPSERT a curated memory record (auto-embedded)
  search_memory   — keyword search over curated memory
  semantic_search — vector search over curated memory
  search_chat     — vector search over the full chat-history archive
  get_memory      — fetch ONE record's full, untruncated content by id
  list_memory     — browse a project's record ids/titles (no content)
  query_raw       — arbitrary SurrealQL (graph traversal, aggregates)
  save_commit     — record a git commit against a project

Projects (which working dir → which memory table) and the SurrealDB
connection come from _config.py (projects.json + environment). There is no
hardcoded project list — edit projects.json to match your own repos.

Tables are created lazily by SurrealDB on first write; run scripts/init_db.py
once to add the vector/keyword indexes that make search fast.
"""
import json
import re

import httpx
from fastmcp import FastMCP

from _config import load_config, project_keys, embed_one

CFG = load_config()
_SD = CFG["surrealdb"]

mcp = FastMCP(
    "brethof-mind",
    instructions=(
        "SurrealDB-backed long-term memory for Claude Code. Call load_project() "
        "at conversation start. Use save_memory the moment a decision, "
        "correction, or status locks. Use semantic_search/search_memory for "
        "curated memory and search_chat to recall full past conversations. "
        "get_memory(id) reads one record in full (search results are previews); "
        "list_memory(project) browses ids/titles."
    ),
)


async def _query(sql: str) -> list:
    """Execute SurrealQL over HTTP and return the parsed result list."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _SD["url"].rstrip("/") + "/sql",
            content=sql,
            headers={
                "Accept": "application/json",
                "surreal-ns": _SD["ns"],
                "surreal-db": _SD["db"],
            },
            auth=(_SD["user"], _SD["pass"]),
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()


def _all_tables() -> list[str]:
    return project_keys(CFG)


def _default_table() -> str:
    return CFG.get("default_project", "global")


@mcp.tool()
async def load_project(project: str) -> str:
    """Load recent memories for a project at conversation start.

    Args:
        project: A project key from your projects.json (e.g. 'global').
    """
    table = project
    results = await _query(
        f"SELECT id, type, title, content, scope, updated_at FROM {table} "
        f"WHERE obsolete != true ORDER BY updated_at DESC LIMIT 30;"
    )
    records = results[0].get("result", []) if results else []

    # Also surface cross-cutting context from the default project.
    default = _default_table()
    global_records = []
    if table != default:
        gr = await _query(
            f"SELECT id, type, title, content FROM {default} "
            f"WHERE type IN ['feedback', 'user_pref', 'infrastructure'] "
            f"AND obsolete != true LIMIT 15;"
        )
        global_records = gr[0].get("result", []) if gr else []

    output = [f"## Project: {project} ({len(records)} memories)\n"]
    for r in records:
        title = r.get("title") or r.get("id", "?")
        rtype = r.get("type", "?")
        content = r.get("content", "")
        if content and len(content) > 300:
            content = content[:300] + "..."
        output.append(f"### [{rtype}] {title}\n{content}\n")

    if global_records:
        output.append(f"\n## {default} ({len(global_records)} records)\n")
        for r in global_records:
            title = r.get("title") or r.get("id", "?")
            content = r.get("content", "")
            if content and len(content) > 200:
                content = content[:200] + "..."
            output.append(f"- [{r.get('type','')}] {title}: {content}")

    return "\n".join(output)


@mcp.tool()
async def save_memory(
    project: str,
    record_id: str,
    memory_type: str,
    title: str,
    content: str,
) -> str:
    """Save or update a memory record (auto-embedded for semantic search).

    Args:
        project: A project key from your projects.json.
        record_id: Snake_case id (e.g. 'auth_migration_status').
        memory_type: decision | architecture | project_status | bug |
                     feedback | reference | snippet | commit
        title: Short descriptive title.
        content: Full content of the memory.
    """
    table = project
    safe_content = content.replace("\\", "\\\\").replace("'", "\\'")
    safe_title = title.replace("\\", "\\\\").replace("'", "\\'")

    embed_text = f"{title} [{memory_type}] {content[:500]}"
    vec_str = json.dumps(embed_one(embed_text, CFG))

    results = await _query(
        f"UPSERT {table}:{record_id} SET "
        f"type = '{memory_type}', "
        f"title = '{safe_title}', "
        f"content = '{safe_content}', "
        f"scope = 'project', "
        f"embedding = {vec_str}, "
        f"updated_at = time::now();"
    )

    if results and results[0].get("status") == "OK":
        return f"Saved {table}:{record_id}"
    return f"Error: {json.dumps(results)}"


@mcp.tool()
async def search_memory(query_text: str, project: str = "") -> str:
    """Full-text search curated memories across one or all projects.

    Stemmed + BM25-ranked (so 'deploying' matches 'deploy'), over titles and
    content. For meaning-based recall use semantic_search instead.

    Args:
        query_text: Words to search for in titles and content.
        project: Optional project key. Leave empty to search all.
    """
    tables = [project] if project else _all_tables()
    safe = query_text.replace("\\", "\\\\").replace("'", "\\'")

    all_results = []
    for table in tables:
        results = await _query(
            f"SELECT id, type, title, content, "
            f"(search::score(1) ?? 0) + (search::score(2) ?? 0) AS score "
            f"FROM {table} WHERE content @1@ '{safe}' OR title @2@ '{safe}' "
            f"ORDER BY score DESC LIMIT 10;"
        )
        records = results[0].get("result", []) if results else []
        for r in records:
            r["_table"] = table
            all_results.append(r)

    if not all_results:
        return f"No results for '{query_text}'"

    all_results.sort(key=lambda r: r.get("score", 0) or 0, reverse=True)
    output = []
    for r in all_results[:20]:
        title = r.get("title") or r.get("id", "?")
        content = (r.get("content") or "")[:200]
        output.append(f"[{r['_table']}/{r.get('type','')}] {title}: {content}")
    return "\n".join(output)


@mcp.tool()
async def semantic_search(query_text: str, project: str = "", top_k: int = 5) -> str:
    """Search curated memories by meaning (vector similarity).

    Args:
        query_text: Natural-language query.
        project: Optional project key. Leave empty to search all.
        top_k: Number of results (default 5).
    """
    vec_str = json.dumps(embed_one(query_text, CFG))
    tables = [project] if project else _all_tables()

    all_results = []
    for table in tables:
        results = await _query(
            f"SELECT id, type, title, content, vector::distance::knn() AS dist "
            f"FROM {table} WHERE embedding <|{top_k},COSINE|> {vec_str} ORDER BY dist;"
        )
        records = results[0].get("result", []) if results else []
        for r in records:
            r["_table"] = table
            all_results.append(r)

    all_results.sort(key=lambda r: r.get("dist", 999))
    all_results = all_results[:top_k]

    if not all_results:
        return f"No semantic results for '{query_text}'"

    output = []
    for r in all_results:
        score = 1 - r.get("dist", 0)
        title = r.get("title") or r.get("id", "?")
        content = (r.get("content") or "")[:200]
        output.append(f"[{score:.2f}] [{r['_table']}/{r.get('type','')}] {title}: {content}")
    return "\n".join(output)


@mcp.tool()
async def search_chat(query_text: str, project: str = "", top_k: int = 8) -> str:
    """Semantic search over the full chat-history archive (<project>_chat tables).

    Searches every past Claude Code conversation — prompts, assistant turns,
    decisions, code — across one or all projects. Use this to recall what was
    discussed/decided/tried in earlier sessions. semantic_search covers the
    curated memory; this covers the raw archive.

    Args:
        query_text: Natural-language query.
        project: Optional project key. Leave empty to search all.
        top_k: Number of results (default 8).
    """
    vec_str = json.dumps(embed_one(query_text, CFG))
    tables = [f"{project}_chat"] if project else [f"{t}_chat" for t in _all_tables()]

    all_results = []
    for table in tables:
        results = await _query(
            f"SELECT id, line_type, role, session_id, text, timestamp, "
            f"vector::distance::knn() AS dist FROM {table} "
            f"WHERE embedding <|{top_k},COSINE|> {vec_str} ORDER BY dist;"
        )
        records = results[0].get("result", []) if results else []
        for r in records:
            r["_table"] = table
            all_results.append(r)

    all_results.sort(key=lambda r: r.get("dist", 999))
    all_results = all_results[:top_k]

    if not all_results:
        return f"No chat-archive results for '{query_text}'"

    output = []
    for r in all_results:
        score = 1 - r.get("dist", 0)
        ts = (r.get("timestamp") or "")[:16]
        txt = " ".join((r.get("text") or "").split())[:260]
        output.append(
            f"[{score:.2f}] [{r['_table']}/{r.get('line_type', '')}"
            f"/{r.get('role') or '-'}] {ts} {(r.get('session_id') or '')[:8]}: {txt}"
        )
    return "\n".join(output)


@mcp.tool()
async def query_raw(sql: str) -> str:
    """Execute raw SurrealQL (graph traversal, aggregates, custom queries).

    Args:
        sql: A SurrealQL query string.
    """
    results = await _query(sql)
    output = []
    for r in results:
        if r.get("status") == "ERR":
            output.append(f"ERROR: {r.get('result', '?')}")
        else:
            records = r.get("result", [])
            if isinstance(records, list):
                for rec in records[:30]:
                    output.append(json.dumps(rec, default=str, ensure_ascii=False))
            else:
                output.append(str(records))
    return "\n".join(output) if output else "(empty result)"


@mcp.tool()
async def save_commit(
    project: str,
    commit_hash: str,
    message: str,
    files_changed: str,
    branch: str = "main",
) -> str:
    """Record a git commit against a project.

    Args:
        project: A project key from your projects.json.
        commit_hash: Git commit hash (short or full).
        message: Commit message.
        files_changed: Comma-separated list of changed files.
        branch: Git branch name.
    """
    table = project
    safe_msg = message.replace("\\", "\\\\").replace("'", "\\'")
    safe_files = files_changed.replace("\\", "\\\\").replace("'", "\\'")

    results = await _query(
        f"CREATE {table}:commit_{commit_hash[:8]} SET "
        f"type = 'commit', "
        f"title = '{safe_msg}', "
        f"content = 'hash: {commit_hash}, branch: {branch}, files: {safe_files}', "
        f"scope = 'project', "
        f"updated_at = time::now();"
    )

    if results and results[0].get("status") == "OK":
        return f"Commit {commit_hash[:8]} saved to {table}"
    return f"Error: {json.dumps(results)}"


@mcp.tool()
async def get_memory(record_id: str, project: str = "") -> str:
    """Fetch ONE memory record's FULL, untruncated content by id.

    search_memory / semantic_search / load_project all return short previews
    (~200-300 chars). Once you know a record's id, use this to read the whole
    thing — no raw SQL needed.

    Args:
        record_id: Full id 'table:key' (e.g. 'global:nova_pipeline_v3'), or
                   just the key if you also pass `project`.
        project: Table to use when record_id has no 'table:' prefix.
    """
    rid = record_id.strip()
    if ":" not in rid:
        if not project:
            return ("Error: pass a full id like 'global:my_key', or "
                    "record_id='my_key' together with project='global'.")
        rid = f"{project}:{rid}"
    table, _, key = rid.partition(":")
    if not (re.fullmatch(r"[A-Za-z0-9_]+", table)
            and re.fullmatch(r"[A-Za-z0-9_]+", key)):
        return (f"Error: unsupported id '{record_id}' "
                f"(expected snake_case table:key).")

    results = await _query(f"SELECT * FROM {table}:{key};")
    records = results[0].get("result", []) if results else []
    if not records:
        return f"No record found: {table}:{key}"
    r = records[0]
    r.pop("embedding", None)  # drop the 384-d vector — never useful to read
    title = r.get("title") or r.get("id", "?")
    rtype = r.get("type", "?")
    content = r.get("content", "")
    meta = {k: v for k, v in r.items()
            if k not in ("content", "title", "type", "id", "embedding")}
    out = [f"# {table}:{key}  [{rtype}]", title, ""]
    if meta:
        out.append("meta: " + json.dumps(meta, default=str, ensure_ascii=False))
        out.append("")
    out.append(content)
    return "\n".join(out)


@mcp.tool()
async def list_memory(project: str, memory_type: str = "", limit: int = 50) -> str:
    """Browse a project's memory — id + type + title (no content), newest first.

    See what's stored and grab an id to read in full with get_memory, without
    guessing ids or dropping to raw SQL.

    Args:
        project: Project key (table).
        memory_type: Optional type filter (decision, architecture, bug, ...).
        limit: Max records returned (default 50).
    """
    table = project
    where = "obsolete != true"
    if memory_type:
        safe_t = memory_type.replace("\\", "\\\\").replace("'", "\\'")
        where += f" AND type = '{safe_t}'"
    # Deliberately NO `ORDER BY` in SQL: on this DB an equality predicate
    # (type = ...) combined with ORDER BY updated_at can 400 when updated_at is
    # mixed-typed (string vs datetime) across records. Fetch unsorted, then
    # sort in Python — robust regardless of how clean the timestamps are.
    results = await _query(
        f"SELECT id, type, title, updated_at FROM {table} "
        f"WHERE {where} LIMIT 1000;"
    )
    records = results[0].get("result", []) if results else []
    if not records:
        suffix = f" of type '{memory_type}'" if memory_type else ""
        return f"No records in '{project}'{suffix}"
    records.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    records = records[:max(1, int(limit))]
    out = [f"## {project}: {len(records)} records "
           f"(use get_memory <id> for full content)"]
    for r in records:
        ts = (str(r.get("updated_at") or ""))[:10]
        out.append(f"{r.get('id')}  [{r.get('type','?')}]  "
                   f"{r.get('title','')}  ({ts})")
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run(transport="stdio")
