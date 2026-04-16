import unittest

from ui.connector_state import build_connector_context, default_connector_context, ensure_connector_state, set_connector_selection


class ConnectorStateTests(unittest.TestCase):
    def test_default_connector_context_starts_in_auto_mode(self):
        context = default_connector_context()

        self.assertEqual(context["mode"], "auto")
        self.assertEqual(context["selected_toolkit"], "")
        self.assertFalse(context["enforce_toolkit"])

    def test_set_connector_selection_updates_manual_toolkit_and_account(self):
        state = {}
        ensure_connector_state(state)

        set_connector_selection(
            state,
            mode="manual",
            selected_toolkit="hubspot",
            selected_account_id="acct-1",
            selected_account_alias="Work HubSpot",
        )

        context = build_connector_context(state)
        self.assertEqual(context["mode"], "manual")
        self.assertEqual(context["selected_toolkit"], "HUBSPOT")
        self.assertEqual(context["selected_account_id"], "acct-1")
        self.assertEqual(context["selected_account_alias"], "Work HubSpot")
        self.assertTrue(context["enforce_toolkit"])
        self.assertTrue(context["enforce_account"])

    def test_set_connector_selection_resets_to_auto_cleanly(self):
        state = {"connector_context": {"mode": "manual", "selected_toolkit": "GMAIL"}}

        set_connector_selection(state, mode="auto")

        context = build_connector_context(state)
        self.assertEqual(context, default_connector_context())
