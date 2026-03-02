"""Minimal Jira client for the Jira MCP-style server.

Uses Jira Cloud REST API with email + API token authentication.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests


class JiraConfigError(RuntimeError):
    pass


class JiraWriteDisabledError(RuntimeError):
    pass


def _get_base_config() -> tuple[str, str, str]:
    base_url = os.getenv("JIRA_BASE_URL")
    email = os.getenv("JIRA_EMAIL")
    api_token = os.getenv("JIRA_API_TOKEN")

    if not base_url or not email or not api_token:
        raise JiraConfigError(
            "JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN must be set in the environment to use Jira."
        )

    return base_url.rstrip("/"), email, api_token


def _auth_headers(email: str, api_token: str) -> Dict[str, str]:
    from base64 import b64encode

    token = b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _writes_enabled() -> bool:
    value = os.getenv("JIRA_ALLOW_WRITES", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _ensure_writes_enabled() -> None:
    if not _writes_enabled():
        raise JiraWriteDisabledError(
            "Jira writes are disabled. Set JIRA_ALLOW_WRITES=true to enable create/update actions."
        )


def _to_adf_text_document(text: str) -> Dict[str, Any]:
    """Convert plain text into Jira ADF document format."""

    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _raise_for_status_with_detail(resp: requests.Response, *, operation: str) -> None:
    """Raise JiraConfigError with response details when HTTP status is not successful."""

    try:
        resp.raise_for_status()
        return
    except requests.HTTPError as exc:
        detail = ""
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                detail = str(payload.get("errorMessages") or payload.get("errors") or payload)
            else:
                detail = str(payload)
        except Exception:
            detail = resp.text

        raise JiraConfigError(
            f"{operation} failed with status {resp.status_code}: {detail}"
        ) from exc


def test_connection() -> Dict[str, Any]:
    """Validate Jira auth and project access."""

    base_url, email, api_token = _get_base_config()
    project_key = os.getenv("JIRA_PROJECT_KEY")
    if not project_key:
        raise JiraConfigError("JIRA_PROJECT_KEY must be set in the environment.")

    headers = _auth_headers(email, api_token)

    myself_resp = requests.get(f"{base_url}/rest/api/3/myself", headers=headers, timeout=15)
    myself_resp.raise_for_status()
    myself = myself_resp.json()

    project_resp = requests.get(
        f"{base_url}/rest/api/3/project/{project_key}",
        headers=headers,
        timeout=15,
    )
    project_resp.raise_for_status()
    project = project_resp.json()

    return {
        "ok": True,
        "user": {
            "accountId": myself.get("accountId"),
            "displayName": myself.get("displayName"),
            "emailAddress": myself.get("emailAddress"),
        },
        "project": {
            "id": project.get("id"),
            "key": project.get("key"),
            "name": project.get("name"),
        },
    }


def get_issue_details(*, key: str, fields: list[str] | None = None) -> Dict[str, Any]:
    """Fetch selected fields from a Jira issue (read-only helper)."""

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)

    requested_fields = fields or [
        "summary",
        "description",
        "status",
        "assignee",
        "priority",
        "story_points",
        "updated",
    ]

    # Keep order and remove duplicates while preserving names expected by callers.
    requested_fields = list(dict.fromkeys(requested_fields))

    api_field_map = {
        "summary": "summary",
        "description": "description",
        "status": "status",
        "assignee": "assignee",
        "priority": "priority",
        "updated": "updated",
        "story_points": "customfield_10016",
    }
    jira_fields = [api_field_map[name] for name in requested_fields if name in api_field_map]
    if not jira_fields:
        jira_fields = [
            "summary",
            "description",
            "status",
            "assignee",
            "priority",
            "customfield_10016",
            "updated",
        ]

    resp = requests.get(
        f"{base_url}/rest/api/3/issue/{key}",
        headers=headers,
        params={"fields": ",".join(jira_fields)},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    f = data.get("fields") or {}

    assignee_obj = f.get("assignee") or {}
    priority_obj = f.get("priority") or {}
    status_obj = f.get("status") or {}

    description_text = None
    description = f.get("description")
    if isinstance(description, dict):
        # Minimal ADF text flattening
        chunks: list[str] = []
        for block in description.get("content", []) or []:
            for item in block.get("content", []) or []:
                txt = item.get("text")
                if isinstance(txt, str) and txt.strip():
                    chunks.append(txt.strip())
        if chunks:
            description_text = " ".join(chunks)

    result: Dict[str, Any] = {"key": data.get("key")}
    for field_name in requested_fields:
        if field_name == "summary":
            result["summary"] = f.get("summary")
        elif field_name == "description":
            result["description"] = description_text
        elif field_name == "status":
            result["status"] = status_obj.get("name")
        elif field_name == "assignee":
            result["assignee"] = assignee_obj.get("displayName")
        elif field_name == "priority":
            result["priority"] = priority_obj.get("name")
        elif field_name == "updated":
            result["updated"] = f.get("updated")
        elif field_name == "story_points":
            result["story_points"] = f.get("customfield_10016")

    return result


def get_active_sprint_issues(assignee: str | None = None) -> List[Dict[str, Any]]:
    """Return issues for the current project that are in progress.

    This is a simplified approximation: it does not truly inspect sprints,
    but it is good enough to validate the end-to-end flow for now.
    """

    base_url, email, api_token = _get_base_config()
    project_key = os.getenv("JIRA_PROJECT_KEY")
    if not project_key:
        raise JiraConfigError("JIRA_PROJECT_KEY must be set in the environment.")

    jql_parts = [f"project = {project_key}", "statusCategory != Done"]
    if assignee:
        jql_parts.append(f"assignee = \"{assignee}\"")

    jql = " AND ".join(jql_parts)

    # Official JQL search endpoint; we request only the fields we care about
    # to keep payloads small but still informative for the agents.
    url = f"{base_url}/rest/api/3/search/jql"
    params = {
        "jql": jql,
        "fields": (
            "summary,description,issuetype,status,assignee,reporter,"
            "priority,updated,subtasks,customfield_10020,customfield_10016"
        ),
    }

    headers = _auth_headers(email, api_token)
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    
    issues: List[Dict[str, Any]] = []
    for issue in data.get("issues", []):
        fields = issue.get("fields", {})
        assignee_obj = fields.get("assignee") or {}
        priority_obj = fields.get("priority") or {}

        # Story points are commonly stored in customfield_10016 in Jira Cloud
        story_points = fields.get("customfield_10016")

        # Sprint information is commonly in customfield_10020 as a list
        sprint_name = None
        sprint_state = None
        sprints = fields.get("customfield_10020") or []
        if isinstance(sprints, list) and sprints:
            sprint = sprints[0]
            sprint_name = sprint.get("name")
            sprint_state = sprint.get("state")

        issues.append(
            {
                "key": issue.get("key"),
                "summary": fields.get("summary"),
                "status": (fields.get("status") or {}).get("name"),
                "assignee": assignee_obj.get("displayName"),
                "story_points": story_points,
                "sprint_name": sprint_name,
                "sprint_state": sprint_state,
                "priority": priority_obj.get("name"),
            }
        )

    return issues


def comment_on_issue(key: str, comment: str) -> Dict[str, Any]:
    """Post a comment on a Jira issue."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    url = f"{base_url}/rest/api/3/issue/{key}/comment"

    headers = _auth_headers(email, api_token)

    payload = {"body": _to_adf_text_document(comment)}

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    _raise_for_status_with_detail(resp, operation=f"comment_on_issue({key})")
    return resp.json()


