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
        """投票

        当 LLM 调用失败（[LLM_ERROR] / [LLM_CALL_LIMIT_REACHED]）时，
        fallback 到基于选项关键词 + agent persona 的简单启发式：
        - 理性 / 现实 / 未来：偏好实际/经济类选项
        - 感性 / 家人：偏好关系/情感类选项
        - 野心：偏好"突破"类选项
        - 身体：偏好"健康/休息"类选项
        - 退而求其次：选第一个选项 + 中立权重
        """
        user_prompt = self._build_user_prompt(agenda, mode="vote")
        content = await self.llm.chat(self.system_prompt(), user_prompt, temperature=0.5)
        options = agenda.get("options", [])

        # Fallback 检测：LLM 失败时 content 是 [LLM_ERROR...] 或 [LLM_CALL_LIMIT_REACHED]
        if content.startswith("[LLM_ERROR") or content.startswith("[LLM_CALL_LIMIT_REACHED"):
            return self._fallback_vote(agenda, content)

        option, weight, reasoning, parse_fallback = self._parse_vote(content, options)
        # 强度和理由独立解析，让 _parse_vote 单独负责选项
        weight = self._parse_strength(content)
        reasoning = self._parse_reasoning(content)
        # 把 parse_fallback 信号写进 reasoning 前缀，让 driver / 输出能统计
        if parse_fallback:
            reasoning = f"[parse-fallback] {reasoning}"
        return AgentVote(
            agent=self.name,
            role=self.voice,
            emoji=self.emoji,
            option=option,
            weight=weight,
            reasoning=reasoning,
        )

    def _fallback_vote(self, agenda: dict[str, Any], err_content: str) -> AgentVote:
        """LLM 不可用时的启发式投票"""
        options = agenda.get("options", [])

        # 按 agent persona 找匹配选项
        persona_keywords = {
            "rational": ["实际", "稳", "数据", "数据", "成本", "收益", "效率", "系统", "理性", "算", "长期", "ROI", "钱", "薪资", "技术", "工作", "找", "学"],
            "emotional": ["关系", "感", "朋友", "TA", "陪伴", "一起", "感受", "爱", "生活", "玩", "享受", "当下", "跟着", "想要", "快乐"],
            "ambitious": ["大", "突破", "all in", "跳槽", "转", "新", "升", "创业", "高薪", "更好", "行业", "天花板", "看齐", "敢"],
            "realistic": ["稳", "现实", "先", "保留", "安全", "风险", "成本", "负债", "存款", "副业", "过渡", "观察"],
            "family": ["爸妈", "父母", "回家", "对象", "结婚", "相亲", "稳定", "传统", "陪伴", "家庭", "家人", "未来"],
            "future_me": ["5年", "10年", "20年", "临终", "长期", "未来", "天花板", "老了", "人生", "选择"],
            "body": ["休息", "运动", "健康", "调养", "跑步", "睡眠", "看医生", "养", "GAP", "停下来"],
        }
        # luck 走随机
        if self.name == "luck":
            import random
            rng = random.Random(hash((self.state.seed, self.state.current_quarter, self.name)))
            choice = options[rng.randint(0, len(options) - 1)] if options else ""
            return AgentVote(agent=self.name, role=self.voice, emoji=self.emoji,
                             option=choice, weight=rng.choice([-1, 0, 1]),
                             reasoning=f"[fallback random: {err_content[:30]}]")

        kws = persona_keywords.get(self.name, [])
        best_option = ""
        best_score = -1
        for opt in options:
            score = sum(1 for kw in kws if kw in opt)
            if score > best_score:
                best_score = score
                best_option = opt

        if not best_option:
            best_option = options[0] if options else ""

        return AgentVote(
            agent=self.name, role=self.voice, emoji=self.emoji,
            option=best_option, weight=1 if best_score > 0 else 0,
            reasoning=f"[fallback heuristic match score={best_score}: {err_content[:40]}]",
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
        """解析投票输出 (4 层 fuzzy fallback)

        解析顺序 (越靠前越精确):
        1. 严格正则: "选项: X" / "选项：X" (允许中英冒号 + 任意空白)
        2. 字母扫描: content 前 200 字符里出现 A/B/C... 单字母
        3. 选项文本: content 里出现选项本身的子串 (中英文混合)
        4. 默认 options[0] + 标记 _parse_fallback=True 让 caller 知道

        Returns:
            (option, weight, reasoning, parse_fallback) — 注意返回值多了一项

        修了 issue #2 (fuzzy parse)。原版在 LLM 不输出标准格式时会静默投
        options[0]，导致"vote tally"实质上是个 50% 噪声。v2 加了
        parse_fallback 标志，让 driver 可以统计"这个 decision 里 7 个 agent
        几个走 fallback"，作为 diversity 诊断信号。
        """
        # 默认
        option = options[0] if options else ""
        weight = 0
        reasoning = content[:200]
        parse_fallback = True  # 任何非严格路径都算 fallback

        if not content or not options:
            return option, weight, reasoning, parse_fallback

        # 1) 严格正则 — "选项: X" / "选项：X" 允许大写小写, 允许空白
        strict = re.search(r"选项\s*[：:]\s*([A-Za-z])", content)
        if strict:
            idx = ord(strict.group(1).upper()) - ord('A')
            if 0 <= idx < len(options):
                return options[idx], weight, reasoning, False  # 严格命中 = 不算 fallback

        # 2) 字母扫描 — content 前 200 字符里孤立的 A/B/C 单字母
        # 用 decision verb 上下文匹配: "选 A" / "投 B" / "倾向 C" / "A 项" / "A. xxx"
        # 避免 "API" / "B 站" / "C 盘" 误命中
        decision_verbs = r"(?:选|投|倾向|选是|答案是|选的是|就是|应选)"
        for i, opt in enumerate(options):
            letter = chr(ord('A') + i)
            patterns = [
                # 决策动词 + 字母
                rf"{decision_verbs}\s*{letter}\b",
                # 字母 + 标点 (像 "A." "A、" 列表项)
                rf"(?:^|[\s:：]){letter}\s*[\.。,，、]",
                # 行首字母
                rf"^{letter}\s*[\.。:：,，、]",
            ]
            for p in patterns:
                if re.search(p, content[:200], re.MULTILINE):
                    return opt, weight, reasoning, True  # 字母级 fallback

        # 3) 选项文本子串匹配 — 中文场景
        for opt in options:
            # 取选项前 6 字符 (中文截断友好) 作为匹配 key
            key = opt[:6].strip()
            if key and key in content:
                return opt, weight, reasoning, True  # 文本级 fallback

        # 4) 全部失败 -> 默认 options[0] (parse_fallback 已经是 True)
        return option, weight, reasoning, parse_fallback

    def _parse_strength(self, content: str) -> int:
        """解析投票强度 — 按 key 长度倒序匹配, 避免 "反对" 抢 "强烈反对" """
        for label, w in sorted(VOTE_WEIGHTS.items(), key=lambda kv: -len(kv[0])):
            if label in content:
                return w
        return 0

    def _parse_reasoning(self, content: str) -> str:
        """解析理由"""
        reason_match = re.search(r"理由[：:]\s*(.+)", content, re.DOTALL)
        if reason_match:
            return reason_match.group(1).strip()[:200]
        return content[:200]
