"""季度驱动器

每个季度：
1. world.tick() 生成事件
2. 决定是否召开董事会
3. 执行决策 → 更新指标
4. 记录历史
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.state import LifeState, DecisionRecord
from core.world import World, WorldEvent
from meeting.council import Council


# 决策选项 → 指标影响（粗略规则）
# 关键词匹配：选了包含某关键词的选项，就触发对应 effect
OPTION_EFFECTS: list[dict[str, Any]] = [
    {
        "match": ["保研", "考研", "读研", "出国", "留学", "MBA"],
        "effects": {
            "career_level": +5,
            "career_income_yearly": -2,
            "free_hours_weekly": -10,
            "meaning_score": -3,
            "regret_index": -2,
        },
    },
    {
        "match": ["工作", "入行", "入职", "接 offer", "接工作"],
        "effects": {
            "career_level": +3,
            "career_income_yearly": +5,
            "free_hours_weekly": -15,
            "physical_health": -3,
            "mental_health": -2,
        },
    },
    {
        "match": ["创业", "all in", "全职", "加入创业"],
        "effects": {
            "career_level": +2,
            "career_income_yearly": -3,
            "net_worth": -5,
            "free_hours_weekly": -20,
            "physical_health": -5,
            "mental_health": -8,
            "meaning_score": +10,
            "regret_index": -5,
        },
    },
    {
        "match": ["结婚", "领证", "要孩子", "生孩子"],
        "effects": {
            "relationship_density": +20,
            "net_worth": -10,
            "free_hours_weekly": -15,
            "meaning_score": +5,
        },
    },
    {
        "match": ["买", "上车", "买房"],
        "effects": {
            "net_worth": -30,
            "cash_flow_monthly": -1.0,
            "meaning_score": +8,
            "regret_index": -3,
        },
    },
    {
        "match": ["社团", "社交", "恋爱", "在一起", "结婚"],
        "effects": {
            "relationship_density": +10,
            "free_hours_weekly": -5,
        },
    },
    {
        "match": ["休息", "休整", "请假", "gap", "休学"],
        "effects": {
            "physical_health": +10,
            "mental_health": +8,
            "free_hours_weekly": +10,
            "net_worth": -2,
        },
    },
    {
        "match": ["辞职", "裸辞", "走人"],
        "effects": {
            "cash_flow_monthly": -1.5,
            "net_worth": -3,
            "mental_health": +5,
            "regret_index": +2,
        },
    },
    {
        "match": ["考公", "考编", "公务员", "体制"],
        "effects": {
            "career_level": +1,
            "career_income_yearly": -3,
            "free_hours_weekly": +15,
            "mental_health": +5,
        },
    },
    {
        "match": ["锻炼", "调养", "看医生", "运动", "睡觉"],
        "effects": {
            "physical_health": +5,
            "mental_health": +3,
        },
    },
    {
        "match": ["参加", "加入", "报名", "去"],
        "effects": {
            "career_level": +2,
            "relationship_density": +3,
        },
    },
    {
        "match": ["不参加", "不加入", "放弃", "不", "拒绝", "婉拒"],
        "effects": {
            "regret_index": +1,
        },
    },
    {
        "match": ["再等等", "再等", "等", "拖"],
        "effects": {
            "regret_index": +2,
        },
    },
]


# 事件类型默认影响
TYPE_EFFECTS = {
    "milestone": {"regret_index": -1},
    "opportunity": {"regret_index": -2},
    "crisis": {"physical_health": -2, "mental_health": -2},
    "crossroads": {},
}


# 经济周期对每个季度的基础影响
ECONOMY_EFFECTS = {
    "boom": {"career_income_yearly": +1.5, "net_worth": +2},
    "normal": {"career_income_yearly": +0.5, "net_worth": +0.5},
    "recession": {"career_income_yearly": -0.3, "net_worth": -0.5},
    "crisis": {"career_income_yearly": -1.0, "net_worth": -1.5, "mental_health": -3},
}


def apply_effects(metrics_obj, effects: dict[str, float]) -> None:
    """应用效果到 LifeMetrics"""
    for k, v in effects.items():
        if k == "net_worth":
            metrics_obj.net_worth += v
        elif k == "cash_flow_monthly":
            metrics_obj.cash_flow_monthly += v
        elif k == "physical_health":
            metrics_obj.physical_health = max(0, min(100, metrics_obj.physical_health + v))
        elif k == "mental_health":
            metrics_obj.mental_health = max(0, min(100, metrics_obj.mental_health + v))
        elif k == "relationship_density":
            metrics_obj.relationship_density = max(0, min(100, metrics_obj.relationship_density + v))
        elif k == "career_level":
            metrics_obj.career_level = max(0, min(100, metrics_obj.career_level + v))
        elif k == "career_income_yearly":
            metrics_obj.career_income_yearly = max(0, metrics_obj.career_income_yearly + v)
        elif k == "free_hours_weekly":
            metrics_obj.free_hours_weekly = max(0, min(168, metrics_obj.free_hours_weekly + v))
        elif k == "meaning_score":
            metrics_obj.meaning_score = max(0, min(100, metrics_obj.meaning_score + v))
        elif k == "regret_index":
            metrics_obj.regret_index = max(0, metrics_obj.regret_index + v)


def compute_decision_effects(decision: DecisionRecord) -> dict[str, float]:
    """根据决策选项计算 effect"""
    effects: dict[str, float] = {}
    # 类型默认
    type_def = TYPE_EFFECTS.get(decision.event_type, {})
    for k, v in type_def.items():
        effects[k] = effects.get(k, 0) + v
    # 选项匹配
    chosen = decision.chosen
    for rule in OPTION_EFFECTS:
        for kw in rule["match"]:
            if kw in chosen:
                for k, v in rule["effects"].items():
                    effects[k] = effects.get(k, 0) + v
                break
    return effects


class Driver:
    """季度驱动器"""
    
    def __init__(self, state: LifeState, world: World, council: Council):
        self.state = state
        self.world = world
        self.council = council
        self.start_quarter = 0
        self.end_quarter = 48
    
    async def run(self, quiet: bool = False) -> list[DecisionRecord]:
        """跑完所有季度"""
        for q in range(self.start_quarter, self.end_quarter):
            await self.tick_quarter(q, quiet=quiet)
        return self.state.decisions
    
    async def tick_quarter(self, q: int, quiet: bool = False) -> None:
        """一个季度"""
        self.state.current_quarter = q
        self.state.current_age = 18 + q * 0.25
        self.state.life_stage = self.state.determine_stage()
        
        # 1. 世界 tick
        events = self.world.tick()
        
        # 2. 开会（如果有事）
        decision = None
        if events:
            # 取第一个事件开会
            event = events[0]
            try:
                decision = await self.council.hold(event)
                self.state.decisions.append(decision)
                # 各 agent 记住这次决策
                for a in self.council.agents:
                    a.remember_decision({
                        "quarter": decision.quarter,
                        "title": decision.event_title,
                        "chosen": decision.chosen,
                        "outcome": decision.outcome,
                    })
            except Exception as e:
                if not quiet:
                    import traceback, sys
                    msg = f"\n⚠️  Q{q} council error: {type(e).__name__}: {e}\n"
                    msg += "".join(traceback.format_exception(type(e), e, e.__traceback__))
                    sys.stdout.write(msg)
                    sys.stdout.flush()
        
        # 3. 应用 effects
        if decision:
            effects = compute_decision_effects(decision)
            apply_effects(self.state.metrics, effects)
            decision.outcome = _summarize_outcome(self.state, effects)
        
        # 4. 经济周期基础影响
        econ = ECONOMY_EFFECTS.get(self.world.economy_phase, ECONOMY_EFFECTS["normal"])
        apply_effects(self.state.metrics, econ)
        
        # 5. 自然漂移
        _natural_drift(self.state.metrics, self.state.life_stage)
        
        # 6. 记录
        self.state.record_metrics()
        
        # 7. 打印进度
        if not quiet:
            _print_quarter_log(self.state, decision, self.world.economy_phase)


def asyncio_run(coro):
    """同步运行 async（仅在非 async 上下文里使用）"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("Already in a running event loop; use 'await' instead.")
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _summarize_outcome(state: LifeState, effects: dict[str, float]) -> str:
    parts = []
    if effects.get("net_worth", 0) > 0:
        parts.append(f"净资产+{effects['net_worth']:.0f}万")
    if effects.get("net_worth", 0) < 0:
        parts.append(f"净资产{effects['net_worth']:.0f}万")
    if effects.get("career_level", 0) > 0:
        parts.append("事业↑")
    if effects.get("relationship_density", 0) > 0:
        parts.append("关系↑")
    if effects.get("physical_health", 0) < 0:
        parts.append("身体↓")
    if effects.get("mental_health", 0) < 0:
        parts.append("心理↓")
    if not parts:
        parts.append("平稳")
    return " / ".join(parts)


def _natural_drift(metrics, stage: str) -> None:
    """每季度的自然漂移"""
    # 健康会缓慢下降（如果长期高压）
    if metrics.career_level > 60 and metrics.free_hours_weekly < 25:
        metrics.physical_health = max(0, metrics.physical_health - 0.5)
        metrics.mental_health = max(0, metrics.mental_health - 0.3)
    # 自由时间随事业上升而下降（基础规律）
    if metrics.career_level > 50 and metrics.free_hours_weekly > 40:
        metrics.free_hours_weekly = max(20, metrics.free_hours_weekly - 0.3)


def _print_quarter_log(state: LifeState, decision: DecisionRecord | None, econ_phase: str):
    age = state.current_age
    stage = state.life_stage
    m = state.metrics
    if decision:
        title = decision.event_title[:30]
        chosen = decision.chosen[:15]
        print(f"  Q{state.current_quarter:>2} | {age:5.1f}岁 {stage:20s} | 📌 {title} → {chosen}  [经济:{econ_phase}]")
    else:
        print(f"  Q{state.current_quarter:>2} | {age:5.1f}岁 {stage:20s} | （平淡期）  [经济:{econ_phase}]")
