# ============================================================
# openclaw/client.py  (v5)
# ============================================================

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)


class OpenClawClient:
    def __init__(self, base_url: str = None):
        self.base_url         = (base_url or os.getenv("OPENCLAW_URL", "http://localhost:3000")).rstrip("/")
        self._is_healthy      = None
        self._last_check      = 0.0
        self._health_ttl      = 30.0

    def run_agent(self, agent_name: str, system_prompt: str,
                  user_message: str, allowed_tools: list = None) -> str:
        try:
            r = requests.post(
                f"{self.base_url}/agent/run",
                json={"agent": agent_name, "system": system_prompt,
                      "message": user_message, "tools": allowed_tools or []},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json().get("response", "No response from OpenClaw.")
            return f"❌ OpenClaw HTTP {r.status_code}: {r.text[:200]}"
        except requests.exceptions.ConnectionError:
            return "❌ OpenClaw gateway not reachable. Start: `openclaw gateway start`"
        except requests.exceptions.Timeout:
            return "❌ OpenClaw timed out (30s)."
        except Exception as e:
            return f"❌ OpenClaw error: {str(e)}"

    def health_check(self) -> bool:
        now = time.time()
        if self._is_healthy is not None and (now - self._last_check) < self._health_ttl:
            return self._is_healthy
        try:
            r = requests.get(f"{self.base_url}/health", timeout=3)
            self._is_healthy = r.status_code == 200
        except Exception:
            self._is_healthy = False
        self._last_check = now
        logger.info(f"[OPENCLAW] Health: {'OK' if self._is_healthy else 'OFFLINE'}")
        return self._is_healthy
