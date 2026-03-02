"""HTTP-based Jira MCP-style server.

This is *not* a full Model Context Protocol server implementation yet,
but it exposes the same tool IDs that the Python client expects, using
simple HTTP JSON endpoints. It is enough to validate Jira + agents
end-to-end, and can later be wrapped or replaced by a real MCP server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import unicodedata
from uuid import uuid4
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

from jira_mcp_server import jira_client
from src.llm.github_models_client import GithubModelsClient


app = FastAPI(title="AgileAgents Jira MCP-style Server")


@dataclass
class PendingActionPlan:
    """In-memory plan for two-phase Jira writes."""

    plan_id: str
    action: str
    params: Dict[str, Any]
    reason: str | None
    preview: str
    created_at: str


_PENDING_PLANS: dict[str, PendingActionPlan] = {}


_AUDIT_LOG_PATH = Path(os.getenv("JIRA_ASSISTANT_AUDIT_LOG", "logs/sm_assistant_audit.jsonl"))


def _write_audit_log(event: Dict[str, Any]) -> None:
    """Append one JSON event to the local assistant audit log."""

    _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with _AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _create_plan(*, action: str, params: Dict[str, Any], reason: str | None) -> Dict[str, Any]:
    """Create and store a plan, then return plan result payload."""

    _validate_plan_inputs(action, params)

    plan_id = str(uuid4())
    preview = _build_plan_preview(action, params)
    created_at = datetime.now(timezone.utc).isoformat()
    plan = PendingActionPlan(
        plan_id=plan_id,
        action=action,
        params=params,
        reason=reason,
        preview=preview,
        created_at=created_at,
    )
    _PENDING_PLANS[plan_id] = plan

    _write_audit_log(
        {
            "event": "plan_created",
            "plan_id": plan_id,
            "action": action,
            "reason": reason,
            "preview": preview,
            "params": params,
        }
    )

    return {
        "plan_id": plan_id,
        "phase": "plan",
        "action": action,
        "preview": preview,
        "reason": reason,
        "created_at": created_at,
        "requires_confirmation": True,
        "confirmation_text": "CONFIRM",
    }


def _parse_natural_request_to_action(request_text: str) -> tuple[str, Dict[str, Any]]:
    """Very lightweight parser for human Jira assistant requests.

    Supported intents:
    - create_issue
    - comment_issue
    - transition_issue
    - assign_issue
    - update_priority
    - edit_issue
    - create_subtask
    - move_to_active_sprint
    """

    text = request_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="request_text cannot be empty.")

    normalized_lower = _normalize_for_matching(text)

    key = _extract_issue_key(text)

    write_intent_tokens = [
        "actualiza",
        "actualizar",
        "update",
        "change",
        "set",
        "mark",
        "transicionar",
        "transiciona",
        "mueve",
        "mover",
        "move",
        "pasa",
        "pasar",
        "cambia",
        "cambiar",
        "transition",
        "asigna",
        "asignar",
        "assign",
        "reasignar",
        "reassign",
        "edita",
        "editar",
        "edit",
        "coment",
        "crear",
        "create",
    ]
    has_write_intent = any(token in normalized_lower for token in write_intent_tokens)

    # read issue details
    if key and not has_write_intent and any(
        token in normalized_lower
        for token in [
            "descrip",
            "description",
            "detalle",
            "details",
            "estado",
            "status",
            "prioridad",
            "priority",
            "resumen",
            "nombre",
            "titulo",
            "title",
            "summary",
            "asignad",
            "assignee",
            "story point",
            "storypoint",
            "puntos",
            "punto",
            "estimacion",
            "estimate",
        ]
    ):
        desired_fields: list[str] = []
        if any(t in normalized_lower for t in ["descrip", "description"]):
            desired_fields.append("description")
        if any(t in normalized_lower for t in ["resumen", "summary", "nombre", "titulo", "title"]):
            desired_fields.append("summary")
        if any(t in normalized_lower for t in ["estado", "status"]):
            desired_fields.append("status")
        if any(t in normalized_lower for t in ["asignad", "assignee"]):
            desired_fields.append("assignee")
        if any(t in normalized_lower for t in ["prioridad", "priority"]):
            desired_fields.append("priority")
        if any(
            t in normalized_lower
            for t in [
                "story point",
                "storypoint",
                "puntos de historia",
                "puntos",
                "estimacion",
                "estimate",
            ]
        ):
            desired_fields.append("story_points")
        if not desired_fields:
            desired_fields = [
                "summary",
                "description",
                "status",
                "assignee",
                "priority",
                "story_points",
                "updated",
            ]
        return "get_issue_details", {"key": key, "fields": desired_fields}

    # create_subtask
    if any(token in normalized_lower for token in ["subtask", "sub-task", "sub tarea", "subtarea"]):
        parent = key
        if not parent:
            raise HTTPException(status_code=400, detail="Could not infer parent issue key for subtask.")
        summary = text
        m = re.search(r"(?:subtask|sub-task|subtarea|sub tarea)\s+(?:de|for|para)?\s*.*?:\s*(.+)$", text, flags=re.IGNORECASE)
        if m:
            summary = m.group(1).strip()
        return "create_subtask", {"parent_key": parent, "summary": summary}

    # assign_issue
    if any(token in normalized_lower for token in ["asigna", "asignar", "assign", "reassign", "reasignar"]):
        if not key:
            raise HTTPException(status_code=400, detail="Could not infer issue key for assignment.")
        # Heuristic: text after 'a ' / 'to '
        assignee = None
        m_es = re.search(r"\ba\s+([A-Za-zÁÉÍÓÚáéíóúÑñ0-9 ._\-@]+)$", text)
        m_en = re.search(r"\bto\s+([A-Za-zÁÉÍÓÚáéíóúÑñ0-9 ._\-@]+)$", text, flags=re.IGNORECASE)
        if m_es:
            assignee = m_es.group(1).strip()
        elif m_en:
            assignee = m_en.group(1).strip()
        if not assignee:
            raise HTTPException(status_code=400, detail="Could not infer assignee name.")
        return "assign_issue", {"key": key, "assignee": assignee}

    # transition_issue
    if any(
        token in normalized_lower
        for token in [
            "actualiza",
            "actualizar",
            "update",
            "estado",
            "status",
            "mueve",
            "move",
            "pasa",
            "transition",
            "transicionar",
            "transiciona",
            "change",
            "set",
            "mark",
        ]
    ):
        if key:
            for token, canonical in [
                ("in progress", "In Progress"),
                ("done", "Done"),
                ("to do", "To Do"),
                ("todo", "To Do"),
                ("review", "Review"),
                ("qa", "QA"),
                ("blocked", "Blocked"),
                ("on hold", "On Hold"),
            ]:
                if token in normalized_lower:
                    return "transition_issue", {"key": key, "target_status": canonical}

            m = re.search(r"(?:a|to|as)\s+([A-Za-z][A-Za-z ]+?)(?:\.|,|;|$)", text, flags=re.IGNORECASE)
            if m:
                target_status = m.group(1).strip()
                # Trim common trailing chatter from conversational requests.
                target_status = re.sub(
                    r"\b(and|y)\b.*$",
                    "",
                    target_status,
                    flags=re.IGNORECASE,
                ).strip()
                target_status = re.sub(
                    r"^(estado|status)\s+",
                    "",
                    target_status,
                    flags=re.IGNORECASE,
                ).strip()
                return "transition_issue", {"key": key, "target_status": target_status}

    # update_priority
    if any(token in normalized_lower for token in ["prioridad", "priority"]):
        if not key:
            raise HTTPException(status_code=400, detail="Could not infer issue key for priority update.")
        m = re.search(r"(?:prioridad|priority)\s+(?:a|to)?\s*([A-Za-z]+)", text, flags=re.IGNORECASE)
        if not m:
            raise HTTPException(status_code=400, detail="Could not infer target priority name.")
        return "update_priority", {"key": key, "priority_name": m.group(1).strip()}

    # comment_issue
    if any(token in normalized_lower for token in ["comenta", "comentario", "comment"]):
        if not key:
            raise HTTPException(status_code=400, detail="Could not infer issue key for comment.")
        m = re.search(r"(?:comentario|comment)\s*[:\-]\s*(.+)$", text, flags=re.IGNORECASE)
        comment = m.group(1).strip() if m else text
        return "comment_issue", {"key": key, "comment": comment}

    # edit_issue summary/description
    if any(token in normalized_lower for token in ["editar", "edita", "edit", "actualiza", "update summary", "description", "descrip"]):
        if not key:
            raise HTTPException(status_code=400, detail="Could not infer issue key for edit.")
        if "description" in normalized_lower or "descrip" in normalized_lower:
            m = re.search(r"(?:description|descripción)\s*[:\-]\s*(.+)$", text, flags=re.IGNORECASE)
            if m:
                return "edit_issue", {"key": key, "description": m.group(1).strip()}
        m = re.search(r"(?:summary|resumen|título|titulo)\s*[:\-]\s*(.+)$", text, flags=re.IGNORECASE)
        if m:
            return "edit_issue", {"key": key, "summary": m.group(1).strip()}

    # move_to_active_sprint
    if any(token in normalized_lower for token in ["sprint", "backlog", "mover al sprint", "move to sprint"]):
        if not key:
            raise HTTPException(status_code=400, detail="Could not infer issue key for sprint move.")
        return "move_to_active_sprint", {"key": key}

    # create_issue as fallback
    if any(token in normalized_lower for token in ["crear", "create", "nueva issue", "new issue", "nuevo ticket", "new ticket"]):
        summary = text
        m = re.search(r"(?:crear|create)\s+(?:issue|ticket|historia|tarea)?\s*[:\-]\s*(.+)$", text, flags=re.IGNORECASE)
        if m:
            summary = m.group(1).strip()
        return "create_issue", {"summary": summary, "issue_type": "Task"}

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not infer action from request_text. "
            "Try formats like: 'asigna SCC-1 a Juan', 'mueve SCC-1 a In Progress', "
            "'crear issue: texto', 'comentario SCC-1: texto', "
            "'dame la descripcion de SCC-2'."
        ),
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from plain text or fenced blocks."""

    cleaned = text.strip()
    if not cleaned:
        return None

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None

    return None


