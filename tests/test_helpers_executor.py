import unittest

import helpers.executor as executor
from tests.support import Spy, make_module, patch_attr, stubbed_modules


class RunAgentTests(unittest.TestCase):
    def test_run_agent_returns_unknown_agent_for_missing_config(self):
        with patch_attr(executor, "get_agent", Spy(return_value=None)):
            result = executor.run_agent("ghost", "hello")

        self.assertEqual(result["agent"], "ghost")
        self.assertFalse(result["success"])
        self.assertIn("Unknown agent", result["output"])

    def test_run_agent_uses_text_path_for_text_only_agents(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "tool_mode": "text_only",
            "allowed_tools": [],
        }
        generate = Spy(return_value="plain response")
        with patch_attr(executor, "get_agent", Spy(return_value=agent)), \
             patch_attr(executor, "generate", generate):
            result = executor.run_agent("assistant", "hello", "ctx", history=[{"role": "user", "content": "old"}])

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "plain response")
        self.assertEqual(len(generate.calls), 1)

    def test_run_agent_keeps_tool_enabled_agent_on_text_path_without_workspace_context(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "tool_mode": "tool_enabled",
            "allowed_tools": ["GMAIL_SEND_EMAIL"],
        }
        generate = Spy(return_value="still text")
        with patch_attr(executor, "get_agent", Spy(return_value=agent)), \
             patch_attr(executor, "resolve_agent_tool_access", Spy(return_value={
                 "tool_mode": "tool_enabled",
                 "allowed_tools": ["GMAIL_SEND_EMAIL"],
                 "invalid_legacy_tools": [],
                 "resolution_source": "legacy_explicit_allowlist",
                 "tool_policy": "",
                 "legacy_tool_instructions": "",
             })), \
             patch_attr(executor, "generate", generate):
            result = executor.run_agent("assistant", "hello", "ctx")

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "still text")
        self.assertEqual(len(generate.calls), 1)

    def test_run_agent_returns_invalid_tool_when_config_only_contains_unknown_tools(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "tool_mode": "tool_enabled",
            "allowed_tools": ["NOT_REAL"],
        }
        with patch_attr(executor, "get_agent", Spy(return_value=agent)), \
             patch_attr(executor, "resolve_agent_tool_access", Spy(return_value={
                 "tool_mode": "tool_enabled",
                 "allowed_tools": [],
                 "invalid_legacy_tools": ["NOT_REAL"],
                 "resolution_source": "legacy_explicit_allowlist",
                 "tool_policy": "",
                 "legacy_tool_instructions": "",
             })):
            result = executor.run_agent("assistant", "hello", "ctx", workspace_id="ws1", db=object())

        self.assertFalse(result["success"])
        self.assertEqual(result["mode"], "invalid_tool")
        self.assertIn("does not match the registry", result["output"])

    def test_run_agent_delegates_to_tool_path_when_valid_tools_are_available(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "tool_mode": "tool_enabled",
            "allowed_tools": ["GMAIL_SEND_EMAIL"],
        }
        run_with_tools = Spy(return_value={"mode": "tool", "success": True})
        with patch_attr(executor, "get_agent", Spy(return_value=agent)), \
             patch_attr(executor, "resolve_agent_tool_access", Spy(return_value={
                 "tool_mode": "tool_enabled",
                 "allowed_tools": ["GMAIL_SEND_EMAIL"],
                 "invalid_legacy_tools": [],
                 "resolution_source": "legacy_explicit_allowlist",
                 "tool_policy": "",
                 "legacy_tool_instructions": "",
             })), \
             patch_attr(executor, "get_tools_by_names", Spy(return_value=[{"tool_name": "GMAIL_SEND_EMAIL"}])), \
             patch_attr(executor, "_run_with_tools", run_with_tools):
            result = executor.run_agent("assistant", "hello", "ctx", workspace_id="ws1", db=object())

        self.assertEqual(result, {"mode": "tool", "success": True})
        self.assertEqual(len(run_with_tools.calls), 1)

    def test_run_agent_logs_text_generation_failures_without_changing_response_shape(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "tool_mode": "text_only",
            "allowed_tools": [],
        }
        log_exception = Spy()
        with patch_attr(executor, "get_agent", Spy(return_value=agent)), \
             patch_attr(executor, "generate", Spy(side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))), \
             patch_attr(executor, "log_exception", log_exception):
            result = executor.run_agent("assistant", "hello", "ctx", workspace_id="ws1")

        self.assertFalse(result["success"])
        self.assertEqual(result["output"], "Error: boom")
        self.assertEqual(len(log_exception.calls), 1)
        self.assertEqual(log_exception.calls[0][0][1], "agent.run.failed")

    def test_run_with_tools_uses_filtered_prompt_tools_when_capability_layer_applies(self):
        agent = {
            "name": "Buddy",
            "role": "Assistant",
            "goal": "Help",
            "tone": "Warm",
            "boundaries": "None",
            "output_format": "Text",
            "allowed_tools": ["GMAIL_FETCH_EMAILS", "TAVILY_SEARCH"],
        }
        gmail_tool = {
            "tool_name": "GMAIL_FETCH_EMAILS",
            "toolkit": "GMAIL",
            "action": "List recent emails",
            "expected_params": [],
        }
        tavily_tool = {
            "tool_name": "TAVILY_SEARCH",
            "toolkit": "TAVILY",
            "action": "Search the web via Tavily",
            "expected_params": ["query"],
        }
        generate_tools = Spy(return_value="plain response")
        with patch_attr(executor, "prepare_tools_for_prompt", Spy(return_value={
            "tools": [gmail_tool],
            "filter_applied": True,
            "groups": ["email"],
            "reason": "intent_match",
        })), \
             patch_attr(executor, "get_tool_schemas", Spy(return_value=[])), \
             patch_attr(executor, "generate_with_tool_awareness", generate_tools), \
             stubbed_modules({"tools.tool_executor": make_module("tools.tool_executor", attempt_tool_call=Spy())}):
            result = executor._run_with_tools(
                agent_key="assistant",
                agent=agent,
                agent_name="Buddy",
                system_prompt="sys",
                user_input="check my inbox",
                available_tools=[gmail_tool, tavily_tool],
                workspace_id="ws1",
                db=object(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "plain response")
        self.assertEqual(
            generate_tools.calls[0][1]["available_tools"],
            [gmail_tool],
        )
