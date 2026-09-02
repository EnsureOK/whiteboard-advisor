"""单文件 HTML 渲染层。

改造自 MIT 套件 insurance-business-operations 的问数引擎(指标/维度注册表已内嵌化)。

红线：
- **所有取值一律经 `esc()` 转义**。工单含客户自由文本，未转义等于把 `<script>`
  直接注入报告，这是真实注入风险，禁止在模板里拼接原始值。
- **零外链**：CSS 内联、图表为内联 SVG，无 CDN、无字体外链、无追踪脚本，
  报告可离线打开、可作为附件发送、可打印。
- **图表下方始终附数据表**：图是摘要，表是证据；表格可复制、可校对、可读屏。
- **口径与限制强制展示**：provenance / limitations / caveat / 自定义口径标记
  不允许被渲染层裁掉，读者必须能看到数字是怎么来的。
- 不可算一律显示固定文案，绝不显示 0 或空白。
"""
from __future__ import annotations

from datetime import datetime
from html import escape as _escape

from app.services import chartkit as wo_chart

NA_TEXT = "当前数据不足以计算"
CUSTOM_BADGE = "自定义口径"

STYLE = """
:root{--ink:#1a1a1a;--muted:#4d4d4d;--line:#d0d0d0;--bg:#ffffff;
--brand:#1f4e79;--warn:#a3480a;--soft:#f5f7fa}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--ink);
font-family:system-ui,'Microsoft YaHei','PingFang SC','Hiragino Sans GB',
'Noto Sans CJK SC',sans-serif;font-size:14px;line-height:1.6}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;color:var(--brand)}
h2{font-size:16px;margin:28px 0 10px;padding-left:9px;
border-left:4px solid var(--brand)}
.sub{color:var(--muted);font-size:13px;margin:0 0 18px}
.echo{background:var(--soft);border:1px solid var(--line);border-radius:6px;
padding:12px 14px;margin:0 0 18px}
.echo dt{font-weight:600;color:var(--muted);font-size:12px}
.echo dd{margin:2px 0 10px}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:0}
.kpi{flex:1 1 200px;border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.kpi .label{color:var(--muted);font-size:12px}
.kpi .value{font-size:24px;font-weight:600;margin-top:4px}
.kpi .na{font-size:14px;font-weight:400;color:var(--muted)}
.kpi .cmp{font-size:12px;color:var(--muted);margin-top:4px}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;
border:1px solid var(--warn);color:var(--warn);margin-left:6px}
figure{margin:0}
figcaption{color:var(--muted);font-size:12px;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
caption{text-align:left;color:var(--muted);font-size:12px;padding-bottom:6px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:right}
th{background:var(--soft);font-weight:600}
th.dim,td.dim{text-align:left}
td.na{color:var(--muted)}
tfoot td,tfoot th{font-weight:600;background:var(--soft)}
ol.notes,ul.notes{margin:6px 0;padding-left:22px;color:var(--muted);font-size:13px}
.prov{font-size:12px;color:var(--muted)}
.prov table{font-size:12px}
.prov th,.prov td{text-align:left}
.foot{margin-top:26px;border-top:1px solid var(--line);padding-top:10px;
color:var(--muted);font-size:12px}
.alert{border:1px solid var(--warn);border-left-width:4px;border-radius:4px;
padding:12px 14px;background:#fdf7f2}
pre{white-space:pre-wrap;font-family:inherit;margin:6px 0}
@media print{body{padding:0}.kpi,table,figure{break-inside:avoid}}
"""


def esc(value) -> str:
    """唯一的转义入口。渲染层任何位置写入数据都必须经过它。"""
    return _escape("" if value is None else str(value), quote=True)


def _fmt(value, fmt: str) -> str:
    return wo_chart.format_by_fmt(fmt, value, na_text=NA_TEXT)


