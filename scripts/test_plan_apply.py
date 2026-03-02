from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path when executing this script directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.mcp.client import McpClient
from src.mcp.tools_jira import apply_scrum_master_action, plan_scrum_master_action


def _example_params(action: str) -> dict[str, Any]:
    examples: dict[str, dict[str, Any]] = {
        "create_issue": {
            "summary": "[Demo] Issue creada por Scrum Master Assistant",
            "description": "Issue de prueba para validar plan/apply.",
            "issue_type": "Task",
            "story_points": 2,
        },
        "comment_issue": {
            "key": "SCC-1",
            "comment": "Comentario de prueba desde Scrum Master Assistant.",
        },
        "transition_issue": {
            "key": "SCC-1",
            "target_status": "In Progress",
        },
        "assign_issue": {
            "key": "SCC-1",
            "assignee": "Samuel Esteban Cano Chocce",
        },
        "update_priority": {
            "key": "SCC-1",
            "priority_name": "High",
        },
        "edit_issue": {
            "key": "SCC-1",
            "summary": "SCC-1 actualizado por script plan/apply",
            "description": "Descripción actualizada por script de prueba.",
        },
        "create_subtask": {
            "parent_key": "SCC-1",
            "summary": "Subtarea demo desde script plan/apply",
            "description": "Subtarea para validar flujo de confirmación.",
            "issue_type": "Sub-task",
            "story_points": 1,
        },
        "move_to_active_sprint": {
            "key": "SCC-1"
            # opcional: "board_id": 2
        },
    }
    return examples.get(action, {})


def _print_json(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _extract_result(raw_result: Any) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        result = raw_result.get("result")
        if isinstance(result, dict):
            return result
        return raw_result
    return {"value": raw_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Jira Scrum Master assistant plan/apply actions through MCP.",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "create_issue",
            "comment_issue",
            "transition_issue",
            "assign_issue",
            "update_priority",
            "edit_issue",
            "create_subtask",
            "move_to_active_sprint",
        ],
        help="Action to plan/apply.",
    )
    parser.add_argument(
        "--params-json",
        default=None,
        help=(
            "JSON object with action params. If omitted, built-in example params are used."
        ),
    )
    parser.add_argument(
        "--reason",
        default="manual test from script",
        help="Reason/audit text for plan_action.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute apply_action after plan_action.",
    )

    args = parser.parse_args()

    if args.params_json:
        try:
            params = json.loads(args.params_json)
            if not isinstance(params, dict):
                raise ValueError("params-json must decode to a JSON object")
        except Exception as exc:
            print(f"Error parsing --params-json: {exc}")
            return 2
    else:
        params = _example_params(args.action)

    if not params:
        print("No params provided and no example params available for this action.")
        return 2

    client = McpClient()
    print(f"MCP endpoint: {client.config.endpoint}")
    print(f"Action: {args.action}")
    _print_json("Params", params)

    try:
        plan = plan_scrum_master_action(
            client,
            action=args.action,
            params=params,
            reason=args.reason,
        )
    except Exception as exc:
        print(f"plan_action failed: {type(exc).__name__}: {exc}")
        return 1

    plan_result = _extract_result(plan.raw_result)
    _print_json("Plan Result", plan_result)

    plan_id = plan_result.get("plan_id")
    if not plan_id:
        print("No plan_id returned; cannot continue to apply.")
        return 1

    if not args.apply:
        print("\nPlan created successfully. Re-run with --apply to execute it.")
        print(f"plan_id={plan_id}")
        return 0

    print("\nAbout to apply the plan with explicit confirmation...")
    try:
        apply = apply_scrum_master_action(
            client,
            plan_id=plan_id,
            confirm=True,
            confirmation_text="CONFIRM",
        )
    except Exception as exc:
        print(f"apply_action failed: {type(exc).__name__}: {exc}")
        print("Tip: verify JIRA_ALLOW_WRITES=true in the MCP server environment.")
        return 1

    apply_result = _extract_result(apply.raw_result)
    _print_json("Apply Result", apply_result)
    print("\nDone. Check Jira board and logs/sm_assistant_audit.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
