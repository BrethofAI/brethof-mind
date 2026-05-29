---
description: Distill this session into brethof-mind memory (run before /compact)
---
Curate this session into memory. Be thorough — this is what stops you (or the
user) having to remember everything across sessions and compactions.

1. Read the MEMORY INDEX (`get_memory <default_project>:memory_index`) for the
   current tables, areas, id patterns, and rules. Navigate from it.
2. Walk the session and group what happened by AREA (the areas listed in your
   index — e.g. backend, frontend, infra, docs, whatever your index defines).
3. For EACH touched area:
   - **state** — UPSERT `state:<area>` (ONE row, predictable id, never fork):
     `status`, `recent_changes` (prepend a dated one-liner), `next_actions`,
     `updated_at`. Use pointer fields for anything volatile (a fast-moving
     value → link/where-to-look, not the value itself).
   - **knowledge** — save genuinely new decisions / facts / runbooks / gotchas
     to that area's curated table. Stamp `source='conversation'`,
     `captured_at`=today, `area`. Update-in-place if it refines an existing
     record (don't duplicate).
   - **rules** — if the user gave a new correction/convention, add it to
     `rules` (`area`=<area>, or `'all'` if cross-cutting); set `pin=true` if it
     is a golden rule that must surface every session.
   - **content** — log anything shipped to your content/ledger tables (git
     commits are auto-captured by the post-commit hook).
4. **Supersede:** anything this session made FALSE → delete it (or relate
   `supersedes`). Do not keep stale-but-old-looking records — history lives in
   the chat archive, so deletion from curated memory is safe and recoverable.
5. **Volatility is a judgment, not a rule:** store a value only if it's stable
   for that area; if it moves often, store a pointer to where the live value
   lives. Either way stamp source + date so the next read can judge freshness.
6. Use predictable ids and the existing tables. NEVER invent a new table
   without registering it in the index first.
7. Report a short per-area summary: state updated · N saved · M superseded.
