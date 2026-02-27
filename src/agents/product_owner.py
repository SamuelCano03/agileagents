# src/agents/product_owner.py
from .base_agent import BaseAgent

class ProductOwnerAgent(BaseAgent):
    def evaluate_progress(self, update: str):
        system_prompt = f"""
        Eres el Product Owner de {self.context['project_name']}. 
        Tu objetivo es maximizar el valor del producto.
        Criterios de éxito (DoD): {self.context['definition_of_done']}
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"El equipo reporta esto: {update}. ¿Cumple con la visión del producto?"}
            ]
        )
        return response.choices[0].message.content
