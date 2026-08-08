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
        
        # 1. 检查预设 milestone
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
        
        # 3. 经济周期更新
        self.economy_counter += 1
        if self.economy_counter > 12:
            self.economy_counter = 0
            phases = ["boom", "normal", "recession", "crisis"]
            self.economy_phase = self.rng.choice(phases)
        
        return events
    
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
