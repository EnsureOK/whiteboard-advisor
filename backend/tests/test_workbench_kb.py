"""工作台数据层 + 知识库管线测试(不依赖外网:embedding 走 mock 向量)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from app.db import Base, get_db
from app.db_models import KbChunk, KbDocument
from app.services import kb
from app.services import embedding as emb


@pytest.fixture()
def test_db(monkeypatch):
    # mock embedding,避免测试打真实千帆
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


def test_chunk_text_paragphs_and_merge():
    text = "短段一。\n\n" + "这是很长的一段," * 80 + "\n\n短段三。"
    chunks = kb.chunk_text(text)
    assert len(chunks) >= 3
    assert all(len(c) <= kb.CHUNK_SIZE + kb.CHUNK_OVERLAP + 50 for c in chunks)
    assert kb.chunk_text("   ") == []


def test_extract_text_html_strips_tags():
    raw = "<html><script>bad()</script><body><h1>标题</h1><p>正文内容</p></body></html>".encode("utf-8")
    text = kb.extract_text("html", raw)
    assert "标题" in text and "正文内容" in text and "bad()" not in text


def test_index_and_search(test_db):
    doc = kb.create_document(
        test_db,
        title="核保手册",
        doc_type="text",
        raw=b"x",
        scope="global",
    )
    para1 = "甲状腺结节 3 类:重疾险通常除外甲状腺癌责任,医疗险可能除外或加费。" + "TI-RADS 分级越高,核保越谨慎。" * 6
    para2 = "高血压服药后血压稳定在 140/90 以下,重疾险多可加费或标准体承保。" + "合并蛋白尿或心电图异常时需要体检复查后评估。" * 6
    para3 = "糖尿病控制良好的部分产品可承保,多数重疾险会拒保或延期。" + "妊娠期糖尿病产后恢复正常满一年,一般可标准体承保。" * 6
    doc = asyncio_run(kb.index_inline_text(test_db, doc, para1 + "\n\n" + para2 + "\n\n" + para3))
    assert doc.status == "indexed" and doc.chunk_count >= 2

    hits = asyncio_run(kb.search_async(test_db, "甲状腺结节 重疾 除外"))
    assert hits, "应该能检索到"
    assert hits[0]["docTitle"] == "核保手册"
    assert hits[0]["text"].startswith("甲状腺结节")


def test_scope_isolation(test_db):
    doc_global = kb.create_document(test_db, title="全局条款", doc_type="text", scope="global")
    asyncio_run(kb.index_inline_text(test_db, doc_global, "张三客户的专属备注:保费 5 万。"))
    doc_private = kb.create_document(test_db, title="客户私有", doc_type="text", scope="client:ffffffffffffffffffffffffffffffff")
    asyncio_run(kb.index_inline_text(test_db, doc_private, "王五客户的私密谈话记录:预算 8 万。"))

    # 不带 client 只见全局;带 client 可见两库
    hits_public = asyncio_run(kb.search_async(test_db, "预算 万"))
    assert all(h["scope"] == "global" for h in hits_public)
    hits_all = asyncio_run(kb.search_async(test_db, "预算 万", client_id="f" * 32))
    assert {h["scope"] for h in hits_all} >= {"global", "client:ffffffffffffffffffffffffffffffff"}


def test_delete_document_removes_chunks(test_db):
    doc = kb.create_document(test_db, title="待删除", doc_type="text", scope="global")
    asyncio_run(kb.index_inline_text(test_db, doc, "要被删除的内容。"))
    assert test_db.query(KbChunk).count() > 0
    assert kb.delete_document(test_db, doc.id)
    assert test_db.query(KbChunk).count() == 0


def test_check_id_rejects_bad_input():
    with pytest.raises(ValueError):
        kb.check_id("../evil")
    with pytest.raises(ValueError):
        kb.check_id("short")
    assert kb.check_id("a" * 32) == "a" * 32


# ---------- API 层(覆盖 get_db) ----------

@pytest.fixture()
def api_client(test_db, monkeypatch):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_kb_text_endpoint_and_search_api(api_client):
    r = api_client.post(
        "/api/kb/documents/text",
        json={"title": "API 粘贴", "text": "年金险现金价值表是保证利益的唯一依据。", "scope": "global"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "indexed"

    r = api_client.post("/api/kb/search", json={"query": "年金 现金价值"})
    assert r.status_code == 200
    assert any("现金价值" in h["text"] for h in r.json()["hits"])

    bad = api_client.post("/api/kb/documents/text", json={"title": "x", "text": "y", "scope": "client:zzz"})
    assert bad.status_code == 400


def _make_client(api_client, name="测试客户", type_="family") -> dict:
    r = api_client.post("/api/workbench/clients", json={"name": name, "type": type_})
    assert r.status_code == 200
    return r.json()


def test_client_policy_engagement_api(api_client):
    c = _make_client(api_client, "李强一家", "family")
    assert c["type"] == "family" and c["policies"] == [] and c["engagements"] == []

    m = api_client.post(
        f"/api/workbench/clients/{c['id']}/members",
        json={"name": "李强", "relation": "本人"},
    ).json()

    p = api_client.post(
        f"/api/workbench/clients/{c['id']}/policies",
        json={"memberId": m["id"], "line": "重疾险", "productName": "测试重疾", "amount": 1_000_000, "status": "active"},
    )
    assert p.status_code == 200
    assert p.json()["statusLabel"] == "有效"

    e = api_client.post(
        f"/api/workbench/clients/{c['id']}/engagements",
        json={"kind": "claim", "title": "住院理赔", "line": "医疗险"},
    )
    assert e.status_code == 200
    assert e.json()["kindLabel"] == "理赔中"

    boot = api_client.get("/api/workbench/bootstrap").json()
    me = next(x for x in boot["clients"] if x["id"] == c["id"])
    assert len(me["policies"]) == 1 and len(me["engagements"]) == 1

    # 完结事项后不再出现在 open 聚合里
    done = api_client.patch(f"/api/workbench/engagements/{e.json()['id']}", json={"status": "done"})
    assert done.status_code == 200
    boot = api_client.get("/api/workbench/bootstrap").json()
    me = next(x for x in boot["clients"] if x["id"] == c["id"])
    assert me["engagements"] == []

    bad = api_client.post(f"/api/workbench/clients/{c['id']}/engagements", json={"kind": "nope"})
    assert bad.status_code == 400


def test_client_files_upload(api_client, test_db):
    c = _make_client(api_client, "带资料的客户", "personal")
    files = [
        ("files", ("体检报告.txt", "甲状腺结节 TI-RADS 2 类,建议随访。".encode("utf-8"), "text/plain")),
        ("files", ("保单照片.png", b"\x89PNG\r\n\x1a\nfakepng", "image/png")),
    ]
    r = api_client.post(f"/api/workbench/clients/{c['id']}/files", files=files)
    assert r.status_code == 200
    out = r.json()
    assert len(out) == 2
    doc_rec = next(x for x in out if x["filename"].endswith(".txt"))
    img_rec = next(x for x in out if x["filename"].endswith(".png"))
    # 文档进入该客户私有知识库;图片仅存档
    assert doc_rec["kind"] == "document" and doc_rec["kbDocId"]
    assert img_rec["kind"] == "image" and img_rec["kbDocId"] is None
    kb_doc = test_db.get(KbDocument, doc_rec["kbDocId"])
    assert kb_doc is not None and kb_doc.scope == f"client:{c['id']}"

    listed = api_client.get(f"/api/workbench/clients/{c['id']}/files").json()
    assert len(listed) == 2

    raw = api_client.get(f"/api/workbench/clients/{c['id']}/files/{img_rec['id']}/raw")
    assert raw.status_code == 200 and raw.content.startswith(b"\x89PNG")

    gone = api_client.delete(f"/api/workbench/clients/{c['id']}/files/{img_rec['id']}")
    assert gone.status_code == 200
    assert len(api_client.get(f"/api/workbench/clients/{c['id']}/files").json()) == 1


def test_task_flow_end_to_end(api_client, test_db):
    from app.db_models import Client, Member, Policy

    c = Client(name="测试家庭", client_type="family")
    test_db.add(c)
    test_db.flush()
    m = Member(client_id=c.id, name="测试人", relation="本人", seq=0)
    test_db.add(m)
    test_db.flush()
    # 真实托管保单:重疾已配满基线(100万),矩阵该格应为 ok
    test_db.add(Policy(client_id=c.id, member_id=m.id, line="重疾险", amount=1_000_000, status="active"))
    test_db.commit()

    r = api_client.post("/api/workbench/tasks", json={"clientId": c.id, "kind": "policy_review"})
    assert r.status_code == 200
    task = r.json()
    assert len(task["plan"]) == 5

    r = api_client.post(f"/api/workbench/tasks/{task['id']}/approve", json={})
    assert r.json()["status"] == "approved"

    approval_seen = False
    for _ in range(6):
        s = api_client.post(f"/api/workbench/tasks/{task['id']}/step").json()
        if s["awaiting"]:
            approval_seen = True
            cf = api_client.post(f"/api/workbench/tasks/{task['id']}/confirm", json={"eventId": s["event"]["id"]})
            assert cf.json()["event"]["status"] == "confirmed"
        elif s["taskStatus"] == "done":
            break
    assert approval_seen, "审批卡应出现一次"

    arts = api_client.get("/api/workbench/artifacts", params={"clientId": c.id}).json()
    assert len(arts) == 1 and arts[0]["type"] == "review_matrix"
    assert len(arts[0]["content"]["rows"]) == 1
    assert arts[0]["content"]["cols"] == ["身故保障", "重疾保障", "医疗费用", "意外保障", "教育/养老现金流"]
    cells = arts[0]["content"]["rows"][0]["cells"]
    assert cells["重疾保障"]["level"] == "ok" and cells["重疾保障"]["current"] == 1_000_000
    assert cells["身故保障"]["level"] == "high" and cells["身故保障"]["current"] == 0


def test_company_review_uses_company_cols(api_client, test_db):
    from app.db_models import Client, Policy

    c = Client(name="测试公司", client_type="company")
    test_db.add(c)
    test_db.flush()
    test_db.add(Policy(client_id=c.id, member_id=None, line="企财险", amount=5_000_000, status="active"))
    test_db.commit()

    r = api_client.post("/api/workbench/tasks", json={"clientId": c.id, "kind": "policy_review"})
    task = r.json()
    api_client.post(f"/api/workbench/tasks/{task['id']}/approve", json={})
    for _ in range(6):
        s = api_client.post(f"/api/workbench/tasks/{task['id']}/step").json()
        if s["awaiting"]:
            api_client.post(f"/api/workbench/tasks/{task['id']}/confirm", json={"eventId": s["event"]["id"]})
        elif s["taskStatus"] == "done":
            break

    arts = api_client.get("/api/workbench/artifacts", params={"clientId": c.id}).json()
    content = arts[0]["content"]
    assert content["cols"] == ["企业财产", "雇主责任", "团体医疗", "公众/产品责任"]
    assert len(content["rows"]) == 1 and content["rows"][0]["member"] == "测试公司"
    assert content["rows"][0]["cells"]["企业财产"]["level"] == "ok"


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
