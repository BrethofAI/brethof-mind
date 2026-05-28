#!/usr/bin/env python3
"""brethof-mind — SurrealDB-backed long-term memory MCP server for Claude Code.

Exposes eleven tools over stdio:
  load_project    — dump a project's recent memory at conversation start
  save_memory     — UPSERT a curated memory NOTE (type/title/content, auto-embedded)
  save_record     — UPSERT a STRUCTURED record (arbitrary typed fields)
  search_memory   — keyword search over curated memory
  semantic_search — vector search over curated memory
  search_chat     — vector search over the full chat-history archive
  get_memory      — fetch ONE record's full, untruncated content by id
  list_memory     — browse a project's record ids/titles (no content)
  recent_records  — recent structured rows from a table (scoped, filtered)
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


def _resolve_id(record_id: str, project: str = "") -> tuple[str, str]:
    """Normalize a record id to a (table, key) pair.

    Accepts either a full ``table:key`` or a bare ``key`` (with ``project``
    supplying the table). Both halves are validated as snake_case so that a
    stray ``table:`` prefix can't produce a malformed ``table:table:key``
    (which SurrealDB rejects with an opaque parse-error 400) and so the id
    can't inject SurrealQL. Raises ValueError with a clear message on bad
    input. Shared by save_memory and get_memory.
    """
    rid = record_id.strip()
    if ":" in rid:
        table, _, key = rid.partition(":")
    elif project:
        table, key = project, rid
    else:
        raise ValueError("pass a full id like 'global:my_key', or "
                         "record_id='my_key' together with project='global'.")
    if not (re.fullmatch(r"[A-Za-z0-9_]+", table)
            and re.fullmatch(r"[A-Za-z0-9_]+", key)):
        raise ValueError(f"unsupported id '{record_id}' "
                         f"(expected snake_case table:key).")
    return table, key


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
    try:
        table, key = _resolve_id(record_id, project)
    except ValueError as e:
        return f"Error: {e}"
    safe_content = content.replace("\\", "\\\\").replace("'", "\\'")
    safe_title = title.replace("\\", "\\\\").replace("'", "\\'")
    safe_type = memory_type.replace("\\", "\\\\").replace("'", "\\'")

    embed_text = f"{title} [{memory_type}] {content[:500]}"
    vec_str = json.dumps(embed_one(embed_text, CFG))

    results = await _query(
        f"UPSERT {table}:{key} SET "
        f"type = '{safe_type}', "
        f"title = '{safe_title}', "
        f"content = '{safe_content}', "
        f"scope = 'project', "
        f"embedding = {vec_str}, "
        f"updated_at = time::now();"
    )

    if results and results[0].get("status") == "OK":
        return f"Saved {table}:{key}"
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

    SurrealDB v3 note: `CONTAINS` is the ARRAY-membership operator and 400s on
    strings — for substring use `string::contains(field, 'x')` or the `~`
    operator. Prefer get_memory for full single-record reads and recent_records
    for scoped table reads; keep query_raw for ->graph traversal and aggregates.

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
    """Record a git commit into the project's commit ledger (<project>_commit).

    Commits land in <project>_commit, NOT the curated <project> note store:
    they are high-volume and would otherwise dominate the SessionStart
    recent-memory view. They stay searchable there (recent_records / query_raw
    / search_chat-style FTS) without burying decisions and status.

    Args:
        project: A project key from your projects.json.
        commit_hash: Git commit hash (short or full).
        message: Commit message.
        files_changed: Comma-separated list of changed files.
        branch: Git branch name.
    """
    table = f"{project}_commit"
    safe_msg = message.replace("\\", "\\\\").replace("'", "\\'")
    safe_files = files_changed.replace("\\", "\\\\").replace("'", "\\'")

    results = await _query(
        f"UPSERT {table}:commit_{commit_hash[:8]} SET "
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
async def save_record(project: str, record_id: str, fields: str,
                      embed_text: str = "") -> str:
    """UPSERT a STRUCTURED record (arbitrary typed fields) into a table.

    save_memory stores a prose NOTE (type/title/content). save_record stores a
    structured record: pass `fields` as a JSON object and every key becomes a
    real SurrealDB field you can SELECT, filter, index, and link with graph
    edges — for a product catalog, a posts/events ledger, etc. `updated_at` is
    set automatically. Pass `embed_text` to also store an embedding so the
    record is findable via semantic_search.

    Args:
        project: Table name (snake_case). Need NOT be a configured project; the
                 table is created on first write. Tables that aren't configured
                 projects stay out of the blanket search tools — ideal for a
                 high-volume ledger you only read deliberately (with
                 recent_records / query_raw).
        record_id: Snake_case key, or a full 'table:key'.
        fields: JSON object of fields to set, e.g.
                '{"name": "Brethof Voice Pro", "kind": "product", "tags": ["a","b"]}'.
                Values may be strings, numbers, booleans, arrays, or objects.
        embed_text: Optional text to embed for semantic_search ('' = skip).
    """
    try:
        table, key = _resolve_id(record_id, project)
    except ValueError as e:
        return f"Error: {e}"
    try:
        data = json.loads(fields)
    except json.JSONDecodeError as e:
        return f"Error: `fields` is not valid JSON: {e}"
    if not isinstance(data, dict):
        return "Error: `fields` must be a JSON object, e.g. '{\"name\": \"X\"}'."
    sets = []
    for k, v in data.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            return f"Error: bad field name '{k}' (snake_case, no dots)."
        sets.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
    if embed_text.strip():
        sets.append(f"embedding = {json.dumps(embed_one(embed_text, CFG))}")
    sets.append("updated_at = time::now()")
    results = await _query(f"UPSERT {table}:{key} SET " + ", ".join(sets) + ";")
    if results and results[0].get("status") == "OK":
        return f"Saved {table}:{key} ({len(data)} fields)"
    return f"Error: {json.dumps(results)}"


@mcp.tool()
async def recent_records(project: str, days: int = 0, where: str = "",
                         limit: int = 20) -> str:
    """Recent STRUCTURED records from a table, newest first (scoped reader).

    For high-volume tables (e.g. a posts ledger) — read a bounded, filtered
    slice instead of scanning the whole table or pulling it into context.
    Returns compact JSON rows (the embedding vector is dropped).

    Args:
        project: Table name (snake_case).
        days: Keep only rows whose updated_at is within the last N days
              (0 = no time filter).
        where: Optional extra SurrealQL filter inserted verbatim, e.g.
               "pillar = 'bvp' AND channel = 'x'". You author it — trusted input.
        limit: Max rows, 1-200 (default 20).
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", project):
        return f"Error: bad table name '{project}' (snake_case)."
    clauses = []
    if days and int(days) > 0:
        clauses.append(f"updated_at > time::now() - {int(days)}d")
    if where.strip():
        clauses.append(f"({where.strip()})")
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    lim = max(1, min(int(limit), 200))
    results = await _query(
        f"SELECT * FROM {project}{where_sql} ORDER BY updated_at DESC LIMIT {lim};"
    )
    rows = results[0].get("result", []) if results else []
    if not rows:
        return f"No records in '{project}'" + (" matching filter." if clauses else ".")
    out = [f"{project}: {len(rows)} record(s)"]
    for r in rows:
        r.pop("embedding", None)
        out.append(json.dumps(r, default=str, ensure_ascii=False))
    return "\n".join(out)


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
    try:
        table, key = _resolve_id(record_id, project)
    except ValueError as e:
        return f"Error: {e}"

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
