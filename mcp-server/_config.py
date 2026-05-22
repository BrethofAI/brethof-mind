"""Shared config + SurrealDB access for the brethof-mind MCP server and scripts.

Single source of truth (server-side) for:
  - where the DB is (url / ns / db) and how to authenticate (user / pass)
  - the project map: which working directory maps to which memory table
  - a minimal synchronous SurrealQL HTTP client (stdlib only)
  - a lazy embedding helper (fastembed) shared by the server and ingest script

The hooks carry their own lean equivalent (hooks/_sm.py) so they stay
self-contained when copied into ~/.claude/hooks/ — keep the two in sync.

Config discovery for projects.json (first hit wins):
  1. $BRETHOF_MIND_CONFIG
  2. ~/.config/brethof-mind/projects.json
  3. <repo>/projects.json
  4. <repo>/projects.example.json   (so a fresh clone runs out of the box)
  5. built-in default (single 'global' project)

Environment variables override the JSON 'surrealdb' block when set:
  SURREALDB_URL, SURREALDB_NS, SURREALDB_DB, SURREALDB_USER, SURREALDB_PASS
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = {
    "surrealdb": {"url": "http://localhost:8200", "ns": "ai", "db": "memory"},
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "embedding_dim": 384,
    "default_project": "global",
    "projects": [{"key": "global", "match": []}],
}

_REPO = Path(__file__).resolve().parent.parent


def _config_path() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("BRETHOF_MIND_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".config" / "brethof-mind" / "projects.json")
    candidates.append(_REPO / "projects.json")
    candidates.append(_REPO / "projects.example.json")
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def load_config() -> dict:
    """Load projects.json (or defaults) and apply environment overrides."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    path = _config_path()
    if path:
        try:
            user_cfg = json.loads(path.read_text(encoding="utf-8"))
            cfg.update(user_cfg)
        except Exception:
            pass  # malformed config → fall back to defaults, never crash
    sd = cfg.setdefault("surrealdb", {})
    sd["url"] = os.environ.get("SURREALDB_URL", sd.get("url", "http://localhost:8200"))
    sd["ns"] = os.environ.get("SURREALDB_NS", sd.get("ns", "ai"))
    sd["db"] = os.environ.get("SURREALDB_DB", sd.get("db", "memory"))
    sd["user"] = os.environ.get("SURREALDB_USER", "root")
    sd["pass"] = os.environ.get("SURREALDB_PASS", "root")
    return cfg


def project_keys(cfg: dict) -> list[str]:
    """Ordered list of project table keys from the config."""
    return [p["key"] for p in cfg.get("projects", []) if p.get("key")]


def detect_project(cwd: str, cfg: dict) -> str:
    """Map an absolute working directory to a project key by substring match."""
    c = (cwd or "").lower().replace("\\", "/")
    for proj in cfg.get("projects", []):
        for m in proj.get("match", []):
            if m and m.lower() in c:
                return proj["key"]
    return cfg.get("default_project", "global")


def auth_header(cfg: dict) -> str:
    sd = cfg["surrealdb"]
    return "Basic " + base64.b64encode(
        f"{sd['user']}:{sd['pass']}".encode()
    ).decode()


def surreal_query(sql: str, cfg: dict, timeout: float = 60.0,
                  with_context: bool = True) -> list:
    """Minimal synchronous SurrealQL client (stdlib urllib).

    Returns the parsed JSON result list. Raises urllib errors on failure
    so callers can decide how to handle (scripts surface them; the server
    uses its own async client).

    with_context=True selects ns/db via headers (normal operation). Set it
    False to run at the connection root — needed to bootstrap the namespace
    and database, which cannot be header-selected until they exist."""
    sd = cfg["surrealdb"]
    headers = {"Accept": "application/json", "Authorization": auth_header(cfg)}
    if with_context:
        headers["surreal-ns"] = sd["ns"]
        headers["surreal-db"] = sd["db"]
    req = urllib.request.Request(
        sd["url"].rstrip("/") + "/sql",
        data=sql.encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── lazy embedding (fastembed) ───────────────────────────────────────────
_embed_model = None


def embed_texts(texts: list[str], cfg: dict) -> list[list[float]]:
    """Embed a batch of texts with the configured sentence-transformer.
    Model loads once on first call (~23 MB download the very first time)."""
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(
            cfg.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2")
        )
    return [[float(x) for x in v] for v in _embed_model.embed(texts)]


def embed_one(text: str, cfg: dict) -> list[float]:
    return embed_texts([text], cfg)[0]
