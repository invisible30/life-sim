"""Agent 抽象基类

每个 agent 是"我"的一个侧面。负责：
- 基于人设表达立场
- 回应其他 agent
- 投票决策
- 记忆累积
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from core.state import LifeState
from llm.client import LLMClient


# 投票强度 -> 数值
VOTE_WEIGHTS = {
    "强烈支持": 2,
    "支持": 1,
    "中立": 0,
    "反对": -1,
    "强烈反对": -2,
}


@dataclass
class AgentView:
    """agent 的单次表达"""
    agent: str
    role: str  # 中文身份
    emoji: str
    content: str
    round: int = 1  # 1 = 立场表达, 2+ = 互相回应


@dataclass
class AgentVote:
    agent: str
    role: str
    emoji: str = ""
    option: str = ""
    weight: int = 0  # -2 / -1 / 0 / 1 / 2
    reasoning: str = ""


class Agent(ABC):
    """agent 基类"""
    
    name: str = "base"
    voice: str = "我"  # 中文身份
    emoji: str = "🙂"
    persona_intro: str = ""  # 短描述
    base_weight: float = 1.0  # 基础投票权重
    
    def __init__(self, llm: LLMClient, state: LifeState):
        self.llm = llm
        self.state = state
        self.memory: list[dict[str, Any]] = []  # 最近决策的摘要
        self.drift = 0.0  # 权重漂移（基于过去判断的对错）
    
    @property
    def current_weight(self) -> float:
        return max(0.2, min(2.0, self.base_weight + self.drift))
    
    @abstractmethod
    def persona_prompt(self) -> str:
        """返回该 agent 的人设 prompt 段"""
        ...
    
    def system_prompt(self) -> str:
        """完整 system prompt"""
        state_summary = self._state_summary()
        memory_summary = self._memory_summary()
        return f"""你是「{self.voice}」——「我」这个人的一个侧面。
{self.persona_intro}

# 你的视角
{self.persona_prompt()}

# 当前人生状态
{state_summary}

# 最近的决策历史
{memory_summary if memory_summary else "（尚无历史决策）"}

# 你的任务
在人生董事会上，你需要：
1. 表达你对当前议题的立场（基于你的视角，不需要面面俱到）
2. 投票时直接给出选项和强度（强烈支持/支持/中立/反对/强烈反对）
3. 用第一人称，像在跟其他"我"对话
4. 简短（80-200 字），不要写成议论文
5. 必要时引用过去的决策或后悔的事
"""
    
    def _state_summary(self) -> str:
        s = self.state
        m = s.metrics
        return f"""- 年龄：{s.current_age:.1f} 岁
