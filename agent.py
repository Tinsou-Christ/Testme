import json
import re
import time
import httpx
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME, SYSTEM_PROMPT, TOKEN_WARN_LIMIT, MAX_LOOPS
from sandbox import TOOLS, execute_tool, close_sandbox

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

conversations: dict[int, list[dict]] = {}
MAX_HISTORY = 30

# Strip <thought>...</thought> blocks from AI responses
_THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    """Remove <thought>...</thought> blocks, return only the visible answer."""
    if not text:
        return text
    return _THOUGHT_RE.sub("", text).strip()


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def get_token_count(user_id: int) -> int:
    if user_id not in conversations:
        return 0
    return sum(_estimate_tokens(m.get("content", "") or "") for m in conversations[user_id])


def get_context_info(user_id: int) -> dict:
    if user_id not in conversations:
        return {"tokens": 0, "messages": 0, "warn": False}
    history = conversations[user_id]
    tokens = get_token_count(user_id)
    return {
        "tokens": tokens,
        "messages": len([m for m in history if m["role"] == "user"]),
        "warn": tokens >= TOKEN_WARN_LIMIT,
    }


COMPACT_API_URL = "https://puruboy-api.vercel.app/api/ai/gemini-v2"
COMPACT_MAX_RETRIES = 5


def _call_compact_api(prompt: str) -> str | None:
    """Call Gemini compact API with exponential backoff. Returns answer or None on failure."""
    for attempt in range(COMPACT_MAX_RETRIES):
        try:
            resp = httpx.post(
                COMPACT_API_URL,
                json={"prompt": prompt},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") and data.get("result", {}).get("answer"):
                return data["result"]["answer"]
            return None
        except Exception:
            if attempt < COMPACT_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    return None


def compact_history(user_id: int) -> str:
    if user_id not in conversations or len(conversations[user_id]) <= 1:
        return "No conversation to compact."

    history = conversations[user_id]
    user_msgs = [m for m in history[1:] if m["role"] == "user"]
    full_text = "\n".join(f"User: {m['content']}" for m in user_msgs)

    summary_prompt = (
        "Ringkas percakapan berikut menjadi paragraf singkat yang mencakup "
        "poin-poin penting, topik utama, dan tujuan user. "
        "Jangan lewatkan detail teknis atau kode yang dibahas. "
        "Gunakan format poin-poin:\n\n"
        f"{full_text}"
    )

    summary = _call_compact_api(summary_prompt)
    if summary:
        conversations[user_id] = [
            history[0],
            {"role": "user", "content": f"[Conversation Summary]\n{summary}"},
            {"role": "assistant", "content": "Context compacted. Continuing from summary."},
        ]
        new_tokens = get_token_count(user_id)
        return f"Context compacted!\nBefore: {len(history)-1} messages\nAfter: 2 messages\nTokens: ~{new_tokens}"
    return "Compact failed: Gemini API unavailable after retries."


def chat_with_tools(user_id: int, message: str, on_loop=None):
    """Generator that yields (text, is_done, loop_count) tuples.
    on_loop is called with loop_count when a tool call is processed."""
    if user_id not in conversations:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_with_date = f"{SYSTEM_PROMPT}\n\nCurrent date and time: {now} (UTC+0 / use local timezone if specified)."
        conversations[user_id] = [{"role": "system", "content": system_with_date}]

    history = conversations[user_id]
    history.append({"role": "user", "content": message})

    if len(history) > MAX_HISTORY + 1:
        history[:] = [history[0]] + history[-(MAX_HISTORY):]

    loop_count = 0

    while loop_count < MAX_LOOPS:
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=history,
                tools=TOOLS,
                max_tokens=2000,
                temperature=0.7,
            )
        except Exception as e:
            yield f"Error: {str(e)}", True, loop_count, []
            return

        choice = response.choices[0]

        # If no tool calls, return the text response
        if not choice.message.tool_calls:
            reply = _strip_thinking(choice.message.content or "")
            history.append({"role": "assistant", "content": reply})
            yield reply, True, loop_count, []
            return

        # Process tool calls — convert to dict so history stays serializable
        tool_calls_data = []
        for tc in choice.message.tool_calls:
            tool_calls_data.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
        history.append({
            "role": "assistant",
            "content": choice.message.content or "",
            "tool_calls": tool_calls_data,
        })
        tool_results = []
        pending_files = []

        for tool_call in choice.message.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            result_text, file_data = execute_tool(user_id, func_name, func_args)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text,
            })
            if file_data:
                pending_files.append(file_data)

        history.extend(tool_results)
        loop_count += 1

        if on_loop:
            on_loop(loop_count)

        # Yield progress with optional files
        yield f"Processing... ({loop_count}/{MAX_LOOPS})", False, loop_count, pending_files

    # Max loops reached
    reply = "Error: Max tool call loops (50) reached."
    history.append({"role": "assistant", "content": reply})
    yield reply, True, loop_count, []


def chat_stream(user_id: int, message: str):
    """Streaming text-only chat (no tools)."""
    if user_id not in conversations:
        conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    history = conversations[user_id]
    history.append({"role": "user", "content": message})

    if len(history) > MAX_HISTORY + 1:
        history[:] = [history[0]] + history[-(MAX_HISTORY):]

    try:
        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=history,
            max_tokens=2000,
            temperature=0.7,
            stream=True,
        )
        full_reply = ""
        reasoning_parts = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # Collect reasoning tokens (some models return text here)
            if hasattr(delta, "reasoning") and delta.reasoning:
                reasoning_parts.append(delta.reasoning)
            # Collect content tokens
            if delta.content:
                full_reply += delta.content
                yield delta.content, False
        # Fallback: if content is empty, use reasoning text
        if not full_reply and reasoning_parts:
            full_reply = "".join(reasoning_parts)
        full_reply = _strip_thinking(full_reply)
        if full_reply:
            yield full_reply, False
        history.append({"role": "assistant", "content": full_reply})
        yield full_reply, True
    except Exception as e:
        yield f"Error: {str(e)}", True


def chat(user_id: int, message: str) -> str:
    if user_id not in conversations:
        conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    history = conversations[user_id]
    history.append({"role": "user", "content": message})

    if len(history) > MAX_HISTORY + 1:
        history[:] = [history[0]] + history[-(MAX_HISTORY):]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=history,
            max_tokens=2000,
            temperature=0.7,
        )
        reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"Error: {str(e)}"


def clear_history(user_id: int) -> None:
    conversations.pop(user_id, None)
    close_sandbox(user_id)
