"""工作台任务引擎:计划生成 + 分步执行。

设计:任务计划由若干步骤组成,前端逐步调用 /tasks/{id}/step 推进,
每步产生一条 TaskEvent(时间线);approval 类型步骤必须先确认才继续。
这样执行节奏完全由前端控制,矩阵工件可随步骤逐格填充,无需后台进程。

保单数据读真实 Policy 表(客户托管保单);无保单时矩阵给出提示而非编造。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session as OrmSession

from app.db_models import (
    Artifact,
    Client,
    Task,
    TaskEvent,
    utcnow_iso,
)
from app.services import kb

logger = logging.getLogger("whiteboard-advisor.task-engine")

# 快捷指令 -> 任务类型
QUICK_COMMANDS = {
    "检视保单": "policy_review",
    "生成方案": "generate_plan",
    "准备面谈": "prepare_visit",
    "写跟进": "followup",
}


# ---------- 计划生成 ----------

_PLANS: dict[str, list[dict]] = {
    "policy_review": [
        {"tool": "policy_db", "title": "调取客户档案与托管保单"},
        {"tool": "kb_search", "title": "检索产品条款与核保规则", "query": "保障责任 等待期 免赔额"},
        {"tool": "approval", "title": "确认检视范围(含体检报告授权)"},
        {"tool": "gap_calc", "title": "逐成员计算保障缺口"},
        {"tool": "compose", "title": "生成保单检视矩阵报告"},
    ],
    "generate_plan": [
        {"tool": "policy_db", "title": "调取客户画像与需求分析"},
        {"tool": "kb_search", "title": "检索可推荐产品与费用演示", "query": "产品 费率 利益演示"},
        {"tool": "approval", "title": "确认方案预算与偏好"},
        {"tool": "compose", "title": "生成保障方案书"},
    ],
    "prepare_visit": [
        {"tool": "policy_db", "title": "调取客户档案与历史接触"},
        {"tool": "kb_search", "title": "检索面谈话术与常见异议", "query": "面谈 异议处理 话术"},
        {"tool": "compose", "title": "生成面谈提纲"},
    ],
    "followup": [
        {"tool": "policy_db", "title": "调取最近接触与进行中事项"},
        {"tool": "compose", "title": "起草跟进信息"},
    ],
}


def build_plan(task_kind: str, client: Client, message: str) -> list[dict]:
    """生成计划步骤。LLM 可用时由模型细化,否则用模板计划。"""
    base = _PLANS.get(task_kind, _PLANS["followup"])
    return [dict(step) for step in base]


# ---------- 步骤执行 ----------

def _next_seq(db: OrmSession, task_id: str) -> int:
    events = db.query(TaskEvent).filter(TaskEvent.task_id == task_id).all()
    return (max((e.seq for e in events), default=0)) + 1


def add_event(db: OrmSession, task: Task, *, type_: str, title: str, status: str, payload: dict) -> TaskEvent:
    event = TaskEvent(
        task_id=task.id,
        seq=_next_seq(db, task.id),
        type=type_,
        title=title[:200],
        status=status,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _client_snapshot(db: OrmSession, client: Client) -> dict:
    members = [
        {
            "id": m.id,
            "name": m.name,
            "relation": m.relation,
            "badge": m.badge,
            "birthday": m.birthday,
            "notes": m.notes,
        }
        for m in client.members
    ]
    engagements = [
        {"kind": e.kind, "title": e.title, "line": e.line, "status": e.status}
        for e in client.engagements
        if e.status == "open"
    ]
    return {
        "client": {"id": client.id, "name": client.name, "type": client.client_type, "notes": client.notes},
        "members": members,
        "engagements": engagements,
    }


# ---------- 保障维度(按客户类型) ----------

PERSONAL_COLS = ["身故保障", "重疾保障", "医疗费用", "意外保障", "教育/养老现金流"]
COMPANY_COLS = ["企业财产", "雇主责任", "团体医疗", "公众/产品责任"]

# 险种 -> 检视维度(个人/家庭)
_LINE_TO_PERSONAL_COL = {
    "寿险": "身故保障",
    "定期寿险": "身故保障",
    "终身寿险": "身故保障",
    "重疾险": "重疾保障",
    "医疗险": "医疗费用",
    "百万医疗": "医疗费用",
    "团体医疗": "医疗费用",
    "意外险": "意外保障",
    "年金险": "教育/养老现金流",
    "教育金": "教育/养老现金流",
    "增额终身寿": "教育/养老现金流",
}

# 险种 -> 检视维度(企业)
_LINE_TO_COMPANY_COL = {
    "企财险": "企业财产",
    "财产一切险": "企业财产",
    "雇主责任险": "雇主责任",
    "团体医疗": "团体医疗",
    "团体意外": "雇主责任",
    "公众责任险": "公众/产品责任",
    "产品责任险": "公众/产品责任",
}

# 建议保额基线(演示值,元):正式版可按收入/负债/年龄推算
_PERSONAL_BASELINE = {
    "身故保障": 3_000_000,
    "重疾保障": 1_000_000,
    "医疗费用": 2_000_000,
    "意外保障": 2_000_000,
    "教育/养老现金流": 1_500_000,
}
_COMPANY_BASELINE = {
    "企业财产": 5_000_000,
    "雇主责任": 1_000_000,
    "团体医疗": 500_000,
    "公众/产品责任": 2_000_000,
}


def _load_policies(client: Client) -> dict:
    """读取托管保单(真实数据)。"""
    policies = [
        {
            "id": p.id,
            "memberId": p.member_id,
            "line": p.line,
            "productName": p.product_name,
            "insurer": p.insurer,
            "amount": p.amount,
            "premium": p.premium,
            "expiryDate": p.expiry_date,
            "status": p.status,
        }
        for p in client.policies
    ]
    return {"count": len(policies), "policies": policies}


def _run_policy_db(db: OrmSession, task: Task, client: Client) -> dict:
    return _client_snapshot(db, client) | {"policies": _load_policies(client)}


async def _run_kb_search(db: OrmSession, task: Task, client: Client, step: dict) -> dict:
    query = step.get("query") or task.title
    hits = await kb.search_async(db, query, client_id=client.id, top_k=4)
    return {"query": query, "hits": hits}


def _run_gap_calc(db: OrmSession, task: Task, client: Client) -> dict:
    """基于真实托管保单逐行计算保障缺口。

    个人/家庭:每位成员一行;企业:公司整体一行。
    未映射到维度的保单(车险/家财险等)进 extras 附注。
    """
    is_company = client.client_type == "company"
    cols = COMPANY_COLS if is_company else PERSONAL_COLS
    line_map = _LINE_TO_COMPANY_COL if is_company else _LINE_TO_PERSONAL_COL
    baseline = _COMPANY_BASELINE if is_company else _PERSONAL_BASELINE

    # 聚合: (row_key, col) -> 已有保额
    current: dict[tuple[str, str], int] = {}
    extras: list[dict] = []
    for p in client.policies:
        if p.status == "lapsed":
            continue
        col = line_map.get(p.line)
        if not col:
            extras.append({"line": p.line, "productName": p.product_name, "amount": p.amount, "status": p.status})
            continue
        row_key = "company" if is_company else (p.member_id or "unassigned")
        current[(row_key, col)] = current.get((row_key, col), 0) + p.amount

    rows = []
    if is_company:
        row_defs = [("company", client.name)]
    else:
        row_defs = [(m.id, m.name) for m in client.members]

    for row_key, row_name in row_defs:
        cells = {}
        for col in cols:
            have = current.get((row_key, col), 0)
            # 家庭内非第一位成员用 1/3 基线(演示简化;正式版按角色/收入推算)
            need = baseline[col]
            if not is_company and row_defs and row_key != row_defs[0][0]:
                need = need // 3
            gap = max(0, need - have)
            level = "ok" if gap == 0 else ("high" if gap > need * 0.5 else "mid")
            cells[col] = {
                "current": have,
                "need": need,
                "gap": gap,
                "level": level,
                "text": ("保障充足" if gap == 0 else f"缺口 {gap // 10000} 万"),
            }
        rows.append({"memberId": row_key, "member": row_name, "cells": cells})

    return {
        "rows": rows,
        "cols": cols,
        "extras": extras,
        "policyCount": len([p for p in client.policies if p.status != "lapsed"]),
    }


async def _run_compose(db: OrmSession, task: Task, client: Client) -> tuple[Artifact, dict]:
    gap = _run_gap_calc(db, task, client)
    if gap["policyCount"] == 0:
        summary = "该客户暂无托管保单,矩阵按零保障与建议基线计算;录入保单后重新检视可得到真实缺口。"
    else:
        summary = f"基于 {gap['policyCount']} 份托管保单与建议保额基线计算;基线为演示值,正式版按收入/负债/年龄推算。"
    content = {
        "kind": "review_matrix",
        "clientId": client.id,
        "cols": gap["cols"],
        "rows": gap["rows"],
        "extras": gap["extras"],
        "summary": summary,
        "generatedAt": utcnow_iso(),
    }
    # 同类型工件版本递增
    existing = db.query(Artifact).filter(Artifact.client_id == client.id).all()
    same_type = [a for a in existing if a.type == "review_matrix"]
    artifact = Artifact(
        client_id=client.id,
        task_id=task.id,
        type="review_matrix",
        title=f"{client.name}·保单检视矩阵",
        version=(max(a.version for a in same_type) + 1) if same_type else 1,
        content_json=json.dumps(content, ensure_ascii=False),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact, content


async def execute_step(db: OrmSession, task: Task) -> tuple[TaskEvent, bool]:
    """执行任务的下一个未完成步骤。

    返回 (event, awaiting_confirmation)。
    - 普通 step: running -> done
    - approval step: 状态 waiting_confirm,确认后由 confirm_event 推进为 confirmed
    - compose step: 额外生成/更新工件,event payload 带工件摘要
    """
    from app.services.workbench_store import parse_plan

    client = db.get(Client, task.client_id)
    plan = parse_plan(task.plan_json)

    events = db.query(TaskEvent).filter(TaskEvent.task_id == task.id).all()
    done_count = sum(1 for e in events if e.status in ("done", "confirmed"))

    if done_count >= len(plan):
        raise ValueError("no pending step")

    # 若上一步是 approval 且未确认 -> 阻塞
    last = max(events, key=lambda e: e.seq) if events else None
    if last and last.type == "approval" and last.status == "waiting_confirm":
        return last, True

    step = plan[done_count]
    tool = step.get("tool", "generic")
    title = step.get("title", tool)

    if tool == "approval":
        event = add_event(
            db, task, type_="approval", title=title, status="waiting_confirm",
            payload={"step": step, "hint": "涉及客户敏感信息(如体检报告),需经纪人确认授权后继续。"},
        )
        return event, True

    event = add_event(db, task, type_="tool", title=title, status="running", payload={"tool": tool})

    if tool == "policy_db":
        payload = _run_policy_db(db, task, client)
    elif tool == "kb_search":
        payload = await _run_kb_search(db, task, client, step)
    elif tool == "gap_calc":
        payload = _run_gap_calc(db, task, client)
    elif tool == "compose":
        artifact, content = await _run_compose(db, task, client)
        payload = {
            "artifact": {"id": artifact.id, "type": artifact.type, "title": artifact.title, "version": artifact.version},
            "summary": content.get("summary", ""),
        }
    else:
        payload = {"tool": tool}

    event.payload_json = json.dumps({"tool": tool, **payload}, ensure_ascii=False)
    event.status = "done"
    db.commit()
    db.refresh(event)
    return event, False


def confirm_event(db: OrmSession, task: Task, event_id: str) -> TaskEvent:
    event = db.get(TaskEvent, event_id)
    if not event or event.task_id != task.id:
        raise ValueError("event not found")
    if event.status != "waiting_confirm":
        raise ValueError("event not awaiting confirm")
    event.status = "confirmed"
    db.commit()
    db.refresh(event)
    return event
