import unittest

from helpers.agent_capabilities import AGENT_CAPABILITY_POLICIES
from helpers.agent_personas import PERSONAS
from helpers.configs import AGENTS


class HelperConfigSeparationTests(unittest.TestCase):
    def test_persona_data_remains_separate_from_capability_data(self):
        persona = PERSONAS["assistant"]
        capability = AGENT_CAPABILITY_POLICIES["assistant"]

        self.assertIn("communication_style", persona)
        self.assertNotIn("allowed_tools", persona)
        self.assertNotIn("tool_mode", persona)

        self.assertIn("tool_policy", capability)
        self.assertNotIn("tone", capability)
        self.assertNotIn("boundaries", capability)

    def test_merged_agents_preserve_legacy_compatibility_fields(self):
        assistant = AGENTS["assistant"]

        self.assertIn("tone", assistant)
        self.assertIn("allowed_tools", assistant)
        self.assertIn("tool_mode", assistant)
        self.assertIn("communication_style", assistant)
        self.assertEqual(assistant["output_format"], assistant["communication_style"])
