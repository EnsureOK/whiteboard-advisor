"""云端 LLM 代理网关(WB_ROLE=cloud 时挂载)。

千帆 OpenAI 兼容协议的透明代理:桌面端把 QIANFAN_BASE_URL 指到
https://<cloud>/llm/v2、QIANFAN_API_KEY 换成用户登录 JWT,本地 llm.py /
agents SDK / embedding.py 一行不改。

职责:验用户 token -> 查积分余额(不足 402) -> 换真实千帆 key 转发
(流式透传) -> 从响应 usage 计量扣积分(服务端扣,分发包不可绕过)。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db import get_db
from app.db_models import User
from app.services import auth as auth_svc
from app.services import credits

logger = logging.getLogger("whiteboard-advisor.llm-gateway")

router = APIRouter(prefix="/llm/v2")

_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


def _upstream_headers() -> dict:
    return {"Authorization": f"Bearer {settings.qianfan_api_key}", "Content-Type": "application/json"}


def _err(status: int, message: str, code: str) -> HTTPException:
    # OpenAI 风格错误体,客户端 SDK 能解析出 message
    return HTTPException(status, {"error": {"message": message, "type": code, "code": code}})


def _gate(db: OrmSession, user: User) -> None:
    if not settings.has_llm:
        raise _err(503, "网关未配置上游模型 key", "gateway_not_configured")
    if not credits.has_credits(db, user):
        raise _err(402, "积分不足:请在工作台计费面板续费套餐或购买积分包", "insufficient_credits")


def _consume_tokens(user_id: str, tokens: int, ref: str) -> None:
    """流结束后独立会话扣费(请求级 session 在流式期间已释放)。"""
    if tokens <= 0:
        return
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if u:
            credits.consume(db, u, tokens, ref=ref)
    except Exception as e:  # noqa: BLE001 计量失败记日志,不影响已返回的响应
        logger.warning("gateway consume failed: %s", e)
    finally:
        db.close()


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(auth_svc.get_current_user),
    db: OrmSession = Depends(get_db),
):
    _gate(db, user)
    body = await request.json()
    url = f"{settings.qianfan_base_url}/chat/completions"
    user_id = user.id

    if body.get("stream"):
        # 流式:SSE 逐块透传,同时解析各 chunk 的 usage(include_usage 时最后一块携带)
        body.setdefault("stream_options", {})["include_usage"] = True

        async def gen():
            total = 0
            client = httpx.AsyncClient(timeout=_TIMEOUT)
            try:
                async with client.stream("POST", url, headers=_upstream_headers(), json=body) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "ignore")[:300]
                        yield f'data: {json.dumps({"error": {"message": detail, "code": "upstream_error"}})}\n\n'.encode()
                        return
                    buf = b""
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if not line.startswith(b"data:"):
                                continue
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                continue
                            try:
                                usage = json.loads(data).get("usage") or {}
                                if usage.get("total_tokens"):
                                    total = int(usage["total_tokens"])
                            except ValueError:
                                pass
            finally:
                await client.aclose()
                _consume_tokens(user_id, total, "gateway:chat")

        return StreamingResponse(gen(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=_upstream_headers(), json=body)
    if resp.status_code == 200:
        try:
            total = int((resp.json().get("usage") or {}).get("total_tokens") or 0)
        except ValueError:
            total = 0
        _consume_tokens(user_id, total, "gateway:chat")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))


@router.post("/embeddings")
async def embeddings(
    request: Request,
    user: User = Depends(auth_svc.get_current_user),
    db: OrmSession = Depends(get_db),
):
    _gate(db, user)
    body = await request.json()
    url = f"{settings.qianfan_base_url}/embeddings"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=_upstream_headers(), json=body)
    if resp.status_code == 200:
        try:
            total = int((resp.json().get("usage") or {}).get("total_tokens") or 0)
        except ValueError:
            total = 0
        _consume_tokens(user.id, total, "gateway:embedding")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))


@router.get("/models")
async def models(user: Optional[User] = Depends(auth_svc.get_optional_user)):
    """连通性探测(桌面端设置页可用);不鉴权失败也返回网关标识。"""
    return {"object": "list", "gateway": "workbench-cloud",
            "data": [{"id": settings.model_fast, "object": "model"}]}
