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
    return {"token": auth_svc.create_token(user.id), "user": auth_svc.user_out(user)}


@router.post("/login")
async def login(body: Credentials, db: OrmSession = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if not user or not auth_svc.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": auth_svc.create_token(user.id), "user": auth_svc.user_out(user)}


@router.get("/me")
async def me(user: User = Depends(auth_svc.get_current_user)) -> dict:
    return {"user": auth_svc.user_out(user)}
