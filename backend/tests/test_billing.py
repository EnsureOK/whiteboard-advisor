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


def test_register_grants_signup_bonus_and_demo_checkout(api_client, test_db, monkeypatch):
    # 与本机 .env 解耦:强制走演示通道
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "stripe_api_key", "")
    r = api_client.post("/api/auth/register", json={"username": "tester01", "password": "password8"})
    assert r.status_code == 200
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    st = api_client.get("/api/billing/status", headers=headers).json()
    assert st["credits"]["packCredits"] == 2000.0  # 注册赠礼

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
