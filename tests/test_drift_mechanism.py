"""Tests for Council.update_agent_weights — drift mechanism (issue #1)

These tests verify the previously-empty method actually mutates agent.drift
based on (vote × outcome) signals, with clamping at [-0.5, 0.5].
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meeting.council import Council
from core.state import DecisionRecord
from agents.rational import RationalAgent
from agents.emotional import EmotionalAgent


class _StubState:
    """Minimal stand-in for LifeState; Council only uses it via agents."""
    seed = 42
    current_quarter = 0
    current_age = 18.0
    def __init__(self):
        self.metrics = type("M", (), {})()
        self.person = type("P", (), {})()


@pytest.fixture
def council():
    """Build a council with stub LLM (None) — drift mechanism doesn't call LLM."""
    state = _StubState()
    council = Council.__new__(Council)  # bypass __init__'s LLM requirement
    council.state = state
    council.enabled = {}
    a1 = RationalAgent(llm=None, state=state)
    a2 = EmotionalAgent(llm=None, state=state)
    council.agents = [a1, a2]
    return council, a1, a2


def make_record(votes: dict[str, int]) -> DecisionRecord:
    return DecisionRecord(
        quarter=0, age=18.0,
        event_title="test", event_description="",
        event_type="milestone", options=["A", "B"],
        chosen="A", votes=votes, debates=[], reasoning="",
        outcome="事业↑",
    )


def test_drift_no_change_on_neutral_outcome(council):
    """outcome_quality == 0 (平稳) -> no drift change"""
    c, a1, a2 = council
    initial_a1 = a1.drift
    initial_a2 = a2.drift
    record = make_record({"rational": 1, "emotional": 1})
    c.update_agent_weights(record, effects={})  # 平稳
    assert a1.drift == initial_a1
    assert a2.drift == initial_a2


def test_drift_up_when_agreed_with_good_outcome(council):
    """rational 投了当选 + outcome 好 -> drift ↑"""
    c, a1, a2 = council
    initial = a1.drift
    record = make_record({"rational": 1, "emotional": -1})
    c.update_agent_weights(record, effects={"net_worth": 10.0, "career_level": 5.0})
    assert a1.drift > initial, f"rational should drift up, got {a1.drift}"
    assert a1.drift == initial + c.DRIFT_STEP


def test_drift_down_when_agreed_with_bad_outcome(council):
    """rational 投了当选 + outcome 坏 -> drift ↓"""
    c, a1, a2 = council
    initial = a1.drift
    record = make_record({"rational": 1, "emotional": -1})
    c.update_agent_weights(record, effects={"net_worth": -10.0, "mental_health": -5.0})
    assert a1.drift < initial, f"rational should drift down, got {a1.drift}"
    assert a1.drift == initial - c.DRIFT_STEP


def test_drift_up_when_dissent_was_right(council):
    """少数派蒙对了: emotional 反对当选, outcome 坏 -> emotional drift ↑"""
    c, a1, a2 = council
    initial = a2.drift
    record = make_record({"rational": 1, "emotional": -1})
    c.update_agent_weights(record, effects={"net_worth": -10.0})  # bad outcome
    assert a2.drift > initial, f"emotional should drift up (蒙对了少数派), got {a2.drift}"


def test_drift_clamped_at_positive_ceiling(council):
    """drift 不会超过 +0.5"""
    c, a1, _a2 = council
    a1.drift = 0.49
    record = make_record({"rational": 1})
    for _ in range(10):  # 触发 10 次
        c.update_agent_weights(record, effects={"net_worth": 10.0})
    assert a1.drift <= 0.5


def test_drift_clamped_at_negative_floor(council):
    """drift 不会低于 -0.5"""
    c, a1, _a2 = council
    a1.drift = -0.49
    record = make_record({"rational": 1})
    for _ in range(10):
        c.update_agent_weights(record, effects={"net_worth": -10.0})
    assert a1.drift >= -0.5


def test_drift_unchanged_for_luck_agent(council):
    """luck 不参与 drift (它根本没 vote 参与 signal)"""
    from agents.luck import LuckAgent
    state = _StubState()
    luck = LuckAgent(llm=None, state=state)
    c, a1, a2 = council
    c.agents.append(luck)
    initial_luck = luck.drift
    record = make_record({"rational": 1, "emotional": 1, "luck": 1})
    c.update_agent_weights(record, effects={"net_worth": 10.0})
    assert luck.drift == initial_luck


def test_drift_zero_vote_no_change(council):
    """agent 弃权 (weight=0) -> drift 不动"""
    c, a1, a2 = council
    initial = a1.drift
    record = make_record({"rational": 0, "emotional": 1})
    c.update_agent_weights(record, effects={"net_worth": 10.0})
    assert a1.drift == initial


def test_drift_current_weight_reflects_drift(council):
    """验证 base.Agent.current_weight 把 drift 也算进去了"""
    c, a1, _a2 = council
    a1.base_weight = 1.0
    a1.drift = 0.3
    assert abs(a1.current_weight - 1.3) < 0.001


def test_drift_accumulates_across_decisions(council):
    """连续 5 次都对 -> drift 累计"""
    c, a1, _a2 = council
    initial = a1.drift
    record = make_record({"rational": 1})
    for _ in range(5):
        c.update_agent_weights(record, effects={"net_worth": 10.0})
    expected = min(0.5, initial + 5 * c.DRIFT_STEP)
    assert a1.drift == expected
