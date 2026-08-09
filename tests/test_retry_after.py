"""Tests for issue #17 — Retry-After header respect

Verifies that the LLMClient's retry loop honors the Retry-After header
returned by 429/503 responses, instead of using only its own exponential
backoff.
"""
import sys
import os
import asyncio
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import LLMClient, LLMConfig


class _FakeResp:
    def __init__(self, status, headers):
        self.status_code = status
        self.headers = headers
        self.request = type("R", (), {})()


class _FakeRateLimitError(Exception):
    def __init__(self, retry_after=None, status=429):
        self.status_code = status
        if retry_after is not None:
            self.response = _FakeResp(status, {"retry-after": str(retry_after)})
        else:
            self.response = _FakeResp(status, {})


def test_parse_retry_after_delta_seconds():
    """Retry-After: 30 -> 30.0"""
    exc = _FakeRateLimitError(retry_after=30)
    result = LLMClient._parse_retry_after(exc)
    assert result == 30.0


def test_parse_retry_after_decimal():
    """Retry-After: 1.5 -> 1.5"""
    exc = _FakeRateLimitError(retry_after=1.5)
    result = LLMClient._parse_retry_after(exc)
    assert result == 1.5


def test_parse_retry_after_http_date():
    """Retry-After: HTTP-date (e.g. 1s in the future) -> ~1.0"""
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone
    future = datetime.now(timezone.utc) + timedelta(seconds=1)
    http_date = format_datetime(future)
    exc = _FakeRateLimitError(retry_after=http_date)
    result = LLMClient._parse_retry_after(exc)
    # 应该返回 0-2 之间 (允许一些时钟漂移)
    assert 0 <= result <= 2, f"got {result}"


def test_parse_retry_after_missing_header():
    """没有 Retry-After header -> None"""
    exc = _FakeRateLimitError(retry_after=None)
    result = LLMClient._parse_retry_after(exc)
    assert result is None


def test_parse_retry_after_unparseable():
    """garbage 值 -> None (不抛)"""
    exc = _FakeRateLimitError(retry_after="not a number or date")
    result = LLMClient._parse_retry_after(exc)
    assert result is None


def test_parse_retry_after_no_response_attr():
    """没 response 属性的 exception -> None"""
    class Plain(Exception):
        pass
    result = LLMClient._parse_retry_after(Plain("boom"))
    assert result is None


@pytest.mark.asyncio
async def test_chat_uses_retry_after_when_429():
    """429 带 Retry-After: 0.5 -> 实际 wait >= 0.5s (而不是默认 backoff)"""
    import openai
    cfg = LLMConfig(api_key="x", max_retries=1, max_total_calls=10,
                    retry_base_delay=0.001, retry_max_delay=10.0)
    c = LLMClient(cfg)
    call_count = 0
    async def stub(system, user, temperature, max_tokens):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 构造一个 openai.RateLimitError, 带 Retry-After: 2
            raise openai.RateLimitError(
                message="rate limited",
                response=_FakeResp(429, {"retry-after": "2"}),
                body=None,
            )
        return "ok"
    c._chat_openai_once = stub
    t0 = time.monotonic()
    result = await c.chat("s", "u")
    elapsed = time.monotonic() - t0
    assert result == "ok"
    # Retry-After=2 应该让我们至少等 2s
    assert elapsed >= 2.0, f"expected >= 2s wait (Retry-After=2), got {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_chat_caps_retry_after_to_max():
    """Retry-After=999 (荒谬值) 应该被 cap 到 retry_max_delay=2s"""
    import openai
    cfg = LLMConfig(api_key="x", max_retries=1, max_total_calls=10,
                    retry_base_delay=0.001, retry_max_delay=2.0)
    c = LLMClient(cfg)
    call_count = 0
    async def stub(system, user, temperature, max_tokens):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise openai.RateLimitError(
                message="rate limited",
                response=_FakeResp(429, {"retry-after": "999"}),
                body=None,
            )
        return "ok"
    c._chat_openai_once = stub
    t0 = time.monotonic()
    result = await c.chat("s", "u")
    elapsed = time.monotonic() - t0
    assert result == "ok"
    # 999 被 cap 到 2.0, 再加 jitter, 所以 wait < 3s
    assert 0.5 <= elapsed <= 3.0, f"expected ~1-2s wait (capped), got {elapsed:.2f}s"
