# Architecture

A quick tour of the moving parts. If you want to add a new persona, swap the
voting rule, or wire in a different LLM, this is the map.

## High-level flow

```
        ┌────────────────────────────────────────────────────────┐
        │                      config.yaml                       │
        │  (initial person, enabled agents, meeting triggers)    │
        └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌────────────────────────────────────────────────────────┐
        │  main.py  /  multi_run.py                              │
        │  Build LifeState from config + seed.                   │
        └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌────────────────────────────────────────────────────────┐
        │  core.driver.Driver  —  quarter loop                   │
        │  for each quarter:                                     │
        │    1. world.pick_event(state)         → Event         │
        │    2. council.run(event, state)       → Choice        │
        │    3. state.transition(choice)        → new state     │
        │    4. log decision                    → output/seed/  │
        └────────────────────────────────────────────────────────┘
                  │                    │                    │
                  ▼                    ▼                    ▼
        ┌──────────────┐   ┌─────────────────────┐   ┌──────────────┐
        │ core.world   │   │ meeting.council     │   │ core.state   │
        │ event picker │   │  (the boardroom)    │   │ 14 metrics   │
        └──────────────┘   └─────────────────────┘   └──────────────┘
                                  │
                                  ▼
        ┌────────────────────────────────────────────────────────┐
        │  Council — 4-stage deliberation                         │
        │                                                        │
        │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
        │  │ position │→ │ debate   │→ │ vote     │→ │ tally  │  │
        │  │(parallel)│  │(1 round) │  │(parallel)│  │(majority)│ │
        │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │
        │       │              │             │            │      │
        │       ▼              ▼             ▼            ▼      │
        │   7 LLM calls   7 LLM calls    7 LLM calls    rule     │
        └────────────────────────────────────────────────────────┘
```

## The boardroom in detail — `meeting/council.py`

For each milestone / crossroads / crisis / opportunity event, the Council runs
four stages:

### Stage 1 — `positions` (parallel)
Each enabled agent gets the event description, the current state, and its
persona prompt. They independently write a 100-200 word position. Runs as
`asyncio.gather` over the agent list.

### Stage 2 — `debate` (sequential, 1 round by default)
Each agent, in turn, sees **all other agents' positions** and is asked to
rebut, concede, or sharpen their stance. Configurable via
`LIFE_MAX_DEBATE_ROUNDS` (default `1`, can be `0` for ablations).

### Stage 3 — `vote` (parallel)
Each agent picks one of the 4-6 event options. Output is constrained to a
single token (option index) for reliability. Falls back to a default vote on
parse failure.

### Stage 4 — `tally`
Hard majority wins. Tie-breaking currently uses a fixed preference
(理性我 > 现实我 > 未来我 > ...). This is the most "blunt" part of the system
— see [Limitations](README.md#limitations) in the README and the
contribution ideas below.

## State model — `core/state.py`

`LifeState` tracks 14 metrics over 47 quarters (ages 18-30):

| Dimension | Range | Source |
|---|---|---|
| 净资产(万) | unbounded | salary - living - investments |
| 年收入(万) | 0-200 | role + city tier + path history |
| 事业等级 | 0-100 | path-dependent, with diminishing returns |
| 身体健康 | 0-100 | body agent, sleep, exercise |
| 心理健康 | 0-100 | regret, support network, autonomy |
| 关系网密度 | 0-100 | contacts maintained + new ties |
| 感情满意度 | 0-100 | relationship status × stage fit |
| 自由时间 (h/week) | 0-100 | workload inverse |
| 月现金流(万) | unbounded | net monthly |
| 学业 | 0-100 | GPA / publications / graduation |
| 行业地位 | 0-100 | role + tenure + reputation |
| 安全感 | 0-100 | income stability + health + relationships |
| 后悔指数 | 0-100 | accumulation of foregone paths |
| 父母关系 | 0-100 | family agent + actual contact |

Some metrics compound (net worth, career), some are bounded (health, mental),
some are path-dependent (regret, relationship). See `core/state.py` for the
transition function and the comments for the underlying intuitions.

## Where to extend

| You want to… | Touch this |
|---|---|
| Add a new persona | `agents/<name>.py` (subclass `BaseAgent`), enable in `config.yaml` |
| Add a new event | `data/events.json` (JSON list with `id`, `stage`, `type`, `title`, `description`, `options`) |
| Change voting rule | `meeting/council.py` `tally()` |
| Change state dynamics | `core/state.py` `transition()` |
| Add a new metric | `core/state.py` + `reporting/chart_builder.py` |
| Switch LLM provider | `llm/client.py` (drop-in OpenAI-compatible) |
| Add personality seeding | `multi_run.py` `_sample_person()` |

## Tech choices

- **Python 3.10+** for `asyncio` and `asyncio.gather` parallelism.
- **No framework** — `Driver` is a hand-rolled loop. No Django, no LangChain.
  Reasoning: the simulation logic is small enough that an agent framework
  would dominate the actual code, and a misbehaving framework would be hard
  to debug during long runs.
- **Jinja2** for the biography template. The persona/debate text is dumped
  as-is into the template — no post-processing, no LLM-based summarisation.
- **Chart.js** from CDN. The HTML reports are self-contained otherwise, so
  they can be opened from `file://` without a server.
- **JSON log + HTML report** as the only persistence. No DB. Easy to diff,
  easy to version, easy to ship.

## Concurrency model

`multi_run.py` uses `multiprocessing` (not threading) for the per-seed
workers — Python's GIL means a multi-agent deliberation would serialise
inside one process anyway. Each worker is an independent OS process with its
own LLM rate-limit budget, its own log, and its own progress file.

The per-seed deliberation itself uses `asyncio.gather` for the parallel
agent calls — single-process, single-event-loop, fan out 7 agents, fan in.

## Where the cost goes

For one life (47 decisions):
- ~750 LLM calls (47 × (8 positions + 8 debate + 8 votes) = ~1128 if every
  decision triggers a full meeting; crossroad events skip the debate, so
  closer to 750)
- ~5-15 min wall time on MiniMax-M2.7-highspeed
- ~$0.05 cost at MiniMax pricing

The biggest wins for cost reduction are:
1. **Smaller model** for the simple stages (vote is one token — could be a
   much smaller model than the position/debate).
2. **Skip debate on non-crossroad events** (already done — only
   `type in {milestone, crossroads, crisis, opportunity}` triggers debate).
3. **Cache event descriptions** that repeat across seeds (already done at
   the JSON level — events are static text).

## Known sharp edges

1. **LLM JSON parse failures** are swallowed and the agent falls back to
   abstention. Watch for silent agent no-shows in the `votes` log.
2. **State divergence under identical seed** can occur if the LLM is
   non-deterministic at temperature > 0. Set `LIFE_TEMPERATURE=0.0` for
   reproducible runs (slower, more boring).
3. **Long runs leak memory** if you don't restart the worker process. The
   default 1-life-per-worker design avoids this.