def create_issue(
    *,
    summary: str,
    description: str | None = None,
    issue_type: str = "Task",
    story_points: float | None = None,
) -> Dict[str, Any]:
    """Create a Jira issue in the configured project."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    project_key = os.getenv("JIRA_PROJECT_KEY")
    if not project_key:
        raise JiraConfigError("JIRA_PROJECT_KEY must be set in the environment.")

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }

    if description:
        fields["description"] = _to_adf_text_document(description)

    if story_points is not None:
        fields["customfield_10016"] = story_points

    payload = {"fields": fields}

    headers = _auth_headers(email, api_token)
    resp = requests.post(
        f"{base_url}/rest/api/3/issue",
        headers=headers,
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "id": data.get("id"),
        "key": data.get("key"),
        "self": data.get("self"),
    }


def seed_sample_backlog(*, topic: str = "Agile training", count: int = 5) -> Dict[str, Any]:
    """Create a small sample backlog for training/demo sessions."""

    _ensure_writes_enabled()

    count = max(1, min(count, 15))
    templates = [
        ("Definir visión y objetivo del sprint", 3.0),
        ("Configurar tablero y columnas de trabajo", 2.0),
        ("Crear historia para flujo principal de usuario", 5.0),
        ("Agregar criterios de aceptación y Definition of Done", 3.0),
        ("Preparar métricas básicas de avance", 2.0),
        ("Diseñar plan de mitigación de bloqueos", 3.0),
        ("Documentar dependencias técnicas", 2.0),
    ]

    created: List[Dict[str, Any]] = []
    for idx in range(count):
        title, points = templates[idx % len(templates)]
        issue = create_issue(
            summary=f"[{topic}] {title}",
            description=(
                f"Historia de ejemplo para practicar ceremonias Scrum en el tema '{topic}'. "
                "Incluye update diario, riesgos y dependencias."
            ),
            issue_type="Task",
            story_points=points,
        )
        created.append(issue)

    return {
        "created_count": len(created),
        "issues": created,
        "topic": topic,
    }


def transition_issue_status(*, key: str, target_status: str) -> Dict[str, Any]:
    """Transition an issue to a target Jira status by name."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)

    transitions_resp = requests.get(
        f"{base_url}/rest/api/3/issue/{key}/transitions",
        headers=headers,
        timeout=20,
    )
    transitions_resp.raise_for_status()
    transitions_data = transitions_resp.json()
    transitions = transitions_data.get("transitions", [])

    target = None
    target_status_lower = target_status.strip().lower()
    for item in transitions:
        to_obj = item.get("to") or {}
        name = (to_obj.get("name") or "").strip().lower()
        if name == target_status_lower:
            target = item
            break

    if target is None:
        available = [((t.get("to") or {}).get("name") or "") for t in transitions]
        raise JiraConfigError(
            f"Status '{target_status}' is not an available transition for issue {key}. "
            f"Available: {available}"
        )

    payload = {"transition": {"id": target.get("id")}}
    apply_resp = requests.post(
        f"{base_url}/rest/api/3/issue/{key}/transitions",
        headers=headers,
        json=payload,
        timeout=20,
    )
    apply_resp.raise_for_status()

    return {
        "key": key,
        "target_status": target_status,
        "transition_id": target.get("id"),
    }