def _is_na(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def _pct(value) -> str:
    return NA_TEXT if _is_na(value) else f"{value * 100:+.2f}%"


# ---------------------------------------------------------------- 片段

def render_echo(result: dict, spec: dict | None = None) -> str:
    """查询条件中文回显。读者必须先看到「统计的是什么」再看数字。"""
    items = [("查询口径回显", result.get("echo") or "（无）")]
    period = ((result.get("provenance") or {}).get("period") or {})
    if period.get("label"):
        items.append(("统计周期", period["label"]))
    rows = []
    for label, value in items:
        rows.append(f"<dt>{esc(label)}</dt><dd><pre>{esc(value)}</pre></dd>")
    return f'<dl class="echo">{"".join(rows)}</dl>'


def _metric_columns(result: dict) -> list[dict]:
    return [c for c in result.get("columns") or []
            if c.get("kind") == "metric" and not c["key"].endswith(("__prev", "__change"))]


def render_kpi_cards(result: dict) -> str:
    """KPI 卡。同环比可用时并列展示基期与变化，不可比时说明原因。"""
    totals = result.get("totals") or {}
    comparison = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
    cards = []
    for col in _metric_columns(result):
        key, fmt = col["key"], col.get("fmt", "int")
        value = totals.get(key)
        badge = f'<span class="badge">{esc(CUSTOM_BADGE)}</span>' if col.get("custom") else ""
        value_cls = "value na" if _is_na(value) else "value"
        cmp_html = ""
        if comparison.get("available"):
            prev, change = totals.get(f"{key}__prev"), totals.get(f"{key}__change")
            cmp_html = (f'<div class="cmp">基期 {esc(_fmt(prev, fmt))}　'
                        f'{esc(comparison.get("label", "对比"))} {esc(_pct(change))}</div>')
        elif comparison.get("reason"):
            cmp_html = f'<div class="cmp">{esc(comparison["reason"])}</div>'
        cards.append(f'<div class="kpi"><div class="label">{esc(col["label"])}{badge}</div>'
                     f'<div class="{value_cls}">{esc(_fmt(value, fmt))}</div>{cmp_html}</div>')
    return f'<div class="kpis">{"".join(cards)}</div>' if cards else ""


def render_table(result: dict, *, caption: str = "") -> str:
    """数据表。维度列左对齐、数值列右对齐，表头带 scope 供读屏定位。"""
    columns = result.get("columns") or []
    if not columns:
        return ""
    caveats = _caveat_index(result)
    head = []
    for col in columns:
        cls = "dim" if col.get("kind") == "dimension" else ""
        marker = ""
        if col.get("caveat"):
            marker = f'<sup>[{caveats[col["caveat"]]}]</sup>'
        badge = f'<span class="badge">{esc(CUSTOM_BADGE)}</span>' if col.get("custom") else ""
        head.append(f'<th scope="col" class="{cls}">{esc(col["label"])}{badge}{marker}</th>')

    body = []
    for row in result.get("rows") or []:
        cells = []
        for col in columns:
            value = row.get(col["key"])
            if col.get("kind") == "dimension":
                cells.append(f'<td class="dim">{esc(value)}</td>')
            elif _is_na(value):
                cells.append(f'<td class="na">{esc(NA_TEXT)}</td>')
            else:
                cells.append(f'<td>{esc(_fmt(value, col.get("fmt", "int")))}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")

    foot = ""
    totals = result.get("totals") or {}
    has_dimension = any(c.get("kind") == "dimension" for c in columns)
    if has_dimension and (result.get("rows") or []):
        # 只有分组结果才需要合计行；无维度时表格本身就是合计
        cells = []
        for idx, col in enumerate(columns):
            if col.get("kind") == "dimension":
                text = "合计" if idx == 0 else ""
                cells.append(f'<th scope="row" class="dim">{esc(text)}</th>')
            else:
                value = totals.get(col["key"])
                cells.append(f'<td class="na">{esc(NA_TEXT)}</td>' if _is_na(value)
                             else f'<td>{esc(_fmt(value, col.get("fmt", "int")))}</td>')
        foot = f"<tfoot><tr>{''.join(cells)}</tr></tfoot>"

    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    return (f'<table>{cap}<thead><tr>{"".join(head)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody>{foot}</table>')


def _caveat_index(result: dict) -> dict[str, int]:
    index: dict[str, int] = {}
    for col in result.get("columns") or []:
        text = col.get("caveat")
        if text and text not in index:
            index[text] = len(index) + 1
    return index


def render_footnotes(result: dict, chart: dict | None = None, *, level: int = 2) -> str:
    """口径注意事项 + 图表说明 + 数据限制。三者都不允许省略。"""
    hx = f"h{level}"
    blocks = []
    caveats = _caveat_index(result)
    if caveats:
        items = "".join(f"<li>[{idx}] {esc(text)}</li>" for text, idx in caveats.items())
        blocks.append(f"<{hx}>口径注意事项</{hx}><ol class=\"notes\">{items}</ol>")
    notes = list((chart or {}).get("notes") or [])
    if (chart or {}).get("reason"):
        notes.append(chart["reason"])
    limits = list(result.get("limitations") or [])
    if notes or limits:
        items = "".join(f"<li>{esc(text)}</li>" for text in notes + limits)
        blocks.append(f"<{hx}>数据与图表限制</{hx}><ul class=\"notes\">{items}</ul>")
    return "".join(blocks)


def render_provenance(result: dict, profile: dict | None = None, *,
                      level: int = 2) -> str:
    """数据来源与口径披露。LLM 只能引用这里的数字写结论，因此必须完整呈现。"""
    hx = f"h{level}"
    prov = result.get("provenance") or {}
    if not prov:
        return ""
    rows: list[tuple[str, str]] = []
    if prov.get("profile"):
        chain = "→".join(prov.get("profile_chain") or []) or prov["profile"]
        rows.append(("语义配置 profile", f"{prov['profile']}（继承链：{chain}）"))
    if prov.get("rows_in") is not None:
        rows.append(("原始行数 / 剔除后行数",
                     f"{prov.get('rows_in')} → {prov.get('rows_after_exclusion')}"))
    rows.append(("工单总量 / 本次口径内工单量",
                 f"{prov.get('order_count_total')} / {prov.get('order_count_scoped')}"))
    if prov.get("multi_node_orders"):
        rows.append(("多节点工单（已按工单去重）", str(prov["multi_node_orders"])))
    for name, count in (prov.get("exclusions") or {}).items():
        rows.append((f"剔除规则「{name}」剔除行数", str(count)))
    period = prov.get("period") or {}
    if period.get("label"):
        rows.append(("统计周期", period["label"]))
    if prov.get("filters"):
        rows.append(("筛选条件", "；".join(prov["filters"])))
    for label, key in (("未命中值域映射的取值", "unmapped_values"),
                       ("无法解析的时间取值", "unparsed_datetimes")):
        detail = prov.get(key) or {}
        if detail:
            rows.append((label, "；".join(f"{field}: {vals}"
                                          for field, vals in detail.items())))
    body = "".join(f'<tr><th scope="row">{esc(k)}</th><td>{esc(v)}</td></tr>'
                   for k, v in rows)
    return (f'<{hx}>数据来源与口径披露</{hx}><div class="prov"><table>'
            f'<caption>以下为本报告数字的取数与口径依据</caption>'
            f'<tbody>{body}</tbody></table></div>')


def render_chart_block(chart: dict | None, result: dict, chart_id: str = "wochart") -> str:
    """图表区。ECharts 模式内联本地脚本并把 SVG 放进 noscript 兜底。"""
    if not chart or chart.get("type") == "none" or not chart.get("svg"):
        return ""
    svg = chart["svg"]
    if chart.get("engine") == "echarts" and chart.get("echarts"):
        holder = f"{chart_id}-echarts"
        option = _js_inline(chart["echarts"]["option"])
        return (f'<figure><div id="{holder}" style="width:100%;height:420px"></div>'
                f'<noscript>{svg}</noscript>'
                f'<script>{chart["echarts"]["js"]}</script>'
                f'<script>(function(){{var el=document.getElementById("{holder}");'
                f'if(window.echarts&&el){{echarts.init(el).setOption('
                f'{option});}}else if(el){{el.innerHTML='
                f'{_js_string(svg)};}}}})();</script>'
                f'<figcaption>交互图表（ECharts 本地内联）。'
                f'完整数值见下方数据表。</figcaption></figure>')
    return (f'<figure>{svg}<figcaption>图表为内联 SVG，离线可用、可打印。'
            f'完整数值见下方数据表。</figcaption></figure>')


def _js_inline(text: str) -> str:
    """把 JSON 字面量安全内联进 `<script>`。工单取值会进入图表标签，只要出现
    `</script>` 就能提前闭合脚本块并注入代码；`<script` 也会让解析器进入
    转义态、改变闭合时机。因此把所有 `<` 转成 `\\u003c`（JSON 字符串里等价），
    U+2028/2029 在 JS 中是换行符会破坏字面量，一并转义。"""
    return (text.replace("<", "\\u003c")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _js_string(text: str) -> str:
    """把 SVG 安全嵌入 JS 字面量：闭合标签与引号都要处理，避免脚本被截断。"""
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
               .replace("</", "<\\/").replace("\n", "\\n"))
    return f'"{escaped}"'


# ---------------------------------------------------------------- 页面

def _result_parts(result: dict, spec: dict, profile: dict, *, chart: dict | None,
                  chart_id: str, engine: str | None,
                  level: int = 2) -> tuple[list[str], dict | None]:
    """单个查询结果的正文片段。`level` 控制小节标题层级，避免多段报告跳级。"""
    hx = f"h{level}"
    status = result.get("status", "ok")
    title = result.get("title") or "工单分析报告"
    parts = [render_echo(result, spec)]

    if status == "ok":
        if chart is None:
            chart = wo_chart.build_chart(spec, result, profile, chart_id=chart_id,
                                         engine=engine)
        cards = render_kpi_cards(result)
        if cards:
            parts.append(f"<{hx}>关键指标</{hx}>{cards}")
        block = render_chart_block(chart, result, chart_id)
        if block:
            parts.append(f"<{hx}>图表</{hx}>{block}")
        # 图表必须与数据表同时出现：图是摘要，表是可核对的证据。
        # 即使 Spec 关掉了表格，只要出了图就仍然附表，并在脚注说明原因。
        forced = bool(block) and result.get("show_table") is False
        if result.get("show_table", True) or block:
            parts.append(f"<{hx}>数据表</{hx}>"
                         + render_table(result, caption=title))
        if forced:
            result = dict(result)
            result["limitations"] = list(result.get("limitations") or []) + [
                "Spec 关闭了数据表，但图表必须附带可核对的数值，已强制展示数据表。"]
        parts.append(render_footnotes(result, chart, level=level))
    elif status == "empty":
        advice = (result.get("advice") or {}).get("text") or "当前条件下没有任何工单。"
        parts.append(f'<{hx}>没有匹配的数据</{hx}>'
                     f'<div class="alert"><pre>{esc(advice)}</pre></div>')
        parts.append(render_footnotes(result, None, level=level))
    else:
        messages = result.get("messages") or [i.get("message", "")
                                              for i in result.get("issues") or []]
        items = "".join(f"<li>{esc(text)}</li>" for text in messages)
        parts.append(f'<{hx}>该问题当前无法回答</{hx}><div class="alert">'
                     f'<ul class="notes">{items}</ul>'
                     f'<p>以上为拒答原因。本 skill 不会用近似指标顶替，也不会估算数值。</p>'
                     f'</div>')

    parts.append(render_provenance(result, profile, level=level))
    return parts, chart


def _document(title: str, parts: list[str]) -> str:
    """单文件自包含文档外壳：内联样式、零外链。"""
    return ("<!DOCTYPE html>\n"
            f'<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)}</title><style>{STYLE}</style></head>"
            f'<body><main class="wrap">{"".join(parts)}</main></body></html>')


def _foot(charts: list[dict | None]) -> str:
    engine_text = "内联 SVG" if not any((c or {}).get("echarts") for c in charts) \
        else "ECharts（本地内联）"
    return (f'<div class="foot">图表引擎：{esc(engine_text)}　'
            f'本报告为单文件自包含 HTML，无外部链接与追踪脚本，可离线打开与打印。'
            f'数字口径以「数据来源与口径披露」为准。</div>')


def render_html(result: dict, spec: dict | None = None, profile: dict | None = None, *,
                chart: dict | None = None, generated_at: str | None = None,
                engine: str | None = None, chart_id: str = "wochart") -> str:
    """渲染单文件 HTML。`chart` 缺省时按 profile 与 Spec 自动生成。"""
    spec = spec or {}
    profile = profile or {}
    title = result.get("title") or "工单分析报告"
    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts = [f"<h1>{esc(title)}</h1>"]
    profile_id = profile.get("id") or (result.get("provenance") or {}).get("profile") or "-"
    parts.append(f'<p class="sub">语义配置：{esc(profile_id)}　生成时间：{esc(stamp)}</p>')

    body, chart = _result_parts(result, spec, profile, chart=chart, chart_id=chart_id,
                                engine=engine, level=2)
    parts += body
    parts.append(_foot([chart]))
    return _document(title, parts)


def render_multi_html(run: dict, profile: dict | None = None, *,
                      generated_at: str | None = None,
                      engine: str | None = None) -> str:
    """把模板的多段结果渲染成**一个**自包含 HTML。

    每段都带自己的口径回显、图表、数据表与披露：多段共处一页不等于共用口径，
    某段拒答或为空也照原样展示，不用其他段的数字顶替。
    """
    profile = profile or {}
    title = run.get("display_name") or run.get("template_id") or "工单分析报告"
    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profile_id = profile.get("id") or "-"

    parts = [f"<h1>{esc(title)}</h1>",
             f'<p class="sub">语义配置：{esc(profile_id)}　'
             f'模板：{esc(run.get("template_id", "-"))}　生成时间：{esc(stamp)}</p>']
    variables = run.get("variables") or {}
    if variables:
        items = "".join(f"<li>{esc(k)}：{esc(_var_text(v))}</li>"
                        for k, v in variables.items())
        parts.append(f'<h2>模板入参</h2><ul class="notes">{items}</ul>')

    charts: list[dict | None] = []
    sections = run.get("sections") or []
    for idx, section in enumerate(sections, start=1):
        result = section.get("result") or {}
        heading = section.get("title") or result.get("title") or f"第 {idx} 段"
        parts.append(f"<h2>{idx}. {esc(heading)}</h2>")
        body, chart = _result_parts(result, section.get("spec") or {}, profile,
                                    chart=None, chart_id=f"wochart{idx}",
                                    engine=engine, level=3)
        parts += body
        charts.append(chart)

    parts.append(_foot(charts))
    return _document(title, parts)


def _var_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value)
    return str(value)
