-- ════════════════════════════════════════════════════════════════════════
-- brethof-mind — field contract reference.
--
-- This file DOCUMENTS the shape of the data. It is NOT applied directly:
-- scripts/init_db.py generates the real DEFINE TABLE / DEFINE INDEX
-- statements per project from your projects.json (table names are your
-- project keys). Tables are SCHEMALESS, so these fields are a contract for
-- the code that reads/writes them, not an enforced schema.
--
-- For each project key K, init_db.py creates two tables: K and K_chat.
-- It also creates one project-independent graph relation: supersedes.
-- ════════════════════════════════════════════════════════════════════════


-- ── Curated memory table  (one per project key, e.g. `global`) ───────────
-- The gold layer: decisions, architecture notes, status, feedback, etc.
-- Written by the save_memory MCP tool.
--
--   type          string   decision | architecture | project_status | bug |
--                          feedback | reference | snippet | commit
--   title         string   short descriptive title
--   content       string   full text of the memory
--   scope         string   'project'
--   embedding     number[] sentence-transformer vector (dim = embedding_dim)
--   updated_at    datetime set by save_memory
--   obsolete      bool     true → hidden from load_project
--   pin           bool     true → always surfaced by the SessionStart hook
--   superseded_at      datetime
--   superseded_reason  string
--   superseded_by      record   link to the replacement (see `supersedes`)
--
-- Indexes (created by init_db.py): embedding (HNSW vector); content + title
-- (full-text BM25, via the `memory_text` analyzer — lowercase/ascii/snowball);
-- updated_at; obsolete.


-- ── Chat archive table  (one per project key, e.g. `global_chat`) ────────
-- The raw layer: every Claude Code transcript line, mirrored by the
-- chat_stop hook (live) and scripts/ingest_transcripts.py (backfill).
--
--   session_id   string      Claude Code session id
--   project      string      project key
--   line_type    string      user | assistant | system | chunk_continuation | ...
--   role         string      user | assistant (message lines)
--   uuid         string      transcript line uuid
--   parent_uuid  string      parent line uuid
--   parent       record      link to parent row (graph traversal)
--   seq          int         line index within the transcript (backfill only)
--   timestamp    datetime    line timestamp
--   text         string      extracted human-readable text (capped)
--   embedding    number[]    sentence-transformer vector, conversation lines only
--   raw          object      the complete original JSON line
--   transcript   string      source .jsonl path
--   chunk_seq / chunk_total / parent_chunk / chunk_text
--                            present only when a single oversized line was
--                            split across multiple rows (see chat_stop hook)
--
-- Indexes (created by init_db.py): embedding (HNSW), session_id, timestamp,
-- line_type, and text — the text index is FULLTEXT (BM25, via the memory_text
-- analyzer) so the archive is keyword-searchable (search_chat_text), not only
-- vector-searchable (search_chat). Built CONCURRENTLY so re-indexing a large
-- existing archive doesn't block past the HTTP timeout.


-- ── Commit ledger  (one per project key, e.g. `global_commit`) ───────────
-- Git commits, written by the save_commit hook + MCP tool. Deliberately a
-- SEPARATE table from the curated <project> note store: a commit is recorded
-- on every commit, so left in the note store they dominate the SessionStart
-- recent-memory view. Kept here, they stay searchable without the noise.
--
--   type        string    'commit'
--   title       string    commit subject line
--   content     string    'hash: ..., branch: ..., files: ...'
--   scope       string    'project'
--   updated_at  datetime
--
-- Indexes (created by init_db.py): updated_at; content (FULLTEXT BM25).


-- ── supersedes  (graph relation, project-independent) ────────────────────
-- new_record -> supersedes -> old_record. Lets you walk replacement chains:
--   SELECT ->supersedes->memory FROM <key>:some_id;
DEFINE TABLE IF NOT EXISTS supersedes TYPE RELATION SCHEMALESS PERMISSIONS NONE;
DEFINE INDEX IF NOT EXISTS idx_supersedes_in  ON supersedes FIELDS in;
DEFINE INDEX IF NOT EXISTS idx_supersedes_out ON supersedes FIELDS out;
