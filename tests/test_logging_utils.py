import json
import logging
import unittest

from utils.logging_utils import log_event, request_context


class _CaptureLogger:
    def __init__(self):
        self.calls = []

    def log(self, level, message, exc_info=False):
        self.calls.append((level, message, exc_info))


class _BrokenLogger:
    def log(self, level, message, exc_info=False):
        raise RuntimeError("logger failed")


class LoggingUtilsTests(unittest.TestCase):
    def test_log_event_includes_request_context_and_redacts_sensitive_fields(self):
        logger = _CaptureLogger()
        with request_context(request_id="req-1", workspace_id="ws-1"):
            log_event(
                logger,
                logging.INFO,
                "tool.execute.start",
                tool_name="GMAIL_SEND_EMAIL",
                connect_url="https://secret.example",
            )

        self.assertEqual(len(logger.calls), 1)
        payload = json.loads(logger.calls[0][1])
        self.assertEqual(payload["event"], "tool.execute.start")
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["tool_name"], "GMAIL_SEND_EMAIL")
        self.assertEqual(payload["connect_url"], "[redacted]")

    def test_log_event_swallows_logger_failures(self):
        with request_context(request_id="req-1", workspace_id="ws-1"):
            log_event(_BrokenLogger(), logging.INFO, "request.start", agent_name="assistant")
