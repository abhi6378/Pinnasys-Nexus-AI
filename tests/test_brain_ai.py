import unittest
from types import SimpleNamespace

from tests.support import Spy, import_fresh, make_module, make_sqlalchemy_stubs, patch_attr


def load_brain_ai_module():
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["storage.repositories"] = make_module(
        "storage.repositories",
        get_brain=lambda *args, **kwargs: None,
        update_brain=lambda *args, **kwargs: None,
        get_knowledge=lambda *args, **kwargs: [],
        add_knowledge=lambda *args, **kwargs: SimpleNamespace(id="k-1"),
        get_working_memory=lambda *args, **kwargs: None,
        list_memory_records=lambda *args, **kwargs: [],
        search_memory_records=lambda *args, **kwargs: [],
        get_memory_records_by_ids=lambda *args, **kwargs: [],
        list_memory_embeddings=lambda *args, **kwargs: [],
        upsert_memory_record=lambda *args, **kwargs: None,
    )
    return import_fresh("brain.brain_ai", stubs)


class BrainAITests(unittest.TestCase):
    def setUp(self):
        self.brain_ai = load_brain_ai_module()

    def test_get_relevant_context_builds_hybrid_memory_pack_without_embeddings(self):
        brain = SimpleNamespace(
            company_name="Acme",
            brand_context="We sell workflow automation.",
            tone="Concise",
            audience="Operators",
            goals="Increase qualified pipeline",
            services="Automation consulting",
            pricing="Custom",
            competitors="Other SaaS tools",
            support_style="Fast",
        )
        working = SimpleNamespace(
            current_goal="Prepare launch plan",
            active_tasks=["draft sequence"],
            open_questions=["who approves copy?"],
            current_draft_summary="Initial draft exists.",
            recent_tool_summary="Verified Gmail action succeeded.",
            latest_workflow_summary="lead_capture workflow completed.",
            project_focus="Q2 launch",
            state_json={},
        )
        memory_records = [
            SimpleNamespace(
                id="m-1",
                memory_type="preference",
                title="Email preference",
                summary="Prefers draft before send.",
                content="Prefers draft before send.",
                tags=["email"],
                tool_tags=[],
                entity_tags=[],
                importance_score=0.9,
                confidence_score=0.9,
                pinned=True,
                updated_at=None,
                created_at=None,
            )
        ]
        with patch_attr(self.brain_ai.repo, "get_brain", Spy(return_value=brain)), \
             patch_attr(self.brain_ai.repo, "get_working_memory", Spy(return_value=working)), \
             patch_attr(self.brain_ai.repo, "list_memory_records", Spy(return_value=memory_records)), \
             patch_attr(self.brain_ai.repo, "search_memory_records", Spy(return_value=memory_records)), \
             patch_attr(self.brain_ai.repo, "list_memory_embeddings", Spy(return_value=[])), \
             patch_attr(self.brain_ai.repo, "get_knowledge", Spy(return_value=[SimpleNamespace(title="Legacy", content="Older note", type="text")])), \
             patch_attr(self.brain_ai, "get_embedding_service", Spy(return_value=type("Svc", (), {"model_name": "none", "embed_text": lambda self, text: None})())):
            context = self.brain_ai.BrainAI("ws1", object()).get_relevant_context("email launch", limit=4)

        self.assertIn("=== BUSINESS PROFILE ===", context)
        self.assertIn("=== WORKING MEMORY ===", context)
        self.assertIn("=== KEY MEMORIES ===", context)
        self.assertIn("Prefers draft before send.", context)
        self.assertIn("=== LEGACY KNOWLEDGE ===", context)

    def test_save_to_knowledge_preserves_legacy_behavior_and_bridges_memory(self):
        add_knowledge = Spy(return_value=SimpleNamespace(id="k-1"))
        upsert_memory_record = Spy()
        with patch_attr(self.brain_ai.repo, "add_knowledge", add_knowledge), \
             patch_attr(self.brain_ai.repo, "upsert_memory_record", upsert_memory_record):
            self.brain_ai.BrainAI("ws1", object()).save_to_knowledge("Pricing", "Starts at $99", tags=["pricing"])

        self.assertEqual(len(add_knowledge.calls), 1)
        self.assertEqual(len(upsert_memory_record.calls), 1)
        self.assertEqual(upsert_memory_record.calls[0][1]["memory_type"], "semantic_fact")
