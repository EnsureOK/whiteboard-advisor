"""企业微信自建应用·双向对话通道(经纪人在企微里 @助理干活)。

激活条件(.env,由企微管理后台获得):
  WECOM_CORP_ID=...            WECOM_APP_SECRET=...
  WECOM_APP_TOKEN=...          WECOM_APP_AES_KEY=...(43位 EncodingAESKey)
  WECOM_APP_AGENTID=...
未配置时本路由返回 404,不影响其他功能。

企微后台"接收消息"回调地址填: https://你的域名/api/wecom/callback

指令协议(V1):
  简报                -> 回当日简报(未生成则即时生成)
  客户                -> 回客户列表
  @客户名 问题/任务    -> 绑定该客户跑 agent,结果主动推送回来
  其他文本            -> 使用提示

被动回复 5 秒超时装不下 agent,一律先回"已收到,处理中",完成后用
应用消息主动推送(access_token 会缓存)。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings

logger = logging.getLogger("whiteboard-advisor.wecom-app")

router = APIRouter(prefix="/api/wecom")


def _enabled() -> bool:
    return bool(
        settings.wecom_corp_id
        and settings.wecom_app_secret
        and settings.wecom_app_token
        and settings.wecom_app_aes_key
    )


# ---------- 加解密(企微 WXBizMsgCrypt 协议:AES-256-CBC + SHA1 签名) ----------

def _aes_key() -> bytes:
    return base64.b64decode(settings.wecom_app_aes_key + "=")


def _sign(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    raw = "".join(sorted([token, timestamp, nonce, encrypt]))
    return hashlib.sha1(raw.encode()).hexdigest()


def decrypt_msg(encrypt_b64: str) -> str:
    """解密 Encrypt 字段,返回明文 XML(校验 corp_id)。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _aes_key()
    data = base64.b64decode(encrypt_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    dec = cipher.decryptor()
    plain = dec.update(data) + dec.finalize()
    pad = plain[-1]
    plain = plain[:-pad]
    # 16 随机字节 | 4 字节网络序长度 | msg | corp_id
    msg_len = struct.unpack(">I", plain[16:20])[0]
    msg = plain[20 : 20 + msg_len].decode("utf-8")
    corp = plain[20 + msg_len :].decode("utf-8")
    if corp != settings.wecom_corp_id:
        raise ValueError("corp_id mismatch")
    return msg


def encrypt_msg(reply_xml: str) -> str:
    """按企微协议加密明文 XML,返回 Encrypt 的 base64。"""
    import os as _os

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _aes_key()
    msg = reply_xml.encode("utf-8")
    payload = _os.urandom(16) + struct.pack(">I", len(msg)) + msg + settings.wecom_corp_id.encode()
    pad = 32 - len(payload) % 32
    payload += bytes([pad]) * pad
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    enc = cipher.encryptor()
    return base64.b64encode(enc.update(payload) + enc.finalize()).decode()


def _passive_reply(to_user: str, content: str) -> str:
    """构造加密的被动回复 XML。"""
    now = str(int(time.time()))
    nonce = base64.b16encode(socket.gethostname()[:6].encode()).decode()[:10] + now[-4:]
    plain = (
        f"<xml><ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{settings.wecom_corp_id}]]></FromUserName>"
        f"<CreateTime>{now}</CreateTime><MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content></xml>"
    )
    encrypt = encrypt_msg(plain)
    sig = _sign(settings.wecom_app_token, now, nonce, encrypt)
    return (
        f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
        f"<TimeStamp>{now}</TimeStamp><Nonce><![CDATA[{nonce}]]></Nonce></xml>"
    )


# ---------- 主动推送 ----------

_token_cache: dict = {"token": "", "expires": 0.0}


async def _access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": settings.wecom_corp_id, "corpsecret": settings.wecom_app_secret},
        )
        data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"wecom gettoken failed: {data}")
    _token_cache["token"] = data["access_token"]
    _token_cache["expires"] = time.time() + int(data.get("expires_in", 7200))
    return _token_cache["token"]


