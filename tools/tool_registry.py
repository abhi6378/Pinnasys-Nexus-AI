# ============================================================
# tools/tool_registry.py  (v6)
# ============================================================
# WHAT THIS IS:
#   The complete tool layer. Tools are pre-registered,
#   permission-gated, and triggered via three explicit modes.
#
# THREE TRIGGER MODES (v6):
#   1. auto_tools    — declared in agent config, run unconditionally
#   2. intent_tools  — triggered by agent's intent label (INTENT_TOOL_MAP)
#   3. keyword_tools — triggered by keywords in task text
#
# SPEC EXAMPLE (intent-based):
#   if "email" in task:
#       tool_registry.run("send_email")
#
# RULES:
#   ✅ All tools pre-registered in TOOL_REGISTRY (no auto-discovery)
#   ✅ ToolExecutionLayer is the ONLY gateway
#   ✅ Permission enforced against agent's allowed_tools
#   ✅ Memory tools auto-injected with workspace
#   ✅ Each trigger mode is explicit and auditable
#   ❌ No ad-hoc tool calls from outside this module
#   ❌ No autonomous tool loops
# ============================================================

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ── MOCK TOOLS ────────────────────────────────────────────────

def mock_send_email(to: str = "client@example.com",
                    subject: str = "Follow-up",
                    body: str = "") -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    return (
        f"✉️ Email sent to {to} | Subject: '{subject}' | "
        f"Sent: {ts} | Status: Delivered"
    )


def mock_search_web(query: str = "") -> str:
    return (
        f"🔍 Web search: '{query}' | "
        f"Top result: 'Complete Guide to {query}' — Vol: ~14,200/mo | "
        f"Related: '{query} strategy', '{query} best practices'"
    )


def mock_read_sheet(sheet_name: str = "Sales Data") -> str:
    return (
        f"📊 Sheet '{sheet_name}': 186 rows | Revenue: $52,840 | "
        f"Top: Premium Plan ($26,100) | Growth: South Asia +38% | "
        f"Churn: 4.2% | New trials: 127"
    )


def mock_create_task(task: str = "", project: str = "General",
                     assignee: str = "Team") -> str:
    task_id = f"TASK-{datetime.now().strftime('%H%M%S')}"
    return (
        f"✅ [{task_id}] in '{project}': '{task[:60]}' | "
        f"Assigned: {assignee} | Priority: Normal | Due: +3 days"
    )


def mock_query_database(query: str = "") -> str:
    return (
        f"🗄️ Query: {query[:80]} | 5 rows returned | "
        f"user_101: $4,200 India Pro | user_202: $3,100 US Premium | "
        f"user_303: $2,800 UK Pro | user_404: $1,900 SG Starter"
    )


def mock_get_crm_data(account: str = "") -> str:
    return (
        f"👤 CRM | Open deals: 14 | Pipeline: $182,000 | "
        f"Avg deal: $13,000 | Win rate: 34% | Top rep: Sarah K."
    )


def mock_schedule_post(platform: str = "LinkedIn",
                       content: str = "", time: str = "09:00") -> str:
    return (
        f"📅 Scheduled on {platform} @ {time} | "
        f"Preview: '{content[:60]}...' | Est. reach: 2,400–4,800"
    )


def mock_get_analytics(metric: str = "traffic") -> str:
    return (
        f"📈 Analytics: '{metric}' | Sessions: 28,400 | "
        f"Bounce: 38% | Top page: /pricing (+22%) | Conv: 3.4%"
    )


# ── MEMORY TOOLS ──────────────────────────────────────────────

def save_to_workspace(workspace=None, agent_name: str = "",
                      content: str = "", key: str = "") -> str:
    if workspace and agent_name:
        workspace.save_agent_output(agent_name, content, task=key or "explicit save")
        return f"💾 Saved to Brain AI under '{agent_name}'."
    return "❌ Save failed: missing workspace or agent name."


def read_from_workspace(workspace=None, agent_name: str = "") -> str:
    if not workspace:
        return "❌ Read failed: no workspace."
    if agent_name:
        output = workspace.get_agent_output(agent_name)
        return output if output else f"⚠️ No output from '{agent_name}' yet."
    return workspace.get_all_agent_outputs_summary()


