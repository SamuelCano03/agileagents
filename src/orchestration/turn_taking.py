"""Turn-taking logic for Scrum ceremonies."""

from __future__ import annotations

from typing import Iterable, List

from src.conversations.messages import ConversationMessage, UserContext
from src.orchestration.session_manager import ScrumSession
from src.orchestration.context_builder import StandupContext, MemberContext


def run_daily_standup(
    session: ScrumSession,
    standup_context: StandupContext,
) -> List[ConversationMessage]:
    """Simulate a simple daily stand-up.

    This is a first version meant to demonstrate agent interaction.
    It does *not* yet use true multi-turn reasoning from the underlying
    Microsoft Agent Framework, but it already structures the flow in a
    way that will be easy to upgrade.
    """

    messages: List[ConversationMessage] = []

    # 1) Scrum Master opens the stand-up
    opening = session.scrum_master.generate_reply(
        history=session.messages,
        user_context=session.user,
        extra_context={"fallback_message": _opening_message(session.user)},
    )
    messages.append(opening)

    # 2) Each team member gives an update based on their context
    history: List[ConversationMessage] = session.messages + messages
    member_summaries: List[dict] = []
    for member_ctx, member_agent in zip(standup_context.members, session.team_members):
        member_msg, member_summary = _member_update(member_agent, member_ctx, session.user, history)
        messages.append(member_msg)
        history.append(member_msg)
        member_summaries.append(member_summary)

    # 3) Scrum Master closes, using a light-weight summary of blockers/issues
    closing_summary = _closing_message(session.user, member_summaries)
    closing = session.scrum_master.generate_reply(
        history=history,
        user_context=session.user,
        extra_context={"fallback_message": closing_summary},
    )
    messages.append(closing)

    session.add_messages(messages)
    return messages


def _opening_message(user: UserContext) -> str:
    if user.language == "en":
        return "Welcome to the stand-up. We'll quickly go through updates and blockers."
    return "Bienvenidos a la daily. Revisemos rápidamente avances y bloqueos."


def _closing_message(user: UserContext, member_summaries: List[dict]) -> str:
    total_issues = sum(len(m.get("issues", [])) for m in member_summaries)
    blockers = [m for m in member_summaries if m.get("has_blockers")]

    if user.language == "en":
        if total_issues == 0:
            base = "Thanks everyone. No active issues were reported today."
        else:
            base = f"Thanks everyone. We discussed {total_issues} Jira issues across the team."

        if blockers:
            blocker_keys = ", ".join(
                sorted({k for m in blockers for k in m.get("issues", [])})
            )
            return (
                base
                + f" Let's prioritize unblocking: {blocker_keys}. We'll follow up right after this call."
            )

        return base + " Let's follow up on any smaller concerns asynchronously."

    # Spanish
    if total_issues == 0:
        base = "Gracias a todos. Hoy no se reportaron historias activas en Jira."
    else:
        base = f"Gracias a todos. Revisamos {total_issues} historias de Jira entre el equipo."

    if blockers:
        blocker_keys = ", ".join(
            sorted({k for m in blockers for k in m.get("issues", [])})
        )
        return (
            base
            + f" Demos prioridad a desbloquear: {blocker_keys}. Luego coordinamos los siguientes pasos."
        )

    return base + " Sigamos cualquier detalle menor de forma asíncrona."


def _member_update(
    member_agent,
    member_ctx: MemberContext,
    user: UserContext,
    history: Iterable[ConversationMessage],
) -> tuple[ConversationMessage, dict]:
    """Create a basic message for a given team member.

    For now we just pass in a high-level summary of issues as
    `extra_context`. Later this will become richer, possibly with the
    agent itself deciding how to query tools during its turn.
    """

    issues = member_ctx.jira_issues
    used_team_fallback = bool(getattr(member_ctx, "used_team_fallback", False))

    issue_keys = [i.key for i in issues if i.key]
    # Simple heuristic: any issue whose status name contains keywords is considered a blocker.
    blocker_like_statuses = {"blocked", "impeded", "on hold"}
    has_blockers = any(
        (i.status or "").lower() in blocker_like_statuses for i in issues
    )

    # Build language-aware descriptions using richer Jira information
    # We focus on the first few issues to keep the message concise.
    top_issues = issues[:2]

    def _format_issue_snippets(lang: str) -> str:
        snippets: list[str] = []
        for issue in top_issues:
            base = issue.key
            if issue.summary:
                base += f" ({issue.summary})"
            if issue.story_points is not None:
                pts = issue.story_points
                if lang == "en":
                    base += f" [{pts} story points]"
                else:
                    base += f" [{pts} puntos]"
            snippets.append(base)
        return "; ".join(snippets)

    if member_agent.language == "en":
        if issue_keys:
            descr = _format_issue_snippets("en")
            if used_team_fallback:
                yesterday = (
                    f"I don't see issues directly assigned to my display name, "
                    f"so I progressed on sprint-priority Jira work: {descr}."
                )
            else:
                yesterday = f"I progressed on Jira work: {descr}."
            if issues and issues[0].sprint_name:
                sprint = issues[0].sprint_name
                yesterday += f" This is part of sprint '{sprint}'."
            today = "I will keep pushing these stories toward Done."
        else:
            yesterday = "I worked on several sprint tasks without specific Jira issues recorded."
            today = "I will continue with the current sprint work."

        blockers = (
            "I am blocked on some issues that need attention."
            if has_blockers
            else "I have no major blockers for now."
        )
    else:
        if issue_keys:
            descr = _format_issue_snippets("es")
            if used_team_fallback:
                yesterday = (
                    f"no veo historias asignadas exactamente a mi nombre, "
                    f"así que avancé en historias prioritarias del sprint: {descr}."
                )
            else:
                yesterday = f"avancé en trabajo de Jira: {descr}."
            if issues and issues[0].sprint_name:
                sprint = issues[0].sprint_name
                yesterday += f" Esto forma parte del sprint '{sprint}'."
            today = "seguiré empujando esas historias hacia terminado."
        else:
            yesterday = "avancé en tareas del sprint sin historias específicas en Jira."
            today = "seguiré trabajando en el trabajo planificado del sprint."

        blockers = (
            "tengo bloqueos en algunas historias que necesitamos revisar."
            if has_blockers
            else "no tengo bloqueos importantes por ahora"
        )

    msg = member_agent.generate_reply(
        history=history,
        user_context=user,
        extra_context={
            "yesterday": yesterday,
            "today": today,
            "blockers": blockers,
            "issues": issue_keys,
            "commits": member_ctx.commits,
        },
    )

    summary = {
        "member": member_ctx.name,
        "issues": issue_keys,
        "has_blockers": has_blockers,
    }

    return msg, summary
