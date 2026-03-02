"""Simple CLI entrypoint for running a daily stand-up simulation."""

from __future__ import annotations

import argparse
import re
import json
from typing import List

from rich.console import Console

from src.config.settings import settings
from src.conversations.messages import ConversationMessage, UserContext
from src.mcp.client import McpClient
from src.mcp.tools_jira import (
    apply_scrum_master_action,
    get_active_sprint_issues,
    handle_scrum_master_request,
    seed_sample_backlog,
)
from src.orchestration.context_builder import build_standup_context
from src.orchestration.session_manager import ScrumSession
from src.orchestration.turn_taking import run_daily_standup
from src.agents.scrum_master_agent import ScrumMasterAgent
from src.agents.product_owner_agent import ProductOwnerAgent
from src.agents.team_member_agent import TeamMemberAgent


console = Console()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run an Agile Agents daily stand-up simulation.")
    parser.add_argument("--language", choices=["es", "en"], default=settings.default_language)
    parser.add_argument(
        "--members",
        nargs="+",
        default=["Alice", "Bob"],
        help="Names of AI team members to include in the stand-up.",
    )

    parser.add_argument(
        "--main-member",
        "--main_member",
        dest="main_member",
        default=None,
        help=(
            "Name of the human main team member who will participate "
            "interactively in the stand-up. This should match the assignee "
            "name used in Jira."
        ),
    )

    parser.add_argument(
        "--seed-demo-backlog",
        dest="seed_demo_backlog",
        type=int,
        default=0,
        help=(
            "Create a demo Jira backlog with N issues before running the stand-up. "
            "Requires Jira writes enabled on MCP server (JIRA_ALLOW_WRITES=true)."
        ),
    )
    parser.add_argument(
        "--seed-topic",
        dest="seed_topic",
        default="Agile training",
        help="Topic/prefix used when creating demo backlog issues.",
    )
    parser.add_argument(
        "--sm-assistant-chat",
        dest="sm_assistant_chat",
        action="store_true",
        help=(
            "Run interactive Scrum Master assistant chat for natural-language Jira actions "
            "(plan + explicit confirmation + apply)."
        ),
    )

    args = parser.parse_args(argv)

    # Session-level context for the simulated team stand-up.
    # The main member (if provided) participates later in an interactive turn,
    # but is not the facilitator of the AI-only stand-up.
    user = UserContext(name="Scrum Master", language=args.language)

    scrum_master = ScrumMasterAgent(language=args.language)
    product_owner = ProductOwnerAgent(language=args.language)
    team_members: List[TeamMemberAgent] = [
        TeamMemberAgent(name=m, language=args.language) for m in args.members
    ]

    session = ScrumSession(
        user=user,
        scrum_master=scrum_master,
        product_owner=product_owner,
        team_members=team_members,
    )

    mcp_client = McpClient()

    if args.sm_assistant_chat:
        _run_scrum_master_assistant_chat(mcp_client=mcp_client, language=args.language)
        return

    if args.seed_demo_backlog and args.seed_demo_backlog > 0:
        _seed_demo_backlog_via_cli(
            mcp_client=mcp_client,
            language=args.language,
            count=args.seed_demo_backlog,
            topic=args.seed_topic,
        )

    standup_ctx = build_standup_context(
        user=user,
        member_names=args.members,
        mcp_client=mcp_client,
    )

    messages = run_daily_standup(session, standup_ctx)

    _print_transcript(messages)

    # After the AI-only stand-up, optionally run an interactive turn
    # where the main (human) member gives their update and chats with
    # the Scrum Master.
    if args.main_member:
        _run_main_member_interaction(
            main_member=args.main_member,
            scrum_master=scrum_master,
            mcp_client=mcp_client,
            language=args.language,
        )


def _print_transcript(messages) -> None:
    """Render the conversation in a simple, readable way."""

    for msg in messages:
        if isinstance(msg.metadata, dict):
            agent = msg.metadata.get("agent")
            speaker_name = msg.metadata.get("name")
        else:
            agent = None
            speaker_name = None

        if agent == "team_member" and speaker_name:
            prefix = f"[team_member:{speaker_name}]"
        else:
            prefix = f"[{agent}]" if agent else f"[{msg.role}]"

        console.print(f"{prefix} {msg.content}")


