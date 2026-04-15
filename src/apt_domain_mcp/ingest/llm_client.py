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
            timeout=float(os.getenv("LITELLM_TIMEOUT", "120")),
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


_diag_first_content_printed = False


def chat_json(system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any] | None:
    """Call the chat endpoint and return parsed JSON, or None on any failure.
    The system prompt must instruct the model to return strict JSON only."""
    global _diag_first_content_printed
    client = get_client()
    if client is None:
        return None

    # LiteLLM proxy 경유 Bedrock Claude Sonnet 4.6는 response_format={"type":"json_object"}를
    # 전달받으면 choices[0].message.content를 빈 "{}"로만 반환하는 이슈가 있다.
    # 따라서 기본값은 OFF. 모델이 ```json ... ``` fence로 감싼 JSON을 돌려주더라도
    # 본 함수의 파서가 fence를 제거하므로 문제없다.
    # 다른 provider(예: OpenAI direct)에서 strict JSON mode가 필요한 경우
    # LITELLM_USE_JSON_MODE=1을 명시 설정.
    use_response_format = os.getenv("LITELLM_USE_JSON_MODE", "0") == "1"
    kwargs: dict[str, Any] = {
        "model": get_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if use_response_format:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        # Log with error class so we can tell 400 (unsupported param) vs 429 vs 5xx
        log.warning("LLM call failed type=%s msg=%s", type(e).__name__, str(e)[:300])
        return None

    # Print first raw content for diagnostics
    if not _diag_first_content_printed:
        _diag_first_content_printed = True
        print(f"[llm_client] first raw content ({len(content)} chars): {content[:500]!r}")

    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    # Tolerate extra trailing text after JSON object: find first '{' ... matching '}'
    if content and content[0] != "{":
        start = content.find("{")
        if start >= 0:
            content = content[start:]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        log.warning("LLM returned non-JSON content (err=%s): %s", e, content[:300])
        return None


def _is_timeout_error(e: Exception) -> bool:
    return type(e).__name__ in ("APITimeoutError", "Timeout", "ReadTimeout")


def chat_text(system: str, user: str, *, max_tokens: int = 4096) -> str | None:
    """Plain text chat helper for non-JSON output (e.g., wiki generation).
    Retries once on timeout with an extended per-call deadline.
    Returns stripped content, or None on any failure."""
    client = get_client()
    if client is None:
        return None

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    def _call(use_client):
        return use_client.chat.completions.create(
            model=get_model(),
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )

    try:
        resp = _call(client)
    except Exception as e:  # noqa: BLE001
        if _is_timeout_error(e):
            log.info("LLM chat_text timed out, retrying once with extended timeout")
            try:
                resp = _call(client.with_options(timeout=300.0))
            except Exception as e2:  # noqa: BLE001
                log.warning(
                    "LLM chat_text retry failed type=%s msg=%s",
                    type(e2).__name__,
                    str(e2)[:300],
                )
                return None
        else:
            log.warning(
                "LLM chat_text failed type=%s msg=%s", type(e).__name__, str(e)[:300]
            )
            return None

    content = resp.choices[0].message.content or ""
    return content.strip() or None
