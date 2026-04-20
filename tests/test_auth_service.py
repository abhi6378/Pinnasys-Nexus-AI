import unittest
from types import SimpleNamespace
from contextlib import contextmanager
import os

from tests.support import Spy, patch_attr


@contextmanager
def patch_env(updates):
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        import auth.service as service

        self.service = service

    def test_sign_in_with_google_payload_creates_user_identity_workspace_and_session(self):
        user = SimpleNamespace(
            id="user-1",
            email="jane@example.com",
            display_name="Jane",
            avatar_url="https://avatar",
            status="active",
        )
        workspace = SimpleNamespace(id="ws-1", name="Jane Workspace", created_at="now")
        repo = SimpleNamespace(
            get_external_identity=Spy(return_value=None),
            get_user_by_email=Spy(return_value=None),
            upsert_user=Spy(return_value=user),
            upsert_external_identity=Spy(return_value=SimpleNamespace(id="identity-1")),
            ensure_default_workspace_for_user=Spy(return_value=workspace),
            create_auth_session=Spy(return_value=SimpleNamespace(id="session-1")),
        )

        with patch_attr(self.service, "repo", repo), \
             patch_attr(self.service.secrets, "token_urlsafe", Spy(return_value="raw-session")):
            created_user, created_workspace, token = self.service.sign_in_with_google_payload(
                db=object(),
                payload={
                    "sub": "google-sub-1",
                    "email": "Jane@Example.com",
                    "email_verified": True,
                    "name": "Jane",
                    "picture": "https://avatar",
                },
            )

        self.assertEqual(created_user.id, "user-1")
        self.assertEqual(created_workspace.id, "ws-1")
        self.assertEqual(token, "raw-session")
        self.assertEqual(repo.upsert_external_identity.calls[0][1]["provider"], "google")
        self.assertEqual(repo.upsert_external_identity.calls[0][1]["provider_subject"], "google-sub-1")
        self.assertNotEqual(repo.create_auth_session.calls[0][1]["session_hash"], "raw-session")

    def test_validate_google_csrf_allows_absent_tokens_for_bearer_style_clients(self):
        request = SimpleNamespace(cookies={})
        self.service.validate_google_csrf(request, "")

    def test_validate_google_csrf_rejects_mismatch(self):
        request = SimpleNamespace(cookies={"g_csrf_token": "cookie-token"})
        with self.assertRaises(Exception):
            self.service.validate_google_csrf(request, "body-token")

    def test_auth_required_is_read_dynamically(self):
        with patch_env({"SINTRA_AUTH_REQUIRED": "1"}):
            self.assertTrue(self.service.is_auth_required())
        with patch_env({"SINTRA_AUTH_REQUIRED": "0"}):
            self.assertFalse(self.service.is_auth_required())

    def test_get_current_user_from_request_uses_hashed_session_token(self):
        user = SimpleNamespace(
            id="user-1",
            email="jane@example.com",
            display_name="Jane",
            avatar_url="",
            status="active",
        )
        repo = SimpleNamespace(
            get_active_auth_session_by_hash=Spy(return_value=SimpleNamespace(user_id="user-1")),
            get_user=Spy(return_value=user),
        )
        request = SimpleNamespace(headers={"authorization": "Bearer raw-session"}, cookies={})

        with patch_attr(self.service, "repo", repo):
            result = self.service.get_current_user_from_request(db=object(), request=request)

        self.assertEqual(result.id, "user-1")
        self.assertNotEqual(repo.get_active_auth_session_by_hash.calls[0][0][1], "raw-session")


if __name__ == "__main__":
    unittest.main()
