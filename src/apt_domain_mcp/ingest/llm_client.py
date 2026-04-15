"""LiteLLM proxy client for ingest-time LLM calls.

Portal 환경에서는 내부 LiteLLM proxy가 `us.anthropic.claude-sonnet-4-6`
(AWS Bedrock 경유)을 기본 제공한다. 외부 네트워크 접근 없이 사내 인프라로만
동작하므로 본 모듈은 해당 proxy를 OpenAI-compatible client로 호출한다.

Env vars (우선순위 순서):
    LITELLM_BASE_URL | LITELLM_PROXY_URL | OPENAI_BASE_URL
    LITELLM_API_KEY  | LITELLM_MASTER_KEY | OPENAI_API_KEY
    LITELLM_MODEL    (default: us.anthropic.claude-sonnet-4-6)

base_url 또는 api_key 중 하나라도 비어 있으면 `is_available()`이 False를
반환하며, 상위 호출처는 keyword fallback으로 동작한다.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"


def _first_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return ""


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=_first_env("LITELLM_BASE_URL", "LITELLM_PROXY_URL", "OPENAI_BASE_URL"),
            api_key=_first_env("LITELLM_API_KEY", "LITELLM_MASTER_KEY", "OPENAI_API_KEY"),
            model=_first_env("LITELLM_MODEL") or DEFAULT_MODEL,
            timeout=float(os.getenv("LITELLM_TIMEOUT", "30")),
        )

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)


_client = None
_client_config: LLMConfig | None = None


def get_client():
    """Return a lazily-initialized OpenAI client pointed at the LiteLLM proxy.
    Returns None when required env is missing."""
    global _client, _client_config

    if _client is not None:
        return _client

    cfg = LLMConfig.from_env()
    if not cfg.is_available():
        log.info("LiteLLM client not configured (base_url/api_key missing) — tagger will fall back to keyword rules")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai package not installed — tagger will fall back to keyword rules")
        return None

    _client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout)
    _client_config = cfg
    log.info("LiteLLM client initialized model=%s base_url=%s", cfg.model, cfg.base_url)
    return _client


def get_model() -> str:
    if _client_config is not None:
        return _client_config.model
    return LLMConfig.from_env().model


def chat_json(system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any] | None:
    """Call the chat endpoint and return parsed JSON, or None on any failure.
    The system prompt must instruct the model to return strict JSON only."""
    client = get_client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=get_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        log.warning("LLM call failed: %s — falling back", e)
        return None

    content = content.strip()
    if content.startswith("```"):
        # strip ```json ... ```
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        log.warning("LLM returned non-JSON content: %s (err=%s)", content[:200], e)
        return None
