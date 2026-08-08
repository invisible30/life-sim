"""Life-curve chart generation.

Renders four subplot charts (net worth + income, health, career, lifestyle) into
a single dark-themed PNG, returned as a base64-encoded string so it can be
embedded directly in the HTML biography without writing any extra files.
"""
from __future__ import annotations

import base64
import io
import warnings
from typing import Any

import matplotlib

# Use the headless backend so this works in CI / containers without a display.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (import after backend switch)
from matplotlib import font_manager  # noqa: E402

# ---------------------------------------------------------------------------
# Style: dark background + soft palette. We try to register a CJK-capable
# font if one is available, otherwise fall back to whatever the system has.
# ---------------------------------------------------------------------------
_BG = "#0f1419"
_PANEL = "#1a2332"
_GRID = "#2d3e50"
_TEXT = "#e6e6e6"
_MUTED = "#8a9bae"
_ACCENT = "#f0c674"  # warm gold, matches the HTML theme

# Soft, distinguishable series colors on a dark canvas.
_PALETTE = ["#f0c674", "#82c4c3", "#e07a5f", "#9d8df1", "#7fb069", "#e07b91"]


def _configure_style() -> None:
    """Apply global dark-style settings. Safe to call multiple times."""
    plt.rcParams.update({
        "figure.facecolor": _BG,
        "axes.facecolor": _PANEL,
        "axes.edgecolor": _GRID,
        "axes.labelcolor": _TEXT,
        "axes.titlecolor": _ACCENT,
        "xtick.color": _MUTED,
        "ytick.color": _MUTED,
        "text.color": _TEXT,
        "grid.color": _GRID,
        "grid.linestyle": "--",
        "grid.alpha": 0.5,
        "font.family": _resolve_font_family(),
        "font.size": 10,
    })


