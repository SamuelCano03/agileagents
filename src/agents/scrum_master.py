# scrum_master.py
from .base_agent import BaseAgent

class ScrumMasterAgent(BaseAgent):
    def run_daily(self, team_updates: str) -> str:
        system_prompt = """
Eres un Scrum Master que facilita dailies de forma realista.

Objetivos:
- pedir avances
- detectar bloqueos
- hacer preguntas de seguimiento
- mantener el timebox
- promover mejora continua

Hablas como un Scrum Master humano, no como un bot.
Sé breve, claro y empático.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": team_updates},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )

        return response.choices[0].message.content
