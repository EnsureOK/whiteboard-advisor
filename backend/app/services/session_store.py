"""Session 存储:内存 + 文件持久化(JSON)。

V0.1 用文件持久化(单机即可),支持跨进程 resume。M_future 可换 PostgreSQL+S3。
每轮对话后 save();start 携带 sessionId 时 load() 续航。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.models.schemas import Language, Session, SessionState
from app.services import zone_engine

logger = logging.getLogger("whiteboard-advisor.store")

from app.paths import DATA_DIR as _WB_DATA_DIR

_BASE = Path(_WB_DATA_DIR)
_DATA_DIR = _BASE / "sessions"
_SHARE_DIR = _BASE / "shares"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_SHARE_DIR.mkdir(parents=True, exist_ok=True)

SHARE_TTL_DAYS = 7

_CACHE: dict[str, Session] = {}


def _path(session_id: str) -> Path:
    return _DATA_DIR / f"{session_id}.json"


def save(session: Session) -> None:
    from datetime import datetime, timezone

    session.last_active_at = datetime.now(timezone.utc)
    _CACHE[session.id] = session
    try:
        _path(session.id).write_text(session.model_dump_json(indent=2), encoding="utf-8")
    except OSError:
        logger.exception("session 持久化失败: %s", session.id)


def _load(session_id: str) -> Optional[Session]:
    if session_id in _CACHE:
        return _CACHE[session_id]
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        s = Session.model_validate_json(p.read_text(encoding="utf-8"))
        _CACHE[s.id] = s
        return s
    except Exception:  # noqa: BLE001
        logger.exception("session 读取失败: %s", session_id)
        return None


def create(language: Language = Language.zh, template_id: str = "family-protection") -> Session:
    s = Session(language=language, template_id=template_id, state=SessionState.template_loaded)
    zone_engine.init_zones(s)
    save(s)
    return s


def get(session_id: str) -> Optional[Session]:
    return _load(session_id)


def get_or_create(
    session_id: Optional[str], language: Language = Language.zh, template_id: str = "family-protection"
) -> Session:
    if session_id:
        existing = _load(session_id)
        if existing:
            return existing
    return create(language, template_id)


def ensure_share(session: Session) -> str:
    """生成(或刷新)只读分享 token,默认 7 天过期。"""
    token = secrets.token_urlsafe(12)
    session.share_token = token
    session.share_expires_at = datetime.now(timezone.utc) + timedelta(days=SHARE_TTL_DAYS)
    (_SHARE_DIR / f"{token}.txt").write_text(session.id, encoding="utf-8")
    save(session)
    return token


def resolve_share(token: str) -> Optional[Session]:
    """返回 token 对应且未过期的 session;过期或无效返回 None。"""
    ptr = _SHARE_DIR / f"{token}.txt"
    if not ptr.exists():
        return None
    session = _load(ptr.read_text(encoding="utf-8").strip())
    if not session or session.share_token != token:
        return None
    if session.share_expires_at and session.share_expires_at < datetime.now(timezone.utc):
        return None
    return session


def set_template(session: Session, template_id: str) -> Session:
    """切换模板:重置 zones 为新模板骨架(已有同名 zone 数据保留)。"""
    session.template_id = template_id
    old = session.zones
    session.zones = {}
    zone_engine.init_zones(session)
    for zid, z in session.zones.items():
        if zid in old and old[zid].data:
            session.zones[zid] = old[zid]
    save(session)
    return session
