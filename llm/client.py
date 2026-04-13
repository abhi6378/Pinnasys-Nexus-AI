"""
llm/client.py  —  All LLM calls go through here (GPT-4o-mini)
"""
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o-mini"


def generate(prompt: str, system_prompt: str = "You are a helpful AI assistant.",
             temperature: float = 0.7, max_tokens: int = 1500) -> str:
    """
    Core LLM call. Returns plain string response.
    All agents call this function — never call OpenAI directly from elsewhere.
    """
    response = _client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()


def generate_json(prompt: str, system_prompt: str = "") -> str:
    """
    LLM call that forces JSON output mode.
    Caller is responsible for json.loads() on the result.
    """
    sys = system_prompt or (
        "You are a helpful assistant. Always respond with valid JSON only. "
        "No markdown fences, no explanation, just the JSON object."
    )
    response = _client.chat.completions.create(
        model=MODEL,
        temperature=0.3,
        max_tokens=1500,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": sys},
            {"role": "user",   "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()


def generate_with_tool_awareness(
    prompt: str,
    system_prompt: str,
    available_tools: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> str:
    """
    LLM call that injects available tool descriptions into the system prompt
    and asks the model to respond with either:
      - Normal text (when no tool is needed)
      - A JSON block containing a tool_call (when a tool should be used)

    The response is a raw string. The caller parses it to detect tool_call JSON.

    This avoids OpenAI's native function-calling API (which requires a different
    response format) and works with any model that can produce JSON when asked.

    Parameters:
        prompt          — User's message
        system_prompt   — Agent's base system prompt
        available_tools — List of tool registry entries [{"tool_name", "action", ...}]
        temperature     — LLM temperature
        max_tokens      — Max response tokens

    Returns:
        Raw LLM response string.
    """
    # Build tool catalog for the prompt
    if available_tools:
        tool_lines = []
        for t in available_tools:
            tool_lines.append(
                f"  - {t['tool_name']}: {t['action']} (toolkit: {t['toolkit']})"
            )
        tool_catalog = "\n".join(tool_lines)

        tool_instruction = f"""

--- AVAILABLE TOOLS ---
You have access to the following tools:
{tool_catalog}

IMPORTANT RULES FOR TOOL USE:
- If the user's request can be answered from your knowledge alone, respond normally with text.
- If the user's request requires executing an external action (sending email, creating event, etc.), respond with ONLY a JSON block like this:
```json
{{"message": "Brief explanation of what you're doing", "tool_call": {{"name": "TOOL_NAME_FROM_LIST", "params": {{"param1": "value1"}}}}}}
```
- Only use tool names from the list above. Do not invent tools.
- If unsure whether to use a tool, respond with normal text instead.
- Never use a tool for something you can answer directly.
--- END TOOLS ---
"""
        full_system = system_prompt + tool_instruction
    else:
        full_system = system_prompt

    response = _client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": full_system},
            {"role": "user",   "content": prompt},
        ],
    )
    return response.choices[0].message.content.strip()
