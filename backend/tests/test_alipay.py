"""支付宝当面付测试:RSA2 签名往返 / notify 验签 / 下单-轮询-履约 / 幂等。

支付宝网关用 FakeClient 替身;密钥用测试内临时生成的 RSA 对
(应用私钥与"支付宝公钥"取同一对,足以验证签名管线)。"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.db_models import Order, User
from app.services import alipay_pay
from app.services import embedding as emb


@pytest.fixture()
def rsa_keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    monkeypatch.setattr(settings, "alipay_appid", "2021000000000001")
    monkeypatch.setattr(settings, "alipay_app_private_key", priv)
    monkeypatch.setattr(settings, "alipay_public_key", pub)
    return key


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


def test_sign_verify_roundtrip(rsa_keys):
    unsigned = "app_id=x&method=alipay.trade.precreate&timestamp=2026-09-06 10:00:00"
    sig = alipay_pay._sign(unsigned)
    assert alipay_pay._verify(unsigned, sig)
    assert not alipay_pay._verify(unsigned + "&total_amount=0.01", sig)  # 篡改即失败


def test_verify_notify(rsa_keys):
    form = {"out_trade_no": "o1", "trade_status": "TRADE_SUCCESS", "total_amount": "9.90",
            "sign_type": "RSA2"}
    unsigned = "&".join(f"{k}={v}" for k, v in sorted(form.items()) if k not in ("sign", "sign_type"))
    form["sign"] = alipay_pay._sign(unsigned)
    assert alipay_pay.verify_notify(dict(form))
    tampered = dict(form, total_amount="0.01")
    assert not alipay_pay.verify_notify(tampered)


class _FakeGwClient:
    """替身支付宝网关:precreate 返回二维码;query 首查 pending,再查 paid。"""

    calls = {"query": 0}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        from urllib.parse import parse_qs

        params = {k: v[0] for k, v in parse_qs(content.decode("utf-8")).items()}
        method = params["method"]

        class _R:
            def __init__(self, payload):
                self._p = payload

            def json(self):
                return self._p

        if method == "alipay.trade.precreate":
            return _R({"alipay_trade_precreate_response": {
                "code": "10000", "qr_code": "https://qr.alipay.com/bax0test"}})
        _FakeGwClient.calls["query"] += 1
        status = "TRADE_SUCCESS" if _FakeGwClient.calls["query"] >= 2 else "WAIT_BUYER_PAY"
        return _R({"alipay_trade_query_response": {
            "code": "10000", "trade_status": status, "trade_no": "20260906trade001"}})


def test_precreate_poll_fulfill_idempotent(api_client, test_db, rsa_keys, monkeypatch):
    monkeypatch.setattr(alipay_pay.httpx, "AsyncClient", _FakeGwClient)
    _FakeGwClient.calls["query"] = 0

    r = api_client.post("/api/auth/register", json={"username": "ali01", "password": "password8"})
    token = r.json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}

    r = api_client.post("/api/billing/alipay/precreate", json={"item": "pack_s"}, headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["qrCode"].startswith("https://qr.alipay.com/")
    assert len(body["qrImage"]) > 100  # base64 png
    order_id = body["orderId"]

    # 首查未支付
    r = api_client.get(f"/api/billing/alipay/orders/{order_id}", headers=hdr)
    assert r.json()["status"] == "pending"
    # 再查已支付 -> 履约
    r = api_client.get(f"/api/billing/alipay/orders/{order_id}", headers=hdr)
    assert r.json()["status"] == "paid"
    u = test_db.query(User).filter(User.username == "ali01").first()
    assert u.credit_tokens_pack == 1200 * 2000  # 积分包·小 1200 分

    # 幂等:再查/通知重放都不重复入账
    api_client.get(f"/api/billing/alipay/orders/{order_id}", headers=hdr)
    form = {"out_trade_no": order_id, "trade_status": "TRADE_SUCCESS", "total_amount": "9.90"}
    unsigned = "&".join(f"{k}={v}" for k, v in sorted(form.items()))
    form["sign"] = alipay_pay._sign(unsigned)
    r = api_client.post("/api/billing/alipay/notify", data=form)
    assert r.text == "success"
    test_db.refresh(u)
    assert u.credit_tokens_pack == 1200 * 2000


def test_notify_bad_sign_rejected(api_client, test_db, rsa_keys):
    r = api_client.post("/api/billing/alipay/notify",
                        data={"out_trade_no": "x", "trade_status": "TRADE_SUCCESS", "sign": "AAAA"})
    assert r.text == "failure"


def test_precreate_unconfigured_503(api_client, test_db):
    r = api_client.post("/api/auth/register", json={"username": "ali02", "password": "password8"})
    token = r.json()["token"]
    r = api_client.post("/api/billing/alipay/precreate", json={"item": "pack_s"},
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503
