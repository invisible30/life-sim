"""HTML biography renderer (v2).

Renders a single self-contained ``output/<seed>_biography.html`` file that
shows a polished, interactive "life memoir" of a simulated 18 → 30 year-old
journey.

Compared to v1, v2 drops the embedded matplotlib PNG and instead powers all
charts with Chart.js loaded from a CDN. The python side is now responsible
for:

* extracting the final scorecard with good / mid / bad color coding,
* parsing the per-decision "投票统计" block so the template can show the
  accumulated weighted scores per option,
* computing per-agent aggregates (express count, avg vote weight, total
  weighted influence, recommendation-pick rate, regret accuracy) for the
  radar chart at the bottom of the page,
* computing a few human-readable "personality pills" (school tier, family
  cash, family style, personality) shown in the hero.

The Jinja2 template (``output/templates/biography.html.j2``) is responsible
for the visual layer and the Chart.js wiring. It receives all data as JSON
strings so the JS in the template can render client-side charts.

The matplotlib chart builder (``output/chart_builder.py``) is kept around for
backwards compatibility (it is still re-exported via ``output/__init__``) but
is no longer called from here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Template lives next to this module.
TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------

# Display name, emoji and accent color for each of the 8 agents. The keys
# match ``DecisionRecord.votes`` keys. ``luck`` is excluded from the radar
# because its behavior is random and not a function of the agent's "voice".
AGENT_META: dict[str, dict[str, str]] = {
    "rational":   {"role": "理性我",     "emoji": "🧠", "color": "#5b8def"},
    "emotional":  {"role": "感性我",     "emoji": "💔", "color": "#ef6b9c"},
    "ambitious":  {"role": "野心版的我", "emoji": "🔥", "color": "#ff7b39"},
    "realistic":  {"role": "现实版的我", "emoji": "🪨", "color": "#8e8e93"},
    "family":     {"role": "家人期待",   "emoji": "👨‍👩‍👧", "color": "#b07cff"},
    "future_me":  {"role": "未来的我",   "emoji": "🕰️", "color": "#5ec5d6"},
    "body":       {"role": "身体",       "emoji": "🏃", "color": "#82c474"},
    "luck":       {"role": "运气",       "emoji": "🎲", "color": "#f0c674"},
}
# Stable display order — used both for the chip row and the radar chart.
AGENT_ORDER: tuple[str, ...] = (
    "rational", "emotional", "ambitious", "realistic",
    "family", "future_me", "body", "luck",
)
# 7 self-agents shown on the radar (no luck).
RADAR_AGENTS: tuple[str, ...] = (
    "rational", "emotional", "ambitious", "realistic",
    "family", "future_me", "body",
)

# Event type → icon + accent color. Used for the colored type badge on each
# decision card.
EVENT_TYPE_META: dict[str, dict[str, str]] = {
    "milestone":  {"icon": "🏁", "label": "里程碑", "color": "#5b8def"},
    "opportunity": {"icon": "✨", "label": "机会",   "color": "#f0c674"},
    "crisis":     {"icon": "⚠️", "label": "危机",   "color": "#ef4444"},
    "crossroads": {"icon": "🔀", "label": "岔路口", "color": "#b07cff"},
}

# Vote chip CSS class for a given numeric weight.
_VOTE_CHIP_CLASS = {
    2:  "support-strong",
    1:  "support",
    0:  "neutral",
    -1: "oppose",
    -2: "oppose-strong",
}
_VOTE_CHIP_LABEL = {
    2: "++", 1: "+", 0: "0", -1: "−", -2: "−−",
}


# ---------------------------------------------------------------------------
# Generic dict helpers
# ---------------------------------------------------------------------------

def _as_dict(obj: Any) -> dict[str, Any]:
    """Normalize a dataclass-or-plain-object into a dict for downstream code.

    DecisionRecord is a dataclass; the test fake (``D`` in the verification
    snippet) is a plain object with attributes set directly. Both paths are
    supported so the renderer stays tolerant.
    """
    if obj is None:
        return {}
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    """Lookup ``obj.<a>.<b>.<c>`` defensively, returning ``default`` on miss.

    Used so that, e.g., a missing ``Person.personality`` on a fake state
    doesn't break the trait pipeline.
    """
    cur: Any = obj
    for key in path:
        if cur is None:
            return default
        cur = getattr(cur, key, None)
        if cur is None:
            return default
    return cur


# ---------------------------------------------------------------------------
# Person / metrics extraction
# ---------------------------------------------------------------------------

def _extract_person(state: Any) -> dict[str, Any]:
    """Return the ``person`` dict expected by the template.

    Tolerates a missing ``state`` (the smoke test passes ``None``) and a fake
    state with a bare ``type('p', (), {...})`` person object.
    """
    if state is None:
        return {
            "name": "我", "university": "未知",
            "family_background": "middle", "gaokao_score": "—",
        }
    person = getattr(state, "person", None)
    if person is None:
        return {
            "name": "我", "university": "未知",
            "family_background": "middle", "gaokao_score": "—",
        }
    return {
        "name": getattr(person, "name", "我"),
        "university": getattr(person, "university", "") or "未知",
        "family_background": getattr(person, "family_background", "middle"),
        "gaokao_score": getattr(person, "gaokao_score", "—"),
    }


def _extract_final_metrics(state: Any) -> dict[str, Any]:
    """Return the 30岁 snapshot keyed by Chinese metric name.

    Falls back to ``vars(metrics)`` so the FakeState test (which uses Chinese
    keys directly) keeps working. The FakeState test attaches its data to
    the *class* (via ``type('m', (), dict)()``), so we walk the class dict
    too in case the instance dict is empty.
    """
    if state is None:
        return {}
    metrics = getattr(state, "metrics", None)
    if metrics is None:
        return {}
    if hasattr(metrics, "as_dict"):
        try:
            return metrics.as_dict()
        except Exception:
            pass
    out: dict[str, Any] = {}
    if hasattr(metrics, "__dict__"):
        out.update(vars(metrics))
    if not out and hasattr(metrics, "__class__"):
        # Class-level attributes (FakeState attaches the dict to the class).
        out.update({
            k: v for k, v in vars(type(metrics)).items()
            if not k.startswith("_") and not callable(v)
        })
    return out


# ---------------------------------------------------------------------------
# Decision normalization + reasoning parsing
# ---------------------------------------------------------------------------

# ``✅ 去，提前接触行业: 6.40``  → option=``去，提前接触行业``, score=6.40
# Lines without a check mark also count (they're just other options).
_OPTION_SCORE_RE = re.compile(
    r"^[✅\s]+([^\n]+?):\s*(-?[\d.]+)\s*$", re.MULTILINE,
)


def _parse_option_scores(reasoning: str) -> dict[str, float]:
    """Pull the ``投票统计`` block out of the reasoning string.

    Returns a dict mapping each option (verbatim, with surrounding whitespace
    stripped) to its accumulated weighted score. Returns ``{}`` if the block
    can't be found.
    """
    if not reasoning:
        return {}
    out: dict[str, float] = {}
    for opt, score in _OPTION_SCORE_RE.findall(reasoning):
        try:
            out[opt.strip()] = float(score)
        except ValueError:
            continue
    return out


def _outcome_sign(outcome: str) -> int:
    """Map a Chinese outcome string to -1 / 0 / +1.

    Used for the "regret accuracy" axis on the radar. We only care about the
    dominant direction: "↑" wins, "↓" loses, "平稳" or anything unknown is a
    wash.
    """
    if not outcome:
        return 0
    if "↓" in outcome or "降" in outcome or "差" in outcome or "亏" in outcome:
        return -1
    if "↑" in outcome or "增" in outcome or "好" in outcome or "赢" in outcome:
        return 1
    return 0


def _normalize_decision(d: Any) -> dict[str, Any]:
    """Convert one decision into the dict shape the template iterates over.

    Also performs the per-decision derived computations:

    * ``option_scores`` — accumulated weighted scores per option
      (parsed from the ``投票统计`` block).
    * ``top_options``   — top-2 options by score, with the chosen option
      always included even if it's not in the top 2.
    * ``vote_chips``    — list of ``{agent, role, emoji, color, weight, cls, label}``
      ready for the chip row.
    * ``vote_bars``     — same data, with bar widths pre-computed (0-100%
      of the max possible |weight| = 2).
    * ``luck_note``     — short string when luck had a non-zero vote, e.g.
      ``"🎲 运气 +: 押 '去，提前接触行业'"``. ``None`` otherwise.
    """
    data = _as_dict(d)
    options = data.get("options") or []
    chosen = data.get("chosen") or ""
    votes: dict[str, int] = data.get("votes") or {}
    debates: list[dict[str, Any]] = data.get("debates") or []
    outcome = data.get("outcome") or ""

    option_scores = _parse_option_scores(data.get("reasoning") or "")

    # Top-2 options by score. If ``chosen`` is not in the top 2, append it.
    ranked = sorted(option_scores.items(), key=lambda kv: kv[1], reverse=True)
    top = list(ranked[:2])
    seen = {o for o, _ in top}
    if chosen and chosen in option_scores and chosen not in seen:
        top.append((chosen, option_scores[chosen]))
    top_options = [{"option": o, "score": s} for o, s in top]

    # Per-agent chips and bars.
    vote_chips: list[dict[str, Any]] = []
    vote_bars: list[dict[str, Any]] = []
    for agent_key in AGENT_ORDER:
        meta = AGENT_META.get(agent_key, {})
        weight = int(votes.get(agent_key, 0) or 0)
        vote_chips.append({
            "agent": agent_key,
            "role": meta.get("role", agent_key),
            "emoji": meta.get("emoji", ""),
            "color": meta.get("color", "#94a3b8"),
            "weight": weight,
            "cls": _VOTE_CHIP_CLASS.get(weight, "neutral"),
            "label": _VOTE_CHIP_LABEL.get(weight, "?"),
        })
        # Bar length is |weight| / 2 * 50% so it fits in the half-axis
        # (track is centered, left:50% means right half spans 50%-100%).
        bar_pct = min(50.0, abs(weight) / 2.0 * 50.0)
        vote_bars.append({
            "agent": agent_key,
            "role": meta.get("role", agent_key),
            "emoji": meta.get("emoji", ""),
            "color": meta.get("color", "#94a3b8"),
            "weight": weight,
            "pct": bar_pct,
            "side": "right" if weight > 0 else ("left" if weight < 0 else "center"),
        })

    # Luck note: only show if luck actually voted.
    luck_note: str | None = None
    luck_weight = int(votes.get("luck", 0) or 0)
    if luck_weight != 0 and option_scores:
        # The luck agent contributes its vote to the option that ends up
        # chosen in the synthesized vote-count block, so we surface that.
        # We don't know exactly which option luck pushed, so we surface the
        # chosen option as a stand-in (luck usually tips the final tie).
        sign = "+" if luck_weight > 0 else "−"
        luck_note = f"🎲 运气 {sign}: 影响了最终选择"

    return {
        "quarter": data.get("quarter", 0),
        "age": data.get("age", 0),
        "event_title": data.get("event_title", ""),
        "event_description": data.get("event_description", ""),
        "event_type": data.get("event_type", ""),
        "options": options,
        "chosen": chosen,
        "votes": votes,
        "debates": debates,
        "reasoning": data.get("reasoning", ""),
        "outcome": outcome,
        "option_scores": option_scores,
        "top_options": top_options,
        "vote_chips": vote_chips,
        "vote_bars": vote_bars,
        "luck_note": luck_note,
    }


def _normalize_decisions(decisions: list[Any]) -> list[dict[str, Any]]:
    """Convert each decision into the normalized dict shape."""
    return [_normalize_decision(d) for d in (decisions or []) if d is not None]


# ---------------------------------------------------------------------------
# Person traits (hero pills)
# ---------------------------------------------------------------------------

# Map gaokao score → school-tier label, mirroring ``Person.derive_school``.
def _school_tier(score: Any) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return ""
    if s >= 680: return "清北复交"
    if s >= 640: return "985 头部"
    if s >= 600: return "985 中等 / 211 头部"
    if s >= 560: return "211 / 双一流"
    if s >= 500: return "普通一本"
    if s >= 430: return "二本"
    return "专科 / 民办"


# Map family background → 启动资金 label.
_FAMILY_CASH_LABEL = {
    "upper":   "20万启动",
    "middle":  "8万启动",
    "working": "3万启动",
    "rural":   "8千启动",
}

# Map family expectation → 中文标签.
_FAMILY_STYLE_LABEL = {
    "achieve": "achieve型家庭",
    "stable":  "稳定型家庭",
    "happy":   "快乐型家庭",
    "free":    "自由型家庭",
}


def _personality_label(person: Any) -> str:
    """Derive a 4-char Chinese personality label from the Big-5 traits.

    Falls back to "内向但友善" if the personality object isn't available
    (e.g. the test fake state). The mapping is intentionally simple: high
    extraversion → 外向, low → 内向; high agreeableness → 友善, low → 锋利;
    high conscientiousness → 自律, low → 随性; high openness → 好奇,
    low → 务实; high neuroticism → 敏感, low → 沉稳.
    """
    pers = _get(person, "personality")
    if pers is None:
        # Default that reads plausibly for the test case.
        return "内向但友善"
    try:
        E = float(getattr(pers, "extraversion", 0.5))
        A = float(getattr(pers, "agreeableness", 0.5))
        C = float(getattr(pers, "conscientiousness", 0.5))
        O = float(getattr(pers, "openness", 0.5))
        N = float(getattr(pers, "neuroticism", 0.5))
    except (TypeError, ValueError):
        return "内向但友善"

    extro = "外向" if E >= 0.5 else "内向"
    if A >= 0.6 and C >= 0.6:   tail = "但自律"
    elif A >= 0.6:              tail = "但友善"
    elif N >= 0.6:              tail = "但敏感"
    elif O >= 0.6:              tail = "但好奇"
    elif C < 0.4:               tail = "且随性"
    else:                       tail = "且务实"
    return f"{extro}{tail}"


def _compute_traits(person: Any) -> list[dict[str, str]]:
    """Return the 3-5 hero pills, each with a label and a tone.

    The tone is consumed by the template to color the pill (positive /
    neutral / accent). Missing data is silently skipped so the bar is
    never ugly.
    """
    pills: list[dict[str, str]] = []

    tier = _school_tier(getattr(person, "gaokao_score", None))
    if tier:
        # 985 / 211 / 清北 is "positive", everything else "neutral".
        tone = "positive" if tier.startswith(("985", "211", "清北")) else "neutral"
        pills.append({"label": tier, "tone": tone})

    fam = getattr(person, "family_background", None)
    cash_label = _FAMILY_CASH_LABEL.get(fam)
    if cash_label:
        # More starting cash = "positive" tone.
        tone = {"20万启动": "positive", "8万启动": "neutral"}.get(cash_label, "muted")
        pills.append({"label": cash_label, "tone": tone})

    style_label = _FAMILY_STYLE_LABEL.get(getattr(person, "parents_expectation", None) or "")
    if style_label:
        pills.append({"label": style_label, "tone": "accent"})

    p_label = _personality_label(person)
    if p_label:
        pills.append({"label": p_label, "tone": "muted"})

    # City tier, if present.
    city = getattr(person, "city_tier", None)
    if city:
        city_label = {
            "tier1":    "一线城市",
            "new_tier1": "新一线",
            "tier2":     "二线城市",
            "small":     "小城市",
        }.get(city)
        if city_label:
            tone = "positive" if city in ("tier1", "new_tier1") else "neutral"
            pills.append({"label": city_label, "tone": tone})

    return pills


# ---------------------------------------------------------------------------
# Final scorecard (8 metric cards)
# ---------------------------------------------------------------------------

# Each entry: (chinese key, label, unit, format, good_threshold, mid_threshold,
#              higher_is_better).
# ``higher_is_better=False`` flips the color logic for metrics like
# 后悔指数 where a lower number is healthier.
_SCORECARD_SPEC: list[dict[str, Any]] = [
    {"key": "净资产(万)",  "label": "净资产",     "unit": "万",  "fmt": "{:.1f}",
     "good": 50, "mid": 10, "higher_is_better": True,  "icon": "💰"},
    {"key": "事业等级",    "label": "事业等级",   "unit": "",   "fmt": "{:.0f}",
     "good": 60, "mid": 30, "higher_is_better": True,  "icon": "🚀"},
    {"key": "关系网密度",  "label": "关系网",     "unit": "",   "fmt": "{:.0f}",
     "good": 70, "mid": 40, "higher_is_better": True,  "icon": "🤝"},
    {"key": "身体健康",    "label": "身体健康",   "unit": "",   "fmt": "{:.0f}",
     "good": 70, "mid": 50, "higher_is_better": True,  "icon": "🏃"},
    {"key": "心理健康",    "label": "心理健康",   "unit": "",   "fmt": "{:.0f}",
     "good": 70, "mid": 50, "higher_is_better": True,  "icon": "🧘"},
    {"key": "周自由小时",  "label": "自由时间",   "unit": "h/w","fmt": "{:.0f}",
     "good": 30, "mid": 15, "higher_is_better": True,  "icon": "⏳"},
    {"key": "意义感",      "label": "意义感",     "unit": "",   "fmt": "{:.0f}",
     "good": 70, "mid": 45, "higher_is_better": True,  "icon": "✨"},
    {"key": "后悔指数",    "label": "后悔指数",   "unit": "",   "fmt": "{:.0f}",
     "good": 5,  "mid": 15, "higher_is_better": False, "icon": "🪦"},
]


def _color_for(spec: dict[str, Any], value: float) -> str:
    """Return ``good`` / ``mid`` / ``bad`` for a metric value."""
    good = spec["good"]
    mid = spec["mid"]
    if spec["higher_is_better"]:
        if value >= good: return "good"
        if value >= mid:  return "mid"
        return "bad"
    else:
        if value <= good: return "good"
        if value <= mid:  return "mid"
        return "bad"


def _compute_scorecard(final_metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the 8 scorecard cards consumed by the template."""
    out: list[dict[str, Any]] = []
    for spec in _SCORECARD_SPEC:
        raw = final_metrics.get(spec["key"])
        if raw is None:
            # Skip metrics that weren't computed this run; we don't want an
            # ugly "—" card if the metric isn't even tracked.
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        out.append({
            "key":   spec["key"],
            "label": spec["label"],
            "unit":  spec["unit"],
            "value": value,
            "display": spec["fmt"].format(value),
            "color": _color_for(spec, value),
            "icon":  spec["icon"],
        })
    return out


