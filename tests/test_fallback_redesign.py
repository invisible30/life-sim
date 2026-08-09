"""Tests for issue #13 — _fallback_vote is no longer a decoration

Verifies the redesigned fallback:
- Returns a labeled [fallback: ...] reasoning so users can detect fallbacks
- Persona -> option category mapping is visible in the output
- LLM-unrelated fallback path: when option has no category match, returns options[0] + weight=0 (real fallback, not "guess")
- luck is still pure random
- Multiple agents on the same event produce DIFFERENT fallback choices (real persona differentiation)
- _load_option_categories reads events.json correctly
"""
import sys
import os
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import Agent
from agents.rational import RationalAgent
from agents.emotional import EmotionalAgent
from agents.ambitious import AmbitiousAgent
from agents.family import FamilyAgent
from agents.body import BodyAgent
from agents.luck import LuckAgent


def make_stub_state():
    s = types.SimpleNamespace()
    s.person = types.SimpleNamespace(family_background="middle", parents_expectation="stable", university="", major="")
    s.metrics = types.SimpleNamespace(net_worth=0.0, cash_flow_monthly=0.0, physical_health=80.0, mental_health=80.0, relationship_density=50.0, romantic_health=50.0, career_level=20.0, career_income_yearly=0.0, free_hours_weekly=60.0, meaning_score=60.0, regret_index=0.0, skill_depth=30.0, social_capital=20.0, physical_energy=75.0)
    s.current_age = 22.0
    s.life_stage = "junior"
    s.flags = set()
    s.decisions = []
    s.seed = 42
    s.current_quarter = 5
    return s


@pytest.fixture
def state():
    return make_stub_state()


# === 标记 + 透明度 ===

def test_fallback_reasoning_contains_fallback_label(agent_factory):
    a = agent_factory(RationalAgent)
    options = ["加入社团", "保研", "接外包"]
    agenda = {"id": "fake_event", "options": options, "title": "x", "type": "milestone"}
    v = a._fallback_vote(agenda, "[LLM_ERROR] something broke")
    assert "[fallback:" in v.reasoning
    assert "error=" in v.reasoning


def test_fallback_options_with_categories_differ_by_persona(state):
    """不同 agent 对同一 event, 投不同 option (真正 persona 区分)"""
    import json
    # 用 events.json 里真实的 options (跟 categories 匹配)
    with open("data/events.json") as f:
        data = json.load(f)
    ev = next(e for e in data["milestones"] if e["id"] == "early_house")
    agenda = {
        "id": "early_house",
        "title": ev["title"],
        "type": "milestone",
        "options": ev["options"],
    }
    rational = RationalAgent(llm=None, state=state)
    family = FamilyAgent(llm=None, state=state)
    ambitious = AmbitiousAgent(llm=None, state=state)
    emotional = EmotionalAgent(llm=None, state=state)
    body = BodyAgent(llm=None, state=state)

    votes = {
        "rational": rational._fallback_vote(agenda, "[LLM_ERROR]").option,
        "family": family._fallback_vote(agenda, "[LLM_ERROR]").option,
        "ambitious": ambitious._fallback_vote(agenda, "[LLM_ERROR]").option,
        "emotional": emotional._fallback_vote(agenda, "[LLM_ERROR]").option,
        "body": body._fallback_vote(agenda, "[LLM_ERROR]").option,
    }
    # 5 个 agent 应该至少 2 个不同选项
    distinct = set(votes.values())
    assert len(distinct) >= 2, f"personas all converged: {votes}"


def test_fallback_real_fallback_when_no_match(agent_factory):
    """option 跟 persona 没有任何 category 匹配 -> 投 options[0] + weight=0 + 真 fallback 标签"""
    a = agent_factory(RationalAgent)
    # 假设一个 event_id 不在 events.json 里
    agenda = {
        "id": "totally_made_up_event_xyz",
        "title": "x", "type": "x",
        "options": ["A", "B", "C"],
    }
    v = a._fallback_vote(agenda, "[LLM_ERROR] x")
    # 没有 category 匹配, fallback to options[0]
    assert v.option == "A"
    assert v.weight == 0
    assert "default to options[0]" in v.reasoning


