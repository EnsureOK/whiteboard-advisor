"""积分服务:以 token 为最小记账单位(1 积分 = 2000 tokens)。

- plan 池:套餐月赠,plan 过期即清零(惰性结算)
- pack 池:积分包购买,永不过期
- 消耗顺序:plan 先、pack 后;单笔调用允许把 pack 池扣到小幅负值
  (LLM 调用结束才知道 usage),下一次入口拦截
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session as OrmSession

from app.db_models import CreditLedger, User

TOKENS_PER_CREDIT = 2000


def tokens_to_credits(tokens: int) -> float:
    return round(tokens / TOKENS_PER_CREDIT, 2)


def _plan_active(user: User) -> bool:
    if user.plan == "free" or not user.plan_expires_at:
        return False
    try:
        return datetime.fromisoformat(user.plan_expires_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _settle_expiry(db: OrmSession, user: User) -> None:
    """plan 过期后惰性清零 plan 池(记一笔负流水)。"""
    if user.credit_tokens_plan > 0 and not _plan_active(user):
        db.add(
            CreditLedger(
                user_id=user.id,
                delta_tokens=-user.credit_tokens_plan,
                source="plan_expire",
                ref=f"plan {user.plan} expired",
            )
        )
        user.credit_tokens_plan = 0
        db.commit()


def grant(db: OrmSession, user: User, tokens: int, *, source: str, ref: str = "") -> None:
    """发放积分。pack_grant/signup_grant 进 pack 池(不过期);
    plan_grant/redeem_grant 等进 plan 池(随套餐到期清零)。"""
    if tokens <= 0:
        return
    if source in ("pack_grant", "signup_grant"):
        user.credit_tokens_pack += tokens
    else:
        user.credit_tokens_plan += tokens
    db.add(CreditLedger(user_id=user.id, delta_tokens=tokens, source=source, ref=ref))
    db.commit()


def consume(db: OrmSession, user: User, tokens: int, *, ref: str = "") -> None:
    """按实际 usage 扣减:plan 池先,余下扣 pack 池(可小幅为负)。"""
    if tokens <= 0:
        return
    _settle_expiry(db, user)
    from_plan = min(user.credit_tokens_plan, tokens) if _plan_active(user) else 0
    user.credit_tokens_plan -= from_plan
    user.credit_tokens_pack -= tokens - from_plan
    db.add(CreditLedger(user_id=user.id, delta_tokens=-tokens, source="consume", ref=ref))
    db.commit()


def has_credits(db: OrmSession, user: User) -> bool:
    _settle_expiry(db, user)
    plan_ok = _plan_active(user) and user.credit_tokens_plan > 0
    return plan_ok or user.credit_tokens_pack > 0


def balance(db: OrmSession, user: User) -> dict:
    _settle_expiry(db, user)
    plan_tokens = user.credit_tokens_plan if _plan_active(user) else 0
    return {
        "planTokens": plan_tokens,
        "packTokens": user.credit_tokens_pack,
        "planCredits": tokens_to_credits(plan_tokens),
        "packCredits": tokens_to_credits(user.credit_tokens_pack),
        "totalCredits": tokens_to_credits(plan_tokens + max(0, user.credit_tokens_pack)),
    }


def month_consumed_tokens(db: OrmSession, user: User) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = (
        db.query(CreditLedger)
        .filter(
            CreditLedger.user_id == user.id,
            CreditLedger.source == "consume",
            CreditLedger.created_at >= month_start,
        )
        .all()
    )
    return -sum(r.delta_tokens for r in rows)
