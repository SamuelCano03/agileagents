# AgileAgents

Multi-agent AI for Scrum / Agile simulations and meeting automation.

## Hybrid mode (Copilot + GitHub Models)

This project supports a hybrid approach:

- Use GitHub Copilot in VS Code with MCP tools for the best in-editor experience.
- Optionally enable local LLM replies in agents (e.g. Scrum Master) via GitHub Models.

### Environment variables for local LLM replies

Set these variables in your local `.env` (do not commit secrets):

- `LLM_MODE=github_models` to enable LLM-backed agent responses.
- `GITHUB_MODEL=<model-name>` e.g. `gpt-4.1-mini`.
- `GITHUB_TOKEN=<token>` with access to your models endpoint.
- `GITHUB_MODELS_ENDPOINT=<chat-completions-endpoint>`
	- default used by the code: `https://models.inference.ai.azure.com/chat/completions`
- Optional tuning:
	- `LLM_TEMPERATURE=0.2`
	- `LLM_MAX_TOKENS=500`

If `LLM_MODE` is not set (or set to `none`), the app falls back to template-based responses.

## Jira MCP write tools (create/seed backlog)

The Jira MCP-style server now exposes these tools:

- `jira.test_connection`
- `jira.create_issue`
- `jira.seed_sample_backlog`

### Safe write mode

Write operations are protected by default. To allow issue creation, set:

- `JIRA_ALLOW_WRITES=true`

in the environment where the MCP Jira server is running, then restart the server.

If not enabled, write tools return HTTP 403.

### Quick verification flow

1. Start the MCP Jira server.
2. Call `jira.test_connection` to verify auth/project access.
3. Call `jira.seed_sample_backlog` with a small count (e.g. 2) to create a demo backlog when needed.

## Scrum Master assistant (Phase 1 + Phase 2)

Two-phase Jira writes are available through MCP tools:

- `scrum_master_assistant.plan_action` (no write, returns `plan_id` + preview)
- `scrum_master_assistant.apply_action` (executes write only with explicit confirmation)
- `scrum_master_assistant.handle_request` (natural language -> planned action)

### Supported actions

- `create_issue`
- `comment_issue`
- `transition_issue`
- `assign_issue`
- `update_priority`
- `edit_issue`
- `create_subtask`
- `move_to_active_sprint`

### Confirmation contract

`apply_action` requires:

- `confirm=true`
- `confirmation_text="CONFIRM"`

### Audit log

All plan/apply events are appended to:

- `logs/sm_assistant_audit.jsonl`

You can override path via env var:

- `JIRA_ASSISTANT_AUDIT_LOG=<path>`

### Natural language from CLI (no scripts)

You can run an interactive chat mode that turns human requests into a planned action,
shows preview, and asks for confirmation before applying:

`uv run -m src.interfaces.cli --sm-assistant-chat --language es`

Examples you can type inside chat:

- `mueve SCC-1 a In Progress`
- `asigna SCC-1 a Samuel Esteban Cano Chocce`
- `actualiza prioridad de SCC-1 a High`
- `crear issue: Revisar dependencias para integración de pagos`
- `comentario SCC-1: tengo dependencia con permisos de GitHub`
- `dame la descripción de SCC-2`

Read-only requests (like issue details) are executed immediately and do not require confirmation.

### Current UX behavior

- Read requests now return only the requested fields (for example, status only), avoiding noisy `None` values.
- CLI chat renders read responses in natural language text instead of raw JSON dictionaries.
- Write requests still use plan + explicit confirmation before apply.

## Using Copilot Chat in VS Code (natural language)

This repo now includes a workspace MCP configuration for VS Code Copilot Chat.

Included files:

- `.vscode/mcp.json` -> registers an MCP stdio server named `agileAgentsJira`
- `.vscode/settings.json` -> enables `chat.mcp.autostart`
- `src/mcp/vscode_bridge_server.py` -> native MCP bridge that forwards tool calls to `jira_mcp_server.server`

Recommended flow:

1. Start MCP server: `JIRA_ALLOW_WRITES=true uv run uvicorn jira_mcp_server.server:app --reload --port 8000`
2. Reopen VS Code window (or run `Developer: Reload Window`) so MCP config is loaded.
3. Open Copilot Chat in VS Code.
4. If prompted, trust/start server `agileAgentsJira`.
5. Ask in natural language, for example:
	- "Dame el estado de SCC-2"
	- "Dame los story points de SCC-2"
	- "Actualiza el estado de SCC-2 a In Progress"
6. For write actions, Copilot should first propose/confirm and then apply.

Suggested 3-prompt validation in Copilot Chat:

1. Read: `Dame el estado de SCC-2`
2. Plan write: `Quiero mover SCC-2 a In Progress. Dame primero el plan y no apliques nada sin mi confirmación.`
3. Apply: `Confirma y aplica el plan anterior con CONFIRM.`

### Daily from Copilot Chat (no script)

The MCP bridge now includes a `daily_run` tool so you can execute the daily directly from Copilot Chat.
For end-user UX, prefer `daily_present` and `daily_followup_present` (markdown-first responses).

When the daily starts, it returns a markdown ASCII banner (`daily_banner_markdown`) designed for a nicer chat UI.

Example prompts:

- `Ejecuta la daily en español con Alice y Bob.`
- `Run a daily in English with Alice, Bob, and Carol.`
- `Ejecuta la daily con Alice y Bob e incluye una actualización de Samuel: "Ayer avancé SCC-2 y hoy bloqueo por permisos".`

Recommended clean flow in Copilot Chat:

1. `daily_present(language="es", members=["Alice","Bob"], main_member="Samuel Esteban Cano Chocce")`
2. Copilot shows `assistant_message_markdown` directly.
3. User replies update in natural language.
4. `daily_followup_present(session_id=..., user_reply="...")`
5. Repeat step 4 until closed (`listo`).

If you provide `main_member` but omit `main_member_update`, the flow behaves like the script version:

1. `daily_run` returns the main member's Jira task list in markdown + asks for update (`ayer/hoy/bloqueos`).
2. Call `daily_followup` with the user update.
3. Scrum Master gives feedback and asks a dependency follow-up.
4. Call `daily_followup` again with that answer.
5. Session remains open for extra questions/requests (Scrum concepts, dependencies, Jira read/write), using `daily_followup` repeatedly until the user says `listo`/`done`.

Copilot Chat rendering hint:

- `daily_run` and `daily_followup` now return `assistant_message_markdown`.
- For best UX, render that field directly (it includes ASCII banner, task list, prompts, and responses in markdown format).

`daily_run` returns:

- `summary`: short natural summary
- `transcript`: ordered list of interventions (`speaker`, `content`)
- `main_member_block`: optional acknowledgment block when you provide main member update
- `follow_up`: optional follow-up question with `session_id` to continue via `daily_followup`

### Main member follow-up (Copilot Chat)

When you provide `main_member` and `main_member_update`, the daily can return a follow-up question from Scrum Master.

Use the returned `follow_up.session_id` with `daily_followup` and the human reply to close that interaction.

Typical flow in chat:

1. Run daily with main member update.
2. Copilot shows Scrum Master follow-up question.
3. User replies with dependency/risk details.
4. Copilot calls `daily_followup` and returns Scrum Master guidance + next action.
5. Optionally keep calling `daily_followup` for additional requests (including Jira changes via plan/apply).

If your local Copilot setup still does not invoke MCP tools directly, keep using the CLI chat mode as fallback while wiring your VS Code MCP tool registration.
