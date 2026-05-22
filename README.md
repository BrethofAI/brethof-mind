# brethof-mind

**Long-term memory for [Claude Code](https://docs.claude.com/en/docs/claude-code), built on [SurrealDB](https://surrealdb.com).**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Built on SurrealDB](https://img.shields.io/badge/built%20on-SurrealDB-ff00a0.svg)
![Local-first](https://img.shields.io/badge/local--first-no%20cloud-2ea44f.svg)

*Don't summarize your memory — search it. The complete session history for
Claude Code: full-text + vector + graph, 100% local, no API.*

Claude Code forgets everything between sessions. brethof-mind gives it a
persistent, searchable memory: an MCP server with seven tools and four
lifecycle hooks, backed by a local SurrealDB. Decisions, status, and bugs are
saved as curated memory; every conversation is mirrored into a complete,
searchable archive. All of it stays on your machine.

### Why keep everything?

Claude Code's context gets compacted and truncated — and summaries are lossy:
they're one model's guess at what mattered. brethof-mind doesn't summarize. It
keeps the **complete transcript** of every session and makes it searchable, so
recall never depends on what survived a summary or fit in a context window.

No summarization model. No separate memory agent. No API keys. Just your raw
history — searchable three ways: **full-text, vector, and graph** — 100% local.

```
 you open a new session ─► SessionStart hook loads pinned rules, recent
                            memory, and a recap of where you left off
 you ask a question ─────► the agent searches memory before answering
 the agent works ───────► saves decisions as they lock (save_memory)
 every turn ends ────────► the whole turn is archived (chat_stop hook)
 next week, different repo ─► "what did we decide about X?" → search_chat
```

## What you get

- **Cross-session recall.** The agent picks up where it left off — no
  re-explaining context every morning.
- **Two memory layers.** A small *curated* layer (decisions, architecture,
  status) and a complete *archive* of every transcript line.
- **Three ways to search.** Full-text (stemmed, BM25-ranked), semantic
  (vector), and graph (SurrealQL traversal + relations).
- **Multi-project.** One DB, one table per project. A `projects.json` maps each
  repo to its own memory.
- **Local-first, no API.** SurrealDB in a local container; embeddings computed
  locally (fastembed, CPU). No API keys, no per-call cost — runs offline after
  a one-time ~23 MB model download. Nothing leaves your machine.
- **Robust archiving.** The Stop hook chunks oversized turns, retries
  per-statement, and tracks a byte offset so it never loses or duplicates a
  line.

## How it works

Four parts — a SurrealDB container, an MCP server, four hooks, and a
`projects.json` that ties them together. Full diagram and data flow in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

**Requirements:** Docker + Docker Compose, Python 3.9+, and Claude Code.

```bash
git clone https://github.com/BrethofAI/brethof-mind.git
cd brethof-mind

# 1. Configure
cp .env.example .env                      # optional: change DB credentials
cp projects.example.json projects.json    # edit: map your repos → tables
mkdir -p ~/.config/brethof-mind
cp projects.json ~/.config/brethof-mind/  # the location every part checks

# 2. Start SurrealDB (+ Surrealist UI on :8201)
docker compose up -d

# 3. Install the MCP server + create the schema/indexes
python -m venv mcp-server/.venv
mcp-server/.venv/bin/pip install -r mcp-server/requirements.txt
mcp-server/.venv/bin/python mcp-server/scripts/init_db.py

# 4. Register the MCP server with Claude Code (name it "memory")
claude mcp add memory -- \
  "$(pwd)/mcp-server/.venv/bin/python" "$(pwd)/mcp-server/server.py"

# 5. Install the hooks
cp hooks/*.py ~/.claude/hooks/
# then merge settings.example.json's "hooks" block into ~/.claude/settings.json
# (point the Stop hook at mcp-server/.venv/bin/python — it needs fastembed)
```

Restart Claude Code. A new session now opens with your memory loaded.

Optionally, **import the history you already have**:

```bash
mcp-server/.venv/bin/python mcp-server/scripts/ingest_transcripts.py
```

## The tools

The MCP server exposes seven tools (registered above as `memory`):

| Tool | What it does |
|---|---|
| `load_project(project)` | Dump a project's recent curated memory. |
| `save_memory(project, id, type, title, content)` | UPSERT a curated record (auto-embedded). |
| `search_memory(query, project?)` | Full-text search over curated memory (stemmed, BM25-ranked). |
| `semantic_search(query, project?, top_k?)` | Vector search over curated memory. |
| `search_chat(query, project?, top_k?)` | Vector search over the full chat archive. |
| `query_raw(sql)` | Arbitrary SurrealQL — graph traversal, aggregates. |
| `save_commit(project, hash, message, files, branch?)` | Record a git commit. |

## The hooks

| Hook | Event | Role |
|---|---|---|
| `load_memory.py` | SessionStart | Inject pinned rules, recent memory, last-session recap. |
| `memory_nudge.py` | UserPromptSubmit | Remind the agent to search memory first. |
| `chat_stop.py` | Stop | Mirror new transcript lines into the archive. |
| `save_commit.py` | git post-commit | Record commits (copy into a repo's `.git/hooks/`). |

## Configuration

Everything is driven by `projects.json` (copied to `~/.config/brethof-mind/`):

```json
{
  "surrealdb": { "url": "http://localhost:8200", "ns": "ai", "db": "memory" },
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_dim": 384,
  "default_project": "global",
  "projects": [
    { "key": "global", "match": [] },
    { "key": "webapp", "match": ["my-webapp", "frontend"] },
    { "key": "api",    "match": ["my-api", "backend"] }
  ]
}
```

Each `match` is a list of substrings tested against the working directory's
absolute path. First match wins; unmatched dirs fall into `default_project`.
The `key` is the table name. After adding a project, re-run `init_db.py` to
create its indexes. **Credentials never go in this file** — they come from the
environment (see `.env.example`).

## Privacy

The Stop hook mirrors **every transcript line** — including tool output and
file contents — into the local archive. It stays on your machine and is never
committed (`data/` is gitignored), but you should choose deliberately which
projects you point it at. **Read [`docs/PRIVACY.md`](docs/PRIVACY.md) before
installing**, and see it for how to exclude projects, disable archiving, and
purge data.

## Extending

SurrealDB is a graph database, so memory isn't limited to flat rows. Add your
own domain tables and relations and surface them at session start — see
[`examples/content_graph/`](examples/content_graph/) for a worked template.

## License

MIT — see [LICENSE](LICENSE). Built on SurrealDB, FastMCP, and fastembed;
not affiliated with or endorsed by their respective projects.
