"""世界引擎

负责：
- 维护经济周期
- 按年龄/阶段触发预设事件
- 随机抛出现意外事件
- 提供给董事会的事件对象
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.state import LifeState


DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass
class WorldEvent:
    id: str
    type: str            # milestone / opportunity / crisis / crossroads
    title: str
    description: str
    options: list[str]
    trigger_age: float | None = None
    stage: str | None = None
    
    def to_agenda(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "options": self.options,
        }


class World:
    """世界状态 + 事件生成器"""
    
    def __init__(self, state: LifeState):
        self.state = state
        self.rng = random.Random(state.seed)
        self._load_events()
        self.economy_phase = "normal"  # boom / normal / recession / crisis
        self.economy_counter = self.rng.randint(0, 8)
    
    def _load_events(self) -> None:
        with open(DATA_DIR / "events.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        self.milestones = data["milestones"]
        self.random_events = data["random_events"]
        self._fired: set[str] = set()
    
    def tick(self) -> list[WorldEvent]:
        """每个季度调用，返回本季度发生的事件"""
        events: list[WorldEvent] = []
        age = self.state.current_age
        
        # 1. 检查预设 milestone（带连锁优先级）
        # 如果有 flag 匹配某个 milestone，优先触发它
        for ms in self.milestones:
            if ms["id"] in self._fired:
                continue
            ta = ms.get("trigger_age", 999)
            if abs(age - ta) < 0.15:  # ±0.15 年容差
                events.append(self._mk_event(ms))
                self._fired.add(ms["id"])
                break  # 每季度最多 1 个 milestone
        
        # 2. 随机事件
        for re in self.random_events:
            if self.rng.random() < re.get("weight", 0.05):
                events.append(self._mk_event(re))
                # 一次只发 1 个随机事件
                break
        
        # 3. 连锁事件（基于之前决策生成的 flag）
        chain = self._chain_event()
        if chain and not events:
            events.append(chain)
        
        # 4. 经济周期更新
        self.economy_counter += 1
        if self.economy_counter > 12:
            self.economy_counter = 0
            phases = ["boom", "normal", "recession", "crisis"]
            self.economy_phase = self.rng.choice(phases)
        
        return events
    
    def _chain_event(self) -> WorldEvent | None:
        """基于 state.flags 派生连锁事件
        
        每次决策后，driver 会分析 chosen 选项，写入 state.flags。
        world 在下一个 tick 看到相关 flag 时，会追加一个紧跟的事件。
        """
        flags = self.state.flags
        age = self.state.current_age
        
        # 毕业 → 找工作焦虑（如果还没 flag in_job）
        if "in_job" not in flags and age > 22 and "grad_recently" in flags:
            return self._mk_chain_event(
                "chain_first_job_anxiety",
                "milestone",
                "刚毕业，还没稳定工作",
                "毕业一个月了，简历投了几十份。offer 没几个，焦虑开始上来了。",
                ["继续海投", "降低期望", "找实习过渡", "找朋友内推", "GAP 一个月", "做自由职业"],
            )
        
        # 结婚 → 蜜月期问题
        if "married_recently" in flags and age > 26:
            return self._mk_chain_event(
                "chain_marriage_reality",
                "crisis",
                "新婚磨合期",
                "结婚 3-6 个月。开始暴露生活习惯差异：家务分配、消费观、跟朋友聚会频率。",
                ["坐下来开家庭会议", "互相妥协", "先忍着", "看婚姻咨询师", "冷战几天", "自己找事做"],
            )
        
        # 孩子 → 育儿压力
        if "has_child" in flags and age > 28:
            return self._mk_chain_event(
                "chain_parenthood",
                "crisis",
                "新手父母",
                "宝宝 3 个月大。每晚醒 4 次，白天上班，晚上带娃。",
                ["请父母来帮忙", "请月嫂/育儿嫂", "换到轻松岗位", "咬牙撑过去", "跟公司谈弹性", "伴侣轮流请假"],
            )
        
        # 创业 → 现金流紧张
        if "in_startup" in flags and age > 27:
            return self._mk_chain_event(
                "chain_cash_crunch",
                "crisis",
                "创业 6 个月，钱快烧完了",
                "账上资金只够撑 3 个月。下一轮融资还没着落。",
                ["拼命找下一轮", "自己掏钱补", "砍人砍成本", "开始盈利", "卖给大公司", "认清现实关掉"],
            )
        
        # 读研 → 论文压力
        if "in_grad_school" in flags and age > 23:
            return self._mk_chain_event(
                "chain_thesis_pressure",
                "milestone",
                "研究生：开题/中期/答辩",
                "导师催你推进。论文/项目/实习三选一。",
                ["all in 论文", "找实习刷简历", "跟导师磨时间", "考虑转硕", "做横向赚钱", "退学工作"],
            )
        
        return None
    
    def _mk_chain_event(self, event_id: str, etype: str, title: str, desc: str, options: list[str]) -> WorldEvent:
        return WorldEvent(
            id=event_id,
            type=etype,
            title=title,
            description=desc,
            options=options,
        )
    
    def _mk_event(self, raw: dict[str, Any]) -> WorldEvent:
        desc = raw.get("description", "")
        # 模板替换
        desc = desc.format(
            university=self.state.person.university or "你的大学",
            major=self.state.person.major or "你的专业",
        )
        return WorldEvent(
            id=raw["id"],
            type=raw.get("type", "crossroads"),
            title=raw.get("title", "?"),
            description=desc,
            options=raw.get("options", ["继续", "放弃"]),
            trigger_age=raw.get("trigger_age"),
            stage=raw.get("stage"),
        )
    
    def get_industry_outlook(self, industry: str) -> dict[str, float]:
        """返回行业景气度"""
        base = {
            "boom": {"salary_mult": 1.4, "hiring": 1.5, "promotion_speed": 1.3},
            "normal": {"salary_mult": 1.0, "hiring": 1.0, "promotion_speed": 1.0},
            "recession": {"salary_mult": 0.85, "hiring": 0.6, "promotion_speed": 0.7},
            "crisis": {"salary_mult": 0.7, "hiring": 0.3, "promotion_speed": 0.5},
        }[self.economy_phase]
        return base
