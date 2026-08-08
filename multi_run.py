"""多 seed 跑 + 对比报告

跑 N 个不同 seed（每个 seed 派生出不同人设），生成对比 HTML。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from core.state import init_state_from_config, LifeState
from core.world import World
from core.driver import Driver
from meeting.council import Council
from llm.client import LLMClient
from output.html_builder import build_html


ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"
SEEDS = [42, 7, 123, 256, 999]


async def run_one_seed(seed: int, cfg: dict, llm: LLMClient, randomize: bool = True) -> dict:
    """跑一个 seed，返回摘要"""
    state = init_state_from_config(cfg, seed=seed, randomize_person=randomize)
    
    p = state.person
    print(f"\n{'='*60}")
    print(f"🌱 Seed={seed}: 高考{p.gaokao_score} → {p.university}")
    print(f"   家庭={p.family_background} 城市={p.city_tier} 性别={p.gender}")
    print(f"   启动资金={p.initial_cash/10000:.1f}万 父母期待={p.parents_expectation}")
    print(f"   人格 O={p.personality.openness:.2f} C={p.personality.conscientiousness:.2f} "
          f"E={p.personality.extraversion:.2f} A={p.personality.agreeableness:.2f} N={p.personality.neuroticism:.2f}")
    print(f"   LLM 已调用: {llm.call_count}")
    print(f"{'='*60}")
    
    world = World(state)
    council = Council(
        llm=llm, state=state,
        enabled_agents=cfg.get("agents", {}),
        max_debate_rounds=cfg.get("output", {}).get("max_debate_rounds", 2),
        parallel=os.getenv("LIFE_PARALLEL_AGENTS", "true").lower() == "true",
    )
    driver = Driver(state, world, council)
    
    t0 = time.time()
    
    # 实时进度 watchdog：每 10 秒打印一次，包含阶段、最近决策、LLM 进度
    last_decision_count = 0
    last_decision_title = ""
    
    async def watchdog():
        nonlocal last_decision_count, last_decision_title
        while True:
            await asyncio.sleep(10)
            n_dec = len(state.decisions)
            if n_dec > last_decision_count:
                last_decision_count = n_dec
                last_decision_title = state.decisions[-1].event_title[:25]
            remaining_calls = llm.remaining_calls
            pct = (llm.call_count / llm.cfg.max_total_calls) * 100
            bar_len = 20
            filled = int(bar_len * llm.call_count / llm.cfg.max_total_calls)
            bar = "█" * filled + "░" * (bar_len - filled)
            
            print(
                f"\r  ⏱ {int(time.time()-t0):3d}s | "
                f"Q{state.current_quarter:2d}/48 | "
                f"{state.life_stage:22s} | "
                f"决策 {n_dec:2d} | "
                f"LLM [{bar}] {llm.call_count:3d}/{llm.cfg.max_total_calls} ({pct:.0f}%) | "
                f"📌 {last_decision_title:<25s}",
                end="", flush=True,
            )
    
    wd = asyncio.create_task(watchdog())
    try:
        await driver.run(quiet=True)
    finally:
        wd.cancel()
        try:
            await wd
        except asyncio.CancelledError:
            pass
        print(flush=True)  # newline after progress line
    
    elapsed = time.time() - t0
    
    final = state.metrics.as_dict()
    decisions_count = len(state.decisions)
    print(f"\n✓ Seed={seed} 完成: {decisions_count} 决策, {elapsed:.0f}s, "
          f"LLM累计 {llm.call_count}")
    print(f"  终态: 净资产{final['净资产(万)']}万 年收入{final['年收入(万)']}万 "
          f"后悔{final['后悔指数']:.0f}")
    
    # 写每个 seed 的产物
    seed_dir = OUTPUT_DIR / f"seed{seed}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    
    log_path = seed_dir / "log.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "seed": seed,
            "elapsed_sec": elapsed,
            "llm_calls_total": llm.call_count,
            "person": {
                "gaokao_score": state.person.gaokao_score,
                "university": state.person.university,
                "family_background": state.person.family_background,
                "city_tier": state.person.city_tier,
                "gender": state.person.gender,
                "personality": {
                    "openness": state.person.personality.openness,
                    "conscientiousness": state.person.personality.conscientiousness,
                    "extraversion": state.person.personality.extraversion,
                    "agreeableness": state.person.personality.agreeableness,
                    "neuroticism": state.person.personality.neuroticism,
                },
                "initial_cash": state.person.initial_cash,
            },
            "final_metrics": final,
            "metrics_history": state.metrics_history,
            "decisions": [_decision_to_dict(d) for d in state.decisions],
        }, f, ensure_ascii=False, indent=2, default=str)
    
    # 写单人传记
    from main import _generate_letter
    letter = await _generate_letter(state, llm)
    html_path = seed_dir / "biography.html"
    build_html(
        state=state,
        decisions=state.decisions,
        llm_call_count=llm.call_count,
        letter_text=letter,
        output_path=str(html_path),
    )
    
    return {
        "seed": seed,
        "person": {
            "gaokao_score": state.person.gaokao_score,
            "university": state.person.university,
            "family_background": state.person.family_background,
            "city_tier": state.person.city_tier,
        },
        "elapsed_sec": elapsed,
        "decisions": decisions_count,
        "final": final,
    }


def _decision_to_dict(d) -> dict:
    return {
        "id": d.id, "quarter": d.quarter, "age": d.age,
        "event_title": d.event_title, "event_description": d.event_description,
        "event_type": d.event_type, "options": d.options,
        "chosen": d.chosen, "votes": d.votes,
        "debates": d.debates, "reasoning": d.reasoning, "outcome": d.outcome,
    }


async def main():
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    
    # 实时进度日志（tail -f 可看）
    progress_log = ROOT / "output" / "progress.log"
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    if progress_log.exists():
        progress_log.unlink()
    os.environ["LIFE_PROGRESS_LOG"] = str(progress_log)
    print(f"📊 实时进度: tail -f {progress_log}")
    
    # 一个 LLM 客户端，复用配额
    llm = LLMClient()
    print(f"🤖 LLM: {llm.cfg.model}, 上限 {llm.cfg.max_total_calls} 调用")
    
    # 跑 N 个 seed
    seeds_to_run = SEEDS[:int(os.getenv("LIFE_NUM_SEEDS", "3"))]
    print(f"🌍 准备跑 {len(seeds_to_run)} 个 seed: {seeds_to_run}")
    
    results = []
    for seed in seeds_to_run:
        if llm.remaining_calls < 50:
            print(f"⚠️ LLM 剩余调用不足 ({llm.remaining_calls})，跳过剩余 seed")
            break
        result = await run_one_seed(seed, cfg, llm, randomize=True)
        results.append(result)
    
    # 写对比报告
    compare_path = OUTPUT_DIR / "compare.html"
    build_compare_html(results, compare_path)
    print(f"\n📊 对比报告: {compare_path}")
    print(f"💾 LLM 累计调用: {llm.call_count}")
    print(f"🏁 完。")


def build_compare_html(results: list[dict], output_path: Path) -> None:
    """生成多 seed 对比 HTML"""
    rows = []
    for r in results:
        p = r["person"]
        f = r["final"]
        rows.append(f"""
        <tr>
            <td><strong>Seed {r['seed']}</strong></td>
            <td>{p['gaokao_score']} → {p['university']}</td>
            <td>{p['family_background']} / {p['city_tier']}</td>
            <td>{f['净资产(万)']} 万</td>
            <td>{f['年收入(万)']} 万</td>
            <td>{f['事业等级']}</td>
            <td>{f['关系网密度']}</td>
            <td>{f['意义感']}</td>
            <td>{f['后悔指数']}</td>
            <td><a href="seed{r['seed']}/biography.html" target="_blank">📄 看传记</a></td>
        </tr>
        """)
    
    # 关键决策对比
    decision_compare = []
    for r in results:
        log = json.load(open(OUTPUT_DIR / f"seed{r['seed']}" / "log.json", encoding="utf-8"))
        decisions = log["decisions"]
        chosen_list = [f"Q{d['quarter']} {d['age']:.0f}岁: {d['chosen'][:20]}" for d in decisions[:8]]
        decision_compare.append(f"""
        <div class="card">
            <h3>Seed {r['seed']}: {r['person']['university']}</h3>
            <ol>{''.join(f'<li>{c}</li>' for c in chosen_list)}</ol>
        </div>
        """)
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多 Seed 人生对比</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", sans-serif;
         background: #0f1419; color: #e6e6e6; max-width: 1300px;
         margin: 0 auto; padding: 32px; line-height: 1.55; }}
  h1, h2, h3 {{ color: #f0c674; }}
  h1 {{ font-size: 28px; }}
  h2 {{ font-size: 22px; margin: 32px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #2d3e50; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2d3e50; }}
  th {{ background: #1a2332; color: #f0c674; }}
  tr:hover {{ background: #1a2332; }}
  .card {{ background: #1a2332; padding: 20px; border-radius: 10px;
           margin: 12px 0; }}
  ol li {{ margin: 4px 0; color: #b8c4d1; }}
  a {{ color: #f0c674; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .summary {{ background: linear-gradient(135deg, #1a2332, #15202b);
              padding: 24px; border-radius: 12px; margin: 16px 0; }}
  .insight {{ color: #82c4c3; font-style: italic; margin: 8px 0; }}
</style>
</head>
<body>
  <h1>🧬 多 Seed 人生沙盘对比</h1>
  <div class="summary">
    <p>共跑了 <strong>{len(results)}</strong> 个 seed（每个人设不同），全部跑完 18-30 岁。</p>
    <p>对比这些人生轨迹的差异：起点（高考分/家庭/城市）vs 终点（净资产/事业/意义感）。</p>
  </div>

  <h2>📊 终态对比表</h2>
  <table>
    <thead>
      <tr>
        <th>Seed</th>
        <th>起点 (高考 → 学校)</th>
        <th>家庭 / 城市</th>
        <th>净资产</th>
        <th>年收入</th>
        <th>事业等级</th>
        <th>关系网</th>
        <th>意义感</th>
        <th>后悔指数</th>
        <th>传记</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>

  <h2>🛣️ 关键决策路径对比</h2>
  <p class="insight">同样的事件，不同人设走出了不一样的路。</p>
  {''.join(decision_compare)}

  <h2>💡 观察</h2>
  <div class="card">
    <p>看几个 seed 的 30 岁终态，能发现：</p>
    <ul>
      <li><strong>高考分 + 家庭背景</strong>的组合，比单一因素更能预测终态。</li>
      <li><strong>关系网密度</strong>几乎所有 seed 都满了——说明 agent 群体决策倾向于"不孤立自己"。</li>
      <li><strong>后悔指数</strong>都接近 0——可能是 agent 群体倾向于"自圆其说"，值得改进。</li>
      <li>具体怎么走，<a href="seed{results[0]['seed']}/biography.html" target="_blank">看完整传记</a>。</li>
    </ul>
  </div>

</body>
</html>"""
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"✅ 对比报告: {output_path} ({len(html)/1024:.1f} KB)")


if __name__ == "__main__":
    asyncio.run(main())
