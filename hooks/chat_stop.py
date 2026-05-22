#!/usr/bin/env python3
"""Stop hook — live chat-archive sync.

Runs at the end of every assistant turn (Claude Code `Stop` event). Mirrors
this session's NEW transcript lines into the project's <project>_chat table:
raw line kept whole, promoted fields, a sentence-transformer embedding on the
conversation lines, and a parent graph link. A per-session byte offset means
it never double-inserts or misses a line.

Capture only — no blocking. Robust by construction: ANY failure exits 0, so
the turn is never broken.

NOTE: this hook needs `fastembed` to embed lines, so wire it (in
~/.claude/settings.json) to the mcp-server virtualenv's python — see
settings.example.json. If fastembed is unavailable it still archives every
line, just without embeddings (backfill them later with ingest_transcripts.py).

Hardened against HTTP 413: SurrealDB body limits are raised to 64 MiB in
docker-compose; any single transcript line whose UPSERT would exceed
CHUNK_THRESHOLD is split into linked rows; a failed batch retries per
statement; the offset only advances when every row writes successfully.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sm import load_config, detect_project  # noqa: E402
import base64
import urllib.request

CFG = load_config()
STATE_DIR = os.path.expanduser("~/.claude/chat_sync")
TEXT_CAP = 50000
EMBED_CAP = 4000
BATCH_BYTES = 32 * 1024 * 1024       # 32 MiB SQL-batch ceiling (server limit 64)
CHUNK_THRESHOLD = 20 * 1024 * 1024   # a single row's UPSERT > 20 MiB → chunk it
CHUNK_PAYLOAD = 16 * 1024 * 1024     # each chunk row carries ~16 MiB of raw payload


def q(sql: str) -> list:
    """Raising SurrealQL client — callers depend on exceptions for fallback."""
    sd = CFG["surrealdb"]
    auth = base64.b64encode(f"{sd['user']}:{sd['pass']}".encode()).decode()
    req = urllib.request.Request(
        sd["url"].rstrip("/") + "/sql",
        data=sql.encode("utf-8"),
        headers={"Accept": "application/json", "surreal-ns": sd["ns"],
                 "surreal-db": sd["db"], "Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def esc_id(rid: str) -> str:
    return "`" + str(rid).replace("`", "") + "`"


def extract_text(d):
    """Return (text, embed_flag). embed_flag True only for real conversation."""
    t = d.get("type")
    msg = d.get("message") if isinstance(d.get("message"), dict) else None
    if t == "user" and msg:
        c = msg.get("content")
        if isinstance(c, str):
            return c, True
        if isinstance(c, list):
            out = []
            for b in c:
                if not isinstance(b, dict):
                    continue
                bc = b.get("content")
                if isinstance(bc, str):
                    out.append(bc)
                elif isinstance(bc, list):
                    for x in bc:
                        if isinstance(x, dict) and x.get("type") == "text":
                            out.append(x.get("text", ""))
            return "\n".join(out), False
    if t == "assistant" and msg:
        c = msg.get("content")
        if isinstance(c, list):
            out = []
            for b in c:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    out.append(b.get("text", ""))
                elif bt == "thinking":
                    out.append(b.get("thinking", ""))
                elif bt == "tool_use":
                    out.append(f"[tool_use: {b.get('name', '?')}]")
            return "\n".join(s for s in out if s), True
    for k in ("content", "lastPrompt", "customTitle"):
        v = d.get(k)
        if isinstance(v, str):
            return v, False
    return "", False


def log(msg: str):
    try:
        with open(os.path.join(STATE_DIR, "chat_stop.log"), "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def maybe_chunk_row(rec_id, row):
    """Split a row whose UPSERT would exceed CHUNK_THRESHOLD into N linked rows.

    Reconstruction: fetch row; if chunk_total > 1, fetch siblings
    WHERE parent_chunk = rec_id ORDER BY chunk_seq and concat chunk_text.
    """
    full_sql = "UPSERT _:_ CONTENT " + json.dumps(row, ensure_ascii=False, default=str)
    if len(full_sql.encode("utf-8")) < CHUNK_THRESHOLD:
        return [(rec_id, row, False)]
    raw_obj = row.get("raw", {})
    raw_json = json.dumps(raw_obj, ensure_ascii=False, default=str)
    parts = [raw_json[i:i + CHUNK_PAYLOAD]
             for i in range(0, len(raw_json), CHUNK_PAYLOAD)]
    total = len(parts)
    log(f"chunking row {rec_id}: raw_json {len(raw_json)}B → {total} parts")
    first = dict(row)
    first["raw"] = None
    first["chunk_seq"] = 1
    first["chunk_total"] = total
    first["chunk_text"] = parts[0]
    result = [(rec_id, first, False)]
    for i, part in enumerate(parts[1:], start=2):
        cont_id = f"{rec_id}__chunk_{i}"
        cont_row = {
            "session_id": row.get("session_id"),
            "project": row.get("project"),
            "line_type": "chunk_continuation",
            "parent_chunk": rec_id,
            "chunk_seq": i,
            "chunk_total": total,
            "chunk_text": part,
            "timestamp": row.get("timestamp"),
        }
        result.append((cont_id, cont_row, True))
    return result


def send_with_fallback(stmts: list) -> bool:
    """Send statements in size-bounded batches; on batch failure retry each
    statement individually. Returns True iff ALL statements succeeded."""
    all_ok = True
    batch, size = [], 0
    for st in stmts:
        nb = len(st.encode("utf-8"))
        if batch and size + nb > BATCH_BYTES:
            all_ok = _flush_batch(batch) and all_ok
            batch, size = [], 0
        batch.append(st)
        size += nb
    if batch:
        all_ok = _flush_batch(batch) and all_ok
    return all_ok


def _flush_batch(batch: list) -> bool:
    try:
        q(";\n".join(batch) + ";")
        return True
    except Exception as e_batch:
        log(f"batch upsert fail ({len(batch)} stmts), falling back per-stmt: {e_batch}")
        all_ok = True
        for st in batch:
            try:
                q(st + ";")
            except Exception as e_one:
                preview = st[:200].replace("\n", " ")
                log(f"  single upsert fail: {e_one} | stmt[0:200]={preview!r}")
                all_ok = False
        return all_ok


def main() -> int:
    try:
        inp = json.load(sys.stdin)
    except Exception:
        return 0

    transcript = inp.get("transcript_path")
    session_id = inp.get("session_id") or ""
    cwd = inp.get("cwd") or ""
    if not transcript or not session_id or not os.path.exists(transcript):
        return 0

    project = detect_project(cwd, CFG)
    table = f"{project}_chat"

    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{session_id}.json")
    state = {"offset": 0}
    if os.path.exists(state_path):
        try:
            state.update(json.load(open(state_path)))
        except Exception:
            pass

    rows = []
    to_embed = []
    try:
        with open(transcript, encoding="utf-8") as f:
            f.seek(state.get("offset", 0))
            while True:
                raw_line = f.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                uuid = d.get("uuid")
                rec_id = uuid or (
                    session_id + "-" + hashlib.sha1(line.encode()).hexdigest()[:16])
                text, embed = extract_text(d)
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                row = {
                    "session_id": session_id,
                    "project": project,
                    "line_type": d.get("type", "?"),
                    "role": msg.get("role"),
                    "uuid": uuid,
                    "parent_uuid": d.get("parentUuid"),
                    "timestamp": d.get("timestamp"),
                    "text": text[:TEXT_CAP],
                    "raw": d,
                    "transcript": transcript,
                }
                if embed and text.strip():
                    to_embed.append((len(rows), text[:EMBED_CAP]))
                rows.append((rec_id, row))
            new_offset = f.tell()
    except Exception as e:
        log(f"sync read fail: {e}")
        return 0

    if to_embed:
        try:
            from fastembed import TextEmbedding
            model = TextEmbedding(
                CFG.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"))
            vecs = list(model.embed([t for _, t in to_embed]))
            for (idx, _), vec in zip(to_embed, vecs):
                rows[idx][1]["embedding"] = [float(x) for x in vec]
        except Exception as e:
            log(f"embed fail (rows still stored unembedded): {e}")

    expanded = []
    for rid, row in rows:
        for ch_rid, ch_row, _is_cont in maybe_chunk_row(rid, row):
            expanded.append((ch_rid, ch_row))

    if not expanded:
        state["offset"] = new_offset
        try:
            json.dump(state, open(state_path, "w"))
        except Exception:
            pass
        return 0

    stmts = []
    for rid, row in expanded:
        stmts.append(f"UPSERT {table}:{esc_id(rid)} CONTENT "
                     + json.dumps(row, ensure_ascii=False, default=str))
    for rid, row in expanded:
        if row.get("uuid") and row.get("parent_uuid"):
            stmts.append(f"UPDATE {table}:{esc_id(row['uuid'])} "
                         f"SET parent = {table}:{esc_id(row['parent_uuid'])}")

    if not send_with_fallback(stmts):
        log(f"partial fail — offset NOT advanced "
            f"(was {state.get('offset', 0)}, would be {new_offset}); retry next turn")
        return 0

    state["offset"] = new_offset
    try:
        json.dump(state, open(state_path, "w"))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
