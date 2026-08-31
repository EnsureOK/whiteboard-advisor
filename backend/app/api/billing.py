"""会员计费 API(本地适配器)。

套餐 / 当前权益 / 兑换码开通 / 订单列表。
生产可切换为纯 serverless: cloudbase/billing 云函数(同一套模型,
前端直连云函数 HTTP 入口,本模块仅作为开发与降级适配层)。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.db import get_db
from app.db_models import Order, RedeemCode, Task, User
from app.services import auth as auth_svc

router = APIRouter(prefix="/api/billing")

# 套餐定义(serverless 版同样以这份配置为单一事实来源)
PLANS = {
    "free": {"name": "免费版", "priceCents": 0, "taskQuota": 3, "days": 0, "features": ["3 次任务/月", "全局知识库", "基础规划"]},
    "pro": {"name": "专业版", "priceCents": 2900, "taskQuota": -1, "days": 30, "features": ["无限任务", "家庭私有知识库", "会员专属模板", "优先客服"]},
    "pro_year": {"name": "专业版(年付)", "priceCents": 26800, "taskQuota": -1, "days": 365, "features": ["无限任务", "家庭私有知识库", "会员专属模板", "优先客服", "年付 8.5 折"]},
}


class RedeemBody(BaseModel):
    code: str


class CreateOrderBody(BaseModel):
    plan: str
    channel: str = "wechat_pay"  # wechat_pay 预留;当前演示环境直接标记已支付


def _plan_active(user: User) -> bool:
    if user.plan == "free":
        return False
    if not user.plan_expires_at:
        return True
    try:
        return datetime.fromisoformat(user.plan_expires_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def billing_status(db: OrmSession, user: User) -> dict:
    active = _plan_active(user)
    quota = PLANS.get(user.plan, PLANS["free"])["taskQuota"] if active else PLANS["free"]["taskQuota"]
    used = (
        db.query(Task)
        .filter(Task.created_by == user.id, Task.created_at >= _month_start().isoformat())
        .count()
    )
    plan_key = user.plan if active else "free"
    plan = PLANS.get(plan_key, PLANS["free"])
    return {
        "plan": plan_key,
        "planName": plan["name"],
        "features": plan["features"],
        "active": active,
        "planExpiresAt": user.plan_expires_at if active else None,
        "taskQuota": quota,          # -1 = 无限
        "taskUsed": used,
        "taskRemaining": None if quota == -1 else max(0, quota - used),
    }


@router.get("/plans")
async def plans() -> dict:
    return {"plans": [{"id": k, **v} for k, v in PLANS.items()]}


@router.get("/status")
async def status(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    return billing_status(db, user)


@router.post("/redeem")
async def redeem(body: RedeemBody, user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    code = body.code.strip().upper()
    rc = db.get(RedeemCode, code)
    if not rc or rc.status != "unused":
        raise HTTPException(404, "兑换码无效或已被使用")
    return _activate(db, user, rc.plan, rc.days, "redeem", {"code": code}, rc)


@router.post("/orders")
async def create_order(body: CreateOrderBody, user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    """下单。channel=wechat_pay 时返回待支付订单(演示环境直接标记已支付)。

    serverless 生产版:该动作由云函数生成微信支付统一下单参数,
    支付回调在云函数里验签后标记 paid 并延长会员。
    """
    plan = PLANS.get(body.plan)
    if not plan or body.plan == "free":
        raise HTTPException(400, "无效套餐")
    order = Order(
        user_id=user.id,
        plan=body.plan,
        amount_cents=plan["priceCents"],
        channel=body.channel,
        status="paid" if body.channel == "wechat_pay" else "created",
        meta_json=json.dumps({"demo": True}, ensure_ascii=False),
        paid_at=None,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    if body.channel == "wechat_pay":
        _grant_plan(db, user, body.plan, plan["days"])
        order.paid_at = datetime.now(timezone.utc).isoformat()
        db.commit()
    return {
        "order": {
            "id": order.id,
            "plan": order.plan,
            "amountCents": order.amount_cents,
            "channel": order.channel,
            "status": order.status,
        },
        "payParams": None,  # 生产版:微信支付 JSAPI/Native 参数
    }


@router.get("/orders")
async def list_orders(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()
    return [
        {
            "id": o.id,
            "plan": o.plan,
            "amountCents": o.amount_cents,
            "channel": o.channel,
            "status": o.status,
            "createdAt": o.created_at,
            "paidAt": o.paid_at,
        }
        for o in rows
    ]


def _activate(db: OrmSession, user: User, plan: str, days: int, channel: str, meta: dict, rc: Optional[RedeemCode] = None) -> dict:
    order = Order(
        user_id=user.id, plan=plan, amount_cents=0, channel=channel, status="paid",
        meta_json=json.dumps(meta, ensure_ascii=False), paid_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(order)
    _grant_plan(db, user, plan, days)
    if rc:
        rc.status = "used"
        rc.used_by = user.id
        rc.used_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return {"ok": True, "order": {"id": order.id}, "status": billing_status(db, user)}


def _grant_plan(db: OrmSession, user: User, plan: str, days: int) -> None:
    user.plan = plan
    base = datetime.now(timezone.utc)
    if user.plan_expires_at:
        try:
            prev = datetime.fromisoformat(user.plan_expires_at)
            if prev > base:
                base = prev
        except ValueError:
            pass
    user.plan_expires_at = (base + timedelta(days=days)).isoformat()
    db.commit()
    db.refresh(user)
