"""Tests for issue #6 — sequential vs parallel debate

These tests verify that _respond_round:
- In sequential mode (default), each agent sees the prior agent's response
- In parallel mode (legacy), all agents see the same prev_views snapshot
- Exceptions on one agent don't abort the round
- LIFE_DEBATE_MODE env var controls the mode
"""
import sys
import os
import asyncio
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meeting.council import Council
from agents.base import Agent, AgentView


class _StubState:
    seed = 42
    current_quarter = 0
    current_age = 18.0
    def __init__(self):
        self.metrics = types.SimpleNamespace(
            net_worth=0.0, cash_flow_monthly=0.0, physical_health=80.0,
            mental_health=80.0, relationship_density=50.0, romantic_health=50.0,
            career_level=20.0, career_income_yearly=0.0, free_hours_weekly=60.0,
            meaning_score=60.0, regret_index=0.0, skill_depth=30.0,
            social_capital=20.0, physical_energy=75.0,
        )
        self.person = types.SimpleNamespace(
            family_background="middle", parents_expectation="stable",
            university="", major="",
        )
        self.flags = set()
        self.decisions = []


class _TrackingAgent(Agent):
    """Records what `other_views` it received each call, so we can verify
    sequential vs parallel ordering."""
    name = "tracker"
    voice = "tracker"
    emoji = "🔍"

    def __init__(self, llm, state, tag):
        super().__init__(llm, state)
        self.tag = tag
        self.calls: list[list[AgentView]] = []

    def persona_prompt(self) -> str:
        return "tracking only"

    async def respond(self, agenda, other_views, round_num):
        # Snapshot what we got this call
        self.calls.append(list(other_views))
        return AgentView(
            agent=self.tag, role=self.tag, emoji=self.emoji,
            content=f"response from {self.tag} (saw {len(other_views)} prior views)",
            round=round_num,
        )


@pytest.fixture
def council():
    """Council with 3 tracking agents (no luck, no LLM)."""
    state = _StubState()
    council = Council.__new__(Council)
    council.state = state
    council.enabled = {}
    a1 = _TrackingAgent(llm=None, state=state, tag="alpha")
    a2 = _TrackingAgent(llm=None, state=state, tag="beta")
    a3 = _TrackingAgent(llm=None, state=state, tag="gamma")
    council.agents = [a1, a2, a3]
    # 1 concurrency so sequential vs parallel is observable
    council._llm_sem = asyncio.Semaphore(1)
    return council, a1, a2, a3


def test_sequential_each_agent_sees_prior_responses(council, monkeypatch):
    """Sequential 模式: alpha 只看 prev, beta 看 prev + alpha, gamma 看 prev + alpha + beta"""
    monkeypatch.setenv("LIFE_DEBATE_MODE", "sequential")
    c, a1, a2, a3 = council
    prev_views = [
        AgentView(agent="r1", role="r1", emoji="x", content="initial view from r1", round=1),
        AgentView(agent="r2", role="r2", emoji="x", content="initial view from r2", round=1),
    ]
    result = asyncio.run(c._respond_round({"title": "t", "options": []}, prev_views, 2))

    # alpha 看到的就是 prev_views
    assert len(a1.calls[0]) == 2
    assert a1.calls[0][0].content == "initial view from r1"

    # beta 看到 prev + alpha 的回应
    assert len(a2.calls[0]) == 3
    assert a2.calls[0][2].content.startswith("response from alpha")

    # gamma 看到 prev + alpha + beta
    assert len(a3.calls[0]) == 4
    assert a3.calls[0][2].content.startswith("response from alpha")
    assert a3.calls[0][3].content.startswith("response from beta")


def test_parallel_all_agents_see_same_snapshot(council, monkeypatch):
    """Parallel 模式: 三个 agent 都只看到 prev, 不互相参考"""
    monkeypatch.setenv("LIFE_DEBATE_MODE", "parallel")
    c, a1, a2, a3 = council
    prev_views = [
        AgentView(agent="r1", role="r1", emoji="x", content="initial view from r1", round=1),
    ]
    result = asyncio.run(c._respond_round({"title": "t", "options": []}, prev_views, 2))
    # 三个 agent 都只看到 1 个 prev_view, 没人看到其他 agent 的回应
    assert len(a1.calls[0]) == 1
    assert len(a2.calls[0]) == 1
    assert len(a3.calls[0]) == 1
    assert a1.calls[0][0].content == "initial view from r1"


def test_sequential_default_mode(council, monkeypatch):
    """不设 LIFE_DEBATE_MODE 时, 默认 sequential (issue acceptance criterion)"""
    monkeypatch.delenv("LIFE_DEBATE_MODE", raising=False)
    c, a1, a2, a3 = council
    prev_views = [
        AgentView(agent="r1", role="r1", emoji="x", content="initial", round=1),
    ]
    result = asyncio.run(c._respond_round({"title": "t", "options": []}, prev_views, 2))
    # 三个 agent 看到不同长度的 views (递增)
    assert len(a1.calls[0]) == 1
    assert len(a2.calls[0]) == 2
    assert len(a3.calls[0]) == 3


def test_sequential_one_agent_failure_doesnt_abort(council, monkeypatch):
    """一个 agent 失败, 其他 agent 继续 (不整个 round abort)"""
    monkeypatch.setenv("LIFE_DEBATE_MODE", "sequential")
    monkeypatch.setenv("LIFE_QUIET", "1")
    c, a1, a2, a3 = council

    # 让 a2 失败
    async def boom(*a, **k):
        raise RuntimeError("simulated LLM failure")
    a2.respond = boom

    prev_views = [AgentView(agent="r1", role="r1", emoji="x", content="init", round=1)]
    result = asyncio.run(c._respond_round({"title": "t", "options": []}, prev_views, 2))
    # alpha + gamma 成功了, beta 失败被跳过
    assert len(result) == 2
    tags = [v.agent for v in result]
    assert "alpha" in tags
    assert "gamma" in tags
    assert "beta" not in tags
