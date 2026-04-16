from __future__ import annotations

from models.contracts import ConnectorContext


def default_connector_context() -> dict:
    return ConnectorContext().to_dict()


def ensure_connector_state(state) -> None:
    state.setdefault("connector_context", default_connector_context())


def set_connector_selection(
    state,
    *,
    mode: str = "auto",
    selected_toolkit: str = "",
    selected_account_id: str = "",
    selected_account_alias: str = "",
    source: str = "chat_input",
) -> dict:
    connector = ConnectorContext.from_value(
        {
            "mode": mode,
            "selected_toolkit": selected_toolkit,
            "selected_connector_key": selected_toolkit,
            "selected_account_id": selected_account_id,
            "selected_account_alias": selected_account_alias,
            "enforce_toolkit": bool(selected_toolkit and mode == "manual"),
            "enforce_account": bool(selected_account_id),
            "source": source,
        }
    )
    if connector.is_auto():
        connector = ConnectorContext()
    state["connector_context"] = connector.to_dict()
    return state["connector_context"]


def build_connector_context(state) -> dict:
    ensure_connector_state(state)
    return ConnectorContext.from_value(state.get("connector_context")).to_dict()
