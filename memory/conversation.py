# ============================================================
# memory/conversation.py
# ------------------------------------------------------------
# Per-agent conversation history.
# Separate from workspace memory — this tracks the back-and-forth
# chat for each individual agent so GPT has turn-by-turn context.
# ============================================================


class ConversationMemory:
    """Stores per-agent chat history for context in multi-turn conversations."""

    def __init__(self, max_turns: int = 10):
        self.history: dict[str, list] = {}
        self.max_messages = max_turns * 2   # user + assistant per turn

    def get_history(self, agent_name: str) -> list:
        return self.history.get(agent_name, [])

    def add_message(self, agent_name: str, role: str, content: str):
        if agent_name not in self.history:
            self.history[agent_name] = []

        self.history[agent_name].append({"role": role, "content": content})

        # Trim to max window — keep the most recent messages
        if len(self.history[agent_name]) > self.max_messages:
            self.history[agent_name] = self.history[agent_name][-self.max_messages:]

    def clear_agent(self, agent_name: str):
        self.history[agent_name] = []

    def clear_all(self):
        self.history = {}
