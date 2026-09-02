"""合规审核测试:三层查重 / 冲突消解(套件 Part 3 夹具)/ pattern 降级审核 /
compose 自动标注 / API 与计费流水。conftest 已强制无 LLM,全部走确定性路径。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.services import compliance
from app.services import embedding as emb
from app.services.demo_seed import SEED_COMPLIANCE_RULES, ensure_compliance_rules


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


def _rule(code, text, rule_set="rs_t", **kw):
    return {
        "rule_set_id": rule_set,
        "risk_code": code,
        "rule_text": text,
        "audit_point": f"检查 {code}",
        "risk_level": kw.get("risk_level", "中"),
        "suggestion": "",
        **kw,
    }


# ---------- 三层查重 ----------

def test_dedup_risk_key_global(test_db):
    r1 = compliance.add_rules(test_db, [_rule("A1", "不得承诺保证收益条款甲")])
    assert r1["added"] == ["rs_t:A1"]
    # 同 risk_key 再录 -> skip
    r2 = compliance.add_rules(test_db, [_rule("A1", "完全不同的另一段文字内容")])
    assert r2["added"] == [] and r2["skipped"][0]["riskKey"] == "rs_t:A1"


def test_dedup_content_hash_scoped_to_rule_set(test_db):
    text = "不得以停售为由炒作销售保险产品"
    compliance.add_rules(test_db, [_rule("B1", text)])
    # 同规则集同内容不同编号 -> 内容指纹拦截
    r = compliance.add_rules(test_db, [_rule("B2", text)])
    assert r["added"] == []
    # 不同规则集同内容 -> 正常(不同制度引用同一上位条文属正常)
    r = compliance.add_rules(test_db, [_rule("B1", text, rule_set="rs_other")])
    assert r["added"] == ["rs_other:B1"]


def test_dedup_similarity_warns_not_blocks(test_db):
    compliance.add_rules(test_db, [_rule("C1", "不得向投保人承诺保证收益或保本保息")])
    r = compliance.add_rules(test_db, [_rule("C2", "不得向投保人承诺保证收益或者保本保息!")])
    assert r["added"] == ["rs_t:C2"]  # 写入不被阻塞
    assert r["warnings"] and r["warnings"][0]["similarTo"] == "rs_t:C1"


def test_update_versioning(test_db):
    compliance.add_rules(test_db, [_rule("D1", "旧版本条文内容")])
    r = compliance.add_rules(test_db, [_rule("D1", "新版本条文内容")], on_conflict="update")
    assert r["updated"] == ["rs_t:D1"]
    rows = compliance.search_rules(test_db)
    live = [x for x in rows if x.risk_key == "rs_t:D1"]
    assert len(live) == 1 and live[0].version == 2 and live[0].rule_text == "新版本条文内容"


# ---------- 冲突消解(套件 audit-prompt Part 3 夹具) ----------

def test_resolve_conflicts_part3_fixture():
    candidates = [
        {"riskKey": "tk:1.1", "ruleSetId": "tk", "riskCode": "1.1",
         "eventKey": "带客户参观寺庙并烧香祈福", "riskLevel": "高", "confidence": 0.91},
        {"riskKey": "tk:3", "ruleSetId": "tk", "riskCode": "3",
         "eventKey": "带客户参观寺庙并烧香祈福", "riskLevel": "高", "confidence": 0.9},
    ]
    policies = {
        "tk:1.1": {"priority": 30, "suppressed_by_keys": ["tk:1.2", "tk:1.3", "tk:3"],
                   "suppresses_keys": [], "same_event_only": True, "same_rule_set_only": True},
        "tk:3": {"priority": 100, "suppressed_by_keys": [],
                 "suppresses_keys": ["tk:1.1", "tk:1.2", "tk:1.3", "tk:2", "tk:4", "tk:5"],
                 "same_event_only": True, "same_rule_set_only": True},
    }
    violations, suppressed = compliance.resolve_conflicts(candidates, policies)
    assert [v["riskKey"] for v in violations] == ["tk:3"]
    assert len(suppressed) == 1
    assert suppressed[0]["riskKey"] == "tk:1.1"
    assert suppressed[0]["suppressedBy"] == "tk:3"  # 审计字段保留


def test_resolve_conflicts_different_event_no_suppression():
    candidates = [
        {"riskKey": "tk:1.1", "ruleSetId": "tk", "riskCode": "1.1", "eventKey": "事件甲", "riskLevel": "高"},
        {"riskKey": "tk:3", "ruleSetId": "tk", "riskCode": "3", "eventKey": "事件乙", "riskLevel": "高"},
    ]
    policies = {"tk:3": {"priority": 100, "suppresses_keys": ["tk:1.1"]}}
    violations, suppressed = compliance.resolve_conflicts(candidates, policies)
    assert len(violations) == 2 and suppressed == []  # 不同 event_key 不互斥


# ---------- pattern 降级审核 + 种子规则 ----------

def test_seed_rules_idempotent(test_db):
    n1 = ensure_compliance_rules(test_db)
    assert n1 == len(SEED_COMPLIANCE_RULES)
    assert ensure_compliance_rules(test_db) == 0  # 幂等


def test_pattern_audit_hits_and_resolution(test_db):
    ensure_compliance_rules(test_db)
    report = asyncio.get_event_loop().run_until_complete(
        compliance.audit_text(test_db, "这款产品跟银行存款一样,保本保息稳赚不赔,绝对安全!", mode="pattern")
    )
    assert report["overallRisk"] == "高"
    keys = {v["riskKey"] for v in report["violations"]}
    assert "rs_industry_base:C01" in keys  # 保证收益必命中
    assert report["disclaimer"]  # 复核声明不可省略

    clean = asyncio.get_event_loop().run_until_complete(
        compliance.audit_text(test_db, "为您整理了重疾险的保障责任说明,具体以条款为准。", mode="pattern")
    )
    assert clean["violations"] == [] and clean["overallRisk"] == "无"


def test_compose_doc_auto_annotation(api_client, test_db):
    """无 LLM 模板路径下,任务产出的文档应带 compliance 标注。"""
    ensure_compliance_rules(test_db)
    r = api_client.post("/api/workbench/clients", json={"name": "标注测试", "type": "personal"})
    cid = r.json()["id"]
    r = api_client.post("/api/workbench/tasks", json={"clientId": cid, "kind": "generate_plan"})
    task = r.json()
    api_client.post(f"/api/workbench/tasks/{task['id']}/approve", json={"auto": False})
    for _ in range(8):
        s = api_client.post(f"/api/workbench/tasks/{task['id']}/step").json()
        if s["awaiting"]:
            api_client.post(
                f"/api/workbench/tasks/{task['id']}/confirm",
                json={"eventId": s["event"]["id"], "auto": False},
            )
        elif s["taskStatus"] == "done":
            break
    arts = api_client.get("/api/workbench/artifacts", params={"clientId": cid}).json()
    doc = next(a for a in arts if a["content"].get("kind") == "doc")
    assert "compliance" in doc["content"]
    assert doc["content"]["compliance"]["rulesChecked"] == len(SEED_COMPLIANCE_RULES)


# ---------- API ----------

def test_audit_api_and_ledger(api_client, test_db):
    ensure_compliance_rules(test_db)
    # 未登录演示模式可用
    r = api_client.post("/api/workbench/compliance/audit", json={"text": "买保险送现金红包,即将停售!"})
    assert r.status_code == 200
    body = r.json()
    assert body["overallRisk"] == "高" and len(body["violations"]) >= 2

    # 空文本 400
    assert api_client.post("/api/workbench/compliance/audit", json={"text": "  "}).status_code == 400

    # 规则列表
    rules = api_client.get("/api/workbench/compliance/rules").json()
    assert len(rules) == len(SEED_COMPLIANCE_RULES)

    # 余额烧光后 402
    from app.db_models import User
    from app.services import credits

    r = api_client.post("/api/auth/register", json={"username": "compl01", "password": "password8"})
    token = r.json()["token"]
    u = test_db.query(User).filter(User.username == "compl01").first()
    credits.consume(test_db, u, u.credit_tokens_pack + u.credit_tokens_plan + 1, ref="burn")
    r = api_client.post(
        "/api/workbench/compliance/audit",
        json={"text": "保本保息"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 402
