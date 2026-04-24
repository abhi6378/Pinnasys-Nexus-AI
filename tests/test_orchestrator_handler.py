import unittest
from types import SimpleNamespace

from tests.support import Spy, import_fresh, make_module, make_sqlalchemy_stubs, patch_attr


def load_handler_module():
    class StubConnectorContext:
        def __init__(
            self,
            mode="auto",
            selected_toolkit="",
            selected_account_id="",
            selected_account_alias="",
            validation_status="ok",
            connected=False,
            available_account_count=0,
            effective_account_id="",
            effective_account_alias="",
        ):
            self.mode = mode
            self.selected_toolkit = selected_toolkit
            self.selected_account_id = selected_account_id
            self.selected_account_alias = selected_account_alias
            self.validation_status = validation_status
            self.connected = connected
            self.available_account_count = available_account_count
            self.effective_account_id = effective_account_id
            self.effective_account_alias = effective_account_alias

        def to_dict(self):
            return {
                "mode": self.mode,
                "selected_toolkit": self.selected_toolkit,
                "selected_connector_key": self.selected_toolkit,
                "selected_account_id": self.selected_account_id,
                "selected_account_alias": self.selected_account_alias,
                "enforce_toolkit": bool(self.selected_toolkit and self.mode == "manual"),
                "enforce_account": bool(self.selected_account_id),
                "source": "system_inferred",
                "display_label": self.selected_toolkit.title(),
                "validation_status": self.validation_status,
                "stale_selection": False,
                "status_reason": "",
                "available_account_count": self.available_account_count,
                "effective_account_id": self.effective_account_id,
                "effective_account_alias": self.effective_account_alias,
                "connected": self.connected,
            }

    class StubConnectorStatus:
        def __init__(self, validation_status="ok"):
            self.validation_status = validation_status

        def to_dict(self):
            return {
                "toolkit": "",
                "connector_key": "",
                "label": "",
                "slug": "",
                "connected": False,
                "status": "unknown",
                "source": "local_cache",
                "validation_status": self.validation_status,
                "status_reason": "",
                "stale": False,
                "stale_selection": False,
                "account_required": False,
                "account_count": 0,
                "selected_account_id": "",
                "selected_account_alias": "",
                "effective_account_id": "",
                "effective_account_alias": "",
                "connect_url": None,
                "setup_message": "",
                "connection_mode": "",
                "auth_mode": "",
                "last_verified_at": "",
                "accounts": [],
            }

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
        extract_and_save=lambda workspace_id, content, db, **kwargs: None,
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
    stubs["tools.connector_service"] = make_module(
        "tools.connector_service",
        normalize_connector_context=lambda value=None: StubConnectorContext(
            mode=str((value or {}).get("mode", "auto") or "auto"),
            selected_toolkit=str((value or {}).get("selected_toolkit", "") or ""),
            selected_account_id=str((value or {}).get("selected_account_id", "") or ""),
        ),
        refresh_connector_status=lambda *args, **kwargs: StubConnectorStatus(),
        validate_connector_context=lambda connector, workspace_id, db, **kwargs: (
            connector,
            StubConnectorStatus(),
            "",
        ),
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
             patch_attr(self.handler, "_should_probe_opportunity", Spy(return_value=True)), \
             patch_attr(self.handler, "detect_opportunity", Spy(return_value={
                 "title": "Follow up",
                 "description": "Send a campaign",
                 "workflow_hint": "content_creation",
             })), \
             patch_attr(self.handler, "extract_and_save", Spy()):
            result = self.handler.handle_request("hello", "ws1", object(), force_agent="assistant")

        self.assertEqual(result["idea"]["id"], "idea-1")
        self.assertEqual(len(repo.push_idea.calls), 1)

    def test_should_probe_opportunity_is_deterministic_for_same_input(self):
        output = "x" * 250

        first = self.handler._should_probe_opportunity("ws1", "hello", output)
        second = self.handler._should_probe_opportunity("ws1", "hello", output)

        self.assertEqual(first, second)

    def test_sanitize_history_output_filters_tool_and_error_noise(self):
        self.assertEqual(
            self.handler._sanitize_history_output("Assistant needs access to Gmail to continue."),
            "",
        )
        self.assertEqual(
            self.handler._sanitize_history_output("Here is the clean summary."),
            "Here is the clean summary.",
        )

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

    def test_handle_request_passes_connector_context_to_agent_execution(self):
        self.handler.repo.save_conversation = Spy()
        exec_single = Spy(return_value={
            "mode": "single",
            "agent": "assistant",
            "output": "done",
            "steps": [],
        })
        with patch_attr(self.handler, "_exec_single_agent", exec_single), \
             patch_attr(self.handler, "extract_and_save", Spy()), \
             patch_attr(self.handler, "detect_opportunity", Spy(return_value=None)):
            self.handler.handle_request(
                "hello",
                "ws1",
                object(),
                force_agent="assistant",
                connector_context={"mode": "manual", "selected_toolkit": "HUBSPOT"},
            )

        self.assertEqual(
            exec_single.calls[0][1]["connector_context"],
            {
                "mode": "manual",
                "selected_toolkit": "HUBSPOT",
                "selected_connector_key": "HUBSPOT",
                "selected_account_id": "",
                "selected_account_alias": "",
                "enforce_toolkit": True,
                "enforce_account": False,
                "source": "system_inferred",
                "display_label": "Hubspot",
                "validation_status": "ok",
                "stale_selection": False,
                "status_reason": "",
                "available_account_count": 0,
                "effective_account_id": "",
                "effective_account_alias": "",
                "connected": False,
            },
        )

    def test_handle_request_returns_validation_error_for_invalid_connector_selection(self):
        self.handler.repo.save_conversation = Spy()
        with patch_attr(
            self.handler,
            "validate_connector_context",
            Spy(
                return_value=(
                    SimpleNamespace(to_dict=lambda: {}, selected_toolkit="HUBSPOT", mode="manual"),
                    SimpleNamespace(to_dict=lambda: {"validation_status": "invalid_toolkit"}),
                    "Bad connector",
                )
            ),
        ):
            result = self.handler.handle_request(
                "hello",
                "ws1",
                object(),
                connector_context={"mode": "manual", "selected_toolkit": "HUBSPOT"},
            )

        self.assertEqual(result["mode"], "validation_error")
        self.assertEqual(result["output"], "Bad connector")
        self.assertEqual(result["connector_status"]["validation_status"], "invalid_toolkit")
        self.assertEqual(len(self.handler.repo.save_conversation.calls), 0)

    def test_exec_single_agent_preserves_approval_metadata_on_validation_error(self):
        with patch_attr(
            self.handler,
            "run_agent",
            Spy(return_value={
                "mode": "validation_error",
                "name": "Assistant",
                "output": "Approval required before send.",
                "success": False,
                "approval_required": True,
                "approval_requirement": {"required": True, "risk_level": "high"},
                "resume_token": "resume-1",
                "pending_kind": "approval",
            }),
        ):
            result = self.handler._exec_single_agent(
                "assistant",
                "send it",
                "ctx",
                workspace_id="ws1",
                db=object(),
            )

        self.assertEqual(result["mode"], "validation_error")
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["resume_token"], "resume-1")
        self.assertEqual(result["pending_kind"], "approval")

    def test_handle_request_uses_router_workflow_to_schedule_chat_automation(self):
        self.handler.repo.save_conversation = Spy()
        responses = [
            {
                "mode": "clarify",
                "agent": "system",
                "output": "I found the schedule, but not the automation target.",
                "steps": [],
                "schedule_target_required": True,
            },
            {
                "mode": "automation_scheduled",
                "agent": "system",
                "output": "Scheduled `email_triage` automation.",
                "steps": [],
                "automation": {"id": "task-1"},
            },
        ]
        schedule_spy = Spy(side_effect=lambda *args, **kwargs: responses.pop(0))
        with patch_attr(self.handler, "detect_workflow", Spy(return_value=None)), \
             patch_attr(self.handler, "maybe_create_chat_schedule", schedule_spy), \
             patch_attr(
                 self.handler,
                 "route_request",
                 Spy(return_value={"route_type": "workflow", "selected_workflow": "email_triage"}),
             ), \
             patch_attr(self.handler, "extract_and_save", Spy()):
            result = self.handler.handle_request(
                "check my recent unread mail and schedule replies for tomorrow at 9 am",
                "ws1",
                object(),
            )

        self.assertEqual(result["mode"], "automation_scheduled")
        self.assertEqual(len(schedule_spy.calls), 2)
        self.assertEqual(schedule_spy.calls[1][1]["workflow_key"], "email_triage")
        self.assertEqual(len(self.handler.repo.save_conversation.calls), 1)
