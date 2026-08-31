"""知识库管理 API:上传 / 列表 / 详情 / 删除 / 重建索引 / 检索测试。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.db import get_db
from app.services import kb
from app.services import workbench_store as store

logger = logging.getLogger("whiteboard-advisor.kb-api")

router = APIRouter(prefix="/api/kb")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB


class InlineTextBody(BaseModel):
    title: str
    text: str
    tags: Optional[list[str]] = None
    scope: str = "global"


class UrlBody(BaseModel):
    title: Optional[str] = None
    url: str
    tags: Optional[list[str]] = None
    scope: str = "global"


class SearchBody(BaseModel):
    query: str
    clientId: Optional[str] = None
    topK: int = 6


def _validate_scope(scope: str) -> str:
    if scope == "global":
        return scope
    if scope.startswith("client:"):
        try:
            kb.check_id(scope.split("client:", 1)[1], "client id")
        except ValueError:
            raise HTTPException(400, "scope 中的 client id 不合法")
        return scope
    raise HTTPException(400, "scope 必须是 global 或 client:<id>")


@router.get("/documents")
async def list_documents(scope: Optional[str] = None, db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = db.query(kb.KbDocument).order_by(kb.KbDocument.created_at.desc()).all()
    if scope:
        rows = [d for d in rows if d.scope == scope]
    return [store.kb_doc_out(d) for d in rows]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, db: OrmSession = Depends(get_db)) -> dict:
    doc = db.get(kb.KbDocument, kb.check_id(doc_id, "document id"))
    if not doc:
        raise HTTPException(404, "document not found")
    return store.kb_doc_out(doc, with_chunks=True)


@router.post("/documents")
async def upload_document(
    file: Optional[UploadFile] = File(default=None),
    title: str = Form(default=""),
    tags: str = Form(default="[]"),
    scope: str = Form(default="global"),
    db: OrmSession = Depends(get_db),
) -> dict:
    """文件上传(PDF/Word/TXT/MD/HTML)。解析入库为异步状态:parsing -> indexed。"""
    _validate_scope(scope)
    if file is None:
        raise HTTPException(400, "missing file")

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 20MB 限制")
    filename = os.path.basename(file.filename or "untitled")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in kb.SUPPORTED_EXT:
        raise HTTPException(400, f"暂不支持的文件类型: {ext or '(无扩展名)'}")

    try:
        tag_list = json.loads(tags) if tags else []
        if not isinstance(tag_list, list):
            raise ValueError("tags must be a list")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "tags 必须是 JSON 数组")

    doc = kb.create_document(
        db,
        title=title or os.path.splitext(filename)[0],
        doc_type=kb.SUPPORTED_EXT[ext],
        raw=raw,
        filename=filename,
        tags=[str(t)[:40] for t in tag_list][:10],
        scope=scope,
    )
    kb.save_upload_file(doc, raw)

    # 后台解析入库(状态机: parsing -> indexed / failed)
    asyncio.get_running_loop().create_task(_index_later(doc.id))
    return store.kb_doc_out(doc)


async def _index_later(doc_id: str) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        await kb.index_document(session, doc_id)
    except Exception as e:  # noqa: BLE001 状态已在 index_document 内标记 failed
        logger.warning("async index failed: %s", e)
    finally:
        session.close()


@router.post("/documents/text")
async def upload_inline_text(body: InlineTextBody, db: OrmSession = Depends(get_db)) -> dict:
    """手动粘贴文本直接入库。"""
    scope = _validate_scope(body.scope)
    if not body.text.strip():
        raise HTTPException(400, "text 为空")
    doc = kb.create_document(
        db,
        title=body.title,
        doc_type="text",
        raw=body.text.encode("utf-8"),
        tags=body.tags or [],
        scope=scope,
    )
    try:
        await kb.index_inline_text(db, doc, body.text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"入库失败: {e}")
    return store.kb_doc_out(doc, with_chunks=True)


@router.post("/documents/url")
async def upload_from_url(body: UrlBody, db: OrmSession = Depends(get_db)) -> dict:
    """抓取网页正文入库。"""
    scope = _validate_scope(body.scope)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url 必须以 http(s):// 开头")
    doc = kb.create_document(
        db,
        title=body.title or url[:80],
        doc_type="html",
        tags=body.tags or [],
        scope=scope,
        source_url=url,
    )
    try:
        await kb.index_document(db, doc.id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"抓取/入库失败: {e}")
    db.refresh(doc)
    return store.kb_doc_out(doc, with_chunks=False)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, db: OrmSession = Depends(get_db)) -> dict:
    if not kb.delete_document(db, doc_id):
        raise HTTPException(404, "document not found")
    return {"ok": True}


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: str, db: OrmSession = Depends(get_db)) -> dict:
    try:
        doc = await kb.index_document(db, doc_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"重建索引失败: {e}")
    return store.kb_doc_out(doc)


@router.post("/search")
async def search(body: SearchBody, db: OrmSession = Depends(get_db)) -> dict:
    hits = await kb.search_async(
        db,
        body.query,
        client_id=body.clientId,
        top_k=max(1, min(body.topK, 20)),
    )
    return {"query": body.query, "hits": hits}
