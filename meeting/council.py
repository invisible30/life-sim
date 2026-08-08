"""人生董事会

流程：
1. 议程陈述
2. 每个 agent 立场表达（可并发）
3. 互相回应（最多 N 轮）
4. 加权投票
5. 决策融合
6. 写入 DecisionRecord
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any

from agents import Agent, AgentView, AgentVote, make_all_agents
from core.state import LifeState, DecisionRecord
from core.world import WorldEvent
from llm.client import LLMClient


class Council:
    """人生董事会主持人"""
    
    def __init__(
        self,
        llm: LLMClient,
        state: LifeState,
        enabled_agents: dict | None = None,
        max_debate_rounds: int = 2,
        parallel: bool = True,
    ):
        self.llm = llm
        self.state = state
        self.enabled = enabled_agents or {}
        self.max_debate_rounds = max_debate_rounds
        self.parallel = parallel
        self.agents: list[Agent] = make_all_agents(llm, state, enabled_agents)
    
    def refresh_agents(self) -> None:
        """重建 agent（状态变化时调用）"""
        self.agents = make_all_agents(self.llm, self.state, self.enabled)
    
    async def hold(self, event: WorldEvent) -> DecisionRecord:
        """开一次会，处理一个事件"""
        agenda = event.to_agenda()
        t0 = time.time()
        
        # 1. 立场表达（所有 LLM agent 并发）
        if self.parallel:
            views = await self._express_parallel(agenda)
        else:
            views = await self._express_serial(agenda)
        
        # 2. 互相回应（最多 N 轮）
        all_views = list(views)
        for r in range(2, 2 + self.max_debate_rounds):
            if not all_views:
                break
            # 每人回应一次
            new_views = await self._respond_round(agenda, all_views, r)
            if not new_views:
                break
            all_views.extend(new_views)
        
        # 3. 投票（所有 agent 并发）
        votes = await self._vote_parallel(agenda)
        
        # 4. 决策融合
        chosen, scores, abstained = self._fuse(agenda["options"], votes)
        reasoning = self._build_reasoning(votes, scores, chosen)
        
        # 5. 写入记录
        record = DecisionRecord(
            quarter=self.state.current_quarter,
            age=self.state.current_age,
            event_title=event.title,
            event_description=event.description,
            event_type=event.type,
            options=agenda["options"],
            chosen=chosen,
            votes={v.agent: v.weight for v in votes},
            debates=[asdict(v) for v in all_views],
            reasoning=reasoning,
        )
        
        return record
    
    async def _express_parallel(self, agenda) -> list[AgentView]:
        # luck 不调 LLM
        llm_agents = [a for a in self.agents if a.name != "luck"]
        results = await asyncio.gather(
            *[a.express(agenda) for a in llm_agents],
            return_exceptions=True,
        )
        views = []
        for a, r in zip(llm_agents, results):
            if isinstance(r, Exception):
                views.append(AgentView(agent=a.name, role=a.voice, emoji=a.emoji, content=f"[ERROR: {r}]"))
            else:
                views.append(r)
        # luck
        luck = next((a for a in self.agents if a.name == "luck"), None)
        if luck:
            views.append(await luck.express(agenda))
        return views
    
    async def _express_serial(self, agenda) -> list[AgentView]:
        views = []
        for a in self.agents:
            try:
                v = await a.express(agenda)
                views.append(v)
            except Exception as e:
                views.append(AgentView(agent=a.name, role=a.voice, emoji=a.emoji, content=f"[ERROR: {e}]"))
        return views
    
    async def _respond_round(
        self,
        agenda,
        prev_views: list[AgentView],
        round_num: int,
    ) -> list[AgentView]:
        llm_agents = [a for a in self.agents if a.name != "luck"]
        tasks = [a.respond(agenda, prev_views, round_num) for a in llm_agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        new_views = []
        for a, r in zip(llm_agents, results):
            if isinstance(r, Exception):
                continue
            new_views.append(r)
        return new_views
    
    async def _vote_parallel(self, agenda) -> list[AgentVote]:
        tasks = [a.vote(agenda) for a in self.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        votes = []
        for a, r in zip(self.agents, results):
            if isinstance(r, Exception):
                votes.append(AgentVote(agent=a.name, role=a.voice, option="", weight=0, reasoning=f"[ERROR: {r}]"))
            else:
                votes.append(r)
        return votes
    
    def _fuse(
        self,
        options: list[str],
        votes: list[AgentVote],
    ) -> tuple[str, dict[str, float], list[str]]:
        """加权求和，返回得票最高的选项"""
        scores: dict[str, float] = {opt: 0.0 for opt in options}
        abstained: list[str] = []
        
        for v in votes:
            agent = next((a for a in self.agents if a.name == v.agent), None)
            weight = agent.current_weight if agent else 1.0
            
            if not v.option or v.option not in scores:
                abstained.append(v.agent)
                continue
            
            scores[v.option] += v.weight * weight
        
        if not scores or max(scores.values()) == 0:
            # 全部弃权 / 全 0 分 -> 随机选
            import random
            chosen = random.Random(self.state.seed).choice(options)
        else:
            chosen = max(scores, key=scores.get)
        
        return chosen, scores, abstained
    
    def _build_reasoning(self, votes, scores, chosen) -> str:
        lines = ["# 投票统计"]
        for opt, sc in scores.items():
            marker = "✅" if opt == chosen else "  "
            lines.append(f"{marker} {opt}: {sc:.2f}")
        lines.append("\n# 各方投票")
        for v in votes:
            sign = {2: "++", 1: "+", 0: "0", -1: "-", -2: "--"}.get(v.weight, "?")
            lines.append(f"- {v.emoji}{v.role} [{sign}] → {v.option or '弃权'}: {v.reasoning[:80]}")
        return "\n".join(lines)
    
    def update_agent_weights(self, record: DecisionRecord, outcome: str) -> None:
        """根据决策结果调整 agent 权重（简单的对错机制）"""
        # 这里可以做得更精细，目前是占位
        for a in self.agents:
            if a.name == "luck":
                continue
            # TODO: 基于 outcome 和每个 agent 之前的主张对比
            # 现在先保持简单
            pass
