"""多 seed 跑 + 对比报告

跑 N 个不同 seed（每个 seed 派生出不同人设），生成对比 HTML。
- 支持 resume：已经跑完的 seed 会跳过
- 单独的 per-seed LLM 预算（避免一个 seed 用光配额）
- 实时进度写到 progress.log
"""
from __future__ import annotations

import asyncio
import json
import os
import logging

logger = logging.getLogger(__name__)
import logging

logger = logging.getLogger(__name__)
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
from reporting.html_builder import build_html


ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"

# issue #21: 统一 logger, basicConfig 只在 main 跑时配一次
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# 10 个多样性 seed（覆盖不同人设分布）
SEEDS = [42, 7, 123, 256, 999, 314, 271, 88, 555, 1618]


async def run_one_seed(
    seed: int,
    cfg: dict,
    llm: LLMClient,
    randomize: bool = True,
    seed_dir: Path | None = None,
) -> dict:
    """跑一个 seed，返回摘要"""
    state = init_state_from_config(cfg, seed=seed, randomize_person=randomize)

    p = state.person
    logger.info("=" * 60)
    logger.info("🌱 Seed=%d: 高考%d → %s", seed, p.gaokao_score, p.university)
    logger.info("   家庭=%s 城市=%s 性别=%s", p.family_background, p.city_tier, p.gender)
    logger.info("   启动资金=%.1f万 父母期待=%s", p.initial_cash/10000, p.parents_expectation)
    logger.info("   人格 O=%.2f C=%.2f E=%.2f A=%.2f N=%.2f",
                p.personality.openness, p.personality.conscientiousness,
                p.personality.extraversion, p.personality.agreeableness,
                p.personality.neuroticism)
    logger.info("   LLM 已调用: %d/%d", llm.call_count, llm.cfg.max_total_calls)
    logger.info("=" * 60)
    
    world = World(state)
    debate_rounds = int(os.getenv("LIFE_MAX_DEBATE_ROUNDS", "1")) or \
                    cfg.get("output", {}).get("max_debate_rounds", 1)
    council = Council(
        llm=llm, state=state,
        enabled_agents=cfg.get("agents", {}),
        max_debate_rounds=debate_rounds,
        parallel=os.getenv("LIFE_PARALLEL_AGENTS", "true").lower() == "true",
    )
    driver = Driver(state, world, council)
    
    t0 = time.time()
    
    # 实时进度 watchdog
    last_decision_count = 0
    last_decision_title = ""
    
    async def watchdog():
        nonlocal last_decision_count, last_decision_title
        while True:
            await asyncio.sleep(15)
            n_dec = len(state.decisions)
            if n_dec > last_decision_count:
                last_decision_count = n_dec
                last_decision_title = state.decisions[-1].event_title[:25]
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
        print(flush=True)  # 进度条换行, 保留 print 因为是 UI element 不是 log
    
    elapsed = time.time() - t0
    
    final = state.metrics.as_dict()
    decisions_count = len(state.decisions)
    logger.info("✓ Seed=%d 完成: %d 决策, %.0fs, LLM累计 %d",
                seed, decisions_count, elapsed, llm.call_count)
    logger.info("  终态: 净资产%s万 年收入%s万 事业%s 心理%s 后悔%s",
                final['净资产(万)'], final['年收入(万)'],
                final['事业等级'], final['心理健康'], final['后悔指数'])
    
    # 写每个 seed 的产物
    if seed_dir is None:
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
    
    # 写单人传记（letter 失败也不影响主流程）
    try:
        from main import _generate_letter
        letter = await _generate_letter(state, llm)
    except Exception as e:
        logger.warning("Letter 生成失败: %s", e)
        letter = f"（信件生成失败：{e}）"

    try:
        html_path = seed_dir / "biography.html"
        build_html(
            state=state,
            decisions=state.decisions,
            llm_call_count=llm.call_count,
            letter_text=letter,
            output_path=str(html_path),
        )
    except Exception as e:
        logger.warning("HTML 生成失败: %s", e, exc_info=True)

    return {
        "seed": seed,
        "person": {
            "gaokao_score": state.person.gaokao_score,
            "university": state.person.university,
            "family_background": state.person.family_background,
            "city_tier": state.person.city_tier,
            "gender": state.person.gender,
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


def is_seed_complete(seed: int, output_dir: Path) -> bool:
    """检查 seed 是否已经跑完（log.json + biography.html 都存在）"""
    seed_dir = output_dir / f"seed{seed}"
    if not (seed_dir / "log.json").exists():
        return False
    if not (seed_dir / "biography.html").exists():
        return False
    # 验证 log.json 完整（>10 个决策）
    try:
        log = json.load(open(seed_dir / "log.json", encoding="utf-8"))
        return len(log.get("decisions", [])) >= 10
    except Exception:
        return False


async def main():
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    
    # 实时进度日志
    progress_log = OUTPUT_DIR / "progress.log"
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    if progress_log.exists():
        progress_log.unlink()
    os.environ["LIFE_PROGRESS_LOG"] = str(progress_log)
    logger.info("📊 实时进度: tail -f %s", progress_log)

    # 一个 LLM 客户端，复用配额
    llm = LLMClient()
    logger.info("🤖 LLM: %s, 上限 %d 调用", llm.cfg.model, llm.cfg.max_total_calls)
    logger.info("   重试: %d 次 (指数退避)", llm.cfg.max_retries)

    # 跑 N 个 seed
    requested = int(os.getenv("LIFE_NUM_SEEDS", "10"))
    seeds_to_run = SEEDS[:requested]

    # 跳过已经跑完的（resume 支持）
    seeds_pending = [s for s in seeds_to_run if not is_seed_complete(s, OUTPUT_DIR)]
    seeds_done = [s for s in seeds_to_run if is_seed_complete(s, OUTPUT_DIR)]
    logger.info("🌍 计划跑 %d 个 seed", len(seeds_to_run))
    if seeds_done:
        logger.info("   ✅ 已完成 (resume skip): %s", seeds_done)
    logger.info("   🆕 待跑: %s", seeds_pending)
    
    results = []
    # 把已完成的也加进 results（从 log.json 提取）
    for seed in seeds_done:
        log = json.load(open(OUTPUT_DIR / f"seed{seed}" / "log.json", encoding="utf-8"))
        p = log["person"]
        f = log["final_metrics"]
        # 兼容老格式 log：缺字段用 get 兜底
        results.append({
            "seed": seed,
            "person": {
                "gaokao_score": p.get("gaokao_score", "?"),
                "university": p.get("university", "?"),
                "family_background": p.get("family_background", "?"),
                "city_tier": p.get("city_tier", "?"),
                "gender": p.get("gender", "?"),
            },
            "elapsed_sec": log.get("elapsed_sec", 0),
            "decisions": len(log.get("decisions", [])),
            "final": f,
        })
    
    # 真正跑剩下的
    per_seed_min_calls = int(os.getenv("LIFE_PER_SEED_MIN_CALLS", "200"))
    for seed in seeds_pending:
        if llm.remaining_calls < per_seed_min_calls:
            logger.warning("LLM 剩余调用不足 (%d < %d), 跳过剩余 seed",
                           llm.remaining_calls, per_seed_min_calls)
            break
        try:
            result = await run_one_seed(seed, cfg, llm, randomize=True)
            results.append(result)
        except Exception as e:
            logger.error("Seed=%d 失败: %s: %s", seed, type(e).__name__, e, exc_info=True)
            continue

    # 按 seed 顺序排序
    results.sort(key=lambda r: r["seed"])

    # 写对比报告
    compare_path = OUTPUT_DIR / "compare.html"
    build_compare_html(results, compare_path)
    logger.info("📊 对比报告: %s", compare_path)
    logger.info("💾 LLM 累计调用: %d", llm.call_count)
    logger.info("🏁 完。共 %d/%d 个 seed 成功。", len(results), len(seeds_to_run))


def build_compare_html(results: list[dict], output_path: Path) -> None:
    """生成多 seed 对比 HTML（v2 — 暗色 + Chart.js + 分数卡）"""
    if not results:
        logger.warning("无结果, 跳过对比报告")
        return
    
    rows = []
    for r in results:
        p = r["person"]
        f = r["final"]
        # 解析 flags
        seed_log_path = OUTPUT_DIR / f"seed{r['seed']}" / "log.json"
        flags_str = ""
        if seed_log_path.exists():
            try:
                log = json.load(open(seed_log_path, encoding="utf-8"))
                # 收集所有 flag（用最后一条 metric_history 里的）
                if log.get("metrics_history"):
                    flags_str = ", ".join(log["metrics_history"][-1].get("flags", []))
            except Exception:
                pass
        
        # 评分
        score = (
            f.get("净资产(万)", 0) * 0.5
            + f.get("年收入(万)", 0) * 2
            + f.get("事业等级", 0)
            + f.get("心理健康", 0)
            + f.get("关系网密度", 0) * 0.5
            + f.get("意义感", 0)
            - f.get("后悔指数", 0)
        )
        
        rows.append(f"""
        <tr data-score="{score:.1f}">
            <td><strong>Seed {r['seed']}</strong></td>
            <td>{p['gaokao_score']} → {p['university']}</td>
            <td>{p['family_background']} / {p['city_tier']}</td>
            <td>{f['净资产(万)']} 万</td>
            <td>{f['年收入(万)']} 万</td>
            <td>{f['事业等级']}</td>
            <td>{f['关系网密度']}</td>
            <td>{f['心理健康']}</td>
            <td>{f['意义感']}</td>
            <td>{f['后悔指数']}</td>
            <td><strong style="color: #f0c674;">{score:.0f}</strong></td>
            <td><a href="seed{r['seed']}/biography.html" target="_blank">📄 看传记</a></td>
        </tr>
        """)
    
    # 关键决策对比
    decision_compare = []
    for r in results:
        log_path = OUTPUT_DIR / f"seed{r['seed']}" / "log.json"
        if not log_path.exists():
            continue
        log = json.load(open(log_path, encoding="utf-8"))
        decisions = log.get("decisions", [])
        chosen_list = [f"Q{d['quarter']} {d['age']:.0f}岁: {d['chosen'][:25]}" for d in decisions[:10]]
        decision_compare.append(f"""
        <div class="card">
            <h3>Seed {r['seed']}: {r['person']['university']}</h3>
            <p class="meta">家庭={r['person']['family_background']} · 城市={r['person']['city_tier']} · 
               {r['decisions']} 决策 · {r['elapsed_sec']:.0f}s</p>
            <ol>{''.join(f'<li>{c}</li>' for c in chosen_list)}</ol>
        </div>
        """)
    
    # 准备 chart data：每个 seed 14 项指标
    chart_labels = list(results[0]["final"].keys())  # 14 个指标
    chart_datasets = []
    palette = [
        "#f0c674", "#82c4c3", "#c594c5", "#94e2d5", "#fab387",
        "#a6e3a1", "#f38ba8", "#89b4fa", "#f9e2af", "#b4befe",
    ]
    for i, r in enumerate(results):
        vals = list(r["final"].values())
        chart_datasets.append({
            "label": f"Seed {r['seed']}",
            "data": vals,
            "borderColor": palette[i % len(palette)],
            "backgroundColor": palette[i % len(palette)] + "20",
            "borderWidth": 2,
            "pointRadius": 3,
        })
    
    # 分布图数据：14 指标的 mean / std / min / max
    import statistics
    metric_stats = {}
    for label in chart_labels:
        vals = [r["final"][label] for r in results]
        metric_stats[label] = {
            "min": min(vals),
            "max": max(vals),
            "mean": statistics.mean(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0,
        }
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多 Seed 人生对比 — {len(results)} 个版本</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0f1419; color: #e6e6e6; max-width: 1500px;
         margin: 0 auto; padding: 32px; line-height: 1.55; }}
  h1 {{ color: #f0c674; font-size: 32px; margin: 0 0 8px; }}
  h2 {{ color: #f0c674; font-size: 22px; margin: 40px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #2d3e50; }}
  h3 {{ color: #82c4c3; font-size: 16px; margin: 12px 0; }}
  .subtitle {{ color: #6c7a89; font-size: 14px; margin: 0 0 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2d3e50; }}
  th {{ background: #1a2332; color: #f0c674; font-weight: 600; }}
  tr:hover {{ background: #1a2332; }}
  .card {{ background: #1a2332; padding: 20px; border-radius: 10px;
           margin: 12px 0; border-left: 3px solid #f0c674; }}
  .meta {{ color: #6c7a89; font-size: 12px; margin: 4px 0 12px; }}
  ol li {{ margin: 4px 0; color: #b8c4d1; }}
  a {{ color: #f0c674; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .summary {{ background: linear-gradient(135deg, #1a2332, #15202b);
              padding: 24px; border-radius: 12px; margin: 16px 0;
              border: 1px solid #2d3e50; }}
  .insight {{ color: #82c4c3; font-style: italic; margin: 8px 0; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
  .chart-box {{ background: #1a2332; padding: 16px; border-radius: 10px; min-height: 320px; }}
  .chart-box-wide {{ grid-column: span 2; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 12px 0; }}
  .stat-cell {{ background: #1a2332; padding: 8px; border-radius: 6px; text-align: center; }}
  .stat-num {{ font-size: 18px; font-weight: bold; color: #f0c674; }}
  .stat-lbl {{ font-size: 11px; color: #6c7a89; margin-top: 2px; }}
</style>
</head>
<body>
  <h1>🧬 多 Seed 人生沙盘对比</h1>
  <p class="subtitle">共 {len(results)} 个不同人设的人生轨迹（18-30 岁），来自同一个人格模型在 10 个不同 seed 下的推演</p>

  <div class="summary">
    <div class="stat-grid">
      <div class="stat-cell"><div class="stat-num">{len(results)}</div><div class="stat-lbl">Seed 数量</div></div>
      <div class="stat-cell"><div class="stat-num">{sum(r['decisions'] for r in results)}</div><div class="stat-lbl">总决策数</div></div>
      <div class="stat-cell"><div class="stat-num">{sum(r['elapsed_sec'] for r in results):.0f}s</div><div class="stat-lbl">总耗时</div></div>
      <div class="stat-cell"><div class="stat-num">{min(r['final']['净资产(万)'] for r in results):.0f}-{max(r['final']['净资产(万)'] for r in results):.0f}</div><div class="stat-lbl">净资产区间 (万)</div></div>
      <div class="stat-cell"><div class="stat-num">{min(r['final']['后悔指数'] for r in results):.0f}-{max(r['final']['后悔指数'] for r in results):.0f}</div><div class="stat-lbl">后悔指数区间</div></div>
    </div>
    <p class="insight">观察不同人设、不同决策路径在 12 年后的终态差异——同样的世界模型，不同的"我"。</p>
  </div>

  <h2>📈 14 项指标分布（雷达 / 折线）</h2>
  <div class="charts">
    <div class="chart-box chart-box-wide"><canvas id="radarChart"></canvas></div>
    <div class="chart-box chart-box-wide"><canvas id="lineChart"></canvas></div>
    <div class="chart-box chart-box-wide"><canvas id="stdevChart"></canvas></div>
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
        <th>事业</th>
        <th>关系网</th>
        <th>心理</th>
        <th>意义感</th>
        <th>后悔</th>
        <th>综合分</th>
        <th>传记</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>

  <h2>🛣️ 关键决策路径对比</h2>
  <p class="insight">同样的事件，不同人设走出了不一样的路。前 10 个决策一览：</p>
  {''.join(decision_compare)}

  <h2>💡 观察</h2>
  <div class="card">
    <p>看 {len(results)} 个 seed 的 30 岁终态，能发现：</p>
    <ul>
      <li><strong>起点 (高考分/家庭) 决定下限</strong>，但 <strong>过程选择决定上限</strong>。</li>
      <li><strong>事业等级</strong> 和 <strong>年收入</strong> 相关性高，但 <strong>意义感</strong> 独立于物质——有的 seed 钱多但意义感低。</li>
      <li><strong>后悔指数</strong> 取决于"未选之路"的心理负担，agent 群体决策会一定程度平抑极端后悔。</li>
      <li>想看具体故事，<a href="seed{results[0]['seed']}/biography.html" target="_blank">点开任一传记</a>。</li>
    </ul>
  </div>

<script>
const LABELS = {json.dumps(chart_labels, ensure_ascii=False)};
const DATASETS = {json.dumps(chart_datasets, ensure_ascii=False)};

// 雷达图
new Chart(document.getElementById('radarChart'), {{
  type: 'radar',
  data: {{ labels: LABELS, datasets: DATASETS }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: '14 指标雷达图（每个 seed 一条）', color: '#f0c674' }},
      legend: {{ labels: {{ color: '#b8c4d1' }} }}
    }},
    scales: {{
      r: {{
        angleLines: {{ color: '#2d3e50' }},
        grid: {{ color: '#2d3e50' }},
        pointLabels: {{ color: '#b8c4d1', font: {{ size: 11 }} }},
        ticks: {{ color: '#6c7a89', backdropColor: 'transparent' }},
      }}
    }}
  }}
}});

// 折线图
new Chart(document.getElementById('lineChart'), {{
  type: 'line',
  data: {{ labels: LABELS, datasets: DATASETS }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: '14 指标折线图（同样每个 seed 一条）', color: '#f0c674' }},
      legend: {{ labels: {{ color: '#b8c4d1' }} }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#b8c4d1', maxRotation: 45, minRotation: 45 }}, grid: {{ color: '#2d3e50' }} }},
      y: {{ ticks: {{ color: '#6c7a89' }}, grid: {{ color: '#2d3e50' }} }}
    }}
  }}
}});

// 标准差柱状图（哪些指标差异最大）
const STATS = {json.dumps([{
  "label": k,
  "min": v["min"],
  "max": v["max"],
  "mean": v["mean"],
  "stdev": v["stdev"],
} for k, v in metric_stats.items()], ensure_ascii=False)};

new Chart(document.getElementById('stdevChart'), {{
  type: 'bar',
  data: {{
    labels: STATS.map(s => s.label),
    datasets: [
      {{
        label: '均值',
        data: STATS.map(s => s.mean.toFixed(1)),
        backgroundColor: '#82c4c3',
      }}, {{
        label: '标准差',
        data: STATS.map(s => s.stdev.toFixed(1)),
        backgroundColor: '#f0c674',
      }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      title: {{ display: true, text: '14 指标的均值与标准差（标准差越大 = seed 间差异越大）', color: '#f0c674' }},
      legend: {{ labels: {{ color: '#b8c4d1' }} }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#b8c4d1', maxRotation: 45, minRotation: 45 }}, grid: {{ color: '#2d3e50' }} }},
      y: {{ ticks: {{ color: '#6c7a89' }}, grid: {{ color: '#2d3e50' }} }}
    }}
  }}
}});
</script>

</body>
</html>"""
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    logger.info("✅ 对比报告: %s (%.1f KB)", output_path, len(html)/1024)


if __name__ == "__main__":
    asyncio.run(main())
