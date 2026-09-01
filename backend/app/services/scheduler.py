"""内建定时作业:到期扫描 + 每日简报(按日幂等,进程内 asyncio 循环)。

- expiry_scan(每天一次): 30 天内到期的保单 -> 自动建续期事项 + 待办
- daily_briefing(每天 08:30 后首个 tick;当天首次启动也会补跑):
  汇总今日待办/7 天内到期/理赔中/今日应联系 -> LLM 成文(模板兜底)
  -> 存 DailyBriefing;配置企微群机器人 webhook 时推送

WB_DISABLE_SCHEDULER=1 可关闭(测试环境)。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from app.db_models import (
    Client,
    DailyBriefing,
    Engagement,
    Policy,
    SchedulerRun,
    Todo,
)

logger = logging.getLogger("whiteboard-advisor.scheduler")

BRIEFING_AFTER_HOUR = 8
BRIEFING_AFTER_MINUTE = 30
EXPIRY_WINDOW_DAYS = 30


def _today() -> str:
    return date.today().isoformat()


def _ran(db: OrmSession, job: str, run_date: str) -> bool:
    return (
        db.query(SchedulerRun)
        .filter(SchedulerRun.job == job, SchedulerRun.run_date == run_date)
        .first()
        is not None
    )


def _mark(db: OrmSession, job: str, run_date: str, detail: str = "") -> None:
    db.add(SchedulerRun(job=job, run_date=run_date, detail=detail[:200]))
    db.commit()


# ---------- 到期扫描 ----------

def scan_expiring(db: OrmSession) -> int:
    """30 天内到期且无进行中续期事项的保单:建续期事项+待办,标记待续期。"""
    horizon = (date.today() + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    today = _today()
    created = 0
    policies = (
        db.query(Policy)
        .filter(
            Policy.expiry_date.isnot(None),
            Policy.expiry_date <= horizon,
            Policy.expiry_date >= today,
            Policy.status.in_(("active", "pending_renewal")),
        )
        .all()
    )
    for p in policies:
        has_renewal = (
            db.query(Engagement)
            .filter(
                Engagement.policy_id == p.id,
                Engagement.kind == "renewal",
                Engagement.status == "open",
            )
            .first()
        )
        if has_renewal:
            continue
        client = db.get(Client, p.client_id)
        if not client:
            continue
        days_left = (date.fromisoformat(p.expiry_date) - date.today()).days
        db.add(
            Engagement(
                client_id=p.client_id,
                kind="renewal",
                title=f"{p.line}《{p.product_name or '未名产品'}》续期跟进",
                line=p.line,
                policy_id=p.id,
                note=f"{p.expiry_date} 到期(剩 {days_left} 天),自动创建",
            )
        )
        db.add(
            Todo(
                title=f"【自动】{client.name} {p.line}保单 {days_left} 天后到期",
                detail=f"《{p.product_name or '未名产品'}》{p.expiry_date} 到期,确认续保意愿与缴费安排。",
                priority="high" if days_left <= 7 else "normal",
                client_id=p.client_id,
            )
        )
        if p.status == "active":
            p.status = "pending_renewal"
        created += 1
    db.commit()
    return created


# ---------- 每日简报 ----------

def _collect_briefing_facts(db: OrmSession) -> dict:
    today = _today()
    week = (date.today() + timedelta(days=7)).isoformat()
    todos = db.query(Todo).filter(Todo.status == "open").all()
    expiring = (
        db.query(Policy)
        .filter(Policy.expiry_date.isnot(None), Policy.expiry_date <= week, Policy.expiry_date >= today)
        .all()
    )
    claims = (
        db.query(Engagement)
        .filter(Engagement.kind == "claim", Engagement.status == "open")
        .all()
    )
    contact_today = db.query(Client).filter(Client.next_contact == today).all()
    client_name = {c.id: c.name for c in db.query(Client).all()}
    return {
        "todos": [f"{client_name.get(t.client_id, '')} {t.title}".strip() for t in todos],
        "expiring": [
            f"{client_name.get(p.client_id, '')} {p.line}《{p.product_name}》{p.expiry_date} 到期"
            for p in expiring
        ],
        "claims": [f"{client_name.get(e.client_id, '')} {e.title}" for e in claims],
        "contactToday": [c.name for c in contact_today],
    }


def _briefing_template(facts: dict) -> str:
    lines = [f"**今日工作简报** {_today()}"]
    if facts["expiring"]:
        lines.append("\n**7 天内到期**")
        lines += [f"- {x}" for x in facts["expiring"]]
    if facts["claims"]:
        lines.append("\n**理赔跟进中**")
        lines += [f"- {x}" for x in facts["claims"]]
    if facts["contactToday"]:
        lines.append("\n**今日应联系**")
        lines += [f"- {x}" for x in facts["contactToday"]]
    if facts["todos"]:
        lines.append("\n**待办**")
        lines += [f"- {x}" for x in facts["todos"][:8]]
    if len(lines) == 1:
        lines.append("\n今天没有到期、理赔或待办事项,可以主动做几次客户关怀。")
    return "\n".join(lines)


async def _briefing_llm(facts: dict) -> str | None:
    from app.config import settings

    if not settings.has_llm:
        return None
    try:
        import json as _json

        from app.services.agent import load_soul
        from app.services.llm import _call_qianfan

        prompt = (
            f"{load_soul()}\n\n"
            f"任务: 为经纪人写一份今日工作简报(markdown,150-250 字)。今天是 {_today()}。\n"
            f"事实数据: {_json.dumps(facts, ensure_ascii=False)}\n"
            "要求: 先一句话概括今天的重点,再分组列出(到期/理赔/应联系/待办,空组不列),"
            '给出 1 条最优先的行动建议。输出 JSON: {"briefing": "简报正文 markdown"}'
        )
        raw, _usage = await _call_qianfan([{"role": "user", "content": prompt}], settings.model_fast)
        # _call_qianfan 走 json_object 模式:取 JSON 里的正文;解析失败按纯文本
        text = raw.strip()
        try:
            data = _json.loads(text)
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str) and v.strip():
                        text = v.strip()
                        break
        except (ValueError, TypeError):
            pass
        return text[:2000] or None
    except Exception as e:  # noqa: BLE001
        logger.warning("briefing llm failed: %s", e)
        return None


async def generate_briefing(db: OrmSession) -> str:
    facts = _collect_briefing_facts(db)
    content = (await _briefing_llm(facts)) or _briefing_template(facts)
    today = _today()
    row = db.query(DailyBriefing).filter(DailyBriefing.date == today).first()
    if row:
        row.content = content
    else:
        db.add(DailyBriefing(date=today, content=content))
    db.commit()

    # 企微群机器人推送(配置了 webhook 才发)
    try:
        from app.services import wecom

        await wecom.push_markdown(content)
    except Exception as e:  # noqa: BLE001 推送失败不影响简报本身
        logger.warning("wecom push failed: %s", e)
    return content


# ---------- 主循环 ----------

async def tick() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        today = _today()
        if not _ran(db, "expiry_scan", today):
            n = scan_expiring(db)
            _mark(db, "expiry_scan", today, f"created={n}")
            if n:
                logger.info("expiry_scan created %s renewal items", n)
        now = datetime.now()
        after_time = now.hour > BRIEFING_AFTER_HOUR or (
            now.hour == BRIEFING_AFTER_HOUR and now.minute >= BRIEFING_AFTER_MINUTE
        )
        if after_time and not _ran(db, "daily_briefing", today):
            await generate_briefing(db)
            _mark(db, "daily_briefing", today)
            logger.info("daily briefing generated")
    finally:
        db.close()


async def scheduler_loop() -> None:
    logger.info("scheduler started")
    while True:
        try:
            await tick()
        except Exception as e:  # noqa: BLE001 循环不能死
            logger.warning("scheduler tick failed: %s", e)
        await asyncio.sleep(60)


def enabled() -> bool:
    return os.environ.get("WB_DISABLE_SCHEDULER") != "1"
