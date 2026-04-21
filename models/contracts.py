from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable


@dataclass
class ApprovalRequirement:
    """Structured approval/risk contract used across routing and tools."""

    required: bool = False
    risk_level: str = "low"
    reason: str = ""
    categories: list[str] = field(default_factory=list)
    mode: str = "auto"

    @classmethod
    def from_value(cls, value: Any) -> "ApprovalRequirement":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                required=bool(value.get("required", False)),
                risk_level=str(value.get("risk_level", "low") or "low"),
                reason=str(value.get("reason", "") or ""),
                categories=[str(item) for item in value.get("categories", [])],
                mode=str(value.get("mode", "auto") or "auto"),
            )
        return cls(required=bool(value))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteStepSkeleton:
    agent: str | None = None
    task: str = ""
    system_family: str = ""
    operation: str = ""
    capability_group: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "RouteStepSkeleton | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        return cls(
            agent=value.get("agent"),
            task=str(value.get("task", "") or "").strip(),
            system_family=str(value.get("system_family", "") or "").strip(),
            operation=str(value.get("operation", "") or "").strip(),
            capability_group=str(value.get("capability_group", "") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    """Internal structured routing decision."""

    route_type: str
    confidence: float = 0.5
    intent: str = ""
    domain: str = ""
    system_family: str = ""
    operation: str = ""
    requires_live_data: bool = False
    approval_required: ApprovalRequirement = field(default_factory=ApprovalRequirement)
    selected_agent: str | None = None
    selected_workflow: str | None = None
    missing_info: list[str] = field(default_factory=list)
    reason: str = ""
    ordered_steps: list[RouteStepSkeleton] = field(default_factory=list)
    clarification_question: str = ""
    risk_flags: list[str] = field(default_factory=list)
    route_method: str = "llm_router"

    def replace(self, **changes) -> "RouteDecision":
        return replace(self, **changes)

    def to_legacy_dict(self) -> dict[str, Any]:
        clarification_question = self.clarification_question
        if not clarification_question and self.route_type == "clarify" and self.missing_info:
            clarification_question = self.missing_info[0]

        return {
            "route_type": self.route_type,
            "confidence": self.confidence,
            "primary_intent": self.intent,
            "reason": self.reason,
            "selected_agent": self.selected_agent,
            "selected_workflow": self.selected_workflow,
            "steps": [step.to_dict() for step in self.ordered_steps],
            "clarification_question": clarification_question,
            "risk_flags": list(self.risk_flags),
            "route_method": self.route_method,
            "domain": self.domain,
            "system_family": self.system_family,
            "operation": self.operation,
            "requires_live_data": self.requires_live_data,
            "approval_required": self.approval_required.required,
            "approval_requirement": self.approval_required.to_dict(),
            "missing_info": list(self.missing_info),
        }


@dataclass
class CapabilityRequest:
    """Capability-first request that may later resolve to a concrete tool."""

    capability_group: str = ""
    toolkit_family: str = ""
    action_class: str = ""
    operation: str = ""
    risk_level: str = "low"
    requires_live_data: bool = False
    execution_mode: str = ""
    preferred_tools: list[str] = field(default_factory=list)
    fallback_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionConstraint:
    toolkit: str = ""
    account_id: str = ""
    account_alias: str = ""
    source: str = "system_inferred"
    scope: str = "request"
    required: bool = False
    reason: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "ExecutionConstraint":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                toolkit=str(value.get("toolkit", "") or "").upper(),
                account_id=str(value.get("account_id", "") or ""),
                account_alias=str(value.get("account_alias", "") or ""),
                source=str(value.get("source", "system_inferred") or "system_inferred"),
                scope=str(value.get("scope", "request") or "request"),
                required=bool(value.get("required", False)),
                reason=str(value.get("reason", "") or ""),
            )
        return cls()

    def is_empty(self) -> bool:
        return not any(
            (
                self.toolkit,
                self.account_id,
                self.account_alias,
                self.reason,
                self.required,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolPlan:
    """Agent plan for external actions or live data access."""

    agent_key: str
    user_intent: str = ""
    llm_message: str = ""
    capability: CapabilityRequest = field(default_factory=CapabilityRequest)
    concrete_tool_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    raw_request: dict[str, Any] = field(default_factory=dict)
    iteration: int = 1
    idempotency_key: str = ""
    execution_constraint: ExecutionConstraint = field(default_factory=ExecutionConstraint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_key": self.agent_key,
            "user_intent": self.user_intent,
            "llm_message": self.llm_message,
            "capability": self.capability.to_dict(),
            "concrete_tool_name": self.concrete_tool_name,
            "params": dict(self.params),
            "raw_request": dict(self.raw_request),
            "iteration": self.iteration,
            "idempotency_key": self.idempotency_key,
            "execution_constraint": self.execution_constraint.to_dict(),
        }


@dataclass
class ToolResolution:
    status: str = "resolved"
    tool_name: str | None = None
    candidate_tools: list[str] = field(default_factory=list)
    toolkit: str = ""
    normalized_params: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)
    approval_requirement: ApprovalRequirement = field(default_factory=ApprovalRequirement)
    connection_ready: bool | None = None
    resolution_source: str = ""
    reason: str = ""
    execution_mode: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tool_name": self.tool_name,
            "candidate_tools": list(self.candidate_tools),
            "toolkit": self.toolkit,
            "normalized_params": dict(self.normalized_params),
            "schema": dict(self.schema),
            "approval_requirement": self.approval_requirement.to_dict(),
            "connection_ready": self.connection_ready,
            "resolution_source": self.resolution_source,
            "reason": self.reason,
            "execution_mode": self.execution_mode,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class ToolExecutionResult:
    status: str
    tool_name: str | None = None
    toolkit: str = ""
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0
    connect_url: str | None = None
    resume_token: str = ""
    approval_requirement: ApprovalRequirement = field(default_factory=ApprovalRequirement)
    raw_response: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    idempotency_key: str = ""

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "toolkit": self.toolkit,
            "connect_url": self.connect_url,
            "resume_token": self.resume_token,
            "verified": self.verified,
            "idempotency_key": self.idempotency_key,
            "approval_required": self.approval_requirement.required,
            "approval_requirement": self.approval_requirement.to_dict(),
            "pending_kind": str(self.raw_response.get("pending_kind", "") or ""),
            "idempotent_replay": bool(self.raw_response.get("idempotent_replay", False)),
        }


