import unittest

from tests.support import Spy, patch_attr


class ComposioClientTests(unittest.TestCase):
    def setUp(self):
        import tools.composio_client as composio_client

        self.client = composio_client
        self.client._tool_version_cache.clear()

    def test_validate_tool_slug_uses_raw_catalog_schema(self):
        raw_lookup = Spy(return_value={"slug": "GMAIL_FETCH_EMAILS"})
        scoped_lookup = Spy(return_value=[])

        with patch_attr(self.client, "is_available", lambda: True), \
             patch_attr(self.client, "_get_raw_tool_schema", raw_lookup), \
             patch_attr(self.client, "get_tool_schemas", scoped_lookup):
            result = self.client.validate_tool_slug("GMAIL_FETCH_EMAILS")

        self.assertTrue(result["available"])
        self.assertTrue(result["exists"])
        self.assertEqual(len(raw_lookup.calls), 1)
        self.assertEqual(len(scoped_lookup.calls), 0)

    def test_get_tool_schemas_catalog_mode_uses_raw_lookup(self):
        raw_lookup = Spy(side_effect=lambda tool_name: {"slug": tool_name})
        default_client = Spy(side_effect=AssertionError("scoped client fetch should not be used for catalog mode"))

        with patch_attr(self.client, "is_available", lambda: True), \
             patch_attr(self.client, "_get_raw_tool_schema", raw_lookup), \
             patch_attr(self.client, "_get_client", default_client):
            result = self.client.get_tool_schemas("__catalog__", ["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL"])

        self.assertEqual([item["slug"] for item in result], ["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL"])
        self.assertEqual(len(raw_lookup.calls), 2)
        self.assertEqual(len(default_client.calls), 0)

    def test_get_live_tool_schema_catalog_mode_uses_raw_lookup(self):
        raw_lookup = Spy(return_value={"slug": "GMAIL_FETCH_EMAILS", "inputParameters": {}})

        with patch_attr(self.client, "_get_raw_tool_schema", raw_lookup):
            result = self.client.get_live_tool_schema("__catalog__", "GMAIL_FETCH_EMAILS")

        self.assertEqual(result["slug"], "GMAIL_FETCH_EMAILS")
        self.assertEqual(len(raw_lookup.calls), 1)

    def test_execute_tool_passes_resolved_specific_version(self):
        class FakeTools:
            def __init__(self):
                self.kwargs = None

            def execute(self, slug, arguments, **kwargs):
                self.kwargs = kwargs
                return {"ok": True}

        fake_tools = FakeTools()
        fake_client = type("Client", (), {"tools": fake_tools})()

        with patch_attr(self.client, "_get_client", lambda user_id: fake_client), \
             patch_attr(self.client, "_resolve_action", lambda tool_name: tool_name), \
             patch_attr(self.client, "_resolve_tool_version", lambda tool_name, toolkit="": "20260413_01"), \
             patch_attr(self.client, "get_tool", lambda tool_name: {"toolkit": "GMAIL"}), \
             patch_attr(self.client, "allow_composio_version_check_bypass", lambda: False):
            self.client.execute_tool("ws-1", "GMAIL_FETCH_EMAILS", {}, connected_account_id="acct-1")

        self.assertEqual(fake_tools.kwargs["version"], "20260413_01")
        self.assertEqual(fake_tools.kwargs["connected_account_id"], "acct-1")

    def test_resolve_tool_version_uses_available_versions_fallback(self):
        with patch_attr(self.client, "_get_raw_tool_schema", lambda tool_name: {"available_versions": ["latest", "20260424_01"]}), \
             patch_attr(self.client, "get_tool", lambda tool_name: {"toolkit": "GMAIL"}):
            version = self.client._resolve_tool_version("GMAIL_FETCH_EMAILS", "GMAIL")

        self.assertEqual(version, "20260424_01")


if __name__ == "__main__":
    unittest.main()