def _find_assignable_user_account_id(*, display_name_or_email: str) -> str:
    """Resolve an assignable user's accountId by display name or email query."""

    base_url, email, api_token = _get_base_config()
    project_key = os.getenv("JIRA_PROJECT_KEY")
    if not project_key:
        raise JiraConfigError("JIRA_PROJECT_KEY must be set in the environment.")

    headers = _auth_headers(email, api_token)
    resp = requests.get(
        f"{base_url}/rest/api/3/user/assignable/search",
        headers=headers,
        params={"project": project_key, "query": display_name_or_email},
        timeout=20,
    )
    resp.raise_for_status()
    users = resp.json() or []

    if not users:
        raise JiraConfigError(
            f"No assignable Jira user found for query '{display_name_or_email}'."
        )

    # Prefer exact displayName/email match when possible.
    q = display_name_or_email.strip().lower()
    for user in users:
        display_name = (user.get("displayName") or "").strip().lower()
        email_address = (user.get("emailAddress") or "").strip().lower()
        if q and (display_name == q or email_address == q):
            account_id = user.get("accountId")
            if account_id:
                return account_id

    account_id = users[0].get("accountId")
    if not account_id:
        raise JiraConfigError("Resolved Jira user does not include accountId.")
    return account_id


def assign_issue(*, key: str, assignee: str) -> Dict[str, Any]:
    """Assign or reassign an issue to a Jira user."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    account_id = _find_assignable_user_account_id(display_name_or_email=assignee)
    headers = _auth_headers(email, api_token)

    resp = requests.put(
        f"{base_url}/rest/api/3/issue/{key}/assignee",
        headers=headers,
        json={"accountId": account_id},
        timeout=20,
    )
    resp.raise_for_status()

    return {
        "key": key,
        "assignee": assignee,
        "assignee_account_id": account_id,
    }


def _resolve_priority_id(*, priority_name: str) -> str:
    """Get Jira priority id by priority name."""

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)

    resp = requests.get(f"{base_url}/rest/api/3/priority", headers=headers, timeout=20)
    resp.raise_for_status()
    priorities = resp.json() or []

    target = priority_name.strip().lower()
    for p in priorities:
        name = (p.get("name") or "").strip().lower()
        if name == target:
            pid = p.get("id")
            if pid:
                return pid

    available = [p.get("name") for p in priorities]
    raise JiraConfigError(
        f"Priority '{priority_name}' not found. Available priorities: {available}"
    )


def update_issue_priority(*, key: str, priority_name: str) -> Dict[str, Any]:
    """Update Jira issue priority by name."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)
    priority_id = _resolve_priority_id(priority_name=priority_name)

    resp = requests.put(
        f"{base_url}/rest/api/3/issue/{key}",
        headers=headers,
        json={"fields": {"priority": {"id": priority_id}}},
        timeout=20,
    )
    resp.raise_for_status()

    return {
        "key": key,
        "priority_name": priority_name,
        "priority_id": priority_id,
    }


