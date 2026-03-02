"""Native MCP stdio bridge for VS Code Copilot Chat.

This server exposes a subset of AgileAgents Jira tools via MCP and forwards
calls to the existing HTTP Jira assistant backend (`jira_mcp_server.server`).

Why this file exists:
- VS Code Copilot Chat expects a real MCP server (stdio/http transport).
- `jira_mcp_server.server` currently exposes MCP-style HTTP routes but not
  the MCP protocol directly.
- This bridge adapts MCP tool invocations to those HTTP routes.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from uuid import uuid4

import requests
from fastmcp import FastMCP

from src.agents.product_owner_agent import ProductOwnerAgent
from src.agents.scrum_master_agent import ScrumMasterAgent
from src.agents.team_member_agent import TeamMemberAgent
from src.config.mcp_client_config import McpConnectionConfig
from src.conversations.messages import ConversationMessage, UserContext
from src.mcp.client import McpClient
from src.mcp.tools_jira import JiraIssue
from src.orchestration.context_builder import build_standup_context
from src.orchestration.session_manager import ScrumSession
from src.orchestration.turn_taking import run_daily_standup


mcp = FastMCP("agileAgentsJira")

_PENDING_DAILY_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _backend_base_url() -> str:
    return os.getenv("AGILEAGENTS_MCP_HTTP_BASE", "http://127.0.0.1:8000").rstrip("/")


def _call_backend(tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{_backend_base_url()}/tools/{tool_id}"
    response = requests.post(url, json=payload, timeout=60)
    if response.status_code >= 400:
        detail: str
        try:
            parsed = response.json()
            if isinstance(parsed, dict) and parsed.get("detail"):
                detail = str(parsed.get("detail"))
            else:
                detail = str(parsed)
        except Exception:
            detail = response.text
        raise RuntimeError(f"Backend call failed ({response.status_code}) for {tool_id}: {detail}")

    parsed = response.json()
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _message_speaker(message: ConversationMessage) -> str:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    agent = metadata.get("agent")
    name = metadata.get("name")
    if agent == "team_member" and name:
        return f"team_member:{name}"
    if isinstance(agent, str) and agent:
        return agent
    return message.role


def _looks_like_daily_request(text: str) -> bool:
    lowered = text.lower()
    tokens = [
        "daily",
        "standup",
        "stand-up",
        "scrum diario",
        "ejecuta la daily",
        "run daily",
    ]
    return any(token in lowered for token in tokens)


def _contains_risk_or_dependency(text: str) -> bool:
    lowered = text.lower()
    no_risk_tokens = [
        "sin bloqueos",
        "sin bloqueo",
        "no tengo bloqueos",
        "no tengo bloqueo",
        "no hay bloqueos",
        "no hay bloqueo",
        "sobre bloqueos, no",
        "sin dependencias",
        "no tengo dependencias",
        "no dependency",
        "no dependencies",
        "no blockers",
        "without blockers",
    ]
    if any(token in lowered for token in no_risk_tokens):
        return False

    tokens = [
        "bloqueo",
        "bloqueado",
        "dependencia",
        "riesgo",
        "blocked",
        "dependency",
        "risk",
        "permiso",
        "permissions",
    ]
    return any(token in lowered for token in tokens)


def _extract_done_candidate_key(text: str) -> str | None:
    lowered = text.lower()
    if "done" not in lowered:
        return None

    if not any(token in lowered for token in ["falta", "falto", "pendiente", "resta", "solo falt", "left", "remaining"]):
        return None

    keys = _extract_issue_keys(text)
    if not keys:
        return None
    return keys[0]


def _extract_issue_keys(text: str) -> List[str]:
    keys = re.findall(r"\b[A-Za-z][A-Za-z0-9]+-\d+\b", text)
    return sorted({key.upper() for key in keys})


def _contains_close_intent(text: str) -> bool:
    lowered = text.lower().strip()
    close_tokens = {
        "no",
        "no gracias",
        "ninguna",
        "nada más",
        "nada mas",
        "cerrar",
        "finalizar",
        "listo",
        "done",
        "no thanks",
        "close",
        "finish",
    }
    return lowered in close_tokens


def _contains_apply_intent(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["aplica", "aplicar", "apply", "confirm", "confirma"])


def _daily_banner_markdown(language: str) -> str:
    title = "SCRUM DAILY BRIDGE" if language == "en" else "PUENTE DAILY SCRUM"
    subtitle = (
        "Team sync + blockers + Jira actions"
        if language == "en"
        else "Sincronía de equipo + bloqueos + acciones Jira"
    )
    return (
        "```text\n"
        "  ____   ____  ____  _   _ __  __   ____    _    ___ _  __   __\n"
        " / ___| / ___||  _ \\| | | |  \\/  | |  _ \\  / \\  |_ _| |/ /   \\ \\n"
        " \\___ \\| |    | |_) | | | | |\\/| | | | | |/ _ \\  | || ' /_____\\ \\\n"
        "  ___) | |___ |  _ <| |_| | |  | | | |_| / ___ \\ | || . \\_____/ /\n"
        " |____/ \\____||_| \\_\\\\___/|_|  |_| |____/_/   \\_\\___|_|\\_\\   /_/\n"
        "\n"
        f" {title}\n"
        f" {subtitle}\n"
        "```"
    )


def _format_member_issues_markdown(*, member: str, issues: List[Dict[str, Any]], language: str) -> str:
    if not issues:
        if language == "en":
            return f"### {member} - Active Jira items\nNo active issues found right now."
        return f"### {member} - Tareas activas en Jira\nNo encontré tareas activas por ahora."

    if language == "en":
        lines = [f"### {member} - Active Jira items"]
    else:
        lines = [f"### {member} - Tareas activas en Jira"]

    for issue in issues:
        key = issue.get("key") or "(no-key)"
        summary = issue.get("summary") or "(sin resumen)"
        status = issue.get("status") or "Unknown"
        priority = issue.get("priority") or "-"
        points = issue.get("story_points")
        points_txt = f"{points}" if points is not None else "-"
        lines.append(
            f"- **{key}**: {summary}  \n"
            f"  - Estado: `{status}` | Prioridad: `{priority}` | Story points: `{points_txt}`"
        )

    return "\n".join(lines)


def _format_update_request(language: str, main_member: str) -> str:
    if language == "en":
        return (
            f"{main_member}, please share your update using this format:\n"
            "- Yesterday: what you completed\n"
            "- Today: what you will do\n"
            "- Blockers/Dependencies: what might slow you down"
        )
    return (
        f"{main_member}, por favor comparte tu update usando este formato:\n"
        "- Ayer: qué completaste\n"
        "- Hoy: qué harás\n"
        "- Bloqueos/Dependencias: qué puede frenarte"
    )


def _format_optional_request_prompt(language: str) -> str:
    if language == "en":
        return (
            "If you have any question or request, tell me now. "
            "I can help with Scrum concepts, dependencies, Jira reads and Jira changes. "
            "If no more actions are needed, reply with 'done'."
        )
    return (
        "Si tienes alguna pregunta o petición, dímela ahora. "
        "Puedo ayudarte con conceptos Scrum, dependencias, lecturas de Jira y cambios en Jira. "
        "Si no necesitas más acciones, responde 'listo'."
    )


def _unwrap_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(payload, dict):
        return payload
    return {"value": payload}


def _format_read_execution_result(action: str | None, execution_result: Any, language: str) -> str:
    if not isinstance(execution_result, dict):
        return str(execution_result)

    if action == "get_issue_details":
        key = execution_result.get("key", "(no-key)")
        lines = [f"### Issue {key}"]
        labels = {
            "summary": "Resumen" if language == "es" else "Summary",
            "description": "Descripción" if language == "es" else "Description",
            "status": "Estado" if language == "es" else "Status",
            "assignee": "Asignado" if language == "es" else "Assignee",
            "priority": "Prioridad" if language == "es" else "Priority",
            "story_points": "Story points",
            "updated": "Actualizado" if language == "es" else "Updated",
        }
        order = ["summary", "description", "status", "assignee", "priority", "story_points", "updated"]
        for field in order:
            if field in execution_result:
                value = execution_result.get(field)
                if value is None:
                    value = "sin valor" if language == "es" else "not set"
                lines.append(f"- **{labels[field]}:** {value}")
        return "\n".join(lines)

    lines = ["### Resultado de lectura" if language == "es" else "### Read result"]
    for key, value in execution_result.items():
        lines.append(f"- **{key}:** {value}")
    return "\n".join(lines)


def _main_member_issues(main_member: str) -> List[Dict[str, Any]]:
    payload = _call_backend("jira.get_active_sprint_issues", {"assignee": main_member})
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _daily_team_summary_markdown(*, transcript: List[Dict[str, str]], language: str) -> str:
    if language == "en":
        lines = ["### Team daily summary"]
    else:
        lines = ["### Resumen de daily del equipo"]

    member_lines = [row for row in transcript if str(row.get("speaker", "")).startswith("team_member:")]
    if not member_lines:
        lines.append("- No team member updates were generated." if language == "en" else "- No se generaron updates del equipo.")
        return "\n".join(lines)

    for row in member_lines:
        speaker = str(row.get("speaker", "team_member"))
        name = speaker.split(":", 1)[1] if ":" in speaker else speaker
        content = str(row.get("content", "")).strip()
        lines.append(f"- **{name}:** {content}")

    return "\n".join(lines)


def _build_daily_run_assistant_markdown(
    *,
    language: str,
    daily_banner: str,
    transcript: List[Dict[str, str]],
    follow_up: Dict[str, Any] | None,
    main_member_block: Dict[str, str] | None,
) -> str:
    blocks: List[str] = [daily_banner, _daily_team_summary_markdown(transcript=transcript, language=language)]

    if follow_up and follow_up.get("tasks_markdown"):
        blocks.append(str(follow_up.get("tasks_markdown")))
        blocks.append(f"### {'Siguiente paso' if language == 'es' else 'Next step'}\n{follow_up.get('question')}")
    elif follow_up and follow_up.get("question"):
        blocks.append(f"### {'Siguiente paso' if language == 'es' else 'Next step'}\n{follow_up.get('question')}")

    if main_member_block:
        if language == "es":
            blocks.append(
                "### Feedback usuario principal\n"
                f"- **Usuario:** {main_member_block.get('member')}\n"
                f"- **Update:** {main_member_block.get('update')}\n"
                f"- **Scrum Master:** {main_member_block.get('scrum_master_reply')}"
            )
        else:
            blocks.append(
                "### Main member feedback\n"
                f"- **Member:** {main_member_block.get('member')}\n"
                f"- **Update:** {main_member_block.get('update')}\n"
                f"- **Scrum Master:** {main_member_block.get('scrum_master_reply')}"
            )

    return "\n\n".join(blocks)


def _build_followup_markdown(*, language: str, title: str, body: str, extra: str | None = None) -> str:
    heading = f"### {title}"
    parts = [heading, body]
    if extra:
        parts.append(extra)
    if language == "es":
        parts.append("_Si no hay más acciones, responde `listo`._")
    else:
        parts.append("_If there are no more actions, reply with `done`._")
    return "\n\n".join(parts)


def _looks_generic_member_update(content: str) -> bool:
    lowered = content.lower()
    return (
        "sin historias especificas" in lowered
        or "sin historias específicas" in lowered
        or "without specific jira issues" in lowered
        or "sprint tasks without specific jira" in lowered
    )


def _build_member_fallback_update_text(
    *,
    language: str,
    member_name: str,
    issues: List[Dict[str, Any]],
) -> str:
    selected = issues[:2]
    snippets: List[str] = []
    for issue in selected:
        key = issue.get("key") or "(no-key)"
        summary = issue.get("summary") or "(sin resumen)"
        points = issue.get("story_points")
        if points is not None:
            snippets.append(f"{key} ({summary}) [{points} puntos]")
        else:
            snippets.append(f"{key} ({summary})")

    joined = "; ".join(snippets) if snippets else "(sin historias)"

    if language == "en":
        return (
            f"Yesterday I progressed on sprint-priority Jira work for the team: {joined}. "
            "Today I will continue moving these stories toward Done. "
            "I have no major blockers right now."
        )

    return (
        f"Ayer avancé en historias prioritarias del sprint para el equipo: {joined}. "
        "Hoy seguiré empujando estas historias hacia Done. "
        "No tengo bloqueos importantes por ahora."
    )


def _response_with_markdown(result_payload: Dict[str, Any], markdown: str) -> Dict[str, Any]:
    """Return markdown in both top-level and nested payload for better Copilot rendering."""

    nested = dict(result_payload)
    nested["assistant_message_markdown"] = markdown
    nested["assistant_message"] = markdown
    return {
        "assistant_message_markdown": markdown,
        "content_markdown": markdown,
        "assistant_message": markdown,
        "result": nested,
    }


def _extract_markdown(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("assistant_message_markdown"), str):
        return str(payload.get("assistant_message_markdown"))

    result = payload.get("result")
    if isinstance(result, dict):
        value = result.get("assistant_message_markdown")
        if isinstance(value, str):
            return value
    return ""


@mcp.tool(description="Get Jira issue details by key. Supports fields like summary, description, status, assignee, priority, story_points, updated.")
def jira_get_issue_details(key: str, fields: List[str] | None = None) -> Dict[str, Any]:
    return _call_backend("jira.get_issue_details", {"key": key, "fields": fields})


@mcp.tool(description="Get active sprint issues, optionally filtered by assignee display name.")
def jira_get_active_sprint_issues(assignee: str | None = None) -> Dict[str, Any]:
    return _call_backend("jira.get_active_sprint_issues", {"assignee": assignee})


@mcp.tool(description="Test Jira connectivity and configured project access.")
def jira_test_connection() -> Dict[str, Any]:
    return _call_backend("jira.test_connection", {})


@mcp.tool(description="Handle a Scrum Master request in natural language. Read requests are executed immediately; write requests are returned as plan+preview.")
def scrum_master_handle_request(request_text: str, reason: str | None = None) -> Dict[str, Any]:
    if _looks_like_daily_request(request_text):
        return {
            "result": {
                "phase": "routing",
                "message": (
                    "This looks like a daily/standup request. "
                    "Use tool 'daily_present' instead of scrum_master_handle_request."
                ),
                "recommended_tool": "daily_present",
                "requires_confirmation": False,
            }
        }

    return _call_backend(
        "scrum_master_assistant.handle_request",
        {"request_text": request_text, "reason": reason},
    )


@mcp.tool(description="Plan a Jira write action without applying it. Returns plan_id and preview.")
def scrum_master_plan_action(action: str, params: Dict[str, Any], reason: str | None = None) -> Dict[str, Any]:
    return _call_backend(
        "scrum_master_assistant.plan_action",
        {"action": action, "params": params, "reason": reason},
    )


@mcp.tool(description="Apply a previously planned Jira action. Requires confirm=true and confirmation_text='CONFIRM'.")
def scrum_master_apply_action(
    plan_id: str,
    confirm: bool = True,
    confirmation_text: str = "CONFIRM",
) -> Dict[str, Any]:
    return _call_backend(
        "scrum_master_assistant.apply_action",
        {
            "plan_id": plan_id,
            "confirm": confirm,
            "confirmation_text": confirmation_text,
        },
    )


@mcp.tool(description="Create a Jira issue in the configured project. Requires writes enabled on backend server.")
def jira_create_issue(
    summary: str,
    description: str | None = None,
    issue_type: str = "Task",
    story_points: float | None = None,
) -> Dict[str, Any]:
    return _call_backend(
        "jira.create_issue",
        {
            "summary": summary,
            "description": description,
            "issue_type": issue_type,
            "story_points": story_points,
        },
    )


@mcp.tool(description="Seed sample backlog issues in Jira. Requires writes enabled on backend server.")
def jira_seed_sample_backlog(topic: str = "Agile training", count: int = 5) -> Dict[str, Any]:
    return _call_backend("jira.seed_sample_backlog", {"topic": topic, "count": count})


@mcp.tool(
    description=(
        "Run the Scrum daily simulation and return a structured transcript. "
        "Use this from Copilot Chat to execute dailies without CLI scripts. "
        "If main_member_update is not provided, returns a session follow-up asking the main member for update. "
        "IMPORTANT: render `assistant_message_markdown` directly to the user when present."
    )
)
def daily_run(
    language: str = "es",
    members: List[str] | None = None,
    main_member: str | None = None,
    main_member_update: str | None = None,
    ask_main_member_followup: bool = True,
) -> Dict[str, Any]:
    if language not in {"es", "en"}:
        raise RuntimeError("language must be 'es' or 'en'.")

    member_names = [name.strip() for name in (members or ["Alice", "Bob"]) if name and name.strip()]
    if not member_names:
        raise RuntimeError("At least one team member is required.")

    user = UserContext(name="Scrum Master", language=language)
    scrum_master = ScrumMasterAgent(language=language)
    product_owner = ProductOwnerAgent(language=language)
    team_members = [TeamMemberAgent(name=name, language=language) for name in member_names]

    session = ScrumSession(
        user=user,
        scrum_master=scrum_master,
        product_owner=product_owner,
        team_members=team_members,
    )

    mcp_client = McpClient(
        config=McpConnectionConfig(endpoint=_backend_base_url(), env=os.getenv("MCP_ENV", "dev"))
    )

    main_member_issues_cache: List[Dict[str, Any]] = []
    fallback_jira_items = None
    if main_member:
        try:
            main_member_issues_cache = _main_member_issues(main_member)
        except Exception:
            main_member_issues_cache = []

    if main_member_issues_cache:
        fallback_jira_items = [
            JiraIssue(
                key=str(item.get("key", "")),
                summary=str(item.get("summary", "")),
                status=str(item.get("status", "Unknown")),
                assignee=(str(item.get("assignee")) if item.get("assignee") is not None else None),
                story_points=item.get("story_points"),
                sprint_name=(str(item.get("sprint_name")) if item.get("sprint_name") is not None else None),
                sprint_state=(str(item.get("sprint_state")) if item.get("sprint_state") is not None else None),
                priority=(str(item.get("priority")) if item.get("priority") is not None else None),
            )
            for item in main_member_issues_cache
            if isinstance(item, dict)
        ]

    standup_ctx = build_standup_context(
        user=user,
        member_names=member_names,
        mcp_client=mcp_client,
        fallback_jira_items=fallback_jira_items,
    )
    messages = run_daily_standup(session, standup_ctx)

    transcript: List[Dict[str, str]] = [
        {"speaker": _message_speaker(msg), "content": msg.content} for msg in messages
    ]

    if main_member_issues_cache:
        team_member_counter = 0
        for row in transcript:
            speaker = str(row.get("speaker", ""))
            if not speaker.startswith("team_member:"):
                continue

            content = str(row.get("content", ""))
            if not _looks_generic_member_update(content):
                team_member_counter += 1
                continue

            start = (team_member_counter * 2) % max(len(main_member_issues_cache), 1)
            fallback_slice = main_member_issues_cache[start : start + 2]
            if not fallback_slice:
                fallback_slice = main_member_issues_cache[:2]

            row["content"] = _build_member_fallback_update_text(
                language=language,
                member_name=speaker.split(":", 1)[1] if ":" in speaker else speaker,
                issues=fallback_slice,
            )
            team_member_counter += 1

    daily_banner = _daily_banner_markdown(language)

    main_member_block: Dict[str, str] | None = None
    follow_up: Dict[str, Any] | None = None
    if main_member and not main_member_update:
        issues = main_member_issues_cache

        tasks_markdown = _format_member_issues_markdown(member=main_member, issues=issues, language=language)
        request_text = _format_update_request(language, main_member)
        session_id = str(uuid4())
        _PENDING_DAILY_SESSIONS[session_id] = {
            "stage": "awaiting_update",
            "language": language,
            "main_member": main_member,
            "issues_markdown": tasks_markdown,
            "last_plan_id": None,
        }
        follow_up = {
            "session_id": session_id,
            "tasks_markdown": tasks_markdown,
            "question": request_text,
            "requires_user_reply": True,
            "next_tool": "daily_followup",
        }

    if main_member and main_member_update:
        user_msg = ConversationMessage(role="user", content=main_member_update)
        session.add_messages([user_msg])
        if language == "en":
            fallback = (
                f"Thanks {main_member}. I noted your update. "
                "Please keep blockers visible in Jira and raise risks early."
            )
        else:
            fallback = (
                f"Gracias {main_member}. Tomé nota de tu actualización. "
                "Mantén los bloqueos visibles en Jira y avisa riesgos con anticipación."
            )

        sm_reply = scrum_master.generate_reply(
            history=session.messages,
            user_context=UserContext(name=main_member, language=language),
            extra_context={
                "fallback_message": fallback,
                "main_member": main_member,
                "user_update": main_member_update,
            },
        )
        session.add_messages([sm_reply])
        main_member_block = {
            "member": main_member,
            "update": main_member_update,
            "scrum_master_reply": sm_reply.content,
        }

        if ask_main_member_followup:
            issue_keys = _extract_issue_keys(main_member_update)
            if language == "en":
                if issue_keys:
                    question_fallback = (
                        f"Regarding {issue_keys[0]}, do you see any dependency that could delay today's progress?"
                    )
                else:
                    question_fallback = (
                        "Do you see any dependency or risk that could delay your progress today?"
                    )
            else:
                if issue_keys:
                    question_fallback = (
                        f"Sobre {issue_keys[0]}, ¿ves alguna dependencia que pueda retrasar el avance de hoy?"
                    )
                else:
                    question_fallback = (
                        "¿Ves alguna dependencia o riesgo que pueda retrasar tu avance de hoy?"
                    )

            sm_question = scrum_master.generate_reply(
                history=session.messages,
                user_context=UserContext(name=main_member, language=language),
                extra_context={
                    "fallback_message": question_fallback,
                    "main_member": main_member,
                    "user_update": main_member_update,
                },
            )
            session.add_messages([sm_question])
            session_id = str(uuid4())
            _PENDING_DAILY_SESSIONS[session_id] = {
                "stage": "awaiting_followup_answer",
                "language": language,
                "main_member": main_member,
                "question": sm_question.content,
                "user_update": main_member_update,
                "last_plan_id": None,
            }
            follow_up = {
                "session_id": session_id,
                "question": sm_question.content,
                "requires_user_reply": True,
                "next_tool": "daily_followup",
            }

    if language == "en":
        summary = (
            f"Daily completed with {len(member_names)} team member(s). "
            "Transcript is included in 'transcript'."
        )
    else:
        summary = (
            f"Daily ejecutada con {len(member_names)} miembro(s) del equipo. "
            "El detalle está en 'transcript'."
        )

    assistant_message_markdown = _build_daily_run_assistant_markdown(
        language=language,
        daily_banner=daily_banner,
        transcript=transcript,
        follow_up=follow_up,
        main_member_block=main_member_block,
    )

    return _response_with_markdown(
        {
            "summary": summary,
            "daily_banner_markdown": daily_banner,
            "language": language,
            "members": member_names,
            "transcript": transcript,
            "main_member_block": main_member_block,
            "follow_up": follow_up,
            "render_hint": "render assistant_message_markdown",
        },
        assistant_message_markdown,
    )


@mcp.tool(
    description=(
        "Run the daily and return a presentation-first markdown payload for end users. "
        "Use this as the default daily tool in Copilot Chat for cleaner UX."
    )
)
def daily_present(
    language: str = "es",
    members: List[str] | None = None,
    main_member: str | None = None,
    main_member_update: str | None = None,
    ask_main_member_followup: bool = True,
) -> Dict[str, Any]:
    raw = daily_run(
        language=language,
        members=members,
        main_member=main_member,
        main_member_update=main_member_update,
        ask_main_member_followup=ask_main_member_followup,
    )
    result_obj = raw.get("result")
    result: Dict[str, Any] = result_obj if isinstance(result_obj, dict) else {}
    follow_up = result.get("follow_up") if isinstance(result.get("follow_up"), dict) else None

    response: Dict[str, Any] = {
        "assistant_message_markdown": _extract_markdown(raw),
        "session_id": follow_up.get("session_id") if follow_up else None,
        "requires_user_reply": bool(follow_up),
        "next_tool": "daily_followup_present" if follow_up else None,
    }
    return response


@mcp.tool(
    description=(
        "Continue a daily conversational session. "
        "Use session_id from daily_run.follow_up and provide user_reply for update, follow-up answers, "
        "or Scrum/Jira requests. IMPORTANT: render `assistant_message_markdown` directly when present."
    )
)
def daily_followup(session_id: str, user_reply: str) -> Dict[str, Any]:
    pending = _PENDING_DAILY_SESSIONS.get(session_id)
    if not pending:
        raise RuntimeError("Invalid or expired session_id. Run daily_run again to start a new follow-up.")

    language = str(pending.get("language", "es"))
    main_member = str(pending.get("main_member", "team member"))
    stage = str(pending.get("stage", "awaiting_update"))
    last_plan_id = pending.get("last_plan_id")

    scrum_master = ScrumMasterAgent(language=language)
    user_ctx = UserContext(name=main_member, language=language)

    # Allow users to close the daily at any stage after update has started.
    if stage != "awaiting_update" and _contains_close_intent(user_reply):
        _PENDING_DAILY_SESSIONS.pop(session_id, None)
        closing = (
            "Perfecto, cerramos la daily. ¡Buen sprint!"
            if language == "es"
            else "Great, daily closed. Have a productive sprint!"
        )
        response_md = _build_followup_markdown(
            language=language,
            title="Daily cerrada" if language == "es" else "Daily closed",
            body=closing,
        )
        return _response_with_markdown(
            {
                "session_id": session_id,
                "closed": True,
                "scrum_master_reply": closing,
            },
            response_md,
        )

    if stage == "awaiting_update":
        update_text = user_reply.strip()
        if not update_text:
            raise RuntimeError("User update cannot be empty.")

        done_candidate_key = _extract_done_candidate_key(update_text)

        if language == "en":
            feedback_fallback = (
                f"Thanks {main_member}. I noted your update. "
                "Please keep your Jira stories updated and highlight blockers early."
            )
        else:
            feedback_fallback = (
                f"Gracias {main_member}. Tomé nota de tu actualización. "
                "Mantén tus historias de Jira al día y visibiliza bloqueos temprano."
            )

        if done_candidate_key:
            if language == "en":
                feedback_fallback += (
                    f" I also see that {done_candidate_key} might be ready for Done; "
                    "if you want, I can transition it in Jira for you."
                )
            else:
                feedback_fallback += (
                    f" También veo que {done_candidate_key} parece lista para Done; "
                    "si quieres, puedo hacer esa transición en Jira por ti."
                )

        feedback_msg = scrum_master.generate_reply(
            history=[ConversationMessage(role="user", content=update_text)],
            user_context=user_ctx,
            extra_context={
                "fallback_message": feedback_fallback,
                "user_update": update_text,
                "main_member": main_member,
            },
        )

        issue_keys = _extract_issue_keys(update_text)
        if language == "en":
            question_fallback = (
                f"About {issue_keys[0]}, do you see any dependency that could delay progress today?"
                if issue_keys
                else "Do you see any dependency or risk that could delay your progress today?"
            )
        else:
            question_fallback = (
                f"Sobre {issue_keys[0]}, ¿ves alguna dependencia que pueda retrasar tu avance hoy?"
                if issue_keys
                else "¿Ves alguna dependencia o riesgo que pueda retrasar tu avance hoy?"
            )

        question_msg = scrum_master.generate_reply(
            history=[ConversationMessage(role="assistant", content=feedback_msg.content)],
            user_context=user_ctx,
            extra_context={
                "fallback_message": question_fallback,
                "user_update": update_text,
                "main_member": main_member,
            },
        )

        pending["stage"] = "awaiting_followup_answer"
        pending["question"] = question_msg.content
        pending["user_update"] = update_text
        _PENDING_DAILY_SESSIONS[session_id] = pending

        response_md = _build_followup_markdown(
            language=language,
            title="Feedback del Scrum Master" if language == "es" else "Scrum Master feedback",
            body=feedback_msg.content,
            extra=(f"**{'Repregunta' if language == 'es' else 'Follow-up question'}:** {question_msg.content}"),
        )
        return _response_with_markdown(
            {
                "session_id": session_id,
                "stage": "awaiting_followup_answer",
                "scrum_master_feedback": feedback_msg.content,
                "followup_question": question_msg.content,
                "requires_user_reply": True,
            },
            response_md,
        )

    if stage == "awaiting_followup_answer":
        if _contains_risk_or_dependency(user_reply):
            if language == "en":
                fallback = (
                    "Thanks, let's manage that dependency immediately. "
                    "Please keep it visible in Jira, tag the owner, and leave a follow-up note for today."
                )
            else:
                fallback = (
                    "Gracias, gestionemos esa dependencia de inmediato. "
                    "Déjala visible en Jira, etiqueta al responsable y deja un seguimiento para hoy."
                )
        else:
            if language == "en":
                fallback = (
                    "Great. Keep momentum and update Jira as soon as anything changes."
                )
            else:
                fallback = (
                    "Perfecto. Mantén el ritmo y actualiza Jira apenas cambie algo."
                )

        ack_msg = scrum_master.generate_reply(
            history=[ConversationMessage(role="user", content=user_reply)],
            user_context=user_ctx,
            extra_context={
                "fallback_message": fallback,
                "followup_user_reply": user_reply,
                "followup_question": pending.get("question"),
                "main_member": main_member,
            },
        )

        pending["stage"] = "awaiting_requests"
        _PENDING_DAILY_SESSIONS[session_id] = pending

        response_md = _build_followup_markdown(
            language=language,
            title="Daily en curso" if language == "es" else "Daily in progress",
            body=ack_msg.content,
            extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
        )
        return _response_with_markdown(
            {
                "session_id": session_id,
                "stage": "awaiting_requests",
                "scrum_master_reply": ack_msg.content,
                "next_prompt": _format_optional_request_prompt(language),
                "requires_user_reply": True,
            },
            response_md,
        )

    if stage == "awaiting_requests":

        if _contains_apply_intent(user_reply) and last_plan_id:
            applied_payload = _call_backend(
                "scrum_master_assistant.apply_action",
                {"plan_id": last_plan_id, "confirm": True, "confirmation_text": "CONFIRM"},
            )
            applied = _unwrap_result(applied_payload)
            pending["last_plan_id"] = None
            _PENDING_DAILY_SESSIONS[session_id] = pending
            body_text = "Cambio aplicado correctamente en Jira." if language == "es" else "Change applied in Jira."
            response_md = _build_followup_markdown(
                language=language,
                title="Resultado Jira" if language == "es" else "Jira result",
                body=body_text,
                extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
            )
            return _response_with_markdown(
                {
                    "session_id": session_id,
                    "stage": "awaiting_requests",
                    "scrum_master_reply": body_text,
                    "apply_result": applied,
                    "next_prompt": _format_optional_request_prompt(language),
                },
                response_md,
            )

        try:
            handled_payload = _call_backend(
                "scrum_master_assistant.handle_request",
                {"request_text": user_reply, "reason": "daily follow-up request"},
            )
            handled = _unwrap_result(handled_payload)
            phase = handled.get("phase")
            action = handled.get("action")

            if phase == "read":
                read_text = _format_read_execution_result(action, handled.get("execution_result"), language)
                response_md = _build_followup_markdown(
                    language=language,
                    title="Consulta Jira" if language == "es" else "Jira query",
                    body=read_text,
                    extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
                )
                return _response_with_markdown(
                    {
                        "session_id": session_id,
                        "stage": "awaiting_requests",
                        "scrum_master_reply": read_text,
                        "next_prompt": _format_optional_request_prompt(language),
                    },
                    response_md,
                )

            plan_id = handled.get("plan_id")
            if plan_id:
                pending["last_plan_id"] = plan_id
                _PENDING_DAILY_SESSIONS[session_id] = pending
                if language == "es":
                    plan_text = (
                        f"Plan creado: {handled.get('preview')}\n"
                        "Si estás de acuerdo, responde con 'aplica' para ejecutarlo."
                    )
                else:
                    plan_text = (
                        f"Plan created: {handled.get('preview')}\n"
                        "If you agree, reply with 'apply' to execute it."
                    )
                response_md = _build_followup_markdown(
                    language=language,
                    title="Plan Jira" if language == "es" else "Jira plan",
                    body=plan_text,
                    extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
                )
                return _response_with_markdown(
                    {
                        "session_id": session_id,
                        "stage": "awaiting_requests",
                        "scrum_master_reply": plan_text,
                        "plan_id": plan_id,
                        "preview": handled.get("preview"),
                        "next_prompt": _format_optional_request_prompt(language),
                    },
                    response_md,
                )

            response_md = _build_followup_markdown(
                language=language,
                title="Respuesta" if language == "es" else "Response",
                body=str(handled),
                extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
            )
            return _response_with_markdown(
                {
                    "session_id": session_id,
                    "stage": "awaiting_requests",
                    "scrum_master_reply": str(handled),
                    "next_prompt": _format_optional_request_prompt(language),
                },
                response_md,
            )
        except Exception:
            if language == "en":
                fallback = (
                    "Good question. As Scrum Master, I suggest keeping focus on sprint goal, "
                    "making blockers visible early, and aligning dependencies with clear ownership."
                )
            else:
                fallback = (
                    "Buena pregunta. Como Scrum Master, te sugiero mantener foco en el objetivo del sprint, "
                    "visibilizar bloqueos temprano y alinear dependencias con responsables claros."
                )

            conceptual = scrum_master.generate_reply(
                history=[ConversationMessage(role="user", content=user_reply)],
                user_context=user_ctx,
                extra_context={
                    "fallback_message": fallback,
                    "main_member": main_member,
                    "user_request": user_reply,
                },
            )
            response_md = _build_followup_markdown(
                language=language,
                title="Guía Scrum" if language == "es" else "Scrum guidance",
                body=conceptual.content,
                extra=(f"**{'Siguiente paso' if language == 'es' else 'Next step'}:** {_format_optional_request_prompt(language)}"),
            )
            return _response_with_markdown(
                {
                    "session_id": session_id,
                    "stage": "awaiting_requests",
                    "scrum_master_reply": conceptual.content,
                    "next_prompt": _format_optional_request_prompt(language),
                },
                response_md,
            )

    body_text = (
        "Estado de sesión no reconocido; ejecuta daily_run para reiniciar."
        if language == "es"
        else "Unknown session state; run daily_run to restart."
    )
    response_md = _build_followup_markdown(
        language=language,
        title="Estado de sesión" if language == "es" else "Session state",
        body=body_text,
    )
    return _response_with_markdown(
        {
            "session_id": session_id,
            "stage": stage,
            "scrum_master_reply": body_text,
            "closed": False,
        },
        response_md,
    )


@mcp.tool(
    description=(
        "Continue a daily session with a presentation-first markdown response. "
        "Use session_id from daily_present and pass user_reply."
    )
)
def daily_followup_present(session_id: str, user_reply: str) -> Dict[str, Any]:
    raw = daily_followup(session_id=session_id, user_reply=user_reply)
    result_obj = raw.get("result")
    result: Dict[str, Any] = result_obj if isinstance(result_obj, dict) else {}
    closed = bool(result.get("closed", False))
    return {
        "assistant_message_markdown": _extract_markdown(raw),
        "session_id": result.get("session_id", session_id),
        "closed": closed,
        "requires_user_reply": not closed,
        "next_tool": None if closed else "daily_followup_present",
    }


if __name__ == "__main__":
    mcp.run()
