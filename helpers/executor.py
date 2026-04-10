"""
helpers/executor.py  —  Runs a single helper with Brain AI context
"""
from helpers.configs import get_agent
from llm.client import generate


def build_system_prompt(agent: dict, brain_context: str) -> str:
    return f"""You are {agent['name']}, an AI {agent['role']}.

Your goal: {agent['goal']}
Your tone: {agent['tone']}
Your boundaries: {agent['boundaries']}
Output format: {agent['output_format']}

--- BUSINESS CONTEXT (use this to personalize your response) ---
{brain_context}
--- END CONTEXT ---

Always stay in your role. Use the business context to make your response
specific and relevant to this business. Never make up facts not in the context.
"""


def run_agent(agent_key: str, user_input: str, brain_context: str = "") -> dict:
    """
    Executes a single helper.
    Returns: {agent, output, success}
    """
    agent = get_agent(agent_key)
    if not agent:
        return {"agent": agent_key, "output": f"Unknown agent: {agent_key}", "success": False}

    system_prompt = build_system_prompt(agent, brain_context or "No context available.")

    try:
        output = generate(
            prompt=user_input,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=1500
        )
        return {"agent": agent_key, "name": agent["name"], "output": output, "success": True}
    except Exception as e:
        return {"agent": agent_key, "output": f"Error: {str(e)}", "success": False}
