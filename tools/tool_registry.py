# ============================================================
# tools/tool_registry.py
# ------------------------------------------------------------
# Central registry for ALL tools.
# Each tool is a plain Python function.
# Agents declare which tools they can use in configs.py.
# The orchestrator enforces this — agents cannot call tools
# outside their allowed_tools list.
#
# Tool types:
#   - Mock tools  : fake responses for demo
#   - Memory tools: read/write to shared workspace
#   - Real tools  : (Phase 3) Gmail, Sheets via Composio
# ============================================================

from datetime import datetime


# ── MOCK TOOLS ────────────────────────────────────────────────
# These return realistic fake data for demos.
# Replace with real API calls in production.

def mock_send_email(to: str = "client@example.com",
                    subject: str = "Follow-up",
                    body: str = "") -> str:
    return (
        f"✅ Email sent successfully.\n"
        f"   To: {to}\n"
        f"   Subject: '{subject}'\n"
        f"   Sent at: {datetime.now().strftime('%H:%M:%S')}"
    )


def mock_search_web(query: str = "") -> str:
    return (
        f"✅ Web search complete for: '{query}'\n"
        f"   Top results:\n"
        f"   1. 'Complete guide to {query}' — searchexample.com\n"
        f"   2. '{query}: 2024 trends and insights' — blog.example.com\n"
        f"   3. 'How to master {query}' — medium.com\n"
        f"   Estimated search volume: 12,000/month"
    )


def mock_read_sheet(sheet_name: str = "Sales Data") -> str:
    return (
        f"✅ Google Sheet '{sheet_name}' loaded.\n"
        f"   Rows: 142 | Date range: Jan–Mar 2024\n"
        f"   Revenue total : $48,320\n"
        f"   Top product   : Premium Plan ($22,100 | 46%)\n"
        f"   Top region    : South Asia (+34% MoM)\n"
        f"   Churn rate    : 3.2% (down from 4.1%)"
    )


def mock_create_task(task: str = "", project: str = "General",
                     assignee: str = "Team") -> str:
    return (
        f"✅ Task created.\n"
        f"   Project  : {project}\n"
        f"   Task     : {task}\n"
        f"   Assignee : {assignee}\n"
        f"   Due      : {datetime.now().strftime('%Y-%m-%d')} + 3 days"
    )


def mock_query_database(query: str = "") -> str:
    return (
        f"✅ Query executed: {query}\n"
        f"   Results (3 rows):\n"
        f"   | user_id | revenue | country | plan    |\n"
        f"   | 101     | $4,200  | India   | Pro     |\n"
        f"   | 202     | $3,100  | US      | Premium |\n"
        f"   | 303     | $2,800  | UK      | Pro     |"
    )


# ── MEMORY TOOLS ──────────────────────────────────────────────
# These let agents read/write to the shared workspace.
# The workspace object is injected at runtime by the orchestrator.

def save_to_workspace(workspace, agent_name: str, content: str, key: str = "") -> str:
    """Saves agent output to shared workspace memory."""
    workspace.save_agent_output(agent_name, content, task=key)
    return f"✅ Saved to shared workspace under '{agent_name}'."


def read_from_workspace(workspace, agent_name: str = "") -> str:
    """Reads another agent's output from shared workspace."""
    if agent_name:
        output = workspace.get_agent_output(agent_name)
        return output if output else f"No output from '{agent_name}' found in workspace."
    return workspace.get_all_agent_outputs_summary()


# ── TOOL REGISTRY ─────────────────────────────────────────────
# Maps tool name (string) → function.
# Used by the orchestrator to resolve and call tools.

TOOL_REGISTRY = {
    "mock_send_email":    mock_send_email,
    "mock_search_web":    mock_search_web,
    "mock_read_sheet":    mock_read_sheet,
    "mock_create_task":   mock_create_task,
    "mock_query_database": mock_query_database,
    "save_to_workspace":  save_to_workspace,
    "read_from_workspace": read_from_workspace,
}


def call_tool(tool_name: str, workspace=None, **kwargs) -> str:
    """
    Calls a tool by name. Memory tools receive the workspace object.

    Args:
        tool_name : Name of the tool from TOOL_REGISTRY
        workspace : WorkspaceMemory instance (for memory tools)
        **kwargs  : Arguments for the specific tool

    Returns:
        str: Tool result text
    """
    fn = TOOL_REGISTRY.get(tool_name)
    if not fn:
        return f"❌ Tool '{tool_name}' not found in registry."

    # Memory tools need the workspace injected
    if tool_name in ("save_to_workspace", "read_from_workspace") and workspace:
        return fn(workspace=workspace, **kwargs)

    return fn(**kwargs)


def get_tools_description(tool_names: list) -> str:
    """
    Returns a human-readable description of a list of tools.
    Injected into the agent's system prompt so it knows what it can do.
    """
    descriptions = {
        "mock_send_email":     "send_email(to, subject, body) — send an email",
        "mock_search_web":     "search_web(query) — search the internet",
        "mock_read_sheet":     "read_sheet(sheet_name) — read Google Sheets data",
        "mock_create_task":    "create_task(task, project) — create a project task",
        "mock_query_database": "query_database(query) — run a database query",
        "save_to_workspace":   "save_to_workspace(content) — save output to shared memory",
        "read_from_workspace": "read_from_workspace(agent_name) — read another agent's output",
    }
    lines = [descriptions.get(t, t) for t in tool_names if t in descriptions]
    return "\nAvailable tools:\n" + "\n".join(f"  • {l}" for l in lines) if lines else ""
