"""保障图表报告测试:确定性组装 / SVG 渲染 / 工件与导出端点。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.db_models import Client, Member, Policy
from app.services import embedding as emb
from app.services.coverage_report import build_coverage_report


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


def _client_with_policies(db) -> Client:
    c = Client(name="图表测试家", client_type="family")
    db.add(c)
    db.commit()
    m = Member(client_id=c.id, name="老王", relation="本人")
    db.add(m)
    db.commit()
    db.add_all([
        Policy(client_id=c.id, member_id=m.id, line="终身寿险", product_name="A",
               amount=1_000_000, premium=20_000, expiry_date=None, status="active"),
        Policy(client_id=c.id, member_id=m.id, line="医疗险", product_name="B",
               amount=2_000_000, premium=800, expiry_date="2026-10-01", status="active"),
        Policy(client_id=c.id, member_id=m.id, line="重疾险", product_name="C",
               amount=300_000, premium=6_000, expiry_date=None, status="lapsed"),  # 失效不计
    ])
    db.commit()
    db.refresh(c)
    return c


def test_build_report_deterministic(test_db):
    c = _client_with_policies(test_db)
    title, content = build_coverage_report(test_db, c)
    assert content["kind"] == "chart_report"
    html = content["html"]
    # 4 段、图表为内联 SVG、零外链
    assert html.count("<h2>") == 4
    assert html.count("<svg") >= 2
    # 零外链:不加载任何远程资源(SVG 的 xmlns 命名空间标识符除外)
    for needle in ('src="http', "src='http", 'href="http', "url(http", "@import"):
        assert needle not in html
    # 金额与保单求和一致(300 万,失效的 30 万不计)
    assert "在保总保额" in html and "300" in content["summary"]
    assert "20,800" in content["summary"] or "20800" in content["summary"].replace(",", "")
    # 医疗险报销额度口径披露
    assert "报销额度上限" in html
    # 到期分布含 2026
    assert "2026" in html


def test_build_report_no_policies(test_db):
    c = Client(name="空客户", client_type="personal")
    test_db.add(c)
    test_db.commit()
    m = Member(client_id=c.id, name="小李", relation="本人")
    test_db.add(m)
    test_db.commit()
    test_db.refresh(c)
    title, content = build_coverage_report(test_db, c)
    assert "暂无托管保单" in content["html"]
    assert content["html"].count("<h2>") == 4  # 结构完整不缺段


def test_report_api_and_export(api_client, test_db):
    c = _client_with_policies(test_db)
    r = api_client.post(f"/api/workbench/clients/{c.id}/coverage-report")
    assert r.status_code == 200
    art = r.json()
    assert art["type"] == "coverage_report" and art["version"] == 1

    # 工件列表可见
    arts = api_client.get("/api/workbench/artifacts", params={"clientId": c.id}).json()
    assert any(a["id"] == art["id"] for a in arts)

    # HTML 导出
    r = api_client.get(f"/api/workbench/artifacts/{art['id']}/export", params={"fmt": "html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert b"<svg" in r.content

    # 不支持的组合
    assert api_client.get(
        f"/api/workbench/artifacts/{art['id']}/export", params={"fmt": "docx"}
    ).status_code == 400

    # 再生成一次 -> 版本递增
    r = api_client.post(f"/api/workbench/clients/{c.id}/coverage-report")
    assert r.json()["version"] == 2
