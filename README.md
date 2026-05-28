# brethof-mind

**Long-term memory for [Claude Code](https://docs.claude.com/en/docs/claude-code), built on [SurrealDB](https://surrealdb.com).**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![SurrealDB v3.0.5](https://img.shields.io/badge/SurrealDB-v3.0.5-ff00a0.svg)
![Local-first](https://img.shields.io/badge/local--first-no%20cloud-2ea44f.svg)

*Don't summarize your memory — search it. The complete session history for
Claude Code: full-text + vector + graph, 100% local, no API.*

Claude Code forgets everything between sessions. brethof-mind gives it a
persistent, searchable memory: an MCP server with eleven tools and four
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

### Two memories, working together

brethof-mind keeps two stores, and the pairing is the point:

- **Curated memory** — what the agent writes down on purpose: decisions,
  architecture, status, your corrections. Dense and high-signal. Your *notes*.
- **Full chat memory** — every transcript line, captured automatically.
  Complete and raw. The *recording*.

Notes are fast but lossy; the recording is the insurance. When the notes missed
something, the full history still has it — searchable. You get the speed of a
curated view without ever depending on one.

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
- **Two memories, working together.** A dense *curated* layer (your notes)
  backed by a complete *chat archive* (the recording) — so recall never
  depends on what a summary kept.
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

## Works with

The memory tools run in any MCP-capable client; the hooks (auto-capture +
auto-load) come with Claude Code — and Claude Desktop runs Claude Code as its
engine, so you get the full experience in both.

- ✅ **Claude Code** — full: memory tools + automatic capture & recall (hooks)
- ✅ **Claude Desktop** — full: it runs on Claude Code, so the same hooks + tools apply
- 🔜 **OpenClaw** — coming soon
- 🔜 **Hermes** — coming soon (its design already targets this memory)

Any other MCP client can use the memory tools too — the automatic capture is
the Claude Code (and Desktop) piece.

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

The MCP server exposes eleven tools (registered above as `memory`):

| Tool | What it does |
|---|---|
| `load_project(project)` | Dump a project's recent curated memory. |
| `save_memory(project, id, type, title, content)` | UPSERT a curated prose record/note (auto-embedded). |
| `save_record(project, id, fields, embed_text?)` | UPSERT a structured record — `fields` is a JSON object, each key becomes a real queryable field. `embed_text` enables semantic search. |
| `search_memory(query, project?)` | Full-text search over curated memory (stemmed, BM25-ranked). |
| `semantic_search(query, project?, top_k?)` | Vector search over curated memory. |
| `search_chat(query, project?, top_k?)` | Vector search over the full chat archive. |
| `get_memory(record_id, project?)` | Fetch ONE record's full, untruncated content by id (search results are previews). |
| `list_memory(project, type?, limit?)` | Browse a project's record ids/titles (no content), newest first. |
| `recent_records(project, days?, where?, limit?)` | Recent structured rows from a table, newest first — a scoped/filtered reader for high-volume ledgers. |
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

**Overriding the detected project.** Path-substring matching misfiles work
whose cwd doesn't reflect the project — SSH/remote sessions, monorepos, or
driving one project from another's directory. Two overrides take precedence
over `match` (highest first):

- `BRETHOF_MIND_PROJECT=<key>` in the environment — pins the whole session.
  Set it before launching Claude Code when, e.g., you'll be operating a
  remote box from an unrelated local directory.
- A `.brethof-mind-project` file (first line = a project key) anywhere from
  the cwd upward — pins a directory tree whose path matches no `match`
  substring. Handy to commit into a repo whose folder name isn't in `match`.

## Compatibility

Built and tested against **SurrealDB v3.0.5** (pinned in `docker-compose.yml`).
SurrealQL evolves across major versions — 3.0, for instance, renamed the
full-text index clause from `SEARCH ANALYZER` to `FULLTEXT ANALYZER` — so a
newer server may need small tweaks.

`init_db.py` reads the running server's version and warns if its major version
differs from the tested one, so you get a clear heads-up rather than a cryptic
parse error. When you validate brethof-mind against a new SurrealDB release,
bump it in two places: the `image:` tag in `docker-compose.yml` and
`SUPPORTED_SURREALDB` in `mcp-server/_config.py`.

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
