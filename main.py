"""Life Sim — 入口

跑完 18-30 岁的人生沙盘，生成 HTML 报告 + JSON 日志。
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
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
from reporting.html_builder import build_html


ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"


# issue #21: 统一用 logger, 替换 print. basicConfig 只在 main 跑时配一次.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("life_sim.main")


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
    logger.info("实时进度: tail -f %s", progress_log)
    
    logger.info("=" * 70)
    logger.info("🧬 Life Sim — Multi-Agent 人生沙盘 (18-30)")
    logger.info("=" * 70)
    
    cfg = load_config()
    seed = int(os.getenv("LIFE_SEED", "42"))
    
    # 1. 初始化状态
    state = init_state_from_config(cfg, seed=seed)
    logger.info("👤 主角: %s, %s家庭, 高考 %d → %s",
                state.person.gender, state.person.family_background,
                state.person.gaokao_score, state.person.university)
    logger.info("💰 启动资金: %.1f万 | 父母期待: %s",
                state.person.initial_cash/10000, state.person.parents_expectation)
    
    # 2. 初始化 LLM
    llm = LLMClient()
    logger.info("🤖 LLM: %s / %s @ %s", llm.cfg.provider, llm.cfg.model, llm.cfg.base_url)
    logger.info("   调用上限: %d", llm.cfg.max_total_calls)
    
    # 3. 世界 + 董事会
    world = World(state)
    debate_rounds = int(os.getenv("LIFE_MAX_DEBATE_ROUNDS", "0")) or \
                    cfg.get("output", {}).get("max_debate_rounds", 2)
    council = Council(
        llm=llm,
        state=state,
        enabled_agents=cfg.get("agents", {}),
        max_debate_rounds=debate_rounds,
        parallel=os.getenv("LIFE_PARALLEL_AGENTS", "true").lower() == "true",
    )
    logger.info("   辩论轮数: %d", debate_rounds)
    logger.info("👥 Agent: %s", [a.name for a in council.agents])
    
    # 4. 跑
    end_quarter = int(os.getenv("LIFE_END_QUARTER", "0")) or None
    if end_quarter is None:
        end_quarter = (int(cfg["simulation"]["end_age"]) - int(cfg["simulation"]["start_age"])) * 4
    driver = Driver(state, world, council, end_quarter=end_quarter)
    logger.info("⏱️  开始跑 18→%s (%d 季度)...", cfg['simulation']['end_age'], end_quarter)
    t0 = time.time()
    
    try:
        await driver.run(quiet=False)
    except KeyboardInterrupt:
        logger.warning("⏸️  中断")
    
    elapsed = time.time() - t0
    n_decisions = len(state.decisions)
    logger.info("✓ 跑完 %d 个决策, %.1fs, LLM 调用 %d 次", n_decisions, elapsed, llm.call_count)
    
    # 5. 生成终态评估
    final = state.metrics.as_dict()
    logger.info("📊 30岁终态:")
    for k, v in final.items():
        logger.info("   %s: %s", k, v)
    
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
    logger.info("💾 日志: %s", log_path)
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
        logger.info("📄 报告: %s", html_path)
    except Exception as e:
        logger.error("⚠️  HTML 生成失败: %s", e, exc_info=True)

    logger.info("=" * 70)
    logger.info("🏁 完。打开 %s 看完整传记。", html_path)


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
    raw = await llm.chat(system, user, temperature=0.9, max_tokens=1500)
    # 剥掉模型泄露的 <think>...</think> 推理段
    import re as _re
    return _re.sub(r"<think>.*?</think>\s*", "", raw, flags=_re.DOTALL).strip()


if __name__ == "__main__":
    asyncio.run(main())
