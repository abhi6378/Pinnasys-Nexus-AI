import unittest

import workflows.engine as engine
from tests.support import Spy, patch_attr


class WorkflowEngineTests(unittest.TestCase):
    def test_run_workflow_returns_unknown_for_missing_key(self):
        result = engine.run_workflow("missing_workflow", "hello", "ctx")

        self.assertTrue(result["error"])
        self.assertIn("Unknown workflow", result["final_output"])

    def test_email_triage_workflow_uses_definition_runner_and_resume_state(self):
        calls = []

        def fake_run_agent(agent_key, user_input, brain_context="", **kwargs):
            calls.append((agent_key, user_input))
            return {
                "name": agent_key.title(),
                "output": f"{agent_key}:{user_input[:30]}",
                "success": True,
                "tool_used": "GMAIL_CREATE_EMAIL_DRAFT" if "Draft Replies" in kwargs.get("workflow_state", {}).get("current_step", "") else "GMAIL_FETCH_EMAILS",
            }

        resume_state = {
            "completed_steps": [
                {
                    "step": "Read Emails",
                    "agent": "Assistant",
                    "input": "existing",
                    "output": "triaged",
                    "tool_used": "GMAIL_FETCH_EMAILS",
                    "tool_output": {"emails": []},
                    "success": True,
                }
            ]
        }

        with patch_attr(engine, "run_agent", Spy(side_effect=fake_run_agent)):
            result = engine.email_triage_workflow("check inbox", "ctx", resume_state=resume_state)

        self.assertFalse(result["error"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["step"], "Read Emails")
        self.assertEqual(result["steps"][1]["step"], "Draft Replies")
        self.assertEqual(len(calls), 1)

    def test_lead_capture_finalizer_preserves_sync_status_contract(self):
        def fake_run_agent(agent_key, user_input, brain_context="", **kwargs):
            tool_used = None
            if "CRM sync" in user_input:
                tool_used = "HUBSPOT_CREATE_CONTACT"
            if "spreadsheet update" in user_input:
                tool_used = "GOOGLESHEETS_CREATE_SPREADSHEET_ROW"
            return {
                "name": agent_key.title(),
                "output": "done",
                "success": True,
                "tool_used": tool_used,
            }

        with patch_attr(engine, "run_agent", Spy(side_effect=fake_run_agent)):
            result = engine.lead_capture_workflow("Jane Doe jane@example.com", "ctx")

        self.assertIn("HubSpot: ✅ Synced", result["final_output"])
        self.assertIn("Google Sheets: ✅ Logged", result["final_output"])

    def test_workflow_step_connector_hints_override_manual_connector_per_step(self):
        step_connectors = []

        def fake_run_agent(agent_key, user_input, brain_context="", **kwargs):
            step_connectors.append(
                (
                    kwargs.get("workflow_state", {}).get("current_step"),
                    kwargs.get("connector_context", {}),
                )
            )
            return {
                "name": agent_key.title(),
                "output": "done",
                "success": True,
            }

        with patch_attr(engine, "run_agent", Spy(side_effect=fake_run_agent)):
            engine.lead_capture_workflow(
                "Jane Doe jane@example.com",
                "ctx",
                connector_context={"mode": "manual", "selected_toolkit": "GMAIL", "selected_account_id": "acct-1"},
            )

        step_map = {step: connector for step, connector in step_connectors}
        self.assertEqual(step_map["Extract Lead"]["selected_toolkit"], "GMAIL")
        self.assertEqual(step_map["Log to HubSpot"]["selected_toolkit"], "HUBSPOT")
        self.assertEqual(step_map["Log to HubSpot"]["selected_account_id"], "")
        self.assertEqual(step_map["Log to Sheets"]["selected_toolkit"], "GOOGLE_SHEETS")
