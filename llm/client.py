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
