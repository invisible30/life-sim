"""Tests for Agent._parse_vote — fuzzy 4-tier fallback (issue #2)

Each test corresponds to a failure mode called out in the issue body.
"""
import sys
import os
import pytest

# allow `import agents.base` when running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import Agent


class _StubAgent(Agent):
    """Minimal concrete agent for testing the abstract base."""
    name = "stub"
    voice = "测试"
    emoji = "🧪"

    def persona_prompt(self) -> str:
        return "test"


@pytest.fixture
def agent():
    # _parse_vote does not call llm or state, so None is safe
    return _StubAgent(llm=None, state=None)


OPTIONS = [
    "加入 1-2 个感兴趣的社团，活跃社交",
    "专注学业，争取保研",
    "做点小生意 / 接外包练手",
    "跟室友抱团，深度经营小圈子",
]


def test_strict_match_chinese_colon(agent):
    """Tier 1: 严格正则匹配 中文冒号 + 字母"""
    content = "我决定选社团方向。\n选项：B\n强度：支持\n理由：保研对我最有利。"
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    assert option == OPTIONS[1]
    assert fb is False  # 严格命中 = 不算 fallback


def test_strict_match_english_colon(agent):
    """Tier 1: 严格路径只支持中文 "选项" 关键词; 英文走 tier 2 (decision-verb)"""
    content = "选 A. Strongly agree."
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    assert option == OPTIONS[0]
    # 这是 tier 2 (decision verb) 命中 — fallback=True
    assert fb is True


def test_letter_scan_isolated_a(agent):
    """Tier 2: 孤立字母匹配 — '我选 A' 风格"""
    content = "我比较倾向 A 这个方向, 因为它能扩展人脉。"
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    assert option == OPTIONS[0]
    assert fb is True  # 字母级算 fallback


def test_letter_scan_does_not_match_inside_word(agent):
    """Tier 2: 不会误匹配 'API' / 'B 站' 这种"""
    content = "看了下 API 文档和 B 站教程, 还是不确定选什么。"
    # 应该 fall through 到 tier 3 或 4
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    # tier 3 抓不到 (没有完整选项文本), 走 tier 4 默认
    assert option == OPTIONS[0]
    assert fb is True


def test_option_text_substring_chinese(agent):
    """Tier 3: 选项文本子串匹配 — 真实 LLM 经常不写 '选项: A' 而是直接说选项内容"""
    content = "我觉得做点小生意 / 接外包练手这条路最适合我。"
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    assert option == OPTIONS[2]
    assert fb is True


def test_default_when_nothing_matches(agent):
    """Tier 4: 全部失败 -> 默认 options[0] + parse_fallback=True"""
    content = "今天天气真好，完全没在想选什么。"
    option, w, r, fb = agent._parse_vote(content, OPTIONS)
    assert option == OPTIONS[0]
    assert fb is True


def test_strength_parsing(agent):
    """强度关键词解析"""
    s1 = agent._parse_strength("强度：强烈支持")
    s2 = agent._parse_strength("强烈反对这个方案")
    s3 = agent._parse_strength("中立")
    s4 = agent._parse_strength("I have no strong opinion.")
    assert s1 == 2
    assert s2 == -2
    assert s3 == 0
    assert s4 == 0


def test_parse_vote_returns_4_tuple(agent):
    """API contract: 4-tuple not 3-tuple (regression catch)"""
    result = agent._parse_vote("选项: A", OPTIONS)
    assert len(result) == 4
    option, w, reasoning, parse_fallback = result
    assert isinstance(parse_fallback, bool)


def test_empty_content_uses_default(agent):
    """空内容 -> 默认 + parse_fallback"""
    option, w, r, fb = agent._parse_vote("", OPTIONS)
    assert option == OPTIONS[0]
    assert fb is True


def test_empty_options_returns_empty(agent):
    """空选项列表 -> 不崩"""
    option, w, r, fb = agent._parse_vote("选项: A", [])
    assert option == ""
    assert fb is True


def test_recovery_rate_4_of_5(agent):
    """Acceptance criterion from issue #2: 4/5 known-bad outputs recover"""
    bad_inputs = [
        ("我选 C。", OPTIONS[2]),                  # letter scan -> 命中
        ("专注学业，争取保研就对了。", OPTIONS[1]), # text scan -> 命中
        ("做点小生意 / 接外包练手。", OPTIONS[2]),   # text scan -> 命中
        ("综合考虑，应该是 A 这个最稳。", OPTIONS[0]), # letter scan -> 命中
        ("这是一个完全无关的句子没有选项。", OPTIONS[0]), # 完全不匹配 -> 默认
    ]
    correct = 0
    for content, expected in bad_inputs:
        got, _, _, _ = agent._parse_vote(content, OPTIONS)
        if got == expected:
            correct += 1
    assert correct >= 4, f"只恢复了 {correct}/5, 期望 >= 4"