def _normalize_for_matching(text: str) -> str:
    """Lowercase, remove accents and common punctuation noise for keyword matching."""

    without_accents = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    return without_accents.lower().strip()


def _extract_issue_key(text: str) -> str | None:
    """Extract Jira issue key from free text, accepting mixed case input."""

    key_match = re.search(r"\b[A-Za-z][A-Za-z0-9]+-\d+\b", text)
    if not key_match:
        return None
    return key_match.group(0).upper()


def _parse_natural_request_with_llm(request_text: str) -> tuple[str, Dict[str, Any]] | None:
    """Fallback parser using GitHub Models when configured."""

    client = GithubModelsClient.from_settings()
    if client is None:
        return None

    system_prompt = (
        "You convert Jira assistant user requests into JSON. "
        "Return ONLY JSON object with this shape: "
        "{\"action\": \"<action>\", \"params\": { ... }}. "
        "Supported actions: create_issue, comment_issue, transition_issue, "
        "assign_issue, update_priority, edit_issue, create_subtask, "
        "move_to_active_sprint, get_issue_details."
    )

    llm_text = client.generate(
        system_prompt=system_prompt,
        history=[],
        user_prompt=f"Request: {request_text}",
    )
    if not llm_text:
        return None

    payload = _extract_json_object(llm_text)
    if not payload:
        return None

    action = payload.get("action")
    params = payload.get("params")
    if not isinstance(action, str) or not isinstance(params, dict):
        return None

    return action.strip(), params


