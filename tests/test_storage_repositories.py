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
        ["workspace_id", "updated_at", "scope_type", "user_id", "membership_id", "selected_by_user_id"],
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
            "updated_at",
            "resume_token",
            "pending_kind",
            "approved",
            "approved_at",
            "context_json",
            "idempotency_key",
            "approval_requirement_json",
            "conversation_id",
            "expires_at",
            "id",
        ],
    )
    ToolConnectionModel = make_model_class(
        "ToolConnectionModel",
        [
            "workspace_id",
            "user_id",
            "toolkit",
            "status",
            "connected_account_id",
            "is_default",
            "updated_at",
            "metadata_json",
            "account_label",
            "last_verified_at",
            "last_seen_remote_at",
            "revoked_at",
            "status_reason",
            "status_updated_at",
        ],
    )
    ToolIdempotencyRecordModel = make_model_class(
        "ToolIdempotencyRecordModel",
        [
            "workspace_id",
            "tool_name",
            "idempotency_key",
            "status",
            "pending_request_id",
            "tool_call_log_id",
            "input_hash",
            "output_json",
            "error_message",
            "updated_at",
            "completed_at",
            "id",
        ],
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
        "models.tool_connections": make_module(
            "models.tool_connections",
            ToolConnectionModel=ToolConnectionModel,
        ),
        "models.tool_idempotency_records": make_module(
            "models.tool_idempotency_records",
            ToolIdempotencyRecordModel=ToolIdempotencyRecordModel,
        ),
    }
    stubs["sqlalchemy"].orm = stubs["sqlalchemy.orm"]
    repo = import_fresh("storage.repositories", stubs)
    repo._pending_tool_request_model = PendingToolRequestModel
    repo._tool_connection_model = ToolConnectionModel
    repo._tool_idempotency_model = ToolIdempotencyRecordModel
    return repo


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = load_repositories_module()

    def test_list_workspaces_falls_back_when_owner_user_column_is_missing(self):
        class ExplodingQuery:
            def order_by(self, *args):
                return self

            def all(self):
                raise RuntimeError("column workspaces.owner_user_id does not exist")

        class FakeSelect:
            def with_only_columns(self, *args):
                return self

            def order_by(self, *args):
                return self

        class FakeTable:
            c = SimpleNamespace(id="id", name="name", created_at="created_at")

            def select(self):
                return FakeSelect()

        class FakeMappings:
            def all(self):
                return [{"id": "ws-1", "name": "Acme", "created_at": "2026-01-01"}]

        class FakeExecuteResult:
            def mappings(self):
                return FakeMappings()

        class LegacyWorkspaceSession(FakeSession):
            def query(self, model):
                return ExplodingQuery()

            def execute(self, stmt):
                return FakeExecuteResult()

        db = LegacyWorkspaceSession()
        with patch_attr(self.repo, "_get_reflected_table", lambda db, name: FakeTable()):
            workspaces = self.repo.list_workspaces(db)

        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0].id, "ws-1")
        self.assertEqual(workspaces[0].name, "Acme")
        self.assertEqual(db.rollbacks, 1)

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

    def test_save_conversation_accepts_request_metadata_without_breaking_compatibility(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "c-1"):
            conv = self.repo.save_conversation(
                db,
                "ws-1",
                "assistant",
                "hi",
                "hello",
                request_id="req-1",
                metadata_json={"mode": "single"},
            )

        self.assertEqual(conv.request_id, "req-1")
        self.assertEqual(conv.metadata_json, {"mode": "single"})

    def test_save_workflow_run_persists_steps_and_final_output(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "wf-1"):
            run = self.repo.save_workflow_run(db, "ws-1", "email_triage", [{"step": "one"}], "done")

        self.assertEqual(run.workflow_name, "email_triage")
        self.assertEqual(run.steps, [{"step": "one"}])
        self.assertEqual(run.final_output, "done")

    def test_save_workflow_run_accepts_status_and_request_metadata(self):
        db = FakeSession()
        with patch_attr(self.repo, "_id", lambda: "wf-1"):
            run = self.repo.save_workflow_run(
                db,
                "ws-1",
                "email_triage",
                [{"step": "one"}],
                "paused",
                status="paused",
                request_id="req-1",
                metadata_json={"resume_token": "resume-1"},
            )

        self.assertEqual(run.status, "paused")
        self.assertEqual(run.request_id, "req-1")
        self.assertEqual(run.metadata_json, {"resume_token": "resume-1"})

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

    def test_save_pending_tool_request_sets_expiry_and_reuses_existing_rows(self):
        existing = self.repo._pending_tool_request_model(
            workspace_id="ws-1",
            agent_key="assistant",
            original_input="send it",
            requested_tool="GMAIL_SEND_EMAIL",
            requested_toolkit="GMAIL",
            pending_kind="approval",
            status="pending",
            resume_token="old-token",
            context_json={},
            idempotency_key="idem-1",
            approved=False,
            approval_requirement_json={},
            conversation_id="",
        )
        db = FakeSession({
            self.repo._pending_tool_request_model: FakeQuery(first_result=existing),
        })

        row = self.repo.save_pending_tool_request(
            db,
            "ws-1",
            agent_key="assistant",
            original_input="send it",
            requested_tool="GMAIL_SEND_EMAIL",
            requested_toolkit="GMAIL",
            resume_token="new-token",
            pending_kind="approval",
            context_json={"workflow_key": "email_triage"},
            idempotency_key="idem-1",
            approval_requirement_json={"required": True},
        )

        self.assertEqual(row.resume_token, "new-token")
        self.assertEqual(row.context_json, {"workflow_key": "email_triage"})
        self.assertEqual(row.idempotency_key, "idem-1")
        self.assertIsNotNone(row.expires_at)

    def test_transition_pending_tool_request_updates_status_conditionally(self):
        row = self.repo._pending_tool_request_model(
            workspace_id="ws-1",
            status="pending",
            resume_token="resume-1",
            context_json={},
            pending_kind="auth",
            approved=False,
        )
        db = FakeSession({
            self.repo._pending_tool_request_model: FakeQuery(first_result=row),
        })

        updated = self.repo.transition_pending_tool_request(
            db,
            "resume-1",
            to_status="completed",
            allowed_statuses=("pending", "resumed"),
        )

        self.assertEqual(updated.status, "completed")

    def test_approve_pending_tool_request_marks_row_and_context(self):
        row = self.repo._pending_tool_request_model(
            workspace_id="ws-1",
            status="pending",
            resume_token="resume-1",
            context_json={},
            idempotency_key="idem-1",
            pending_kind="approval",
            approved=False,
        )
        db = FakeSession({
            self.repo._pending_tool_request_model: FakeQuery(first_result=row),
        })

        updated = self.repo.approve_pending_tool_request(db, "resume-1")

        self.assertTrue(updated.approved)
        self.assertIn("idem-1", updated.context_json["approved_idempotency_keys"])

    def test_claim_and_update_tool_idempotency_record_are_backward_compatible(self):
        query = FakeQuery(first_result=None)
        db = FakeSession({
            self.repo._tool_idempotency_model: query,
        })
        with patch_attr(self.repo, "_id", lambda: "idem-row"):
            row = self.repo.claim_tool_idempotency_record(
                db,
                "ws-1",
                "GMAIL_SEND_EMAIL",
                "idem-1",
                input_hash="hash-1",
                status="in_progress",
            )

        self.assertEqual(row.id, "idem-row")
        self.assertEqual(row.status, "in_progress")
        query.first_result = row
        updated = self.repo.update_tool_idempotency_record(
            db,
            "ws-1",
            "GMAIL_SEND_EMAIL",
            "idem-1",
            status="success",
            output_json={"ok": True},
            completed=True,
        )
        self.assertEqual(updated.status, "success")
        self.assertEqual(updated.output_json, {"ok": True})
