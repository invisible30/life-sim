# Life Sim — Multi-Agent 人生沙盘

> 不同 agent 扮演"我"的多个侧面，每到人生关键岔路口就开"人生董事会"，让这些"我"开吵、投票、决策。看一段完整的人生如何展开。

## 核心理念

把"自我"拆成 7 个角色 + 1 个随机数：

| Agent | 关注 | 风格 |
|---|---|---|
| 🧠 理性我 | ROI、长期收益 | 数据驱动、冷淡 |
| 💔 感性我 | 体验、关系 | 表达充沛 |
| 🔥 野心版的我 | 35/40/45 岁的我 | 永远 push 更大 |
| 🪨 现实版的我 | 风险、约束 | 泼冷水、保守 |
| 👨‍👩‍👧 家人期待 | 父母叙事、稳定 | 传统价值 |
| 🕰️ 未来的我 | 5/10/20 年后视角 | 引用后悔学 |
| 🏃 身体 | 睡眠、运动、压力 | 简短提醒 |
| 🎲 运气 | 不可控事件 | 纯随机数 |

## 跑法

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配 LLM key
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY 或 ANTHROPIC_API_KEY

# 3. 跑
python -m life_sim.main
# 或
python main.py
```

## 产出

- `output/<seed>_biography.html` — 人生传记 + 仪表盘
- `output/<seed>_log.json` — 完整决策日志

## 目录

```
life-sim/
├── core/         # 状态、世界引擎、季度驱动
├── agents/       # 7 个 agent
├── meeting/      # 人生董事会
├── llm/          # LLM 客户端
├── output/       # 报告生成
├── data/         # 事件库 / 行业数据
└── main.py       # 入口
```