def _seed_demo_backlog_via_cli(
    *,
    mcp_client: McpClient,
    language: str,
    count: int,
    topic: str,
) -> None:
    """Create demo backlog issues through MCP and print results."""

    safe_count = max(1, min(count, 15))

    if language == "en":
        console.print(
            f"[system] Seeding demo backlog with {safe_count} issue(s) for topic '{topic}'..."
        )
    else:
        console.print(
            f"[system] Poblando backlog demo con {safe_count} issue(s) para el tema '{topic}'..."
        )

    try:
        result = seed_sample_backlog(mcp_client, topic=topic, count=safe_count)
        payload = result.raw_result if isinstance(result.raw_result, dict) else {}
        created = payload.get("issues", []) if isinstance(payload, dict) else []

        if language == "en":
            console.print(
                f"[system] Demo backlog created: {payload.get('created_count', len(created))} issue(s)."
            )
        else:
            console.print(
                f"[system] Backlog demo creado: {payload.get('created_count', len(created))} issue(s)."
            )

        for item in created:
            key = item.get("key") if isinstance(item, dict) else None
            if key:
                console.print(f"  - {key}")

    except Exception as exc:
        if language == "en":
            console.print(
                "[system] Could not seed demo backlog. "
                "Check MCP/Jira server and whether writes are enabled "
                f"(JIRA_ALLOW_WRITES=true). Detail: {type(exc).__name__}."
            )
        else:
            console.print(
                "[system] No se pudo poblar el backlog demo. "
                "Verifica el servidor MCP/Jira y que las escrituras estén habilitadas "
                f"(JIRA_ALLOW_WRITES=true). Detalle: {type(exc).__name__}."
            )


def _run_scrum_master_assistant_chat(*, mcp_client: McpClient, language: str) -> None:
    """Interactive natural-language chat for Jira assistant actions.

    This mode calls the MCP tool `scrum_master_assistant.handle_request`
    to create a plan from user text and then asks explicit confirmation
    before applying writes.
    """

    if language == "en":
        console.print(
            "[system] Scrum Master Assistant chat mode. Type a Jira request in natural language."
        )
        console.print("[system] Type 'exit' to quit.")
    else:
        console.print(
            "[system] Modo chat del Scrum Master Assistant. Escribe una petición Jira en lenguaje natural."
        )
        console.print("[system] Escribe 'exit' para salir.")

    while True:
        prompt = "[you]" if language == "en" else "[tú]"
        try:
            console.print(f"{prompt} ", end="")
            text = input().strip()
        except KeyboardInterrupt:
            console.print("\n[system] Bye." if language == "en" else "\n[system] Hasta luego.")
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit", "salir"}:
            break

        try:
            plan = handle_scrum_master_request(
                mcp_client,
                request_text=text,
                reason="sm assistant chat",
            )
            payload = plan.raw_result if isinstance(plan.raw_result, dict) else {}
            result = payload.get("result", payload) if isinstance(payload, dict) else {}
        except Exception as exc:
            err_detail = _format_exception_detail(exc)
            if language == "en":
                console.print(f"[scrum_master] I couldn't process that request: {err_detail}")
            else:
                console.print(f"[scrum_master] No pude procesar esa petición: {err_detail}")
            continue

        plan_id = result.get("plan_id") if isinstance(result, dict) else None
        action = result.get("action") if isinstance(result, dict) else None
        preview = result.get("preview") if isinstance(result, dict) else None
        requires_confirmation = bool(result.get("requires_confirmation", True)) if isinstance(result, dict) else True

        # Read-only actions can return immediate execution_result.
        if not requires_confirmation:
            execution_result = result.get("execution_result") if isinstance(result, dict) else None
            if language == "en":
                console.print(f"[scrum_master] Read action executed: {action}")
            else:
                console.print(f"[scrum_master] Acción de lectura ejecutada: {action}")
            console.print(f"[system] {_format_read_execution_result(execution_result, language=language)}")
            continue

        if language == "en":
            console.print(f"[scrum_master] Planned action: {action}")
            console.print(f"[scrum_master] Preview: {preview}")
            console.print("[scrum_master] Apply this change? (yes/no)")
        else:
            console.print(f"[scrum_master] Acción planificada: {action}")
            console.print(f"[scrum_master] Vista previa: {preview}")
            console.print("[scrum_master] ¿Aplico este cambio? (sí/no)")

        answer = input("> ").strip().lower()
        if answer not in {"yes", "y", "si", "sí"}:
            if language == "en":
                console.print("[scrum_master] Okay, plan kept but not applied.")
            else:
                console.print("[scrum_master] Perfecto, el plan queda creado pero no aplicado.")
            continue

        if not plan_id:
            if language == "en":
                console.print("[scrum_master] No plan_id found; cannot apply.")
            else:
                console.print("[scrum_master] No encontré plan_id; no puedo aplicar.")
            continue

        try:
            applied = apply_scrum_master_action(
                mcp_client,
                plan_id=plan_id,
                confirm=True,
                confirmation_text="CONFIRM",
            )
            apply_payload = applied.raw_result if isinstance(applied.raw_result, dict) else {}
            apply_result = (
                apply_payload.get("result", apply_payload)
                if isinstance(apply_payload, dict)
                else apply_payload
            )
            if language == "en":
                console.print("[scrum_master] Change applied successfully.")
            else:
                console.print("[scrum_master] Cambio aplicado correctamente.")
            console.print(f"[system] {apply_result}")
        except Exception as exc:
            err_detail = _format_exception_detail(exc)
            if language == "en":
                console.print(f"[scrum_master] Apply failed: {err_detail}")
            else:
                console.print(f"[scrum_master] Falló la aplicación: {err_detail}")


