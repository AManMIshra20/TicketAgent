#!/usr/bin/env python3
"""Hook: refuse to let a write tool be handed to the model.

CLAUDE.md section 5 says the human-review gate is enforced in three layers.
This is the layer the workshop calls a hook, and it sits outside all three:
a check that runs automatically, that the agent editing this repo cannot skip
or argue with.

The distinction matters. CLAUDE.md tells an agent what the rules are --
that is a request. `tests/test_safety_boundary.py` proves the rules hold --
but only when somebody runs it. This runs on every edit to the files that
define the model's capabilities, whether or not anyone remembered to ask.

Wired in `.claude/settings.json` as a PostToolUse hook on Write and Edit.
Exit code 2 tells Claude Code the edit is unacceptable and feeds this
message back, so the violation is corrected rather than committed.

Run it by hand any time:

    python .claude/hooks/check_tool_boundary.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "src" / "ticket_agent" / "tool_schemas.py"
AGENT = REPO / "src" / "ticket_agent" / "agent.py"

# The tools the model is permitted to see. Kept in step with
# tests/test_safety_boundary.py -- if you change one, change both.
ALLOWED = {
    "list_untriaged_tickets",
    "get_ticket",
    "search_tickets",
    "get_requester",
    "get_sop",
    "search_sops",
    "submit_triage_proposal",
    "submit_sop_proposal",
}

# Substrings that betray a mutating tool.
WRITE_VERBS = (
    "post", "create", "update", "delete", "add_", "assign", "publish",
    "send", "close", "merge", "write", "set_", "remove", "execute", "apply",
)


def _tool_names() -> set[str]:
    """Read the tool names out of the schema file without importing it.

    Parsed rather than imported so the check still runs when the module is
    mid-edit and would not import, and so a hook never executes repo code.
    """
    tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                names.add(value.value)
    return names


def _agent_imports_executor() -> bool:
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "executor" in node.module:
            return True
        if isinstance(node, ast.Import):
            if any("executor" in alias.name for alias in node.names):
                return True
    return False


def main() -> int:
    problems: list[str] = []

    if not SCHEMAS.exists():
        print(f"tool_schemas.py is missing at {SCHEMAS}", file=sys.stderr)
        return 2

    try:
        names = _tool_names()
    except SyntaxError as exc:
        # Mid-edit and unparseable. Not a boundary violation; say so and pass.
        print(f"tool_schemas.py does not parse yet ({exc}); boundary not checked.")
        return 0

    unexpected = names - ALLOWED
    if unexpected:
        problems.append(
            f"These tools are exposed to the model but are not on the allowlist: "
            f"{', '.join(sorted(unexpected))}."
        )

    write_shaped = {
        n for n in names
        if any(v in n.lower() for v in WRITE_VERBS) and not n.startswith("submit_")
    }
    if write_shaped:
        problems.append(
            f"These tool names look like writes: {', '.join(sorted(write_shaped))}."
        )

    if AGENT.exists():
        try:
            if _agent_imports_executor():
                problems.append(
                    "agent.py imports the executor. The agent must not be able "
                    "to reach it."
                )
        except SyntaxError:
            pass

    if problems:
        print(
            "BLOCKED: the model's tool surface would allow a write.\n\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\n\nThe human-approval gate in this project is a code boundary, not\n"
              "a prompt instruction (CLAUDE.md section 5). A tool that changes\n"
              "anything belongs in executor.py, reachable only from a proposal a\n"
              "human approved.\n\n"
              "If this is deliberate, update the allowlist in BOTH\n"
              "  .claude/hooks/check_tool_boundary.py\n"
              "  tests/test_safety_boundary.py\n"
              "and say why in the same commit.",
            file=sys.stderr,
        )
        return 2

    print(f"Tool boundary intact: {len(names)} tools, all read-only or submit-only.")
    return 0


if __name__ == "__main__":
    # Claude Code passes hook payload on stdin. We do not need it -- the check
    # is on the repo's state, not on what was edited -- but drain it so the
    # process never blocks on an unread pipe.
    if not sys.stdin.isatty():
        try:
            json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            pass
    sys.exit(main())
