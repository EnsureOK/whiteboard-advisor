"""工作台数据访问与序列化。"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from app.db_models import (
    ENGAGEMENT_KINDS,
    POLICY_STATUSES,
    Artifact,
    Client,
    ClientFile,
    Engagement,
    KbDocument,
    Member,
    Policy,
    Task,
    Todo,
    WorkMessage,
)


def parse_plan(plan_json: str) -> list[dict]:
    try:
        steps = json.loads(plan_json or "[]")
        return steps if isinstance(steps, list) else []
    except json.JSONDecodeError:
        return []


def member_out(m: Member) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "relation": m.relation,
        "badge": m.badge,
        "birthday": m.birthday,
        "notes": m.notes,
        "seq": m.seq,
    }


def policy_out(p: Policy) -> dict:
    return {
        "id": p.id,
        "clientId": p.client_id,
        "memberId": p.member_id,
        "line": p.line,
        "productName": p.product_name,
        "insurer": p.insurer,
        "amount": p.amount,
        "premium": p.premium,
        "effectiveDate": p.effective_date,
        "expiryDate": p.expiry_date,
        "status": p.status,
        "statusLabel": POLICY_STATUSES.get(p.status, p.status),
        "notes": p.notes,
    }


def engagement_out(e: Engagement) -> dict:
    return {
        "id": e.id,
        "clientId": e.client_id,
        "kind": e.kind,
        "kindLabel": ENGAGEMENT_KINDS.get(e.kind, e.kind),
        "title": e.title,
        "line": e.line,
        "policyId": e.policy_id,
        "status": e.status,
        "note": e.note,
        "createdAt": e.created_at,
    }


def client_file_out(f: ClientFile) -> dict:
    return {
        "id": f.id,
        "clientId": f.client_id,
        "filename": f.filename,
        "contentType": f.content_type,
        "sizeBytes": f.size_bytes,
        "kind": f.kind,
        "kbDocId": f.kb_doc_id,
        "createdAt": f.created_at,
    }


def client_out(c: Client) -> dict:
    open_engagements = [e for e in c.engagements if e.status == "open"]
    return {
        "id": c.id,
        "name": c.name,
        "type": c.client_type,
        "notes": c.notes,
        "nextContact": c.next_contact,
        "members": [member_out(m) for m in c.members],
        "policies": [policy_out(p) for p in c.policies],
        "engagements": [engagement_out(e) for e in open_engagements],
        "fileCount": len(c.files),
    }


def event_out(e) -> dict:
    return {
        "id": e.id,
        "seq": e.seq,
        "type": e.type,
        "title": e.title,
        "status": e.status,
        "payload": _safe_json(e.payload_json),
        "createdAt": e.created_at,
    }


def task_out(t: Task) -> dict:
    return {
        "id": t.id,
        "clientId": t.client_id,
        "title": t.title,
        "kind": t.kind,
        "status": t.status,
        "plan": parse_plan(t.plan_json),
        "events": [event_out(e) for e in t.events],
        "createdAt": t.created_at,
        "updatedAt": t.updated_at,
    }


def artifact_out(a: Artifact) -> dict:
    return {
        "id": a.id,
        "clientId": a.client_id,
        "taskId": a.task_id,
        "type": a.type,
        "title": a.title,
        "version": a.version,
        "content": _safe_json(a.content_json),
        "createdAt": a.created_at,
    }


def todo_out(t: Todo) -> dict:
    return {
        "id": t.id,
        "clientId": t.client_id,
        "title": t.title,
        "detail": t.detail,
        "priority": t.priority,
        "status": t.status,
        "due": t.due,
    }


def message_out(m: WorkMessage) -> dict:
    return {
        "id": m.id,
        "clientId": m.client_id,
        "role": m.role,
        "content": m.content,
        "citations": _safe_json(m.citations_json) or [],
        "toolEvents": _safe_json(m.tool_events_json) or [],
        "taskId": m.task_id,
        "createdAt": m.created_at,
    }


def kb_doc_out(d: KbDocument, *, with_chunks: bool = False) -> dict:
    out = {
        "id": d.id,
        "title": d.title,
        "filename": d.filename,
        "docType": d.doc_type,
        "sizeBytes": d.size_bytes,
        "tags": _safe_json(d.tags_json),
        "scope": d.scope,
        "status": d.status,
        "error": d.error,
        "chunkCount": d.chunk_count,
        "sourceUrl": d.source_url,
        "createdAt": d.created_at,
        "updatedAt": d.updated_at,
    }
    if with_chunks:
        out["chunks"] = [
            {"id": c.id, "seq": c.seq, "text": c.text, "hasEmbedding": c.embedding is not None}
            for c in d.chunks
        ]
    return out


def _safe_json(raw: str) -> object:
    try:
        return json.loads(raw or "null")
    except json.JSONDecodeError:
        return None


def get_client(db: OrmSession, client_id: str) -> Optional[Client]:
    return db.get(Client, client_id)
