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
            self.memory_extractor.extract_and_save("ws1", "content", object())

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

    def test_extract_and_save_swallows_failures(self):
        # Characterization: extraction is best-effort and intentionally suppresses exceptions.
        log_exception = Spy()
        with patch_attr(self.memory_extractor, "generate_json", Spy(side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))), \
             patch_attr(self.memory_extractor, "log_exception", log_exception):
            self.memory_extractor.extract_and_save("ws1", "content", object())

        self.assertEqual(len(log_exception.calls), 1)
        self.assertEqual(log_exception.calls[0][0][1], "memory.extract.failed")
