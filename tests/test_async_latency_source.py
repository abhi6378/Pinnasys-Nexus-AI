import ast
import unittest
from pathlib import Path


class AsyncLatencySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.api_source = (cls.root / "api" / "routes.py").read_text(encoding="utf-8")
        cls.composio_source = (cls.root / "tools" / "composio_client.py").read_text(encoding="utf-8")
        cls.connector_source = (cls.root / "tools" / "connector_service.py").read_text(encoding="utf-8")
        cls.executor_source = (cls.root / "helpers" / "executor.py").read_text(encoding="utf-8")
        cls.worker_source = (cls.root / "automation" / "worker.py").read_text(encoding="utf-8")
        cls.scheduler_source = (cls.root / "automation" / "scheduler.py").read_text(encoding="utf-8")

    def test_chat_route_remains_sync(self):
        tree = ast.parse(self.api_source)
        chat_defs = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "api_chat"]
        async_chat_defs = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "api_chat"]
        self.assertEqual(len(chat_defs), 1)
        self.assertEqual(async_chat_defs, [])

    def test_composio_exposes_async_thread_offload_wrappers(self):
        self.assertIn("async def async_list_connected_accounts", self.composio_source)
        self.assertIn("async def async_get_connect_link", self.composio_source)
        self.assertIn("async def async_get_tool_schemas", self.composio_source)
        self.assertIn("async def async_validate_tool_slug", self.composio_source)
        self.assertIn("async def async_execute_tool", self.composio_source)
        self.assertIn("asyncio.to_thread", self.composio_source)

    def test_composio_has_cache_and_observability_hooks(self):
        self.assertIn("_auth_config_cache", self.composio_source)
        self.assertIn("COMPOSIO_AUTH_CONFIG_CACHE_TTL_SECONDS", self.composio_source)
        self.assertIn("composio.schemas.fetch", self.composio_source)
        self.assertIn("composio.catalog.cache_hit", self.composio_source)
        self.assertIn("composio.tool.execute", self.composio_source)

    def test_connector_service_uses_request_cache_for_workspace_list(self):
        self.assertIn("def _ensure_request_cache", self.connector_source)
        self.assertIn("workspace_connectors:", self.connector_source)
        self.assertIn("connector.accounts.refresh_decision", self.connector_source)

    def test_agent_tool_loop_has_request_scoped_schema_cache(self):
        self.assertIn('execution_context.setdefault("prompt_tool_schema_cache"', self.executor_source)
        self.assertIn("agent.schema_cache", self.executor_source)

    def test_worker_and_scheduler_emit_duration_logs(self):
        self.assertIn("automation.worker.run", self.worker_source)
        self.assertIn("automation.worker.batch", self.worker_source)
        self.assertIn("duration_ms=elapsed_ms", self.worker_source)
        self.assertIn("automation.scheduler.enqueue_due", self.scheduler_source)
        self.assertIn("duration_ms=elapsed_ms", self.scheduler_source)

    def test_docs_capture_sync_async_boundary(self):
        self.assertTrue((self.root / "docs" / "async_latency_boundary.md").exists())


if __name__ == "__main__":
    unittest.main()
