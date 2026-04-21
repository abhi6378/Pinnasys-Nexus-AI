import unittest
from pathlib import Path


class AutomationSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.model_source = (cls.root / "models" / "scheduled_tasks.py").read_text(encoding="utf-8")
        cls.migration_source = (
            cls.root / "alembic" / "versions" / "20260422_01_scheduled_automations.py"
        ).read_text(encoding="utf-8")
        cls.routes_source = (cls.root / "api" / "routes.py").read_text(encoding="utf-8")
        cls.worker_source = (cls.root / "automation" / "worker.py").read_text(encoding="utf-8")
        cls.service_source = (cls.root / "automation" / "service.py").read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.sidebar_source = (cls.root / "ui" / "sidebar.py").read_text(encoding="utf-8")

    def test_models_define_durable_task_and_run_tables(self):
        self.assertIn('class ScheduledTaskModel(Base):', self.model_source)
        self.assertIn('class ScheduledTaskRunModel(Base):', self.model_source)
        self.assertIn('workspace_id = Column(String, ForeignKey("workspaces.id"', self.model_source)
        self.assertIn('actor_user_id = Column(String, ForeignKey("users.id"', self.model_source)
        self.assertIn('membership_id = Column(String, ForeignKey("workspace_memberships.id"', self.model_source)
        self.assertIn('Index("ix_scheduled_tasks_due"', self.model_source)
        self.assertIn('Index("uq_scheduled_task_runs_run_key"', self.model_source)

    def test_migration_is_additive_and_reversible(self):
        self.assertIn('revision = "20260422_01"', self.migration_source)
        self.assertIn('down_revision = "20260417_04"', self.migration_source)
        self.assertIn('op.create_table(', self.migration_source)
        self.assertIn('"scheduled_tasks"', self.migration_source)
        self.assertIn('"scheduled_task_runs"', self.migration_source)
        self.assertIn('op.drop_table("scheduled_task_runs")', self.migration_source)
        self.assertIn('op.drop_table("scheduled_tasks")', self.migration_source)

    def test_api_routes_expose_automation_management(self):
        self.assertIn('/workspace/{workspace_id}/automations', self.routes_source)
        self.assertIn('class AutomationCreateRequest(BaseModel):', self.routes_source)
        self.assertIn('automation_service.create_schedule', self.routes_source)
        self.assertIn('automation_service.run_now', self.routes_source)
        self.assertIn('automation_service.complete_run_from_resume', self.routes_source)

    def test_worker_reuses_orchestrator_and_preserves_context(self):
        self.assertIn('handle_request(', self.worker_source)
        self.assertIn('force_workflow=force_workflow or None', self.worker_source)
        self.assertIn('"scheduled_run_id"', self.worker_source)
        self.assertIn('"idempotency_key"', self.worker_source)
        self.assertIn('status = "approval_required"', self.worker_source)

    def test_service_has_scheduler_lifecycle_helpers(self):
        self.assertIn('def enqueue_due_runs', self.service_source)
        self.assertIn('def run_now', self.service_source)
        self.assertIn('def pause_schedule', self.service_source)
        self.assertIn('def resume_schedule', self.service_source)
        self.assertIn('def cancel_schedule', self.service_source)

    def test_streamlit_page_is_wired(self):
        self.assertIn('render_automations', self.app_source)
        self.assertIn('"automations"', self.sidebar_source)
        self.assertTrue((self.root / "ui" / "pages" / "automations_page.py").exists())


if __name__ == "__main__":
    unittest.main()
