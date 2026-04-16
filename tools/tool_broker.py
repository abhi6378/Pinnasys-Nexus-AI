from __future__ import annotations

import hashlib
import json
from typing import Protocol

from models.contracts import ConnectorContext, ToolExecutionResult, ToolPlan, ToolResolution
from tools.capability_layer import build_capability_request, resolve_capability_request
from tools.composio_client import get_live_tool_schema, get_tool_schemas
from tools.tool_registry import (
    get_tool,
    get_tool_approval_requirement,
    get_tool_schema,
    is_agent_allowed,
    normalize_tool_input,
    resolve_tool_name,
)


def attempt_tool_call(*args, **kwargs):
    from tools.tool_executor import attempt_tool_call as _attempt_tool_call

    return _attempt_tool_call(*args, **kwargs)


class ToolBroker(Protocol):
    def resolve(
        self,
        plan: ToolPlan,
        *,
        workspace_id: str = "",
        db=None,
        allowed_tool_names: list[str] | None = None,
        connector_context: ConnectorContext | dict | None = None,
    ) -> ToolResolution:
        ...

    def execute(
        self,
        resolution: ToolResolution,
        plan: ToolPlan,
        *,
        workspace_id: str,
        db,
        original_input: str = "",
        conversation_id: str = "",
        context_json: dict | None = None,
        callback_url: str = "",
        connector_context: ConnectorContext | dict | None = None,
    ) -> ToolExecutionResult:
        ...


