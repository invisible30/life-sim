"""Event description sanitizer (issue #20)

data/events.json is user-editable. The `description` field flows into the
LLM prompt via _build_user_prompt(). Without sanitization, a malicious or
careless edit could inject prompt-injection attacks.

This module:
- Strips common LLM control tokens (<|im_start|>, <|im_end|>, <|eot_id|>, etc.)
- Truncates descriptions > 500 chars
- Replaces known injection phrases with [REDACTED]
- Removes zero-width / invisible Unicode characters

It does NOT try to be a complete prompt-injection firewall. The goal is to
block the obvious low-effort cases. High-effort attacks against the LLM
itself are out of scope.
"""
from __future__ import annotations

import re
from typing import Iterable


# 常见 LLM 控制 token (OpenAI, Anthropic, Llama 风格)
# 用 plain string 不是 raw — raw string 里 \| 是字面 2 字符, 不是我们想要的
_LLM_TOKENS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|im_sep|>",
    "<|eot_id|>",
    "<|endoftext|>",
    "<|padding|>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "</s>",
    "<s>",
]

# 已知注入模式 (case-insensitive). 命中整段替换为 [REDACTED].
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?the\s+above",
    r"disregard\s+(all\s+)?(prior|previous)\s+(instructions|prompts?)",
    r"forget\s+(everything|all)\s+(above|before)",
    r"新的?指示[：:]\s*忽略",          # 中文: "新指示: 忽略"
    r"忽略.{0,15}(指令|指示|规则|要求|命令)",  # 中文: 忽略...指令 (容许中间夹词)
    r"system\s*prompt\s*[:：]",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+(if\s+you\s+are\s+)?a\s+different",
]

# Zero-width / invisible chars that attackers use to smuggle instructions
_INVISIBLE_CHARS = [
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\ufeff",  # BOM / zero-width no-break space
]

MAX_DESCRIPTION_LEN = 500  # chars


def sanitize_description(text: str, max_len: int = MAX_DESCRIPTION_LEN) -> str:
    """Clean a single event description.

    Pipeline (in order):
    1. Strip LLM control tokens
    2. Replace known injection phrases with [REDACTED]
    3. Remove zero-width chars
    4. Collapse whitespace
    5. Truncate to max_len chars
    """
    if not text:
        return ""

    out = text

    # 1) 控制 token
    for tok in _LLM_TOKENS:
        out = re.sub(re.escape(tok), "", out, flags=re.IGNORECASE)

    # 2) 注入模式
    for pat in _INJECTION_PATTERNS:
        out = re.sub(pat, "[REDACTED]", out, flags=re.IGNORECASE)

    # 3) 零宽字符
    for ch in _INVISIBLE_CHARS:
        out = out.replace(ch, "")

    # 4) 折叠空白 (多个空白 -> 一个)
    out = re.sub(r"\s+", " ", out).strip()

    # 5) 截断 (保留 max_len 总长度, 留 3 字符给省略号)
    if len(out) > max_len:
        out = out[:max_len - 3].rstrip() + "..."

    return out


def sanitize_options(options: Iterable[str], max_len_each: int = 200) -> list[str]:
    """Sanitize the option list of an event.

    Each option is shorter than description, so smaller max_len. The injection
    patterns in sanitize_description also catch the obvious cases here.
    """
    return [sanitize_description(opt, max_len=max_len_each) for opt in options]


def sanitize_event(event: dict) -> dict:
    """Sanitize a full event dict in-place-ish. Returns a new dict.

    Only the user-controlled string fields (description, options) are touched.
    The id, type, title, trigger_age etc. are assumed internal/safe.
    """
    if not isinstance(event, dict):
        return event
    out = dict(event)
    if "description" in out:
        out["description"] = sanitize_description(out["description"])
    if "options" in out and isinstance(out["options"], list):
        out["options"] = sanitize_options(out["options"])
    return out
