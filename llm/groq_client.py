import json
import logging
from typing import Any, Dict, Optional

import httpx

from config import config

logger = logging.getLogger("clanfall.groq")

SYSTEM_PROMPT = (
    "You are the evidence narrator for a social-deduction game. You will be given one "
    "fact derived from the match record. Rewrite it as a single short, neutral sentence a "
    "player would read on the meeting screen. Do not add any information that is not in "
    "the fact, do not accuse anyone, and do not speculate about roles. "
    'Respond with JSON only: {"headline": "..."}'
)


def _parse_json_content(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        fence = text.rfind("```")
        if fence >= 0:
            text = text[:fence]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class GroqClient:
    """
    Groq OpenAI-compatible chat client. Returns None when the key is missing or
    Groq is unreachable so callers fall back to deterministic templates.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        self.api_key = api_key if api_key is not None else config.llm.groq_api_key
        self.model = model or config.llm.groq_model
        self.base_url = (base_url or config.llm.groq_base_url).rstrip("/")
        self.timeout = config.llm.groq_timeout_seconds
        self.connect_timeout = config.llm.groq_connect_timeout_seconds
        flag = config.llm.groq_enabled if enabled is None else enabled
        self.enabled = bool(flag and self.api_key)

    @property
    def is_configured(self) -> bool:
        return self.enabled

    def ping(self) -> bool:
        if not self.enabled:
            return False
        try:
            with httpx.Client(timeout=httpx.Timeout(2.0, connect=self.connect_timeout)) as client:
                resp = client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Groq ping failed: %s", exc)
            return False

    def generate_json(self, user_prompt: str, system: str = SYSTEM_PROMPT) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=self.connect_timeout)) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                    },
                )
            if resp.status_code != 200:
                logger.warning("Groq HTTP %s: %s", resp.status_code, resp.text[:200])
                return None
            choices = resp.json().get("choices") or []
            if not choices:
                return None
            return _parse_json_content((choices[0].get("message") or {}).get("content"))
        except Exception as exc:
            logger.warning("Groq generate failed: %s", exc)
            return None