async def push_text(to_user: str, content: str) -> None:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
            json={
                "touser": to_user,
                "msgtype": "text",
                "agentid": int(settings.wecom_app_agentid or 0),
                "text": {"content": content[:2000]},
            },
        )
        data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"wecom push failed: {data}")


# ---------- 指令处理 ----------

async def _handle_command(from_user: str, text: str) -> None:
    """异步处理企微指令,结果主动推送。"""
    from app.db import SessionLocal
    from app.db_models import Client, DailyBriefing

    db = SessionLocal()
    try:
        text = text.strip()
        if text in ("简报", "今日简报"):
            from datetime import date as _date

            from app.services import scheduler

            row = db.query(DailyBriefing).filter(DailyBriefing.date == _date.today().isoformat()).first()
            content = row.content if row else await scheduler.generate_briefing(db)
            await push_text(from_user, content)
            return
        if text in ("客户", "客户列表"):
            names = [c.name for c in db.query(Client).all()]
            await push_text(from_user, "客户列表:\n" + "\n".join(f"- {n}" for n in names[:30]))
            return
        if text.startswith("@"):
            rest = text[1:]
            clients = db.query(Client).all()
            target = next((c for c in clients if rest.startswith(c.name)), None)
            if target is None:
                # 名字前缀模糊匹配
                target = next((c for c in clients if c.name and c.name[:2] in rest[:6]), None)
            if target is None:
                await push_text(from_user, "没找到这个客户。发「客户」查看列表,格式:@客户名 你的问题")
                return
            message = rest[len(target.name) :].strip() or "给我这位客户的近况摘要"
            from app.services.agent import agent_available, run_agent_stream, strip_fake_tool_calls

            if not agent_available():
                await push_text(from_user, "AI 通道未配置,暂无法处理。")
                return
            buffer: list[str] = []
            async for ev in run_agent_stream(db, target, [], message):
                if ev["type"] == "delta":
                    buffer.append(ev["text"])
                elif ev["type"] == "final" and not buffer and ev["content"]:
                    buffer.append(ev["content"])
            reply = strip_fake_tool_calls("".join(buffer)) or "(没有产出回答)"
            await push_text(from_user, f"【{target.name}】\n{reply}")
            return
        await push_text(
            from_user,
            "可用指令:\n简报 — 今日工作简报\n客户 — 客户列表\n@客户名 问题 — 让助理处理该客户的事",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("wecom command failed: %s", e)
        try:
            await push_text(from_user, f"处理失败:{str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


# ---------- 回调端点 ----------

@router.get("/callback")
async def verify_url(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    """企微后台配置回调地址时的 URL 验证。"""
    if not _enabled():
        raise HTTPException(404, "wecom app not configured")
    if _sign(settings.wecom_app_token, timestamp, nonce, echostr) != msg_signature:
        raise HTTPException(403, "bad signature")
    return Response(content=decrypt_msg(echostr), media_type="text/plain")


@router.post("/callback")
async def receive_message(request: Request, msg_signature: str, timestamp: str, nonce: str):
    if not _enabled():
        raise HTTPException(404, "wecom app not configured")
    body = await request.body()
    root = ET.fromstring(body)
    encrypt = root.findtext("Encrypt") or ""
    if _sign(settings.wecom_app_token, timestamp, nonce, encrypt) != msg_signature:
        raise HTTPException(403, "bad signature")
    plain = ET.fromstring(decrypt_msg(encrypt))
    msg_type = plain.findtext("MsgType") or ""
    from_user = plain.findtext("FromUserName") or ""
    if msg_type == "text":
        content = plain.findtext("Content") or ""
        asyncio.get_running_loop().create_task(_handle_command(from_user, content))
        return Response(
            content=_passive_reply(from_user, "已收到,处理中…结果稍后发你。"),
            media_type="application/xml",
        )
    return Response(content="", media_type="text/plain")