def _resolve_font_family() -> list[str]:
    """Return a font-family list that can render Chinese if possible."""
    # Common CJK-capable fonts on macOS / Linux / Windows. We list the ones
    # we want first, then fall back to whatever DejaVu / sans-serif provides.
    preferred = [
        "PingFang SC",
        "Microsoft YaHei",
        "Source Han Sans CN",
        "Source Han Sans SC",
        "Noto Sans CJK SC",
        "Noto Sans CJK",
        "WenQuanYi Micro Hei",
        "Heiti SC",
        "Hiragino Sans GB",
        "SimHei",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    matched = [name for name in preferred if name in available]
    # Always end with a generic family so matplotlib has a last resort.
    return matched + ["DejaVu Sans", "sans-serif"]


def _series(metrics_history: list[dict[str, Any]], key: str) -> tuple[list[float], list[float]]:
    """Pull (x, y) for a given metric key. ``x`` is age, ``y`` is the value.

    Missing keys yield empty series instead of raising — the chart will simply
    skip them.
    """
    xs: list[float] = []
    ys: list[float] = []
    for snap in metrics_history:
        m = snap.get("metrics", {}) if isinstance(snap, dict) else {}
        if key in m:
            xs.append(float(snap.get("age", 0)))
            ys.append(float(m[key]))
    return xs, ys


def _label_for(metric_key: str) -> str:
    """Friendly Chinese axis label for a metric dict key."""
    return {
        "净资产(万)": "净资产 (万元)",
        "年收入(万)": "年收入 (万元)",
        "月现金流(万)": "月现金流 (万元)",
        "身体健康": "身体健康",
        "心理健康": "心理健康",
        "事业等级": "事业等级",
        "意义感": "意义感",
        "周自由小时": "周自由小时",
        "关系网密度": "关系网密度",
        "后悔指数": "后悔指数",
    }.get(metric_key, metric_key)


def _draw_dual_axis(ax, x_left, y_left, key_left, x_right, y_right, key_right) -> None:
    """Draw two related series on a shared x-axis (age) with twin y-axes."""
    color_left, color_right = _PALETTE[0], _PALETTE[1]

    line1, = ax.plot(
        x_left, y_left, color=color_left, linewidth=2.0, marker="o", markersize=3,
        label=_label_for(key_left),
    )
    ax.set_ylabel(_label_for(key_left), color=color_left)
    ax.tick_params(axis="y", labelcolor=color_left)
    ax.grid(True, alpha=0.3)

    ax2 = ax.twinx()
    ax2.set_facecolor(_PANEL)
    line2, = ax2.plot(
        x_right, y_right, color=color_right, linewidth=2.0, marker="s", markersize=3,
        linestyle="--", label=_label_for(key_right),
    )
    ax2.set_ylabel(_label_for(key_right), color=color_right)
    ax2.tick_params(axis="y", labelcolor=color_right)

    # One combined legend so users see both series.
    ax.legend(handles=[line1, line2], loc="upper left", framealpha=0.2,
              facecolor=_PANEL, edgecolor=_GRID, labelcolor=_TEXT, fontsize=9)


def _draw_dual_line(ax, x1, y1, key1, x2, y2, key2) -> None:
    """Draw two series on shared axes (0-100 friendly)."""
    color1, color2 = _PALETTE[0], _PALETTE[2]
    ax.plot(x1, y1, color=color1, linewidth=2.0, marker="o", markersize=3, label=_label_for(key1))
    ax.plot(x2, y2, color=color2, linewidth=2.0, marker="s", markersize=3, label=_label_for(key2))
    ax.set_ylim(0, 100)
    ax.legend(loc="best", framealpha=0.2, facecolor=_PANEL, edgecolor=_GRID,
              labelcolor=_TEXT, fontsize=9)
    ax.grid(True, alpha=0.3)


def build_charts(metrics_history: list[dict[str, Any]]) -> str:
    """Render the 4-chart life-curve panel as a base64-encoded PNG string.

    The returned string is the raw base64 payload (no ``data:image/png;base64,``
    prefix) so the HTML template can wrap it.
    """
    _configure_style()

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle("18 → 30 岁 人生曲线", fontsize=16, color=_ACCENT, fontweight="bold", y=0.98)

    # -- Chart 1: net worth + yearly income (dual axis) ---------------------
    x_nw, y_nw = _series(metrics_history, "净资产(万)")
    x_in, y_in = _series(metrics_history, "年收入(万)")
    _draw_dual_axis(axes[0, 0], x_nw, y_nw, "净资产(万)", x_in, y_in, "年收入(万)")
    axes[0, 0].set_title("💰 净资产 & 年收入", fontsize=12, pad=10)
    axes[0, 0].set_xlabel("年龄")

    # -- Chart 2: physical + mental health -----------------------------------
    x_ph, y_ph = _series(metrics_history, "身体健康")
    x_mh, y_mh = _series(metrics_history, "心理健康")
    _draw_dual_line(axes[0, 1], x_ph, y_ph, "身体健康", x_mh, y_mh, "心理健康")
    axes[0, 1].set_title("🏃 健康 (身体 & 心理)", fontsize=12, pad=10)
    axes[0, 1].set_xlabel("年龄")

    # -- Chart 3: career level + meaning score -------------------------------
    x_cl, y_cl = _series(metrics_history, "事业等级")
    x_ms, y_ms = _series(metrics_history, "意义感")
    _draw_dual_line(axes[1, 0], x_cl, y_cl, "事业等级", x_ms, y_ms, "意义感")
    axes[1, 0].set_title("🚀 事业 & 意义感", fontsize=12, pad=10)
    axes[1, 0].set_xlabel("年龄")

    # -- Chart 4: free hours/week + relationship density ---------------------
    x_fh, y_fh = _series(metrics_history, "周自由小时")
    x_rd, y_rd = _series(metrics_history, "关系网密度")
    _draw_dual_line(axes[1, 1], x_fh, y_fh, "周自由小时", x_rd, y_rd, "关系网密度")
    axes[1, 1].set_title("⏳ 自由时间 & 关系网", fontsize=12, pad=10)
    axes[1, 1].set_xlabel("年龄")

    # Some chart titles use emoji (💰🏃🚀⏳). Matplotlib will yell about missing
    # glyphs on systems without an emoji font, but the PNG still renders fine.
    # Silence locally so callers' logs stay clean.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        fig.tight_layout(rect=(0, 0, 1, 0.96))

        # Serialize to base64. PNG is the right format for lossless chart rendering.
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")
