"""Session manager for Scrum simulation runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from src.conversations.messages import ConversationMessage, UserContext
from src.agents.scrum_master_agent import ScrumMasterAgent
from src.agents.product_owner_agent import ProductOwnerAgent
from src.agents.team_member_agent import TeamMemberAgent


@dataclass
class ScrumSession:
    """Holds state for a single Scrum ceremony simulation."""

    user: UserContext
    scrum_master: ScrumMasterAgent
    product_owner: ProductOwnerAgent
    team_members: List[TeamMemberAgent]
    messages: List[ConversationMessage] = field(default_factory=list)

    def add_messages(self, new_messages: Iterable[ConversationMessage]) -> None:
        self.messages.extend(list(new_messages))
