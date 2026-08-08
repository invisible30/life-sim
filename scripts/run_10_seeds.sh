#!/bin/bash
# Master runner: 跑 10 个 seeds，写 master log，支持 resume
# 设计为 nohup 后台执行
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# 加载 .env（但用更高优先级覆盖关键变量）
set -a
source .env
set +a

# 关键配置：给 10 个 seed 足够的 LLM 预算
# 注意：必须在 source .env 之后，确保我们的设置生效
export LIFE_MAX_LLM_CALLS=${LIFE_MAX_LLM_CALLS_OVERRIDE:-12000}
export LIFE_TEMPERATURE=${LIFE_TEMPERATURE_OVERRIDE:-0.85}
export LIFE_PARALLEL_AGENTS=${LIFE_PARALLEL_AGENTS_OVERRIDE:-true}
export LIFE_MAX_DEBATE_ROUNDS=${LIFE_MAX_DEBATE_ROUNDS_OVERRIDE:-1}
export LIFE_NUM_SEEDS=${LIFE_NUM_SEEDS_OVERRIDE:-10}
export LIFE_MAX_CONCURRENT_LLM=${LIFE_MAX_CONCURRENT_LLM_OVERRIDE:-7}

# 输出目录
mkdir -p output/logs
MASTER_LOG=output/logs/master_$(date +%Y%m%d_%H%M%S).log
SYMLINK=output/logs/master_latest.log

echo "🧬 Life Sim 10-Seed Runner" | tee -a "$MASTER_LOG"
echo "  Start: $(date)" | tee -a "$MASTER_LOG"
echo "  LIFE_MAX_LLM_CALLS=$LIFE_MAX_LLM_CALLS" | tee -a "$MASTER_LOG"
echo "  LIFE_NUM_SEEDS=$LIFE_NUM_SEEDS" | tee -a "$MASTER_LOG"
echo "  LIFE_MAX_DEBATE_ROUNDS=$LIFE_MAX_DEBATE_ROUNDS" | tee -a "$MASTER_LOG"
echo "  log: $MASTER_LOG" | tee -a "$MASTER_LOG"
echo "==========================================" | tee -a "$MASTER_LOG"

# 启动
python -u multi_run.py 2>&1 | tee -a "$MASTER_LOG"
EXIT_CODE=${PIPESTATUS[0]}

echo "==========================================" | tee -a "$MASTER_LOG"
echo "🏁 End: $(date) (exit=$EXIT_CODE)" | tee -a "$MASTER_LOG"

# 软链接到 latest
ln -sf "$(basename $MASTER_LOG)" "$SYMLINK"

exit $EXIT_CODE
