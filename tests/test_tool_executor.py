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
        ["workspace_id", "agent_key", "tool_name", "toolkit", "status", "idempotency_key", "pending_kind", "approval_required"],
    )
    ToolConnectionModel = make_model_class(
        "ToolConnectionModel",
        [
            "workspace_id",
            "toolkit",
            "status",
            "connected_account_id",
            "updated_at",
            "last_verified_at",
            "is_default",
            "account_label",
            "last_seen_remote_at",
            "revoked_at",
            "status_reason",
        ],
    )
    PendingToolRequestModel = make_model_class(
        "PendingToolRequestModel",
        [
            "workspace_id",
            "agent_key",
            "original_input",
            "requested_tool",
            "requested_toolkit",
            "status",
            "resume_token",
            "pending_kind",
            "idempotency_key",
            "approval_requirement_json",
            "approved",
            "approved_at",
            "context_json",
            "conversation_id",
            "id",
        ],
    )
    ToolIdempotencyRecordModel = make_model_class(
        "ToolIdempotencyRecordModel",
        ["workspace_id", "tool_name", "idempotency_key", "status", "pending_request_id", "input_hash", "output_json", "id"],
    )
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["tools.tool_registry"] = make_module(
        "tools.tool_registry",
        get_tool=lambda tool_name: None,
        get_tool_approval_requirement=lambda tool_name: type(
            "ApprovalRequirement",
            (),
            {
                "required": False,
                "risk_level": "low",
                "reason": "",
                "mode": "auto",
                "to_dict": lambda self: {
                    "required": False,
                    "risk_level": "low",
                    "reason": "",
                    "categories": [],
                    "mode": "auto",
                },
            },
        )(),
        get_tool_idempotency_fields=lambda tool_name: (),
        is_agent_allowed=lambda tool_name, agent_key: True,
    )
    stubs["tools.composio_client"] = make_module(
        "tools.composio_client",
        is_available=lambda: False,
        check_connection=lambda *args, **kwargs: {},
        get_connect_link=lambda *args, **kwargs: None,
        get_live_tool_schema=lambda *args, **kwargs: {},
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
    stubs["models.tool_idempotency_records"] = make_module(
        "models.tool_idempotency_records",
        ToolIdempotencyRecordModel=ToolIdempotencyRecordModel,
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
             patch_attr(
                 self.tool_executor,
                 "validate_tool_input",
                 Spy(return_value=(
                     {"recipient_email": "user@example.com"},
                     ["Missing required parameter: body"],
                 )),
             ), \
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
            updated_at=self.tool_executor.utc_now(),
            last_verified_at=self.tool_executor.utc_now(),
            is_default=True,
            account_label="Primary Gmail",
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

    def test_attempt_tool_call_uses_fresh_local_connection_without_remote_check(self):
        existing_connection = self.tool_executor.ToolConnectionModel(
            workspace_id="ws1",
            toolkit="GMAIL",
            status="connected",
            connected_account_id="acct-1",
            updated_at=self.tool_executor.utc_now(),
            last_verified_at=self.tool_executor.utc_now(),
            is_default=True,
            account_label="Primary Gmail",
        )
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=existing_connection),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        check_connection = Spy(return_value={"connected": True})
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", check_connection), \
             patch_attr(self.tool_executor, "execute_tool", Spy(return_value={"data": {"ok": True}, "error": None})):
            result = self.tool_executor.attempt_tool_call("GMAIL_SEND_EMAIL", "assistant", "ws1", db)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(check_connection.calls), 0)

    def test_attempt_tool_call_uses_selected_account_for_connection_check(self):
        db = FakeSession({
            self.tool_executor.ToolConnectionModel: FakeQuery(first_result=None),
        })
        tool_entry = {"toolkit": "GMAIL", "expected_params": [], "requires_auth": True}
        check_connection = Spy(return_value={"connected": False, "status": "not_found", "error": None})
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "check_connection", check_connection), \
             patch_attr(self.tool_executor, "get_connect_link", Spy(return_value="https://connect.example")), \
             patch_attr(self.tool_executor, "get_toolkit_auth_details", Spy(return_value={})):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                original_input="send it",
                selected_account_id="acct-99",
            )

        self.assertEqual(result["status"], "connect_required")
        self.assertEqual(check_connection.calls[0][1]["preferred_account_id"], "acct-99")

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

    def test_attempt_tool_call_returns_schema_validation_error_for_invalid_email(self):
        db = FakeSession()
        tool_entry = {
            "toolkit": "GMAIL",
            "expected_params": ["recipient_email", "body"],
            "requires_auth": True,
            "schema": {
                "required": ["recipient_email", "body"],
                "properties": {
                    "recipient_email": {"type": "string", "format": "email"},
                    "body": {"type": "string", "min_length": 1},
                },
            },
        }
        registry_validate = Spy(return_value=({"recipient_email": "not-an-email", "body": "hi"}, ["Parameter 'recipient_email' must be a valid email address."]))
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_input", registry_validate):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                input_args={"recipient_email": "not-an-email", "body": "hi"},
            )

        self.assertEqual(result["status"], "validation_error")
        self.assertIn("valid email address", result["error"])

    def test_attempt_tool_call_combines_live_schema_validation_errors(self):
        db = FakeSession()
        tool_entry = {
            "toolkit": "SLACK",
            "expected_params": ["channel"],
            "requires_auth": True,
        }
        live_schema = {
            "function": {
                "parameters": {
                    "required": ["channel"],
                    "properties": {
                        "channel": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                }
            }
        }
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "is_available", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "validate_tool_slug", Spy(return_value={"available": True, "exists": True, "error": None})), \
             patch_attr(self.tool_executor, "validate_tool_input", Spy(return_value=({"channel": "C123", "limit": "bad"}, []))), \
             patch_attr(self.tool_executor, "get_live_tool_schema", Spy(return_value=live_schema)):
            result = self.tool_executor.attempt_tool_call(
                "SLACK_FETCH_CONVERSATION_HISTORY",
                "assistant",
                "ws1",
                db,
                input_args={"channel": "C123", "limit": "bad"},
            )

        self.assertEqual(result["status"], "validation_error")
        self.assertIn("must be an integer", result["error"])

    def test_attempt_tool_call_blocks_unapproved_high_risk_write(self):
        db = FakeSession({
            self.tool_executor.PendingToolRequestModel: FakeQuery(first_result=None),
            self.tool_executor.ToolIdempotencyRecordModel: FakeQuery(first_result=None),
        })
        tool_entry = {
            "toolkit": "GMAIL",
            "expected_params": ["recipient_email", "body"],
            "requires_auth": False,
            "write_action": True,
        }
        approval_requirement = type(
            "ApprovalRequirement",
            (),
            {
                "required": True,
                "risk_level": "high",
                "reason": "Sends a real email.",
                "mode": "confirm_or_explicit_execute",
                "to_dict": lambda self: {
                    "required": True,
                    "risk_level": "high",
                    "reason": "Sends a real email.",
                    "categories": ["email"],
                    "mode": "confirm_or_explicit_execute",
                },
            },
        )()
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "get_tool_approval_requirement", Spy(return_value=approval_requirement)), \
             patch_attr(self.tool_executor, "get_tool_idempotency_fields", Spy(return_value=("recipient_email", "body"))):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                input_args={"recipient_email": "user@example.com", "body": "hi"},
                original_input="send it",
            )

        self.assertEqual(result["status"], "validation_error")
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["pending_kind"], "approval")
        self.assertTrue(result["resume_token"])

    def test_attempt_tool_call_replays_successful_idempotent_write_without_reexecution(self):
        existing_record = self.tool_executor.ToolIdempotencyRecordModel(
            workspace_id="ws1",
            tool_name="GMAIL_SEND_EMAIL",
            idempotency_key="abc123",
            status="success",
            input_hash="",
            output_json={"ok": True},
        )
        db = FakeSession({
            self.tool_executor.ToolIdempotencyRecordModel: FakeQuery(first_result=existing_record),
        })
        tool_entry = {
            "toolkit": "GMAIL",
            "expected_params": ["recipient_email", "body"],
            "requires_auth": False,
            "write_action": True,
        }
        execute_tool = Spy(return_value={"data": {"ok": False}, "error": None})
        with patch_attr(self.tool_executor, "get_tool", Spy(return_value=tool_entry)), \
             patch_attr(self.tool_executor, "is_agent_allowed", Spy(return_value=True)), \
             patch_attr(self.tool_executor, "get_tool_idempotency_fields", Spy(return_value=("recipient_email", "body"))), \
             patch_attr(self.tool_executor, "execute_tool", execute_tool):
            result = self.tool_executor.attempt_tool_call(
                "GMAIL_SEND_EMAIL",
                "assistant",
                "ws1",
                db,
                input_args={"recipient_email": "user@example.com", "body": "hi"},
                context_json={"idempotency_key": "abc123"},
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"], {"ok": True})
        self.assertTrue(result["idempotent_replay"])
        self.assertEqual(len(execute_tool.calls), 0)