class ComposioDirectBroker:
    """Capability-first broker backed by the existing Composio execution path."""

    @staticmethod
    def _build_idempotency_key(plan: ToolPlan, tool_name: str, normalized_params: dict) -> str:
        base = {
            "agent_key": plan.agent_key,
            "tool_name": tool_name,
            "params": normalized_params,
            "execution_mode": plan.capability.execution_mode,
        }
        serialized = json.dumps(base, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]

    def resolve(
        self,
        plan: ToolPlan,
        *,
        workspace_id: str = "",
        db=None,
        allowed_tool_names: list[str] | None = None,
        connector_context: ConnectorContext | dict | None = None,
    ) -> ToolResolution:
        allowed_set = set(allowed_tool_names or [])
        connector = ConnectorContext.from_value(connector_context or plan.capability.metadata.get("connector_context"))
        requested_tool_name = resolve_tool_name(plan.concrete_tool_name or "")

        if requested_tool_name:
            requested_entry = get_tool(requested_tool_name)
            if (
                not connector.is_auto()
                and connector.selected_toolkit
                and requested_entry
                and str(requested_entry.get("toolkit", "")).upper() != connector.selected_toolkit
            ):
                return ToolResolution(
                    status="invalid_tool",
                    tool_name=requested_tool_name,
                    candidate_tools=[requested_tool_name],
                    approval_requirement=get_tool_approval_requirement(requested_tool_name),
                    resolution_source="connector_constraint",
                    reason=(
                        f"The selected connector {connector.selected_toolkit} does not allow tool "
                        f"{requested_tool_name}."
                    ),
                )
            candidate_tools = [requested_tool_name]
            resolution_source = "explicit_tool"
        else:
            capability_resolution = resolve_capability_request(
                plan.agent_key,
                plan.capability,
                allowed_tool_names=list(allowed_set) if allowed_set else None,
                connector_context=connector,
            )
            candidate_tools = capability_resolution.get("candidate_tools", [])
            resolution_source = capability_resolution.get("resolution_reason", "capability_metadata")

        if not candidate_tools:
            return ToolResolution(
                status="invalid_tool",
                candidate_tools=[],
                approval_requirement=get_tool_approval_requirement(plan.concrete_tool_name or ""),
                resolution_source=resolution_source,
                reason="No tool candidates matched the requested capability.",
            )

        tool_name = candidate_tools[0]
        if allowed_set and tool_name not in allowed_set:
            return ToolResolution(
                status="invalid_tool",
                tool_name=tool_name,
                candidate_tools=candidate_tools,
                approval_requirement=get_tool_approval_requirement(tool_name),
                resolution_source=resolution_source,
                reason="Resolved tool is not in the agent allow-policy.",
            )

        tool_entry = get_tool(tool_name)
        if tool_entry is None:
            return ToolResolution(
                status="invalid_tool",
                tool_name=tool_name,
                candidate_tools=candidate_tools,
                approval_requirement=get_tool_approval_requirement(tool_name),
                resolution_source=resolution_source,
                reason="Resolved tool does not exist in the registry.",
            )

        if not is_agent_allowed(tool_name, plan.agent_key):
            return ToolResolution(
                status="validation_error",
                tool_name=tool_name,
                candidate_tools=candidate_tools,
                toolkit=tool_entry.get("toolkit", ""),
                approval_requirement=get_tool_approval_requirement(tool_name),
                resolution_source=resolution_source,
                reason="Requesting agent is not authorized to use the resolved tool.",
            )

        normalized_params = normalize_tool_input(tool_name, plan.params)
        idempotency_key = ""
        if tool_entry.get("write_action"):
            idempotency_key = self._build_idempotency_key(plan, tool_name, normalized_params)
        effective_account_id = connector.effective_account_id or connector.selected_account_id
        if (
            not connector.is_auto()
            and tool_entry.get("requires_auth")
            and connector.available_account_count > 1
            and not effective_account_id
        ):
            return ToolResolution(
                status="validation_error",
                tool_name=tool_name,
                candidate_tools=candidate_tools,
                toolkit=tool_entry.get("toolkit", ""),
                approval_requirement=get_tool_approval_requirement(tool_name),
                resolution_source="connector_account_required",
                reason=(
                    f"Multiple {connector.display_label or connector.selected_toolkit} accounts are connected. "
                    "Select which account to use before running a live action."
                ),
            )

        connection_ready = connector.connected if tool_entry.get("requires_auth") else True

        schema = get_tool_schema(tool_name)
        if workspace_id:
            live_schema = get_live_tool_schema(workspace_id, tool_name)
            if not live_schema:
                live_schemas = get_tool_schemas(workspace_id, [tool_name])
                live_schema = live_schemas[0] if live_schemas else {}
            if live_schema:
                schema["live_schema"] = live_schema

        return ToolResolution(
            status="resolved",
            tool_name=tool_name,
            candidate_tools=candidate_tools,
            toolkit=tool_entry.get("toolkit", ""),
            normalized_params=normalized_params,
            schema=schema,
            approval_requirement=get_tool_approval_requirement(tool_name),
            connection_ready=connection_ready,
            resolution_source=resolution_source,
            reason="Capability resolved successfully.",
            execution_mode=tool_entry.get("execution_mode", plan.capability.execution_mode),
            idempotency_key=idempotency_key,
        )

    def execute(
        self,
        resolution: ToolResolution,
        plan: ToolPlan,
        *,
        workspace_id: str,
        db,
        original_input: str = "",
        conversation_id: str = "",
        context_json: dict | None = None,
        callback_url: str = "",
        connector_context: ConnectorContext | dict | None = None,
        ) -> ToolExecutionResult:
        connector = ConnectorContext.from_value(connector_context or plan.capability.metadata.get("connector_context"))
        execution_context = dict(context_json or {})
        if not connector.is_auto():
            execution_context.setdefault("connector_context", connector.to_dict())
        if resolution.status != "resolved" or not resolution.tool_name:
            return ToolExecutionResult(
                status=resolution.status,
                tool_name=resolution.tool_name,
                toolkit=resolution.toolkit,
                error=resolution.reason or "Tool resolution failed.",
                approval_requirement=resolution.approval_requirement,
                raw_response=resolution.to_dict(),
                idempotency_key=resolution.idempotency_key,
            )

        result = attempt_tool_call(
            tool_name=resolution.tool_name,
            agent_key=plan.agent_key,
            workspace_id=workspace_id,
            db=db,
            input_args=resolution.normalized_params,
            original_input=original_input,
            conversation_id=conversation_id,
            context_json=execution_context,
            callback_url=callback_url,
            selected_account_id=connector.effective_account_id or connector.selected_account_id,
        )

        return ToolExecutionResult(
            status=result.get("status", "failure"),
            tool_name=resolution.tool_name,
            toolkit=result.get("toolkit", resolution.toolkit),
            output=result.get("output"),
            error=result.get("error"),
            duration_ms=float(result.get("duration_ms", 0.0) or 0.0),
            connect_url=result.get("connect_url"),
            resume_token=str(result.get("resume_token", "") or ""),
            approval_requirement=resolution.approval_requirement,
            raw_response=result,
            verified=result.get("status") == "success",
            idempotency_key=resolution.idempotency_key,
        )


def build_tool_plan(
    agent_key: str,
    *,
    user_intent: str,
    concrete_tool_name: str | None = None,
    params: dict | None = None,
    llm_message: str = "",
    route_decision=None,
    capability_hint: dict | None = None,
    iteration: int = 1,
    connector_context: ConnectorContext | dict | None = None,
) -> ToolPlan:
    capability = build_capability_request(
        agent_key,
        user_input=user_intent,
        route_decision=route_decision,
        requested_tool_name=concrete_tool_name or "",
        capability_hint=capability_hint,
        connector_context=connector_context,
    )
    return ToolPlan(
        agent_key=agent_key,
        user_intent=user_intent,
        llm_message=llm_message,
        capability=capability,
        concrete_tool_name=concrete_tool_name,
        params=dict(params or {}),
        raw_request=dict(capability_hint or {}),
        iteration=iteration,
        idempotency_key="",
    )