def _format_exception_detail(exc: Exception) -> str:
    """Extract user-friendly error details from HTTP exceptions."""

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
                if detail:
                    return f"{type(exc).__name__}: {detail}"
                return f"{type(exc).__name__}: {json.dumps(payload, ensure_ascii=False)}"
        except Exception:
            text = getattr(response, "text", "")
            if text:
                return f"{type(exc).__name__}: {text}"

    return f"{type(exc).__name__}: {exc}"


def _format_read_execution_result(execution_result: object, *, language: str) -> str:
    """Render read-only Jira results as concise natural language."""

    if not isinstance(execution_result, dict):
        return str(execution_result)

    key = execution_result.get("key")
    if not key:
        return str(execution_result)

    field_labels_es = {
        "summary": "nombre",
        "description": "descripción",
        "status": "estado",
        "assignee": "asignado",
        "priority": "prioridad",
        "story_points": "story points",
        "updated": "actualizado",
    }
    field_labels_en = {
        "summary": "summary",
        "description": "description",
        "status": "status",
        "assignee": "assignee",
        "priority": "priority",
        "story_points": "story points",
        "updated": "updated",
    }

    labels = field_labels_en if language == "en" else field_labels_es
    ordered_fields = [
        "summary",
        "description",
        "status",
        "assignee",
        "priority",
        "story_points",
        "updated",
    ]

    parts: list[str] = []
    for field in ordered_fields:
        if field not in execution_result:
            continue
        value = execution_result.get(field)
        if value is None:
            value = "not set" if language == "en" else "sin valor"
        parts.append(f"{labels[field]}: {value}")

    if not parts:
        return f"{key}" if language == "en" else f"{key}"

    if language == "en":
        return f"Issue {key} -> " + " | ".join(parts)
    return f"Issue {key} -> " + " | ".join(parts)


