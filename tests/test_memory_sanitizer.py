import unittest

from brain.memory_sanitizer import sanitize_structure_for_memory, sanitize_text_for_memory, summarize_safe_tool_outcome


class MemorySanitizerTests(unittest.TestCase):
    def test_sanitize_text_redacts_secret_like_material(self):
        text = "Authorization: Bearer abcdef123456\napi_key: sk-secretvalue\nsafe line"

        sanitized = sanitize_text_for_memory(text)

        self.assertIn("Authorization: [REDACTED]", sanitized)
        self.assertIn("api_key: [REDACTED]", sanitized)
        self.assertIn("safe line", sanitized)

    def test_sanitize_structure_drops_sensitive_keys(self):
        payload = {
            "token": "abcdef",
            "headers": {"Authorization": "Bearer secret"},
            "message_id": "123",
        }

        sanitized = sanitize_structure_for_memory(payload)

        self.assertNotIn("token", sanitized)
        self.assertIn("message_id", sanitized)

    def test_summarize_safe_tool_outcome_produces_prompt_safe_summary(self):
        result = summarize_safe_tool_outcome(
            tool_name="GMAIL_SEND_EMAIL",
            toolkit="GMAIL",
            status="success",
            verified=True,
            output={"Authorization": "Bearer secret", "message_id": "123"},
        )

        self.assertIn("Verified GMAIL_SEND_EMAIL action succeeded", result["summary"])
        self.assertNotIn("Authorization", str(result["safe_output"]))
