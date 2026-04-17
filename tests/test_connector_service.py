import unittest

from tests.support import Spy, import_fresh, make_module, make_sqlalchemy_stubs, patch_attr


def load_connector_service_module():
    stubs = {}
    stubs.update(make_sqlalchemy_stubs())
    stubs["storage.repositories"] = make_module(
        "storage.repositories",
        upsert_tool_connection=lambda *args, **kwargs: None,
        set_tool_connection_status=lambda *args, **kwargs: None,
        list_tool_connections=lambda *args, **kwargs: [],
        get_workspace_connector_preference=lambda *args, **kwargs: None,
        upsert_workspace_connector_preference=lambda *args, **kwargs: None,
    )
    stubs["tools.composio_client"] = make_module(
        "tools.composio_client",
        get_connect_link=lambda *args, **kwargs: None,
        list_connected_accounts=lambda *args, **kwargs: [],
    )
    stubs["tools.tool_registry"] = make_module(
        "tools.tool_registry",
        get_toolkit_label=lambda toolkit: toolkit.title(),
        get_toolkit_metadata=lambda toolkit: {"label": toolkit.title(), "slug": toolkit.lower(), "auth_mode": "oauth2"},
        list_toolkits=lambda: [],
        list_ui_toolkits=lambda: [],
        normalize_toolkit_key=lambda toolkit: str(toolkit or "").upper(),
    )
    stubs["utils.time_utils"] = make_module(
        "utils.time_utils",
        utc_now=lambda: __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc),
    )
    stubs["utils.logging_utils"] = make_module(
        "utils.logging_utils",
        log_event=lambda *args, **kwargs: None,
        log_exception=lambda *args, **kwargs: None,
    )
    return import_fresh("tools.connector_service", stubs)


