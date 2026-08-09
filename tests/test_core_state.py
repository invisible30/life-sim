"""Tests for core/state.py — issue #3 acceptance: at least one test per module

Covers the deterministic parts of state init and transition:
- Person.derive_school() correctly maps gaokao scores to school tiers
- Person.derive_initial_cash() correctly maps family background to starting cash
- LifeMetrics.as_dict() returns all 14 expected fields
- LifeState.determine_stage() returns reasonable life stages
- init_state_from_config() works with a minimal config

No LLM calls — these are pure deterministic tests.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import Person, Personality, LifeMetrics, LifeState, init_state_from_config


# === Person.derive_school ===

@pytest.mark.parametrize("gaokao,expected_school_substr", [
    (700, "清北复交"),
    (680, "清北复交"),
    (660, "985 头部"),
    (640, "985 头部"),
    (620, "985"),
    (600, "985"),
    (580, "211"),
    (560, "211"),
    (530, "一本"),
    (500, "一本"),
    (450, "二本"),
    (430, "二本"),
    (400, "专科"),
])
def test_derive_school_thresholds(gaokao, expected_school_substr):
    p = Person(gaokao_score=gaokao)
    assert expected_school_substr in p.derive_school(), \
        f"gaokao={gaokao} -> {p.derive_school()!r}, expected substring {expected_school_substr!r}"


def test_derive_school_returns_non_empty_string():
    p = Person(gaokao_score=500)
    assert len(p.derive_school()) > 0


# === Person.derive_initial_cash ===

@pytest.mark.parametrize("family,expected_range", [
    ("upper", (100000, 300000)),
    ("middle", (50000, 150000)),
    ("working", (10000, 60000)),
    ("rural", (0, 20000)),
    ("unknown", (20000, 80000)),  # 默认 fallback
])
def test_derive_initial_cash(family, expected_range):
    p = Person(family_background=family)
    cash = p.derive_initial_cash()
    lo, hi = expected_range
    assert lo <= cash <= hi, f"family={family} -> {cash}, expected in [{lo}, {hi}]"


# === LifeMetrics.as_dict ===

def test_life_metrics_as_dict_has_14_fields():
    m = LifeMetrics()
    d = m.as_dict()
    # 中文键名 (这是 reporting/ chart_builder.py 期望的)
    assert "净资产(万)" in d
    assert "身体健康" in d
    assert "心理健康" in d
    assert len(d) == 14, f"expected 14 metrics, got {len(d)}: {list(d.keys())}"


def test_life_metrics_default_values():
    m = LifeMetrics()
    assert m.net_worth == 0.0
    assert m.physical_health == 80.0
    assert m.mental_health == 80.0
    assert m.career_level == 20.0


# === LifeState.determine_stage ===

@pytest.mark.parametrize("age,expected_stage", [
    (18.0, "freshman"),
    (19.5, "sophomore"),
    (20.5, "junior"),
    (21.5, "senior"),
    (22.5, "grad_school_or_first_job"),
    (24.0, "early_career"),
    (27.0, "career_growth"),
    (29.0, "career_settling"),
])
def test_determine_stage_age_buckets(age, expected_stage):
    s = LifeState(current_age=age)
    assert s.determine_stage() == expected_stage


# === init_state_from_config ===

def test_init_state_from_config_minimal():
    """最小 config 应该能跑通, 不会崩"""
    cfg = {
        "simulation": {
            "start_age": 18,
            "end_age": 30,
            "initial_person": {
                "gaokao_score": 600,
                "family_background": "middle",
                "gender": "male",
                "city_tier": "tier2",
            },
        },
        "agents": {
            "rational": {"enabled": True},
        },
    }
    state = init_state_from_config(cfg, seed=42)
    assert state.seed == 42
    assert state.current_age == 18.0
    assert state.life_stage == "freshman"  # 18 岁是大一
    assert isinstance(state.person, Person)
    assert isinstance(state.metrics, LifeMetrics)
    assert state.metrics.physical_health == 80.0


def test_init_state_from_config_with_initial_person():
    """带 initial_person 的 config 应该正确填充"""
    cfg = {
        "simulation": {
            "start_age": 18,
            "end_age": 30,
            "initial_person": {
                "gaokao_score": 680,
                "family_background": "upper",
                "city_tier": "tier1",
                "gender": "male",
            },
        },
    }
    state = init_state_from_config(cfg, seed=7)
    assert state.person.gaokao_score == 680
    assert state.person.family_background == "upper"
    assert state.person.city_tier == "tier1"
    # derive_school 应该把 680 分映射到清北复交
    assert "清北" in state.person.derive_school()
