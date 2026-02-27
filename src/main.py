import os
import json
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from orchestrator import AgileOrchestrator

load_dotenv()

# Configuración desde .env corregido
endpoint = os.getenv("AZURE_AI_ENDPOINT","")
model_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT") # gpt-5-nano

# Cargar contexto
with open("data/project_context.json") as f:
    project_context = json.load(f)

# Inicializar cliente Foundry
client = AIProjectClient(
    credential=DefaultAzureCredential(),
    endpoint=endpoint
)

# Obtener cliente OpenAI (Protocolo estable)
openai_client = client.get_openai_client(api_version="2024-10-21")

# Instanciar el Orquestador
orchestrator = AgileOrchestrator(openai_client, model_name, project_context)

# Simulación de interacción
update = "Dev1: Sigo con el login de GitHub, pero el API me da error 403 y no puedo avanzar."
results = orchestrator.run_daily_sync(update)

print("\n--- Resultado Final del Razonamiento ---")
print(f"Estrategia SM: {results['sm']}")
if results['po']:
    print(f"Impacto Negocio (PO): {results['po']}")