class ConnectorServiceTests(unittest.TestCase):
    def setUp(self):
        self.connector_service = load_connector_service_module()

    def test_list_workspace_connectors_is_local_first_and_lazy_about_connect_urls(self):
        with patch_attr(self.connector_service, "list_ui_toolkits", lambda: ["GMAIL", "HUBSPOT"]), \
             patch_attr(
                 self.connector_service,
                 "list_connector_accounts",
                 lambda workspace_id, toolkit, db, **kwargs: (
                     [{"connected_account_id": "acct-1", "account_alias": "Work Gmail", "display_label": "Work Gmail", "status": "connected"}]
                     if toolkit == "GMAIL"
                     else []
                 ),
             ), \
             patch_attr(self.connector_service, "get_connect_link", lambda workspace_id, toolkit: f"https://connect/{toolkit.lower()}"):
            result = self.connector_service.list_workspace_connectors(
                "ws-1",
                object(),
                selected_toolkit="HUBSPOT",
                include_connect_url=True,
            )

        gmail = next(item for item in result if item["toolkit"] == "GMAIL")
        hubspot = next(item for item in result if item["toolkit"] == "HUBSPOT")
        self.assertTrue(gmail["connected"])
        self.assertIsNone(gmail["connect_url"])
        self.assertFalse(hubspot["connected"])
        self.assertEqual(hubspot["connect_url"], "https://connect/hubspot")

    def test_validate_connector_context_autoselects_only_connected_account(self):
        connector = self.connector_service.build_connector_context(
            mode="manual",
            selected_toolkit="GMAIL",
        )
        with patch_attr(
            self.connector_service,
            "get_connector_status_summary",
            lambda *args, **kwargs: self.connector_service.ConnectorStatusSummary(
                toolkit="GMAIL",
                connector_key="GMAIL",
                label="Gmail",
                connected=True,
                validation_status="ok",
                account_count=1,
                effective_account_id="acct-1",
                effective_account_alias="Primary Gmail",
            ),
        ):
            normalized, summary, error = self.connector_service.validate_connector_context(
                connector,
                "ws-1",
                object(),
            )

        self.assertEqual(error, "")
        self.assertEqual(summary.effective_account_id, "acct-1")
        self.assertEqual(normalized.selected_account_id, "acct-1")
        self.assertEqual(normalized.selected_account_alias, "Primary Gmail")
        self.assertTrue(normalized.enforce_account)

    def test_list_connector_accounts_prefers_local_cache_when_fresh(self):
        remote = Spy(return_value=[
            {"connected_account_id": "acct-remote", "account_alias": "Remote", "status": "connected"},
        ])
        with patch_attr(
            self.connector_service.repo,
            "list_tool_connections",
            lambda db, workspace_id, toolkit="": [
                type(
                    "Row",
                    (),
                    {
                        "connected_account_id": "acct-1",
                        "status": "connected",
                        "metadata_json": {"account_alias": "Local Gmail"},
                        "account_label": "Local Gmail",
                        "is_default": True,
                        "last_verified_at": self.connector_service.utc_now(),
                        "updated_at": self.connector_service.utc_now(),
                    },
                )()
            ],
        ), \
             patch_attr(self.connector_service, "list_connected_accounts", remote):
            result = self.connector_service.list_connector_accounts(
                "ws-1",
                "GMAIL",
                object(),
                allow_remote=False,
            )

        self.assertEqual(result[0]["account_alias"], "Local Gmail")
        self.assertEqual(len(remote.calls), 0)

    def test_list_connector_accounts_refreshes_when_selected_account_missing(self):
        remote = Spy(return_value=[
            {"connected_account_id": "acct-2", "account_alias": "Sales Gmail", "status": "connected"},
        ])
        upsert = Spy()
        with patch_attr(
            self.connector_service.repo,
            "list_tool_connections",
            lambda db, workspace_id, toolkit="": [],
        ), \
             patch_attr(self.connector_service.repo, "upsert_tool_connection", upsert), \
             patch_attr(self.connector_service, "list_connected_accounts", remote):
            result = self.connector_service.list_connector_accounts(
                "ws-1",
                "gmail",
                object(),
                selected_account_id="acct-2",
                allow_remote=True,
            )

        self.assertEqual(result[0]["connected_account_id"], "acct-2")
        self.assertEqual(upsert.calls[0][1]["toolkit"], "GMAIL")
        self.assertEqual(len(remote.calls), 1)

    def test_persisted_connector_context_round_trips_workspace_preference(self):
        stored = {}

        def upsert(db, workspace_id, **kwargs):
            stored[workspace_id] = dict(kwargs)
            return type("Row", (), {"workspace_id": workspace_id, **kwargs})()

        def get_pref(db, workspace_id):
            value = stored.get(workspace_id)
            if not value:
                return None
            return type("Row", (), {"workspace_id": workspace_id, **value})()

        with patch_attr(self.connector_service.repo, "upsert_workspace_connector_preference", upsert), \
             patch_attr(self.connector_service.repo, "get_workspace_connector_preference", get_pref):
            persisted = self.connector_service.persist_connector_context(
                "ws-1",
                {
                    "mode": "manual",
                    "selected_toolkit": "hubspot",
                    "selected_account_id": "acct-9",
                    "selected_account_alias": "Sales HubSpot",
                    "source": "sidebar",
                },
                object(),
            )
            loaded = self.connector_service.load_persisted_connector_context("ws-1", object())

        self.assertEqual(persisted.selected_toolkit, "HUBSPOT")
        self.assertEqual(loaded.selected_toolkit, "HUBSPOT")
        self.assertEqual(loaded.selected_account_id, "acct-9")
        self.assertEqual(loaded.source, "sidebar")

    def test_synchronize_connector_accounts_revokes_missing_local_accounts(self):
        revoked = Spy()
        with patch_attr(
            self.connector_service.repo,
            "list_tool_connections",
            lambda db, workspace_id, toolkit="": [
                type(
                    "Row",
                    (),
                    {
                        "connected_account_id": "acct-old",
                        "status": "connected",
                    },
                )()
            ],
        ), \
             patch_attr(self.connector_service.repo, "upsert_tool_connection", Spy()), \
             patch_attr(self.connector_service.repo, "set_tool_connection_status", revoked), \
             patch_attr(
                 self.connector_service,
                 "list_connected_accounts",
                 lambda workspace_id, toolkit, force_refresh=False: [
                     {"connected_account_id": "acct-new", "account_alias": "Primary HubSpot", "status": "connected"},
                 ],
             ):
            accounts = self.connector_service.synchronize_connector_accounts(
                "ws-1",
                "HUBSPOT",
                object(),
                force_refresh=True,
                request_cache={},
            )

        self.assertEqual(accounts[0].connected_account_id, "acct-new")
        self.assertEqual(revoked.calls[0][1]["connected_account_id"], "acct-old")
        self.assertEqual(revoked.calls[0][1]["status"], "revoked")
