"""Shared helpers for the brethof-mind Claude Code hooks.

The hooks are installed together into ~/.claude/hooks/, so they import this
sibling module. It is intentionally a lean mirror of mcp-server/_config.py
(stdlib only, no fastembed/httpx) so the SessionStart and UserPromptSubmit
hooks stay fast and dependency-free. Keep the two in sync.

Config discovery for projects.json (first hit wins):
  1. $BRETHOF_MIND_CONFIG
  2. ~/.config/brethof-mind/projects.json
  3. built-in default (single 'global' project)

Environment overrides the JSON 'surrealdb' block when set:
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
    "default_project": "global",
    "projects": [{"key": "global", "match": []}],
}


def _config_path() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("BRETHOF_MIND_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".config" / "brethof-mind" / "projects.json")
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = _config_path()
    if path:
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    sd = cfg.setdefault("surrealdb", {})
    sd["url"] = os.environ.get("SURREALDB_URL", sd.get("url", "http://localhost:8200"))
    sd["ns"] = os.environ.get("SURREALDB_NS", sd.get("ns", "ai"))
    sd["db"] = os.environ.get("SURREALDB_DB", sd.get("db", "memory"))
    sd["user"] = os.environ.get("SURREALDB_USER", "root")
    sd["pass"] = os.environ.get("SURREALDB_PASS", "root")
    return cfg


def detect_project(cwd: str, cfg: dict) -> str:
    c = (cwd or "").lower().replace("\\", "/")
    for proj in cfg.get("projects", []):
        for m in proj.get("match", []):
            if m and m.lower() in c:
                return proj["key"]
    return cfg.get("default_project", "global")


def query(sql: str, cfg: dict, timeout: float = 6.0):
    """Synchronous SurrealQL over HTTP. Returns parsed list, or [] on failure
    (hooks must never raise — a failed query just yields no context)."""
    sd = cfg["surrealdb"]
    auth = base64.b64encode(f"{sd['user']}:{sd['pass']}".encode()).decode()
    req = urllib.request.Request(
        sd["url"].rstrip("/") + "/sql",
        data=sql.encode("utf-8"),
        headers={
            "Accept": "application/json",
            "surreal-ns": sd["ns"],
            "surreal-db": sd["db"],
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return []


def result_rows(result_set) -> list:
    if isinstance(result_set, dict):
        rows = result_set.get("result", [])
        return rows if isinstance(rows, list) else []
    return []
