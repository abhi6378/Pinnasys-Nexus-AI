# ============================================================
# memory/conversation.py  (v5)
# ============================================================
# Per-agent turn-by-turn conversation history.
# Separate from WorkspaceMemory (Brain AI).
#
# WorkspaceMemory  = cross-agent shared context (business, outputs)
# ConversationMemory = per-agent chat history for multi-turn LLM context
# ============================================================


class ConversationMemory:
    """
    Per-agent conversation history for multi-turn context.
    Passed to run_llm() as the `history` list.
    Trimmed to a sliding window of max_turns.
    """

    def __init__(self, max_turns: int = 12):
        self.history:      dict[str, list] = {}
        self.max_messages: int = max_turns * 2

    def get_history(self, agent_name: str) -> list[dict]:
        return list(self.history.get(agent_name, []))

    def add_message(self, agent_name: str, role: str, content: str):
        if agent_name not in self.history:
            self.history[agent_name] = []
        self.history[agent_name].append({"role": role, "content": content})
        if len(self.history[agent_name]) > self.max_messages:
            self.history[agent_name] = self.history[agent_name][-self.max_messages:]

    def get_turn_count(self, agent_name: str) -> int:
        return len(self.history.get(agent_name, [])) // 2

    def clear_agent(self, agent_name: str):
        self.history[agent_name] = []

    def clear_all(self):
        self.history = {}

    def get_agents_with_history(self) -> list[str]:
        return [n for n, msgs in self.history.items() if msgs]
