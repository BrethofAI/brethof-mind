# Architecture

brethof-mind gives Claude Code a memory that survives across sessions. It has
two layers and four moving parts.

## Two layers of memory

| Layer | Tables | Written by | Read by |
|---|---|---|---|
| **Curated** (gold) | `<project>` | `save_memory` (the agent, deliberately) | `load_project`, `search_memory`, `semantic_search`, SessionStart hook |
| **Archive** (raw) | `<project>_chat` | `chat_stop` hook (automatic, every turn) | `search_chat` (vector), `search_chat_text` (keyword/BM25), SessionStart recap |

The curated layer is small and high-signal: decisions, architecture notes,
status, bugs, user corrections. The archive is everything — a complete,
searchable mirror of every transcript line, queryable by meaning
(`search_chat`) and by exact keyword (`search_chat_text`). The agent promotes
the important bits from archive into curated memory as it works.

Git commits are kept in a separate narrow ledger (`<project>_commit`, written
by `save_commit`) — out of the curated layer so their per-commit volume can
never crowd the session-start view, still searchable when you want them.

## The four parts

```
                          ┌─────────────────────────────┐
   Claude Code  ◄────────►│  MCP server (server.py)      │
     session              │  12 tools over stdio         │
        │                 └──────────────┬──────────────┘
        │ hooks                          │ HTTP /sql
        ▼                                ▼
 ┌──────────────┐                ┌──────────────┐
 │ load_memory  │ SessionStart   │              │
 │ memory_nudge │ UserPromptSub  │   SurrealDB  │  ◄── docker compose
 │ chat_stop    │ Stop ──────────►│  (vectors +  │      (+ Surrealist UI)
 │ save_commit  │ git post-commit │   graph)     │
 └──────────────┘                └──────────────┘
        ▲                                ▲
        └────────── projects.json ───────┘   (which dir → which table)
```

1. **SurrealDB** — the store. One container, a single embedded `surrealkv`
   file. Holds the vectors (HNSW indexes) and the graph relations. The
   optional Surrealist container is just a web UI for browsing.

2. **MCP server** (`mcp-server/server.py`) — a FastMCP stdio server exposing
   the twelve tools. Registered with Claude Code via `claude mcp add`. It reads
   `projects.json` for the project map and the DB connection.

3. **Hooks** (`hooks/`) — four Claude Code lifecycle hooks:
   - `load_memory.py` (SessionStart) injects pinned rules, recent memory, and
     a recap of the last session.
   - `memory_nudge.py` (UserPromptSubmit) reminds the agent to search memory
     before answering from assumption.
   - `chat_stop.py` (Stop) mirrors new transcript lines into `<project>_chat`.
   - `save_commit.py` (git post-commit) records commits.

4. **Config** (`projects.json`) — read by all of the above. Maps a working
   directory (substring match on its path) to a project key, which is the
   table name. Credentials and connection come from the environment.

## Embeddings

Text is embedded with a sentence-transformer (`all-MiniLM-L6-v2`, 384-dim) via
[`fastembed`](https://github.com/qdrant/fastembed) — runs locally on CPU, no
API calls, ~23 MB model downloaded once. `semantic_search` and `search_chat`
do cosine KNN against SurrealDB's HNSW index. Change the model/dim in
`projects.json` (`embedding_model`, `embedding_dim`) and re-run `init_db.py`.

## How the chat archive stays reliable

The Stop hook runs after every turn and is built to never break a turn or lose
a line:

- **Byte-offset per session** — it re-reads only the new tail of the
  transcript, so it never double-inserts or skips.
- **Row chunking** — a single transcript line carrying a huge tool result is
  split into linked rows (`parent_chunk` + `chunk_seq`) so no UPSERT exceeds
  the body limit. Reconstruct by concatenating `chunk_text` in order.
- **Per-statement fallback** — if a batch write fails, each statement is
  retried alone, so one bad row can't block the rest.
- **Atomic offset** — the offset only advances when every row for the window
  wrote successfully; otherwise it retries next turn.
- **64 MiB body limit** — docker-compose raises SurrealDB's HTTP body cap from
  the 1 MiB default so large turns don't hit HTTP 413.

## Graph relations

The backend is a graph DB, so memory isn't just rows. The core `supersedes`
relation links a record to the one it replaced, so you can walk replacement
chains. You can add your own domain tables and edges — see
`examples/content_graph/`.
