"""Life Sim — 入口

跑完 18-30 岁的人生沙盘，生成 HTML 报告 + JSON 日志。
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

# 让 python 能从项目根 import
sys.path.insert(0, str(Path(__file__).parent))

from core.state import init_state_from_config, LifeState
from core.world import World
from core.driver import Driver
from meeting.council import Council
from llm.client import LLMClient
from output.html_builder import build_html


ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def main():
    load_dotenv(ROOT / ".env")
    
    # 实时进度日志路径（tail -f 可看）
    progress_log = ROOT / "output" / "progress.log"
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    if progress_log.exists():
        progress_log.unlink()
    os.environ["LIFE_PROGRESS_LOG"] = str(progress_log)
    print(f"📊 实时进度: tail -f {progress_log}")
    
    print("=" * 70)
    print("🧬 Life Sim — Multi-Agent 人生沙盘 (18-30)")
    print("=" * 70)
    
    cfg = load_config()
    seed = int(os.getenv("LIFE_SEED", "42"))
    
    # 1. 初始化状态
    state = init_state_from_config(cfg, seed=seed)
    print(f"\n👤 主角：{state.person.gender}, {state.person.family_background}家庭, "
          f"高考 {state.person.gaokao_score} → {state.person.university}")
    print(f"💰 启动资金: {state.person.initial_cash/10000:.1f}万 | "
          f"父母期待: {state.person.parents_expectation}")
    
    # 2. 初始化 LLM
    llm = LLMClient()
    print(f"\n🤖 LLM: {llm.cfg.provider} / {llm.cfg.model} @ {llm.cfg.base_url}")
    print(f"   调用上限: {llm.cfg.max_total_calls}")
    
    # 3. 世界 + 董事会
    world = World(state)
    council = Council(
        llm=llm,
        state=state,
        enabled_agents=cfg.get("agents", {}),
        max_debate_rounds=cfg.get("output", {}).get("max_debate_rounds", 2),
        parallel=os.getenv("LIFE_PARALLEL_AGENTS", "true").lower() == "true",
    )
    print(f"👥 Agent: {[a.name for a in council.agents]}")
    
    # 4. 跑
    driver = Driver(state, world, council)
    print(f"\n⏱️  开始跑 18→30 (48 季度)...")
    t0 = time.time()
    
    try:
        await driver.run(quiet=False)
    except KeyboardInterrupt:
        print("\n⏸️  中断")
    
    elapsed = time.time() - t0
    n_decisions = len(state.decisions)
    print(f"\n✓ 跑完 {n_decisions} 个决策, {elapsed:.1f}s, LLM 调用 {llm.call_count} 次")
    
    # 5. 生成终态评估
    final = state.metrics.as_dict()
    print(f"\n📊 30岁终态:")
    for k, v in final.items():
        print(f"   {k}: {v}")
    
    # 6. 写 JSON 日志
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"seed{seed}_log.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "seed": seed,
            "elapsed_sec": elapsed,
            "llm_calls": llm.call_count,
            "person": {
                "gaokao_score": state.person.gaokao_score,
                "university": state.person.university,
                "family_background": state.person.family_background,
            },
            "final_metrics": final,
            "metrics_history": state.metrics_history,
            "decisions": [_decision_to_dict(d) for d in state.decisions],
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 日志: {log_path}")
    
    # 7. 生成 HTML 报告
    letter = await _generate_letter(state, llm)
    html_path = OUTPUT_DIR / f"seed{seed}_biography.html"
    try:
        build_html(
            state=state,
            decisions=state.decisions,
            llm_call_count=llm.call_count,
            letter_text=letter,
            output_path=str(html_path),
        )
        print(f"📄 报告: {html_path}")
    except Exception as e:
        print(f"⚠️  HTML 生成失败: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n{'=' * 70}")
    print(f"🏁 完。打开 {html_path} 看完整传记。")


def _decision_to_dict(d) -> dict:
    return {
        "id": d.id,
        "quarter": d.quarter,
        "age": d.age,
        "event_title": d.event_title,
        "event_description": d.event_description,
        "event_type": d.event_type,
        "options": d.options,
        "chosen": d.chosen,
        "votes": d.votes,
        "debates": d.debates,
        "reasoning": d.reasoning,
        "outcome": d.outcome,
    }


async def _generate_letter(state: LifeState, llm: LLMClient) -> str:
    """让 LLM 写一封 30岁的我给18岁的我的信"""
    m = state.metrics
    summary = (
        f"我30岁了。净资产 {m.net_worth:.0f} 万，年收入 {m.career_income_yearly:.0f} 万，"
        f"事业等级 {m.career_level:.0f}，身体 {m.physical_health:.0f}，心理 {m.mental_health:.0f}，"
        f"关系网 {m.relationship_density:.0f}，意义感 {m.meaning_score:.0f}，"
        f"后悔指数 {m.regret_index:.0f}。\n\n"
        f"我做过的几个关键选择：\n"
    )
    for d in state.decisions[:5]:
        summary += f"- {d.age:.0f}岁：「{d.event_title}」选了「{d.chosen}」→ {d.outcome}\n"
    
    system = """你是 30 岁的"我"，正坐在凌晨的书桌前，给 18 岁的自己写一封信。
你要：
- 真实、有温度、不鸡汤
- 引用一些具体的人生后悔学和真实研究
- 短一点（300-500 字）
- 不教训人，像在跟自己聊天
- 中文，写得自然一点，不要"亲爱的自己"这种开头"""

    user = f"30 岁的你，现在的状态：\n{summary}\n\n请写一封信给 18 岁的自己。"
    return await llm.chat(system, user, temperature=0.9, max_tokens=1500)


if __name__ == "__main__":
    asyncio.run(main())
