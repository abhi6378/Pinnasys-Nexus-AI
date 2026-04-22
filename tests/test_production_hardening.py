import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from tests.support import patch_attr


@contextmanager
def patch_env(updates):
    previous = {key: os.environ.get(key) for key in updates}
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ProductionHardeningTests(unittest.TestCase):
    root = Path(__file__).resolve().parent.parent

    def test_connector_context_serializes_only_connector_fields(self):
        source = (self.root / "api" / "routes.py").read_text(encoding="utf-8")
        method_source = source.split("def to_connector_dict", 1)[1].split("class ConnectorPreferenceUpdateRequest", 1)[0]
        self.assertIn('"selected_toolkit": self.selected_toolkit', method_source)
        self.assertIn('"selected_account_id": self.selected_account_id', method_source)
        self.assertNotIn("workspace_pairs", method_source)
        self.assertNotIn("default_membership", method_source)

    def test_auth_google_response_shapes_default_membership(self):
        source = (self.root / "api" / "routes.py").read_text(encoding="utf-8")
        auth_source = source.split("def api_auth_google", 1)[1].split("@app.get(\"/auth/me\"", 1)[0]
        self.assertIn("default_membership = next(", auth_source)
        self.assertLess(
            auth_source.index("default_membership = next("),
            auth_source.index('"default_workspace": _workspace_payload(workspace, default_membership)'),
        )

    def test_auth_required_rejects_wildcard_cors(self):
        from utils.runtime_config import validate_cors_config

        with patch_env({"SINTRA_AUTH_REQUIRED": "1", "SINTRA_ALLOW_INSECURE_DEV_AUTH": "0"}):
            with self.assertRaises(RuntimeError):
                validate_cors_config(["*"])

    def test_auth_required_requires_strong_session_secret(self):
        from utils.runtime_config import validate_session_config

        with patch_env(
            {
                "SINTRA_AUTH_REQUIRED": "1",
                "SINTRA_SESSION_SECRET": "replace-with-a-long-random-secret",
                "SINTRA_SESSION_COOKIE_SECURE": "true",
            }
        ):
            with self.assertRaises(RuntimeError):
                validate_session_config()

    def test_strict_google_csrf_requires_both_tokens(self):
        from auth.service import AuthServiceError, validate_google_csrf

        request = SimpleNamespace(cookies={})
        with self.assertRaises(AuthServiceError):
            validate_google_csrf(request, "", strict=True)

    def test_logging_sanitizer_redacts_nested_secret_fields(self):
        from utils.logging_utils import sanitize_for_persistence

        payload = sanitize_for_persistence(
            {
                "safe": "ok",
                "nested": {
                    "resume_token": "raw-token",
                    "connect_url": "https://connect",
                    "value": "kept",
                },
            }
        )

        self.assertEqual(payload["safe"], "ok")
        self.assertEqual(payload["nested"]["resume_token"], "[redacted]")
        self.assertEqual(payload["nested"]["connect_url"], "[redacted]")
        self.assertEqual(payload["nested"]["value"], "kept")

    def test_composio_version_bypass_is_flag_gated(self):
        import tools.composio_client as client_module

        class FakeTools:
            def __init__(self):
                self.kwargs = None

            def execute(self, slug, arguments, **kwargs):
                self.kwargs = kwargs
                return {"ok": True}

        fake_tools = FakeTools()
        fake_client = SimpleNamespace(tools=fake_tools)

        with patch_attr(client_module, "_get_client", lambda user_id: fake_client), \
             patch_attr(client_module, "_resolve_action", lambda tool_name: tool_name), \
             patch_env({"COMPOSIO_ALLOW_VERSION_CHECK_BYPASS": "0"}):
            client_module.execute_tool("ws-1", "GMAIL_FETCH_EMAILS", {}, connected_account_id="acct-1")

        self.assertNotIn("dangerously_skip_version_check", fake_tools.kwargs)
        self.assertEqual(fake_tools.kwargs["connected_account_id"], "acct-1")

        with patch_attr(client_module, "_get_client", lambda user_id: fake_client), \
             patch_attr(client_module, "_resolve_action", lambda tool_name: tool_name), \
             patch_env({"COMPOSIO_ALLOW_VERSION_CHECK_BYPASS": "1"}):
            client_module.execute_tool("ws-1", "GMAIL_FETCH_EMAILS", {}, connected_account_id="acct-1")

        self.assertTrue(fake_tools.kwargs["dangerously_skip_version_check"])

    def test_canonical_schema_fallback_blocked_in_auth_required_mode(self):
        source = (self.root / "storage" / "repositories.py").read_text(encoding="utf-8")
        self.assertIn("_CANONICAL_SCHEMA_TABLES", source)
        self.assertIn("legacy_schema_fallback_allowed(table_name)", source)
        self.assertIn("storage.legacy_schema_fallback_blocked", source)


if __name__ == "__main__":
    unittest.main()
