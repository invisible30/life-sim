"""一次性脚本：扩展 events.json
- 给所有现有事件加 consequence_hint
- 新增 18 个事件覆盖 23-30 岁缺漏场景
- 标记 stage + 增加 per-option 数据 (不直接用，但 LLM 可见)
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data" / "events.json"

# 类型默认 hint
TYPE_HINT = {
    "milestone": "重大人生节点，影响后续 2-3 个事件链。",
    "opportunity": "机会窗口——选对了能撬动后续 5 年，选错了要付时间代价。",
    "crisis": "危机时刻——选错代价大，但走对了反而是最大成长机会。",
    "crossroads": "分叉路口。每个方向都是不同的人生路径，5 年后会变成不同的人。",
}


def gen_hint(ev):
    """根据事件内容生成 hint"""
    title = ev.get("title", "")
    desc = ev.get("description", "")
    base = TYPE_HINT.get(ev.get("type", "crossroads"), "")
    # 特定场景的 hint 增强
    if "买房" in title or "房" in title:
        base += " 房子在中国语境下绑定了婚姻/教育/养老，是个杠杆很大的决定。"
    elif "结婚" in title or "婚" in title:
        base += " 婚姻是 5-10 年承诺，影响家庭关系网/财务/事业节奏。"
    elif "孩子" in title or "要孩" in title or "要孩子" in title:
        base += " 孩子的决定不可逆，时间窗口也有限。"
    elif "工作" in title or "offer" in title or "入职" in title or "跳槽" in title:
        base += " 职业路径选择，会决定 5 年后的圈子、技能栈、收入曲线。"
    elif "AI" in title or "转型" in title or "行业" in title:
        base += " 行业选择的影响远超个人努力，'选择 > 努力' 在这里尤其明显。"
    elif "父母" in title or "家人" in title or "相亲" in title:
        base += " 家庭关系是中文语境下无法绕开的暗物质——表面看是选择，实际是关系。"
    elif "分手" in title or "离婚" in title:
        base += " 关系破裂会触发连锁反应：财务分割、心理状态、朋友圈重组。"
    elif "读研" in title or "考研" in title or "保研" in title or "出国" in title or "留学" in title:
        base += " 学历投资的回报曲线非线性——2-3 年后才会显现。"
    elif "身体" in title or "健康" in title or "病" in title or "医" in title:
        base += " 健康是底层资产，一次危机可能改变 5 年规划。"
    elif "朋友" in title or "友情" in title:
        base += " 30 岁后朋友会断崖式减少，关系的维护意愿是稀缺资源。"
    return base


# 新增的 18 个事件
NEW_EVENTS = [
    # === 23-26 岁：早期职业/关系深水区 ===
    {
        "id": "early_first_salary_invest",
        "trigger_age": 22.3,
        "type": "opportunity",
        "stage": "grad_school_or_first_job",
        "title": "第一份工资到手：怎么花？",
        "description": "刚发第一笔工资 1.2W。同学已经在晒'月入过万'，但你清楚这是税前、扣掉房租只剩 4K。",
        "options": [
            "50% 存起来/理财，先建立本金",
            "给爸妈发个红包+请吃饭",
            "犒劳自己，买一直想要的",
            "开始买保险（重疾+医疗）",
            "报班学新技能投资自己",
            "一半存一半花，别亏待自己"
        ]
    },
    {
        "id": "early_burnout",
        "trigger_age": 23.2,
        "type": "crisis",
        "stage": "early_career",
        "title": "职业倦怠：周一恐惧症",
        "description": "每天早上醒来都很累，'为什么要上班'这种念头反复出现。同事笑称你'精神离职'了。",
        "options": [
            "请假一周，出去散心",
            "看心理咨询师（公司有 EAP）",
            "开始面试下家，给自己退路",
            "找同事/朋友倾诉，看是否自己想多了",
            "下班后重建生活节奏（运动/爱好）",
            "做副业找新意义"
        ]
    },
    {
        "id": "early_meet_partner",
        "trigger_age": 23.5,
        "type": "opportunity",
        "stage": "early_career",
        "title": "在朋友局上认识了 TA",
        "description": "TA 跟你同年，聊得挺开心，加了微信。TA 在某互联网公司做产品。",
        "options": [
            "主动约下一次见面",
            "保持线上聊天，看感觉",
            "先问朋友了解下 TA 背景",
            "先不急，做朋友看看",
            "制造再次偶遇的机会",
            "坦诚表达好感，看反应"
        ]
    },
    {
        "id": "early_parents_health_pre",
        "trigger_age": 24.0,
        "type": "crisis",
        "stage": "early_career",
        "title": "妈妈在电话里说腰不好",
        "description": "妈妈在老家，腰疼了半年没告诉你，怕你担心。你在 1500 公里外。",
        "options": [
            "立刻请假回去带她看医生",
            "远程挂号+买机票让爸妈来北京检查",
            "每月固定给钱让她做理疗",
            "劝她来身边住一年",
            "暂时没办法，只能打电话关心",
            "开始考虑要不要回离家近的城市"
        ]
    },
    {
        "id": "early_career_leap",
        "trigger_age": 24.5,
        "type": "crossroads",
        "stage": "early_career",
        "title": "工作 2 年：要不要跳一次？",
        "description": "现在公司学不到新东西了，但团队氛围好。外面有人开出 +30% 薪资，但要从头开始。",
        "options": [
            "跳，趁年轻多试错",
            "不跳，深度积累 1-2 年再说",
            "先跟 leader 谈一次，看能否内部转岗",
            "骑驴找马，不裸辞",
            "去面试当练手，了解市场",
            "自己干点小项目先"
        ]
    },
    {
        "id": "early_living_alone",
        "trigger_age": 24.8,
        "type": "milestone",
        "stage": "early_career",
        "title": "第一次一个人住",
        "description": "从合租搬出来，租了一居室。第一个月很开心，第二个月开始有点孤独。",
        "options": [
            "养个宠物陪自己",
            "周末强迫自己出去社交",
            "把房间布置成自己喜欢的样子",
            "找室友/朋友合租过渡",
            "开始规律做饭+健身",
            "多回爸妈家/朋友家"
        ]
    },
    {
        "id": "early_friend_business",
        "trigger_age": 25.0,
        "type": "opportunity",
        "stage": "career_growth",
        "title": "好朋友要创业拉你入伙",
        "description": "大学室友准备做餐饮，已经租了铺面，找你做合伙人。要投 10W，做店长。",
        "options": [
            "加入，真金白银支持兄弟",
            "不投钱，但可以兼职帮忙出主意",
            "婉拒，自己工作正上升期",
            "看商业模式/财务计划再决定",
            "提少投一些试试水",
            "建议朋友先别急，再调研 3 个月"
        ]
    },
    {
        "id": "early_industry_winter",
        "trigger_age": 25.5,
        "type": "crisis",
        "stage": "career_growth",
        "title": "行业进入寒冬",
        "description": "教培/地产/互联网/金融——你所在的赛道突然不行了。裁员、减薪、招聘冻结。",
        "options": [
            "赶紧换赛道，趁还有竞争力",
            "熬下去，等周期反转",
            "转岗到行业里还有增长的细分",
            "利用行业经验做副业",
            "先去读个书给自己缓冲期",
            "考公/考编换稳定赛道"
        ]
    },
    {
        "id": "career_layoff_survival",
        "trigger_age": 26.0,
        "type": "crisis",
        "stage": "career_growth",
        "title": "被裁员了：N+1 到账",
        "description": "上一秒还在写代码，下一秒 HR 拉你签字。N+1 赔偿+年终奖结清，卡里突然多了 10W。",
        "options": [
            "先休息一个月，整理思绪",
            "立刻找工作，要快",
            "用赔偿金做点小生意",
            "开始认真考虑转行/读研",
            "谈赔偿最大化（+1 个月）",
            "先领失业保险+看机会"
        ]
    },
    {
        "id": "career_marriage_decision",
        "trigger_age": 26.5,
        "type": "crossroads",
        "stage": "career_growth",
        "title": "TA 暗示想结婚：你的真实想法？",
        "description": "恋爱 2 年，双方父母见过面。TA 暗示想领证。你心里其实有犹豫——TA 不是你 100% 确定的人。",
        "options": [
            "诚实告诉 TA 你还没准备好",
            "先订婚，婚期拖 1 年再决定",
            "答应了，婚姻也是磨合出来的",
            "建议两人一起做婚前咨询",
            "考虑分手，拖下去对 TA 不公平",
            "先不结婚，但同居试 1 年"
        ]
    },
    {
        "id": "career_first_100k",
        "trigger_age": 26.7,
        "type": "milestone",
        "stage": "career_growth",
        "title": "存款第一次过 50W",
        "description": "算上工资+理财+年终奖，存款第一次过 50W。父母打电话来问'要不要先帮你在老家付个首付'。",
        "options": [
            "先听父母意见",
            "自己决定，钱先留着投资自己",
            "首付一居室先上车",
            "给自己买个大件奖励",
            "开始做长期理财规划",
            "帮父母还房贷/给养老金"
        ]
    },
    {
        "id": "career_phd_opportunity",
        "trigger_age": 27.0,
        "type": "opportunity",
        "stage": "career_growth",
        "title": "老板让你读博：要不要？",
        "description": "导师/leader 看好你，劝你读博深造。3-5 年时间成本，毕业后能跳到大厂/高校/研究所。",
        "options": [
            "读，未来天花板更高",
            "不读，5 年时间比学位值",
            "在职读博试试",
            "跟家人商量再决定",
            "先问清楚读博的代价和回报",
            "考虑出国读博"
        ]
    },
    # === 27-30 岁：人生定型期 ===
    {
        "id": "settling_mortgage",
        "trigger_age": 27.5,
        "type": "milestone",
        "stage": "career_settling",
        "title": "房贷批下来了",
        "description": "月供 1.8W，30 年。突然觉得每个月的工资都不再是自己的。",
        "options": [
            "咬牙扛，这就是生活",
            "看看能不能出租一间分摊",
            "考虑跳槽涨薪扛房贷",
            "开始认真做副业补贴",
            "跟银行谈调整还款计划",
            "反思：是不是买贵了"
        ]
    },
    {
        "id": "settling_pregnancy",
        "trigger_age": 28.0,
        "type": "crossroads",
        "stage": "career_settling",
        "title": "意外怀孕了",
        "description": "你和 TA 都没计划这么早要孩子。但已经 8 周了。",
        "options": [
            "留下，开始准备当父母",
            "考虑清楚后做手术",
            "先告诉父母和 TA 商量",
            "看一下工作和家庭能否平衡",
            "给 1 个月时间想清楚",
            "咨询专业意见"
        ]
    },
    {
        "id": "settling_aging_self",
        "trigger_age": 28.5,
        "type": "crisis",
        "stage": "career_settling",
        "title": "第一次觉得自己'老了'",
        "description": "体检报告 8 项异常，熬夜一次要 3 天恢复。20 岁时熬一夜第二天照常，现在不行了。",
        "options": [
            "开始认真养生",
            "请私教+严格控制饮食",
            "做一次全面体检+调整",
            "把熬夜的工作尽量白天做完",
            "开始运动习惯（跑步/游泳）",
            "接受身体的自然变化"
        ]
    },
    {
        "id": "settling_friend_suicide",
        "trigger_age": 28.7,
        "type": "crisis",
        "stage": "career_settling",
        "title": "老同学突然离世",
        "description": "高中群里弹出消息：某某走了。35 岁，互联网中层，留下妻子和 3 岁孩子。你 3 个月前还在跟他吃饭。",
        "options": [
            "请假去参加追悼会",
            "捐款/帮家属",
            "开始重新审视自己的人生",
            "联系其他老同学聚一次",
            "把存款给家人多留一份",
            "跟自己说：要做想做的事"
        ]
    },
    {
        "id": "settling_overseas_offer",
        "trigger_age": 29.0,
        "type": "opportunity",
        "stage": "career_settling",
        "title": "海外/外企 offer：要不要出去？",
        "description": "新加坡/欧洲/硅谷的工作机会。薪资是国内 1.5-2 倍，但 3-5 年内可能不回来。",
        "options": [
            "去，趁年轻看看世界",
            "不去，已经在国内有根基",
            "先过去 1-2 年再说",
            "跟 TA 一起决定",
            "看家属能否一起过去",
            "谈远程办公的可能性"
        ]
    },
    {
        "id": "settling_mid_life_reckoning",
        "trigger_age": 29.5,
        "type": "crossroads",
        "stage": "career_settling",
        "title": "30 岁前夜：你到底想成为谁？",
        "description": "朋友圈里有人晒 100W 首付，有人晒 1 岁孩子，有人晒海外定居。你 30 岁，手里有什么？心里想要什么？",
        "options": [
            "专注自己设定的小目标",
            "彻底复盘一次过去 12 年",
            "跟导师/前辈深聊一次",
            "写一封给 5 年后的自己的信",
            "做心理咨询梳理内心",
            "什么都不想，先睡一觉"
        ]
    },
]


# 给所有现有事件加 consequence_hint
def enhance():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    
    # 1. 给现有事件加 consequence_hint
    for ms in data["milestones"]:
        if "consequence_hint" not in ms:
            ms["consequence_hint"] = gen_hint(ms)
    
    # 2. 给现有 random_events 加 consequence_hint
    for re in data["random_events"]:
        if "consequence_hint" not in re:
            re["consequence_hint"] = gen_hint(re)
    
    # 3. 追加新事件
    existing_ids = {m["id"] for m in data["milestones"]}
    added = 0
    for ne in NEW_EVENTS:
        if ne["id"] in existing_ids:
            print(f"  ⏭️  跳过已存在: {ne['id']}")
            continue
        ne["consequence_hint"] = gen_hint(ne)
        # 插到合适位置（按 trigger_age 排序）
        target_age = ne["trigger_age"]
        inserted = False
        for i, m in enumerate(data["milestones"]):
            if m["trigger_age"] > target_age:
                data["milestones"].insert(i, ne)
                inserted = True
                break
        if not inserted:
            data["milestones"].append(ne)
        added += 1
        print(f"  ✅ 新增事件: {ne['id']} ({ne['trigger_age']}岁) - {ne['title'][:30]}")
    
    # 4. 新增几个 random_events
    NEW_RANDOMS = [
        {
            "id": "r_ai_tool_unlock",
            "type": "opportunity",
            "title": "AI 工具突然能用了",
            "description": "GPT/Claude 让你的工作效率翻倍。多出来的时间用来干嘛？",
            "options": ["把活做精", "接更多活多赚钱", "学新技能", "提前下班", "做副业", "躺平休息"],
            "weight": 0.05
        },
        {
            "id": "r_burnout_signal",
            "type": "crisis",
            "title": "连续失眠一周",
            "description": "凌晨 3 点还醒着。身体在警告你。",
            "options": ["请假调整", "看医生", "运动+冥想", "换工作", "看心理咨询师", "熬过去"],
            "weight": 0.04
        },
        {
            "id": "r_unexpected_praise",
            "type": "opportunity",
            "title": "陌生人表扬了你",
            "description": "你做的事被人看到了。可能是同事/客户/网上的陌生人。",
            "options": ["继续深耕", "趁机扩大影响", "当没发生", "感谢对方", "思考这意味着什么", "跟人分享"],
            "weight": 0.03
        },
        {
            "id": "r_money_scam",
            "type": "crisis",
            "title": "差点被骗/被坑",
            "description": "投资/租房/网恋/刷单——某种骗局擦身而过，可能已经损失了一部分钱。",
            "options": ["报警", "跟朋友倾诉", "吃一堑长一智", "维权索赔", "调整心态", "查漏补缺"],
            "weight": 0.02
        },
    ]
    for r in NEW_RANDOMS:
        r["consequence_hint"] = gen_hint(r)
        data["random_events"].append(r)
        added += 1
        print(f"  ✅ 新增 random: {r['id']}")
    
    DATA.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    # 统计
    milestones = data["milestones"]
    randoms = data["random_events"]
    opt_counts = [len(m.get("options", [])) for m in milestones]
    print(f"\n📊 最终统计:")
    print(f"   milestones: {len(milestones)} (新增 {added - len(NEW_RANDOMS)})")
    print(f"   random_events: {len(randoms)} (新增 {len(NEW_RANDOMS)})")
    print(f"   options per milestone: min={min(opt_counts)}, max={max(opt_counts)}, avg={sum(opt_counts)/len(opt_counts):.1f}")
    print(f"   consequence_hint 覆盖: {sum(1 for m in milestones if 'consequence_hint' in m)}/{len(milestones)}")


if __name__ == "__main__":
    enhance()
