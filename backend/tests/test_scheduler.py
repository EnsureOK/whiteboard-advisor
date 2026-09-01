"""定时作业测试:到期扫描幂等 / 简报模板路径。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db_models import Client, DailyBriefing, Engagement, Member, Policy, Todo
from app.services import embedding as emb
from app.services import scheduler


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


def _client_with_policy(db, expiry_days: int) -> tuple[Client, Policy]:
    c = Client(name="到期客户", client_type="personal")
    db.add(c)
    db.flush()
    m = Member(client_id=c.id, name="王一", relation="本人", seq=0)
    db.add(m)
    db.flush()
    p = Policy(
        client_id=c.id,
        member_id=m.id,
        line="医疗险",
        product_name="测试医疗",
        amount=2_000_000,
        expiry_date=(date.today() + timedelta(days=expiry_days)).isoformat(),
        status="active",
    )
    db.add(p)
    db.commit()
    return c, p


def test_scan_expiring_creates_renewal_and_todo(test_db):
    c, p = _client_with_policy(test_db, 20)
    created = scheduler.scan_expiring(test_db)
    assert created == 1
    e = test_db.query(Engagement).filter(Engagement.policy_id == p.id).first()
    assert e is not None and e.kind == "renewal" and e.status == "open"
    todo = test_db.query(Todo).filter(Todo.client_id == c.id).first()
    assert todo is not None and "到期" in todo.title
    test_db.refresh(p)
    assert p.status == "pending_renewal"

    # 幂等:已有进行中续期事项,不重复创建
    assert scheduler.scan_expiring(test_db) == 0


def test_scan_ignores_far_or_lapsed(test_db):
    _client_with_policy(test_db, 90)  # 超出 30 天窗口
    c2 = Client(name="失效客户", client_type="personal")
    test_db.add(c2)
    test_db.flush()
    test_db.add(
        Policy(
            client_id=c2.id,
            line="重疾险",
            expiry_date=(date.today() + timedelta(days=5)).isoformat(),
            status="lapsed",
        )
    )
    test_db.commit()
    assert scheduler.scan_expiring(test_db) == 0


def test_generate_briefing_template(test_db, monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "qianfan_api_key", "")  # 强制模板路径
    monkeypatch.setattr(app_settings, "wecom_webhook_url", "")
    c, p = _client_with_policy(test_db, 5)
    test_db.add(
        Engagement(client_id=c.id, kind="claim", title="住院理赔跟进", status="open")
    )
    test_db.commit()

    content = asyncio_run(scheduler.generate_briefing(test_db))
    assert "今日工作简报" in content
    assert "测试医疗" in content and "住院理赔跟进" in content
    row = test_db.query(DailyBriefing).filter(DailyBriefing.date == date.today().isoformat()).first()
    assert row is not None and row.content == content

    # 再次生成为更新而非重复插入
    asyncio_run(scheduler.generate_briefing(test_db))
    assert test_db.query(DailyBriefing).count() == 1


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
