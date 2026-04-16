import unittest

import tools.tool_broker as tool_broker
from tests.support import FakeSession, Spy, patch_attr


class ToolBrokerTests(unittest.TestCase):
    def test_build_tool_plan_derives_capability_from_concrete_tool(self):
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="send an email",
            concrete_tool_name="GMAIL_SEND_EMAIL",
            params={"to": "user@example.com", "body": "hi"},
        )

        self.assertEqual(plan.capability.capability_group, "email")
        self.assertEqual(plan.capability.action_class, "send")
        self.assertEqual(plan.concrete_tool_name, "GMAIL_SEND_EMAIL")

    def test_resolve_prefers_capability_candidates_within_allowed_policy(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="check my inbox",
            route_decision={"route_type": "single_agent", "system_family": "email", "operation": "read"},
        )

        result = broker.resolve(
            plan,
            allowed_tool_names=["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL"],
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.tool_name, "GMAIL_FETCH_EMAILS")
        self.assertEqual(result.toolkit, "GMAIL")

    def test_resolve_assigns_idempotency_key_for_write_actions(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="send an email",
            concrete_tool_name="GMAIL_SEND_EMAIL",
            params={"recipient_email": "user@example.com", "subject": "Hi", "body": "Hello"},
        )

        result = broker.resolve(
            plan,
            allowed_tool_names=["GMAIL_SEND_EMAIL"],
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.execution_mode, "execute")
        self.assertTrue(result.idempotency_key)

    def test_execute_adapts_attempt_tool_call_to_typed_result(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="check my inbox",
            concrete_tool_name="GMAIL_FETCH_EMAILS",
        )
        resolution = broker.resolve(plan, allowed_tool_names=["GMAIL_FETCH_EMAILS"])

        with patch_attr(tool_broker, "attempt_tool_call", Spy(return_value={
            "status": "success",
            "output": {"emails": []},
            "error": None,
            "duration_ms": 12.5,
            "toolkit": "GMAIL",
        })):
            result = broker.execute(
                resolution,
                plan,
                workspace_id="ws1",
                db=FakeSession(),
                original_input="check my inbox",
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.output, {"emails": []})
        self.assertEqual(result.toolkit, "GMAIL")
        self.assertTrue(result.verified)

    def test_resolve_rejects_explicit_tool_outside_manual_connector_scope(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="check my inbox",
            concrete_tool_name="GMAIL_FETCH_EMAILS",
        )

        result = broker.resolve(
            plan,
            allowed_tool_names=["GMAIL_FETCH_EMAILS"],
            connector_context={"mode": "manual", "selected_toolkit": "HUBSPOT", "enforce_toolkit": True},
        )

        self.assertEqual(result.status, "invalid_tool")
        self.assertEqual(result.resolution_source, "connector_constraint")

    def test_execute_passes_selected_account_id_to_tool_executor(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="check my inbox",
            concrete_tool_name="GMAIL_FETCH_EMAILS",
        )
        resolution = broker.resolve(plan, allowed_tool_names=["GMAIL_FETCH_EMAILS"])
        attempt = Spy(return_value={
            "status": "success",
            "output": {"emails": []},
            "error": None,
            "duration_ms": 12.5,
            "toolkit": "GMAIL",
        })

        with patch_attr(tool_broker, "attempt_tool_call", attempt):
            broker.execute(
                resolution,
                plan,
                workspace_id="ws1",
                db=FakeSession(),
                original_input="check my inbox",
                connector_context={
                    "mode": "manual",
                    "selected_toolkit": "GMAIL",
                    "selected_account_id": "acct-1",
                    "enforce_toolkit": True,
                    "enforce_account": True,
                },
            )

        self.assertEqual(attempt.calls[0][1]["selected_account_id"], "acct-1")
        self.assertEqual(
            attempt.calls[0][1]["context_json"]["connector_context"]["selected_account_id"],
            "acct-1",
        )

    def test_resolve_requires_account_when_manual_connector_has_multiple_accounts(self):
        broker = tool_broker.ComposioDirectBroker()
        plan = tool_broker.build_tool_plan(
            "assistant",
            user_intent="check my inbox",
            concrete_tool_name="GMAIL_FETCH_EMAILS",
        )

        result = broker.resolve(
            plan,
            allowed_tool_names=["GMAIL_FETCH_EMAILS"],
            connector_context={
                "mode": "manual",
                "selected_toolkit": "GMAIL",
                "enforce_toolkit": True,
                "available_account_count": 2,
                "display_label": "Gmail",
                "connected": True,
            },
        )

        self.assertEqual(result.status, "validation_error")
        self.assertEqual(result.resolution_source, "connector_account_required")
