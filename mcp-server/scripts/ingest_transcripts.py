#!/usr/bin/env python3
"""Backfill existing Claude Code transcripts into the <project>_chat tables.

The chat_stop hook captures NEW turns live. Run this once to import the
history you already have. Idempotent: deterministic record ids + UPSERT, so
re-running re-syncs in place without duplicates. It also seeds the hook's
per-session byte offset so live capture continues with no gap or overlap.

  python scripts/ingest_transcripts.py                 # all transcripts
  python scripts/ingest_transcripts.py --one FILE.jsonl # a single transcript

Transcript location defaults to ~/.claude/projects; override with
$CLAUDE_PROJECTS_DIR. Which project each transcript maps to is decided by
projects.json (substring match on the transcript path).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _config import load_config, detect_project, surreal_query, embed_texts  # noqa: E402

PROJECTS_DIR = os.environ.get(
    "CLAUDE_PROJECTS_DIR", os.path.expanduser("~/.claude/projects")
)
CHAT_SYNC_DIR = os.path.expanduser("~/.claude/chat_sync")
UPSERT_MAX_BYTES = 700_000   # keep each /sql body well under the server limit
LINK_BATCH = 200
EMBED_CAP = 4000
TEXT_CAP = 50000


def extract_text(d: dict):
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


def esc_id(rid: str) -> str:
    return "`" + str(rid).replace("`", "") + "`"


def upsert_batch(table: str, rows: list, cfg: dict) -> int:
    """UPSERT rows in size-bounded /sql requests. Returns error count."""
    errors = 0
    batch: list[str] = []
    size = 0

    def flush():
        nonlocal errors, batch, size
        if not batch:
            return
        res = surreal_query(";\n".join(batch) + ";", cfg, timeout=120.0)
        errs = [r for r in res if isinstance(r, dict) and r.get("status") != "OK"]
        for e in errs[:2]:
            print(f"    UPSERT err: {json.dumps(e)[:240]}", flush=True)
        errors += len(errs)
        batch = []
        size = 0

    for rid, row in rows:
        payload = json.dumps(row, ensure_ascii=False, default=str)
        stmt = f"UPSERT {table}:{esc_id(rid)} CONTENT {payload}"
        nbytes = len(stmt.encode("utf-8"))
        if batch and size + nbytes > UPSERT_MAX_BYTES:
            flush()
        batch.append(stmt)
        size += nbytes
    flush()
    return errors


def ingest_file(path: str, cfg: dict) -> dict:
    project = detect_project(path, cfg)
    table = f"{project}_chat"
    rows = []
    to_embed = []
    session_id = ""
    title = ""
    first_ts = last_ts = None

    with open(path, encoding="utf-8") as f:
        for seq, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            session_id = d.get("sessionId") or session_id
            if d.get("type") == "custom-title" and d.get("customTitle"):
                title = d["customTitle"]
            ts = d.get("timestamp")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            uuid = d.get("uuid")
            rec_id = uuid if uuid else f"{d.get('sessionId', 'x')}-{seq}"
            text, embed = extract_text(d)
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            row = {
                "session_id": d.get("sessionId"),
                "project": project,
                "line_type": d.get("type", "?"),
                "role": msg.get("role"),
                "uuid": uuid,
                "parent_uuid": d.get("parentUuid"),
                "seq": seq,
                "timestamp": ts,
                "text": text[:TEXT_CAP],
                "raw": d,
                "transcript": path,
            }
            if embed and text.strip():
                to_embed.append((len(rows), text[:EMBED_CAP]))
            rows.append((rec_id, row))

    if session_id:
        sess_row = {
            "session_id": session_id,
            "project": project,
            "line_type": "session",
            "title": title,
            "text": title,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "line_count": len(rows),
            "transcript": path,
        }
        if title.strip():
            to_embed.append((len(rows), title))
        rows.append((f"{session_id}-session", sess_row))

    embedded = 0
    if to_embed:
        vecs = embed_texts([t for _, t in to_embed], cfg)
        for (ridx, _), vec in zip(to_embed, vecs):
            rows[ridx][1]["embedding"] = vec
            embedded += 1

    errors = upsert_batch(table, rows, cfg)

    # parent graph edges
    links = [(row["uuid"], row["parent_uuid"]) for _, row in rows
             if row.get("uuid") and row.get("parent_uuid")]
    for i in range(0, len(links), LINK_BATCH):
        chunk = links[i:i + LINK_BATCH]
        stmts = [f"UPDATE {table}:{esc_id(u)} SET parent = {table}:{esc_id(pu)}"
                 for u, pu in chunk]
        res = surreal_query(";\n".join(stmts) + ";", cfg, timeout=120.0)
        errors += sum(1 for r in res
                      if isinstance(r, dict) and r.get("status") != "OK")

    # seed the live hook's offset so capture resumes with no gap
    try:
        os.makedirs(CHAT_SYNC_DIR, exist_ok=True)
        sp = os.path.join(CHAT_SYNC_DIR, f"{session_id}.json")
        st = {}
        if os.path.exists(sp):
            try:
                st = json.load(open(sp))
            except Exception:
                st = {}
        st["offset"] = os.path.getsize(path)
        with open(sp, "w") as sf:
            json.dump(st, sf)
    except Exception:
        pass

    return {"project": project, "table": table, "lines": len(rows),
            "embedded": embedded, "links": len(links), "errors": errors}


def main() -> int:
    cfg = load_config()
    args = sys.argv[1:]
    if "--one" in args:
        files = [args[args.index("--one") + 1]]
    else:
        files = sorted(glob.glob(f"{PROJECTS_DIR}/**/*.jsonl", recursive=True))

    print(f"transcripts dir: {PROJECTS_DIR}", flush=True)
    print(f"transcripts: {len(files)}", flush=True)
    if not files:
        print("Nothing to ingest. Set $CLAUDE_PROJECTS_DIR if your transcripts "
              "live elsewhere.", flush=True)
        return 0

    totals: dict[str, dict] = {}
    for n, path in enumerate(files, 1):
        try:
            st = ingest_file(path, cfg)
        except Exception as e:
            print(f"[{n}/{len(files)}] FAIL {path}: {e}", flush=True)
            continue
        t = totals.setdefault(
            st["table"], {"lines": 0, "embedded": 0, "links": 0, "errors": 0})
        for k in ("lines", "embedded", "links", "errors"):
            t[k] += st[k]
        print(f"[{n}/{len(files)}] {st['table']:<14} "
              f"lines={st['lines']:<6} embedded={st['embedded']:<6} "
              f"links={st['links']:<6} errors={st['errors']}  "
              f"{path.split('/')[-1][:40]}", flush=True)

    print("\n=== TOTALS ===", flush=True)
    for table, t in sorted(totals.items()):
        print(f"  {table:<14} lines={t['lines']:<7} embedded={t['embedded']:<7} "
              f"links={t['links']:<7} errors={t['errors']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
