#!/usr/bin/env python3
"""UserPromptSubmit hook — memory-search nudge.

Injects one short standing instruction on every user prompt so the agent
reflexively checks the `memory` MCP for prior decisions/history before
answering project-specific questions, instead of answering from assumption.

Injects the INSTRUCTION, never data — the agent runs and filters the search
itself, staying the gatekeeper of its own context. Stdlib only; never blocks.
"""
import json
import sys

NUDGE = ("Before answering about this project's decisions, history, or past "
         "work: search the memory MCP first.")


def main() -> int:
    try:
        json.load(sys.stdin)  # consume stdin (payload unused)
    except Exception:
        pass
    try:
        json.dump({
            "continue": True,
            "suppressOutput": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": NUDGE,
            },
        }, sys.stdout)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
