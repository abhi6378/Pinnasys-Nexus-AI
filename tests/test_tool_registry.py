import unittest

from tools.tool_registry import (
    build_prompt_tool_catalog,
    get_tool,
    get_tool_approval_requirement,
    get_tool_idempotency_fields,
    get_tool_metadata_gaps,
    get_toolkit_runtime_config,
    validate_tool_input,
)


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

    def test_write_tool_exposes_approval_and_idempotency_metadata(self):
        approval = get_tool_approval_requirement("GMAIL_SEND_EMAIL")

        self.assertTrue(approval.required)
        self.assertEqual(approval.risk_level, "high")
        self.assertEqual(approval.mode, "confirm_or_explicit_execute")
        self.assertEqual(
            get_tool_idempotency_fields("GMAIL_SEND_EMAIL"),
            ("recipient_email", "subject", "body"),
        )

    def test_validate_tool_input_normalizes_aliases_and_validates_types(self):
        normalized, errors = validate_tool_input(
            "GMAIL_SEND_EMAIL",
            {"to": "user@example.com", "body": "", "subject": "hello"},
        )

        self.assertEqual(normalized["recipient_email"], "user@example.com")
        self.assertIn("must not be empty", " ".join(errors))

    def test_validate_tool_input_coerces_integer_strings(self):
        normalized, errors = validate_tool_input(
            "TAVILY_SEARCH",
            {"query": "ai systems", "max_results": "5"},
        )

        self.assertEqual(errors, [])
        self.assertEqual(normalized["max_results"], 5)

    def test_toolkit_runtime_config_centralizes_connector_behavior(self):
        config = get_toolkit_runtime_config("TAVILY")

        self.assertEqual(config["connection_mode"], "custom_key")
        self.assertEqual(config["schema_source"], "composio_live")
