import unittest
from types import SimpleNamespace

from tests.support import (
    FakeQuery,
    FakeSession,
    Spy,
    import_fresh,
    make_model_class,
    make_module,
    patch_attr,
    stubbed_modules,
)


def load_repositories_module():
    WorkspaceModel = make_model_class("WorkspaceModel", ["id", "created_at"])
    BrainProfileModel = make_model_class(
        "BrainProfileModel",
        [
            "workspace_id",
            "company_name",
            "brand_context",
            "tone",
            "audience",
            "goals",
            "services",
            "pricing",
            "competitors",
            "support_style",
            "updated_at",
        ],
    )
    KnowledgeItemModel = make_model_class("KnowledgeItemModel", ["workspace_id", "created_at", "id"])
    QuizAnswerModel = make_model_class("QuizAnswerModel", ["workspace_id"])
    ConversationModel = make_model_class("ConversationModel", ["workspace_id", "created_at"])
    WorkflowRunModel = make_model_class("WorkflowRunModel", ["workspace_id", "created_at"])
    IdeaModel = make_model_class("IdeaModel", ["workspace_id", "created_at", "id", "status"])
    PendingToolRequestModel = make_model_class(
        "PendingToolRequestModel",
        ["workspace_id", "status", "updated_at"],
    )

    stubs = {
        "storage.db": make_module(
            "storage.db",
            WorkspaceModel=WorkspaceModel,
            BrainProfileModel=BrainProfileModel,
            KnowledgeItemModel=KnowledgeItemModel,
            QuizAnswerModel=QuizAnswerModel,
            ConversationModel=ConversationModel,
            WorkflowRunModel=WorkflowRunModel,
            IdeaModel=IdeaModel,
        ),
        "sqlalchemy.orm": make_module("sqlalchemy.orm", Session=type("Session", (), {})),
        "sqlalchemy": make_module("sqlalchemy"),
        "models.pending_tool_requests": make_module(
            "models.pending_tool_requests",
            PendingToolRequestModel=PendingToolRequestModel,
        ),
    }
    stubs["sqlalchemy"].orm = stubs["sqlalchemy.orm"]
    repo = import_fresh("storage.repositories", stubs)
    repo._pending_tool_request_model = PendingToolRequestModel
    return repo


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = load_repositories_module()

    def test_create_workspace_creates_workspace_and_empty_brain_profile(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "ws-1"):
            workspace = self.repo.create_workspace(db, "Acme")

        self.assertEqual(workspace.id, "ws-1")
        self.assertEqual(workspace.name, "Acme")
        self.assertEqual(len(db.added), 2)
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshed, [workspace])

    def test_update_brain_creates_profile_and_ignores_falsy_updates(self):
        db = FakeSession()
        with patch_attr(self.repo, "get_brain", lambda db, workspace_id: None):
            brain = self.repo.update_brain(db, "ws-1", {"company_name": "Acme", "tone": ""})

        # Characterization: falsy update values are skipped instead of clearing fields.
        self.assertEqual(brain.workspace_id, "ws-1")
        self.assertEqual(brain.company_name, "Acme")
        self.assertNotIn("tone", brain.__dict__)
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshed, [brain])

    def test_add_knowledge_defaults_tags_to_empty_list(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "k-1"):
            item = self.repo.add_knowledge(db, "ws-1", "text", "Title", "Body")

        self.assertEqual(item.id, "k-1")
        self.assertEqual(item.tags, [])
        self.assertEqual(db.refreshed, [item])

    def test_get_knowledge_returns_newest_items_when_query_is_empty(self):
        items = [SimpleNamespace(title=f"Item {i}") for i in range(4)]
        db = FakeSession({
            self.repo.KnowledgeItemModel: FakeQuery(all_result=items),
        })

        result = self.repo.get_knowledge(db, "ws-1", "", limit=2)

        self.assertEqual(result, items[:2])

    def test_get_knowledge_scores_content_title_and_tags(self):
        items = [
            SimpleNamespace(title="Other", content="nothing here", tags=[]),
            SimpleNamespace(title="Keyword in title", content="plain", tags=[]),
            SimpleNamespace(title="Other title", content="keyword in content", tags=[]),
            SimpleNamespace(title="Other title", content="plain", tags=["keyword"]),
        ]
        db = FakeSession({
            self.repo.KnowledgeItemModel: FakeQuery(all_result=items),
        })

        result = self.repo.get_knowledge(db, "ws-1", "keyword", limit=10)

        self.assertEqual(result[0], items[2])
        self.assertEqual(result[1:], [items[1], items[3]])

    def test_save_conversation_persists_helper_input_and_output(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "c-1"):
            conv = self.repo.save_conversation(db, "ws-1", "assistant", "hi", "hello")

        self.assertEqual(conv.id, "c-1")
        self.assertEqual(conv.helper, "assistant")
        self.assertEqual(conv.input, "hi")
        self.assertEqual(conv.output, "hello")

    def test_save_workflow_run_persists_steps_and_final_output(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "wf-1"):
            run = self.repo.save_workflow_run(db, "ws-1", "email_triage", [{"step": "one"}], "done")

        self.assertEqual(run.workflow_name, "email_triage")
        self.assertEqual(run.steps, [{"step": "one"}])
        self.assertEqual(run.final_output, "done")

    def test_push_idea_sets_pending_status_and_default_workflow_hint(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "idea-1"):
            idea = self.repo.push_idea(db, "ws-1", "Title", "Desc", "assistant")

        self.assertEqual(idea.status, "pending")
        self.assertEqual(idea.workflow_hint, "")

    def test_list_pending_tool_requests_limits_query(self):
        query = FakeQuery(all_result=["row-1", "row-2", "row-3"])
        db = FakeSession({
            self.repo._pending_tool_request_model: query,
        })
        pending_module = make_module(
            "models.pending_tool_requests",
            PendingToolRequestModel=self.repo._pending_tool_request_model,
        )
        with stubbed_modules({"models.pending_tool_requests": pending_module}):
            result = self.repo.list_pending_tool_requests(db, "ws-1", limit=2)

        self.assertEqual(result, ["row-1", "row-2", "row-3"])
        self.assertEqual(query.limit_value, 2)

    def test_save_conversation_logs_and_reraises_on_database_failure(self):
        class BrokenSession(FakeSession):
            def commit(self):
                raise RuntimeError("db down")

        db = BrokenSession()
        log_exception = Spy()
        with patch_attr(self.repo, "_id", lambda: "c-1"), \
             patch_attr(self.repo, "log_exception", log_exception):
            with self.assertRaises(RuntimeError):
                self.repo.save_conversation(db, "ws-1", "assistant", "hi", "hello")

        self.assertEqual(len(log_exception.calls), 1)
        self.assertEqual(log_exception.calls[0][0][1], "storage.conversation_save_failed")
