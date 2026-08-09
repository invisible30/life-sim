# Life Sim — Multi-Agent 人生沙盘

> **What if your "self" were a board of directors?**
> 7 LLM agents + 1 dice, each playing a different facet of "you", argue and vote at every life crossroads. Then watch a whole life unfold, one quarter at a time.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![LLM](https://img.shields.io/badge/LLM-OpenAI%20Compatible-ff6b6b.svg)](https://platform.openai.com/)

[中文说明](#中文说明) · [Architecture](ARCHITECTURE.md) · [Live demo](output/compare.html)

---

## What is this?

A life-simulation sandbox where the agent is **not a single LLM** but a *council* of seven specialised LLM personas + one random die:

| Persona | Emoji | Vibe | Stance |
|---|---|---|---|
| **理性我** (Rational) | 🧠 | ROI-driven, cold, long-term | "What's the expected value?" |
| **感性我** (Emotional) | 💔 | Experience-first, relationship-led | "How does this *feel*?" |
| **野心我** (Ambitious) | 🔥 | 35/40/45-year-old me, always pushing | "Is this big enough?" |
| **现实我** (Realistic) | 🪨 | Risk-aware, constraint-focused | "What can actually go wrong?" |
| **家人** (Family) | 👨‍👩‍👧 | Parents' narrative, stability, duty | "What would they say?" |
| **未来我** (Future-Me) | 🕰️ | 5/10/20-year hindsight, regret studies | "Will older-me thank me?" |
| **身体** (Body) | 🏃 | Sleep, training, stress, health | "Are you sleeping enough?" |
| **运气** (Luck) | 🎲 | Pure dice roll, no LLM | N/A — just randomness |

At every *milestone* (high-school choice, first job, marriage, housing, layoff, burnout…), the seven agents independently write a position, then **deliberate in one debate round**, then **vote**. The majority pick (with a "fatigue" rule that lets later-life agents override the youth-majority) drives the next state.

Each run simulates one life from age 18 to 30 — about 45 decision points.

## Live demo

The repo ships with two rendered artefacts (see [`.gitignore`](.gitignore) for the whitelist rules):

- **[`output/compare.html`](output/compare.html)** — 10-seed side-by-side comparison (radar chart, scorecard, vote distribution). Open in any browser.
- **[`output/seed42/biography.html`](output/seed42/biography.html)** — full single-life biography (every debate, every vote, every metric over time).

Both load Chart.js from CDN, no build step, no server. Just open the file.

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure LLM
cp .env.example .env
# Edit .env — works with any OpenAI-compatible endpoint (OpenAI, Anthropic, MiniMax, etc.)

# 3. Run a single life (one seed, ~5-15 min depending on LLM)
python main.py

# 4. Run 10 lives in parallel and build a comparison report
python multi_run.py --seeds 10 --workers 4
# → output/seed<N>/{log.json, biography.html}
# → output/compare.html
```

That's it. No database, no auth, no deployment — just an LLM and a JSON config.

## Why this exists

A single LLM playing "you" will always sound the same: hedging, helpful, middle-of-the-road. Even with temperature=1.0, you get a single coherent narrator that converges on a stable personality.

This project inverts the framing: instead of asking *one* model "what would you do?", it stages a **deliberation** between seven explicitly-staked personas. They disagree on purpose. The output is messier, more vivid, and more recognisably human — because humans also argue with themselves.

The trade-off is honesty: this is a *creative* simulation, not a predictive one. It is a mirror, not a forecast. See [Limitations](#limitations) below.

## Project layout

```
life-sim/
├── main.py               # Single-life runner (one seed, end-to-end)
├── multi_run.py          # Multi-seed runner + comparison report builder
├── config.yaml           # Persona config, initial person, meeting triggers
├── requirements.txt
├── pyproject.toml
├── LICENSE
├── README.md
├── ARCHITECTURE.md
│
├── core/                 # Simulation kernel
│   ├── state.py          #   LifeState — 14 metrics over time
│   ├── world.py          #   World — event picker, RNG, narrative
│   └── driver.py         #   Driver — quarter loop, state transitions
│
├── agents/               # 8 personas (7 LLM + 1 random)
│   ├── base.py           #   Base agent interface
│   ├── rational.py
│   ├── emotional.py
│   ├── ambitious.py
│   ├── realistic.py
│   ├── family.py
│   ├── future_me.py
│   ├── body.py
│   └── luck.py           #   pure dice, no LLM
│
├── meeting/              # The "boardroom"
│   └── council.py        #   parallel positions → 1 debate round → parallel votes
│
├── llm/
│   └── client.py         # OpenAI-compatible client w/ exponential-backoff retry
│
├── reporting/            # HTML / chart rendering
│   ├── html_builder.py   #   Jinja2 + Chart.js, single biography
│   ├── chart_builder.py
│   └── templates/
│
├── data/
│   └── events.json       # 72 milestone events across 8 life stages
│
├── output/               # Runtime output (auto-generated)
│   ├── compare.html      #   ← committed: 10-seed comparison
│   └── seed42/           #   ← committed: single-seed showcase
│       ├── biography.html
│       └── log.json
│
└── tests/
```

## Configuration

Edit [`config.yaml`](config.yaml) to change the initial person, enable/disable agents, set meeting triggers, or tune the simulation horizon.

```yaml
simulation:
  start_age: 18
  end_age: 30
  initial_person:
    gaokao_score: 620
    family_background: middle  # upper / middle / working / rural
    city_tier: tier2            # tier1 / new_tier1 / tier2 / small
    personality_seed: { ... }   # Big-5-ish weights per seed
```

For multi-seed runs, `multi_run.py` re-samples `gaokao_score`, `family_background`, and `city_tier` per seed so you can sweep a diverse population.

## LLM compatibility

Tested with:

- **MiniMax** (`https://api.minimaxi.com/v1`, model `MiniMax-M2.7-highspeed`) — fast & cheap
- **OpenAI** (`gpt-4o-mini`, `gpt-4o`)
- **Anthropic** (via OpenAI-compatible proxy)

The default retry strategy is exponential backoff (4 attempts, ~1s → ~12s) on rate-limit and transient errors.

## Cost & runtime

A single life (47 decisions × 8 agents × 2 debate rounds ≈ 750 LLM calls) takes:

| Model | Wall time | Approx cost |
|---|---|---|
| MiniMax-M2.7-highspeed | ~5-10 min | ~$0.05 |
| GPT-4o-mini | ~10-15 min | ~$0.15 |
| GPT-4o | ~15-25 min | ~$1.50 |

A 10-seed parallel run (4 workers) takes ~2-3 hours total wall time on MiniMax.

Tune `LIFE_MAX_LLM_CALLS` in `.env` to cap the spend per worker.

## Limitations

Be honest about what this *isn't*:

- **Not predictive.** Real life is shaped by luck, relationships, and 10,000 micro-decisions. A 47-question survey of the major crossroads will *never* capture it.
- **LLM social-desirability bias.** All 7 personas are the same underlying model. They share a tendency toward "responsible adult" framings. The model will often output *"join clubs, do internships, save money"* even for a 493-score rural-background character. The starting point matters; the persona prompts softens it but don't fully override.
- **Vote aggregation is blunt.** Hard majority picks the "safe" option. Minority voices (especially the contrarian ones) get filtered. Adding weighted voting or a "minority override" probability is on the roadmap.
- **No memory across agents in debate.** Each agent sees the others' positions but not their internal chain-of-thought. With more capable models you could surface CoT into the deliberation.

## Contributing

PRs welcome. The most useful contributions are:

- New events in `data/events.json` (Chinese life milestones especially)
- New personas (e.g. **叛逆的我**, **躺平的我**, **健康焦虑的我** — the project would benefit from explicitly contrarian voices)
- Better aggregation rules in `meeting/council.py`
- Translating the persona prompts to other languages / cultural contexts
- Sample reports from different LLM providers

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system design and where to plug in.

## License

[MIT](LICENSE) — Copyright (c) 2026 steph.

## 中文说明

把"自我"拆成 7 个 LLM agent + 1 个骰子，每个人格带自己的立场和语气。每到一个人生关键岔路口，七个"我"就开吵——先各写立场，再来一轮辩论，最后投票，多数决推动状态往前走。

跑一个 18-30 岁的人生大约 47 个决策点，单 seed 5-15 分钟（取决于 LLM）。多 seed 并行跑 + 对比报告看 `output/compare.html`。

这个项目不是预测你的人生——它是一个**镜子**，不是水晶球。看 7 个互相拉扯的自己，是一件好玩的事。

七个人格：
- 🧠 理性我 — ROI、长期收益
- 💔 感性我 — 体验、关系
- 🔥 野心我 — 永远 push 更大
- 🪨 现实我 — 泼冷水、约束
- 👨‍👩‍👧 家人 — 父母叙事、稳定
- 🕰️ 未来我 — 后悔学、长期视角
- 🏃 身体 — 睡眠、运动、压力
- 🎲 运气 — 纯随机数，不调用 LLM

每跑一个 seed 会同时随机化起点（高考分 / 家庭 / 城市 / 大学），方便横向对比不同起点的人生轨迹。

## Acknowledgements

- The 7-persona design is a riff on *Internal Family Systems* therapy (Schwartz) and the "competing selves" framing in behavioural economics.
- The "future me uses regret research" prompt was inspired by Daniel Pink's *The Power of Regret*.
- Built and battle-tested with [MiniMax](https://platform.minimaxi.com/) `MiniMax-M2.7-highspeed`.
