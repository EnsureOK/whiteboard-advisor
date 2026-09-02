"""图表层：确定性选图 + 纯 Python 内联 SVG 渲染。

改造自 MIT 套件 insurance-business-operations 的问数引擎(指标/维度注册表已内嵌化)。

红线：
- **不可算（None）永不画成 0**：柱图该项留空并标注「不可算」，折线在该点断开。
  把「没数据」画成 0 会直接制造错误的经营结论，这是不可接受的失真。
- 颜色不是唯一区分手段：每个数据点都带数值标注，图注说明项数与最大值。
- 零外链、零第三方绘图依赖：内联 SVG 不依赖 CDN、不依赖系统中文字体
  （放弃 matplotlib 的核心原因是 Linux 缺中文字体会渲染成方框）。
- 选图确定性：同一个 Spec + 同一份结果永远得到同一张图，不含随机与时间因素。
- 图表只是数据表的视觉摘要，任何图都必须与数据表同时出现（由渲染层保证）。
"""
from __future__ import annotations

import json
import math
import os
from html import escape as _escape

# ---- 内嵌指标/维度注册表 ----------------------------------------------------
# 替代套件的指标/维度模块(摘除 pandas 传递依赖)。每个指标必须注册
# label/fmt/higher_is_better:未注册指标会以 FMT_INT 回落,比率类会被
# 渲染成 0/1 并诱发 donut 误选(套件源码探查确认的静默坑)。
from dataclasses import dataclass
from typing import Optional as _Opt

FMT_INT = "int"
FMT_PCT = "pct"
FMT_DAYS = "days"
FMT_TEXT = "text"
FMT_MONEY = "money"

KIND_FIELD = "field"
KIND_EXPLODE = "explode"
KIND_TIME = "time"


def format_by_fmt(fmt, value, *, na_text="当前数据不足以计算"):
    """唯一的数值格式化入口(图表层与渲染层共用)。"""
    if value is None or (isinstance(value, float) and value != value):
        return na_text
    if fmt == FMT_PCT:
        return f"{value * 100:.2f}%"
    if fmt == FMT_DAYS:
        return f"{value:.2f}"
    if fmt == FMT_MONEY:
        s = f"{value:,.2f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    if fmt == FMT_INT:
        return f"{int(round(value))}"
    return str(value)


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    fmt: str = FMT_INT
    higher_is_better: _Opt[bool] = None
    caveat: str = ""


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    field: str
    kind: str = KIND_FIELD


REPORT_METRICS = {m.id: m for m in [
    Metric("amount_wan", "保额(万元)", FMT_MONEY),
    Metric("premium_yuan", "年缴保费(元)", FMT_MONEY),
    Metric("current_wan", "已有保额(万元)", FMT_MONEY),
    Metric("need_wan", "建议保额(万元)", FMT_MONEY),
    Metric("gap_wan", "缺口(万元)", FMT_MONEY, higher_is_better=False),
    Metric("count", "件数", FMT_INT),
]}

REPORT_DIMENSIONS = {d.id: d for d in [
    Dimension("member", "成员", "member"),
    Dimension("line", "险种", "line"),
    Dimension("dimension", "保障维度", "dimension"),
    Dimension("year", "年份", "year", KIND_TIME),
]}


def get_metric(metric_id):
    return REPORT_METRICS.get(metric_id)


def get_dimension(dim_id):
    return REPORT_DIMENSIONS.get(dim_id)
# ---------------------------------------------------------------------------

CHART_TYPES = ("bar", "bar_h", "line", "grouped_bar", "stacked_bar",
               "donut", "heatmap", "bullet", "none")

# 选图阈值。全部为图形排版阈值，不含任何业务/监管语义。
MAX_VERTICAL_BARS = 12      # 超过则改横向条形，竖排中文标签会挤在一起
LONG_LABEL_WIDTH = 12       # 标签显示宽度超过此值改横向（机构名普遍较长）
MAX_DONUT_SLICES = 8        # 环形图超过 8 片无法辨读
MIN_HEATMAP_SIDE = 6        # 两维类别数都达到该值才用热力图
DEFAULT_MAX_CATEGORIES = 30

NA_TEXT = "不可算"

# 配色：全部经 contrast_ratio() 校验，正文色对白底 ≥ 4.5:1，
# 数据色对白底 ≥ 3:1（WCAG AA 对文本与图形对象的分档要求）。
COLOR_TEXT = "#1a1a1a"
COLOR_MUTED = "#4d4d4d"
COLOR_AXIS = "#595959"
COLOR_GRID = "#d0d0d0"
COLOR_BG = "#ffffff"
COLOR_NA = "#f2f2f2"
COLOR_NA_LINE = "#8c8c8c"
PALETTE = ("#1f4e79", "#a3480a", "#276749", "#5b3a8c",
           "#8a1c4a", "#1b5e5e", "#6b5400", "#3f4d5c")
HEAT_SCALE = ("#eef3f8", "#cbdbe9", "#a3c0d9", "#7aa3c6", "#3f7099", "#1f4e79")


def esc(text) -> str:
    """SVG/HTML 文本转义。工单含客户自由文本，未转义等于开放注入。"""
    return _escape("" if text is None else str(text), quote=True)


def fmt_value(value, fmt: str) -> str:
    """数值展示统一走指标层，保证图注与数据表里的写法一致。"""
    return format_by_fmt(fmt, value, na_text=NA_TEXT)


# ---------------------------------------------------------------- 对比度