@app.get("/health")
async def health() -> Dict[str, str]:  # pragma: no cover - trivial
    return {"status": "ok"}


@app.post("/tools/jira.get_active_sprint_issues")
async def jira_get_active_sprint_issues(payload: Dict[str, Any]) -> Dict[str, Any]:
    assignee = payload.get("assignee")
    try:
        issues = jira_client.get_active_sprint_issues(assignee=assignee)
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"items": issues}


@app.post("/tools/jira.comment_on_issue")
async def jira_comment_on_issue(payload: Dict[str, Any]) -> Dict[str, Any]:
    key = payload.get("key")
    comment = payload.get("comment")
    if not key or not comment:
        raise HTTPException(status_code=400, detail="'key' and 'comment' are required fields.")

    try:
        result = jira_client.comment_on_issue(key=key, comment=comment)
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"result": result}


@app.post("/tools/jira.test_connection")
async def jira_test_connection(_: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = jira_client.test_connection()
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"result": result}


@app.post("/tools/jira.get_issue_details")
async def jira_get_issue_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    key = payload.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="'key' is required.")

    fields = payload.get("fields")
    if fields is not None and not isinstance(fields, list):
        raise HTTPException(status_code=400, detail="'fields' must be an array when provided.")

    try:
        result = jira_client.get_issue_details(key=key, fields=fields)
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"result": result}