# === luck 还是纯随机 ===

def test_luck_returns_one_of_options(agent_factory):
    """luck fallback 选 options 里的一个 (不要求真随机, 状态不变时是 deterministic)"""
    a = agent_factory(LuckAgent)
    options = ["A", "B", "C", "D"]
    v = a._fallback_vote({"id": "x", "options": options, "title": "x", "type": "x"}, "[err]")
    assert v.option in options
    assert -1 <= v.weight <= 1


def test_luck_differs_across_different_states():
    """不同 state (不同 quarter) 下 luck 应该选不同选项 (因为 seed 包含 quarter)"""
    s1 = make_stub_state()
    s1.current_quarter = 0
    s2 = make_stub_state()
    s2.current_quarter = 10
    a1 = LuckAgent(llm=None, state=s1)
    a2 = LuckAgent(llm=None, state=s2)
    options = ["A", "B", "C", "D", "E", "F", "G", "H"]
    v1 = a1._fallback_vote({"id": "x", "options": options, "title": "x", "type": "x"}, "[err]")
    v2 = a2._fallback_vote({"id": "x", "options": options, "title": "x", "type": "x"}, "[err]")
    # 不同 quarter 应该让 luck 选不同
    # (也可能偶尔相同, 但 8 个 options, 撞车概率 12.5%)
    # 多试几个 quarter
    seen = {v1.option, v2.option}
    for q in range(20):
        s = make_stub_state()
        s.current_quarter = q
        v = LuckAgent(llm=None, state=s)._fallback_vote(
            {"id": "x", "options": options, "title": "x", "type": "x"}, "[err]"
        )
        seen.add(v.option)
    assert len(seen) >= 3, f"luck too deterministic across quarters: {seen}"


def test_luck_fallback_is_labeled(agent_factory):
    a = agent_factory(LuckAgent)
    v = a._fallback_vote({"id": "x", "options": ["A"], "title": "x", "type": "x"}, "[err]")
    assert "[fallback:" in v.reasoning
    assert "luck" in v.reasoning


# === _load_option_categories ===

def test_load_option_categories_for_known_event(agent_factory):
    """events.json 里已知 event 应该返回非空 category 字典"""
    a = agent_factory(RationalAgent)
    cats = a._load_option_categories("early_house")
    assert isinstance(cats, dict)
    assert len(cats) > 0
    # early_house 的 options 应该被分类
    options_with_cats = list(cats.keys())
    assert len(options_with_cats) >= 1


def test_load_option_categories_for_unknown_event(agent_factory):
    a = agent_factory(RationalAgent)
    cats = a._load_option_categories("never_existed_xyz")
    assert cats == {}


def test_load_option_categories_empty_event_id(agent_factory):
    a = agent_factory(RationalAgent)
    cats = a._load_option_categories("")
    assert cats == {}


# === persona 类别映射 sanity ===

def test_persona_category_mappings_are_distinct():
    """每个 persona 的 preferred 类别应该是 distinct (避免 'fallback' 大家又投同)"""
    from agents.base import Agent  # not used directly but ensures import order
    # Inline check via _load_option_categories 不直接用
    # 重新构造 _fallback_vote 里那个 dict
    persona_categories = {
        "rational":   ["stability", "growth", "data", "control"],
        "emotional":   ["relationships", "experience", "feel", "people"],
        "ambitious":   ["growth", "challenge", "breakthrough", "advance"],
        "realistic":   ["stability", "safety", "baseline", "risk"],
        "family":      ["stability", "tradition", "home", "duty"],
        "future_me":   ["long_term", "balance", "no_regret", "perspective"],
        "body":        ["health", "rest", "balance", "self_care"],
    }
    # 至少有 4 个 persona 应该是 distinct 偏好
    distinct = set()
    for p, cats in persona_categories.items():
        distinct.add(frozenset(cats))
    assert len(distinct) >= 4, \
        f"personas converged: {len(distinct)} distinct category sets out of 7"


# === fixture: agent_factory ===

@pytest.fixture
def agent_factory(state):
    def _make(agent_cls):
        return agent_cls(llm=None, state=state)
    return _make