def _run_main_member_interaction(
    *,
    main_member: str,
    scrum_master: ScrumMasterAgent,
    mcp_client: McpClient,
    language: str,
) -> None:
    """Run an interactive turn for the main human team member.

    The flow is:
    - Fetch Jira issues assigned to the main member and show them.
    - Ask the user to type their own stand-up update.
    - The Scrum Master validates referenced issues and can ask a
      follow-up question.
    - Then a small Q&A loop lets the user ask questions to the
      Scrum Master, until they say they have no more doubts.
    """

    user_ctx = UserContext(name=main_member, language=language)
    conversation_history: list[ConversationMessage] = []

    # 1) Load Jira issues for the main member
    try:
        issues = list(get_active_sprint_issues(mcp_client, assignee=main_member))
    except Exception as exc:
        if language == "en":
            msg = (
                "I couldn't reach Jira through MCP right now. "
                "Please verify that the MCP/Jira server is running and try again. "
                f"Technical detail: {type(exc).__name__}."
            )
        else:
            msg = (
                "No pude conectarme a Jira mediante MCP en este momento. "
                "Verifica que el servidor MCP/Jira esté levantado e inténtalo de nuevo. "
                f"Detalle técnico: {type(exc).__name__}."
            )

        sm_msg = scrum_master.generate_reply(
            history=conversation_history,
            user_context=user_ctx,
            extra_context={"fallback_message": msg, "main_member": main_member},
        )
        conversation_history.append(sm_msg)
        console.print(f"[scrum_master] {sm_msg.content}")
        return
    if not issues:
        # Scrum Master-style feedback if we cannot find issues.
        if language == "en":
            msg = (
                f"I couldn't find active Jira issues assigned to '{main_member}'. "
                "Please check the name or your assignments in Jira."
            )
        else:
            msg = (
                f"No encontré historias activas en Jira asignadas a '{main_member}'. "
                "Revisa que el nombre coincida con el de Jira y que tengas historias asignadas."
            )

        sm_msg = scrum_master.generate_reply(
            history=conversation_history,
            user_context=user_ctx,
            extra_context={"fallback_message": msg, "main_member": main_member},
        )
        conversation_history.append(sm_msg)
        console.print(f"[scrum_master] {sm_msg.content}")
        return

    issue_context = [
        {
            "key": i.key,
            "summary": i.summary,
            "status": i.status,
            "story_points": i.story_points,
            "sprint_name": i.sprint_name,
            "priority": i.priority,
        }
        for i in issues
    ]

    # Show the user's issues
    if language == "en":
        console.print(
            f"[system] Now it's your turn, {main_member}. These are your current Jira issues:",
        )
    else:
        console.print(
            f"[system] Ahora es tu turno, {main_member}. Estas son tus historias actuales en Jira:",
        )

    for issue in issues:
        parts = [issue.key]
        if issue.summary:
            parts.append(f"{issue.summary}")
        if issue.story_points is not None:
            pts = issue.story_points
            parts.append(f"{pts} pts")
        if issue.status:
            parts.append(f"estado: {issue.status}")
        line = " - ".join(parts)
        console.print(f"  - {line}")

    # 2) User gives their own update
    if language == "en":
        console.print(
            "[you] Type your stand-up update about these issues (mention keys like SCC-1 if you want), then press Enter:",
        )
    else:
        console.print(
            "[tú] Escribe tu actualización de daily sobre estas historias (menciona claves como SCC-1 si quieres) y pulsa Enter:",
        )

    user_update = input("> ").strip()
    conversation_history.append(ConversationMessage(role="user", content=user_update))

    # Validate that any referenced issue keys exist and belong to this member
    mentioned_keys = set(
        re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", user_update)
    )
    owned_keys = {i.key for i in issues if i.key}
    issues_by_key = {i.key: i for i in issues if i.key}
    invalid_keys = sorted(k for k in mentioned_keys if k not in owned_keys)

    # Simple detection of risk/dependency language in the user's update
    update_lower = user_update.lower()
    risk_tokens = [
        "riesgo",
        "risk",
        "bloque",
        "blocked",
        "depende",
        "dependen",
        "dependencia",
        "permiso",
        "privileg",
        "acceso",
    ]
    has_risk_in_update = any(tok in update_lower for tok in risk_tokens)
    mentions_github = "github" in update_lower

    if invalid_keys:
        if language == "en":
            text = (
                "I noticed you mentioned issue(s) "
                + ", ".join(invalid_keys)
                + ", but they either do not exist in this context or are not assigned to you. "
                "Please double-check the keys."
            )
        else:
            text = (
                "Noté que mencionaste la(s) historia(s) "
                + ", ".join(invalid_keys)
                + ", pero no las encuentro como historias tuyas en este contexto. "
                "Por favor revisa las claves."
            )
    else:
        # More contextual feedback, especially if the user mentions
        # dependencies, risks or permissions in their update.
        sorted_keys = sorted(mentioned_keys)
        if language == "en":
            if sorted_keys and has_risk_in_update and mentions_github:
                focus_key = sorted_keys[0]
                issue = issues_by_key.get(focus_key)
                issue_name = issue.summary if issue and issue.summary else focus_key
                text = (
                    f"Thanks for the update. For {focus_key} ({issue_name}) I see you "
                    "mentioned GitHub permissions as a dependency. Make sure that dependency "
                    "is clearly described in Jira and that the person managing permissions is "
                    "tagged so the team can unblock you quickly."
                )
            elif sorted_keys and has_risk_in_update:
                text = (
                    "Thanks for the update. Your summary for issues "
                    + ", ".join(sorted_keys)
                    + " highlights some risks or dependencies; let's keep them visible in Jira so "
                    "we can react early."
                )
            elif sorted_keys:
                text = (
                    "Thanks for the update. Your summary for issues "
                    + ", ".join(sorted_keys)
                    + " sounds aligned with the current sprint."
                )
            else:
                text = "Thanks for the update. Your work fits well with the current sprint goals."
        else:
            if sorted_keys and has_risk_in_update and mentions_github:
                focus_key = sorted_keys[0]
                issue = issues_by_key.get(focus_key)
                issue_name = issue.summary if issue and issue.summary else focus_key
                text = (
                    f"Gracias por la actualización. En {focus_key} ({issue_name}) comentas "
                    "una dependencia de permisos en GitHub. Te sugiero dejar esa dependencia "
                    "explícita en Jira y etiquetar a quien gestione los permisos para poder "
                    "desbloquearte lo antes posible."
                )
            elif sorted_keys and has_risk_in_update:
                text = (
                    "Gracias por la actualización. Tu resumen de las historias "
                    + ", ".join(sorted_keys)
                    + " ya refleja algunos riesgos o dependencias; mantengámoslas visibles en Jira "
                    "para poder reaccionar a tiempo."
                )
            elif sorted_keys:
                text = (
                    "Gracias por la actualización. Tu resumen de las historias "
                    + ", ".join(sorted_keys)
                    + " está alineado con los objetivos del sprint."
                )
            else:
                text = "Gracias por la actualización. Tu trabajo está alineado con los objetivos actuales del sprint."

    sm_first = scrum_master.generate_reply(
        history=conversation_history,
        user_context=user_ctx,
        extra_context={
            "fallback_message": text,
            "main_member": main_member,
            "user_update": user_update,
            "mentioned_issue_keys": sorted(mentioned_keys),
            "invalid_issue_keys": invalid_keys,
            "has_risk_in_update": has_risk_in_update,
            "mentions_github": mentions_github,
            "jira_issues": issue_context,
        },
    )
    conversation_history.append(sm_first)
    console.print(f"[scrum_master] {sm_first.content}")

    # If the first Scrum Master response already includes a direct
    # question, treat it as the follow-up question and avoid asking a
    # second, redundant question right after.
    first_has_question = "?" in sm_first.content or "¿" in sm_first.content
    if first_has_question:
        first_answer = input("> ").strip()
        conversation_history.append(ConversationMessage(role="user", content=first_answer))

        first_answer_lower = first_answer.lower()
        positive_yes = {"si", "sí", "yes", "yep", "claro"}
        if first_answer_lower in positive_yes or any(tok in first_answer_lower for tok in risk_tokens):
            if language == "en":
                first_ack = (
                    "Understood. Please keep that dependency visible in Jira and tag the owner "
                    "so we can unblock quickly. If needed, create a follow-up task to track it."
                )
            else:
                first_ack = (
                    "Entendido. Mantén esa dependencia visible en Jira y etiqueta al responsable "
                    "para desbloquearla rápido. Si hace falta, crea una tarea de seguimiento."
                )
            sm_first_ack = scrum_master.generate_reply(
                history=conversation_history,
                user_context=user_ctx,
                extra_context={
                    "fallback_message": first_ack,
                    "first_followup_answer": first_answer,
                    "jira_issues": issue_context,
                },
            )
            conversation_history.append(sm_first_ack)
            console.print(f"[scrum_master] {sm_first_ack.content}")

    # 3) Optional follow-up question from the Scrum Master about one issue
    #    (only if we didn't already consume a question in sm_first).
    open_issues = [i for i in issues if (i.status or "").lower() not in {"done", "closed"}]
    if open_issues and not first_has_question:
        # Prefer an issue explicitly mentioned by the user, otherwise
        # fall back to the first open issue.
        focus = None
        for key in sorted(mentioned_keys):
            issue = issues_by_key.get(key)
            if issue and (issue.status or "").lower() not in {"done", "closed"}:
                focus = issue
                break
        if focus is None:
            focus = open_issues[0]
        if language == "en":
            q_text = (
                f"Regarding {focus.key} ({focus.summary}), do you see any risk or dependency "
                "that could slow you down today?"
            )
        else:
            q_text = (
                f"Sobre {focus.key} ({focus.summary}), ¿ves algún riesgo o dependencia "
                "que pueda frenarte hoy?"
            )

        sm_q = scrum_master.generate_reply(
            history=conversation_history,
            user_context=user_ctx,
            extra_context={
                "fallback_message": q_text,
                "focus_issue": {
                    "key": focus.key,
                    "summary": focus.summary,
                    "status": focus.status,
                    "story_points": focus.story_points,
                    "sprint_name": focus.sprint_name,
                },
                "jira_issues": issue_context,
            },
        )
        conversation_history.append(sm_q)
        console.print(f"[scrum_master] {sm_q.content}")
        followup_answer = input("> ").strip()
        conversation_history.append(ConversationMessage(role="user", content=followup_answer))

        # Basic acknowledgement of the answer, nudging towards action
        ans_lower = followup_answer.lower()
        positive_yes = {"si", "sí", "yes", "yep", "claro"}
        if ans_lower in positive_yes or any(tok in ans_lower for tok in risk_tokens):
            if language == "en":
                fu_text = (
                    f"Got it, let's treat {focus.key} as something to keep an eye on. "
                    "Capture that risk or dependency in Jira and, if needed, "
                    "break it down into a follow-up task so it doesn't block the rest of the work."
                )
            else:
                fu_text = (
                    f"Perfecto, consideremos {focus.key} como algo a vigilar. "
                    "Deja ese riesgo o dependencia reflejado en Jira y, si hace falta, "
                    "créa una tarea de seguimiento para que no bloquee el resto del trabajo."
                )
            sm_fu = scrum_master.generate_reply(
                history=conversation_history,
                user_context=user_ctx,
                extra_context={
                    "fallback_message": fu_text,
                    "focus_issue": {
                        "key": focus.key,
                        "summary": focus.summary,
                        "status": focus.status,
                    },
                    "followup_answer": followup_answer,
                },
            )
            conversation_history.append(sm_fu)
            console.print(f"[scrum_master] {sm_fu.content}")

    # 4) Q&A loop for the main member's questions
    while True:
        if language == "en":
            prompt = "Do you have any question or dependency to discuss? (yes/no) "
        else:
            prompt = "¿Tienes alguna duda o dependencia que quieras comentar? (sí/no) "

        console.print(f"[scrum_master] {prompt}")
        answer = input("> ").strip().lower()
        if answer in {"no", "n", "", "nah", "nop", "nope"}:
            if language == "en":
                farewell = "Great, thanks for the conversation. Let's continue with the sprint."
            else:
                farewell = "Genial, gracias por la conversación. Sigamos con el sprint."

            sm_end = scrum_master.generate_reply(
                history=conversation_history,
                user_context=user_ctx,
                extra_context={
                    "fallback_message": farewell,
                    "main_member": main_member,
                    "jira_issues": issue_context,
                },
            )
            conversation_history.append(sm_end)
            console.print(f"[scrum_master] {sm_end.content}")
            break

        # The user has at least one question
        if language == "en":
            console.print("[you] Please type your question for the Scrum Master:")
        else:
            console.print("[tú] Escribe tu pregunta para el Scrum Master:")

        question = input("> ").strip()
        conversation_history.append(ConversationMessage(role="user", content=question))

        q_keys = set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", question))
        invalid_q_keys = sorted(k for k in q_keys if k not in owned_keys)

        if invalid_q_keys:
            if language == "en":
                ans_text = (
                    "You referenced issue(s) "
                    + ", ".join(invalid_q_keys)
                    + ", but they are not registered as your Jira issues in this context. "
                    "Please double-check who owns them."
                )
            else:
                ans_text = (
                    "Mencionaste la(s) historia(s) "
                    + ", ".join(invalid_q_keys)
                    + ", pero no aparecen como historias tuyas en este contexto. "
                    "Revisa quién es el responsable en Jira."
                )
        else:
            # Generic guidance; if a specific issue is mentioned, reference it with some context
            if q_keys:
                key = sorted(q_keys)[0]
                issue_map = {i.key: i for i in issues if i.key}
                issue = issue_map.get(key)
                if language == "en":
                    base = f"For {key} ({issue.summary if issue else 'this issue'}), "
                    ans_text = (
                        base
                        + "make sure any dependency is clearly documented in the description "
                        "and comments, and that the owner of the dependency is tagged. "
                        "If the risk impacts the sprint, consider creating a follow-up task or "
                        "raising it explicitly in the next stand-up."
                    )
                else:
                    base = f"Para {key} ({issue.summary if issue else 'esta historia'}), "
                    ans_text = (
                        base
                        + "asegúrate de documentar claramente la dependencia en la descripción "
                        "y en los comentarios, etiquetando al responsable. "
                        "Si el riesgo impacta el sprint, crea una tarea de seguimiento o "
                        "menciónalo explícitamente en la próxima daily."
                    )
            else:
                q_lower = question.lower()
                if (
                    "github" in q_lower
                    or "git hub" in q_lower
                    or "permiso" in q_lower
                    or "privileg" in q_lower
                ):
                    if language == "en":
                        ans_text = (
                            "When you lack GitHub permissions, make the need explicit in Jira "
                            "(comment or sub-task) and tag the repository owner or admin. "
                            "You can also agree on an SLA with them so this kind of dependency "
                            "doesn't systematically block your progress."
                        )
                    else:
                        ans_text = (
                            "Cuando no tienes privilegios en GitHub, deja claro en Jira qué acceso "
                            "necesitas y etiqueta al dueño del repositorio o al administrador. "
                            "Pueden acordar un tiempo de respuesta para que este tipo de dependencia "
                            "no bloquee de forma recurrente tu avance."
                        )
                else:
                    if language == "en":
                        ans_text = (
                            "In general, when you have a dependency or question, make it visible in Jira "
                            "(comments or a dedicated sub-task), and ensure the impacted people are tagged. "
                            "That way the team can react quickly without waiting for the next meeting."
                        )
                    else:
                        ans_text = (
                            "En general, cuando tengas una dependencia o duda, hazla visible en Jira "
                            "(comentarios o una subtarea dedicada) y etiqueta a las personas impactadas. "
                            "Así el equipo puede reaccionar rápido sin esperar a la próxima reunión."
                        )

        sm_ans = scrum_master.generate_reply(
            history=conversation_history,
            user_context=user_ctx,
            extra_context={
                "fallback_message": ans_text,
                "question": question,
                "question_issue_keys": sorted(q_keys),
                "invalid_question_issue_keys": invalid_q_keys,
                "jira_issues": issue_context,
            },
        )
        conversation_history.append(sm_ans)
        console.print(f"[scrum_master] {sm_ans.content}")


if __name__ == "__main__":  # pragma: no cover
    main()
