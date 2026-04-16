import unittest

from tests.support import patch_attr
import tools.capability_layer as capability_layer
from tools.capability_layer import (
    build_capability_request,
    build_tool_usage_guidance,
    prepare_tools_for_prompt,
    resolve_agent_tool_access,
    resolve_capability_request,
)


class CapabilityLayerTests(unittest.TestCase):
    def test_resolve_agent_tool_access_derives_tools_from_capability_groups(self):
        result = resolve_agent_tool_access("email_marketer")

        self.assertEqual(result["resolution_source"], "capability_groups")
        self.assertFalse(result["fallback_used"])
        self.assertEqual(
            set(result["allowed_tools"]),
            {"GMAIL_SEND_EMAIL", "GMAIL_FETCH_EMAILS", "GMAIL_CREATE_EMAIL_DRAFT"},
        )

    def test_resolve_agent_tool_access_falls_back_to_legacy_when_mapping_is_incomplete(self):
        result = resolve_agent_tool_access(
            "assistant",
            agent_config={
                "tool_mode": "tool_enabled",
                "allowed_tools": ["GMAIL_SEND_EMAIL", "TAVILY_SEARCH"],
            },
        )

        self.assertEqual(result["resolution_source"], "legacy_explicit_allowlist")
        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["allowed_tools"], ["GMAIL_SEND_EMAIL", "TAVILY_SEARCH"])

    def test_resolve_agent_tool_access_falls_back_to_registry_agent_tools_when_groups_are_missing(self):
        with patch_attr(capability_layer, "get_tools_missing_capability_groups", lambda agent_key: ["NEW_TOOL"]):
            result = resolve_agent_tool_access("assistant")

        self.assertEqual(result["resolution_source"], "registry_agent_fallback")
        self.assertTrue(result["fallback_used"])
        self.assertIn("GMAIL_SEND_EMAIL", result["allowed_tools"])

    def test_build_tool_usage_guidance_uses_registry_metadata(self):
        with patch_attr(capability_layer, "get_tools_by_names", lambda names: [{
            "tool_name": "GMAIL_SEND_EMAIL",
            "toolkit": "GMAIL",
            "description": "Registry-driven description.",
            "action": "Send mail",
            "schema": {"required": ["recipient_email"]},
            "usage_notes": (),
            "safety_notes": (),
        }]):
            guidance = build_tool_usage_guidance(
                "assistant",
                agent_config={
                    "tool_mode": "tool_enabled",
                    "allowed_tools": ["GMAIL_SEND_EMAIL"],
                    "tool_policy": "Only for real sends.",
                },
            )

        self.assertIn("Registry-driven description.", guidance)
        self.assertNotIn("Send mail", guidance)
        self.assertIn("Only for real sends.", guidance)
        self.assertIn("Never simulate inbox", guidance)
        self.assertIn("Prefer drafts over sends", guidance)

    def test_prepare_tools_for_prompt_clarifies_actions_without_mutating_input(self):
        source_tool = {
            "tool_name": "GMAIL_FETCH_EMAILS",
            "toolkit": "GMAIL",
            "action": "List recent emails",
            "description": "Read recent Gmail inbox emails for review, summary, or triage.",
            "capability_groups": ("email",),
            "expected_params": [],
        }

        result = prepare_tools_for_prompt("assistant", [source_tool], "check my inbox")

        self.assertEqual(source_tool["action"], "List recent emails")
        self.assertEqual(len(result["tools"]), 1)
        self.assertIn("Gmail", result["tools"][0]["action"])
        self.assertFalse(result["filter_applied"])

    def test_prepare_tools_for_prompt_filters_tools_by_intent_groups(self):
        gmail_tool = {
            "tool_name": "GMAIL_FETCH_EMAILS",
            "toolkit": "GMAIL",
            "action": "List recent emails",
            "description": "Read recent Gmail inbox emails for review, summary, or triage.",
            "capability_groups": ("email",),
            "expected_params": [],
        }
        tavily_tool = {
            "tool_name": "TAVILY_SEARCH",
            "toolkit": "TAVILY",
            "action": "Search the web via Tavily",
            "description": "Search the web for current information and cited results.",
            "capability_groups": ("research",),
            "expected_params": ["query"],
        }

        result = prepare_tools_for_prompt(
            "assistant",
            [gmail_tool, tavily_tool],
            "check my inbox",
        )

        self.assertTrue(result["filter_applied"])
        self.assertEqual(result["groups"], ["email"])
        self.assertEqual([tool["tool_name"] for tool in result["tools"]], ["GMAIL_FETCH_EMAILS"])

    def test_prepare_tools_for_prompt_falls_back_safely_when_intent_is_unclear(self):
        gmail_tool = {
            "tool_name": "GMAIL_FETCH_EMAILS",
            "toolkit": "GMAIL",
            "action": "List recent emails",
            "description": "Read recent Gmail inbox emails for review, summary, or triage.",
            "capability_groups": ("email",),
            "expected_params": [],
        }
        tavily_tool = {
            "tool_name": "TAVILY_SEARCH",
            "toolkit": "TAVILY",
            "action": "Search the web via Tavily",
            "description": "Search the web for current information and cited results.",
            "capability_groups": ("research",),
            "expected_params": ["query"],
        }

        result = prepare_tools_for_prompt("assistant", [gmail_tool, tavily_tool], "help me out")

        self.assertFalse(result["filter_applied"])
        self.assertEqual(result["reason"], "no_intent_match")
        self.assertEqual(
            [tool["tool_name"] for tool in result["tools"]],
            ["GMAIL_FETCH_EMAILS", "TAVILY_SEARCH"],
        )

    def test_prepare_tools_for_prompt_falls_back_when_tool_metadata_is_incomplete(self):
        gmail_tool = {
            "tool_name": "GMAIL_FETCH_EMAILS",
            "toolkit": "GMAIL",
            "action": "List recent emails",
            "description": "Read recent Gmail inbox emails for review, summary, or triage.",
            "capability_groups": (),
            "expected_params": [],
        }
        tavily_tool = {
            "tool_name": "TAVILY_SEARCH",
            "toolkit": "TAVILY",
            "action": "Search the web via Tavily",
            "description": "Search the web for current information and cited results.",
            "capability_groups": ("research",),
            "expected_params": ["query"],
        }

        result = prepare_tools_for_prompt("assistant", [gmail_tool, tavily_tool], "check my inbox")

        self.assertFalse(result["filter_applied"])
        self.assertEqual(result["reason"], "metadata_incomplete")

    def test_build_capability_request_derives_from_route_and_tool(self):
        request = build_capability_request(
            "assistant",
            user_input="check my inbox",
            route_decision={
                "route_type": "single_agent",
                "primary_intent": "email_triage",
                "system_family": "email",
                "operation": "read",
                "requires_live_data": True,
            },
            requested_tool_name="GMAIL_FETCH_EMAILS",
        )

        self.assertEqual(request.capability_group, "email")
        self.assertEqual(request.action_class, "read")
        self.assertTrue(request.requires_live_data)
        self.assertEqual(request.execution_mode, "read")
        self.assertIn("GMAIL_FETCH_EMAILS", request.preferred_tools)

    def test_resolve_capability_request_returns_candidate_tools(self):
        request = build_capability_request(
            "assistant",
            user_input="check my inbox",
            route_decision={"system_family": "email", "operation": "read", "route_type": "single_agent"},
        )

        result = resolve_capability_request(
            "assistant",
            request,
            allowed_tool_names=["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL"],
        )

        self.assertIn("GMAIL_FETCH_EMAILS", result["candidate_tools"])

    def test_build_capability_request_uses_manual_connector_as_toolkit_family(self):
        request = build_capability_request(
            "assistant",
            user_input="show my deals",
            route_decision={"system_family": "crm", "operation": "read", "route_type": "single_agent"},
            connector_context={"mode": "manual", "selected_toolkit": "HUBSPOT", "enforce_toolkit": True},
        )

        self.assertEqual(request.toolkit_family, "HUBSPOT")
        self.assertEqual(request.metadata["connector_context"]["selected_toolkit"], "HUBSPOT")

    def test_resolve_capability_request_honors_manual_connector_constraint(self):
        request = build_capability_request(
            "assistant",
            user_input="check my inbox",
            route_decision={"system_family": "email", "operation": "read", "route_type": "single_agent"},
        )

        result = resolve_capability_request(
            "assistant",
            request,
            allowed_tool_names=["GMAIL_FETCH_EMAILS", "SLACK_FETCH_CONVERSATION_HISTORY"],
            connector_context={"mode": "manual", "selected_toolkit": "SLACK", "enforce_toolkit": True},
        )

        self.assertEqual(result["candidate_tools"], [])
        self.assertIn(result["resolution_reason"], {"connector_constraint_no_match", "no_candidate_match"})

    def test_resolve_capability_request_supports_hubspot_deal_reads(self):
        request = build_capability_request(
            "assistant",
            user_input="show my ongoing deals",
            route_decision={"system_family": "crm", "operation": "read", "route_type": "single_agent"},
            connector_context={"mode": "manual", "selected_toolkit": "HubSpot", "enforce_toolkit": True},
        )

        result = resolve_capability_request(
            "assistant",
            request,
            allowed_tool_names=["HUBSPOT_LIST_DEALS", "HUBSPOT_CREATE_DEAL"],
            connector_context={"mode": "manual", "selected_toolkit": "HubSpot", "enforce_toolkit": True},
        )

        self.assertIn("HUBSPOT_LIST_DEALS", result["candidate_tools"])
