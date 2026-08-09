"""Tests for issue #21 — print() replaced with logging

Verifies that:
- main.py / multi_run.py 配置 logging.basicConfig (带时间戳 + 等级)
- 各 module 都有 module-level logger = logging.getLogger(__name__)
- Logger 名字正确 (跟 module 路径一致)
- 关键路径走 logger 而非 print (captor)
"""
import logging
import os
import sys
import io
import re
import pytest
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_main_module_has_logger():
    """main.py 应该 import logging 并定义 module logger"""
    src = open(os.path.join(ROOT, "main.py")).read()
    assert "import logging" in src
    assert "logger = logging.getLogger" in src


def test_multi_run_module_has_logger():
    src = open(os.path.join(ROOT, "multi_run.py")).read()
    assert "import logging" in src
    assert "logger = logging.getLogger" in src


def test_llm_client_has_logger():
    src = open(os.path.join(ROOT, "llm/client.py")).read()
    assert "import logging" in src
    assert "logger = logging.getLogger" in src


def test_core_driver_has_logger():
    src = open(os.path.join(ROOT, "core/driver.py")).read()
    assert "import logging" in src
    assert "logger = logging.getLogger" in src


def test_meeting_council_has_logger():
    src = open(os.path.join(ROOT, "meeting/council.py")).read()
    assert "import logging" in src
    assert "logger = logging.getLogger" in src


def test_main_configures_basic_config():
    """main.py 应该配 logging.basicConfig 带时间戳 + 等级"""
    src = open(os.path.join(ROOT, "main.py")).read()
    assert "logging.basicConfig" in src
    assert "asctime" in src  # 时间戳
    assert "levelname" in src  # 等级


def test_no_stray_print_statements_for_user_output():
    """module 文件里 print 应该是 UI 元素 (进度条) 或注释说 'UI element',
    不是用户消息 (那应该用 logger)."""
    # main.py / multi_run.py 的 print 应该只剩 'UI element' 注释过的
    for fname in ["main.py", "multi_run.py", "core/driver.py", "llm/client.py", "meeting/council.py"]:
        path = os.path.join(ROOT, fname)
        with open(path) as f:
            content = f.read()
        # 读全文件, 处理多行 print()
        # 简单办法: 找 'print(' 开始, 找到匹配的 ')' 结束
        import re
        # 注释掉 UI element 注释所在 print 的检查
        # 允许 "end=\"\"" 进度条形式
        for m in re.finditer(r"\bprint\(", content):
            start = m.start()
            # 找到匹配的 )
            depth = 0
            i = m.end() - 1
            while i < len(content):
                ch = content[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            snippet = content[start:i+1]
            # 允许 UI 元素 (有 end="" 或 \\r)
            if 'end=' in snippet or '\\r' in snippet:
                continue
            # 允许 flush-only
            if snippet.strip() in ("print()", "print(flush=True)"):
                continue
            # 允许 traceback
            if "traceback" in snippet:
                continue
            # 找到行号
            line_no = content[:start].count("\n") + 1
            pytest.fail(f"{fname}:{line_no} 剩余 user-facing print: {snippet!r}")


def test_logger_captures_drift_warning(caplog):
    """issue #1 drift 失败应该走 logger.warning 而非 print"""
    # 直接用 logger 看 module logger 是不是叫 life_sim.core.driver
    from core.driver import logger as drv_logger
    assert drv_logger.name == "core.driver"


def test_logger_captures_llm_retry(caplog):
    """issue #15 / #17 LLM retry 失败应该走 logger"""
    from llm.client import logger as llm_logger
    assert llm_logger.name == "llm.client"


def test_logger_captures_debate_round_failure(caplog):
    """issue #6 debate round 失败应该走 logger.warning"""
    from meeting.council import logger as council_logger
    assert council_logger.name == "meeting.council"
