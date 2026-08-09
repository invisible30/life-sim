"""季度驱动器

每个季度：
1. world.tick() 生成事件
2. 决定是否召开董事会
3. 执行决策 → 更新指标 + 写连锁 flag + 触发买房等结构性事件
4. balance_sheet.tick_quarter → 工资入账、扣月供、房子增值
5. 同步净资产 (issue #31: net_worth 派生自 balance_sheet)
6. 记录历史 + 写 progress.log
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.state import LifeState, DecisionRecord
from core.world import World, WorldEvent
from core.balance_sheet import BalanceSheet
from meeting.council import Council

logger = logging.getLogger(__name__)


# 决策选项 → 连锁 flag 映射（影响后续事件）
OPTION_FLAG_MAP: list[dict[str, Any]] = [
    {"match": ["保研", "考研", "读研", "出国", "留学", "MBA"], "flags": ["in_grad_school", "grad_recently"]},
    {"match": ["结婚", "领证", "裸婚", "订婚"], "flags": ["married_recently", "has_partner"]},
    {"match": ["要孩子", "生孩子", "趁年轻", "要，趁年轻", "留下"], "flags": ["has_child"]},
    {"match": ["创业", "all in", "全职", "加入创业"], "flags": ["in_startup"]},
    {"match": ["工作", "入行", "入职", "接工作", "接 offer", "跳槽", "升职"], "flags": ["in_job", "grad_recently"]},
    {"match": ["考公", "考编", "公务员", "体制"], "flags": ["in_civil_service"]},
    {"match": ["gap", "休学", "GAP", "休息", "请假", "调养", "停下来"], "flags": ["in_gap_year"]},
    {"match": ["换城市", "去杭州", "去深圳", "去成都", "去新加坡", "去欧洲", "去硅谷"], "flags": ["relocated"]},
    {"match": ["买房", "上车", "付首付", "老破小", "新房"], "flags": ["home_owner"]},
    {"match": ["养猫", "养狗", "养宠物"], "flags": ["has_pet"]},
    {"match": ["在一起", "认真投入", "试试看", "跟 TA", "表白", "约下一次", "制造再次偶遇"], "flags": ["has_partner"]},
    {"match": ["分手", "断交", "裸辞", "辞职", "走人"], "flags": ["burned_out"]},
    {"match": ["海外", "新加坡", "欧洲", "硅谷", "出国读", "出国读博", "出国读研"], "flags": ["abroad"]},
]


# 买房事件关键词（issue #31）— 这些选项会触发 BalanceSheet.buy_house
HOUSING_BUY_KEYWORDS = ["买房", "上车", "付首付", "老破小", "新房"]


def update_flags(state: LifeState, chosen: str) -> None:
    """根据决策选项更新 state.flags"""
    state.flags.add("decision_made")  # 通用 flag
    for rule in OPTION_FLAG_MAP:
        for kw in rule["match"]:
            if kw in chosen:
                for f in rule["flags"]:
                    state.flags.add(f)
                break


# 决策选项 → 指标影响（粗略规则）
# 关键词匹配：选了包含某关键词的选项，就触发对应 effect
# 注意: net_worth 不在这里直接加减, 由 balance_sheet 派生 (issue #31)
OPTION_EFFECTS: list[dict[str, Any]] = [
    {
        "match": ["保研", "考研", "读研", "出国", "留学", "MBA"],
        "effects": {
            "career_level": +5,
            "career_income_yearly": -2,
            "free_hours_weekly": -10,
            "meaning_score": -3,
            "regret_index": -2,
            "skill_depth": +10,
            "social_capital": +5,
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
            "skill_depth": +5,
            "social_capital": +3,
        },
    },
    {
        "match": ["创业", "all in", "全职", "加入创业"],
        "effects": {
            "career_level": +2,
            "career_income_yearly": -3,
            "free_hours_weekly": -20,
            "physical_health": -5,
            "mental_health": -8,
            "meaning_score": +10,
            "regret_index": -5,
            "skill_depth": +8,
            "social_capital": +10,
        },
    },
    {
        "match": ["结婚", "领证", "要孩子", "生孩子"],
        "effects": {
            "relationship_density": +20,
            "romantic_health": +30,
            "free_hours_weekly": -15,
            "meaning_score": +5,
        },
    },
    {
        # issue #31: 买房的 net_worth 走 balance_sheet.buy_house, 这里不再硬扣
        "match": ["买房", "上车", "付首付", "老破小", "新房"],
        "effects": {
            "meaning_score": +8,
            "regret_index": -3,
        },
    },
    {
        "match": ["社团", "社交", "恋爱", "在一起"],
        "effects": {
            "relationship_density": +10,
            "romantic_health": +5,
            "free_hours_weekly": -5,
        },
    },
    {
        "match": ["休息", "休整", "请假", "gap", "休学"],
        "effects": {
            "physical_health": +10,
            "mental_health": +8,
            "physical_energy": +15,
            "free_hours_weekly": +10,
        },
    },
    {
        "match": ["辞职", "裸辞", "走人"],
        "effects": {
            "cash_flow_monthly": -1.5,
            "mental_health": +5,
            "physical_energy": +10,
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
            "physical_energy": +10,
            "social_capital": +5,
        },
    },
    {
        "match": ["锻炼", "调养", "看医生", "运动", "睡觉"],
        "effects": {
            "physical_health": +5,
            "mental_health": +3,
            "physical_energy": +10,
        },
    },
    {
        "match": ["参加", "加入", "报名", "去"],
        "effects": {
            "career_level": +2,
            "relationship_density": +3,
            "skill_depth": +2,
        },
    },
    {
        "match": ["不参加", "不加入", "放弃", "拒绝", "婉拒"],
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
    {
        "match": ["换城市", "去杭州", "去深圳", "去成都", "relocate"],
        "effects": {
            "social_capital": -8,
            "relationship_density": -5,
            "career_level": +3,
            "career_income_yearly": +2,
        },
    },
    {
        "match": ["养猫", "养狗", "养宠物"],
        "effects": {
            "mental_health": +5,
            "physical_energy": +3,
            "free_hours_weekly": -3,
        },
    },
    {
        "match": ["副业", "私活", "自媒体", "内容", "直播"],
        "effects": {
            "career_income_yearly": +3,
            "free_hours_weekly": -10,
            "skill_depth": +3,
            "social_capital": +2,
            "physical_energy": -5,
        },
    },
    {
        "match": ["兴趣", "爱好", "跑步", "健身", "摄影", "乐器"],
        "effects": {
            "physical_energy": +10,
            "mental_health": +5,
            "meaning_score": +5,
        },
    },
]


# 事件类型默认影响
TYPE_EFFECTS = {
    "milestone": {"regret_index": -1, "skill_depth": +1},
    "opportunity": {"regret_index": -2, "social_capital": +1},
    "crisis": {"physical_health": -2, "mental_health": -2, "physical_energy": -3},
    "crossroads": {"meaning_score": +1},
}


# 经济周期对每个季度的基础影响 (issue #31: net_worth 走 balance_sheet.cash)
ECONOMY_EFFECTS = {
    "boom": {"career_income_yearly": +1.5, "net_worth": +2},
    "normal": {"career_income_yearly": +0.5, "net_worth": +0.5},
    "recession": {"career_income_yearly": -0.3, "net_worth": -0.5},
    "crisis": {"career_income_yearly": -1.0, "net_worth": -1.5, "mental_health": -3},
}


def apply_effects(metrics_obj, effects: dict[str, float], balance_sheet: BalanceSheet | None = None) -> None:
    """应用效果到 LifeMetrics（14 项）

    issue #31: net_worth 不再直接写 metrics, 而是作为 balance_sheet.cash 的增减。
    真正的净资产在 tick_quarter 末尾统一从 balance_sheet.net_worth 派生。
    """
    for k, v in effects.items():
        if k == "net_worth":
            # 净资产效果 → 现金增减 (issue #31)
            if balance_sheet is not None:
                balance_sheet.cash += v
            else:
                # fallback: 旧行为, 写 metrics (向后兼容)
                metrics_obj.net_worth += v
        elif k == "cash_flow_monthly":
            metrics_obj.cash_flow_monthly += v
        elif k == "physical_health":
            metrics_obj.physical_health = max(0, min(100, metrics_obj.physical_health + v))
        elif k == "mental_health":
            metrics_obj.mental_health = max(0, min(100, metrics_obj.mental_health + v))
        elif k == "relationship_density":
            metrics_obj.relationship_density = max(0, min(100, metrics_obj.relationship_density + v))
        elif k == "romantic_health":
            metrics_obj.romantic_health = max(0, min(100, metrics_obj.romantic_health + v))
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
        elif k == "skill_depth":
            metrics_obj.skill_depth = max(0, min(100, metrics_obj.skill_depth + v))
        elif k == "social_capital":
            metrics_obj.social_capital = max(0, min(100, metrics_obj.social_capital + v))
        elif k == "physical_energy":
            metrics_obj.physical_energy = max(0, min(100, metrics_obj.physical_energy + v))


def _load_event_by_id(event_id: str) -> dict | None:
    """从 events.json 里读单个 event, 找不到返回 None. 失败时不抛 (log warning)."""
    if not event_id:
        return None
    try:
        import json
        with open(_EVENTS_PATH) as f:
            data = json.load(f)
        all_events = list(data.get("milestones", [])) + list(data.get("random_events", []))
        for ev in all_events:
            if ev.get("id") == event_id:
                return ev
    except Exception:
        pass
    return None


# issue #14: events.json 路径, 给 _load_event_by_id 用
from pathlib import Path
_EVENTS_PATH = Path(__file__).parent.parent / "data" / "events.json"


def compute_decision_effects(decision: DecisionRecord) -> dict[str, float]:
    """根据决策计算 effect, 优先级 (高 -> 低):
    1. events.json 里这个 event 的 per-event effects (issue #14)
    2. events.json 里这个 event 的 per-option effects (新加: 按 chosen 选对应 effect)
    3. OPTION_EFFECTS 关键字规则 (旧逻辑, 向后兼容)
    4. TYPE_EFFECTS 类型默认 (旧逻辑)
    """
    effects: dict[str, float] = {}

    # 1+2) 查 events.json 里的 event-level effects
    event_def = _load_event_by_id(decision.event_id)
    if event_def is not None:
        # 1) event-level 全局 effects (无 option 依赖)
        for k, v in (event_def.get("effects") or {}).items():
            effects[k] = effects.get(k, 0) + v
        # 2) event-level option-specific effects
        for opt_rule in (event_def.get("option_effects") or []):
            keywords = opt_rule.get("match", [])
            if any(kw in decision.chosen for kw in keywords):
                for k, v in opt_rule.get("effects", {}).items():
                    effects[k] = effects.get(k, 0) + v
                break  # 只匹配第一个

    # 3) OPTION_EFFECTS 关键字规则
    for rule in OPTION_EFFECTS:
        for kw in rule["match"]:
            if kw in decision.chosen:
                for k, v in rule["effects"].items():
                    effects[k] = effects.get(k, 0) + v
                break

    # 4) TYPE_EFFECTS 类型默认
    type_def = TYPE_EFFECTS.get(decision.event_type, {})
    for k, v in type_def.items():
        effects[k] = effects.get(k, 0) + v

    return effects


class Driver:
    """季度驱动器"""

    def __init__(
        self,
        state: LifeState,
        world: World,
        council: Council,
        end_quarter: int | None = None,
        housing_config: dict | None = None,
    ):
        self.state = state
        self.world = world
        self.council = council
        self.start_quarter = 0
        # 允许覆盖 end_quarter（默认 48 季度 = 12 年）
        self.end_quarter = end_quarter if end_quarter is not None else 48
        # 资产负债表配置 (issue #31)
        self.housing_config = housing_config or {}
        # 买房的随机种子 — 同一 seed 跑出来房价一致
        self._buy_rng = random.Random(state.seed)

    def _house_price_for(self, city_tier: str) -> float:
        """根据城市档次 + 随机扰动计算房价"""
        base = self.housing_config.get("base_price_per_tier", {}).get(city_tier, 200)
        jitter = self.housing_config.get("price_jitter", 0.20)
        # ±jitter 随机
        factor = self._buy_rng.uniform(1 - jitter, 1 + jitter)
        return round(base * factor, 1)

    def _try_buy_house(self, decision: DecisionRecord) -> dict | None:
        """检测到买房决策就调 balance_sheet.buy_house
        Returns: buy_house() 返回的 dict, 或者 None (失败/未触发)
        """
        chosen = decision.chosen
        if not any(kw in chosen for kw in HOUSING_BUY_KEYWORDS):
            return None
        if self.state.balance_sheet.has_house:
            return None  # 已经有房, 跳过

        house_price = self._house_price_for(self.state.person.city_tier)
        try:
            info = self.state.balance_sheet.buy_house(
                house_price=house_price,
                down_payment_ratio=self.housing_config.get("down_payment_ratio", 0.30),
                mortgage_rate_annual=self.housing_config.get("mortgage_rate", 0.0375),
                mortgage_years=self.housing_config.get("mortgage_years", 30),
            )
            decision.outcome_extra = {"house_purchase": info}
            return info
        except ValueError as e:
            # 现金不够首付 — 记日志, 不强行买
            logger.warning("买房失败: %s", e)
            return None

    async def run(self, quiet: bool = False) -> list[DecisionRecord]:
        """跑完所有季度"""
        for q in range(self.start_quarter, self.end_quarter):
            await self.tick_quarter(q, quiet=quiet)
            self._write_progress()
        return self.state.decisions

    def _write_progress(self) -> None:
        """写 progress.log 行（tail -f 可看）"""
        path = os.environ.get("LIFE_PROGRESS_LOG", "")
        if not path:
            return
        try:
            m = self.state.metrics
            line = json.dumps({
                "ts": time.time(),
                "q": self.state.current_quarter,
                "age": round(self.state.current_age, 2),
                "stage": self.state.life_stage,
                "decisions": len(self.state.decisions),
                "last_title": self.state.decisions[-1].event_title if self.state.decisions else "",
                "last_chosen": self.state.decisions[-1].chosen if self.state.decisions else "",
                "net_worth": round(m.net_worth, 1),
                "income": round(m.career_income_yearly, 1),
                "career": round(m.career_level, 1),
                "health": round(m.physical_health, 0),
                "mental": round(m.mental_health, 0),
                "meaning": round(m.meaning_score, 0),
                "flags": sorted(self.state.flags),
            }, ensure_ascii=False)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

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
                # 各 agent 记住这次决策 (issue #16: 带更丰富 context)
                # 收集每个 agent 自己的立场 (从 debates 里)
                my_stances: dict[str, str] = {}
                for view in decision.debates:
                    if view.get("round") == 1 and view.get("agent"):
                        my_stances[view["agent"]] = view.get("content", "")
                for a in self.council.agents:
                    a.remember_decision({
                        "quarter": decision.quarter,
                        "agent": a.name,
                        "title": decision.event_title,
                        "chosen": decision.chosen,
                        "outcome": decision.outcome,
                        "my_stance": my_stances.get(a.name, ""),
                        "council_votes": dict(decision.votes or {}),
                    })
            except Exception as e:
                if not quiet:
                    import traceback, sys
                    msg = f"\n⚠️  Q{q} council error: {type(e).__name__}: {e}\n"
                    msg += "".join(traceback.format_exception(type(e), e, e.__traceback__))
                    sys.stdout.write(msg)
                    sys.stdout.flush()

        # 3. 应用 effects + 写连锁 flag
        if decision:
            effects = compute_decision_effects(decision)
            # 买房特殊处理: 一次性扣首付 + 记贷款 (issue #31)
            house_info = self._try_buy_house(decision)
            # 删掉 effects 里的 net_worth (因为买房走 balance_sheet), 防止双重扣
            if house_info is not None:
                effects.pop("net_worth", None)
            apply_effects(self.state.metrics, effects, balance_sheet=self.state.balance_sheet)
            # 4. 决策后调 drift 机制 (issue #1: 之前是空 pass)
            try:
                self.council.update_agent_weights(decision, effects)
            except Exception as e:
                if not quiet:
                    logger.warning("drift update failed: %s: %s", type(e).__name__, e)
            update_flags(self.state, decision.chosen)
            decision.outcome = _summarize_outcome(self.state, effects, house_info)

        # 4. 经济周期基础影响 (net_worth 走 balance_sheet.cash)
        econ = ECONOMY_EFFECTS.get(self.world.economy_phase, ECONOMY_EFFECTS["normal"])
        apply_effects(self.state.metrics, econ, balance_sheet=self.state.balance_sheet)

        # 5. 资产负债表推进: 工资入账 + 扣月供 + 房子增值 (issue #31)
        bs_info = self.state.balance_sheet.tick_quarter(
            income_yearly=self.state.metrics.career_income_yearly,
            savings_rate=self.housing_config.get("savings_rate", 0.40),
            house_appreciation_annual=self.housing_config.get("house_appreciation", 0.02),
        )
        # 净资产从 balance_sheet 派生
        self.state.metrics.net_worth = self.state.balance_sheet.net_worth
        # 月现金流 = 月储蓄 - 月供
        quarterly_save = bs_info["saved"]
        quarterly_mortgage = bs_info["quarterly_mortgage"]
        self.state.metrics.cash_flow_monthly = round(
            (quarterly_save - quarterly_mortgage) / 3, 2
        )

        # 6. 自然漂移
        _natural_drift(self.state.metrics, self.state.life_stage)

        # 7. 记录
        self.state.record_metrics()

        # 8. 打印进度
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


def _summarize_outcome(
    state: LifeState,
    effects: dict[str, float],
    house_info: dict | None = None,
) -> str:
    parts = []
    if house_info is not None:
        # 买房: 显示房价/首付/月供
        parts.append(
            f"🏠 房价 {house_info['house_price']:.0f}w / "
            f"首付 {house_info['down_payment']:.0f}w / "
            f"月供 {house_info['monthly_payment']:.2f}w"
        )
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
    """issue #21: 从 print 改 logger.info (level 0 = no log, 1 = info)."""
    quiet = os.getenv("LIFE_QUIET", "0") == "1"
    if quiet:
        return
    age = state.current_age
    stage = state.life_stage
    if decision:
        title = decision.event_title[:30]
        chosen = decision.chosen[:15]
        logger.info("  Q%-2d | %5.1f岁 %-20s | 📌 %s → %s  [经济:%s]",
                    state.current_quarter, age, stage, title, chosen, econ_phase)
    else:
        logger.info("  Q%-2d | %5.1f岁 %-20s | （平淡期）  [经济:%s]",
                    state.current_quarter, age, stage, econ_phase)