# ── REGISTRY ──────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, callable] = {
    "mock_send_email":     mock_send_email,
    "mock_search_web":     mock_search_web,
    "mock_read_sheet":     mock_read_sheet,
    "mock_create_task":    mock_create_task,
    "mock_query_database": mock_query_database,
    "mock_get_crm_data":   mock_get_crm_data,
    "mock_schedule_post":  mock_schedule_post,
    "mock_get_analytics":  mock_get_analytics,
    "save_to_workspace":   save_to_workspace,
    "read_from_workspace": read_from_workspace,
}

MEMORY_TOOLS: set[str] = {"save_to_workspace", "read_from_workspace"}

TOOL_LABELS: dict[str, str] = {
    "mock_send_email":     "send_email(to, subject, body) — send an email",
    "mock_search_web":     "search_web(query) — keyword/volume lookup",
    "mock_read_sheet":     "read_sheet(sheet_name) — read spreadsheet",
    "mock_create_task":    "create_task(task, project, assignee) — create a task",
    "mock_query_database": "query_database(query) — run a DB query",
    "mock_get_crm_data":   "get_crm_data(account) — CRM pipeline data",
    "mock_schedule_post":  "schedule_post(platform, content, time) — schedule post",
    "mock_get_analytics":  "get_analytics(metric) — web analytics data",
    "save_to_workspace":   "save_to_workspace(content) — persist to Brain AI",
    "read_from_workspace": "read_from_workspace(agent_name) — read agent output",
}


# ── INTENT TOOL MAP (v6 — spec requirement) ───────────────────
# Maps agent intent labels → tools to trigger when that intent fires.
#
# Spec example:
#   if "email" in task:
#       tool_registry.run("send_email")
#
# Here we make that pattern explicit and config-driven:
#   intent="write_email" → triggers mock_send_email (if allowed)

INTENT_TOOL_MAP: dict[str, list[str]] = {
    "write_email":          ["mock_send_email"],
    "seo_optimize":         ["mock_search_web"],
    "analyze_data":         ["mock_read_sheet", "mock_query_database"],
    "build_sales_strategy": ["mock_get_crm_data"],
    "build_strategy":       ["mock_get_analytics"],
    "format_social":        ["mock_schedule_post"],
    "ecom_optimize":        ["mock_read_sheet", "mock_get_analytics"],
    "support_response":     ["mock_create_task"],
    "product_strategy":     ["mock_create_task"],
    "recruitment":          ["mock_create_task"],
    # intents without auto-triggered tools: create_content, public_relations,
    # coaching, assist — these rely on keyword detection instead
}


# ── KEYWORD TRIGGER MAP ───────────────────────────────────────
# Spec example: if "send email" in task → trigger send_email tool.
# Evaluated against task_text.lower().

KEYWORD_TOOL_MAP: list[tuple[str, str]] = [
    ("send email",       "mock_send_email"),
    ("send an email",    "mock_send_email"),
    ("email the",        "mock_send_email"),
    ("draft email",      "mock_send_email"),
    ("search web",       "mock_search_web"),
    ("search for",       "mock_search_web"),
    ("google ",          "mock_search_web"),
    ("keyword research", "mock_search_web"),
    ("read sheet",       "mock_read_sheet"),
    ("spreadsheet",      "mock_read_sheet"),
    ("sales data",       "mock_read_sheet"),
    ("create task",      "mock_create_task"),
    ("create a task",    "mock_create_task"),
    ("add ticket",       "mock_create_task"),
    ("query database",   "mock_query_database"),
    ("run query",        "mock_query_database"),
    ("crm data",         "mock_get_crm_data"),
    ("pipeline data",    "mock_get_crm_data"),
    ("schedule post",    "mock_schedule_post"),
    ("post to ",         "mock_schedule_post"),
    ("analytics",        "mock_get_analytics"),
    ("web traffic",      "mock_get_analytics"),
]


