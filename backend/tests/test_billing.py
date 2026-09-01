"""积分计费测试:消耗顺序 / 过期清零 / 履约幂等 / 下单演示通道 / 402 拦截。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.db_models import Order, User
from app.services import credits
from app.services import embedding as emb


@pytest.fixture()
def test_db(monkeypatch):
    monkeypatch.setattr(emb, "has_embedding_api", lambda: False)
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def api_client(test_db):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _user(db, plan="free", expires_days=None) -> User:
    u = User(username=f"u{id(db) % 10000}", password_hash="x", plan=plan)
    if expires_days is not None:
        u.plan_expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_consume_order_plan_then_pack(test_db):
    u = _user(test_db, plan="basic", expires_days=30)
    credits.grant(test_db, u, 10_000, source="plan_grant")
    credits.grant(test_db, u, 5_000, source="pack_grant")

    credits.consume(test_db, u, 8_000, ref="t1")
    assert u.credit_tokens_plan == 2_000 and u.credit_tokens_pack == 5_000

    # plan 池不够,余下扣 pack;pack 可小幅为负(调用结束才知道 usage)
    credits.consume(test_db, u, 8_000, ref="t2")
    assert u.credit_tokens_plan == 0 and u.credit_tokens_pack == -1_000
    assert not credits.has_credits(test_db, u)


def test_plan_expiry_clears_plan_pool(test_db):
    u = _user(test_db, plan="basic", expires_days=-1)  # 已过期
    u.credit_tokens_plan = 9_000
    u.credit_tokens_pack = 1_000
    test_db.commit()
    bal = credits.balance(test_db, u)
    assert bal["planTokens"] == 0 and u.credit_tokens_plan == 0
    assert bal["packTokens"] == 1_000
    assert credits.has_credits(test_db, u)  # pack 池仍可用


def test_sms_login_claim_and_demo_checkout(api_client, test_db, monkeypatch):
    # 与本机 .env 解耦:强制走演示通道
    from app.config import settings as app_settings
    from app.db_models import SmsCode

    monkeypatch.setattr(app_settings, "stripe_api_key", "")

    # 手机号验证码登录(outbox 通道):发送 → 从库里取码 → 校验 → 自动建号
    r = api_client.post("/api/auth/sms/send", json={"phone": "13800001111"})
    assert r.status_code == 200 and r.json()["provider"] == "outbox"
    # 冷却期内重复发送被拒
    assert api_client.post("/api/auth/sms/send", json={"phone": "13800001111"}).status_code == 429
    code = (
        test_db.query(SmsCode)
        .filter(SmsCode.phone == "13800001111", SmsCode.status == "pending")
        .first()
        .code
    )
    bad = api_client.post("/api/auth/sms/verify", json={"phone": "13800001111", "code": "000000"})
    assert bad.status_code == 401
    r = api_client.post("/api/auth/sms/verify", json={"phone": "13800001111", "code": code})
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["user"]["username"] == "13800001111"
    headers = {"Authorization": f"Bearer {token}"}

    # 免费积分:登录后显式领取,幂等
    st = api_client.get("/api/billing/status", headers=headers).json()
    assert st["welcomeClaimed"] is False and st["credits"]["packCredits"] == 0
    r = api_client.post("/api/billing/claim-welcome", headers=headers)
    assert r.json()["claimed"] is True
    r = api_client.post("/api/billing/claim-welcome", headers=headers)
    assert r.json()["claimed"] is False and r.json()["alreadyClaimed"] is True
    st = api_client.get("/api/billing/status", headers=headers).json()
    assert st["welcomeClaimed"] is True
    assert st["credits"]["packCredits"] == 2000.0  # 新用户礼

    # 演示通道(未配 stripe key):买 basic 直接履约
    r = api_client.post("/api/billing/checkout", json={"item": "basic"}, headers=headers)
    assert r.status_code == 200 and r.json()["demo"] is True
    st = api_client.get("/api/billing/status", headers=headers).json()
    assert st["plan"] == "basic" and st["active"]
    assert st["credits"]["planCredits"] == 10000.0

    # 积分包进 pack 池
    api_client.post("/api/billing/checkout", json={"item": "pack_s"}, headers=headers)
    st = api_client.get("/api/billing/status", headers=headers).json()
    assert st["credits"]["packCredits"] == 2000.0 + 1200.0

    ledger = api_client.get("/api/billing/ledger", headers=headers).json()
    sources = {row["source"] for row in ledger}
    assert {"signup_grant", "plan_grant", "pack_grant"} <= sources


def test_fulfill_order_idempotent(test_db):
    from app.api.billing import _fulfill_order

    u = _user(test_db)
    order = Order(user_id=u.id, plan="pack_s", amount_cents=990, channel="stripe", status="created")
    test_db.add(order)
    test_db.commit()

    _fulfill_order(test_db, order)
    first = u.credit_tokens_pack
    # webhook 幂等:completed 与 async_payment_succeeded 都会触发,按 status 只履约一次
    if order.status != "paid":
        _fulfill_order(test_db, order)
    assert u.credit_tokens_pack == first
    assert order.status == "paid"


def test_chat_blocked_when_no_credits(api_client, test_db):
    from app.db_models import Client

    r = api_client.post("/api/auth/register", json={"username": "poor01", "password": "password8"})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    u = test_db.query(User).filter(User.username == "poor01").first()
    # 烧光注册赠礼
    credits.consume(test_db, u, u.credit_tokens_pack + 1, ref="burn")

    c = Client(name="拦截测试", client_type="personal")
    test_db.add(c)
    test_db.commit()

    r = api_client.post(
        "/api/workbench/chat", json={"clientId": c.id, "message": "hi"}, headers=headers
    )
    assert r.status_code == 402

    r = api_client.post(
        "/api/workbench/tasks", json={"clientId": c.id, "kind": "followup"}, headers=headers
    )
    assert r.status_code == 402

    # 未登录演示模式不拦截
    r = api_client.post("/api/workbench/chat", json={"clientId": c.id, "message": "hi"})
    assert r.status_code == 200


# ---------- 计量收集器(任务/计划/文档修订的 LLM 用量) ----------

def test_collect_usage_sink_mechanics():
    import asyncio

    from app.services.llm import collect_usage, record_usage

    # 未包裹:静默不记不炸
    record_usage({"total_tokens": 100})

    async def _one(n: int) -> None:
        record_usage({"total_tokens": n})

    async def _scenario() -> tuple:
        with collect_usage() as outer:
            record_usage({"total_tokens": 10})
            # 并行子协程继承 context,落入同一收集器(DAG gather 场景)
            await asyncio.gather(_one(20), _one(30))
            with collect_usage() as inner:  # 嵌套:内层独立,不串账
                record_usage({"prompt_tokens": 4, "completion_tokens": 6})
            record_usage({})  # 空 usage 忽略
            return sum(outer), sum(inner)

    # 不用 asyncio.run:它退出时把主线程默认 loop 置空,
    # 会弄炸后续按 get_event_loop 风格写的用例
    loop = asyncio.new_event_loop()
    try:
        outer_total, inner_total = loop.run_until_complete(_scenario())
    finally:
        loop.close()
    assert outer_total == 60
    assert inner_total == 10


def test_usage_consume_hookup(test_db):
    """collect_usage + _consume_usage 组合:任务执行挂点的直接验证。"""
    from app.api.workbench import _consume_usage
    from app.db_models import CreditLedger
    from app.services.llm import collect_usage, record_usage

    u = _user(test_db)
    credits.grant(test_db, u, 10_000, source="pack_grant")

    with collect_usage() as sink:
        record_usage({"total_tokens": 3_000})
        record_usage({"total_tokens": 1_500})
    _consume_usage(test_db, u.id, sum(sink), "task:t1")

    test_db.refresh(u)
    assert u.credit_tokens_pack == 10_000 - 4_500
    row = (
        test_db.query(CreditLedger)
        .filter(CreditLedger.user_id == u.id, CreditLedger.source == "consume")
        .first()
    )
    assert row is not None
    assert row.ref == "task:t1"
    assert row.delta_tokens == -4_500


def test_task_create_no_llm_no_ledger(api_client, test_db):
    """无 LLM 时建任务走模板计划,不应产生任何扣费流水(挂点不误扣)。"""
    from app.db_models import Client, CreditLedger

    r = api_client.post("/api/auth/register", json={"username": "meter01", "password": "password8"})
    token = r.json()["token"]
    u = test_db.query(User).filter(User.username == "meter01").first()
    credits.grant(test_db, u, 10_000, source="pack_grant")  # 过余额拦截
    c = Client(name="计量测试", client_type="personal")
    test_db.add(c)
    test_db.commit()

    r = api_client.post(
        "/api/workbench/tasks",
        json={"clientId": c.id, "kind": "policy_review"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    u = test_db.query(User).filter(User.username == "meter01").first()
    rows = (
        test_db.query(CreditLedger)
        .filter(CreditLedger.user_id == u.id, CreditLedger.source == "consume")
        .all()
    )
    assert rows == []
