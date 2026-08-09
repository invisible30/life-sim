"""Tests for LLMClient Anthropic provider routing (issue #3)

Before this fix, the README/`.env.example` advertised Anthropic support but
chat() would raise NotImplementedError for any LLM_PROVIDER=anthropic.

After the fix, Anthropic must:
- Initialize an AsyncAnthropic client (not the OpenAI one)
- Route _chat_with_retry through _chat_anthropic_once
- Recognize anthropic.* exception types in _is_retryable

We do NOT make a real API call here (CI must be hermetic). Instead we patch
the SDK methods to return canned responses and verify routing.
"""
import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_anthropic_config_loading(monkeypatch):
    """设置 LLM_PROVIDER=anthropic + env 后, LLMConfig 正确解析"""
    from llm.client import LLMClient
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    cfg = LLMClient._load_from_env()
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-3-5-sonnet-latest"
    assert cfg.api_key == "sk-ant-test"
    # 注意: 不要 monkeypatch.delenv LLM_PROVIDER, 因为 .env 文件可能覆盖
    # 测试假设 env 没设时 _load_from_env 会用 openai 默认


def test_anthropic_client_init(monkeypatch):
    """Anthropic client 应该是 AsyncAnthropic 实例"""
    import anthropic
    from llm.client import LLMClient, LLMConfig
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
        api_key="sk-ant-test",
    )
    client = LLMClient(cfg)
    assert isinstance(client._client, anthropic.AsyncAnthropic)


def test_anthropic_routes_to_anthropic_method(monkeypatch):
    """LLM_PROVIDER=anthropic 时, chat() 应该走 _chat_anthropic_once"""
    import anthropic
    from llm.client import LLMClient, LLMConfig

    cfg = LLMConfig(
        provider="anthropic",
        model="claude-3-5-sonnet-latest",
        api_key="sk-ant-test",
        max_retries=0,
    )
    client = LLMClient(cfg)

    # stub the anthropic_once method
    captured = {}

    async def fake_once(system, user, temperature, max_tokens):
        captured["system"] = system
        captured["user"] = user
        captured["provider"] = "anthropic"
        return "stubbed anthropic response"

    client._chat_anthropic_once = fake_once
    result = asyncio.run(client.chat("sys", "user"))
    assert result == "stubbed anthropic response"
    assert captured["provider"] == "anthropic"
    assert captured["system"] == "sys"
    assert captured["user"] == "user"


def test_is_retryable_recognizes_anthropic_errors(monkeypatch):
    """_is_retryable 应该把 anthropic.* 异常也视为可重试 (rate limit / 5xx / timeout)"""
    import anthropic
    from llm.client import LLMClient, LLMConfig
    cfg = LLMConfig(provider="anthropic", api_key="sk-ant-test")
    client = LLMClient(cfg)

    # Construct a proper httpx.Response-like object so APIStatusError accepts it
    class _FakeReq:
        pass

    class _FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}
            self.request = _FakeReq()

    class FakeStatusError(anthropic.APIStatusError):
        def __init__(self, code):
            self.status_code = code
            super().__init__(
                message=f"fake {code}",
                response=_FakeResp(code),
                body=None,
            )

    # 429 / 5xx -> retry
    assert client._is_retryable(FakeStatusError(429)) is True
    assert client._is_retryable(FakeStatusError(500)) is True
    # 4xx (除 429) -> 不 retry
    assert client._is_retryable(FakeStatusError(400)) is False
    assert client._is_retryable(FakeStatusError(401)) is False
    # timeout -> retry
    assert client._is_retryable(
        anthropic.APITimeoutError(request=_FakeReq())
    ) is True


def test_chat_returns_llm_error_when_anthropic_sdk_missing(monkeypatch):
    """如果 anthropic SDK 没装, 不能直接 raise ImportError, 应该返回 [LLM_ERROR]"""
    from llm.client import LLMClient, LLMConfig
    cfg = LLMConfig(provider="anthropic", api_key="sk-ant-test", max_retries=0)
    client = LLMClient(cfg)
    # 删掉 _client, 然后 monkeypatch _chat_anthropic_once 抛 ImportError
    client._chat_anthropic_once = lambda *a, **k: (_ for _ in ()).throw(ImportError("anthropic not installed"))
    # 直接调 _chat_with_retry, 应该走完重试并返回 [LLM_ERROR]
    result = asyncio.run(client._chat_with_retry(
        client._chat_anthropic_once, "sys", "user", None, None
    ))
    assert "[LLM_ERROR]" in result
    assert "ImportError" in result