def _srgb(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 对比度。用于自测配色，也用于热力图自动选字色。"""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def text_color_on(bg: str) -> str:
    """在给定底色上选可读字色，深底用白字、浅底用黑字。"""
    return "#ffffff" if contrast_ratio("#ffffff", bg) >= contrast_ratio(COLOR_TEXT, bg) \
        else COLOR_TEXT


# ---------------------------------------------------------------- 文本排版

def display_width(text) -> int:
    """中文按 2 个西文字符宽度计。SVG 无法测量字宽，只能按字符类别估算。"""
    total = 0
    for ch in str(text or ""):
        total += 2 if ord(ch) > 0x2E7F else 1
    return total


def truncate_label(text, max_width: int = 16) -> str:
    text = str(text or "")
    if display_width(text) <= max_width:
        return text
    out, used = [], 0
    for ch in text:
        step = 2 if ord(ch) > 0x2E7F else 1
        if used + step > max_width - 1:
            break
        out.append(ch)
        used += step
    return "".join(out) + "…"


def wrap_label(text, max_width: int = 8, max_lines: int = 2) -> list[str]:
    """按显示宽度折行；超出行数上限时最后一行截断加省略号，不无限增高图。"""
    text = str(text or "")
    lines: list[str] = []
    cur, used = [], 0
    for ch in text:
        step = 2 if ord(ch) > 0x2E7F else 1
        if used + step > max_width and cur:
            lines.append("".join(cur))
            cur, used = [], 0
        cur.append(ch)
        used += step
    if cur:
        lines.append("".join(cur))
    if not lines:
        return [""]
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = truncate_label(kept[-1] + lines[max_lines], max_width)
        lines = kept
    return lines


def nice_ticks(vmax: float, count: int = 4) -> tuple[float, list[float]]:
    """确定性刻度：1 / 2 / 2.5 / 5 / 10 步进，避免出现 0.333 这类刻度。"""
    if not vmax or vmax <= 0:
        return 1.0, [0.0, 0.5, 1.0]
    raw = vmax / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = mag * 10
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mag * mult:
            step = mag * mult
            break
    top = math.ceil(vmax / step) * step
    ticks, val = [], 0.0
    while val <= top + step * 1e-9:
        ticks.append(round(val, 10))
        val += step
    return top, ticks


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and not (isinstance(value, float) and math.isnan(value))


def _nums(values) -> list[float]:
    return [float(v) for v in values if _is_num(v)]


# ---------------------------------------------------------------- SVG 原语

FONT = ("system-ui, 'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', "
        "'Noto Sans CJK SC', sans-serif")


def _open_svg(width: int, height: int, *, title: str, desc: str,
              chart_id: str, chart_type: str) -> str:
    tid, did = f"{chart_id}-title", f"{chart_id}-desc"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'role="img" aria-labelledby="{tid} {did}" data-chart="{chart_type}" '
            f'class="wo-chart" style="max-width:100%;height:auto" focusable="false">'
            f'<title id="{tid}">{esc(title)}</title>'
            f'<desc id="{did}">{esc(desc)}</desc>'
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="{COLOR_BG}"/>')


def _text(x: float, y: float, content, *, size: int = 12, fill: str = COLOR_TEXT,
          anchor: str = "middle", weight: str = "normal",
          rotate: float | None = None) -> str:
    extra = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" font-family="{FONT}"'
            f'{extra}>{esc(content)}</text>')


def _rect(x: float, y: float, w: float, h: float, fill: str, *,
          stroke: str = "none", dash: str = "") -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" '
            f'height="{max(h, 0):.1f}" fill="{fill}" stroke="{stroke}"{dash_attr}/>')


def _line(x1: float, y1: float, x2: float, y2: float, color: str, *,
          width: float = 1, dash: str = "") -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{width}"{dash_attr}/>')


def _y_axis(pad_l: float, pad_t: float, plot_w: float, plot_h: float,
            ticks: list[float], top: float, fmt: str) -> list[str]:
    parts = []
    for tick in ticks:
        y = pad_t + plot_h - (tick / top) * plot_h if top else pad_t + plot_h
        parts.append(_line(pad_l, y, pad_l + plot_w, y, COLOR_GRID))
        parts.append(_text(pad_l - 8, y + 4, fmt_value(tick, fmt), size=11,
                           fill=COLOR_MUTED, anchor="end"))
    parts.append(_line(pad_l, pad_t, pad_l, pad_t + plot_h, COLOR_AXIS))
    return parts


def _x_tick_labels(labels: list[str], centers: list[float], y_base: float) -> list[str]:
    """长中文标签策略：短标签折两行居中，长标签旋转 -30° 并截断。"""
    widest = max((display_width(l) for l in labels), default=0)
    parts = []
    if widest <= 8:
        for label, cx in zip(labels, centers):
            for idx, line in enumerate(wrap_label(label, 8, 2)):
                parts.append(_text(cx, y_base + 14 + idx * 13, line, size=11,
                                   fill=COLOR_MUTED))
    else:
        for label, cx in zip(labels, centers):
            parts.append(_text(cx, y_base + 16, truncate_label(label, 18), size=11,
                               fill=COLOR_MUTED, anchor="end", rotate=-30))
    return parts


def _legend(items: list[tuple[str, str]], x: float, y: float) -> list[str]:
    """图例。颜色之外同时给文字，色觉障碍用户可辨。"""
    parts, cx = [], x
    for color, label in items:
        parts.append(_rect(cx, y - 9, 11, 11, color))
        parts.append(_text(cx + 16, y, label, size=11, fill=COLOR_MUTED, anchor="start"))
        cx += 16 + display_width(truncate_label(label, 16)) * 6.2 + 18
    return parts


def _na_marker(x: float, y_base: float, w: float) -> list[str]:
    """不可算：画空槽 + 文字，绝不画成 0 高度以外的任何数值形状。"""
    return [_rect(x, y_base - 12, w, 12, COLOR_NA, stroke=COLOR_NA_LINE, dash="3 2"),
            _text(x + w / 2, y_base - 18, NA_TEXT, size=10, fill=COLOR_MUTED)]


def _summary(values, fmt: str, kind: str) -> str:
    nums = _nums(values)
    na = len([v for v in values if not _is_num(v)])
    text = (f"{kind}，共 {len(values)} 项，"
            f"最大值 {fmt_value(max(nums), fmt) if nums else NA_TEXT}，"
            f"最小值 {fmt_value(min(nums), fmt) if nums else NA_TEXT}")
    if na:
        text += f"；其中 {na} 项数据不足无法计算，已留空未按 0 处理"
    return text + "。完整数值见下方数据表。"


# ---------------------------------------------------------------- 分类图

def render_bar(labels: list[str], values: list, *, title: str, fmt: str = "int",
               width: int = 760, height: int = 400, chart_id: str = "wochart") -> str:
    """纵向柱状图。类别少、标签短时可读性最好。"""
    pad_l, pad_r, pad_t, pad_b = 66, 20, 30, 96
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    top, ticks = nice_ticks(max(_nums(values), default=0))
    parts = _y_axis(pad_l, pad_t, plot_w, plot_h, ticks, top, fmt)
    y_base = pad_t + plot_h
    parts.append(_line(pad_l, y_base, pad_l + plot_w, y_base, COLOR_AXIS))

    step = plot_w / max(len(values), 1)
    bar_w = min(step * 0.62, 76)
    centers = []
    for idx, value in enumerate(values):
        cx = pad_l + step * (idx + 0.5)
        centers.append(cx)
        x = cx - bar_w / 2
        if not _is_num(value):
            parts += _na_marker(x, y_base, bar_w)
            continue
        h = (value / top) * plot_h if top else 0
        parts.append(_rect(x, y_base - h, bar_w, h, PALETTE[0]))
        parts.append(_text(cx, y_base - h - 6, fmt_value(value, fmt), size=11))
    parts += _x_tick_labels(labels, centers, y_base)
    return (_open_svg(width, height, title=title, desc=_summary(values, fmt, "柱状图"),
                      chart_id=chart_id, chart_type="bar")
            + "".join(parts) + "</svg>")


def render_bar_h(labels: list[str], values: list, *, title: str, fmt: str = "int",
                 width: int = 760, height: int | None = None,
                 chart_id: str = "wochart") -> str:
    """横向条形图。机构名这类长中文标签只有横排才不重叠。"""
    row_h = 26
    pad_l, pad_r, pad_t, pad_b = 168, 96, 24, 34
    height = height or pad_t + pad_b + row_h * max(len(values), 1)
    plot_w = width - pad_l - pad_r
    top, ticks = nice_ticks(max(_nums(values), default=0))

    parts = []
    for tick in ticks:
        x = pad_l + (tick / top) * plot_w if top else pad_l
        parts.append(_line(x, pad_t, x, pad_t + row_h * len(values), COLOR_GRID))
        parts.append(_text(x, pad_t + row_h * len(values) + 16, fmt_value(tick, fmt),
                           size=11, fill=COLOR_MUTED))
    parts.append(_line(pad_l, pad_t, pad_l, pad_t + row_h * max(len(values), 1), COLOR_AXIS))

    bar_h = row_h * 0.6
    for idx, (label, value) in enumerate(zip(labels, values)):
        y = pad_t + idx * row_h + (row_h - bar_h) / 2
        parts.append(_text(pad_l - 10, y + bar_h - 3, truncate_label(label, 26),
                           size=11, fill=COLOR_MUTED, anchor="end"))
        if not _is_num(value):
            parts.append(_rect(pad_l, y, 12, bar_h, COLOR_NA,
                               stroke=COLOR_NA_LINE, dash="3 2"))
            parts.append(_text(pad_l + 18, y + bar_h - 3, NA_TEXT, size=10,
                               fill=COLOR_MUTED, anchor="start"))
            continue
        w = (value / top) * plot_w if top else 0
        parts.append(_rect(pad_l, y, w, bar_h, PALETTE[0]))
        parts.append(_text(pad_l + w + 6, y + bar_h - 3, fmt_value(value, fmt),
                           size=11, anchor="start"))
    return (_open_svg(width, height, title=title,
                      desc=_summary(values, fmt, "横向条形图"),
                      chart_id=chart_id, chart_type="bar_h")
            + "".join(parts) + "</svg>")


def render_line(labels: list[str], series: list[dict], *, title: str, fmt: str = "int",
                width: int = 760, height: int = 400, chart_id: str = "wochart") -> str:
    """折线图。缺口不连线：把不可算的点连起来等于编造趋势。"""
    pad_l, pad_r, pad_t, pad_b = 66, 24, 30, 86
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    all_values = [v for s in series for v in s["values"]]
    top, ticks = nice_ticks(max(_nums(all_values), default=0))
    parts = _y_axis(pad_l, pad_t, plot_w, plot_h, ticks, top, fmt)
    y_base = pad_t + plot_h
    parts.append(_line(pad_l, y_base, pad_l + plot_w, y_base, COLOR_AXIS))

    n = max(len(labels), 1)
    step = plot_w / n
    centers = [pad_l + step * (i + 0.5) for i in range(len(labels))]
    gaps = 0
    for s_idx, item in enumerate(series):
        color = PALETTE[s_idx % len(PALETTE)]
        run: list[str] = []
        for idx, value in enumerate(item["values"]):
            if not _is_num(value):
                gaps += 1
                if len(run) > 1:
                    parts.append(f'<polyline points="{" ".join(run)}" fill="none" '
                                 f'stroke="{color}" stroke-width="2"/>')
                run = []
                continue
            y = y_base - (value / top) * plot_h if top else y_base
            run.append(f"{centers[idx]:.1f},{y:.1f}")
            parts.append(f'<circle cx="{centers[idx]:.1f}" cy="{y:.1f}" r="3.2" '
                         f'fill="{color}"/>')
            if len(series) == 1 and len(labels) <= 10:
                parts.append(_text(centers[idx], y - 9, fmt_value(value, fmt), size=11))
        if len(run) > 1:
            parts.append(f'<polyline points="{" ".join(run)}" fill="none" '
                         f'stroke="{color}" stroke-width="2"/>')
        elif len(run) == 1:
            pass
    parts += _x_tick_labels(labels, centers, y_base)
    if len(series) > 1:
        parts += _legend([(PALETTE[i % len(PALETTE)], s["label"])
                          for i, s in enumerate(series)], pad_l, height - 12)
    desc = (f"折线图，{len(labels)} 个时间点，{len(series)} 条序列，"
            f"最大值 {fmt_value(max(_nums(all_values)), fmt) if _nums(all_values) else NA_TEXT}")
    if gaps:
        desc += f"；{gaps} 个点数据不足未绘制，折线在该处断开"
    return (_open_svg(width, height, title=title, desc=desc + "。完整数值见下方数据表。",
                      chart_id=chart_id, chart_type="line")
            + "".join(parts) + "</svg>")


def render_grouped_bar(labels: list[str], series: list[dict], *, title: str,
                       fmt: str = "int", width: int = 760, height: int = 420,
                       chart_id: str = "wochart") -> str:
    """分组柱状图。多指标或多分组并列对比，不做任何堆叠求和。"""
    pad_l, pad_r, pad_t, pad_b = 66, 24, 30, 104
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    all_values = [v for s in series for v in s["values"]]
    top, ticks = nice_ticks(max(_nums(all_values), default=0))
    parts = _y_axis(pad_l, pad_t, plot_w, plot_h, ticks, top, fmt)
    y_base = pad_t + plot_h
    parts.append(_line(pad_l, y_base, pad_l + plot_w, y_base, COLOR_AXIS))

    step = plot_w / max(len(labels), 1)
    group_w = min(step * 0.72, 96)
    bar_w = group_w / max(len(series), 1)
    centers = []
    for idx in range(len(labels)):
        cx = pad_l + step * (idx + 0.5)
        centers.append(cx)
        left = cx - group_w / 2
        for s_idx, item in enumerate(series):
            value = item["values"][idx] if idx < len(item["values"]) else None
            x = left + s_idx * bar_w
            color = PALETTE[s_idx % len(PALETTE)]
            if not _is_num(value):
                parts += _na_marker(x + bar_w * 0.1, y_base, bar_w * 0.8)
                continue
            h = (value / top) * plot_h if top else 0
            parts.append(_rect(x + bar_w * 0.1, y_base - h, bar_w * 0.8, h, color))
            if len(labels) * len(series) <= 12:
                parts.append(_text(x + bar_w / 2, y_base - h - 6,
                                   fmt_value(value, fmt), size=10))
    parts += _x_tick_labels(labels, centers, y_base)
    parts += _legend([(PALETTE[i % len(PALETTE)], s["label"])
                      for i, s in enumerate(series)], pad_l, height - 12)
    return (_open_svg(width, height, title=title,
                      desc=_summary(all_values, fmt,
                                    f"分组柱状图，{len(series)} 组 × {len(labels)} 类"),
                      chart_id=chart_id, chart_type="grouped_bar")
            + "".join(parts) + "</svg>")


def render_stacked_bar(labels: list[str], series: list[dict], *, title: str,
                       fmt: str = "int", width: int = 760, height: int = 420,
                       chart_id: str = "wochart") -> str:
    """堆叠柱状图。仅用于可相加的件数类指标；比率不可堆叠。"""
    pad_l, pad_r, pad_t, pad_b = 66, 24, 30, 104
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    stacks, skipped = [], 0
    for idx in range(len(labels)):
        total = 0.0
        for item in series:
            value = item["values"][idx] if idx < len(item["values"]) else None
            if _is_num(value):
                total += float(value)
            else:
                skipped += 1
        stacks.append(total)
    top, ticks = nice_ticks(max(stacks, default=0))
    parts = _y_axis(pad_l, pad_t, plot_w, plot_h, ticks, top, fmt)
    y_base = pad_t + plot_h
    parts.append(_line(pad_l, y_base, pad_l + plot_w, y_base, COLOR_AXIS))

    step = plot_w / max(len(labels), 1)
    bar_w = min(step * 0.58, 72)
    centers = []
    for idx, stack_total in enumerate(stacks):
        cx = pad_l + step * (idx + 0.5)
        centers.append(cx)
        x = cx - bar_w / 2
        cursor = y_base
        for s_idx, item in enumerate(series):
            value = item["values"][idx] if idx < len(item["values"]) else None
            if not _is_num(value) or value <= 0:
                continue
            h = (float(value) / top) * plot_h if top else 0
            parts.append(_rect(x, cursor - h, bar_w, h, PALETTE[s_idx % len(PALETTE)]))
            if h >= 15:
                color = text_color_on(PALETTE[s_idx % len(PALETTE)])
                parts.append(_text(cx, cursor - h / 2 + 4, fmt_value(value, fmt),
                                   size=10, fill=color))
            cursor -= h
        parts.append(_text(cx, cursor - 6, fmt_value(stack_total, fmt), size=11))
    parts += _x_tick_labels(labels, centers, y_base)
    parts += _legend([(PALETTE[i % len(PALETTE)], s["label"])
                      for i, s in enumerate(series)], pad_l, height - 12)
    desc = (f"堆叠柱状图，{len(labels)} 类 × {len(series)} 个构成项，"
            f"最大合计 {fmt_value(max(stacks, default=0), fmt)}")
    if skipped:
        desc += f"；{skipped} 个构成项数据不足未计入堆叠，合计因此偏小"
    return (_open_svg(width, height, title=title, desc=desc + "。完整数值见下方数据表。",
                      chart_id=chart_id, chart_type="stacked_bar")
            + "".join(parts) + "</svg>")


# ---------------------------------------------------------------- 结构与矩阵

def _arc_path(cx: float, cy: float, r_out: float, r_in: float,
              a0: float, a1: float) -> str:
    def point(radius: float, angle: float) -> tuple[float, float]:
        rad = math.radians(angle)
        return cx + radius * math.sin(rad), cy - radius * math.cos(rad)

    large = 1 if (a1 - a0) > 180 else 0
    x0, y0 = point(r_out, a0)
    x1, y1 = point(r_out, a1)
    x2, y2 = point(r_in, a1)
    x3, y3 = point(r_in, a0)
    return (f"M{x0:.1f},{y0:.1f} A{r_out:.1f},{r_out:.1f} 0 {large} 1 {x1:.1f},{y1:.1f} "
            f"L{x2:.1f},{y2:.1f} A{r_in:.1f},{r_in:.1f} 0 {large} 0 {x3:.1f},{y3:.1f} Z")


def render_donut(labels: list[str], values: list, *, title: str, fmt: str = "int",
                 width: int = 760, height: int = 360, chart_id: str = "wochart",
                 share_warning: str = "") -> str:
    """环形图。仅表达构成占比；不可算项不占份额，并在图注声明。"""
    cx, cy = 170, height / 2
    r_out, r_in = 118, 66
    pairs = [(l, v) for l, v in zip(labels, values) if _is_num(v) and float(v) > 0]
    total = sum(float(v) for _, v in pairs)
    dropped = len(values) - len(pairs)

    parts = []
    if not pairs or total <= 0:
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_out}" fill="{COLOR_NA}" '
                     f'stroke="{COLOR_NA_LINE}" stroke-dasharray="4 3"/>')
        parts.append(_text(cx, cy + 5, NA_TEXT, size=14, fill=COLOR_MUTED))
    elif len(pairs) == 1:
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_out}" fill="{PALETTE[0]}"/>')
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_in}" fill="{COLOR_BG}"/>')
    else:
        angle = 0.0
        for idx, (_, value) in enumerate(pairs):
            sweep = float(value) / total * 360
            parts.append(f'<path d="{_arc_path(cx, cy, r_out, r_in, angle, angle + sweep)}" '
                         f'fill="{PALETTE[idx % len(PALETTE)]}"/>')
            angle += sweep
    parts.append(_text(cx, cy - 4, "合计", size=11, fill=COLOR_MUTED))
    parts.append(_text(cx, cy + 16, fmt_value(total, fmt), size=15, weight="600"))

    ly = cy - min(len(pairs), 8) * 11 + 8
    for idx, (label, value) in enumerate(pairs):
        share = float(value) / total if total else 0
        parts.append(_rect(320, ly - 9, 11, 11, PALETTE[idx % len(PALETTE)]))
        parts.append(_text(338, ly, f"{truncate_label(label, 20)}　"
                                    f"{fmt_value(value, fmt)}　{share * 100:.1f}%",
                           size=12, fill=COLOR_TEXT, anchor="start"))
        ly += 22
    desc = f"环形图，{len(pairs)} 个构成项，合计 {fmt_value(total, fmt)}"
    if dropped:
        desc += f"；{dropped} 项为零或数据不足，未占份额"
    if share_warning:
        desc += f"；{share_warning}"
    return (_open_svg(width, height, title=title, desc=desc + "。完整数值见下方数据表。",
                      chart_id=chart_id, chart_type="donut")
            + "".join(parts) + "</svg>")


def render_heatmap(row_labels: list[str], col_labels: list[str], matrix: list[list],
                   *, title: str, fmt: str = "int", width: int = 760,
                   height: int | None = None, chart_id: str = "wochart") -> str:
    """热力图。两维交叉下钻；不可算格子用空白斜纹底而非最浅色，避免读成低值。"""
    pad_l, pad_t, pad_r, pad_b = 150, 74, 20, 30
    cell_w = max((width - pad_l - pad_r) / max(len(col_labels), 1), 40)
    cell_h = 30
    height = height or pad_t + pad_b + cell_h * max(len(row_labels), 1)
    flat = _nums([v for row in matrix for v in row])
    vmin, vmax = (min(flat), max(flat)) if flat else (0, 0)
    span = (vmax - vmin) or 1

    parts = []
    for c_idx, label in enumerate(col_labels):
        x = pad_l + cell_w * (c_idx + 0.5)
        for line_idx, line in enumerate(wrap_label(label, 8, 2)):
            parts.append(_text(x, pad_t - 24 + line_idx * 13, line, size=11,
                               fill=COLOR_MUTED))
    na_cells = 0
    for r_idx, row_label in enumerate(row_labels):
        y = pad_t + r_idx * cell_h
        parts.append(_text(pad_l - 10, y + cell_h / 2 + 4, truncate_label(row_label, 22),
                           size=11, fill=COLOR_MUTED, anchor="end"))
        for c_idx in range(len(col_labels)):
            value = matrix[r_idx][c_idx] if c_idx < len(matrix[r_idx]) else None
            x = pad_l + c_idx * cell_w
            if not _is_num(value):
                na_cells += 1
                parts.append(_rect(x + 1, y + 1, cell_w - 2, cell_h - 2, COLOR_NA,
                                   stroke=COLOR_NA_LINE, dash="3 2"))
                parts.append(_text(x + cell_w / 2, y + cell_h / 2 + 4, NA_TEXT,
                                   size=10, fill=COLOR_MUTED))
                continue
            bucket = int((float(value) - vmin) / span * (len(HEAT_SCALE) - 1) + 1e-9)
            fill = HEAT_SCALE[min(bucket, len(HEAT_SCALE) - 1)]
            parts.append(_rect(x + 1, y + 1, cell_w - 2, cell_h - 2, fill,
                               stroke=COLOR_BG))
            parts.append(_text(x + cell_w / 2, y + cell_h / 2 + 4, fmt_value(value, fmt),
                               size=10, fill=text_color_on(fill)))
    desc = (f"热力图，{len(row_labels)} 行 × {len(col_labels)} 列，"
            f"取值区间 {fmt_value(vmin, fmt)} 至 {fmt_value(vmax, fmt)}，"
            f"每格同时标注数值，不以颜色作为唯一区分")
    if na_cells:
        desc += f"；{na_cells} 格数据不足，已标注为{NA_TEXT}"
    return (_open_svg(width, height, title=title, desc=desc + "。完整数值见下方数据表。",
                      chart_id=chart_id, chart_type="heatmap")
            + "".join(parts) + "</svg>")


def render_bullet(label: str, value, target, *, title: str, fmt: str = "pct",
                  width: int = 760, height: int = 150, chart_id: str = "wochart",
                  higher_is_better: bool = True) -> str:
    """靶心图：实际值对目标值。目标值只来自 profile 配置，代码不内置任何目标。"""
    pad_l, pad_r, pad_t = 168, 120, 56
    bar_h, plot_w = 26, width - pad_l - pad_r
    scale_max = max(_nums([value, target]), default=0) * 1.15 or 1
    top, ticks = nice_ticks(scale_max, 4)

    parts = [_text(pad_l - 12, pad_t + bar_h - 6, truncate_label(label, 24), size=12,
                   fill=COLOR_MUTED, anchor="end"),
             _rect(pad_l, pad_t, plot_w, bar_h, COLOR_NA)]
    for tick in ticks:
        x = pad_l + (tick / top) * plot_w
        parts.append(_line(x, pad_t, x, pad_t + bar_h + 6, COLOR_GRID))
        parts.append(_text(x, pad_t + bar_h + 22, fmt_value(tick, fmt), size=11,
                           fill=COLOR_MUTED))
    if _is_num(value):
        w = (float(value) / top) * plot_w
        parts.append(_rect(pad_l, pad_t + 5, w, bar_h - 10, PALETTE[0]))
        parts.append(_text(pad_l + plot_w + 10, pad_t + bar_h - 7,
                           fmt_value(value, fmt), size=13, weight="600", anchor="start"))
    else:
        parts.append(_text(pad_l + 8, pad_t + bar_h - 7, NA_TEXT, size=12,
                           fill=COLOR_MUTED, anchor="start"))
    gap_text = ""
    if _is_num(target):
        tx = pad_l + (float(target) / top) * plot_w
        parts.append(_line(tx, pad_t - 6, tx, pad_t + bar_h + 6, PALETTE[1], width=3))
        parts.append(_text(tx, pad_t - 12, f"目标 {fmt_value(target, fmt)}", size=11,
                           fill=PALETTE[1]))
        if _is_num(value):
            diff = float(value) - float(target)
            reached = diff >= 0 if higher_is_better else diff <= 0
            gap_text = (f"；{'已达标' if reached else '未达标'}，"
                        f"与目标相差 {fmt_value(abs(diff), fmt)}")
    desc = (f"靶心图，{esc(label)} 实际值 {fmt_value(value, fmt)}，"
            f"目标值 {fmt_value(target, fmt)}{gap_text}")
    return (_open_svg(width, height, title=title, desc=desc + "。完整数值见下方数据表。",
                      chart_id=chart_id, chart_type="bullet")
            + "".join(parts) + "</svg>")


# ---------------------------------------------------------------- 数据抽取

ADDITIVE_FMTS = (FMT_INT, FMT_MONEY)


def _meta(metric_id: str) -> dict:
    metric = get_metric(metric_id)
    if metric is None:
        return {"id": metric_id, "label": metric_id, "fmt": FMT_INT,
                "higher_is_better": None}
    return {"id": metric.id, "label": metric.label, "fmt": metric.fmt,
            "higher_is_better": metric.higher_is_better}


def _is_additive(metric_id: str) -> bool:
    return _meta(metric_id)["fmt"] in ADDITIVE_FMTS


def target_for(profile: dict | None, metric_id: str):
    """目标值只从 profile `targets` 读取。代码内不预置任何达成率目标，
    否则等于替客户设定考核线。"""
    targets = ((profile or {}).get("targets") or {})
    value = targets.get(metric_id)
    return value if _is_num(value) else None


def _max_categories(profile: dict | None) -> int:
    cfg = ((profile or {}).get("chart") or {})
    raw = cfg.get("max_categories")
    return int(raw) if isinstance(raw, int) and raw > 0 else DEFAULT_MAX_CATEGORIES


def _ordered_unique(rows: list[dict], key: str) -> list[str]:
    seen, out = set(), []
    for row in rows:
        val = str(row.get(key, ""))
        if val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _truncate_note(shown: int, total: int) -> str:
    return (f"图表已展示前 {shown} 项，共 {total} 项，"
            f"完整数据见下方表格。") if total > shown else ""


def _one_dim_data(spec: dict, result: dict, limit: int) -> dict:
    dim = spec["dimensions"][0]
    rows = result.get("rows") or []
    total = len(rows)
    kept = rows[:limit]
    return {
        "labels": [str(r.get(dim, "")) for r in kept],
        "series": [{"id": mid, "label": _meta(mid)["label"], "fmt": _meta(mid)["fmt"],
                    "values": [r.get(mid) for r in kept]}
                   for mid in spec["metrics"]],
        "total": total, "shown": len(kept),
    }


def _two_dim_data(spec: dict, result: dict, limit: int) -> dict:
    d1, d2 = spec["dimensions"][0], spec["dimensions"][1]
    metric_id = spec["metrics"][0]
    rows = result.get("rows") or []
    cats_all, groups_all = _ordered_unique(rows, d1), _ordered_unique(rows, d2)
    cats, groups = cats_all[:limit], groups_all[:limit]
    lookup = {(str(r.get(d1, "")), str(r.get(d2, ""))): r.get(metric_id) for r in rows}
    # 缺失组合的含义取决于指标类型：件数类是「确实没有」= 0；
    # 比率类没有样本就是不可算，不能当 0。
    additive = _is_additive(metric_id)
    filled = 0

    def cell(cat: str, group: str):
        nonlocal filled
        if (cat, group) in lookup:
            return lookup[(cat, group)]
        filled += 1
        return 0 if additive else None

    matrix = [[cell(c, g) for g in groups] for c in cats]
    series = [{"id": g, "label": g, "fmt": _meta(metric_id)["fmt"],
               "values": [matrix[c_idx][g_idx] for c_idx in range(len(cats))]}
              for g_idx, g in enumerate(groups)]
    return {"labels": cats, "groups": groups, "matrix": matrix, "series": series,
            "fmt": _meta(metric_id)["fmt"], "metric_label": _meta(metric_id)["label"],
            "total": len(cats_all), "shown": len(cats),
            "group_total": len(groups_all), "group_shown": len(groups),
            "filled": filled, "additive": additive}


# ---------------------------------------------------------------- 选图

def suggest_chart(spec: dict, result: dict, profile: dict | None = None) -> str:
    """确定性选图。同一个 Spec + 同一份结果永远得到同一个结论。"""
    metrics = list(spec.get("metrics") or [])
    dims = list(spec.get("dimensions") or [])
    rows = result.get("rows") or []
    if not metrics or not rows:
        return "none"

    if not dims:
        if len(metrics) == 1 and target_for(profile, metrics[0]) is not None:
            return "bullet"
        return "none"          # 单值结果由 KPI 卡承载，画图没有信息增量
    if len(dims) > 2:
        return "none"          # 三维以上看表格更清楚，硬画只会误读

    if len(dims) == 1:
        dim = get_dimension(dims[0])
        if dim is not None and dim.kind == KIND_TIME:
            return "line"
        if len(metrics) > 1:
            return "grouped_bar"
        labels = [str(r.get(dims[0], "")) for r in rows]
        count = len(labels)
        widest = max((display_width(l) for l in labels), default=0)
        explode = dim is not None and dim.kind == KIND_EXPLODE
        is_org = bool(dim is not None and dim.org_caliber)
        # 环形图只用于「各部分相加等于整体」的构成：件数类 + 非多值维度。
        # 比率类各部分不构成整体，画成环形会诱导读出错误的构成结论；
        # 机构维度属于排名场景（谁高谁低），条形图才读得出顺序，不用环形。
        if (_is_additive(metrics[0]) and not explode and not is_org
                and count <= MAX_DONUT_SLICES and widest <= LONG_LABEL_WIDTH):
            return "donut"
        if count > MAX_VERTICAL_BARS or widest > LONG_LABEL_WIDTH:
            return "bar_h"
        return "bar"

    if len(metrics) > 1:
        return "none"          # 两维多指标是交叉表，图表无法表达
    if any((get_dimension(d) or Dimension(d, d, d)).kind == KIND_TIME
           for d in dims):
        return "line"
    n_rows = len(_ordered_unique(rows, dims[0]))
    n_cols = len(_ordered_unique(rows, dims[1]))
    if n_rows >= MIN_HEATMAP_SIDE and n_cols >= MIN_HEATMAP_SIDE:
        return "heatmap"
    return "stacked_bar" if _is_additive(metrics[0]) else "grouped_bar"


# ---------------------------------------------------------------- 引擎

def resolve_engine(profile: dict | None, engine: str | None = None) -> tuple[str, str | None, str]:
    """返回 `(引擎, 内联 js 内容, 回退说明)`。ECharts 只允许内联本地脚本：
    联网拉 CDN 在私有化环境不可用，且把三方脚本塞进合规报告不可接受。"""
    cfg = ((profile or {}).get("chart") or {})
    want = str(engine or cfg.get("engine") or "svg").lower()
    if want != "echarts":
        return "svg", None, ""
    path = cfg.get("echarts_path")
    if not path:
        return "svg", None, ("已请求 ECharts 模式，但 profile 未配置 chart.echarts_path，"
                             "图表已回退为内联 SVG。")
    if not os.path.isfile(str(path)):
        return "svg", None, (f"已请求 ECharts 模式，但未找到本地脚本 {path}，"
                             f"图表已回退为内联 SVG（本 skill 不联网下载三方脚本，"
                             f"需自行放入该文件）。")
    try:
        with open(str(path), encoding="utf-8") as handle:
            js = handle.read()
    except OSError as exc:
        return "svg", None, (f"读取本地 ECharts 脚本失败（{exc.strerror}），"
                             f"图表已回退为内联 SVG。")
    return "echarts", js, ""


def _json_num(value):
    return value if _is_num(value) else None      # null → ECharts 断开，不当 0


def echarts_option(chart_type: str, payload: dict) -> dict | None:
    """构造 ECharts option。不支持的图型返回 None，由调用方回退 SVG。"""
    labels = list(payload.get("labels") or [])
    series = list(payload.get("series") or [])
    if not series:
        return None
    rotate = 30 if max((display_width(l) for l in labels), default=0) > 8 else 0
    base = {
        "title": {"text": payload.get("title", ""), "left": "center",
                  "textStyle": {"fontSize": 14}},
        "backgroundColor": COLOR_BG,
        "color": list(PALETTE),
        "textStyle": {"fontFamily": FONT},
        "tooltip": {"trigger": "axis"},
    }
    if len(series) > 1:
        base["legend"] = {"bottom": 0}
    cat_axis = {"type": "category", "data": labels,
                "axisLabel": {"interval": 0, "rotate": rotate}}
    val_axis = {"type": "value"}

    if chart_type in ("bar", "grouped_bar", "stacked_bar", "line"):
        base["xAxis"], base["yAxis"] = cat_axis, val_axis
        kind = "line" if chart_type == "line" else "bar"
        base["series"] = []
        for item in series:
            entry = {"type": kind, "name": item["label"],
                     "data": [_json_num(v) for v in item["values"]],
                     "label": {"show": len(labels) * len(series) <= 12}}
            if chart_type == "stacked_bar":
                entry["stack"] = "total"
            if kind == "line":
                entry["connectNulls"] = False
            base["series"].append(entry)
        return base
    if chart_type == "bar_h":
        base["xAxis"], base["yAxis"] = val_axis, dict(cat_axis, inverse=True)
        base["series"] = [{"type": "bar", "name": item["label"],
                           "data": [_json_num(v) for v in item["values"]],
                           "label": {"show": True, "position": "right"}}
                          for item in series]
        return base
    if chart_type == "donut":
        base["tooltip"] = {"trigger": "item"}
        base["series"] = [{"type": "pie", "radius": ["45%", "70%"],
                           "data": [{"name": label, "value": value}
                                    for label, value in zip(labels, series[0]["values"])
                                    if _is_num(value) and float(value) > 0]}]
        return base
    return None


# ---------------------------------------------------------------- 主入口

def _empty(reason: str, notes: list[str]) -> dict:
    return {"type": "none", "svg": "", "engine": "svg", "echarts": None,
            "notes": list(notes), "reason": reason, "shown": 0, "total": 0}


def _no_chart_reason(spec: dict, result: dict) -> str:
    """不出图必须说清楚为什么，读者才知道不是渲染失败。"""
    dims = list(spec.get("dimensions") or [])
    metrics = list(spec.get("metrics") or [])
    if not (result.get("rows") or []):
        return "查询结果为空，未生成图表。"
    if not metrics:
        return "Spec 未指定指标，未生成图表。"
    if not dims:
        return "单值结果由 KPI 卡展示，出图没有信息增量。"
    if len(dims) > 2:
        return "三个及以上维度的交叉结果不出图，请看数据表。"
    if len(dims) == 2 and len(metrics) > 1:
        return "两个维度 + 多个指标属于交叉表，图表无法完整表达，请看数据表。"
    return "该结果不适合出图，请以数据表为准。"


def build_chart(spec: dict, result: dict, profile: dict | None = None, *,
                chart_id: str = "wochart", engine: str | None = None) -> dict:
    """由 Spec + ResultSet 生成图表。返回 dict，渲染层只负责放进页面。

    显式指定的图型与数据形态不符时**降级并说明原因**，不静默换图。
    """
    profile = profile or {}
    notes: list[str] = []
    if (result.get("status") or "ok") != "ok":
        return _empty("查询未返回可用结果，未生成图表。", notes)

    requested = str((spec.get("output") or {}).get("chart") or "auto")
    if requested not in CHART_TYPES and requested != "auto":
        notes.append(f"未识别的图表类型「{requested}」，已改为自动选图。")
        requested = "auto"
    auto = requested == "auto"
    ctype = suggest_chart(spec, result, profile) if auto else requested
    if ctype == "none":
        return _empty("按 Spec 要求不出图，请以数据表为准。" if not auto
                      else _no_chart_reason(spec, result), notes)

    dims = list(spec.get("dimensions") or [])
    metrics = list(spec.get("metrics") or [])
    title = ((spec.get("output") or {}).get("title") or result.get("title") or "")
    fmt = _meta(metrics[0])["fmt"] if metrics else FMT_INT

    # ---- 0 维：只有配置了目标值才有靶心图 ----
    if not dims:
        target = target_for(profile, metrics[0]) if len(metrics) == 1 else None
        if ctype != "bullet":
            return _empty(f"「{ctype}」需要分组维度，当前查询没有维度。", notes)
        if target is None:
            notes.append(f"未在 profile targets 中配置「{_meta(metrics[0])['label']}」"
                         f"的目标值，无法画达成率靶心图（本 skill 不预设考核目标）。")
            return _empty("缺少目标值配置，未生成靶心图。", notes)
        value = (result.get("totals") or {}).get(metrics[0])
        svg = render_bullet(_meta(metrics[0])["label"], value, target, title=title,
                            fmt=fmt, chart_id=chart_id,
                            higher_is_better=_meta(metrics[0])["higher_is_better"] is not False)
        payload = {"labels": [_meta(metrics[0])["label"], "目标"], "title": title,
                   "fmt": fmt,
                   "series": [{"label": "实际", "values": [value]},
                              {"label": "目标", "values": [target]}]}
        return _finalize("bullet", svg, payload, profile, engine, notes, 1, 1)

    # ---- 1 维 ----
    if len(dims) == 1:
        if ctype in ("heatmap", "stacked_bar", "bullet"):
            fallback = suggest_chart(spec, result, profile)
            notes.append(f"「{ctype}」需要两个分组维度（或目标值配置），"
                         f"当前只有一个维度，已改用「{fallback}」。")
            ctype = fallback
            if ctype == "none":
                return _empty("数据形态不支持出图，请看数据表。", notes)
        if ctype == "donut" and len(metrics) > 1:
            notes.append("环形图只能表达单个指标的构成，多指标已改用分组柱状图。")
            ctype = "grouped_bar"
        cap = MAX_DONUT_SLICES if ctype == "donut" else _max_categories(profile)
        data = _one_dim_data(spec, result, cap)
        note = _truncate_note(data["shown"], data["total"])
        if note:
            notes.append(note)
        labels, series = data["labels"], data["series"]
        payload = {"labels": labels, "series": series, "title": title, "fmt": fmt}
        if ctype == "donut":
            warning = ""
            if not _is_additive(metrics[0]):
                warning = ("该指标为比率类，各部分相加不等于整体，"
                           "环形图仅作分布示意，占比请以数据表为准")
                notes.append(warning + "。")
            svg = render_donut(labels, series[0]["values"], title=title, fmt=fmt,
                               chart_id=chart_id, share_warning=warning)
        elif ctype == "line":
            svg = render_line(labels, series, title=title, fmt=fmt, chart_id=chart_id)
        elif ctype == "grouped_bar":
            svg = render_grouped_bar(labels, series, title=title, fmt=fmt,
                                     chart_id=chart_id)
        elif ctype == "bar_h":
            svg = render_bar_h(labels, series[0]["values"], title=title, fmt=fmt,
                               chart_id=chart_id)
        else:
            ctype = "bar"
            svg = render_bar(labels, series[0]["values"], title=title, fmt=fmt,
                             chart_id=chart_id)
        return _finalize(ctype, svg, payload, profile, engine, notes,
                         data["shown"], data["total"])

    # ---- 2 维 ----
    if len(dims) > 2:
        return _empty("三个及以上维度的交叉结果不出图，请看数据表。", notes)
    if len(metrics) > 1:
        return _empty("两个维度 + 多个指标属于交叉表，图表无法完整表达，请看数据表。",
                      notes)
    if ctype in ("bar", "bar_h", "donut", "bullet"):
        fallback = suggest_chart(spec, result, profile)
        notes.append(f"「{ctype}」无法表达两个维度的交叉结果，已改用「{fallback}」。")
        ctype = fallback
    data = _two_dim_data(spec, result, _max_categories(profile))
    if ctype == "stacked_bar" and not data["additive"]:
        notes.append("比率类指标不可相加，堆叠会得出无意义的合计，已改用分组柱状图。")
        ctype = "grouped_bar"
    note = _truncate_note(data["shown"], data["total"])
    if note:
        notes.append(note)
    if data["group_total"] > data["group_shown"]:
        notes.append(f"第二维度已展示前 {data['group_shown']} 项，"
                     f"共 {data['group_total']} 项，完整数据见下方表格。")
    if data["filled"]:
        notes.append(f"{data['filled']} 个维度组合在结果中不存在，"
                     + ("按 0 件展示。" if data["additive"]
                        else f"没有样本无法计算，已标注为{NA_TEXT}，未按 0 处理。"))
    payload = {"labels": data["labels"], "series": data["series"], "title": title,
               "fmt": data["fmt"]}
    if ctype == "heatmap":
        svg = render_heatmap(data["labels"], data["groups"], data["matrix"],
                             title=title, fmt=data["fmt"], chart_id=chart_id)
    elif ctype == "line":
        svg = render_line(data["labels"], data["series"], title=title,
                          fmt=data["fmt"], chart_id=chart_id)
    elif ctype == "stacked_bar":
        svg = render_stacked_bar(data["labels"], data["series"], title=title,
                                 fmt=data["fmt"], chart_id=chart_id)
    else:
        ctype = "grouped_bar"
        svg = render_grouped_bar(data["labels"], data["series"], title=title,
                                 fmt=data["fmt"], chart_id=chart_id)
    return _finalize(ctype, svg, payload, profile, engine, notes,
                     data["shown"], data["total"])


def _finalize(ctype: str, svg: str, payload: dict, profile: dict,
              engine: str | None, notes: list[str], shown: int, total: int) -> dict:
    eng, js, fallback_note = resolve_engine(profile, engine)
    echarts = None
    if eng == "echarts":
        option = echarts_option(ctype, payload)
        if option is None:
            eng = "svg"
            fallback_note = (f"ECharts 模式暂不支持「{ctype}」图型，"
                             f"该图已回退为内联 SVG。")
        else:
            echarts = {"js": js, "option": json.dumps(option, ensure_ascii=False)}
    if fallback_note:
        notes.append(fallback_note)
    return {"type": ctype, "svg": svg, "engine": eng, "echarts": echarts,
            "notes": list(notes), "reason": "", "shown": shown, "total": total}
