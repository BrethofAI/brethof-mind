#!/usr/bin/env python3
"""Git post-commit hook — record each commit into the project's memory table.

Install per repo by copying (or symlinking) this file to .git/hooks/post-commit
and making it executable:

  cp ~/.claude/hooks/save_commit.py /path/to/repo/.git/hooks/post-commit
  chmod +x /path/to/repo/.git/hooks/post-commit

The project is chosen from projects.json by matching the repo's path (see
_sm.py). Never blocks a commit — any failure is swallowed.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sm import load_config, detect_project, query  # noqa: E402


def git_info() -> dict:
    try:
        return {
            "hash": subprocess.check_output(
                ["git", "log", "-1", "--format=%H"], text=True).strip(),
            "message": subprocess.check_output(
                ["git", "log", "-1", "--format=%s"], text=True).strip(),
            "branch": subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip(),
            "files": subprocess.check_output(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", "--"],
                text=True).strip().replace("\n", ", "),
        }
    except Exception:
        return {}


def main():
    cfg = load_config()
    table = detect_project(os.getcwd(), cfg)
    info = git_info()
    if not info:
        return
    short = info["hash"][:8]
    safe_msg = info["message"].replace("\\", "\\\\").replace("'", "\\'")
    safe_files = info["files"].replace("\\", "\\\\").replace("'", "\\'")
    query(
        f"CREATE {table}:commit_{short} SET "
        f"type = 'commit', "
        f"title = '{safe_msg}', "
        f"content = 'hash: {info['hash']}, branch: {info['branch']}, files: {safe_files}', "
        f"scope = 'project', "
        f"updated_at = time::now();",
        cfg,
    )


if __name__ == "__main__":
    main()
