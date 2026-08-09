"""Tests for issue #15 — max_total_calls concurrency safety

Verifies that the asyncio.Lock around budget check + increment prevents the
race where two concurrent chat() calls both pass the budget check and both
increment past the limit.
"""
import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import LLMClient, LLMConfig


def test_budget_lock_present():
    """LLMClient 应该有 _budget_lock"""
    cfg = LLMConfig(api_key="x", max_total_calls=10)
    c = LLMClient(cfg)
    assert hasattr(c, "_budget_lock"), "missing _budget_lock"
    import asyncio
    assert isinstance(c._budget_lock, asyncio.Lock)


def test_sequential_chat_under_budget():
    """串行调 N 次, call_count 应该 == N"""
    cfg = LLMConfig(api_key="x", max_total_calls=100, max_retries=0)
    c = LLMClient(cfg)
    # 用 stub
    async def stub(system, user, temperature, max_tokens):
        return "ok"
    c._chat_openai_once = stub
    for _ in range(5):
        result = asyncio.run(c.chat("s", "u"))
        assert result == "ok"
    assert c.call_count == 5


def test_budget_limit_triggers_limit_reached():
    """达到 max_total_calls 后应该返回 [LLM_CALL_LIMIT_REACHED]"""
    cfg = LLMConfig(api_key="x", max_total_calls=2, max_retries=0)
    c = LLMClient(cfg)
    async def stub(system, user, temperature, max_tokens):
        return "ok"
    c._chat_openai_once = stub
    # 第一次 + 第二次 OK, 第三次应该 LIMIT
    r1 = asyncio.run(c.chat("s", "u"))
    r2 = asyncio.run(c.chat("s", "u"))
    r3 = asyncio.run(c.chat("s", "u"))
    assert r1 == "ok"
    assert r2 == "ok"
    assert "LLM_CALL_LIMIT_REACHED" in r3
    # call_count 应该 == 2 (前两次成功, 第三次没进 _chat_openai_once)
    assert c.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_chat_respects_budget():
    """并发 N 次 chat() 全部成功 (没超 budget) 时, call_count 应该 == N"""
    cfg = LLMConfig(api_key="x", max_total_calls=50, max_retries=0)
    c = LLMClient(cfg)
    call_log = []
    async def stub(system, user, temperature, max_tokens):
        # 让 7 个并发都同时进, 然后再释放
        await asyncio.sleep(0.01)
        call_log.append("called")
        return "ok"
    c._chat_openai_once = stub
    # 模拟 Council 的 7-agent 并发
    results = await asyncio.gather(*[c.chat("s", "u") for _ in range(7)])
    assert all(r == "ok" for r in results)
    # 没有 race: 7 次都成功, call_count = 7
    assert c.call_count == 7
    assert len(call_log) == 7


@pytest.mark.asyncio
async def test_concurrent_chat_at_budget_boundary():
    """在 budget 边界并发 20 次 (budget=10), 应该 10 次成功, 10 次 LIMIT"""
    cfg = LLMConfig(api_key="x", max_total_calls=10, max_retries=0)
    c = LLMClient(cfg)
    async def stub(system, user, temperature, max_tokens):
        # 制造并发窗口: 多个请求同时穿过 budget check
        await asyncio.sleep(0.005)
        return "ok"
    c._chat_openai_once = stub
    results = await asyncio.gather(*[c.chat("s", "u") for _ in range(20)])
    ok_count = sum(1 for r in results if r == "ok")
    limit_count = sum(1 for r in results if "LLM_CALL_LIMIT_REACHED" in r)
    # 关键 assertion: ok_count 不能超过 budget
    assert ok_count <= 10, f"race condition: {ok_count} OK calls, budget was 10"
    # 加上 LIMIT 拒绝, 总数 == 20
    assert ok_count + limit_count == 20
    # call_count 反映实际成功的 (不是 check 通过但被 race 覆盖的)
    assert c.call_count == ok_count
