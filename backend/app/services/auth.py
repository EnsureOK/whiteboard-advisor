"""认证服务:pbkdf2 口令散列 + HS256 JWT。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db import get_db
from app.db_models import User

_ALGO = "HS256"
_TOKEN_TTL_HOURS = 24 * 7
_PBKDF2_ITER = 120_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=_TOKEN_TTL_HOURS)).timestamp())}
    return jwt.encode(payload, _jwt_key(), algorithm=_ALGO)


def _jwt_key() -> str:
    return settings.jwt_secret or "dev-secret-do-not-use-in-production"


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "displayName": u.display_name or u.username,
        "plan": u.plan,
        "planExpiresAt": u.plan_expires_at,
    }


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: OrmSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "未登录")
    try:
        payload = jwt.decode(credentials.credentials, _jwt_key(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "登录已过期,请重新登录")
    user = db.get(User, str(payload.get("sub") or ""))
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: OrmSession = Depends(get_db),
) -> Optional[User]:
    """未登录返回 None;token 无效也返回 None(不阻塞演示流程)。"""
    if credentials is None:
        return None
    try:
        payload = jwt.decode(credentials.credentials, _jwt_key(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    return db.get(User, str(payload.get("sub") or ""))
