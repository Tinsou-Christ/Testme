# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python Telegram bot ("Puru Code AI" / @PuruAI_bot) that integrates with an OpenAI-compatible API for chat completions. Includes E2B cloud sandboxes for secure code execution and a Flask web dashboard with live metrics.

## Running the Project

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python bot.py

# Run with Docker
docker build -t puru-ai-bot . && docker run -p 3000:3000 puru-ai-bot
```

The app starts a Flask dashboard on port 3000 and Telegram bot polling in the main thread. No test suite or linter is configured.

## Architecture

All source lives in the root directory (flat structure, 4 files):

- **bot.py** — Entry point. Telegram command handlers (`/start`, `/menu`, `/ai`, `/tools`, `/context`, `/compact`, `/clear`), Flask dashboard (`GET /` HTML page, `GET /health` JSON), and reconnection loop for Telegram polling. Tracks in-memory metrics (messages, users, command usage). Starts Flask in a daemon thread, then runs Telegram polling in the main thread with auto-reconnect on `Conflict`/`TelegramError`.
- **agent.py** — AI logic layer. Manages per-chat conversation history (in-memory `conversations` dict keyed by chat ID, max 30 messages). Three chat modes: `chat_with_tools()` (agentic tool-calling loop up to 50 iterations — **actively used by main handler**), `chat_stream()` (streaming text-only, no tools), `chat()` (sync, legacy). Handles context compaction via summarization and token estimation (`len(text) // 4`). Strips `<thought>...</thought>` blocks from all responses via `_strip_thinking()`.
- **sandbox.py** — E2B sandbox integration. Defines 5 tools: `bash` (60s timeout), `write_file`, `read_file`, `edit_file`, `send_file`. Each tool returns `(result_text, file_data)` where `file_data` is non-null only for `send_file` (returns `(filename, bytes, caption)`). Per-user sandbox instances stored in `sandboxes` dict. Auto-recreates dead sandboxes on tool failure (single retry).
- **config.py** — All configuration as module-level constants (API keys, model name, token limits, system prompt). No `.env` loading — values are hardcoded.

## Data Flow

User message → `bot.py` `handle_message` (or `_run_with_tools` via `/ai` command) → `agent.py` `chat_with_tools` → OpenAI-compatible API with tool definitions → tool calls dispatched to `sandbox.py` `execute_tool` → E2B sandbox → loop until final text response → Telegram reply.

Both DMs and group messages (when @mentioned or /command) flow through `_run_with_tools`, which sends a status message and edits it with progress updates as tool loops complete.

## Key Configuration (config.py)

| Constant | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI-compatible endpoint credentials |
| `MODEL_NAME` | Model identifier sent to API |
| `E2B_API_KEY` | E2B sandbox service key |
| `MAX_LOOPS` | Tool-calling iteration limit (50) |
| `TOKEN_WARN_LIMIT` / `TOKEN_COMPACT_LIMIT` | Token thresholds for warnings and auto-compaction (20k / 35k) |
| `SYSTEM_PROMPT` | System prompt for AI, includes current datetime injection in `chat_with_tools` |

## Important Patterns

- **Chat ID scoping**: Conversations and sandboxes are keyed by `update.effective_chat.id` (not user ID), so group chats share context and private chats are per-user.
- **Group chat filtering**: Non-command messages in groups are ignored unless they contain `@botusername`. The mention is stripped before passing to the AI.
- **Token warning**: Both `/ai` and regular message handlers check token usage before processing and warn if above `TOKEN_WARN_LIMIT`.
- **Markdown rendering**: Responses are sent with `parse_mode="Markdown"` where possible, falling back to plain text if parsing fails.
- **Tool call serialization**: OpenAI tool call objects are converted to plain dicts before appending to conversation history to keep it JSON-serializable.

## Dependencies

`python-telegram-bot` v21.3 (Telegram API), `openai` v1.35.0 (API client), `httpx` v0.27.2, `e2b` v1.0.4 (cloud sandboxes), `flask` v3.1.1 (dashboard), `psutil` v5.9.8 (system metrics).