- 阶段：{s.life_stage}
- 学校/工作：{s.person.university} / {s.person.major or '未定'}
- 净资产：{m.net_worth:.1f} 万，月现金流：{m.cash_flow_monthly:.2f} 万
- 健康：身体 {m.physical_health:.0f} / 心理 {m.mental_health:.0f}
- 事业等级：{m.career_level:.0f}，年收入：{m.career_income_yearly:.1f} 万
- 关系网：{m.relationship_density:.0f}，意义感：{m.meaning_score:.0f}
- 自由时间：{m.free_hours_weekly:.0f} 小时/周
- 状态标签：{', '.join(sorted(s.flags)) if s.flags else '无'}
- 决策数：{len(s.decisions)} 次
"""
    
    def _memory_summary(self) -> str:
        if not self.memory:
            return ""
        lines = []
        for m in self.memory[-3:]:
            lines.append(
                f"  - Q{m['quarter']} {m['title'][:30]} → 选了「{m['chosen']}」，结果：{m.get('outcome', '?')[:50]}"
            )
        return "\n".join(lines)
    
    def remember_decision(self, decision: dict[str, Any]) -> None:
        """记住一次决策（精简摘要）"""
        self.memory.append({
            "quarter": decision.get("quarter"),
            "title": decision.get("event_title", ""),
            "chosen": decision.get("chosen", ""),
            "outcome": decision.get("outcome", ""),
        })
        if len(self.memory) > 10:
            self.memory = self.memory[-10:]
    
    # ---- 主要方法 ----
    
    async def express(self, agenda: dict[str, Any]) -> AgentView:
        """立场表达（第 1 轮）"""
        user_prompt = self._build_user_prompt(agenda, mode="express")
        content = await self.llm.chat(self.system_prompt(), user_prompt)
        return AgentView(
            agent=self.name,
            role=self.voice,
            emoji=self.emoji,
            content=content,
            round=1,
        )
    
    async def respond(
        self,
        agenda: dict[str, Any],
        other_views: list[AgentView],
        round_num: int,
    ) -> AgentView:
        """互相回应（第 2+ 轮）"""
        user_prompt = self._build_user_prompt(agenda, mode="respond", other_views=other_views, round_num=round_num)
        content = await self.llm.chat(self.system_prompt(), user_prompt)
        return AgentView(
            agent=self.name,
            role=self.voice,
            emoji=self.emoji,
            content=content,
            round=round_num,
        )
    
    async def vote(self, agenda: dict[str, Any]) -> AgentVote:
        """投票"""
        user_prompt = self._build_user_prompt(agenda, mode="vote")
        content = await self.llm.chat(self.system_prompt(), user_prompt, temperature=0.5)
        option, weight, reasoning = self._parse_vote(content, agenda.get("options", []))
        return AgentVote(
            agent=self.name,
            role=self.voice,
            emoji=self.emoji,
            option=option,
            weight=weight,
            reasoning=reasoning,
        )
    
    def _build_user_prompt(
        self,
        agenda: dict[str, Any],
        mode: str,
        other_views: list[AgentView] | None = None,
        round_num: int = 1,
    ) -> str:
        options = agenda.get("options", [])
        opts_text = "\n".join([f"  {chr(65+i)}. {opt}" for i, opt in enumerate(options)])
        
        if mode == "express":
            return f"""# 当前议题
{agenda.get('title', '?')}

类型：{agenda.get('type', '?')}
{agenda.get('description', '')}

# 选项
{opts_text}

请你表达对这个议题的立场。从你的视角看，应该选哪个？为什么？
不需要说别的 agent 会怎么想，只说你自己。"""
        
        elif mode == "respond":
            other_text = "\n\n".join([
                f"## {v.emoji} {v.role}\n{v.content}"
                for v in (other_views or [])
            ])
            return f"""# 议题
{agenda.get('title', '?')}
{agenda.get('description', '')}

# 其他"我"刚说了
{other_text}

# 选项
{opts_text}

这是第 {round_num} 轮回应。你可以选择：
- 反驳某个 agent 的观点
- 支持某个 agent
- 补充新角度
- 修正自己之前的立场

保持你的人设，80-150 字。"""
        
        elif mode == "vote":
            return f"""# 议题
{agenda.get('title', '?')}
{agenda.get('description', '')}

# 选项
{opts_text}

请投票：
- 选择一个选项（A/B/C...）
- 给出强度：强烈支持 / 支持 / 中立 / 反对 / 强烈反对
- 用 1-2 句话解释

格式：
选项: <字母>
强度: <强度>
理由: <一句话>"""
        return ""
    
    def _parse_vote(self, content: str, options: list[str]) -> tuple[str, int, str]:
        """解析投票输出"""
        # 默认
        option = options[0] if options else ""
        weight = 0
        reasoning = content[:200]
        
        # 找选项
        opt_match = re.search(r"选项[：:]\s*([A-Z])", content)
        if opt_match:
            idx = ord(opt_match.group(1).upper()) - ord('A')
            if 0 <= idx < len(options):
                option = options[idx]
        else:
            # 尝试匹配 A/B/C 直接出现
            for i, opt in enumerate(options):
                letter = chr(ord('A') + i)
                if letter in content[:100] or opt[:6] in content:
                    option = opt
                    break
        
        # 找强度
        for label, w in VOTE_WEIGHTS.items():
            if label in content:
                weight = w
                break
        
        # 找理由
        reason_match = re.search(r"理由[：:]\s*(.+)", content, re.DOTALL)
        if reason_match:
            reasoning = reason_match.group(1).strip()[:200]
        
        return option, weight, reasoning