def get_tools_description(tool_names: list[str]) -> str:
    lines = [TOOL_LABELS[t] for t in tool_names if t in TOOL_LABELS]
    if not lines:
        return ""
    return (
        "\n\nYour available tools — call them explicitly when needed:\n"
        + "\n".join(f"  • {l}" for l in lines)
    )


def detect_keyword_tools(task_text: str,
                         allowed_tools: list[str]) -> list[str]:
    """Returns tool names triggered by keywords in task_text."""
    text    = task_text.lower()
    matched = []
    seen    = set()
    for keyword, tool_name in KEYWORD_TOOL_MAP:
        if keyword in text and tool_name in allowed_tools and tool_name not in seen:
            matched.append(tool_name)
            seen.add(tool_name)
            logger.info(f"[TOOL TRIGGER] keyword='{keyword}' → {tool_name}")
    return matched


def detect_intent_tools(intent: str,
                        allowed_tools: list[str]) -> list[str]:
    """
    Returns tool names triggered by agent intent label.
    Only returns tools in the agent's allowed_tools list.

    Example:
        intent="write_email", allowed_tools=[..., "mock_send_email"]
        → ["mock_send_email"]
    """
    candidates = INTENT_TOOL_MAP.get(intent, [])
    matched    = [t for t in candidates if t in allowed_tools]
    if matched:
        logger.info(f"[TOOL TRIGGER] intent='{intent}' → {matched}")
    return matched


# ── TOOL EXECUTION LAYER ──────────────────────────────────────

class ToolExecutionLayer:
    """
    The ONLY gateway for tool execution.
    AgentExecutor uses this — never TOOL_REGISTRY directly.

    Three modes:
      execute_auto_tools()   — auto_tools from config (always run)
      execute_intent_tools() — intent-based triggering (v6 upgrade)
      execute_keyword_tools()— keyword detection in task text
      call_one()             — explicit single tool (orchestrator use)
    """

    def __init__(self, workspace):
        self.workspace = workspace

    def execute_auto_tools(self, tool_names: list[str],
                           agent_name: str,
                           agent_output: str = "") -> dict[str, str]:
        return self._batch(tool_names, agent_name, agent_output, "auto")

    def execute_intent_tools(self, intent: str,
                             allowed_tools: list[str],
                             agent_name: str,
                             agent_output: str = "") -> dict[str, str]:
        """
        Triggers tools based on agent's intent label.
        This is the v6 intent-based tool triggering (spec requirement).
        """
        tools = detect_intent_tools(intent, allowed_tools)
        return self._batch(tools, agent_name, agent_output, "intent")

    def execute_keyword_tools(self, task_text: str,
                              allowed_tools: list[str],
                              agent_name: str,
                              agent_output: str = "") -> dict[str, str]:
        tools = detect_keyword_tools(task_text, allowed_tools)
        return self._batch(tools, agent_name, agent_output, "keyword")

    def call_one(self, tool_name: str, agent_name: str = "",
                 agent_output: str = "", **kwargs) -> str:
        return self._call(tool_name, agent_name, agent_output, **kwargs)

    def _batch(self, tool_names: list[str], agent_name: str,
               agent_output: str, mode: str) -> dict[str, str]:
        results: dict[str, str] = {}
        for tool_name in tool_names:
            try:
                result = self._call(tool_name, agent_name, agent_output)
                results[tool_name] = result
                logger.info(f"[TOOL] {mode.upper()} | {agent_name} → {tool_name}: OK")
            except Exception as e:
                results[tool_name] = f"❌ Tool error: {str(e)}"
                logger.error(f"[TOOL] {agent_name} → {tool_name} FAILED: {e}")
        return results

    def _call(self, tool_name: str, agent_name: str,
              agent_output: str, **kwargs) -> str:
        fn = TOOL_REGISTRY.get(tool_name)
        if not fn:
            return f"❌ Tool '{tool_name}' not in registry."
        if tool_name in MEMORY_TOOLS:
            return fn(workspace=self.workspace, agent_name=agent_name,
                      content=agent_output, **kwargs)
        return fn(**kwargs)

    def is_registered(self, tool_name: str) -> bool:
        return tool_name in TOOL_REGISTRY

    def list_tools(self) -> list[str]:
        return list(TOOL_REGISTRY.keys())
