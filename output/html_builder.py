"""HTML biography renderer.

Glues the chart PNG and the Jinja2 template into a single self-contained
``output/<seed>_biography.html`` file.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from output.chart_builder import build_charts

# Template lives next to this module.
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _as_dict(obj: Any) -> dict[str, Any]:
    """Normalize a dataclass-or-plain-object into a dict for Jinja.

    DecisionRecord and the fake ``D`` test class are both supported, so we
    duck-type: prefer ``asdict`` for dataclasses, fall back to ``__dict__``
    (the test class sets attributes directly, so this is enough).
    """
    if obj is None:
        return {}
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def _extract_person(state: Any) -> dict[str, Any]:
    """Return the ``person`` dict expected by the template.

    Tolerates a missing ``state`` (the smoke test passes ``None``).
    """
    if state is None:
        # Reasonable defaults so the template still renders something readable.
        return {
            "name": "我",
            "university": "未知",
            "family_background": "middle",
            "gaokao_score": "—",
        }
    person = getattr(state, "person", None)
    if person is None:
        return {
            "name": "我",
            "university": "未知",
            "family_background": "middle",
            "gaokao_score": "—",
        }
    return {
        "name": getattr(person, "name", "我"),
        "university": getattr(person, "university", "") or "未知",
        "family_background": getattr(person, "family_background", "middle"),
        "gaokao_score": getattr(person, "gaokao_score", "—"),
    }


def _extract_final_metrics(state: Any) -> dict[str, Any]:
    """Return the 30岁 snapshot of the seven metrics for the hero grid."""
    if state is None:
        return {}
    metrics = getattr(state, "metrics", None)
    if metrics is None:
        return {}
    # Prefer an explicit ``as_dict`` so the keys are localized to Chinese.
    if hasattr(metrics, "as_dict"):
        return metrics.as_dict()
    # Last-resort fallback for test doubles.
    return {k: getattr(metrics, k, 0) for k in (
        "net_worth", "cash_flow_monthly", "physical_health", "mental_health",
        "relationship_density", "career_level", "career_income_yearly",
        "free_hours_weekly", "meaning_score", "regret_index",
    )}


def _normalize_decisions(decisions: list[Any]) -> list[dict[str, Any]]:
    """Convert each decision into the dict shape the template iterates over."""
    out: list[dict[str, Any]] = []
    for d in decisions or []:
        if d is None:
            continue
        data = _as_dict(d)
        # Ensure every template field is present so the template never explodes
        # on a malformed record.
        out.append({
            "quarter": data.get("quarter", 0),
            "age": data.get("age", 0),
            "event_title": data.get("event_title", ""),
            "event_description": data.get("event_description", ""),
            "event_type": data.get("event_type", ""),
            "options": data.get("options", []) or [],
            "chosen": data.get("chosen", ""),
            "votes": data.get("votes", {}) or {},
            "debates": data.get("debates", []) or [],
            "reasoning": data.get("reasoning", ""),
            "outcome": data.get("outcome", ""),
        })
    return out


def build_html(
    state: Any,
    decisions: list[Any],
    llm_call_count: int,
    letter_text: str,
    output_path: str | Path,
) -> None:
    """Render the full biography HTML and write it to ``output_path``.

    Parameters
    ----------
    state
        A ``LifeState`` (or anything with ``.person`` and ``.metrics``). May be
        ``None`` for early smoke tests; the template will still render with
        sensible defaults.
    decisions
        Iterable of ``DecisionRecord``-like objects.
    llm_call_count
        Number of LLM calls made across the run, shown in the hero.
    letter_text
        The "30岁的我给18岁的我" letter body, already a string.
    output_path
        Destination file. Parent directories are created if missing.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metrics_history = getattr(state, "metrics_history", None) or []
    chart_b64 = build_charts(metrics_history) if metrics_history else ""

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("biography.html.j2")

    html = template.render(
        person=_extract_person(state),
        decisions=_normalize_decisions(decisions),
        llm_calls=llm_call_count,
        final_metrics=_extract_final_metrics(state),
        chart_b64=chart_b64,
        letter=letter_text or "",
    )

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(f"✅ 传记已生成: {output_path} ({size_kb:.1f} KB, {len(decisions or [])} 次决策)")
