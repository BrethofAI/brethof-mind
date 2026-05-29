---
description: Recall from memory using vector + graph + keyword (not just keyword)
argument-hint: <topic or question>
---
Recall everything relevant to: $ARGUMENTS

Do NOT default to a single keyword search. Use the right modalities (see the
MEMORY INDEX `<default_project>:memory_index` for which table + modality fits
each need):

1. **semantic_search** (vector) — meaning matches in curated memory.
2. **search_chat** (vector) — what was discussed/tried in past sessions.
3. **search_chat_text / search_memory** (BM25) — EXACT strings: file paths,
   error messages, commit hashes, flags, names.
4. **query_raw graph traversal** (`->edges`) — relationships: what references
   what, what supersedes what, parent/child links, etc.
5. **get_memory(id)** — read full records the searches surface (previews are
   truncated).

Then synthesize ONE grounded answer:
- Cite the record ids you used.
- Check provenance dates; flag anything that looks stale or volatile.
- If the answer isn't in memory, say so plainly — never assert memory contents
  from assumption.