def edit_issue_fields(
    *,
    key: str,
    summary: str | None = None,
    description: str | None = None,
) -> Dict[str, Any]:
    """Edit summary/description of an existing Jira issue."""

    _ensure_writes_enabled()

    if summary is None and description is None:
        raise JiraConfigError("At least one of 'summary' or 'description' must be provided.")

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)

    fields: Dict[str, Any] = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = _to_adf_text_document(description)

    resp = requests.put(
        f"{base_url}/rest/api/3/issue/{key}",
        headers=headers,
        json={"fields": fields},
        timeout=20,
    )
    resp.raise_for_status()

    return {
        "key": key,
        "updated_fields": list(fields.keys()),
    }


def create_subtask(
    *,
    parent_key: str,
    summary: str,
    description: str | None = None,
    story_points: float | None = None,
    issue_type: str = "Sub-task",
) -> Dict[str, Any]:
    """Create a Jira sub-task under an existing parent issue."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    project_key = os.getenv("JIRA_PROJECT_KEY")
    if not project_key:
        raise JiraConfigError("JIRA_PROJECT_KEY must be set in the environment.")

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "parent": {"key": parent_key},
    }
    if description:
        fields["description"] = _to_adf_text_document(description)
    if story_points is not None:
        fields["customfield_10016"] = story_points

    headers = _auth_headers(email, api_token)
    resp = requests.post(
        f"{base_url}/rest/api/3/issue",
        headers=headers,
        json={"fields": fields},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "id": data.get("id"),
        "key": data.get("key"),
        "self": data.get("self"),
        "parent_key": parent_key,
    }


def move_issue_to_active_sprint(*, key: str, board_id: int | None = None) -> Dict[str, Any]:
    """Move issue to currently active sprint on a board."""

    _ensure_writes_enabled()

    base_url, email, api_token = _get_base_config()
    headers = _auth_headers(email, api_token)

    if board_id is None:
        board_id_env = os.getenv("JIRA_BOARD_ID")
        if not board_id_env:
            raise JiraConfigError(
                "board_id not provided and JIRA_BOARD_ID is not set. Cannot resolve active sprint."
            )
        try:
            board_id = int(board_id_env)
        except ValueError as exc:
            raise JiraConfigError("JIRA_BOARD_ID must be an integer.") from exc

    sprint_resp = requests.get(
        f"{base_url}/rest/agile/1.0/board/{board_id}/sprint",
        headers=headers,
        params={"state": "active", "maxResults": 1},
        timeout=20,
    )
    sprint_resp.raise_for_status()
    sprint_data = sprint_resp.json() or {}
    sprints = sprint_data.get("values") or []
    if not sprints:
        raise JiraConfigError(f"No active sprint found for board {board_id}.")

    sprint = sprints[0]
    sprint_id = sprint.get("id")
    sprint_name = sprint.get("name")
    if sprint_id is None:
        raise JiraConfigError("Active sprint does not include id.")

    move_resp = requests.post(
        f"{base_url}/rest/agile/1.0/sprint/{sprint_id}/issue",
        headers=headers,
        json={"issues": [key]},
        timeout=20,
    )
    move_resp.raise_for_status()

    return {
        "key": key,
        "board_id": board_id,
        "sprint_id": sprint_id,
        "sprint_name": sprint_name,
    }
