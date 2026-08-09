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
from core.sanitizer import sanitize_event


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
        # issue #20: 走 sanitizer 把 description / options 里可能的 prompt
        # 注入 (控制 token, "ignore previous instructions", 零宽字符) 清掉
        sanitized = sanitize_event({
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "options": list(self.options),
        })
        return {
            "id": sanitized["id"],
            "type": sanitized["type"],
            "title": sanitized["title"],
            "description": sanitized["description"],
            "options": sanitized["options"],
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

        连锁事件按 priority 排序（数字小的先生效，避免一 tick 多次触发）。
        """
        flags = self.state.flags
        age = self.state.current_age
        quarter = self.state.current_quarter

        # 用一个 set 防止同一 tick 重复触发
        if not hasattr(self, "_chain_cooldown"):
            self._chain_cooldown: dict[str, int] = {}
        # 每个 chain id 至少隔 3 个季度才能再触发（防止循环）
        def _ready(cid: str) -> bool:
            last = self._chain_cooldown.get(cid, -999)
            return quarter - last >= 3

        def _fire(cid: str) -> WorldEvent:
            self._chain_cooldown[cid] = quarter
            return None  # 实际事件在下面 _mk_chain_event 中返回

        # ===== 原有 5 个连锁事件 =====
        # 毕业 → 找工作焦虑
        if "in_job" not in flags and age > 22 and "grad_recently" in flags and _ready("chain_first_job_anxiety"):
            self._chain_cooldown["chain_first_job_anxiety"] = quarter
            return self._mk_chain_event(
                "chain_first_job_anxiety",
                "milestone",
                "刚毕业，还没稳定工作",
                "毕业一个月了，简历投了几十份。offer 没几个，焦虑开始上来了。",
                ["继续海投", "降低期望", "找实习过渡", "找朋友内推", "GAP 一个月", "做自由职业"],
            )

        # 结婚 → 蜜月期问题
        if "married_recently" in flags and age > 26 and _ready("chain_marriage_reality"):
            self._chain_cooldown["chain_marriage_reality"] = quarter
            return self._mk_chain_event(
                "chain_marriage_reality",
                "crisis",
                "新婚磨合期",
                "结婚 3-6 个月。开始暴露生活习惯差异：家务分配、消费观、跟朋友聚会频率。",
                ["坐下来开家庭会议", "互相妥协", "先忍着", "看婚姻咨询师", "冷战几天", "自己找事做"],
            )

        # 孩子 → 育儿压力
        if "has_child" in flags and age > 28 and _ready("chain_parenthood"):
            self._chain_cooldown["chain_parenthood"] = quarter
            return self._mk_chain_event(
                "chain_parenthood",
                "crisis",
                "新手父母",
                "宝宝 3 个月大。每晚醒 4 次，白天上班，晚上带娃。",
                ["请父母来帮忙", "请月嫂/育儿嫂", "换到轻松岗位", "咬牙撑过去", "跟公司谈弹性", "伴侣轮流请假"],
            )

        # 创业 → 现金流紧张
        if "in_startup" in flags and age > 27 and _ready("chain_cash_crunch"):
            self._chain_cooldown["chain_cash_crunch"] = quarter
            return self._mk_chain_event(
                "chain_cash_crunch",
                "crisis",
                "创业 6 个月，钱快烧完了",
                "账上资金只够撑 3 个月。下一轮融资还没着落。",
                ["拼命找下一轮", "自己掏钱补", "砍人砍成本", "开始盈利", "卖给大公司", "认清现实关掉"],
            )

        # 读研 → 论文压力
        if "in_grad_school" in flags and age > 23 and _ready("chain_thesis_pressure"):
            self._chain_cooldown["chain_thesis_pressure"] = quarter
            return self._mk_chain_event(
                "chain_thesis_pressure",
                "milestone",
                "研究生：开题/中期/答辩",
                "导师催你推进。论文/项目/实习三选一。",
                ["all in 论文", "找实习刷简历", "跟导师磨时间", "考虑转硕", "做横向赚钱", "退学工作"],
            )

        # ===== 新增 8 个连锁事件分支 =====

        # 买房 → 房贷压力
        if "home_owner" in flags and age > 25 and _ready("chain_mortgage_pressure"):
            self._chain_cooldown["chain_mortgage_pressure"] = quarter
            return self._mk_chain_event(
                "chain_mortgage_pressure",
                "crisis",
                "房贷压力：工资到账先还贷款",
                "月供占你工资 50%+。开始算每一笔支出。聚会不敢去了，新衣服不敢买了。",
                ["跟银行谈调整还款", "出租一间分摊", "拼命搞副业", "换工作涨薪", "跟另一半协调", "接受现实节衣缩食"],
            )

        # 换城市 → 适应期
        if "relocated" in flags and age > 22 and _ready("chain_relocate_adjust"):
            self._chain_cooldown["chain_relocate_adjust"] = quarter
            return self._mk_chain_event(
                "chain_relocate_adjust",
                "milestone",
                "新城市 3 个月：还没交到朋友",
                "下班回到出租屋，发现约饭都约不到人。周末一个人去公园。开始怀疑决定。",
                ["硬着头皮社交", "找老乡/校友群", "培养独自也能做的爱好", "考虑回去", "再坚持 3 个月", "跟原城市朋友保持线上联系"],
            )

        # 养宠物 → 宠物生病
        if "has_pet" in flags and age > 24 and _ready("chain_pet_sick"):
            self._chain_cooldown["chain_pet_sick"] = quarter
            return self._mk_chain_event(
                "chain_pet_sick",
                "crisis",
                "宠物突然生病了",
                "猫/狗不吃东西，送医发现需要手术，费用 1-3W。",
                ["花钱治", "保守治疗", "看情况决定", "先检查不治疗", "问医生真实预后", "找便宜点的医院"],
            )

        # 体制内 → 体制内天花板
        if "in_civil_service" in flags and age > 26 and _ready("chain_civilservice_plateau"):
            self._chain_cooldown["chain_civilservice_plateau"] = quarter
            return self._mk_chain_event(
                "chain_civilservice_plateau",
                "crossroads",
                "体制内 3 年：一眼看得到退休",
                "工资稳定，但晋升通道拥挤。同期进体制的同学有的已经是副科了。",
                ["继续熬", "走职称路线", "考在职研究生", "发展副业", "申请调动", "跳出国企"],
            )

        # GAP year → 价值重建
        if "in_gap_year" in flags and _ready("chain_gap_pressure"):
            self._chain_cooldown["chain_gap_pressure"] = quarter
            return self._mk_chain_event(
                "chain_gap_pressure",
                "crisis",
                "Gap 3 个月：父母开始问什么时候上班",
                "你还在调整节奏，但家里天天问'到底在干嘛'。存款在减少。",
                ["明确告诉他们你的计划", "找份兼职先干着", "重新评估 gap 的意义", "结束 gap 回正轨", "搬出去住断开干扰", "做点能交差的成果"],
            )

        # 长期高压 → 倦怠崩溃
        if "burned_out" in flags and _ready("chain_burnout_recovery"):
            self._chain_cooldown["chain_burnout_recovery"] = quarter
            return self._mk_chain_event(
                "chain_burnout_recovery",
                "milestone",
                "倦怠持续：决定怎么处理",
                "你开始逃避周一，看手机停不下来，'意义感'这个词很久没想了。",
                ["强制请假 2 周", "看心理咨询师", "跳槽换环境", "调整工作方式", "做副业找新意义", "接受这就是常态"],
            )

        # 多次决定推迟 → 决策疲劳
        if len(self.state.decisions) >= 18 and "decision_fatigue" not in flags:
            self.state.flags.add("decision_fatigue")
            return self._mk_chain_event(
                "chain_decision_fatigue",
                "crisis",
                "决策疲劳：什么都不想选了",
                "你已经做了 18+ 个决策，每个都改变人生走向。突然觉得累了。",
                ["让伴侣/家人帮选", "投硬币", "选最不讨厌的那个", "先不想了", "重置一下", "找信得过的人聊"],
            )

        # 出国 → 异地恋压力
        if "abroad" in flags and "has_partner" in flags and _ready("chain_long_distance"):
            self._chain_cooldown["chain_long_distance"] = quarter
            return self._mk_chain_event(
                "chain_long_distance",
                "crisis",
                "异地恋 1 年：开始扛不住",
                "时差 12 小时，每次视频都说不到 10 分钟。朋友圈看到别人的日常，有点酸。",
                ["买机票飞一次", "讨论团聚时间表", "考虑 TA 来你的城市", "考虑你回来", "坦诚说要不要继续", "想办法结束异地"],
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
