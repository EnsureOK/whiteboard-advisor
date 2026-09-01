"""认证 API:注册 / 登录 / 当前用户。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.db import get_db
from app.db_models import User
from app.services import auth as auth_svc

router = APIRouter(prefix="/api/auth")

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{3,30}$")


class Credentials(BaseModel):
    username: str
    password: str
    displayName: str = ""


class SmsSendBody(BaseModel):
    phone: str


class SmsVerifyBody(BaseModel):
    phone: str
    code: str


@router.post("/sms/send")
async def sms_send(body: SmsSendBody, db: OrmSession = Depends(get_db)) -> dict:
    """发送登录验证码。未配置短信服务商时验证码写入数据目录 sms-outbox.log(内测)。"""
    from app.services import sms

    try:
        return sms.request_code(db, body.phone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(429, str(e))


@router.post("/sms/verify")
async def sms_verify(body: SmsVerifyBody, db: OrmSession = Depends(get_db)) -> dict:
    """校验验证码并登录;手机号首次登录自动创建账号。"""
    from app.services import sms

    try:
        phone = sms.normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not sms.verify_code(db, phone, body.code):
        raise HTTPException(401, "验证码错误或已过期")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(
            username=phone,
            phone=phone,
            password_hash="",  # 验证码登录,无密码
            display_name=f"经纪人{phone[-4:]}",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return {"token": auth_svc.create_token(user.id), "user": auth_svc.user_out(user)}


@router.post("/register")
async def register(body: Credentials, db: OrmSession = Depends(get_db)) -> dict:
    username = body.username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "用户名需为 3-30 位字母/数字/下划线/中文")
    if len(body.password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(409, "用户名已被注册")
    user = User(
        username=username,
        password_hash=auth_svc.hash_password(body.password),
        display_name=(body.displayName or username)[:60],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 免费积分改为登录后显式领取(POST /api/billing/claim-welcome),注册不再自动发
    return {"token": auth_svc.create_token(user.id), "user": auth_svc.user_out(user)}


@router.post("/login")
async def login(body: Credentials, db: OrmSession = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.username == body.username.strip()).first()
    # 验证码登录创建的账号无密码,不允许走密码通道
    if not user or not user.password_hash or not auth_svc.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": auth_svc.create_token(user.id), "user": auth_svc.user_out(user)}


@router.get("/me")
async def me(user: User = Depends(auth_svc.get_current_user)) -> dict:
    return {"user": auth_svc.user_out(user)}
