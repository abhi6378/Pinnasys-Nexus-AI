import unittest

from tools.tool_registry import build_prompt_tool_catalog, get_tool, get_tool_metadata_gaps


class ToolRegistryTests(unittest.TestCase):
    def test_tool_metadata_is_present_in_canonical_registry(self):
        tool = get_tool("GMAIL_SEND_EMAIL")

        self.assertIsNotNone(tool)
        self.assertIn("description", tool)
        self.assertIn("auth_requirement", tool)
        self.assertIn("schema", tool)
        self.assertIn("capability_groups", tool)

    def test_registry_validation_reports_no_gaps_for_current_tools(self):
        self.assertEqual(get_tool_metadata_gaps(), {})

    def test_prompt_catalog_is_generated_from_registry_entries(self):
        catalog = build_prompt_tool_catalog([
            {
                "tool_name": "TEST_TOOL",
                "toolkit": "GMAIL",
                "description": "Canonical description.",
                "schema": {"required": ["foo"]},
                "usage_notes": ("Use carefully.",),
            }
        ])

        self.assertIn("Canonical description.", catalog)
        self.assertIn("required_params: foo", catalog)
        self.assertIn("Use carefully.", catalog)
