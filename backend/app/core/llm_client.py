"""Local LLM provider adapter. Never log prompts or generated patient data."""

import json
import re

import httpx

from config import settings


class LLMResponseError(ValueError):
    """The provider returned an invalid or incomplete response."""


def parse_json_response(text: str):
    """Parse JSON, allowing a single Markdown code fence around the entire response."""
    match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text.strip(), re.DOTALL | re.IGNORECASE)
    return json.loads(match.group(1) if match else text)


def generate(prompt: str, *, timeout: float, json_mode: bool = False) -> str:
    """Return generated text or raise an explicit provider/response error."""
    base = settings.llm_base_url.rstrip("/")
    if not base:
        raise LLMResponseError("LLM_BASE_URL is not configured")
    if settings.llm_provider == "ollama":
        payload = {"model": settings.llm_model, "stream": False, "prompt": prompt}
        if json_mode:
            payload["format"] = "json"
        response = httpx.post(f"{base}/api/generate", json=payload, timeout=timeout)
    elif settings.llm_provider == "foundry_local":
        payload = {
            "model": settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        response = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
    else:
        raise LLMResponseError("Unsupported LLM provider")
    response.raise_for_status()
    try:
        data = response.json()
        if settings.llm_provider == "ollama":
            text = data["response"]
        else:
            choice = data["choices"][0]
            text = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LLMResponseError("Invalid LLM response structure") from exc
    if settings.llm_provider == "foundry_local" and choice.get("finish_reason") not in (None, "stop"):
        raise LLMResponseError("Incomplete LLM response")
    if not isinstance(text, str) or not text.strip():
        raise LLMResponseError("Empty LLM response")
    return text
