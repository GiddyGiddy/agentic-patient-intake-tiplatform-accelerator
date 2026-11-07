import httpx
import pytest

from app.agents import intake_agent, kim_agent
from app.core.llm_client import LLMResponseError, generate, parse_json_response
from config import settings


def test_ollama_request_and_response(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "http://llm:11434/")
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "example")
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return httpx.Response(200, json={"response": '{"complete":true}'}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert generate("test", timeout=60, json_mode=True) == '{"complete":true}'
    assert calls == [("http://llm:11434/api/generate", {
        "model": "example", "stream": False, "prompt": "test", "format": "json"
    }, 60)]


def test_foundry_chat_completion(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:5273")
    monkeypatch.setattr(settings, "llm_provider", "foundry_local")
    monkeypatch.setattr(settings, "llm_model", "phi-test")
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return httpx.Response(200, json={"choices": [{"message": {"content": "antwort"}, "finish_reason": "stop"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert generate("test", timeout=60, json_mode=True) == "antwort"
    assert calls == [("http://127.0.0.1:5273/v1/chat/completions", {
        "model": "phi-test", "messages": [{"role": "user", "content": "test"}], "stream": False
    }, 60)]


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": [[]]}, {"choices": [{"message": {"content": ""}}]}, {"choices": [{"message": {"content": "truncated"}, "finish_reason": "length"}]}])
def test_invalid_foundry_responses_fail(monkeypatch, data):
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:5273")
    monkeypatch.setattr(settings, "llm_provider", "foundry_local")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(200, json=data, request=httpx.Request("POST", url)))
    with pytest.raises(LLMResponseError):
        generate("test", timeout=1)


def test_agent_fallbacks_on_provider_error(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:5273")
    def fail(*args, **kwargs):
        raise LLMResponseError("invalid")
    monkeypatch.setattr(intake_agent, "generate", fail)
    monkeypatch.setattr(kim_agent, "generate", fail)
    assert intake_agent._llm_followup("synthetic") is None
    assert intake_agent._llm_assess("synthetic") is None
    assert kim_agent._llm_letter({"patient_id": "test"}) is None
    assert "fallback" in caplog.text


def test_http_error_is_propagated(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:5273")
    monkeypatch.setattr(settings, "llm_provider", "foundry_local")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(503, request=httpx.Request("POST", url)))
    with pytest.raises(httpx.HTTPStatusError):
        generate("test", timeout=1)


def test_invalid_json_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:5273")
    monkeypatch.setattr(settings, "llm_provider", "foundry_local")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(200, content=b"not json", request=httpx.Request("POST", url)))
    with pytest.raises(LLMResponseError):
        generate("test", timeout=1)


@pytest.mark.parametrize("text", ['{"complete":true}', '```json\n{"complete":true}\n```',
                                  ' ```JSON\r\n{"complete":true}\r\n``` '])
def test_json_response_accepts_json_and_full_fence(text):
    assert parse_json_response(text) == {"complete": True}


def test_json_response_rejects_surrounding_prose():
    with pytest.raises(ValueError):
        parse_json_response('result:\n```json\n{"complete":true}\n```')