@app.post("/tools/jira.create_issue")
async def jira_create_issue(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = payload.get("summary")
    if not summary:
        raise HTTPException(status_code=400, detail="'summary' is required.")

    try:
        result = jira_client.create_issue(
            summary=summary,
            description=payload.get("description"),
            issue_type=payload.get("issue_type", "Task"),
            story_points=payload.get("story_points"),
        )
    except jira_client.JiraWriteDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"result": result}


@app.post("/tools/jira.seed_sample_backlog")
async def jira_seed_sample_backlog(payload: Dict[str, Any]) -> Dict[str, Any]:
    topic = payload.get("topic", "Agile training")
    count = payload.get("count", 5)
    try:
        result = jira_client.seed_sample_backlog(topic=topic, count=int(count))
    except jira_client.JiraWriteDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except jira_client.JiraConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"result": result}


def _build_plan_preview(action: str, params: Dict[str, Any]) -> str:
    if action == "create_issue":
        summary = params.get("summary", "(missing summary)")
        issue_type = params.get("issue_type", "Task")
        return f"Create Jira issue type={issue_type} summary='{summary}'"

    if action == "comment_issue":
        key = params.get("key", "(missing key)")
        return f"Comment on Jira issue {key}"

    if action == "transition_issue":
        key = params.get("key", "(missing key)")
        status = params.get("target_status", "(missing status)")
        return f"Transition Jira issue {key} to status '{status}'"

    if action == "assign_issue":
        key = params.get("key", "(missing key)")
        assignee = params.get("assignee", "(missing assignee)")
        return f"Assign Jira issue {key} to '{assignee}'"

    if action == "update_priority":
        key = params.get("key", "(missing key)")
        priority = params.get("priority_name", "(missing priority)")
        return f"Update Jira issue {key} priority to '{priority}'"

    if action == "edit_issue":
        key = params.get("key", "(missing key)")
        fields = []
        if params.get("summary") is not None:
            fields.append("summary")
        if params.get("description") is not None:
            fields.append("description")
        fields_txt = ",".join(fields) if fields else "(no fields)"
        return f"Edit Jira issue {key} fields: {fields_txt}"

    if action == "create_subtask":
        parent_key = params.get("parent_key", "(missing parent_key)")
        summary = params.get("summary", "(missing summary)")
        return f"Create subtask under {parent_key} with summary '{summary}'"

    if action == "move_to_active_sprint":
        key = params.get("key", "(missing key)")
        board_id = params.get("board_id", "(env JIRA_BOARD_ID)")
        return f"Move Jira issue {key} to active sprint on board {board_id}"

    return f"Unknown action '{action}'"


