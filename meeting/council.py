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
import logging
import os
import time
from dataclasses import asdict
from typing import Any

from agents.base import Agent, AgentView, AgentVote
from agents import make_all_agents
from core.state import LifeState, DecisionRecord
from core.world import WorldEvent
from llm.client import LLMClient

logger = logging.getLogger(__name__)


class Council:
    """人生董事会主持人"""

    # Drift mechanism step size (issue #1 fix)
    # 每次决策后, 押对的 agent drift 增 +DRIFT_STEP, 押错的减 -DRIFT_STEP
    # drift 范围 [-0.5, 0.5]; current_weight 进一步 clamp 到 [0.2, 2.0]
    DRIFT_STEP: float = 0.05

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
        # 限流 semaphore：避免 7 个并发触发 minimax 限速
        max_conc = int(os.getenv("LIFE_MAX_CONCURRENT_LLM", "5"))
        self._llm_sem = asyncio.Semaphore(max_conc)
    
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
            event_id=event.id,            # issue #14: 写 event.id, 让 effect 计算能回查 events.json
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
            *[self._express_with_sem(a, agenda) for a in llm_agents],
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

    async def _express_with_sem(self, agent, agenda):
        async with self._llm_sem:
            return await agent.express(agenda)

    async def _express_serial(self, agenda) -> list[AgentView]:
        views = []
        for a in self.agents:
            try:
                async with self._llm_sem:
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
        """一轮辩论回应. mode 决定是串行 (真辩论) 还是并行 (旧行为, 给 ablation 用)

        Sequential 模式:
            agent 1 看到 prev_views -> 回应
            agent 2 看到 prev_views + agent 1 的最新回应 -> 回应
            agent 3 看到 prev_views + agent 1 + agent 2 的最新回应 -> 回应
            ...
        这样真辩论的 back-and-forth 才能发生: A 反驳 B 后, B 能看到 A 的反驳
        并修正自己立场.

        Parallel 模式 (旧行为):
            所有 agent 同时看到 prev_views 同一个 snapshot, 互不参考
            给 ablation / 性能测试用, 不推荐 (issue #6)

        通过 env var LIFE_DEBATE_MODE=parallel|sequential 切换, 默认 sequential.
        """
        mode = os.getenv("LIFE_DEBATE_MODE", "sequential").lower()
        llm_agents = [a for a in self.agents if a.name != "luck"]
        new_views: list[AgentView] = []

        if mode == "parallel":
            # 旧行为, 留给 ablation. issue #6 之前所有用户都用这个, 7 agent 同时
            # 拿到 prev_views 同一个 snapshot, 没人真辩论.
            tasks = [self._respond_with_sem(a, agenda, prev_views, round_num) for a in llm_agents]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for a, r in zip(llm_agents, results):
                if isinstance(r, Exception):
                    continue
                new_views.append(r)
        else:
            # 新行为: 真辩论. 每个 agent 看到截至目前的最新 views.
            rolling_views = list(prev_views)
            for a in llm_agents:
                try:
                    view = await self._respond_with_sem(a, agenda, rolling_views, round_num)
                    new_views.append(view)
                    # 把本 agent 的最新回应加进 rolling, 让下一个 agent 看到
                    rolling_views = rolling_views + [view]
                except Exception as e:
                    if not os.getenv("LIFE_QUIET"):
                        logger.warning("%s debate round %d failed: %s: %s",
                                       a.name, round_num, type(e).__name__, e)
                    continue
        return new_views

    async def _respond_with_sem(self, agent, agenda, prev_views, round_num):
        async with self._llm_sem:
            return await agent.respond(agenda, prev_views, round_num)

    async def _vote_parallel(self, agenda) -> list[AgentVote]:
        tasks = [self._vote_with_sem(a, agenda) for a in self.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        votes = []
        for a, r in zip(self.agents, results):
            if isinstance(r, Exception):
                votes.append(AgentVote(agent=a.name, role=a.voice, emoji=a.emoji, option="", weight=0, reasoning=f"[ERROR: {r}]"))
            else:
                # ALWAYS ensure emoji is set
                if not getattr(r, "emoji", None):
                    try:
                        r.emoji = a.emoji
                    except Exception:
                        raise RuntimeError(
                            f"vote from agent={a.name!r} (class={type(a).__name__}) "
                            f"has no emoji field and can't be patched. "
                            f"vote class: {type(r).__name__}, "
                            f"attrs: {sorted(vars(r).keys()) if hasattr(r, '__dict__') else 'no __dict__'}"
                        )
                votes.append(r)
        return votes

    async def _vote_with_sem(self, agent, agenda):
        async with self._llm_sem:
            return await agent.vote(agenda)
    
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
            sign = {2: "++", 1: "+", 0: "0", -1: "-", -2: "--"}.get(getattr(v, "weight", 0), "?")
            emoji = getattr(v, "emoji", "")
            role = getattr(v, "role", v.agent if hasattr(v, "agent") else "?")
            opt = getattr(v, "option", "") or "弃权"
            reason = getattr(v, "reasoning", "")[:80]
            lines.append(f"- {emoji}{role} [{sign}] → {opt}: {reason}")
        return "\n".join(lines)
    
    def update_agent_weights(self, record: DecisionRecord, effects: dict[str, float] | None = None) -> None:
        """根据决策结果调整 agent 权重（drift 机制）

        信号:
        - outcome_quality = sum(positive deltas) - sum(negative deltas)
          (从 effects dict 直接算, 不解析字符串)
        - agreement = sign(agent.vote_weight)   # 正=支持当选, 负=反对, 0=中立
        - drift_delta = agreement_sign × outcome_quality_sign × DRIFT_STEP

        效果:
        - agent 投了当选 + outcome 好  -> drift ↑ (这类判断"准", 多投点)
        - agent 投了当选 + outcome 坏  -> drift ↓ ("跟着大流选错", 减权)
        - agent 反对当选 + outcome 坏  -> drift ↑ ("少数派蒙对了", 增权)
        - agent 反对当选 + outcome 好  -> drift ↓ ("少数派错失", 减权)
        - 弃权/中立 -> drift 不变

        drift 会被 base.Agent.current_weight 自动 clamp 到 [-0.5, 0.5]
        (实际 current_weight 还会进一步 clamp 到 [0.2, 2.0])

        Closes #1 (drift mechanism was dead code; the loop body was a `pass`).
        """
        if effects is None:
            return

        outcome_quality = sum(v for v in effects.values() if isinstance(v, (int, float)))
        outcome_sign = 0
        if outcome_quality > 0.01:
            outcome_sign = 1
        elif outcome_quality < -0.01:
            outcome_sign = -1

        if outcome_sign == 0:
            # 中性 outcome (平稳) -> 不调权重, 避免噪声
            return

        votes = record.votes or {}
        for a in self.agents:
            if a.name == "luck":
                continue
            w = votes.get(a.name, 0)
            if w == 0:
                continue
            agreement_sign = 1 if w > 0 else -1
            # 投得"准" = agreement × outcome 同号
            if agreement_sign == outcome_sign:
                a.drift = min(0.5, a.drift + self.DRIFT_STEP)
            else:
                a.drift = max(-0.5, a.drift - self.DRIFT_STEP)
