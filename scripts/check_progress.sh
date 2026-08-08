#!/bin/bash
# Watchdog: 每 5 分钟检查 10-seed 跑的状态
cd /Users/lzc/.minimax-agent-cn/projects/life-sim
LOG=output/logs/watchdog.log
echo "=== $(date) ===" >> $LOG
ps -p $(pgrep -f multi_run.py 2>/dev/null) -o pid,etime 2>/dev/null | tail -1 >> $LOG
echo "  progress lines: $(wc -l < output/progress.log 2>/dev/null)" >> $LOG
echo "  seeds done: $(ls -d output/seed* 2>/dev/null | wc -l)" >> $LOG
tail -1 output/progress.log 2>/dev/null >> $LOG
echo "  last 2 master log lines:" >> $LOG
tail -2 output/logs/master_latest.log 2>/dev/null | head -2 >> $LOG
echo >> $LOG
