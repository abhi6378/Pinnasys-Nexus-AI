from __future__ import annotations

import re
from typing import Any


SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|auth_header|api[_-]?key|credential|cookie|session|oauth)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(bearer\s+[a-z0-9._\-]+|sk-[a-z0-9]{12,}|xox[baprs]-[a-z0-9\-]+|-----begin)",
    re.IGNORECASE,
)
TOKEN_LIKE_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{24,}$")


def sanitize_text_for_memory(text: str | None, *, max_length: int = 1200) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    redacted_lines: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if SENSITIVE_KEY_PATTERN.search(line) and ":" in line:
            key = line.split(":", 1)[0].strip()
            redacted_lines.append(f"{key}: [REDACTED]")
            continue
        line = SENSITIVE_VALUE_PATTERN.sub("[REDACTED]", line)
        if TOKEN_LIKE_PATTERN.match(line):
            line = "[REDACTED]"
        redacted_lines.append(line)

    sanitized = "\n".join(redacted_lines)
    if len(sanitized) <= max_length:
        return sanitized
    return sanitized[:max_length].rstrip() + "..."


def sanitize_structure_for_memory(value: Any, *, max_items: int = 20) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text_for_memory(value)
    if isinstance(value, list):
        return [sanitize_structure_for_memory(item, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, tuple):
        return [sanitize_structure_for_memory(item, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                break
            key_text = str(key)
            if SENSITIVE_KEY_PATTERN.search(key_text):
                continue
            sanitized[key_text] = sanitize_structure_for_memory(item, max_items=max_items)
        return sanitized
    return sanitize_text_for_memory(str(value))


def summarize_safe_tool_outcome(
    *,
    tool_name: str = "",
    toolkit: str = "",
    status: str = "success",
    verified: bool = True,
    output: Any = None,
    error: str = "",
) -> dict[str, Any] | None:
    if not tool_name:
        return None

    safe_output = sanitize_structure_for_memory(output)
    safe_error = sanitize_text_for_memory(error, max_length=300)
    toolkit_label = toolkit or "external system"
    normalized_status = str(status or "success").lower()

    if normalized_status == "success" and verified:
        summary = f"Verified {tool_name} action succeeded in {toolkit_label}."
        if isinstance(safe_output, dict):
            interesting_keys = ", ".join(list(safe_output.keys())[:5])
            if interesting_keys:
                summary += f" Safe output fields: {interesting_keys}."
    else:
        summary = f"{tool_name} in {toolkit_label} returned status={normalized_status}."
        if safe_error:
            summary += f" Reason: {safe_error}"

    return {
        "summary": sanitize_text_for_memory(summary, max_length=400),
        "safe_output": safe_output,
        "safe_error": safe_error,
    }