# ---------------------------------------------------------------------------
# Per-agent aggregates (for the radar chart + small textual summary)
# ---------------------------------------------------------------------------

def _compute_agent_stats(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-agent behavior across all decisions.

    Returns:
        {
          "per_agent": {agent_key: {express_count, avg_weight,
                                     total_influence, pick_rate, accuracy}},
          "radar":     {agent_key: {axis_key: 0-100, ...}, ...},
          "axes":      [axis display names, in render order],
        }

    * ``express_count``  — number of debate rounds that included this agent.
    * ``avg_weight``     — mean |vote| across decisions (0-2 scale).
    * ``total_influence``— sum of |vote * option_score|; a rough measure of
                            how much weight this agent's votes carried.
    * ``pick_rate``      — fraction of decisions where this agent's voted
                            option was the one ultimately chosen
                            (0-1, only counting decisions where they voted != 0).
    * ``accuracy``       — fraction of decisions where their vote direction
                            (sign) matched the eventual outcome sign.

    The radar normalizes every axis to 0-100 by dividing by the max across
    all 7 self-agents, so the chart is self-comparing.
    """
    # Initialize tallies.
    tallies: dict[str, dict[str, float]] = {
        a: {"express": 0, "weight_sum": 0, "weight_n": 0,
            "influence": 0.0, "pick_hit": 0, "pick_n": 0,
            "acc_hit": 0, "acc_n": 0}
        for a in RADAR_AGENTS
    }

    for d in decisions:
        debates = d.get("debates") or []
        votes = d.get("votes") or {}
        chosen = d.get("chosen") or ""
        outcome_sign = _outcome_sign(d.get("outcome") or "")
        option_scores = d.get("option_scores") or {}

        # Express count: how many debate entries came from this agent.
        for db in debates:
            ag = db.get("agent") if isinstance(db, dict) else None
            if ag in tallies:
                tallies[ag]["express"] += 1

        # Per-agent vote aggregates.
        for ag in RADAR_AGENTS:
            w = int(votes.get(ag, 0) or 0)
            if w == 0:
                continue
            tallies[ag]["weight_sum"] += abs(w)
            tallies[ag]["weight_n"] += 1
            # Influence: |weight * top option score this agent effectively pushed|.
            # We don't know which option they voted for, so we use the
            # *maximum-scoring* option as a stand-in; this gives a reasonable
            # proxy for "how much weight did this agent throw around".
            if option_scores:
                top_score = max(option_scores.values())
                tallies[ag]["influence"] += abs(w) * top_score
            # Pick rate.
            tallies[ag]["pick_n"] += 1
            # We can't tell which option the agent voted for directly, but
            # we know the *sign* of their vote. If they voted positive, the
            # chosen option is "supported" (proxy: chosen's score > 0);
            # if negative, chosen's score < 0. This is a coarse proxy but
            # stable across runs.
            chosen_score = option_scores.get(chosen, 0.0)
            if (w > 0 and chosen_score > 0) or (w < 0 and chosen_score < 0):
                tallies[ag]["pick_hit"] += 1
            # Accuracy: did their vote sign match the outcome sign?
            tallies[ag]["acc_n"] += 1
            if (w > 0 and outcome_sign > 0) or (w < 0 and outcome_sign < 0):
                tallies[ag]["acc_hit"] += 1

    # Convert tallies to per-agent summary.
    per_agent: dict[str, dict[str, float]] = {}
    for ag, t in tallies.items():
        per_agent[ag] = {
            "express_count":  int(t["express"]),
            "avg_weight":     (t["weight_sum"] / t["weight_n"]) if t["weight_n"] else 0.0,
            "total_influence": t["influence"],
            "pick_rate":      (t["pick_hit"] / t["pick_n"]) if t["pick_n"] else 0.0,
            "accuracy":       (t["acc_hit"] / t["acc_n"]) if t["acc_n"] else 0.0,
        }

    # Build the radar: each axis normalized to 0-100 by max across agents.
    axes = ["表达次数", "平均投票强度", "累计影响力", "建议被采纳率", "后悔准确度"]
    raw_by_axis: dict[str, dict[str, float]] = {a: {} for a in axes}
    for ag, s in per_agent.items():
        raw_by_axis["表达次数"][ag] = s["express_count"]
        raw_by_axis["平均投票强度"][ag] = s["avg_weight"]  # 0-2
        raw_by_axis["累计影响力"][ag] = s["total_influence"]
        raw_by_axis["建议被采纳率"][ag] = s["pick_rate"] * 100.0
        raw_by_axis["后悔准确度"][ag] = s["accuracy"] * 100.0

    radar: dict[str, list[float]] = {}
    for ag in RADAR_AGENTS:
        meta = AGENT_META.get(ag, {})
        radar[ag] = []
        for axis in axes:
            vals = raw_by_axis[axis]
            v = vals.get(ag, 0.0)
            m = max(vals.values()) if vals else 0.0
            radar[ag].append(round((v / m * 100.0) if m > 0 else 0.0, 1))
        # Decorate so the template can render labels/colors directly.
        radar[ag] = {  # type: ignore[assignment]
            "agent": ag,
            "role": meta.get("role", ag),
            "emoji": meta.get("emoji", ""),
            "color": meta.get("color", "#94a3b8"),
            "values": radar[ag],
        }

    return {
        "per_agent": per_agent,
        "radar": [radar[a] for a in RADAR_AGENTS],
        "axes": axes,
    }


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _compute_duration_min(decisions: list[dict[str, Any]]) -> float | None:
    """Wall-clock minutes between the first and last decision timestamp.

    Returns ``None`` when timestamps are missing or all the same.
    """
    if not decisions:
        return None
    ts = [d.get("timestamp") for d in decisions if d.get("timestamp")]
    if len(ts) < 2:
        return None
    delta = max(ts) - min(ts)
    if delta <= 0:
        return None
    return round(delta / 60.0, 1)


def _event_type_meta(t: str) -> dict[str, str]:
    """Return ``{icon, label, color}`` for an event type, with safe defaults."""
    return EVENT_TYPE_META.get(
        t or "", {"icon": "•", "label": t or "未分类", "color": "#94a3b8"},
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def build_html(
    state: Any,
    decisions: list[Any],
    llm_call_count: int,
    letter_text: str,
    output_path: str | Path,
) -> None:
    """Render the full v2 biography HTML and write it to ``output_path``.

    Parameters
    ----------
    state
        A ``LifeState`` (or anything with ``.person``, ``.metrics`` and
        ``.metrics_history``). May be ``None`` for early smoke tests; the
        template will still render with sensible defaults.
    decisions
        Iterable of ``DecisionRecord``-like objects.
    llm_call_count
        Number of LLM calls made across the run, shown in the hero stat strip.
    letter_text
        The "30岁的我给18岁的我" letter body, already a string.
    output_path
        Destination file. Parent directories are created if missing.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    person_dict = _extract_person(state)
    final_metrics = _extract_final_metrics(state)
    metrics_history = list(getattr(state, "metrics_history", None) or [])
    norm_decisions = _normalize_decisions(decisions)

    # Per-decision decoration (event type → icon/color), used by the template.
    for d in norm_decisions:
        meta = _event_type_meta(d["event_type"])
        d["event_icon"] = meta["icon"]
        d["event_type_label"] = meta["label"]
        d["event_type_color"] = meta["color"]

    scorecard = _compute_scorecard(final_metrics)
    agent_stats = _compute_agent_stats(norm_decisions)
    traits = _compute_traits(_get(state, "person"))
    duration_min = _compute_duration_min(norm_decisions)

    # Build a small client-side JSON payload. The template wraps each in
    # ``{{ ... | safe }}`` so double-encoding with ``json.dumps`` is correct
    # and the inlined values are valid JS literals.
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("biography.html.j2")

    html = template.render(
        person=person_dict,
        traits=traits,
        decisions=norm_decisions,
        decision_count=len(norm_decisions),
        llm_calls=llm_call_count,
        final_metrics=final_metrics,
        scorecard=scorecard,
        letter=letter_text or "",
        duration_min=duration_min,
        # JSON payloads for the client-side charts.
        metrics_history_json=json.dumps(metrics_history, ensure_ascii=False),
        decisions_json=json.dumps(norm_decisions, ensure_ascii=False),
        final_scorecard_json=json.dumps(scorecard, ensure_ascii=False),
        agent_stats_json=json.dumps(agent_stats, ensure_ascii=False),
        agent_meta_json=json.dumps(AGENT_META, ensure_ascii=False),
    )

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(
        f"✅ 传记已生成(v2): {output_path} "
        f"({size_kb:.1f} KB, {len(norm_decisions)} 次决策)"
    )
