"""会员计费 API:积分(按 token 计量) + 月付三档 + 积分包 + Stripe(支付宝/微信)。

- 月付 = 一次性支付买 30 天权益 + 当期积分(Stripe 上微信不支持自动扣款
  订阅,支付宝受限,故不做 recurring;到期提醒续费)
- 积分记账见 services/credits.py(1 积分 = 2000 tokens)
- Stripe: Checkout Session(mode=payment, cny) + webhook 履约,
  alipay/wechat_pay 为异步支付方式,须处理 async_payment_succeeded
- 无 STRIPE_API_KEY 时下单走演示通道(直接标记已支付),开发不阻塞
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db import get_db
from app.db_models import CreditLedger, Order, RedeemCode, User
from app.services import auth as auth_svc
from app.services import credits

logger = logging.getLogger("whiteboard-advisor.billing")

router = APIRouter(prefix="/api/billing")

# 1 积分 = 2000 tokens;套餐月赠以积分定义,换算为 token 入账
CREDIT = credits.TOKENS_PER_CREDIT

# 套餐(单一事实来源;月付=买 30 天)
PLANS = {
    "free": {
        "name": "免费版", "priceCents": 0, "days": 0, "monthlyCredits": 0,
        "features": ["注册赠 2,000 积分", "全局知识库", "基础对话与任务"],
    },
    "basic": {
        "name": "基础版", "priceCents": 5900, "days": 30, "monthlyCredits": 10_000,
        "features": ["每月 10,000 积分(约 2 千万 token)", "客户私有知识库", "文档生成(Word/PPT/Excel)"],
    },
    "pro": {
        "name": "专业版", "priceCents": 9900, "days": 30, "monthlyCredits": 30_000,
        "features": ["每月 30,000 积分(基础版 3 倍)", "客户私有知识库", "文档生成", "优先客服"],
    },
    "max": {
        "name": "旗舰版", "priceCents": 19900, "days": 30, "monthlyCredits": 80_000,
        "features": ["每月 80,000 积分(基础版 8 倍)", "全部专业版权益", "新功能优先体验"],
    },
}

# 积分包(不过期,单价高于月付以激励订阅)
PACKS = {
    "pack_s": {"name": "积分包·小", "priceCents": 990, "credits": 1_200},
    "pack_m": {"name": "积分包·大", "priceCents": 2900, "credits": 4_000},
}

SIGNUP_GRANT_CREDITS = 2_000


class RedeemBody(BaseModel):
    code: str


class CheckoutBody(BaseModel):
    # 商品: basic/pro/max 或 pack_s/pack_m
    item: str


def _plan_active(user: User) -> bool:
    if user.plan == "free":
        return False
    if not user.plan_expires_at:
        return True
    try:
        return datetime.fromisoformat(user.plan_expires_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _welcome_claimed(db: OrmSession, user: User) -> bool:
    return (
        db.query(CreditLedger)
        .filter(CreditLedger.user_id == user.id, CreditLedger.source == "signup_grant")
        .first()
        is not None
    )


def billing_status(db: OrmSession, user: User) -> dict:
    active = _plan_active(user)
    plan_key = user.plan if active else "free"
    plan = PLANS.get(plan_key, PLANS["free"])
    bal = credits.balance(db, user)
    return {
        "plan": plan_key,
        "planName": plan["name"],
        "features": plan["features"],
        "active": active,
        "planExpiresAt": user.plan_expires_at if active else None,
        "credits": bal,
        "monthConsumedCredits": credits.tokens_to_credits(credits.month_consumed_tokens(db, user)),
        "hasCredits": credits.has_credits(db, user),
        "welcomeClaimed": _welcome_claimed(db, user),
        "welcomeCredits": SIGNUP_GRANT_CREDITS,
    }


@router.get("/plans")
async def plans() -> dict:
    return {
        "plans": [{"id": k, **v} for k, v in PLANS.items()],
        "packs": [{"id": k, **v} for k, v in PACKS.items()],
        "tokensPerCredit": CREDIT,
        "stripe": bool(settings.stripe_api_key),
    }


@router.get("/status")
async def status(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    return billing_status(db, user)


@router.get("/ledger")
async def ledger(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(CreditLedger)
        .filter(CreditLedger.user_id == user.id)
        .order_by(CreditLedger.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "deltaCredits": credits.tokens_to_credits(r.delta_tokens),
            "source": r.source,
            "ref": r.ref,
            "createdAt": r.created_at,
        }
        for r in rows
    ]


@router.post("/claim-welcome")
async def claim_welcome(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    """登录后领取免费积分(一次性,幂等)。"""
    already = _welcome_claimed(db, user)
    if not already:
        grant_signup_bonus(db, user)
    return {
        "claimed": not already,
        "alreadyClaimed": already,
        "status": billing_status(db, user),
    }


@router.post("/redeem")
async def redeem(body: RedeemBody, user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> dict:
    code = body.code.strip().upper()
    rc = db.get(RedeemCode, code)
    if not rc or rc.status != "unused":
        raise HTTPException(404, "兑换码无效或已被使用")
    plan_key = rc.plan if rc.plan in PLANS else "basic"
    order = Order(
        user_id=user.id, plan=plan_key, amount_cents=0, channel="redeem", status="paid",
        meta_json=json.dumps({"code": code}, ensure_ascii=False),
        paid_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(order)
    _fulfill_plan(db, user, plan_key, rc.days, order_ref=f"redeem:{code}")
    rc.status = "used"
    rc.used_by = user.id
    rc.used_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return {"ok": True, "status": billing_status(db, user)}


# ---------- Stripe Checkout(支付宝/微信) ----------

def _item_def(item: str) -> tuple[str, dict]:
    if item in PLANS and item != "free":
        return "plan", PLANS[item]
    if item in PACKS:
        return "pack", PACKS[item]
    raise HTTPException(400, "无效商品")


@router.post("/checkout")
async def create_checkout(
    body: CheckoutBody,
    request: Request,
    user: User = Depends(auth_svc.get_current_user),
    db: OrmSession = Depends(get_db),
) -> dict:
    """创建支付:有 Stripe key 时开 Checkout Session(支付宝/微信在
    Dashboard 配置,不传 payment_method_types);无 key 时演示通道直接履约。"""
    kind, item = _item_def(body.item)

    order = Order(
        user_id=user.id,
        plan=body.item,
        amount_cents=item["priceCents"],
        channel="stripe" if settings.stripe_api_key else "demo",
        status="created",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    if not settings.stripe_api_key:
        # 演示通道:直接标记已支付并履约
        _fulfill_order(db, order)
        return {"orderId": order.id, "checkoutUrl": None, "demo": True, "status": billing_status(db, user)}

    from stripe import StripeClient

    client = StripeClient(settings.stripe_api_key)
    origin = request.headers.get("origin") or "http://localhost:5173"
    session = client.checkout.sessions.create(
        params={
            "mode": "payment",
            "line_items": [
                {
                    "price_data": {
                        "currency": "cny",
                        "unit_amount": item["priceCents"],
                        "product_data": {"name": item["name"]},
                    },
                    "quantity": 1,
                }
            ],
            # 不传 payment_method_types:由 Dashboard 配置动态展示 alipay/wechat_pay
            "success_url": f"{origin}/?view=workbench&pay=success",
            "cancel_url": f"{origin}/?view=workbench&pay=cancel",
            "client_reference_id": order.id,
            "metadata": {"orderId": order.id, "userId": user.id, "item": body.item, "kind": kind},
            "integration_identifier": "workbench-billing-kqzmwvxa",
        }
    )
    order.stripe_session_id = session.id
    db.commit()
    return {"orderId": order.id, "checkoutUrl": session.url, "demo": False}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: OrmSession = Depends(get_db)) -> dict:
    """Stripe 回调:alipay/wechat_pay 为异步支付,completed 时可能仍 unpaid,
    须在 async_payment_succeeded 履约;按订单状态幂等。"""
    if not settings.stripe_api_key:
        raise HTTPException(404, "stripe not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    import stripe as stripe_mod

    try:
        if settings.stripe_webhook_secret:
            # 仅验签;数据处理统一用原始 JSON(SDK 对象在不同大版本行为不一)
            stripe_mod.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
        event = json.loads(payload)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid webhook: {e}")

    etype = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    order_id = (data.get("metadata") or {}).get("orderId")

    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if data.get("payment_status") != "unpaid" and order_id:
            order = db.get(Order, order_id)
            if order and order.status != "paid":
                _fulfill_order(db, order)
                logger.info("stripe fulfilled order %s (%s)", order_id, etype)
    elif etype == "checkout.session.async_payment_failed":
        if order_id:
            order = db.get(Order, order_id)
            if order and order.status != "paid":
                order.status = "failed"
                db.commit()
    return {"received": True}


@router.get("/orders")
async def list_orders(user: User = Depends(auth_svc.get_current_user), db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()
    names = {**{k: v["name"] for k, v in PLANS.items()}, **{k: v["name"] for k, v in PACKS.items()}}
    return [
        {
            "id": o.id,
            "item": o.plan,
            "itemName": names.get(o.plan, o.plan),
            "amountCents": o.amount_cents,
            "channel": o.channel,
            "status": o.status,
            "createdAt": o.created_at,
            "paidAt": o.paid_at,
        }
        for o in rows
    ]


# ---------- 履约 ----------

def _fulfill_order(db: OrmSession, order: Order) -> None:
    """标记已支付并发放权益(幂等由调用方按 status 保证)。"""
    user = db.get(User, order.user_id)
    if not user:
        return
    kind, item = _item_def(order.plan)
    if kind == "plan":
        _fulfill_plan(db, user, order.plan, item["days"], order_ref=f"order:{order.id}")
    else:
        credits.grant(db, user, item["credits"] * CREDIT, source="pack_grant", ref=f"order:{order.id}")
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc).isoformat()
    db.commit()


def _fulfill_plan(db: OrmSession, user: User, plan_key: str, days: int, *, order_ref: str) -> None:
    """升级套餐:延长有效期 + 发当期积分(plan 池)。"""
    plan = PLANS[plan_key]
    user.plan = plan_key
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
    if plan["monthlyCredits"]:
        credits.grant(db, user, plan["monthlyCredits"] * CREDIT, source="plan_grant", ref=order_ref)


def grant_signup_bonus(db: OrmSession, user: User) -> None:
    """注册赠礼(pack 池,不过期);幂等:已有 signup_grant 流水则跳过。"""
    existing = (
        db.query(CreditLedger)
        .filter(CreditLedger.user_id == user.id, CreditLedger.source == "signup_grant")
        .first()
    )
    if existing:
        return
    credits.grant(db, user, SIGNUP_GRANT_CREDITS * CREDIT, source="signup_grant", ref="welcome")
