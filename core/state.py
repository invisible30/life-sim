"""核心状态层

定义：
- Person: 18 岁的初始人设
- LifeMetrics: 7 项人生指标
- LifeState: 全局状态容器
- DecisionRecord / MeetingRecord: 决策留痕
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.balance_sheet import BalanceSheet


@dataclass
class Personality:
    """大五人格 - 影响 agent 初始权重和决策风格"""
    openness: float = 0.5            # 开放性 -> 倾向感性/野心
    conscientiousness: float = 0.5   # 尽责性 -> 倾向理性/现实
    extraversion: float = 0.5        # 外向性 -> 影响关系网
    agreeableness: float = 0.5       # 宜人性 -> 倾向家人/感性
    neuroticism: float = 0.5         # 神经质 -> 心理弹性


@dataclass
class Person:
    """18 岁的我"""
    name: str = "我"
    gender: str = "male"
    age: float = 18.0
    city_tier: str = "tier2"               # tier1 / new_tier1 / tier2 / small
    family_background: str = "middle"       # upper / middle / working / rural
    gaokao_score: int = 600                 # 高考分
    university: str = ""                    # 派生
    major: str = ""                         # 派生
    personality: Personality = field(default_factory=Personality)
    
    # 初始资源
    initial_cash: float = 0.0               # 家庭给的启动资金
    family_income_yearly: float = 150000.0  # 家庭年收入（影响期待值）
    parents_expectation: str = "stable"     # stable / achieve / happy / free
    
    def derive_school(self) -> str:
        """根据高考分映射学校档次"""
        if self.gaokao_score >= 680:
            return "清北复交"
        elif self.gaokao_score >= 640:
            return "985 头部"
        elif self.gaokao_score >= 600:
            return "985 中等 / 211 头部"
        elif self.gaokao_score >= 560:
            return "211 / 双一流"
        elif self.gaokao_score >= 500:
            return "普通一本"
        elif self.gaokao_score >= 430:
            return "二本"
        else:
            return "专科 / 民办"
    
    def derive_initial_cash(self) -> float:
        """根据家庭背景映射初始资金"""
        return {
            "upper": 200000.0,
            "middle": 80000.0,
            "working": 30000.0,
            "rural": 8000.0,
        }.get(self.family_background, 50000.0)


@dataclass
class LifeMetrics:
    """14 项人生指标：财务 2 + 健康 2 + 关系 2 + 事业 2 + 自由 1 + 心理 2 + 技能/资本 3"""
    # 财务
    net_worth: float = 0.0            # 净资产（万元）
    cash_flow_monthly: float = 0.0    # 月现金流（万元）
    # 健康
    physical_health: float = 80.0     # 身体健康
    mental_health: float = 80.0        # 心理健康
    # 关系
    relationship_density: float = 50.0 # 关系网密度
    romantic_health: float = 50.0     # 感情满意度（0-100）
    # 事业
    career_level: float = 20.0        # 事业等级
    career_income_yearly: float = 0.0 # 年收入（万元）
    # 自由 / 心理
    free_hours_weekly: float = 60.0   # 周自由时间
    meaning_score: float = 60.0        # 心流 / 意义感
    regret_index: float = 0.0          # 后悔指数（累积）
    # 技能 / 资本
    skill_depth: float = 30.0          # 专业技能深度（0-100）
    social_capital: float = 20.0       # 社会资本（行业地位 + 人脉）
    physical_energy: float = 75.0      # 体能/精力（区别于 health）
    
    def as_dict(self) -> dict[str, float]:
        return {
            "净资产(万)": round(self.net_worth, 1),
            "月现金流(万)": round(self.cash_flow_monthly, 2),
            "身体健康": round(self.physical_health, 1),
            "心理健康": round(self.mental_health, 1),
            "关系网密度": round(self.relationship_density, 1),
            "感情满意度": round(self.romantic_health, 1),
            "事业等级": round(self.career_level, 1),
            "年收入(万)": round(self.career_income_yearly, 1),
            "周自由小时": round(self.free_hours_weekly, 1),
            "意义感": round(self.meaning_score, 1),
            "后悔指数": round(self.regret_index, 1),
            "技能深度": round(self.skill_depth, 1),
            "社会资本": round(self.social_capital, 1),
            "体能精力": round(self.physical_energy, 1),
        }
    
    def all_fields(self) -> list[str]:
        return list(self.as_dict().keys())


@dataclass
class DecisionRecord:
    """单次决策记录"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    quarter: int = 0                # 第几个季度
    age: float = 0.0
    event_id: str = ""              # issue #14: 加这个字段, 便于 effect 计算回查 events.json
    event_title: str = ""
    event_description: str = ""
    event_type: str = ""            # milestone / opportunity / crisis / crossroads
    options: list[str] = field(default_factory=list)
    chosen: str = ""
    votes: dict[str, int] = field(default_factory=dict)   # agent_name -> vote
    debates: list[dict[str, Any]] = field(default_factory=list)  # [{agent, content, round}]
    reasoning: str = ""
    outcome: str = ""               # 决策执行后的结果
    timestamp: float = field(default_factory=time.time)


