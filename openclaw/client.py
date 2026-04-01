# ============================================================
# openclaw/client.py
# ------------------------------------------------------------
# HTTP client for the OpenClaw agent runtime gateway.
# Used for Customer Support and Data Analyst agents (Phase 3).
#
# OpenClaw handles: tool execution, session management,
# agent lifecycle. You run it separately as a local server.
# ============================================================

import requests


class OpenClawClient:

    def __init__(self, base_url: str = "http://localhost:3000"):
        self.base_url = base_url

    def run_agent(self, agent_name: str, system_prompt: str,
                  user_message: str, allowed_tools: list = None) -> str:
        try:
            response = requests.post(
                f"{self.base_url}/agent/run",
                json={
                    "agent":   agent_name,
                    "system":  system_prompt,
                    "message": user_message,
                    "tools":   allowed_tools or [],
                },
                timeout=30,
            )
            if response.status_code == 200:
                return response.json().get("response", "No response from OpenClaw.")
            return f"❌ OpenClaw error {response.status_code}: {response.text}"

        except requests.exceptions.ConnectionError:
            return (
                "❌ Cannot connect to OpenClaw gateway.\n"
                "Start it with: `openclaw gateway start`\n"
                "Falling back to direct LLM."
            )
        except Exception as e:
            return f"❌ OpenClaw Error: {str(e)}"

    def health_check(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
