# Example: a domain content graph

brethof-mind stores *general* memory (decisions, status, bugs) in the
per-project tables. But because the backend is SurrealDB — a graph database —
you can add your own **domain tables and relations** and have the SessionStart
hook surface them automatically.

This example models a content pipeline: **episodes** that **cover** topics and
**state** positions (claims), with **callback** edges between episodes. Swap in
your own nouns — articles, experiments, tickets, customers, releases.

## Files

| File | What it is |
|---|---|
| `schema.surql` | `DEFINE TABLE`/`RELATION` for the graph. Apply once. |
| `session_augment.py` | A function you paste into `hooks/load_memory.py` to surface the graph at session start. |

## Apply the schema

```bash
curl -X POST "$SURREALDB_URL/sql" \
  -H "surreal-ns: $SURREALDB_NS" -H "surreal-db: $SURREALDB_DB" \
  -u "$SURREALDB_USER:$SURREALDB_PASS" \
  --data-binary @examples/content_graph/schema.surql
```

## Surface it at session start

Copy `graph_context()` from `session_augment.py` into `hooks/load_memory.py`,
then append its output to `blocks` in `main()` for the project(s) where it's
relevant:

```python
graph = graph_context(cfg)
if graph:
    blocks.append(graph)
```

Now every session in that project starts with your live content graph in
context — what exists, what's been claimed, what links to what — without you
pasting it in. This is exactly how the project this tool came from keeps a
show's episode/claim graph in front of the agent.

## Why a graph, not just rows

The payoff is traversal. Once episodes link to the claims they make and to
earlier episodes they call back to, you can ask things like "what have we
already argued about topic X, and which episode first said it" in one query:

```surql
SELECT ->states->claim.* FROM episode WHERE number = 12;
SELECT <-covers<-episode.* FROM topic WHERE slug = 'gpu-buying';
```
