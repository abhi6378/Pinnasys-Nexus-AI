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
    MemoryRecordModel = make_model_class(
        "MemoryRecordModel",
        ["workspace_id", "created_at", "updated_at", "id", "canonical_key", "superseded_by", "memory_type", "pinned"],
    )
    WorkingMemoryStateModel = make_model_class("WorkingMemoryStateModel", ["workspace_id", "updated_at"])
    MemoryEmbeddingModel = make_model_class(
        "MemoryEmbeddingModel",
        ["workspace_id", "updated_at", "memory_record_id", "model_name"],
    )
    WorkspaceConnectorPreferenceModel = make_model_class(
        "WorkspaceConnectorPreferenceModel",
        ["workspace_id", "updated_at"],
    )
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
            MemoryRecordModel=MemoryRecordModel,
            WorkingMemoryStateModel=WorkingMemoryStateModel,
            MemoryEmbeddingModel=MemoryEmbeddingModel,
            WorkspaceConnectorPreferenceModel=WorkspaceConnectorPreferenceModel,
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

    def test_upsert_working_memory_merges_lists_and_text_fields(self):
        existing = self.repo.WorkingMemoryStateModel(
            workspace_id="ws-1",
            active_tasks=["draft deck"],
            open_questions=["budget?"],
            state_json={"owner": "founder"},
        )
        db = FakeSession({
            self.repo.WorkingMemoryStateModel: FakeQuery(first_result=existing),
        })

        state = self.repo.upsert_working_memory(
            db,
            "ws-1",
            current_goal="Launch email campaign",
            active_tasks=["draft deck", "review copy"],
            open_questions=["budget?", "who approves?"],
            project_focus="Q2 outreach",
            state_json={"channel": "email"},
        )

        self.assertEqual(state.current_goal, "Launch email campaign")
        self.assertEqual(state.active_tasks, ["draft deck", "review copy"])
        self.assertEqual(state.open_questions, ["budget?", "who approves?"])
        self.assertEqual(state.project_focus, "Q2 outreach")
        self.assertEqual(state.state_json, {"owner": "founder", "channel": "email"})

    def test_upsert_memory_record_merges_existing_canonical_entry(self):
        existing = self.repo.MemoryRecordModel(
            id="m-1",
            workspace_id="ws-1",
            title="Email preference",
            content="Prefers concise emails.",
            summary="Prefers concise emails.",
            tags=["email"],
            entity_tags=[],
            tool_tags=[],
            importance_score=0.4,
            confidence_score=0.6,
            pinned=False,
            canonical_key="preference:email_style",
            metadata_json={"source": "old"},
        )
        db = FakeSession({
            self.repo.MemoryRecordModel: FakeQuery(first_result=existing),
        })

        record = self.repo.upsert_memory_record(
            db,
            "ws-1",
            memory_type="preference",
            title="Email preference",
            content="Prefers concise emails with bullets.",
            summary="Prefers concise emails with bullets.",
            tags=["email", "style"],
            importance_score=0.8,
            confidence_score=0.9,
            canonical_key="preference:email_style",
            metadata_json={"source": "new"},
        )

        self.assertEqual(record.content, "Prefers concise emails with bullets.")
        self.assertEqual(record.tags, ["email", "style"])
        self.assertEqual(record.importance_score, 0.8)
        self.assertEqual(record.metadata_json, {"source": "new"})

    def test_search_memory_records_scores_summary_and_tags(self):
        items = [
            SimpleNamespace(id="1", title="Other", summary="nothing", content="plain", tags=[], entity_tags=[], tool_tags=[], importance_score=0.1),
            SimpleNamespace(id="2", title="Email preference", summary="prefers bullets", content="plain", tags=["email"], entity_tags=[], tool_tags=[], importance_score=0.2),
            SimpleNamespace(id="3", title="Other", summary="plain", content="email pricing notes", tags=[], entity_tags=[], tool_tags=[], importance_score=0.1),
        ]
        query = FakeQuery(all_result=items)
        db = FakeSession({
            self.repo.MemoryRecordModel: query,
        })

        result = self.repo.search_memory_records(db, "ws-1", "email", limit=10)

        self.assertEqual(result[0], items[1])
        self.assertEqual(result[1], items[2])

    def test_upsert_workspace_connector_preference_creates_and_updates_preference(self):
        existing = self.repo.WorkspaceConnectorPreferenceModel(
            workspace_id="ws-1",
            mode="manual",
            selected_toolkit="GMAIL",
            selected_account_id="acct-1",
            selected_account_alias="Work",
            source="sidebar",
        )
        db = FakeSession({
            self.repo.WorkspaceConnectorPreferenceModel: FakeQuery(first_result=existing),
        })

        row = self.repo.upsert_workspace_connector_preference(
            db,
            "ws-1",
            mode="manual",
            selected_toolkit="HUBSPOT",
            selected_account_id="acct-2",
            selected_account_alias="Sales",
            source="chat_input",
        )

        self.assertEqual(row.selected_toolkit, "HUBSPOT")
        self.assertEqual(row.selected_account_id, "acct-2")
        self.assertEqual(row.selected_account_alias, "Sales")
        self.assertEqual(row.source, "chat_input")