def _validate_plan_inputs(action: str, params: Dict[str, Any]) -> None:
    if action == "create_issue":
        if not params.get("summary"):
            raise HTTPException(status_code=400, detail="create_issue requires 'summary'.")
        return

    if action == "comment_issue":
        if not params.get("key") or not params.get("comment"):
            raise HTTPException(status_code=400, detail="comment_issue requires 'key' and 'comment'.")
        return

    if action == "transition_issue":
        if not params.get("key") or not params.get("target_status"):
            raise HTTPException(
                status_code=400,
                detail="transition_issue requires 'key' and 'target_status'.",
            )
        return

    if action == "assign_issue":
        if not params.get("key") or not params.get("assignee"):
            raise HTTPException(status_code=400, detail="assign_issue requires 'key' and 'assignee'.")
        return

    if action == "update_priority":
        if not params.get("key") or not params.get("priority_name"):
            raise HTTPException(
                status_code=400,
                detail="update_priority requires 'key' and 'priority_name'.",
            )
        return

    if action == "edit_issue":
        if not params.get("key"):
            raise HTTPException(status_code=400, detail="edit_issue requires 'key'.")
        if params.get("summary") is None and params.get("description") is None:
            raise HTTPException(
                status_code=400,
                detail="edit_issue requires at least one of 'summary' or 'description'.",
            )
        return

    if action == "create_subtask":
        if not params.get("parent_key") or not params.get("summary"):
            raise HTTPException(
                status_code=400,
                detail="create_subtask requires 'parent_key' and 'summary'.",
            )
        return

    if action == "move_to_active_sprint":
        if not params.get("key"):
            raise HTTPException(status_code=400, detail="move_to_active_sprint requires 'key'.")
        return

    raise HTTPException(
        status_code=400,
        detail=(
            "Unsupported action. Use one of: "
            "create_issue, comment_issue, transition_issue, assign_issue, "
            "update_priority, edit_issue, create_subtask, move_to_active_sprint"
        ),
    )


def _execute_planned_action(plan: PendingActionPlan) -> Dict[str, Any]:
    action = plan.action
    params = plan.params

    if action == "create_issue":
        return jira_client.create_issue(
            summary=params["summary"],
            description=params.get("description"),
            issue_type=params.get("issue_type", "Task"),
            story_points=params.get("story_points"),
        )

    if action == "comment_issue":
        return jira_client.comment_on_issue(
            key=params["key"],
            comment=params["comment"],
        )

    if action == "transition_issue":
        return jira_client.transition_issue_status(
            key=params["key"],
            target_status=params["target_status"],
        )

    if action == "assign_issue":
        return jira_client.assign_issue(
            key=params["key"],
            assignee=params["assignee"],
        )

    if action == "update_priority":
        return jira_client.update_issue_priority(
            key=params["key"],
            priority_name=params["priority_name"],
        )

    if action == "edit_issue":
        return jira_client.edit_issue_fields(
            key=params["key"],
            summary=params.get("summary"),
            description=params.get("description"),
        )

    if action == "create_subtask":
        return jira_client.create_subtask(
            parent_key=params["parent_key"],
            summary=params["summary"],
            description=params.get("description"),
            story_points=params.get("story_points"),
            issue_type=params.get("issue_type", "Sub-task"),
        )

    if action == "move_to_active_sprint":
        board_id = params.get("board_id")
        if board_id is not None:
            board_id = int(board_id)
        return jira_client.move_issue_to_active_sprint(
            key=params["key"],
            board_id=board_id,
        )

    raise HTTPException(status_code=400, detail=f"Unsupported planned action: {action}")


