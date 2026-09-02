"""客户保障可视化报告:确定性组装(零 LLM、零积分、零幻觉)。

数据源:托管保单(Policy)+ 任务引擎的缺口计算(_run_gap_calc)。
渲染走 report_html.render_multi_html(单文件自包含 HTML,内联 SVG 图表)。
段落结构按引擎的 ResultSet 契约构造:rows 以维度/指标 id 为键的扁平 dict。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session as OrmSession

from app.db_models import Client
from app.services import report_html
from app.services.chartkit import FMT_INT, FMT_MONEY

ECHO_BASE = "仅统计状态非失效(lapsed)的托管保单;金额单位万元(医疗险为报销额度)。"


def _col(key: str, label: str, kind: str, fmt: str = "") -> dict:
    c = {"key": key, "label": label, "kind": kind}
    if fmt:
        c["fmt"] = fmt
    return c


def _section(title: str, echo: str, columns: list[dict], rows: list[dict],
             totals: dict, dims: list[str], metrics: list[str], chart: str,
             show_table: bool = True, limitations: list[str] | None = None) -> dict:
    return {
        "title": title,
        "spec": {"dimensions": dims, "metrics": metrics, "output": {"title": title, "chart": chart}},
        "result": {
            "status": "ok",
            "title": title,
            "echo": echo,
            "columns": columns,
            "rows": rows,
            "totals": totals,
            "limitations": limitations or [],
            "show_table": show_table,
        },
    }


def build_coverage_report(db: OrmSession, client: Client) -> tuple[str, dict]:
    """组装四段保障报告,返回 (title, artifact_content)。"""
    active = [p for p in client.policies if p.status != "lapsed"]
    is_company = client.client_type == "company"

    from app.services import task_engine

    gap = task_engine._run_gap_calc(db, None, client)  # noqa: SLF001 复用确定性计算

    # ---- 1. 保障总览(KPI) ----
    total_amount_wan = round(sum(p.amount for p in active) / 10000, 1)
    total_premium = sum(p.premium for p in active)
    total_gap_wan = round(
        sum(cell["gap"] for row in gap["rows"] for cell in row["cells"].values()) / 10000, 1
    )
    overview = _section(
        "保障总览", ECHO_BASE,
        columns=[
            _col("amount_wan", "在保总保额(万元)", "metric", FMT_MONEY),
            _col("premium_yuan", "年缴保费(元)", "metric", FMT_MONEY),
            _col("count", "在保保单件数", "metric", FMT_INT),
            _col("gap_wan", "缺口(万元)", "metric", FMT_MONEY),
        ],
        rows=[], totals={
            "amount_wan": total_amount_wan,
            "premium_yuan": total_premium,
            "count": len(active),
            "gap_wan": total_gap_wan,
        },
        dims=[], metrics=[], chart="none", show_table=False,
        limitations=(["暂无托管保单,以下缺口按建议基线全额计算。"] if not active else None),
    )

    # ---- 2. 成员×维度 缺口构成(堆叠柱) ----
    who = "公司" if is_company else "成员"
    gap_rows = []
    for row in gap["rows"]:
        for col_name, cell in row["cells"].items():
            if cell["gap"] > 0:
                gap_rows.append({
                    "member": row["member"],
                    "dimension": col_name,
                    "gap_wan": round(cell["gap"] / 10000, 1),
                })
    gap_section = _section(
        f"{who}保障缺口构成", ECHO_BASE + " 缺口 = 建议基线 - 已有保额,充足维度不计。",
        columns=[
            _col("member", who, "dimension"),
            _col("dimension", "保障维度", "dimension"),
            _col("gap_wan", "缺口(万元)", "metric", FMT_MONEY),
        ],
        rows=gap_rows, totals={"gap_wan": total_gap_wan},
        dims=["member", "dimension"], metrics=["gap_wan"], chart="stacked_bar",
        limitations=(["各维度保障均已达到建议基线。"] if not gap_rows else None),
    )

    # ---- 3. 保额构成(按险种,横向条) ----
    by_line: dict[str, int] = {}
    for p in active:
        by_line[p.line] = by_line.get(p.line, 0) + p.amount
    line_rows = [
        {"line": line, "amount_wan": round(amt / 10000, 1)}
        for line, amt in sorted(by_line.items(), key=lambda kv: kv[1], reverse=True)
    ]
    line_section = _section(
        "在保保额构成(按险种)", ECHO_BASE,
        columns=[
            _col("line", "险种", "dimension"),
            _col("amount_wan", "保额(万元)", "metric", FMT_MONEY),
        ],
        rows=line_rows, totals={"amount_wan": total_amount_wan},
        dims=["line"], metrics=["amount_wan"], chart="bar_h",
        limitations=(["医疗险为报销额度上限,与给付型保额不可直接相加,构成仅作示意。"]
                     if any(p.line == "医疗险" for p in active) else None),
    )

    # ---- 4. 未来到期/续期分布(按年) ----
    this_year = datetime.now(timezone.utc).year
    by_year: dict[str, dict[str, int]] = {}
    for p in active:
        if not p.expiry_date:
            continue
        year = p.expiry_date[:4]
        if not year.isdigit() or not (this_year <= int(year) <= this_year + 5):
            continue
        agg = by_year.setdefault(year, {"count": 0, "premium_yuan": 0})
        agg["count"] += 1
        agg["premium_yuan"] += p.premium
    year_rows = [
        {"year": y, "count": v["count"], "premium_yuan": v["premium_yuan"]}
        for y, v in sorted(by_year.items())
    ]
    year_section = _section(
        "未来 5 年到期/续期分布", "按保单 expiry_date 所在年份统计;长期险无固定到期日不计入。",
        columns=[
            _col("year", "年份", "dimension"),
            _col("count", "到期件数", "metric", FMT_INT),
            _col("premium_yuan", "涉及年缴保费(元)", "metric", FMT_MONEY),
        ],
        rows=year_rows,
        totals={"count": sum(r["count"] for r in year_rows),
                "premium_yuan": sum(r["premium_yuan"] for r in year_rows)},
        dims=["year"], metrics=["count"], chart="bar",
        limitations=(["未来 5 年内无到期保单。"] if not year_rows else None),
    )

    title = f"{client.name}·保障图表报告"
    generated_at = datetime.now(timezone.utc).isoformat()
    run = {
        "display_name": title,
        "template_id": "coverage-report",
        "sections": [overview, gap_section, line_section, year_section],
    }
    html = report_html.render_multi_html(
        run, profile={"id": "workbench"},
        generated_at=generated_at[:19].replace("T", " "),
    )
    summary = (
        f"在保 {len(active)} 件、总保额 {total_amount_wan:g} 万、年缴保费 {total_premium:,} 元;"
        + (f"保障缺口合计 {total_gap_wan:g} 万。" if total_gap_wan else "各维度保障达到建议基线。")
    )
    content = {
        "kind": "chart_report",
        "clientId": client.id,
        "html": html,
        "summary": summary,
        "generatedAt": generated_at,
    }
    return title, content
