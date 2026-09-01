"""工作台 API:客户/保单/事项/文件/任务/工件/待办/对话(SSE 流式)。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db import get_db
from app.db_models import (
    CLIENT_TYPES,
    ENGAGEMENT_KINDS,
    POLICY_STATUSES,
    Artifact,
    Client,
    ClientFile,
    Engagement,
    Member,
    Policy,
    Task,
    TaskEvent,
    Todo,
    User,
    WorkMessage,
)
from app.services import auth as auth_svc
from app.services import kb, task_engine
from app.services import workbench_store as store

logger = logging.getLogger("whiteboard-advisor.workbench")

router = APIRouter(prefix="/api/workbench")

from app.paths import data_path

CLIENT_FILE_DIR = data_path("client_files")
os.makedirs(CLIENT_FILE_DIR, exist_ok=True)

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB / 个
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}


# ---------- Schemas ----------

class ClientCreate(BaseModel):
    name: str
    type: str = "family"
    notes: str = ""


class ClientPatch(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    nextContact: Optional[str] = None


class MemberCreate(BaseModel):
    name: str
    relation: str = "本人"
    badge: str = ""
    birthday: Optional[str] = None
    notes: str = ""


class PolicyCreate(BaseModel):
    memberId: Optional[str] = None
    line: str
    productName: str = ""
    insurer: str = ""
    amount: int = 0
    premium: int = 0
    effectiveDate: Optional[str] = None
    expiryDate: Optional[str] = None
    status: str = "active"
    notes: str = ""


class PolicyPatch(BaseModel):
    status: Optional[str] = None
    amount: Optional[int] = None
    premium: Optional[int] = None
    expiryDate: Optional[str] = None
    notes: Optional[str] = None


class EngagementCreate(BaseModel):
    kind: str
    title: str = ""
    line: str = ""
    policyId: Optional[str] = None
    note: str = ""


class EngagementPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None


class TaskCreate(BaseModel):
    clientId: str
    kind: str = "generic"
    title: Optional[str] = None
    message: Optional[str] = None


class ApproveBody(BaseModel):
    plan: Optional[list[dict]] = None


class ConfirmBody(BaseModel):
    eventId: str


class ChatBody(BaseModel):
    clientId: str
    message: str


class TodoPatch(BaseModel):
    status: Optional[str] = None


# ---------- Bootstrap ----------

@router.get("/bootstrap")
async def bootstrap(db: OrmSession = Depends(get_db)) -> dict:
    clients = db.query(Client).all()
    todos = db.query(Todo).filter(Todo.status == "open").all()
    from app.db_models import KbDocument

    doc_count = db.query(KbDocument).count()
    indexed_count = sum(1 for d in db.query(KbDocument).all() if d.status == "indexed")
    return {
        "clients": [store.client_out(c) for c in clients],
        "todos": [store.todo_out(t) for t in todos],
        "kb": {"docs": doc_count, "indexed": indexed_count},
        "engagementKinds": ENGAGEMENT_KINDS,
        "policyStatuses": POLICY_STATUSES,
        "llm": settings.has_llm,
        "embedding": settings.has_llm,
    }


# ---------- Clients ----------

def _get_client_or_404(db: OrmSession, client_id: str) -> Client:
    c = db.get(Client, kb.check_id(client_id, "client id"))
    if not c:
        raise HTTPException(404, "client not found")
    return c


@router.post("/clients")
async def create_client(body: ClientCreate, db: OrmSession = Depends(get_db)) -> dict:
    if body.type not in CLIENT_TYPES:
        raise HTTPException(400, "type 必须是 personal / family / company")
    c = Client(name=body.name.strip()[:120] or "未命名客户", client_type=body.type, notes=body.notes[:2000])
    db.add(c)
    db.commit()
    db.refresh(c)
    return store.client_out(c)


@router.patch("/clients/{client_id}")
async def patch_client(client_id: str, body: ClientPatch, db: OrmSession = Depends(get_db)) -> dict:
    c = _get_client_or_404(db, client_id)
    if body.name is not None:
        c.name = body.name.strip()[:120] or c.name
    if body.notes is not None:
        c.notes = body.notes
    if body.nextContact is not None:
        c.next_contact = body.nextContact
    db.commit()
    db.refresh(c)
    return store.client_out(c)


@router.post("/clients/{client_id}/members")
async def create_member(client_id: str, body: MemberCreate, db: OrmSession = Depends(get_db)) -> dict:
    c = _get_client_or_404(db, client_id)
    m = Member(
        client_id=c.id,
        name=body.name.strip()[:60],
        relation=body.relation[:20],
        badge=body.badge[:20],
        birthday=body.birthday,
        notes=body.notes,
        seq=len(c.members),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return store.member_out(m)


# ---------- Policies ----------

@router.post("/clients/{client_id}/policies")
async def create_policy(client_id: str, body: PolicyCreate, db: OrmSession = Depends(get_db)) -> dict:
    c = _get_client_or_404(db, client_id)
    if body.status not in POLICY_STATUSES:
        raise HTTPException(400, "invalid policy status")
    p = Policy(
        client_id=c.id,
        member_id=body.memberId,
        line=body.line.strip()[:30],
        product_name=body.productName[:120],
        insurer=body.insurer[:60],
        amount=max(0, body.amount),
        premium=max(0, body.premium),
        effective_date=body.effectiveDate,
        expiry_date=body.expiryDate,
        status=body.status,
        notes=body.notes,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return store.policy_out(p)


@router.patch("/policies/{policy_id}")
async def patch_policy(policy_id: str, body: PolicyPatch, db: OrmSession = Depends(get_db)) -> dict:
    p = db.get(Policy, kb.check_id(policy_id, "policy id"))
    if not p:
        raise HTTPException(404, "policy not found")
    if body.status is not None:
        if body.status not in POLICY_STATUSES:
            raise HTTPException(400, "invalid policy status")
        p.status = body.status
    if body.amount is not None:
        p.amount = max(0, body.amount)
    if body.premium is not None:
        p.premium = max(0, body.premium)
    if body.expiryDate is not None:
        p.expiry_date = body.expiryDate
    if body.notes is not None:
        p.notes = body.notes
    db.commit()
    db.refresh(p)
    return store.policy_out(p)


# ---------- Engagements ----------

@router.post("/clients/{client_id}/engagements")
async def create_engagement(client_id: str, body: EngagementCreate, db: OrmSession = Depends(get_db)) -> dict:
    c = _get_client_or_404(db, client_id)
    if body.kind not in ENGAGEMENT_KINDS:
        raise HTTPException(400, "invalid engagement kind")
    e = Engagement(
        client_id=c.id,
        kind=body.kind,
        title=body.title[:200] or ENGAGEMENT_KINDS[body.kind],
        line=body.line[:30],
        policy_id=body.policyId,
        note=body.note,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return store.engagement_out(e)


@router.patch("/engagements/{engagement_id}")
async def patch_engagement(engagement_id: str, body: EngagementPatch, db: OrmSession = Depends(get_db)) -> dict:
    e = db.get(Engagement, kb.check_id(engagement_id, "engagement id"))
    if not e:
        raise HTTPException(404, "engagement not found")
    if body.status is not None:
        if body.status not in ("open", "done", "paused"):
            raise HTTPException(400, "invalid status")
        e.status = body.status
    if body.title is not None:
        e.title = body.title[:200]
    if body.note is not None:
        e.note = body.note
    db.commit()
    db.refresh(e)
    return store.engagement_out(e)


# ---------- Client files (多文件上传:文档入私有知识库,图片存档) ----------

def _safe_client_file_path(file_id: str, filename: str) -> str:
    kb.check_id(file_id, "file id")
    name = os.path.basename(filename or "untitled").replace("..", "_")
    name = re.sub(r"[^\w一-鿿.\-]+", "_", name) or "untitled"
    path = os.path.abspath(os.path.join(CLIENT_FILE_DIR, file_id + "_" + name))
    if not path.startswith(os.path.abspath(CLIENT_FILE_DIR) + os.sep):
        raise ValueError("invalid file path")
    return path


@router.post("/clients/{client_id}/files")
async def upload_client_files(
    client_id: str,
    files: list[UploadFile] = File(...),
    db: OrmSession = Depends(get_db),
) -> list[dict]:
    """多文件上传。PDF/Word/TXT/MD/HTML 同时进入该客户私有知识库;图片存档可预览。"""
    c = _get_client_or_404(db, client_id)
    out: list[dict] = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f"「{f.filename}」超过 20MB 限制")
        filename = os.path.basename(f.filename or "untitled")
        ext = os.path.splitext(filename)[1].lower()
        kind = "image" if ext in IMAGE_EXTS else "document"

        rec = ClientFile(
            client_id=c.id,
            filename=filename[:300],
            content_type=f.content_type or "",
            size_bytes=len(raw),
            kind=kind,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)

        path = _safe_client_file_path(rec.id, filename)
        with open(path, "wb") as fh:
            fh.write(raw)
        rec.path = os.path.relpath(path, CLIENT_FILE_DIR)

        # 可解析文档 -> 同步建知识库文档(私有),后台异步解析入库
        if ext in kb.SUPPORTED_EXT:
            doc = kb.create_document(
                db,
                title=os.path.splitext(filename)[0],
                doc_type=kb.SUPPORTED_EXT[ext],
                raw=raw,
                filename=filename,
                tags=["客户资料"],
                scope=kb.client_scope(c.id),
            )
            kb.save_upload_file(doc, raw)
            rec.kb_doc_id = doc.id
            asyncio.get_running_loop().create_task(_index_client_doc_later(doc.id))

        db.commit()
        db.refresh(rec)
        out.append(store.client_file_out(rec))
    return out


async def _index_client_doc_later(doc_id: str) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        await kb.index_document(session, doc_id)
    except Exception as e:  # noqa: BLE001 状态已在 index_document 内标记 failed
        logger.warning("client doc index failed: %s", e)
    finally:
        session.close()


@router.get("/clients/{client_id}/files")
async def list_client_files(client_id: str, db: OrmSession = Depends(get_db)) -> list[dict]:
    c = _get_client_or_404(db, client_id)
    return [store.client_file_out(f) for f in c.files]


@router.get("/clients/{client_id}/files/{file_id}/raw")
async def get_client_file_raw(client_id: str, file_id: str, db: OrmSession = Depends(get_db)):
    _get_client_or_404(db, client_id)
    rec = db.get(ClientFile, kb.check_id(file_id, "file id"))
    if not rec or rec.client_id != client_id:
        raise HTTPException(404, "file not found")
    path = os.path.abspath(os.path.join(CLIENT_FILE_DIR, rec.path))
    if not path.startswith(os.path.abspath(CLIENT_FILE_DIR) + os.sep) or not os.path.exists(path):
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path, media_type=rec.content_type or "application/octet-stream", filename=rec.filename)


@router.delete("/clients/{client_id}/files/{file_id}")
async def delete_client_file(client_id: str, file_id: str, db: OrmSession = Depends(get_db)) -> dict:
    _get_client_or_404(db, client_id)
    rec = db.get(ClientFile, kb.check_id(file_id, "file id"))
    if not rec or rec.client_id != client_id:
        raise HTTPException(404, "file not found")
    if rec.kb_doc_id:
        try:
            kb.delete_document(db, rec.kb_doc_id)
        except ValueError:
            pass
    path = os.path.abspath(os.path.join(CLIENT_FILE_DIR, rec.path))
    if path.startswith(os.path.abspath(CLIENT_FILE_DIR) + os.sep) and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    db.delete(rec)
    db.commit()
    return {"ok": True}


# ---------- Messages ----------

@router.get("/clients/{client_id}/messages")
async def list_messages(client_id: str, db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(WorkMessage)
        .filter(WorkMessage.client_id == client_id)
        .order_by(WorkMessage.created_at)
        .all()
    )
    return [store.message_out(m) for m in rows]


# ---------- Chat (SSE) ----------

_TYPE_LABEL = {"personal": "个人客户", "family": "家庭客户", "company": "企业客户"}


def _client_context(c: Client) -> str:
    members = ";".join(f"{m.name}({m.relation})" for m in c.members) or "未录入"
    policies = (
        ";".join(
            f"{p.line}·{p.product_name or '未名产品'}·保额{p.amount // 10000}万·{POLICY_STATUSES.get(p.status, p.status)}"
            for p in c.policies
        )
        or "暂无托管保单"
    )
    engagements = (
        ";".join(f"{ENGAGEMENT_KINDS.get(e.kind, e.kind)}:{e.title}" for e in c.engagements if e.status == "open")
        or "无进行中事项"
    )
    return (
        f"客户:{c.name}({_TYPE_LABEL.get(c.client_type, c.client_type)}) | 成员:{members} | "
        f"托管保单:{policies} | 进行中事项:{engagements} | 备注:{c.notes or '无'}"
    )


def _chat_system(c: Client, citations: list[dict]) -> str:
    kb_block = ""
    if citations:
        parts = []
        for i, cit in enumerate(citations, 1):
            parts.append(f"[{i}] 来源《{cit['docTitle']}》: {cit['text'][:300]}")
        kb_block = "\n\n知识库检索结果(回答时可引用,引用时标注 [n]):\n" + "\n".join(parts)
    return (
        "你是一位资深保险经纪人身边的智能助理,正在工作台里协助处理客户事务(寿险与财险)。\n"
        f"当前客户上下文: {_client_context(c)}\n"
        "要求:回答简洁、专业、可执行;给建议时说明依据;不编造客户不知道的信息;"
        "涉及具体产品与费率时提醒以条款为准。\n" + kb_block
    )


MOCK_REPLIES = [
    "好的,我已经看过{client}的档案。结合当前托管保单与进行中事项,建议先补齐缺失的保障维度,我可以直接发起一次保单检视。",
    "收到。我检索了知识库,没有找到与这个问题直接冲突的条款;从业务节奏看,{client}下一步适合推进在谈事项的方案确认。",
    "这个问题涉及具体产品条款,我建议以条款原文为准;需要的话我可以把相关条款从知识库调出来放到右侧工件区。",
]


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@router.post("/chat")
async def chat(
    body: ChatBody,
    db: OrmSession = Depends(get_db),
    user: Optional[User] = Depends(auth_svc.get_optional_user),
):
    """流式对话。

    注意:FastAPI 的 yield 依赖会在响应体开始发送前关闭 db session,
    所以生成器内部一律使用自建 session(SessionLocal),不复用注入的 db。
    """
    from app.services import credits

    c = _get_client_or_404(db, body.clientId)
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "empty message")
    # 已登录用户余额拦截;未登录为演示模式(不扣不拦)
    if user and not credits.has_credits(db, user):
        raise HTTPException(402, "积分不足:请续费套餐或购买积分包")
    user_id = user.id if user else None

    db.add(WorkMessage(client_id=c.id, role="user", content=message))
    db.commit()

    history_rows = (
        db.query(WorkMessage)
        .filter(WorkMessage.client_id == c.id)
        .order_by(WorkMessage.created_at)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in history_rows if m.content.strip()]
    client_id = c.id
    client_name = c.name

    from app.db import SessionLocal
    from app.services.agent import agent_available, run_agent_stream

    mode = "agent" if agent_available() else ("rag" if settings.has_llm else "mock")

    async def gen():
        db2 = SessionLocal()
        try:
            c2 = db2.get(Client, client_id)
            if mode == "agent":
                citations: list = []
                tool_events: list = []
                buffer: list[str] = []
                usage_tokens = 0
                try:
                    async for ev in run_agent_stream(db2, c2, history, message):
                        if ev["type"] == "delta":
                            buffer.append(ev["text"])
                            yield _sse({"type": "delta", "text": ev["text"]})
                        elif ev["type"] in ("tool_start", "tool_end"):
                            yield _sse(ev)
                        elif ev["type"] == "final":
                            citations = ev["citations"]
                            tool_events = ev["toolEvents"]
                            usage_tokens = ev.get("usageTokens", 0)
                            if not buffer and ev["content"]:
                                # 个别模型不走增量:最终文本一次性补发
                                buffer.append(ev["content"])
                                yield _sse({"type": "delta", "text": ev["content"]})
                except Exception as e:  # noqa: BLE001 agent 失败降级为纯 RAG 回答
                    logger.warning("agent stream failed, fallback to rag: %s", e)
                    async for chunk in _rag_fallback_stream(db2, c2, history, message, buffer, user_id):
                        yield chunk
                    return
                from app.services.agent import strip_fake_tool_calls

                full = strip_fake_tool_calls("".join(buffer))
                saved = WorkMessage(client_id=client_id, role="assistant", content=full)
                saved.citations_json = json.dumps(citations, ensure_ascii=False)
                saved.tool_events_json = json.dumps(tool_events, ensure_ascii=False)
                db2.add(saved)
                db2.commit()
                _consume_usage(db2, user_id, usage_tokens, "chat:agent")
                yield _sse({
                    "type": "done",
                    "content": full,
                    "citations": citations,
                    "toolEvents": tool_events,
                    "messageId": saved.id,
                })

            elif mode == "rag":
                async for chunk in _rag_fallback_stream(db2, c2, history, message, [], user_id):
                    yield chunk

            else:
                # mock 模式:检索仍真实,回答分片模拟流式
                try:
                    citations = await kb.search_async(db2, message, client_id=client_id, top_k=4)
                except Exception:  # noqa: BLE001
                    citations = []
                reply = MOCK_REPLIES[len(message) % len(MOCK_REPLIES)].format(client=client_name)
                if citations:
                    reply += f" 我在知识库里找到了 {len(citations)} 条相关资料,已在回答下方列出。"
                step = 12
                for i in range(0, len(reply), step):
                    yield _sse({"type": "delta", "text": reply[i : i + step]})
                saved = WorkMessage(client_id=client_id, role="assistant", content=reply)
                saved.citations_json = json.dumps(citations, ensure_ascii=False)
                db2.add(saved)
                db2.commit()
                yield _sse({"type": "done", "citations": citations, "messageId": saved.id})
        finally:
            db2.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _consume_usage(db: OrmSession, user_id: Optional[str], usage_tokens: int, ref: str) -> None:
    """按实际 usage 扣积分;未登录(演示模式)不扣。"""
    if not user_id or usage_tokens <= 0:
        return
    from app.services import credits

    u = db.get(User, user_id)
    if u:
        credits.consume(db, u, usage_tokens, ref=ref)


async def _rag_fallback_stream(
    db: OrmSession,
    c: Client,
    history: list[dict],
    message: str,
    already: list[str],
    user_id: Optional[str] = None,
):
    """agent 不可用/失败时的纯 RAG 流式回答。

    already 非空说明 agent 已流出部分文本:直接以已有内容收尾,避免前端拼接混乱。
    """
    if already:
        full = "".join(already).strip()
        saved = WorkMessage(client_id=c.id, role="assistant", content=full)
        db.add(saved)
        db.commit()
        yield _sse({"type": "done", "citations": [], "messageId": saved.id})
        return

    try:
        citations = await kb.search_async(db, message, client_id=c.id, top_k=4)
    except Exception as e:  # noqa: BLE001 检索失败不阻塞对话
        logger.warning("kb search failed: %s", e)
        citations = []

    from app.services.llm import _call_qianfan_stream

    messages = [{"role": "system", "content": _chat_system(c, citations)}]
    for m in history[-8:]:
        messages.append({"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]})
    if messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": message})

    buffer: list[str] = []
    usage_tokens = 0
    try:
        async for typ, payload in _call_qianfan_stream(messages, settings.model_fast):
            if typ == "usage":
                usage_tokens = int(payload.get("total_tokens", 0) or 0)
                continue
            if typ != "delta":
                continue
            buffer.append(payload)
            yield _sse({"type": "delta", "text": payload})
    except Exception as e:  # noqa: BLE001
        logger.warning("chat stream failed: %s", e)
        yield _sse({"type": "error", "message": str(e)[:200]})
    full = "".join(buffer).strip()
    saved = WorkMessage(client_id=c.id, role="assistant", content=full)
    saved.citations_json = json.dumps(citations, ensure_ascii=False)
    db.add(saved)
    db.commit()
    _consume_usage(db, user_id, usage_tokens, "chat:rag")
    yield _sse({"type": "done", "citations": citations, "messageId": saved.id})


# ---------- Tasks ----------

@router.post("/tasks")
async def create_task(
    body: TaskCreate,
    db: OrmSession = Depends(get_db),
    user: Optional[User] = Depends(auth_svc.get_optional_user),
) -> dict:
    c = _get_client_or_404(db, body.clientId)
    # 已登录用户按积分余额拦截(未登录演示不限制)
    if user:
        from app.services import credits

        if not credits.has_credits(db, user):
            raise HTTPException(402, "积分不足:请续费套餐或购买积分包")
    plan = task_engine.build_plan(body.kind, c, body.message or "")
    title = body.title or f"{c.name}·{(body.message or body.kind)[:40]}"
    task = Task(
        client_id=c.id,
        title=title[:200],
        kind=body.kind,
        created_by=user.id if user else None,
        plan_json=json.dumps(plan, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return store.task_out(task)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, db: OrmSession = Depends(get_db)) -> dict:
    task = db.get(Task, kb.check_id(task_id, "task id"))
    if not task:
        raise HTTPException(404, "task not found")
    return store.task_out(task)


@router.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, body: ApproveBody, db: OrmSession = Depends(get_db)) -> dict:
    task = db.get(Task, kb.check_id(task_id, "task id"))
    if not task:
        raise HTTPException(404, "task not found")
    if body.plan:
        task.plan_json = json.dumps(body.plan, ensure_ascii=False)
    task.status = "approved"
    db.commit()
    db.refresh(task)
    return store.task_out(task)


@router.post("/tasks/{task_id}/step")
async def step_task(task_id: str, db: OrmSession = Depends(get_db)) -> dict:
    task = db.get(Task, kb.check_id(task_id, "task id"))
    if not task:
        raise HTTPException(404, "task not found")
    if task.status in ("planned",):
        task.status = "running"
        db.commit()
    try:
        event, awaiting = await task_engine.execute_step(db, task)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not awaiting and event.status == "done":
        events = db.query(TaskEvent).filter(TaskEvent.task_id == task.id).all()
        plan = store.parse_plan(task.plan_json)
        if sum(1 for ev in events if ev.status in ("done", "confirmed")) >= len(plan):
            task.status = "done"
            db.commit()
    return {"event": store.event_out(event), "awaiting": awaiting, "taskStatus": task.status}


@router.post("/tasks/{task_id}/confirm")
async def confirm_task_event(task_id: str, body: ConfirmBody, db: OrmSession = Depends(get_db)) -> dict:
    task = db.get(Task, kb.check_id(task_id, "task id"))
    if not task:
        raise HTTPException(404, "task not found")
    try:
        event = task_engine.confirm_event(db, task, body.eventId)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"event": store.event_out(event)}


# ---------- Artifacts ----------

@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(artifact_id: str, fmt: str, db: OrmSession = Depends(get_db)):
    """把工件导出为真实办公文件:矩阵→xlsx,文档→docx/pptx。"""
    from urllib.parse import quote

    from fastapi.responses import Response

    from app.services import office

    a = db.get(Artifact, kb.check_id(artifact_id, "artifact id"))
    if not a:
        raise HTTPException(404, "artifact not found")
    if fmt not in office.MEDIA_TYPES:
        raise HTTPException(400, "fmt 必须是 xlsx / docx / pptx")
    content = json.loads(a.content_json or "{}")
    try:
        data = office.export_artifact(a.type, a.title, content, fmt)
    except ValueError as e:
        raise HTTPException(400, str(e))
    filename = f"{a.title}-v{a.version}.{fmt}"
    return Response(
        content=data,
        media_type=office.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/artifacts")
async def list_artifacts(clientId: str, db: OrmSession = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(Artifact)
        .filter(Artifact.client_id == clientId)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    # 同类型只保留最新版
    latest: dict[tuple, Artifact] = {}
    for a in rows:
        key = (a.client_id, a.type)
        if key not in latest:
            latest[key] = a
    return [store.artifact_out(a) for a in latest.values()]


# ---------- Todos ----------

@router.patch("/todos/{todo_id}")
async def patch_todo(todo_id: str, body: TodoPatch, db: OrmSession = Depends(get_db)) -> dict:
    todo = db.get(Todo, kb.check_id(todo_id, "todo id"))
    if not todo:
        raise HTTPException(404, "todo not found")
    if body.status is not None:
        if body.status not in ("open", "done"):
            raise HTTPException(400, "invalid status")
        todo.status = body.status
    db.commit()
    db.refresh(todo)
    return store.todo_out(todo)
