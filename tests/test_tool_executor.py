import unittest

from tests.support import (
    FakeQuery,
    FakeSession,
    Spy,
    import_fresh,
    make_model_class,
    make_module,
    make_sqlalchemy_stubs,
    patch_attr,
)


def load_tool_executor_module():
    ToolCallLogModel = make_model_class(
        "ToolCallLogModel",
        ["workspace_id", "agent_key", "tool_name", "toolkit", "status"],
    )
    ToolConnectionModel = make_model_class(
        "ToolConnectionModel",
        ["workspace_id", "toolkit", "status"],
    )
    PendingToolRequestModel = make_model_class(
        "PendingToolRequestModel",
        ["workspace_id", "agent_key", "original_input", "requested_tool", "requested_toolkit", "status", "resume_token"],
    )
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["tools.tool_registry"] = make_module(
        "tools.tool_registry",
        get_tool=lambda tool_name: None,
        is_agent_allowed=lambda tool_name, agent_key: True,
    )
    stubs["tools.composio_client"] = make_module(
        "tools.composio_client",
        is_available=lambda: False,
        check_connection=lambda *args, **kwargs: {},
        get_connect_link=lambda *args, **kwargs: None,
        get_toolkit_auth_details=lambda *args, **kwargs: {},
        validate_tool_slug=lambda *args, **kwargs: {"available": False, "exists": False, "error": "unavailable"},
        execute_tool=lambda *args, **kwargs: {"data": {}, "error": None},
    )
    stubs["models.tool_call_logs"] = make_module(
        "models.tool_call_logs",
        ToolCallLogModel=ToolCallLogModel,
    )
    stubs["models.tool_connections"] = make_module(
        "models.tool_connections",
        ToolConnectionModel=ToolConnectionModel,
    )
    stubs["models.pending_tool_requests"] = make_module(
        "models.pending_tool_requests",
        PendingToolRequestModel=PendingToolRequestModel,
    )
    return import_fresh("tools.tool_executor", stubs)


class AttemptToolCallTests(unittest.TestCase):
    def setUp(self):
        self.tool_executor = load_tool_executor_module()

    def test_attempt_tool_call_returns_invalid_tool_for_unknown_tool(self):
        db = FakeSession()
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=None)):
            result = self.tool_executor.attempt_tool_call("MISSING", "assistant", "ws1", db)

        self.assertEqual(result["status"], "invalid_tool")
        self.assertEqual(db.added[0].status, "invalid_tool")

    def test_attempt_tool_call_returns_validation_error_when_agent_is_not_allowed(self):
        db = FakeSession()
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=False)):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "validation_error")
        self.assertEqual(db.added[0].status, "validation_error")

    def test_attempt_tool_call_applies_aliases_before_missing_param_validation(self):
        db = FakeSession()
        tool_entry = {
            "toolkit": "GMAIL",
            "expected_params": ["recipient_email", "body"],
            "param_aliases": {"to": "recipient_email"},
            "requires_auth": True,
        }
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=False)):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                input_args={"to": "user@example.com"},
            )

        self.assertEqual(result["status"], "validation_error")
        self.assertEqual(db.added[0].input_json["recipient_email"], "user@example.com")

    def test_attempt_tool_call_returns_connect_required_and_persists_pending_request(self):
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=None),
            self.tool_executor.PendingToolRequestModel: FakeQuery(first_result=None),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", Spy(return_value={"connected": False, "status": "not_found", "error": None})), \
             patch_attr(self.tool_executor, "get_connect_link", Spy(return_value="https://connect.example")), \
             patch_attr(self.tool_executor, "get_toolkit_auth_details", Spy(return_value={})):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                original_input="send it",
            )

        self.assertEqual(result["status"], "connect_required")
        self.assertTrue(result["resume_token"])
        self.assertTrue(any(hasattr(obj, "requested_tool") for obj in db.added))

    def test_attempt_tool_call_returns_auth_unavailable_when_no_connect_link_can_be_built(self):
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=None),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=False)), \
             patch_attr(self.tool_executor, "get_connect_link", Spy(return_value=None)):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "auth_unavailable")
        self.assertEqual(result["resume_token"], "")

    def test_attempt_tool_call_returns_success_for_connected_tool_execution(self):
        existing_connection = self.tool_executor.ToolConnectionModel(
            workspace_id="ws1",
            toolkit="GMAIL",
            status="connected",
            connected_account_id="acct-1",
        )
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=existing_connection),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", Spy(return_value={"connected": True, "connected_account_id": "acct-1", "status": "connected", "error": None})), \
             patch_attr(self.tool_executor, "execute_tool", Spy(return_value={"data": {"ok": True}, "error": None})):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], {"ok": True})

    def test_attempt_tool_call_returns_failure_when_executor_reports_error(self):
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=None),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", Spy(return_value={"connected": True, "connected_account_id": "acct-1", "status": "connected", "error": None})), \
             patch_attr(self.tool_executor, "execute_tool", Spy(return_value={"data": {"ok": False}, "error": "boom"})):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["error"], "boom")

    def test_attempt_tool_call_logs_execution_exceptions_and_returns_failure(self):
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=None),
        })
        log_exception = Spy()
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", Spy(return_value={"connected": True, "connected_account_id": "acct-1", "status": "connected", "error": None})), \
             patch_attr(self.tool_executor, "execute_tool", Spy(side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("explode")))), \
             patch_attr(self.tool_executor, "log_exception", log_exception):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["error"], "explode")
        self.assertEqual(len(log_exception.calls), 1)
        self.assertEqual(log_exception.calls[0][0][1], "tool.execute.failed")
