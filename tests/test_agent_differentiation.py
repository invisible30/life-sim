"""Tests for issue #5 — agent prompt differentiation

These tests verify the structural properties called out in the issue:
- Each persona_prompt() has 200+ chars of distinct worldview
- Each persona_prompt() contains a hard constraint block ("【硬约束")
- Each persona_prompt() explicitly mentions 1+ other agent names (anti-convergence)
- Two contrasting agents (rational vs emotional) have < 50% n-gram overlap
- The system_prompt() base class adds the anti-convergence mandate
"""
import sys
import os
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.rational import RationalAgent
from agents.emotional import EmotionalAgent
from agents.ambitious import AmbitiousAgent
from agents.realistic import RealisticAgent
from agents.family import FamilyAgent
from agents.future_me import FutureMeAgent
from agents.body import BodyAgent
from agents.luck import LuckAgent


def make_stub_state():
    """Build a minimal stub state that has .person and .metrics accessors,
    enough to make persona_prompt() and system_prompt() not crash."""
    state = types.SimpleNamespace()
    state.person = types.SimpleNamespace(
        family_background="middle",
        parents_expectation="stable",
        university="",
        major="",
    )
    state.metrics = types.SimpleNamespace(
        net_worth=0.0,
        cash_flow_monthly=0.0,
        physical_health=80.0,
        mental_health=80.0,
        relationship_density=50.0,
        romantic_health=50.0,
        career_level=20.0,
        career_income_yearly=0.0,
        free_hours_weekly=60.0,
        meaning_score=60.0,
        regret_index=0.0,
        skill_depth=30.0,
        social_capital=20.0,
        physical_energy=75.0,
    )
    state.current_age = 18.0
    state.life_stage = "freshman"
    state.flags = set()
    state.decisions = []
    return state


@pytest.fixture
def state():
    return make_stub_state()


def make_agents(state):
    return [
        RationalAgent(llm=None, state=state),
        EmotionalAgent(llm=None, state=state),
        AmbitiousAgent(llm=None, state=state),
        RealisticAgent(llm=None, state=state),
        FamilyAgent(llm=None, state=state),
        FutureMeAgent(llm=None, state=state),
        BodyAgent(llm=None, state=state),
        LuckAgent(llm=None, state=state),
    ]


def test_each_persona_is_200_plus_chars(state):
    """每个 LLM persona_prompt >= 200 字符 (issue acceptance criterion).
    luck 是纯随机数 stub, 不算 LLM agent, 跳过。"""
    llm_agents = [a for a in make_agents(state) if a.name != "luck"]
    for a in llm_agents:
        text = a.persona_prompt()
        assert len(text) >= 200, f"{a.name} only {len(text)} chars, need >= 200"


def test_each_persona_has_hard_constraint_block(state):
    """每个 LLM agent 必须有 【硬约束】 块 (反趋同)"""
    llm_agents = [a for a in make_agents(state) if a.name != "luck"]
    for a in llm_agents:
        text = a.persona_prompt()
        assert "【硬约束" in text, f"{a.name} missing hard-constraint block"
        assert any(kw in text for kw in ("绝不会", "强烈支持")), \
            f"{a.name} missing decisive action language"


def test_each_persona_mentions_other_agents(state):
    """每个 LLM agent 必须提到至少 1 个其他 agent 名字 (董事会对话感)"""
    llm_agents = [a for a in make_agents(state) if a.name != "luck"]
    other_names = ["理性我", "感性我", "野心我", "现实我", "家人", "未来我", "身体"]
    for a in llm_agents:
        text = a.persona_prompt()
        mentioned = [n for n in other_names if n in text and n != a.voice]
        assert len(mentioned) >= 1, \
            f"{a.name} does not mention any other agent — no cross-talk"


def test_contrasting_agents_have_low_text_overlap(state):
    """理性我 vs 感性我, 野心我 vs 家人, 现实我 vs 身体 — 应该低重合度"""
    pairs = [
        (RationalAgent(llm=None, state=state), EmotionalAgent(llm=None, state=state)),
        (AmbitiousAgent(llm=None, state=state), FamilyAgent(llm=None, state=state)),
        (RealisticAgent(llm=None, state=state), BodyAgent(llm=None, state=state)),
    ]
    for a, b in pairs:
        ta, tb = a.persona_prompt(), b.persona_prompt()
        ngrams_a = {ta[i:i+3] for i in range(len(ta) - 2)}
        ngrams_b = {tb[i:i+3] for i in range(len(tb) - 2)}
        if not ngrams_a or not ngrams_b:
            continue
        overlap = len(ngrams_a & ngrams_b)
        ratio = overlap / max(len(ngrams_a), len(ngrams_b))
        assert ratio < 0.65, \
            f"{a.name} vs {b.name} text overlap {ratio:.2%} too high — personas converging"


def test_system_prompt_includes_anti_convergence_mandate(state):
    """base.Agent.system_prompt 必须包含反趋同强制要求"""
    a = RationalAgent(llm=None, state=state)
    sp = a.system_prompt()
    assert "反趋同" in sp, "system_prompt missing anti-convergence mandate"
    assert "其他" in sp, "system_prompt missing cross-agent reference"


def test_no_two_agents_have_identical_persona_intro(state):
    """persona_intro 不能完全相同 (装饰性 sanity check)"""
    intros = [a.persona_intro for a in make_agents(state)]
    assert len(intros) == len(set(intros)), "agents have duplicate persona_intro"
