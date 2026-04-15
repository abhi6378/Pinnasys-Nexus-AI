"""
llm/client.py  —  Shared OpenAI client helpers for text, JSON, and tool-aware calls.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from tools.tool_registry import build_prompt_tool_catalog, list_toolkit_labels_for_tools

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

try:
    from openai import APIConnectionError, OpenAI, RateLimitError
    _openai_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-specific import issue
    APIConnectionError = RuntimeError
    RateLimitError = RuntimeError
    OpenAI = None
    _openai_import_error = exc

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
    _tenacity_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-specific import issue
    retry = None
    retry_if_exception_type = None
    stop_after_attempt = None
    wait_exponential = None
    _tenacity_import_error = exc

_client: Any | None = None


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Drop None fields so OpenAI receives a clean message payload."""
    return {key: value for key, value in message.items() if value is not None}


def _build_messages(
    system_prompt: str,
    prompt: str,
    history: list[dict] | None = None,
    messages: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Build a chat message list.

    When ``messages`` is provided, it is treated as the full chat state and the
    supplied system prompt is ensured to be present at the front.
    """
    if messages is not None:
        normalized = [_normalize_message(message) for message in messages]
        if normalized and normalized[0].get("role") == "system":
            normalized[0]["content"] = system_prompt
            return normalized
        return [{"role": "system", "content": system_prompt}, *normalized]

    payload: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history:
        payload.extend(_normalize_message(message) for message in history[-10:])
    payload.append({"role": "user", "content": prompt})
    return payload


def _get_client() -> Any:
    """Create the OpenAI client lazily so module import remains lightweight."""
    global _client, _openai_import_error

    if _client is not None:
        return _client
    if OpenAI is None:
        raise RuntimeError(f"OpenAI client import failed: {_openai_import_error}")

    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _call_openai_once(
    model: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, Any]],
    **kwargs: Any,
):
    """Execute a single chat completion call."""
    client = _get_client()
    return client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=messages,
        **kwargs,
    )


if retry and retry_if_exception_type and stop_after_attempt and wait_exponential:

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_openai(
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ):
        """Execute a chat completion call with tenacity retry/backoff."""
        return _call_openai_once(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
            **kwargs,
        )

else:

    def _call_openai(
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ):
        """Fallback when tenacity cannot be imported in the current environment."""
        if _tenacity_import_error:
            logger.warning("Tenacity unavailable, calling OpenAI without retry: %s", _tenacity_import_error)
        return _call_openai_once(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
            **kwargs,
        )


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    """Convert an OpenAI tool call object into a plain dict for reuse."""
    function = getattr(tool_call, "function", None)
    return {
        "id": getattr(tool_call, "id", ""),
        "type": getattr(tool_call, "type", "function"),
        "function": {
            "name": getattr(function, "name", ""),
            "arguments": getattr(function, "arguments", "{}"),
        },
    }


def _normalize_tool_schemas(tool_schemas: list[dict] | None) -> list[dict]:
    """Coerce tool schemas into the OpenAI ``tools`` format."""
    normalized: list[dict] = []
    for schema in tool_schemas or []:
        if not isinstance(schema, dict):
            continue
        if schema.get("type") == "function" and isinstance(schema.get("function"), dict):
            normalized.append(schema)
            continue
        if "function" in schema and isinstance(schema["function"], dict):
            normalized.append({"type": "function", "function": schema["function"]})
            continue
        if schema.get("name") and schema.get("parameters"):
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema.get("description", ""),
                        "parameters": schema["parameters"],
                    },
                }
            )
    return normalized


def generate(
    prompt: str,
    system_prompt: str = "You are a helpful AI assistant.",
    temperature: float = 0.7,
    max_tokens: int = 1500,
    history: list[dict] | None = None,
    messages: list[dict] | None = None,
) -> str:
    """
    Core LLM call. Returns plain string response.
    All agents call this function — never call OpenAI directly from elsewhere.
    """
    response = _call_openai(
        model=MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=_build_messages(system_prompt, prompt, history=history, messages=messages),
    )
    return (response.choices[0].message.content or "").strip()


def generate_json(
    prompt: str,
    system_prompt: str = "",
    history: list[dict] | None = None,
    messages: list[dict] | None = None,
) -> str:
    """
    LLM call that forces JSON output mode.
    Caller is responsible for json.loads() on the result.
    """
    sys = system_prompt or (
        "You are a helpful assistant. Always respond with valid JSON only. "
        "No markdown fences, no explanation, just the JSON object."
    )
    response = _call_openai(
        model=MODEL,
        temperature=0.3,
        max_tokens=1500,
        response_format={"type": "json_object"},
        messages=_build_messages(sys, prompt, history=history, messages=messages),
    )
    return (response.choices[0].message.content or "").strip()


def generate_with_tool_awareness(
    prompt: str,
    system_prompt: str,
    available_tools: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    composio_tool_schemas: list[dict] | None = None,
    history: list[dict] | None = None,
    messages: list[dict] | None = None,
) -> str | dict:
    """
    If ``composio_tool_schemas`` is provided, use OpenAI native function calling.
    Otherwise fall back to the legacy prompt-injection approach.

    Returns:
      - ``str`` when the model responds with plain text
      - ``dict`` with a ``tool_call`` payload when a tool call is requested
    """
    native_tools = _normalize_tool_schemas(composio_tool_schemas)
    message_payload = _build_messages(
        system_prompt,
        prompt,
        history=history,
        messages=messages,
    )

    if native_tools:
        response = _call_openai(
            model=MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=message_payload,
            tools=native_tools,
        )
        choice = response.choices[0]
        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            arguments_raw = getattr(tool_call.function, "arguments", "{}") or "{}"
            try:
                tool_params = json.loads(arguments_raw)
            except json.JSONDecodeError:
                logger.warning("Tool call returned non-JSON arguments for %s", tool_call.function.name)
                tool_params = {}
            return {
                "tool_call": {
                    "name": tool_call.function.name,
                    "params": tool_params,
                },
                "message": (choice.message.content or "").strip(),
                "tool_call_id": getattr(tool_call, "id", ""),
                "openai_tool_call": _serialize_tool_call(tool_call),
            }
        return (choice.message.content or "").strip()

    if available_tools:
        tool_catalog = build_prompt_tool_catalog(available_tools)
        toolkit_labels = list_toolkit_labels_for_tools(available_tools)
        toolkit_phrase = ", ".join(toolkit_labels) if toolkit_labels else "the listed systems"
        tool_instruction = f"""

--- AVAILABLE TOOLS ---
You have access to the following tools:
{tool_catalog}

RULES FOR TOOL USAGE:
1. Use a normal text response only when the request can be fully satisfied
   without touching an external system.
2. Tasks you can answer from your own knowledge — writing, advising,
   explaining, drafting, strategizing — MUST be answered with plain text.
   Do NOT call a tool for these.
3. If the user explicitly asks you to access or change data in {toolkit_phrase},
   and a listed tool can do it, you MUST either:
   - return a tool_call JSON for the next real tool step, or
   - ask a short plain-text clarification question for missing information.
   Never pretend that you already fetched, posted, sent, or updated anything.
4. When you DO need a tool, respond with ONLY a JSON block:
```json
{{"message": "Brief explanation of what you are doing", "tool_call": {{"name": "TOOL_NAME_FROM_LIST_ABOVE", "params": {{"param1": "value1"}}}}}}
```
5. Use ONLY tool names from the list above. Do NOT invent or guess tool names.
6. Include all required parameters you know in the params object.
7. NEVER call the same tool twice with identical parameters.
8. After receiving a tool result, you may either:
   - return another tool_call JSON if another listed tool is still needed, or
   - return the final plain-text answer.
--- END TOOLS ---
"""
        full_system_prompt = f"{system_prompt}{tool_instruction}"
        message_payload = _build_messages(
            full_system_prompt,
            prompt,
            history=history,
            messages=messages,
        )

    response = _call_openai(
        model=MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=message_payload,
    )
    return (response.choices[0].message.content or "").strip()
