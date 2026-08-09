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

# 【反趋同强制要求 — 修了 issue #5】
董事会里还有 6 个其他 "我"。**你不能为了"显得合理"就附和主流**。
具体来说:
- 如果理性我在算 ROI, 你（感性我 / 野心我 / 家人 / 身体 / 未来我）应该明确指出
  哪个角度被他忽略了, 而不是也去算一遍 ROI
- 如果其他人都选 A, 你如果不同意, **必须** 投 B 或弃权, **绝对不能因为"看起来大家
  都觉得 A 不错"就跟投**
- 你在表达立场时, 至少要提到 1 个其他 agent 的名字 (理性我/感性我/野心我/现实我/家人
  /未来我/身体/运气), 明确说"我跟 XX 不同意"或"XX 漏了 X"
- 选边时如果你的立场和别人 100% 一致, 在 voting 前先想一下"我是不是被多数派
  裹挟了"。如果是, 把投票强度降到中立 (0)
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
        """issue #16: 强化 memory 摘要, 让 LLM 真的能基于历史调整立场

        之前: 一行 'Q1 标题 → 选了 X, 结果 Y', LLM 几乎不用
        现在:
        - 自己的"立场"和"投票"(从 debate 记录里捞)
        - council 投票分布 (谁投了支持/反对)
        - outcome 拆解成 ↑/↓ 标记
        - 显式提示: "如果上次 outcome 不好, 这次你倾向怎么改?"
        """
        if not self.memory:
            return ""

        lines = []
        # 最多回看 5 条 (之前 3 条, 加点上下文)
        recent = self.memory[-5:]
        for m in recent:
            q = m.get("quarter", "?")
            title = m.get("title", "")[:30]
            chosen = m.get("chosen", "")
            outcome = m.get("outcome", "")
            my_stance = m.get("my_stance", "")  # 之前的 express() 立场
            council_votes = m.get("council_votes", {})  # {agent: weight}
            agent_name = m.get("agent", self.name)
            my_weight = council_votes.get(agent_name, 0)
            other_agents = [n for n in council_votes if n != agent_name]
            my_agreement = "支持" if my_weight > 0 else ("反对" if my_weight < 0 else "弃权")

            line = f"  - Q{q} {title} → 选了「{chosen}」, outcome: {outcome}"
            if my_stance:
                line += f"\n    你当时立场: {my_stance[:80]}"
            if council_votes:
                agree = [n for n, w in council_votes.items() if (w > 0) == (my_weight > 0) and w != 0]
                disagree = [n for n, w in council_votes.items() if (w > 0) != (my_weight > 0) and w != 0]
                line += f"\n    council 投票: 你{my_agreement} (weight {my_weight}); 同盟: {', '.join(agree) or '无'}; 反对: {', '.join(disagree) or '无'}"

            # 如果 outcome 包含 ↓, 显式提示
            if "↓" in outcome or "失败" in outcome or "后悔" in outcome:
                line += "\n    ⚠️ 上次 outcome 不理想, 这次要不要调整你的立场?"

            lines.append(line)

        return "\n".join(lines)
    
    def remember_decision(self, decision: dict[str, Any]) -> None:
        """issue #16: 记住更丰富的信息, 不只是 chosen + outcome

        多了:
        - my_stance: 这个 agent 当时在 express() 里写了什么立场
        - council_votes: 整张投票表 {agent: weight}, 用来算同盟/反对
        - agent: 这个 memory 条目属于哪个 agent (虽然 self.name 是, 但存一下)

        兼容 title / event_title 两种 key (driver.py 传 title, 老代码传 event_title)
        """
        self.memory.append({
            "quarter": decision.get("quarter"),
            "agent": decision.get("agent", self.name),
            "title": decision.get("title") or decision.get("event_title", ""),
            "chosen": decision.get("chosen", ""),
            "outcome": decision.get("outcome", ""),
            "my_stance": decision.get("my_stance", ""),
            "council_votes": decision.get("council_votes", {}),
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
        """issue #13: LLM 不可用时的真正 fallback, 不是 silent 猜测

        之前 (设计错的):
        - persona_keywords 在中文 option 里几乎必命中
        - "best score" 永远是某选项, 几乎不返回 options[0]
        - 7 个 agent fallback 投的经常跟 LLM 投的相同 -> "fallback" 是装饰

        现在 (新设计):
        1. luck 走纯随机 (之前一样)
        2. 其他 agent 走 persona -> option 类别映射 (类别匹配才投, 不靠 keyword 撞)
           每个 persona 对应一个 preferred category 列表:
           rational -> stability, growth
           emotional -> relationships, experience
           ambitious -> growth, challenge
           realistic -> stability, safety
           family -> stability, tradition
           future_me -> long_term, balance
           body -> health, rest
        3. option 的 "category" 来自 events.json 里 option_effects 的隐含标签
           (没标签就 fall back 到 options[0] + 中立)
        4. reasoning 加 [fallback: persona=X, category=Y, match=Z] 标签
           方便事后统计 "这个 decision 里 7 个 agent 几个走 fallback"
        """
        import logging
        import random
        options = agenda.get("options", [])
        event_id = agenda.get("id", "")

        # === 1. luck 走随机 ===
        if self.name == "luck":
            # issue #13: 不要用 hash() (Python per-process randomization, 不稳),
            # 用 (seed, quarter, name) 拼成稳定 seed
            seed_int = (
                int(self.state.seed) * 10000
                + int(self.state.current_quarter) * 10
                + sum(ord(c) for c in self.name)
            )
            rng = random.Random(seed_int)
            choice = options[rng.randint(0, len(options) - 1)] if options else ""
            return AgentVote(
                agent=self.name, role=self.voice, emoji=self.emoji,
                option=choice, weight=rng.choice([-1, 0, 1]),
                reasoning=f"[fallback: luck random pick (seed={seed_int}); error={err_content[:30]}]",
            )

        # === 2. persona -> preferred categories ===
        persona_categories = {
            "rational":   ["stability", "growth", "data", "control"],
            "emotional":   ["relationships", "experience", "feel", "people"],
            "ambitious":   ["growth", "challenge", "breakthrough", "advance"],
            "realistic":   ["stability", "safety", "baseline", "risk"],
            "family":      ["stability", "tradition", "home", "duty"],
            "future_me":   ["long_term", "balance", "no_regret", "perspective"],
            "body":        ["health", "rest", "balance", "self_care"],
        }
        preferred = persona_categories.get(self.name, [])

        # === 3. 从 events.json 读 option 的隐含 category ===
        option_categories = self._load_option_categories(event_id)

        # === 4. 选 option: 优先选 category 匹配 persona preferred 的, 都不匹配就 options[0] + 中立 ===
        best_option = ""
        best_score = 0
        best_match = ""
        for opt in options:
            cats = option_categories.get(opt, [])
            # 计算 persona preferred 和 option category 的交集大小
            overlap = set(cats) & set(preferred)
            score = len(overlap)
            if score > best_score:
                best_score = score
                best_option = opt
                best_match = ",".join(sorted(overlap))

        # 真正 fallback: 没有任何 category 匹配
        if not best_option or best_score == 0:
            best_option = options[0] if options else ""
            return AgentVote(
                agent=self.name, role=self.voice, emoji=self.emoji,
                option=best_option, weight=0,
                reasoning=f"[fallback: no category match, default to options[0]; error={err_content[:30]}]",
            )

        return AgentVote(
            agent=self.name, role=self.voice, emoji=self.emoji,
            option=best_option, weight=1 if best_score >= 1 else 0,
            reasoning=f"[fallback: persona={self.name}, matched={best_match}; error={err_content[:30]}]",
        )

    def _load_option_categories(self, event_id: str) -> dict[str, list[str]]:
        """从 events.json 读 event 的 option_effects 里提取 category 标签

        option_effects 形如:
          {"match": ["买", "上车"], "effects": {"net_worth": -40, ...}}

        第一层映射: keyword -> human-readable category
          "买/上车/贷款" -> "investment"
          "租/不买/继续" -> "stability"
          "回老家" -> "tradition"
          ...

        没找到 event / 没 option_effects -> 返回空 dict, fallback 走 default
        """
        # keyword -> category 映射 (issue #13 acceptance: 真正可解释的 category)
        KEYWORD_TO_CATEGORY = {
            "买": "investment", "上车": "investment", "贷款": "investment",
            "租": "stability", "不买": "stability", "再等等": "stability", "继续租": "stability",
            "回老家": "tradition", "小城市": "tradition",
            "考": "growth", "研": "growth", "学": "growth", "考证": "growth", "CPA": "growth", "CFA": "growth",
            "offer": "growth", "就业": "growth", "去大厂": "growth",
            "创业": "challenge", "跳槽": "challenge", "转": "challenge",
            "出国": "experience", "海外": "experience", "游学": "experience",
            "GAP": "rest", "gap": "rest", "休息": "rest", "停下来": "rest", "调养": "rest", "看医生": "rest",
            "回家": "tradition", "回家发展": "tradition",
            "分手": "experience", "相亲": "tradition", "结婚": "tradition", "对象": "tradition",
            "陪伴": "relationships", "TA": "relationships", "跟": "relationships",
            "做点小生意": "challenge", "接外包": "challenge",
        }
        if not event_id:
            return {}
        try:
            import json
            from pathlib import Path
            events_path = Path(__file__).parent.parent / "data" / "events.json"
            with open(events_path) as f:
                data = json.load(f)
            all_events = list(data.get("milestones", [])) + list(data.get("random_events", []))
            for ev in all_events:
                if ev.get("id") != event_id:
                    continue
                categories: dict[str, list[str]] = {}
                # 优先用 option_effects 拿 category
                for opt_effect in (ev.get("option_effects") or []):
                    keywords = opt_effect.get("match", [])
                    matched_cats = set()
                    for kw in keywords:
                        if kw in KEYWORD_TO_CATEGORY:
                            matched_cats.add(KEYWORD_TO_CATEGORY[kw])
                    for opt in (ev.get("options") or []):
                        if any(kw in opt for kw in keywords):
                            categories.setdefault(opt, []).extend(matched_cats)
                # 没 option_effects 的 event: option 全部归 "unknown"
                if not categories:
                    for opt in (ev.get("options") or []):
                        categories[opt] = ["unknown"]
                return categories
        except Exception:
            pass
        return {}
    
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
