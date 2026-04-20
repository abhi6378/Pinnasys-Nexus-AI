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


class StreamlitAuthStateTests(unittest.TestCase):
    def setUp(self):
        import ui.auth_state as auth_state

        self.auth_state = auth_state

    def test_auth_disabled_keeps_anonymous_mode(self):
        state = {}
        with patch_env({"SINTRA_AUTH_REQUIRED": "0"}):
            user = self.auth_state.resolve_streamlit_user(object(), state)

        self.assertIsNone(user)
        self.assertFalse(state["auth_required"])
        self.assertTrue(state["auth_checked"])

    def test_auth_enabled_requires_valid_backend_session_token(self):
        state = {"auth_token": "bad-token"}
        with patch_env({"SINTRA_AUTH_REQUIRED": "1"}), \
             patch_attr(self.auth_state, "get_current_user_from_token", Spy(return_value=None)):
            user = self.auth_state.resolve_streamlit_user(object(), state)

        self.assertIsNone(user)
        self.assertEqual(state["auth_token"], "")
        self.assertTrue(state["auth_required"])

    def test_auth_enabled_loads_user_from_backend_session_token(self):
        state = {"auth_token": "session-token"}
        user = SimpleNamespace(
            id="user-1",
            email="jane@example.com",
            display_name="Jane",
            avatar_url="",
            to_dict=lambda: {
                "id": "user-1",
                "email": "jane@example.com",
                "display_name": "Jane",
                "avatar_url": "",
            },
        )
        with patch_env({"SINTRA_AUTH_REQUIRED": "1"}), \
             patch_attr(self.auth_state, "get_current_user_from_token", Spy(return_value=user)):
            result = self.auth_state.resolve_streamlit_user(object(), state)

        self.assertEqual(result.id, "user-1")
        self.assertEqual(state["auth_user"]["id"], "user-1")


if __name__ == "__main__":
    unittest.main()
