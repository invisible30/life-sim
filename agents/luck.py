"""🎲 运气 — 纯随机数，不调 LLM"""
import random
from .base import Agent, AgentView, AgentVote


class LuckAgent(Agent):
    name = "luck"
    voice = "运气"
    emoji = "🎲"
    persona_intro = "不可控事件。我纯随机。"
    base_weight = 0.3  # 弱权重，但有时致命
    
    def persona_prompt(self) -> str:
        return "我不表达观点。我是随机数。"
    
    async def express(self, agenda):
        rng = random.Random(self.state.seed * 7919 + self.state.current_quarter)
        outcomes = ["这一天有点顺", "莫名其妙有点不顺", "没什么特别"]
        return AgentView(
            agent=self.name, role=self.voice, emoji=self.emoji,
            content=f"（掷骰子：{rng.choice(outcomes)}）",
            round=1,
        )
    
    async def respond(self, agenda, other_views, round_num):
        return AgentView(
            agent=self.name, role=self.voice, emoji=self.emoji,
            content="（运气不参与辩论）",
            round=round_num,
        )
    
    async def vote(self, agenda):
        options = agenda.get("options", [])
        if not options:
            return AgentVote(agent=self.name, role=self.voice, option="", weight=0, reasoning="无选项")
        rng = random.Random(self.state.seed * 17 + self.state.current_quarter + len(agenda.get('title','')))
        choice = rng.choice(options)
        # 50% 概率不投票 / 弃权
        if rng.random() < 0.5:
            return AgentVote(agent=self.name, role=self.voice, option="", weight=0, reasoning="弃权")
        # 弱随机权重
        weight = rng.choice([-1, 0, 1])
        return AgentVote(
            agent=self.name, role=self.voice, option=choice, weight=weight,
            reasoning=f"（纯随机摇到 {choice}）",
        )
