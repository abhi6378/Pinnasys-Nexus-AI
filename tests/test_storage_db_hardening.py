import unittest
from pathlib import Path


class StorageDbHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.storage_db_source = (cls.root / "storage" / "db.py").read_text(encoding="utf-8")
        cls.tool_connections_source = (cls.root / "models" / "tool_connections.py").read_text(encoding="utf-8")
        cls.pending_requests_source = (cls.root / "models" / "pending_tool_requests.py").read_text(encoding="utf-8")
        cls.tool_logs_source = (cls.root / "models" / "tool_call_logs.py").read_text(encoding="utf-8")
        cls.tool_idempotency_source = (cls.root / "models" / "tool_idempotency_records.py").read_text(encoding="utf-8")

    def test_alembic_files_exist(self):
        self.assertTrue((self.root / "alembic.ini").exists())
        self.assertTrue((self.root / "alembic" / "env.py").exists())
        self.assertTrue((self.root / "alembic" / "versions" / "20260417_01_baseline.py").exists())
        self.assertTrue((self.root / "alembic" / "versions" / "20260417_02_db_hardening.py").exists())

    def test_storage_db_uses_timezone_aware_columns_and_migration_head_check(self):
        self.assertIn("TZDateTime = DateTime(timezone=True)", self.storage_db_source)
        self.assertIn("def _get_alembic_head_revision()", self.storage_db_source)
        self.assertIn("20260417_02", (self.root / "alembic" / "versions" / "20260417_02_db_hardening.py").read_text(encoding="utf-8"))
        self.assertNotIn("_ensure_additive_connector_columns", self.storage_db_source)

    def test_conversation_and_workflow_models_include_request_metadata(self):
        self.assertIn('request_id = Column(String, default="", index=True)', self.storage_db_source)
        self.assertIn("metadata_json = Column(JSON, default=dict)", self.storage_db_source)
        self.assertIn('status = Column(String, nullable=False, default="completed")', self.storage_db_source)
        self.assertIn('updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)', self.storage_db_source)

    def test_auth_ready_tables_are_declared(self):
        self.assertIn("class UserModel(Base):", self.storage_db_source)
        self.assertIn("class ExternalIdentityModel(Base):", self.storage_db_source)
        self.assertIn("class WorkspaceMembershipModel(Base):", self.storage_db_source)
        self.assertIn('owner_user_id = Column(String, ForeignKey("users.id")', self.storage_db_source)

    def test_tool_connection_model_declares_uniqueness_and_status_checks(self):
        self.assertIn("uq_tool_connections_workspace_toolkit_account", self.tool_connections_source)
        self.assertIn("uq_tool_connections_single_default_active", self.tool_connections_source)
        self.assertIn("ck_tool_connections_status", self.tool_connections_source)
        self.assertIn("ck_tool_connections_auth_mode", self.tool_connections_source)

    def test_pending_requests_logs_and_idempotency_models_have_control_plane_guarantees(self):
        self.assertIn("expires_at = Column(TZDateTime, nullable=True, index=True)", self.pending_requests_source)
        self.assertIn("ck_pending_tool_requests_status", self.pending_requests_source)
        self.assertIn("ck_pending_tool_requests_pending_kind", self.pending_requests_source)
        self.assertIn("ck_tool_call_logs_status", self.tool_logs_source)
        self.assertIn("uq_tool_idempotency_workspace_tool_key", self.tool_idempotency_source)
        self.assertIn("ck_tool_idempotency_records_status", self.tool_idempotency_source)
