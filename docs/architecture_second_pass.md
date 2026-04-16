# Architecture Guide

This repository now uses a compatibility-safe layered architecture for routing, tool planning, Composio execution, and workflows. Public entry points, workflow names, and existing behavior are intentionally preserved. Memory is intentionally deferred and should not be redesigned in this phase.

## Request Flow

1. `orchestrator/handler.py`
   Receives the request, loads Brain context, routes the request, executes either a single agent or workflow, persists conversation state, and manages resume/interruption handling.
2. `orchestrator/router.py`
   Produces a structured route decision with intent, system family, operation, live-data requirement, approval signal, missing-info list, and optional workflow step skeleton.
3. `helpers/executor.py`
   Runs a single agent. Text-only agents stay on the plain generation path. Tool-enabled agents use capability-first planning plus broker-based resolution and verified result synthesis.
4. `tools/tool_broker.py`
   Resolves a `ToolPlan` into a concrete tool and executes through the existing Composio-backed path.
5. `tools/tool_executor.py`
   Performs validation, connection checks, pending-request persistence, live execution, and tool-call logging.

## Key Contracts

Typed internal contracts live in `models/contracts.py`:

- `RouteDecision`
- `ToolPlan`
- `ToolResolution`
- `ToolExecutionResult`
- `WorkflowStepSpec`
- `WorkflowStepResult`
- `ApprovalRequirement`

These contracts are internal. Backward-compatible dict adapters are still used at the handler and executor boundaries where older code expects legacy shapes.

## Routing

Routing is no longer just `route_type + selected_agent`. The router now captures:

- intent
- domain / system family
- operation
- whether live data is required
- whether approval may be required
- execution-blocking missing information
- clarification question
- workflow step skeletons when relevant

Important editing rule:

- Change routing structure in `orchestrator/router.py`
- Do not hardcode new tool slugs in the router
- Prefer intent, system family, operation, and capability reasoning

## Tool Resolution

The planning flow is now:

1. Router determines intent/system/operation
2. Agent plans capability-first
3. Broker resolves capability to a concrete tool
4. Executor validates and executes
5. Final answer is synthesized only from verified tool results

Core files:

- `tools/capability_layer.py`
- `tools/tool_broker.py`
- `tools/tool_registry.py`
- `tools/tool_executor.py`
- `tools/composio_client.py`

## Tool Registry Responsibilities

`tools/tool_registry.py` is the local policy overlay. It should hold business-specific metadata, not Composio execution logic.

It centralizes:

- toolkit metadata
- capability-group metadata
- tool aliases and parameter aliases
- approval / risk / execution mode
- idempotency fields
- lightweight local schemas and defaults
- agent allow-policy overlays

It should not be used for:

- ad hoc routing logic
- workflow orchestration
- direct network execution

## Adding a New Composio Tool

Preferred path:

1. Add or expose the tool in `tools/tool_registry.py`
   Include `tool_name`, `toolkit`, description, capability group, allowed agents, aliases, defaults, and expected params.
2. Add policy overlay metadata in `TOOL_POLICY_OVERLAY` when needed
   Set risk, approval mode, execution mode, idempotency fields, and any tool aliases.
3. Add local schema help in `TOOL_INPUT_SCHEMAS` when helpful
   This gives validation and alias normalization even when live schema is unavailable.
4. If the tool belongs to a new domain, extend capability metadata
   Update `CAPABILITY_GROUP_METADATA` in `tools/tool_registry.py` and `CAPABILITY_GROUPS` in `tools/capability_layer.py`.
5. Do not edit the router for a single new tool
   The router should remain capability-oriented.
6. Do not edit workflows unless a workflow explicitly needs to use the new capability

In most cases, onboarding a new Composio tool should not require changes to:

- `orchestrator/router.py`
- `helpers/executor.py`
- `workflows/engine.py`
- agent persona files

## Adding a New Toolkit / Connector

1. Add toolkit metadata in `TOOLKIT_METADATA` in `tools/tool_registry.py`
   Include slug, label, app enum, auth mode, schema source, connection mode, and optional setup message.
2. Ensure `tools/composio_client.py` can look up auth and schema behavior through that metadata
3. Add at least one tool entry using the toolkit
4. Add or update tests for runtime config, resolution, and validation

If the connector has unusual auth behavior, keep the special-case logic in:

- `tools/composio_client.py`
- `tools/tool_executor.py`

Avoid spreading connector-specific assumptions into the router, workflows, or agent prompts.

## Safety Model

Write actions now carry explicit metadata for:

- risk level
- approval requirement
- approval mode
- execution mode (`read`, `draft`, `execute`)
- idempotency fields

The executor and prompts are hardened so agents:

- do not simulate external access
- prefer read/discovery before write when needed
- prefer drafts when execute intent is unclear
- do not claim success for live actions without verified tool results

## Workflows

`workflows/engine.py` now supports reusable step specs and structured step results while preserving existing workflow names and entry points.

Editing guidance:

- Add reusable step logic in `workflows/engine.py`
- Preserve current workflow keys
- Keep interruption/resume compatibility intact
- Do not redesign persistence or memory in workflow work

## Memory Status

Memory is intentionally deferred. Do not redesign `brain/*` behavior in this phase except for compatibility fixes that are strictly necessary.