@app.post("/tools/scrum_master_assistant.plan_action")
async def scrum_master_plan_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = (payload.get("action") or "").strip()
    params = payload.get("params") or {}
    reason = payload.get("reason")

    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="'params' must be an object.")

    result = _create_plan(action=action, params=params, reason=reason)
    return {"result": result}


@app.post("/tools/scrum_master_assistant.handle_request")
async def scrum_master_handle_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    request_text = (payload.get("request_text") or "").strip()
    reason = payload.get("reason")

    try:
        action, params = _parse_natural_request_to_action(request_text)
    except HTTPException as parser_error:
        parsed = _parse_natural_request_with_llm(request_text)
        if parsed is None:
            raise parser_error
        action, params = parsed

    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="Parsed params must be an object.")

    inferred_key = _extract_issue_key(request_text)
    key_actions = {
        "get_issue_details",
        "comment_issue",
        "transition_issue",
        "assign_issue",
        "update_priority",
        "edit_issue",
        "move_to_active_sprint",
    }
    if action in key_actions and not params.get("key") and inferred_key:
        params["key"] = inferred_key

    if action == "get_issue_details":
        key = params.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="get_issue_details requires 'key'.")

        details = jira_client.get_issue_details(key=key, fields=params.get("fields"))

        _write_audit_log(
            {
                "event": "request_read",
                "request_text": request_text,
                "parsed_action": action,
                "parsed_params": params,
                "execution_result": details,
            }
        )

        return {
            "result": {
                "phase": "read",
                "action": action,
                "request_text": request_text,
                "requires_confirmation": False,
                "execution_result": details,
            }
        }

    result = _create_plan(action=action, params=params, reason=reason or "natural-language request")

    _write_audit_log(
        {
            "event": "request_parsed",
            "request_text": request_text,
            "parsed_action": action,
            "parsed_params": params,
            "plan_id": result.get("plan_id"),
        }
    )

    return {
        "result": {
            **result,
            "request_text": request_text,
        }
    }


@app.post("/tools/scrum_master_assistant.apply_action")
async def scrum_master_apply_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    plan_id = payload.get("plan_id")
    confirm = payload.get("confirm")
    confirmation_text = (payload.get("confirmation_text") or "").strip()

    if not plan_id:
        raise HTTPException(status_code=400, detail="'plan_id' is required.")

    if confirm is not True:
        raise HTTPException(status_code=400, detail="'confirm' must be true to apply a plan.")

    if confirmation_text != "CONFIRM":
        raise HTTPException(status_code=400, detail="'confirmation_text' must equal 'CONFIRM'.")

    plan = _PENDING_PLANS.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found or already applied.")

    _write_audit_log(
        {
            "event": "apply_requested",
            "plan_id": plan_id,
            "action": plan.action,
            "preview": plan.preview,
            "confirm": bool(confirm),
            "confirmation_text": confirmation_text,
        }
    )

    try:
        result = _execute_planned_action(plan)
    except jira_client.JiraWriteDisabledError as exc:
        _write_audit_log(
            {
                "event": "apply_failed",
                "plan_id": plan_id,
                "action": plan.action,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except jira_client.JiraConfigError as exc:
        _write_audit_log(
            {
                "event": "apply_failed",
                "plan_id": plan_id,
                "action": plan.action,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        _write_audit_log(
            {
                "event": "apply_failed",
                "plan_id": plan_id,
                "action": plan.action,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected apply error ({type(exc).__name__}): {exc}",
        ) from exc

    # One-time execution: remove plan after apply.
    _PENDING_PLANS.pop(plan_id, None)

    _write_audit_log(
        {
            "event": "apply_succeeded",
            "plan_id": plan_id,
            "action": plan.action,
            "preview": plan.preview,
            "execution_result": result,
        }
    )

    return {
        "result": {
            "phase": "apply",
            "plan_id": plan_id,
            "action": plan.action,
            "preview": plan.preview,
            "execution_result": result,
        }
    }


# Convenience for `uvicorn jira_mcp_server.server:app --reload`
__all__ = ["app"]
