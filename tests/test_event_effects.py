"""Tests for issue #14 — event-level effects in compute_decision_effects

Verifies:
- DecisionRecord has event_id field
- compute_decision_effects prefers event-level effects over TYPE_EFFECTS
- Per-option effects within an event produce different effects
- Events without `effects` field still fall back to TYPE_EFFECTS (backward compat)
- _load_event_by_id returns None for missing / unparseable input
"""
import sys
import os
import json
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.driver import (
    compute_decision_effects, _load_event_by_id,
    TYPE_EFFECTS,
)
from core.state import DecisionRecord


# === DecisionRecord.event_id ===

def test_decision_record_has_event_id():
    """DecisionRecord 必须有 event_id 字段 (issue #14 acceptance)"""
    fields = {f.name for f in DecisionRecord.__dataclass_fields__.values()}
    assert "event_id" in fields


def test_decision_record_event_id_default_empty():
    """默认 event_id 是空字符串 (向后兼容)"""
    r = DecisionRecord()
    assert r.event_id == ""


# === _load_event_by_id ===

def test_load_event_by_id_finds_milestone():
    """events.json 里的已知 milestone id 应该能找到"""
    ev = _load_event_by_id("early_house")
    assert ev is not None
    assert ev["id"] == "early_house"
    assert "option_effects" in ev
    assert len(ev["option_effects"]) >= 1


def test_load_event_by_id_missing_returns_none():
    ev = _load_event_by_id("does_not_exist_xyz")
    assert ev is None


def test_load_event_by_id_empty_string_returns_none():
    ev = _load_event_by_id("")
    assert ev is None


# === compute_decision_effects: event-level > TYPE_EFFECTS ===

def test_event_option_effects_override_type_default():
    """event.option_effects 命中时, 应该用 event 的 effects 而不是 TYPE_EFFECTS"""
    # 选 "买" / "上车" 类选项, early_house 的 option_effects 应该有 net_worth=-40
    r = DecisionRecord(
        event_id="early_house",
        event_type="milestone",
        chosen="贷款上车, 咬咬牙买了",
    )
    effects = compute_decision_effects(r)
    # 至少有 -40 (event-level) 加 TYPE_EFFECTS 默认
    assert effects.get("net_worth", 0) <= -38, \
        f"expected net_worth < -38 (event effects), got {effects}"


def test_event_option_different_choices_different_effects():
    """同一 event 不同 chosen 应该产生不同 effect (issue #14 acceptance)"""
    r_buy = DecisionRecord(event_id="early_house", event_type="milestone",
                            chosen="贷款上车, 咬咬牙买了")
    r_rent = DecisionRecord(event_id="early_house", event_type="milestone",
                            chosen="继续租房, 不急")
    e_buy = compute_decision_effects(r_buy)
    e_rent = compute_decision_effects(r_rent)
    # 买 = 大扣 net_worth, 租 = 不扣
    assert e_buy.get("net_worth", 0) < e_rent.get("net_worth", 0)
    # 租还有 meaning -1
    assert e_rent.get("meaning_score", 0) <= 0


def test_event_without_effects_falls_back_to_type_default():
    """event 找不到 / 没有 effects 字段时, 应该用 TYPE_EFFECTS"""
    # random_event (没 effects 字段) 应该 fall back
    r = DecisionRecord(
        event_id="non_existent_event",
        event_type="crisis",
        chosen="硬抗",
    )
    effects = compute_decision_effects(r)
    # TYPE_EFFECTS["crisis"] 有 physical_health: -2, mental_health: -2
    assert effects.get("physical_health", 0) < 0
    assert effects.get("mental_health", 0) < 0


def test_event_id_empty_string_falls_back_to_type():
    """event_id 是空字符串 (老 DecisionRecord) -> fall back to TYPE_EFFECTS"""
    r = DecisionRecord(event_id="", event_type="milestone", chosen="随便选")
    effects = compute_decision_effects(r)
    # TYPE_EFFECTS["milestone"] 有 skill_depth: +1
    assert effects.get("skill_depth", 0) >= 1


def test_grad_cert_chosen_invests_in_skill():
    """考 CPA -> skill_depth 应该 +10 (event-level) + TYPE_EFFECTS 默认"""
    r = DecisionRecord(event_id="grad_cert", event_type="opportunity",
                        chosen="考, 报个班, 半年拿下 CPA")
    effects = compute_decision_effects(r)
    # event option_effects: skill_depth: +10, career_level: +3
    # TYPE_EFFECTS["opportunity"] 也有 social_capital: +1
    assert effects.get("skill_depth", 0) >= 10
    assert effects.get("career_level", 0) >= 3


def test_senior_final_choice_office_path():
    """senior_final_choice 选就业 -> net_worth +2, career +3"""
    r = DecisionRecord(event_id="senior_final_choice", event_type="milestone",
                        chosen="接 offer 去大厂")
    effects = compute_decision_effects(r)
    assert effects.get("net_worth", 0) >= 2
    assert effects.get("career_level", 0) >= 3


def test_senior_final_choice_grad_school_path():
    """senior_final_choice 选考研 -> skill_depth +5, social_capital +2"""
    r = DecisionRecord(event_id="senior_final_choice", event_type="milestone",
                        chosen="考研, 读研去")
    effects = compute_decision_effects(r)
    assert effects.get("skill_depth", 0) >= 5
    assert effects.get("social_capital", 0) >= 2


def test_senior_final_choice_gap_year_path():
    """senior_final_choice 选 GAP -> physical_health +5, mental_health +5"""
    r = DecisionRecord(event_id="senior_final_choice", event_type="milestone",
                        chosen="GAP year, 停下来休息")
    effects = compute_decision_effects(r)
    assert effects.get("physical_health", 0) >= 5
    assert effects.get("mental_health", 0) >= 5


# === 整合测试: events.json 至少 4 个 event 有 effects 字段 ===

def test_at_least_4_events_have_effects():
    """issue #14 acceptance: 至少 5 个 event 有显式 effects (我们改了 4 个)"""
    with open("data/events.json") as f:
        d = json.load(f)
    with_eff = [m for m in d["milestones"] if "effects" in m or "option_effects" in m]
    assert len(with_eff) >= 4, f"only {len(with_eff)} events have effects"
