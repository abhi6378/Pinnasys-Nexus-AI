import unittest

from tests.support import Spy, import_fresh, make_module, make_sqlalchemy_stubs, patch_attr


def load_memory_extractor_module():
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["llm.client"] = make_module("llm.client", generate_json=lambda *args, **kwargs: "{}")
    stubs["storage.repositories"] = make_module(
        "storage.repositories",
        add_knowledge=lambda *args, **kwargs: None,
        update_brain=lambda *args, **kwargs: None,
        upsert_memory_record=lambda *args, **kwargs: None,
        upsert_working_memory=lambda *args, **kwargs: None,
        get_memory_embedding=lambda *args, **kwargs: None,
        upsert_memory_embedding=lambda *args, **kwargs: None,
    )
    return import_fresh("brain.memory_extractor", stubs)


class ExtractAndSaveTests(unittest.TestCase):
    def setUp(self):
        self.memory_extractor = load_memory_extractor_module()

    def test_extract_and_save_persists_facts_and_non_empty_profile_updates(self):
        add_knowledge = Spy()
        update_brain = Spy()
        with patch_attr(self.memory_extractor, "generate_json", Spy(return_value="""{
            "has_facts": true,
            "facts": [
                {"title": "Fact 1", "content": "Useful fact", "tags": ["alpha"]},
                {"title": "Fact 2", "content": "", "tags": ["empty"]}
            ],
            "profile_updates": {
                "company_name": "Acme",
                "tone": "",
                "audience": "Founders"
            }
        }""")), \
             patch_attr(self.memory_extractor.repo, "add_knowledge", add_knowledge), \
             patch_attr(self.memory_extractor.repo, "update_brain", update_brain):
            self.memory_extractor.extract_and_save(
                "ws1",
                "This conversation contains enough durable business context to extract useful memory.",
                object(),
                user_input="Our company is Acme and our audience is founders.",
            )

        self.assertEqual(len(add_knowledge.calls), 1)
        add_args, add_kwargs = add_knowledge.calls[0]
        self.assertEqual(add_args[1], "ws1")
        self.assertEqual(add_kwargs, {
            "type_": "text",
            "title": "Fact 1",
            "content": "Useful fact",
            "tags": ["alpha"],
        })
        self.assertEqual(len(update_brain.calls), 1)
        update_args, update_kwargs = update_brain.calls[0]
        self.assertEqual(update_args[1:], ("ws1", {"company_name": "Acme", "audience": "Founders"}))
        self.assertEqual(update_kwargs, {})

    def test_extract_and_save_updates_working_memory_and_sanitizes_tool_data(self):
        upsert_working_memory = Spy()
        upsert_memory_record = Spy(return_value=type("Stored", (), {"id": "mem-1"})())
        get_memory_embedding = Spy(return_value=None)
        upsert_memory_embedding = Spy()
        fake_embedding_service = type(
            "FakeEmbeddingService",
            (),
            {
                "model_name": "test-embed",
                "embed_text": lambda self, text: None,
            },
        )()
        with patch_attr(self.memory_extractor, "generate_json", Spy(return_value="""{
            "memory_worthy": true,
            "profile_updates": {},
            "memory_records": [
                {
                    "memory_type": "preference",
                    "title": "Email preference",
                    "content": "User prefers draft before send.",
                    "summary": "Prefers draft-before-send.",
                    "tags": ["email", "preference"],
                    "importance_score": 0.8,
                    "confidence_score": 0.9,
                    "canonical_key": "preference:email_draft"
                }
            ],
            "working_memory": {
                "current_goal": "Prepare outreach",
                "active_tasks": ["draft email"],
                "open_questions": ["who is the recipient?"]
            }
        }""")), \
             patch_attr(self.memory_extractor.repo, "upsert_working_memory", upsert_working_memory), \
             patch_attr(self.memory_extractor.repo, "upsert_memory_record", upsert_memory_record), \
             patch_attr(self.memory_extractor.repo, "get_memory_embedding", get_memory_embedding), \
             patch_attr(self.memory_extractor.repo, "upsert_memory_embedding", upsert_memory_embedding), \
             patch_attr(self.memory_extractor, "get_embedding_service", Spy(return_value=fake_embedding_service)):
            self.memory_extractor.extract_and_save(
                "ws1",
                "Completed a Gmail action and prepared the next outreach step with durable preferences.",
                object(),
                user_input="Draft the outreach and use Gmail if needed.",
                tool_used="GMAIL_SEND_EMAIL",
                tool_output={
                    "recipient_email": "user@example.com",
                    "Authorization": "Bearer abcdef",
                    "message_id": "123",
                },
            )

        self.assertEqual(len(upsert_working_memory.calls), 1)
        _, wm_kwargs = upsert_working_memory.calls[0]
        self.assertEqual(wm_kwargs["current_goal"], "Prepare outreach")
        self.assertEqual(wm_kwargs["active_tasks"], ["draft email"])
        self.assertGreaterEqual(len(upsert_memory_record.calls), 2)
        tool_call = upsert_memory_record.calls[0]
        self.assertEqual(tool_call[1]["memory_type"], "tool_outcome")
        self.assertNotIn("Authorization", str(tool_call[1]["metadata_json"]))

    def test_extract_and_save_swallows_failures(self):
        # Characterization: extraction is best-effort and intentionally suppresses exceptions.
        log_exception = Spy()
        with patch_attr(self.memory_extractor, "generate_json", Spy(side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))), \
             patch_attr(self.memory_extractor, "log_exception", log_exception):
            self.memory_extractor.extract_and_save(
                "ws1",
                "This is memory-worthy content that should trigger extraction and then fail.",
                object(),
                user_input="Remember this operating preference for later.",
            )

        self.assertEqual(len(log_exception.calls), 1)
        self.assertEqual(log_exception.calls[0][0][1], "memory.extract.failed")
