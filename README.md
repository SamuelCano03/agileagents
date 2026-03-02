# AgileAgents

**AgileAgents** is a multi-agent AI system designed to automate Scrum ceremonies and Jira management. It bridges the gap between conversation and execution by integrating specialized agents directly into your development workflow via the **Model Context Protocol (MCP)**.

---

## 🛠 Configuration

Create a `.env` file with the following variables:

```bash
# Jira Credentials
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_API_TOKEN=your_token
JIRA_EMAIL=your@email.com
JIRA_PROJECT_KEY=SCC

# Governance & Safety
JIRA_ALLOW_WRITES=true # Must be true to enable write operations

# Hybrid Mode (Optional: LLM-backed agent responses)
LLM_MODE=github_models
GITHUB_TOKEN=your_github_pat
GITHUB_MODEL=gpt-4.1-mini

```

---

## 🛡 Jira Governance: Plan & Apply

AgileAgents uses a strict **two-phase contract** for write operations to ensure human-in-the-loop safety:

1. **Plan (`plan_action`)**: The agent analyzes the request and returns a `plan_id` with a preview. No data is changed.
2. **Apply (`apply_action`)**: Execution only occurs with explicit confirmation (`confirm=true` and `confirmation_text="CONFIRM"`).

**Supported Actions:** Create, transition, assign, comment, edit issues, update priority, and move to active sprints.
**Audit Trail:** All events are logged in `logs/sm_assistant_audit.jsonl`.

---

## 📅 Automated Daily Meetings

Run interactive dailies with simulated members (Alice, Bob) and real-time Jira integration for the main developer (the name of the fake members must appear in the jira board in some issues to be tracked).

* **Interactive Flow**: The Scrum Master requests updates, identifies blockers, and suggests Jira actions.
* **Markdown-First UI**: Responses include ASCII banners and structured tables for optimal rendering in Copilot Chat.
* **Contextual Follow-ups**: Specialized tools handle session persistence and dependency tracking.

---

## 🔌 VS Code & Copilot Integration

AgileAgents is designed to live inside **GitHub Copilot Chat** via MCP.

1. **Start Server**: `uv run uvicorn jira_mcp_server.server:app --port 8000`
2. **VS Code Setup**:
* Configure `.vscode/mcp.json` to point to the bridge server.
* Run `Developer: Reload Window` in VS Code.
* Trust the `agileAgentsJira` server when prompted.


3. **Usage**: Ask Copilot in natural language:
* *"What is the status of SCC-2?"*
* *"Execute the daily in Spanish with Alice and Bob."*
* *"Move SCC-5 to 'In Progress' and assign it to me."*



---

## 💻 CLI Interface

For terminal-heavy workflows, use the interactive CLI chat:

```bash
uv run -m src.interfaces.cli --sm-assistant-chat --language en

```

**Example Commands:**

* `move SCC-1 to In Progress`
* `assign SCC-1 to John Doe`
* `update priority of SCC-5 to High`
* `comment SCC-1: waiting for QA approval`