@dataclass
class LifeState:
    """全局状态"""
    seed: int = 42
    person: Person = field(default_factory=Person)
    metrics: LifeMetrics = field(default_factory=LifeMetrics)
    current_quarter: int = 0  # 0-indexed
    current_age: float = 18.0

    # 资产负债表（issue #31）— 净资产的真实算式
    balance_sheet: BalanceSheet = field(default_factory=BalanceSheet)

    # 历史
    metrics_history: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)

    # 阶段
    life_stage: str = "freshman"  # freshman / sophomore / junior / senior / grad / early_career

    # 当前状态标签（用于事件触发）
    flags: set[str] = field(default_factory=set)
    # 例如: {"has_partner", "in_school", "has_job", "burned_out", ...}
    
    def snapshot(self) -> dict[str, Any]:
        return {
            "quarter": self.current_quarter,
            "age": self.current_age,
            "stage": self.life_stage,
            "metrics": self.metrics.as_dict(),
            "flags": sorted(self.flags),
        }
    
    def record_metrics(self) -> None:
        """季度末记录一次"""
        snap = self.snapshot()
        self.metrics_history.append(snap)
    
    def determine_stage(self) -> str:
        """根据年龄决定阶段"""
        a = self.current_age
        if a < 19:
            return "freshman"
        elif a < 20:
            return "sophomore"
        elif a < 21:
            return "junior"
        elif a < 22:
            return "senior"
        elif a < 23:
            return "grad_school_or_first_job"
        elif a < 26:
            return "early_career"
        elif a < 28:
            return "career_growth"
        else:
            return "career_settling"


def init_state_from_config(
    cfg: dict[str, Any],
    seed: int,
    randomize_person: bool = False,
) -> LifeState:
    """从配置初始化 LifeState

    randomize_person: 用 seed 派生 person 的人设（gaokao/family/city/personality），
                      让不同 seed 跑出不同的人生。
    """
    import random
    rng = random.Random(seed)

    pcfg = cfg["simulation"]["initial_person"]

    if randomize_person:
        # 用 seed 派生人设
        gaokao_score = rng.randint(480, 695)
        family = rng.choice(["upper", "middle", "working", "rural"])
        gender = pcfg.get("gender", rng.choice(["male", "female"]))
        city = rng.choice(["tier1", "new_tier1", "tier2", "small"])
        personality = Personality(
            openness=rng.uniform(0.2, 0.9),
            conscientiousness=rng.uniform(0.2, 0.9),
            extraversion=rng.uniform(0.2, 0.9),
            agreeableness=rng.uniform(0.2, 0.9),
            neuroticism=rng.uniform(0.2, 0.9),
        )
    else:
        gaokao_score = pcfg.get("gaokao_score", 600)
        family = pcfg.get("family_background", "middle")
        gender = pcfg.get("gender", "male")
        city = pcfg.get("city_tier", "tier2")
        pcfg_p = pcfg.get("personality_seed", {})
        personality = Personality(
            openness=pcfg_p.get("openness", 0.5),
            conscientiousness=pcfg_p.get("conscientiousness", 0.5),
            extraversion=pcfg_p.get("extraversion", 0.5),
            agreeableness=pcfg_p.get("agreeableness", 0.5),
            neuroticism=pcfg_p.get("neuroticism", 0.5),
        )

    person = Person(
        gaokao_score=gaokao_score,
        family_background=family,
        gender=gender,
        city_tier=city,
        personality=personality,
    )
    person.university = person.derive_school()
    person.initial_cash = person.derive_initial_cash()
    
    # 父母期待根据家庭背景默认
    person.parents_expectation = {
        "upper": "achieve",
        "middle": "stable",
        "working": "stable",
        "rural": "happy",
    }.get(person.family_background, "stable")
    
    metrics = LifeMetrics(
        net_worth=person.initial_cash / 10000.0,  # 净资产由 balance_sheet 驱动，初值会被覆盖
        relationship_density=50 + (person.personality.extraversion - 0.5) * 30,
        free_hours_weekly=70,
    )

    # issue #31: 资产负债表 — 现金 = 启动资金, 净资产 = 现金 + 房子 - 贷款
    balance_sheet = BalanceSheet(
        cash=person.initial_cash / 10000.0,
    )

    state = LifeState(
        seed=seed,
        person=person,
        metrics=metrics,
        balance_sheet=balance_sheet,
        current_quarter=0,
        current_age=float(cfg["simulation"]["start_age"]),
        life_stage="freshman",
    )
    # 同步净资产初值
    state.metrics.net_worth = state.balance_sheet.net_worth
    state.record_metrics()
    return state
