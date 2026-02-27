# src/orchestrator.py
from agents.scrum_master import ScrumMasterAgent
from agents.product_owner import ProductOwnerAgent

class AgileOrchestrator:
    def __init__(self, openai_client, model, context):
        # Inicializamos los agentes
        self.scrum_master = ScrumMasterAgent(openai_client, model, context)
        self.product_owner = ProductOwnerAgent(openai_client, model, context)
        self.context = context

    def run_daily_sync(self, user_update: str):
        print("--- 🤖 Iniciando Ceremonia de Daily ---")
        
        # Paso 1: El Scrum Master analiza el reporte
        sm_feedback = self.scrum_master.run_daily(user_update)
        print(f"\nScrum Master: {sm_feedback}")

        # Paso 2: Razonamiento Multi-Agente (El PO verifica si hay riesgos de negocio)
        if "bloqueo" in user_update.lower() or "atraso" in sm_feedback.lower():
            print("\n--- ⚖️ El Product Owner está interviniendo por riesgo detectado ---")
            po_feedback = self.product_owner.evaluate_progress(user_update)
            return {"sm": sm_feedback, "po": po_feedback}
        
        return {"sm": sm_feedback, "po": None}
