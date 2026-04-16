import unittest

from tests.support import Spy, import_fresh, make_module, patch_attr


def load_router_module():
    stubs = {
        "helpers.configs": make_module(
            "helpers.configs",
            AGENTS={"assistant": {"name": "Buddy", "role": "Assistant", "goal": "Help", "allowed_tools": []}},
        ),
        "workflows.engine": make_module(
            "workflows.engine",
            WORKFLOWS={"email_triage": object()},
        ),
        "llm.client": make_module("llm.client", generate_json=lambda *args, **kwargs: "{}"),
        "storage.repositories": make_module(
            "storage.repositories",
            get_conversations=lambda *args, **kwargs: [],
        ),
        "tools.capability_layer": make_module(
            "tools.capability_layer",
            summarize_agent_capabilities=lambda *args, **kwargs: {
                "capability_groups": [],
                "toolkits": [],
            },
        ),
    }
    return import_fresh("orchestrator.router", stubs)


class RouterLoggingTests(unittest.TestCase):
    def setUp(self):
        self.router = load_router_module()

    def test_route_request_logs_exception_and_returns_none_on_failure(self):
        exception_spy = Spy()
        with patch_attr(self.router, "generate_json", Spy(side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))), \
             patch_attr(self.router, "log_exception", exception_spy):
            result = self.router.route_request("hello", "ws1", object(), "ctx")

        self.assertIsNone(result)
        self.assertEqual(len(exception_spy.calls), 1)
        self.assertEqual(exception_spy.calls[0][0][1], "router.failed")

    def test_route_request_logs_exit_on_success(self):
        exit_spy = Spy()
        with patch_attr(self.router, "generate_json", Spy(return_value='{"route_type":"single_agent","selected_agent":"assistant","confidence":0.9,"steps":[]}')), \
             patch_attr(self.router, "log_event", exit_spy):
            result = self.router.route_request("hello", "ws1", object(), "ctx")

        self.assertEqual(result["route_type"], "single_agent")
        self.assertEqual(len(exit_spy.calls), 3)
        self.assertEqual(exit_spy.calls[1][0][2], "router.decision")
        self.assertEqual(exit_spy.calls[2][0][2], "router.exit")

    def test_route_request_accepts_structured_alias_fields_but_returns_legacy_shape(self):
        raw = (
            '{"route_type":"single_agent","intent":"email_triage","agent":"assistant",'
            '"confidence":0.92,"reason":"best fit","steps":[{"agent":"assistant","task":"triage"}],'
            '"missing_info":[]}'
        )
        with patch_attr(self.router, "generate_json", Spy(return_value=raw)):
            result = self.router.route_request("check my inbox", "ws1", object(), "ctx")

        self.assertEqual(result["route_type"], "single_agent")
        self.assertEqual(result["primary_intent"], "email_triage")
        self.assertEqual(result["selected_agent"], "assistant")
        self.assertNotIn("intent", result)
        self.assertNotIn("agent", result)

    def test_route_request_decision_returns_structured_contract_with_inferred_fields(self):
        raw = (
            '{"route_type":"workflow","primary_intent":"email_triage","selected_workflow":"email_triage",'
            '"confidence":0.87,"reason":"needs inbox access","steps":[{"agent":"assistant","task":"read inbox"}],'
            '"risk_flags":["live_action"]}'
        )
        with patch_attr(self.router, "generate_json", Spy(return_value=raw)):
            result = self.router.route_request_decision("check my inbox", "ws1", object(), "ctx")

        self.assertEqual(result.route_type, "workflow")
        self.assertEqual(result.selected_workflow, "email_triage")
        self.assertEqual(result.system_family, "email")
        self.assertTrue(result.requires_live_data)
        self.assertTrue(result.approval_required.required)
        self.assertEqual(result.ordered_steps[0].agent, "assistant")
