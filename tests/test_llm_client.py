import unittest

import llm.client as llm_client


class LlmClientTests(unittest.TestCase):
    def test_build_messages_compacts_large_explicit_message_list(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "A" * 12000},
            {"role": "tool", "tool_call_id": "call-1", "content": "B" * 12000},
            {"role": "user", "content": "check my inbox"},
        ]

        payload = llm_client._build_messages("system override", "ignored", messages=messages)

        self.assertEqual(payload[0]["role"], "system")
        self.assertEqual(payload[0]["content"], "system override")
        total_chars = sum(len(message.get("content", "") or "") for message in payload if isinstance(message.get("content"), str))
        self.assertLessEqual(total_chars, llm_client.MAX_TOTAL_MESSAGE_CHARS + len("system override"))
        for message in payload[1:]:
            if isinstance(message.get("content"), str):
                self.assertLessEqual(len(message["content"]), llm_client.MAX_MESSAGE_CHARS)

    def test_build_messages_compacts_history(self):
        history = [{"role": "assistant", "content": "X" * 6000} for _ in range(12)]

        payload = llm_client._build_messages("sys", "hello", history=history)

        self.assertEqual(payload[0]["role"], "system")
        self.assertEqual(payload[-1]["role"], "user")
        assistant_messages = [message for message in payload if message.get("role") == "assistant"]
        self.assertLessEqual(len(assistant_messages), llm_client.MAX_HISTORY_MESSAGES)
        for message in assistant_messages:
            self.assertLessEqual(len(message.get("content", "")), llm_client.MAX_MESSAGE_CHARS)