@dataclass(frozen=True)
class WorkflowStepSpec:
    """Declarative workflow step definition."""

    label: str
    agent_key: str
    prompt_builder: Callable[[str, list[dict[str, Any]], dict[str, Any]], str]
    capability_hint: CapabilityRequest | None = None
    requires_live_tool: bool = False
    allow_text_fallback: bool = True


@dataclass
class WorkflowStepResult:
    step: str
    agent: str
    input: str
    output: str
    tool_used: str | None = None
    tool_output: dict[str, Any] | None = None
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "agent": self.agent,
            "input": self.input,
            "output": self.output,
            "tool_used": self.tool_used,
            "tool_output": self.tool_output,
            "success": self.success,
        }


@dataclass
class MemoryRecordInput:
    memory_type: str
    title: str = ""
    content: str = ""
    summary: str = ""
    source_kind: str = ""
    source_reference_id: str = ""
    tags: list[str] = field(default_factory=list)
    entity_tags: list[str] = field(default_factory=list)
    tool_tags: list[str] = field(default_factory=list)
    importance_score: float = 0.5
    confidence_score: float = 0.5
    pinned: bool = False
    canonical_key: str = ""
    metadata_json: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkingMemoryUpdate:
    current_goal: str = ""
    active_tasks: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    current_draft_summary: str = ""
    recent_tool_summary: str = ""
    latest_workflow_summary: str = ""
    project_focus: str = ""
    state_json: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryContextPack:
    profile: dict[str, Any] = field(default_factory=dict)
    working_memory: dict[str, Any] = field(default_factory=dict)
    memories: list[dict[str, Any]] = field(default_factory=list)
    legacy_knowledge: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorContext:
    mode: str = "auto"
    selected_toolkit: str = ""
    selected_connector_key: str = ""
    selected_account_alias: str = ""
    selected_account_id: str = ""
    enforce_toolkit: bool = False
    enforce_account: bool = False
    source: str = "system_inferred"
    display_label: str = ""
    validation_status: str = "ok"
    stale_selection: bool = False
    status_reason: str = ""
    available_account_count: int = 0
    effective_account_id: str = ""
    effective_account_alias: str = ""
    connected: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "ConnectorContext":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            selected_toolkit = str(value.get("selected_toolkit", "") or "").upper()
            selected_connector_key = str(value.get("selected_connector_key", "") or "").upper() or selected_toolkit
            mode = str(value.get("mode", "auto") or "auto").lower()
            enforce_toolkit = bool(value.get("enforce_toolkit", False))
            enforce_account = bool(value.get("enforce_account", False))
            if selected_toolkit and mode != "manual":
                mode = "manual"
            return cls(
                mode=mode,
                selected_toolkit=selected_toolkit,
                selected_connector_key=selected_connector_key,
                selected_account_alias=str(value.get("selected_account_alias", "") or ""),
                selected_account_id=str(value.get("selected_account_id", "") or ""),
                enforce_toolkit=enforce_toolkit or bool(selected_toolkit and mode == "manual"),
                enforce_account=enforce_account or bool(value.get("selected_account_id")),
                source=str(value.get("source", "system_inferred") or "system_inferred"),
                display_label=str(value.get("display_label", "") or ""),
                validation_status=str(value.get("validation_status", "ok") or "ok"),
                stale_selection=bool(value.get("stale_selection", False)),
                status_reason=str(value.get("status_reason", "") or ""),
                available_account_count=int(value.get("available_account_count", 0) or 0),
                effective_account_id=str(value.get("effective_account_id", "") or ""),
                effective_account_alias=str(value.get("effective_account_alias", "") or ""),
                connected=bool(value.get("connected", False)),
            )
        return cls()

    def is_auto(self) -> bool:
        return self.mode != "manual" or not self.selected_toolkit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorAccountSummary:
    toolkit: str = ""
    connected_account_id: str = ""
    account_alias: str = ""
    display_label: str = ""
    status: str = "unknown"
    is_default: bool = False
    is_selected: bool = False
    source: str = "local_cache"
    last_verified_at: str = ""
    stale: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "ConnectorAccountSummary":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                toolkit=str(value.get("toolkit", "") or "").upper(),
                connected_account_id=str(value.get("connected_account_id", "") or ""),
                account_alias=str(value.get("account_alias", "") or ""),
                display_label=str(value.get("display_label", "") or value.get("account_alias", "") or ""),
                status=str(value.get("status", "unknown") or "unknown"),
                is_default=bool(value.get("is_default", False)),
                is_selected=bool(value.get("is_selected", False)),
                source=str(value.get("source", "local_cache") or "local_cache"),
                last_verified_at=str(value.get("last_verified_at", "") or ""),
                stale=bool(value.get("stale", False)),
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorStatusSummary:
    toolkit: str = ""
    connector_key: str = ""
    label: str = ""
    slug: str = ""
    connected: bool = False
    status: str = "unknown"
    source: str = "local_cache"
    validation_status: str = "ok"
    status_reason: str = ""
    stale: bool = False
    stale_selection: bool = False
    account_required: bool = False
    account_count: int = 0
    selected_account_id: str = ""
    selected_account_alias: str = ""
    effective_account_id: str = ""
    effective_account_alias: str = ""
    connect_url: str | None = None
    setup_message: str = ""
    connection_mode: str = ""
    auth_mode: str = ""
    last_verified_at: str = ""
    accounts: list[ConnectorAccountSummary] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: Any) -> "ConnectorStatusSummary":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                toolkit=str(value.get("toolkit", "") or "").upper(),
                connector_key=str(value.get("connector_key", "") or value.get("toolkit", "") or "").upper(),
                label=str(value.get("label", "") or ""),
                slug=str(value.get("slug", "") or ""),
                connected=bool(value.get("connected", False)),
                status=str(value.get("status", "unknown") or "unknown"),
                source=str(value.get("source", "local_cache") or "local_cache"),
                validation_status=str(value.get("validation_status", "ok") or "ok"),
                status_reason=str(value.get("status_reason", "") or ""),
                stale=bool(value.get("stale", False)),
                stale_selection=bool(value.get("stale_selection", False)),
                account_required=bool(value.get("account_required", False)),
                account_count=int(value.get("account_count", 0) or 0),
                selected_account_id=str(value.get("selected_account_id", "") or ""),
                selected_account_alias=str(value.get("selected_account_alias", "") or ""),
                effective_account_id=str(value.get("effective_account_id", "") or ""),
                effective_account_alias=str(value.get("effective_account_alias", "") or ""),
                connect_url=value.get("connect_url"),
                setup_message=str(value.get("setup_message", "") or ""),
                connection_mode=str(value.get("connection_mode", "") or ""),
                auth_mode=str(value.get("auth_mode", "") or ""),
                last_verified_at=str(value.get("last_verified_at", "") or ""),
                accounts=[
                    ConnectorAccountSummary.from_value(item)
                    for item in list(value.get("accounts", []) or [])
                ],
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["accounts"] = [account.to_dict() for account in self.accounts]
        return payload


