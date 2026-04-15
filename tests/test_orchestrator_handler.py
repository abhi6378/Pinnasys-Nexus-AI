import unittest
from types import SimpleNamespace

from tests.support import Spy, import_fresh, make_module, make_sqlalchemy_stubs, patch_attr


def load_handler_module():
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["brain.brain_ai"] = make_module(
        "brain.brain_ai",
        BrainAI=type(
            "BrainAI",
            (),
            {
                "__init__": lambda self, workspace_id, db: None,
                "get_relevant_context": lambda self, user_input: "brain-context",
            },
        ),
    )
    stubs["brain.memory_extractor"] = make_module(
        "brain.memory_extractor",
        extract_and_save=lambda workspace_id, content, db: None,
    )
    stubs["helpers.executor"] = make_module(
        "helpers.executor",
        run_agent=lambda *args, **kwargs: {"output": "stub", "success": True},
    )
    stubs["helpers.configs"] = make_module(
        "helpers.configs",
        AGENTS={
            "assistant": {"role": "Assistant", "goal": "Help"},
            "sales": {"role": "Sales", "goal": "Sell"},
        },
    )
    stubs["workflows.engine"] = make_module(
        "workflows.engine",
        run_workflow=lambda *args, **kwargs: {"final_output": "wf", "steps": [], "error": False},
        WORKFLOWS={"email_triage": object(), "content_creation": object()},
    )
    stubs["storage.repositories"] = make_module(
        "storage.repositories",
        save_conversation=lambda *args, **kwargs: None,
        push_idea=lambda *args, **kwargs: None,
        get_conversations=lambda *args, **kwargs: [],
        save_workflow_run=lambda *args, **kwargs: None,
    )
    stubs["llm.client"] = make_module(
        "llm.client",
        generate_json=lambda *args, **kwargs: '{"agent":"assistant"}',
    )
    stubs["orchestrator.router"] = make_module(
        "orchestrator.router",
        route_request=lambda *args, **kwargs: None,
    )
    return import_fresh("orchestrator.handler", stubs)


class DetectWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler_module()

    def test_detect_workflow_matches_keyword_case_insensitively(self):
        self.assertEqual(
            self.handler.detect_workflow("Please CHECK MY INBOX today"),
            "email_triage",
        )

    def test_detect_workflow_returns_none_when_no_trigger_matches(self):
        self.assertIsNone(self.handler.detect_workflow("tell me a joke"))


class AutoRouteTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler_module()

    def test_auto_route_uses_router_single_agent_result(self):
        route = {"route_type": "single_agent", "selected_agent": "assistant"}
        exec_single = Spy(return_value={"mode": "single"})
        detect_workflow = Spy()
        with patch_attr(self.handler, "route_request", Spy(return_value=route)), \
             patch_attr(self.handler, "_exec_single_agent", exec_single), \
             patch_attr(self.handler, "detect_workflow", detect_workflow):
            result = self.handler._auto_route("hello", "ws1", object(), "ctx")

        self.assertEqual(result, {"mode": "single"})
        self.assertEqual(len(exec_single.calls), 1)
        args, kwargs = exec_single.calls[0]
        self.assertEqual(args, ("assistant", "hello", "ctx"))
        self.assertEqual(kwargs["workspace_id"], "ws1")
        self.assertIsNone(kwargs["resume_state"])
        self.assertEqual(len(detect_workflow.calls), 0)

    def test_auto_route_falls_back_to_keyword_workflow_when_router_returns_none(self):
        exec_workflow = Spy(return_value={"mode": "workflow"})
        detect_agent = Spy()
        with patch_attr(self.handler, "route_request", Spy(return_value=None)), \
             patch_attr(self.handler, "detect_workflow", Spy(return_value="email_triage")), \
             patch_attr(self.handler, "_exec_workflow", exec_workflow), \
             patch_attr(self.handler, "detect_agent", detect_agent):
            result = self.handler._auto_route("check my inbox", "ws1", object(), "ctx")

        self.assertEqual(result, {"mode": "workflow"})
        self.assertEqual(len(exec_workflow.calls), 1)
        self.assertEqual(len(detect_agent.calls), 0)

    def test_auto_route_falls_back_to_detect_agent_when_no_workflow_is_found(self):
        exec_single = Spy(return_value={"mode": "single"})
        with patch_attr(self.handler, "route_request", Spy(return_value=None)), \
             patch_attr(self.handler, "detect_workflow", Spy(return_value=None)), \
             patch_attr(self.handler, "detect_agent", Spy(return_value="assistant")), \
             patch_attr(self.handler, "_exec_single_agent", exec_single):
            result = self.handler._auto_route("do something", "ws1", object(), "ctx")

        self.assertEqual(result, {"mode": "single"})
        self.assertEqual(len(exec_single.calls), 1)

    def test_auto_route_uses_clarify_branch_without_legacy_fallback(self):
        route = {
            "route_type": "clarify",
            "clarification_question": "Which channel should I use?",
        }
        exec_clarify = Spy(return_value={"mode": "clarify"})
        detect_workflow = Spy()
        with patch_attr(self.handler, "route_request", Spy(return_value=route)), \
             patch_attr(self.handler, "_exec_clarify", exec_clarify), \
             patch_attr(self.handler, "detect_workflow", detect_workflow):
            result = self.handler._auto_route("summarize slack", "ws1", object(), "ctx")

        self.assertEqual(result, {"mode": "clarify"})
        self.assertEqual(exec_clarify.calls[0][0], ("Which channel should I use?",))
        self.assertEqual(len(detect_workflow.calls), 0)

    def test_auto_route_workflow_without_valid_key_falls_back_to_legacy_detection(self):
        route = {"route_type": "workflow", "selected_workflow": None}
        exec_workflow = Spy(return_value={"mode": "workflow"})
        with patch_attr(self.handler, "route_request", Spy(return_value=route)), \
             patch_attr(self.handler, "detect_workflow", Spy(return_value="content_creation")), \
             patch_attr(self.handler, "_exec_workflow", exec_workflow):
            result = self.handler._auto_route("write a blog post", "ws1", object(), "ctx")

        self.assertEqual(result, {"mode": "workflow"})
        self.assertEqual(len(exec_workflow.calls), 1)


class HandleRequestTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler_module()
        self.handler.BrainAI = lambda workspace_id, db: SimpleNamespace(
            get_relevant_context=lambda user_input: "brain-context"
        )

    def test_handle_request_force_agent_saves_conversation_and_extracts_memory(self):
        repo = self.handler.repo
        repo.save_conversation = Spy()
        extract_and_save = Spy()
        with patch_attr(self.handler, "_exec_single_agent", Spy(return_value={
            "mode": "single",
            "agent": "assistant",
            "output": "completed output",
            "steps": [],
        })), \
             patch_attr(self.handler, "extract_and_save", extract_and_save), \
             patch_attr(self.handler, "detect_opportunity", Spy(return_value=None)):
            result = self.handler.handle_request("hello", "ws1", object(), force_agent="assistant")

        self.assertEqual(result["mode"], "single")
        self.assertIsNone(result["idea"])
        self.assertFalse(result["error"])
        self.assertEqual(len(repo.save_conversation.calls), 1)
        self.assertEqual(repo.save_conversation.calls[0][0][1:], ("ws1", "assistant", "hello", "completed output"))
        self.assertEqual(len(extract_and_save.calls), 1)
        self.assertEqual(extract_and_save.calls[0][0][0:2], ("ws1", "completed output"))

    def test_handle_request_skips_memory_extraction_for_special_modes(self):
        repo = self.handler.repo
        repo.save_conversation = Spy()
        extract_and_save = Spy()
        with patch_attr(self.handler, "_exec_single_agent", Spy(return_value={
            "mode": "connect_required",
            "agent": "assistant",
            "output": "connect this",
            "steps": [],
            "connect_required": True,
        })), \
             patch_attr(self.handler, "extract_and_save", extract_and_save):
            result = self.handler.handle_request("hello", "ws1", object(), force_agent="assistant")

        self.assertEqual(result["idea"], None)
        self.assertEqual(len(repo.save_conversation.calls), 1)
        self.assertEqual(len(extract_and_save.calls), 0)

    def test_handle_request_pushes_idea_when_probe_hits_and_detector_finds_one(self):
        repo = self.handler.repo
        repo.save_conversation = Spy()
        repo.push_idea = Spy(return_value=SimpleNamespace(
            id="idea-1",
            title="Follow up",
            description="Send a campaign",
        ))
        long_output = "x" * 250
        # Characterization: the current implementation only probes opportunities
        # for long single-agent outputs and a random 40% sampling gate.
        with patch_attr(self.handler, "_exec_single_agent", Spy(return_value={
            "mode": "single",
            "agent": "assistant",
            "output": long_output,
            "steps": [],
        })), \
             patch_attr(self.handler.random, "random", Spy(return_value=0.1)), \
             patch_attr(self.handler, "detect_opportunity", Spy(return_value={
                 "title": "Follow up",
                 "description": "Send a campaign",
                 "workflow_hint": "content_creation",
             })), \
             patch_attr(self.handler, "extract_and_save", Spy()):
            result = self.handler.handle_request("hello", "ws1", object(), force_agent="assistant")

        self.assertEqual(result["idea"]["id"], "idea-1")
        self.assertEqual(len(repo.push_idea.calls), 1)

    def test_handle_request_marks_single_result_as_resumed_when_resume_state_has_workflow_key(self):
        self.handler.repo.save_conversation = Spy()
        with patch_attr(self.handler, "_exec_single_agent", Spy(return_value={
            "mode": "single",
            "agent": "assistant",
            "output": "done",
            "steps": [],
        })), \
             patch_attr(self.handler, "extract_and_save", Spy()), \
             patch_attr(self.handler, "detect_opportunity", Spy(return_value=None)):
            result = self.handler.handle_request(
                "hello",
                "ws1",
                object(),
                force_agent="assistant",
                resume_state={"workflow_key": "email_triage"},
            )

        self.assertTrue(result["workflow_resumed"])
