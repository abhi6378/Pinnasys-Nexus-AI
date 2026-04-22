import unittest


class BackendOwnershipSourceTests(unittest.TestCase):
    def setUp(self):
        with open("api/routes.py", "r", encoding="utf-8") as handle:
            self.routes_source = handle.read()
        with open("models/contracts.py", "r", encoding="utf-8") as handle:
            self.contracts_source = handle.read()
        with open("tools/connector_service.py", "r", encoding="utf-8") as handle:
            self.connector_service_source = handle.read()
        with open("tools/tool_executor.py", "r", encoding="utf-8") as handle:
            self.tool_executor_source = handle.read()
        with open("alembic/versions/20260423_01_membership_runtime_context.py", "r", encoding="utf-8") as handle:
            self.migration_source = handle.read()

    def test_runtime_actor_context_and_api_membership_propagation_are_declared(self):
        self.assertIn("class RuntimeActorContext:", self.contracts_source)
        self.assertIn("membership_id: str = \"\"", self.contracts_source)
        self.assertIn("membership_id=actor.membership_id", self.routes_source)
        self.assertIn("membership_role", self.routes_source)

    def test_connector_preference_api_is_typed_and_scope_aware(self):
        self.assertIn('"/workspace/{workspace_id}/connector-preference"', self.routes_source)
        self.assertIn("ConnectorPreferenceResponse", self.routes_source)
        self.assertIn("ConnectorPreferenceUpdateRequest", self.routes_source)
        self.assertIn("resolve_persisted_connector_preference", self.connector_service_source)
        self.assertIn('"winning_scope"', self.connector_service_source)

    def test_runtime_control_plane_tables_gain_nullable_membership_context(self):
        for table in (
            "conversations",
            "workflow_runs",
            "pending_tool_requests",
            "tool_call_logs",
            "tool_idempotency_records",
        ):
            self.assertIn(table, self.migration_source)
        self.assertIn("ADD COLUMN IF NOT EXISTS membership_id", self.migration_source)
        self.assertIn("ON DELETE SET NULL", self.migration_source)
        self.assertIn("NOT VALID", self.migration_source)

    def test_tool_execution_audit_paths_persist_membership_context(self):
        self.assertIn("membership_id = str((context_json or {}).get(\"membership_id\"", self.tool_executor_source)
        self.assertIn("membership_id=membership_id", self.tool_executor_source)

    def test_streamlit_google_login_or_handoff_was_not_introduced(self):
        self.assertNotIn("/auth/streamlit/start", self.routes_source)
        self.assertNotIn("/auth/streamlit/exchange", self.routes_source)
        self.assertNotIn("auth_handoff_codes", self.routes_source)


if __name__ == "__main__":
    unittest.main()
