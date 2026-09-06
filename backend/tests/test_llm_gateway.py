"""云端 LLM 网关测试:鉴权/余额闸门/转发计量(流式与非流式)。

上游千帆用 FakeClient 替身;网关路由在测试里动态挂载(local 模式默认不挂)。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import llm_gateway
from app.db import Base, get_db
from app.db_models import CreditLedger, User
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
    # 网关流末计量用 SessionLocal 开独立会话 -> 指到测试库
    monkeypatch.setattr("app.db.SessionLocal", factory)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def api_client(test_db, monkeypatch):
    from app.main import app

    if not any(getattr(r, "path", "").startswith("/llm/v2") for r in app.routes):
        app.include_router(llm_gateway.router)
    # 网关闸门要求配置了上游 key(conftest 清空了,单独打开)
    from app.config import settings

    monkeypatch.setattr(settings, "qianfan_api_key", "sk-upstream-test")
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = json.dumps(
        {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3000}}
    ).encode()

    def json(self):
        return json.loads(self.content)


class _FakeStream:
    status_code = 200

    async def aread(self):
        return b""

    async def aiter_bytes(self):
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b'data: {"choices":[],"usage":{"total_tokens":5000}}\n\ndata: [DONE]\n\n'


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def post(self, url, headers=None, json=None):
        assert headers["Authorization"] == "Bearer sk-upstream-test"  # 换成了真 key
        return _FakeResp()

    def stream(self, method, url, headers=None, json=None):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                assert headers["Authorization"] == "Bearer sk-upstream-test"
                return _FakeStream()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _register(api_client, test_db, name: str, tokens: int = 100_000):
    r = api_client.post("/api/auth/register", json={"username": name, "password": "password8"})
    token = r.json()["token"]
    u = test_db.query(User).filter(User.username == name).first()
    if tokens:
        credits.grant(test_db, u, tokens, source="pack_grant")
    return token, u


def test_gateway_requires_auth(api_client):
    r = api_client.post("/llm/v2/chat/completions", json={"messages": []})
    assert r.status_code in (401, 403)


def test_gateway_blocks_no_credits(api_client, test_db):
    token, u = _register(api_client, test_db, "gw01", tokens=0)
    credits.consume(test_db, u, u.credit_tokens_pack + u.credit_tokens_plan + 1, ref="burn")
    r = api_client.post("/llm/v2/chat/completions", json={"messages": []},
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 402
    assert "insufficient_credits" in r.text


def test_gateway_nonstream_meters(api_client, test_db, monkeypatch):
    monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _FakeClient)
    token, u = _register(api_client, test_db, "gw02")
    r = api_client.post("/llm/v2/chat/completions",
                        json={"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "hi"}]},
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["usage"]["total_tokens"] == 3000
    test_db.refresh(u)
    assert u.credit_tokens_pack == 100_000 - 3000
    row = test_db.query(CreditLedger).filter(CreditLedger.ref == "gateway:chat").first()
    assert row is not None and row.delta_tokens == -3000


def test_gateway_stream_passthrough_and_meter(api_client, test_db, monkeypatch):
    monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _FakeClient)
    token, u = _register(api_client, test_db, "gw03")
    with api_client.stream("POST", "/llm/v2/chat/completions",
                           json={"stream": True, "messages": []},
                           headers={"Authorization": f"Bearer {token}"}) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert b'"content":"hi"' in body and b"[DONE]" in body  # 原样透传
    test_db.refresh(u)
    assert u.credit_tokens_pack == 100_000 - 5000


def test_gateway_embeddings_meter(api_client, test_db, monkeypatch):
    class _EmbResp(_FakeResp):
        content = json.dumps({"data": [{"embedding": [0.1]}], "usage": {"total_tokens": 800}}).encode()

    class _EmbClient(_FakeClient):
        async def post(self, url, headers=None, json=None):
            assert url.endswith("/embeddings")
            return _EmbResp()

    monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _EmbClient)
    token, u = _register(api_client, test_db, "gw04")
    r = api_client.post("/llm/v2/embeddings", json={"input": "x"},
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    test_db.refresh(u)
    assert u.credit_tokens_pack == 100_000 - 800
