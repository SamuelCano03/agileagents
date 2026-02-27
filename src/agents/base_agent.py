# src/agents/base_agent.py
class BaseAgent:
    def __init__(self, openai_client, model: str, context: dict):
        self.client = openai_client
        self.model = model
        self.context = context
