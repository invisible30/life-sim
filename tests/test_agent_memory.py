"""Tests for issue #16 — agent memory is no longer dead data

Verifies:
- _memory_summary includes my_stance / council_votes / agent name
- _memory_summary 触发 outcome warning 当 outcome 含 ↓
- remember_decision 存了新字段
- memory 长度 5 (不是 3)
- 空 memory 返回空字符串
"""
import sys
import os
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import Agent
from agents.rational import RationalAgent


def make_stub_state():
    state = types.SimpleNamespace()
    state.person = types.SimpleNamespace(
        family_background="middle", parents_expectation="stable",
        university="", major="",
    )
    state.metrics = types.SimpleNamespace(
        net_worth=0.0, cash_flow_monthly=0.0, physical_health=80.0,
        mental_health=80.0, relationship_density=50.0, romantic_health=50.0,
        career_level=20.0, career_income_yearly=0.0, free_hours_weekly=60.0,
        meaning_score=60.0, regret_index=0.0, skill_depth=30.0,
        social_capital=20.0, physical_energy=75.0,
    )
    state.current_age = 18.0
    state.life_stage = "freshman"
    state.flags = set()
    state.decisions = []
    return state


@pytest.fixture
def agent():
    return RationalAgent(llm=None, state=make_stub_state())


def test_remember_decision_stores_new_fields(agent):
    """remember_decision 应该存 agent, my_stance, council_votes"""
    agent.remember_decision({
        "quarter": 5, "agent": "rational",
        "title": "Test event",
        "chosen": "A",
        "outcome": "事业↑",
        "my_stance": "I think A is best because...",
        "council_votes": {"rational": 2, "emotional": -1, "family": 1},
    })
    assert len(agent.memory) == 1
    mem = agent.memory[0]
    assert mem["agent"] == "rational"
    assert mem["my_stance"] == "I think A is best because..."
    assert mem["council_votes"] == {"rational": 2, "emotional": -1, "family": 1}
    assert mem["outcome"] == "事业↑"


def test_memory_summary_empty_when_no_memory(agent):
    assert agent._memory_summary() == ""


def test_memory_summary_includes_my_stance(agent):
    agent.remember_decision({
        "quarter": 1, "agent": "rational", "title": "选社团",
        "chosen": "A", "outcome": "平稳",
        "my_stance": "I prefer A",
    })
    summary = agent._memory_summary()
    assert "I prefer A" in summary


def test_memory_summary_includes_council_votes(agent):
    agent.remember_decision({
        "quarter": 1, "agent": "rational", "title": "选社团",
        "chosen": "A", "outcome": "平稳",
        "council_votes": {"rational": 2, "emotional": -1, "family": 1, "ambitious": 2},
    })
    summary = agent._memory_summary()
    # 应该列出同盟 (同 positive) 和反对 (negative)
    assert "council 投票" in summary
    assert "你支持" in summary
    # 同盟应该包含 emotional (negative -> 反对), family/ambitious (positive -> 同盟)
    assert "同盟" in summary
    assert "反对" in summary


def test_memory_summary_includes_opposition_label(agent):
    """agent 当时投反对, summary 应该标 '你反对'"""
    agent.remember_decision({
        "quarter": 1, "agent": "rational", "title": "选社团",
        "chosen": "A", "outcome": "平稳",
        "council_votes": {"rational": -2, "emotional": 2, "family": 1},
    })
    summary = agent._memory_summary()
    assert "你反对" in summary


def test_memory_summary_warns_on_negative_outcome(agent):
    """outcome 含 ↓ / 失败 / 后悔 应该触发 warning"""
    for bad_outcome in ["身体↓", "心理失败", "事业后悔", "平稳"]:
        agent.remember_decision({
            "quarter": 1, "agent": "rational", "title": "X",
            "chosen": "A", "outcome": bad_outcome,
        })
        if "↓" in bad_outcome or "失败" in bad_outcome or "后悔" in bad_outcome:
            summary = agent._memory_summary()
            assert "⚠️" in summary, f"no warning for outcome={bad_outcome!r}"
            # 清除, 下次测试
            agent.memory.clear()


def test_memory_summary_uses_5_recent_decisions_not_3(agent):
    """issue #16 acceptance: 之前 3 条, 现在 5 条"""
    for i in range(7):
        agent.remember_decision({
            "quarter": i, "agent": "rational", "title": f"event {i}",
            "chosen": "X", "outcome": "平稳",
        })
    summary = agent._memory_summary()
    # 应该含 event 2,3,4,5,6 (5 条), 不含 0,1
    assert "event 6" in summary
    assert "event 2" in summary
    assert "event 1" not in summary
    assert "event 0" not in summary


def test_memory_capped_at_10(agent):
    """remember_decision 应该 cap memory 在 10 条 (防止 memory leak)"""
    for i in range(15):
        agent.remember_decision({
            "quarter": i, "agent": "rational", "title": f"e{i}",
            "chosen": "X", "outcome": "平稳",
        })
    assert len(agent.memory) == 10
    # 应该是最新的 10 条 (5-14)
    assert agent.memory[0]["quarter"] == 5
    assert agent.memory[-1]["quarter"] == 14