@dataclass
class ScheduleSpec:
    schedule_type: str = "once"
    timezone: str = "UTC"
    start_at: str = ""
    end_at: str = ""
    cron_expression: str = ""
    interval_seconds: int = 0

    @classmethod
    def from_value(cls, value: Any) -> "ScheduleSpec":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                schedule_type=str(value.get("schedule_type", "once") or "once"),
                timezone=str(value.get("timezone", "UTC") or "UTC"),
                start_at=str(value.get("start_at", "") or ""),
                end_at=str(value.get("end_at", "") or ""),
                cron_expression=str(value.get("cron_expression", "") or ""),
                interval_seconds=int(value.get("interval_seconds", 0) or 0),
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: int = 300

    @classmethod
    def from_value(cls, value: Any) -> "RetryPolicy":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                max_attempts=max(1, int(value.get("max_attempts", 1) or 1)),
                backoff_seconds=max(0, int(value.get("backoff_seconds", 300) or 300)),
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPolicy:
    approval_policy: str = "per_run"
    allow_write_actions: bool = True
    idempotency_scope: str = "scheduled_run"

    @classmethod
    def from_value(cls, value: Any) -> "ExecutionPolicy":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                approval_policy=str(value.get("approval_policy", "per_run") or "per_run"),
                allow_write_actions=bool(value.get("allow_write_actions", True)),
                idempotency_scope=str(value.get("idempotency_scope", "scheduled_run") or "scheduled_run"),
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScheduledTaskPayload:
    target_kind: str = "workflow"
    target_name: str = ""
    user_input: str = ""
    force_agent: str = ""
    force_workflow: str = ""
    payload_json: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ScheduledTaskPayload":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            target_kind = str(value.get("target_kind", "workflow") or "workflow")
            target_name = str(value.get("target_name", "") or "")
            return cls(
                target_kind=target_kind,
                target_name=target_name,
                user_input=str(value.get("user_input", "") or value.get("message", "") or ""),
                force_agent=str(value.get("force_agent", "") or (target_name if target_kind == "agent" else "")),
                force_workflow=str(value.get("force_workflow", "") or (target_name if target_kind == "workflow" else "")),
                payload_json=dict(value.get("payload_json", {}) or {}),
            )
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScheduledTaskDefinition:
    id: str = ""
    workspace_id: str = ""
    actor_user_id: str = ""
    membership_id: str = ""
    status: str = "active"
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    payload: ScheduledTaskPayload = field(default_factory=ScheduledTaskPayload)
    connector_context: ConnectorContext = field(default_factory=ConnectorContext)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    metadata_json: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "actor_user_id": self.actor_user_id,
            "membership_id": self.membership_id,
            "status": self.status,
            "schedule": self.schedule.to_dict(),
            "payload": self.payload.to_dict(),
            "connector_context": self.connector_context.to_dict(),
            "retry_policy": self.retry_policy.to_dict(),
            "execution_policy": self.execution_policy.to_dict(),
            "metadata_json": dict(self.metadata_json),
        }


@dataclass
class ScheduledRunContext:
    scheduled_task_id: str = ""
    scheduled_run_id: str = ""
    workspace_id: str = ""
    actor_user_id: str = ""
    membership_id: str = ""
    planned_for: str = ""
    run_key: str = ""
    attempt_number: int = 1
    connector_context: ConnectorContext = field(default_factory=ConnectorContext)
    request_id: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["connector_context"] = self.connector_context.to_dict()
        return payload


@dataclass
class ScheduledRunResult:
    status: str = "queued"
    output: str = ""
    error_message: str = ""
    result_json: dict[str, Any] = field(default_factory=dict)
    resume_token: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
