# Privacy

Read this before you install. brethof-mind is **local-first by design**, but
the Stop hook is powerful and you should understand exactly what it stores.

## What gets stored

The `chat_stop` hook mirrors **every line of every Claude Code transcript** for
a configured project into the `<project>_chat` table. That includes:

- your prompts,
- the assistant's replies **and its thinking**,
- tool calls and their results — which means **file contents you read, command
  output, and anything else that flows through a tool**.

If a secret (API key, token, password) appears in your terminal output or a
file you open during a session, it will be stored in the archive, because the
archive is a faithful mirror of the transcript.

## Where it goes — and where it does not

- **It stays on your machine.** SurrealDB runs in a local container bound to
  `localhost`. Nothing is sent to any third party. Embeddings are computed
  locally (fastembed, on CPU) — no external API.
- **It never goes into git.** The `data/` directory is gitignored. The DB is
  the most sensitive thing here; treat the `data/` folder like you'd treat
  `~/.bash_history` or your shell transcript — back it up privately, never
  commit or share it.

## Controls

**Exclude a project entirely.** Only directories matched in `projects.json` are
archived under their key; everything else falls into `default_project`. To keep
a sensitive repo out of memory, give it no match entry and point
`default_project` at a table you don't search — or run that repo's sessions
with the hooks disabled.

**Turn off live archiving.** Remove the `Stop` hook from your
`~/.claude/settings.json`. You keep curated memory and search; you lose the
automatic transcript mirror.

**Lock down the database.** `root/root` is a convenience default for a
localhost-only setup. Set real `SURREALDB_USER`/`SURREALDB_PASS` in `.env`
before exposing the port anywhere, and don't publish port 8200.

## Purging

Delete everything for one project's archive:

```surql
DELETE <project>_chat;
```

Delete a single session:

```surql
DELETE <project>_chat WHERE session_id = '<id>';
```

Nuke the whole store: stop the container and delete the `data/` directory.

## In short

Local-first, never committed, fully under your control — but it is a complete
record of your sessions. Decide deliberately which projects you point it at.
