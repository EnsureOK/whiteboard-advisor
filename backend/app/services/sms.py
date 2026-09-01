"""短信验证码服务:发送(provider 抽象) + 校验 + 防刷。

Provider:
- 未配置(默认): 验证码写入数据目录 sms-outbox.log 与应用日志,界面不回显
  (内测期管理员可查;配置服务商后自动切真发,零代码改动)
- aliyun: 阿里云短信 HTTP API 直调(无 SDK 依赖),.env 配置:
    SMS_PROVIDER=aliyun
    SMS_ALIYUN_AK_ID=...        SMS_ALIYUN_AK_SECRET=...
    SMS_SIGN_NAME=签名          SMS_TEMPLATE_CODE=SMS_xxx  (模板变量名 code)

防刷:同号 60s 冷却;验证码 5 分钟有效;错 5 次作废;新码作废旧码。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db_models import SmsCode, utcnow_iso
from app.paths import DATA_DIR

logger = logging.getLogger("whiteboard-advisor.sms")

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")  # 大陆手机号

CODE_TTL_MINUTES = 5
SEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone(raw: str) -> str:
    p = re.sub(r"[\s\-+]", "", raw or "")
    if p.startswith("86") and len(p) == 13:
        p = p[2:]
    if not PHONE_RE.fullmatch(p):
        raise ValueError("手机号格式不正确")
    return p


def request_code(db: OrmSession, phone: str) -> dict:
    """生成并下发验证码。返回 {sent, cooldown, provider}。"""
    phone = normalize_phone(phone)

    latest = (
        db.query(SmsCode)
        .filter(SmsCode.phone == phone, SmsCode.status == "pending")
        .order_by(SmsCode.created_at.desc())
        .first()
    )
    if latest:
        created = datetime.fromisoformat(latest.created_at)
        elapsed = (_now() - created).total_seconds()
        if elapsed < SEND_COOLDOWN_SECONDS:
            raise PermissionError(f"发送太频繁,请 {int(SEND_COOLDOWN_SECONDS - elapsed)} 秒后再试")
        latest.status = "void"

    code = f"{secrets.randbelow(1_000_000):06d}"
    rec = SmsCode(
        phone=phone,
        code=code,
        expires_at=(_now() + timedelta(minutes=CODE_TTL_MINUTES)).isoformat(),
    )
    db.add(rec)
    db.commit()

    provider = _deliver(phone, code)
    return {"sent": True, "cooldown": SEND_COOLDOWN_SECONDS, "provider": provider}


def verify_code(db: OrmSession, phone: str, code: str) -> bool:
    phone = normalize_phone(phone)
    rec = (
        db.query(SmsCode)
        .filter(SmsCode.phone == phone, SmsCode.status == "pending")
        .order_by(SmsCode.created_at.desc())
        .first()
    )
    if not rec:
        return False
    if datetime.fromisoformat(rec.expires_at) < _now():
        rec.status = "void"
        db.commit()
        return False
    if rec.attempts >= MAX_ATTEMPTS:
        rec.status = "void"
        db.commit()
        return False
    if not hmac.compare_digest(rec.code, (code or "").strip()):
        rec.attempts += 1
        db.commit()
        return False
    rec.status = "used"
    db.commit()
    return True


# ---------- 发送通道 ----------

def _deliver(phone: str, code: str) -> str:
    provider = (getattr(settings, "sms_provider", "") or "").lower()
    if provider == "aliyun":
        try:
            _send_aliyun(phone, code)
            return "aliyun"
        except Exception as e:  # noqa: BLE001 真发失败落到 outbox,不吞验证码
            logger.error("aliyun sms failed, falling back to outbox: %s", e)
    _send_outbox(phone, code)
    return "outbox"


def _send_outbox(phone: str, code: str) -> None:
    """未配置服务商:验证码写数据目录 sms-outbox.log(仅管理员可见)。"""
    line = f"{utcnow_iso()}  {phone}  code={code}\n"
    try:
        with open(os.path.join(DATA_DIR, "sms-outbox.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    logger.info("SMS outbox: %s -> %s", phone, code)


def _send_aliyun(phone: str, code: str) -> None:
    """阿里云短信 SendSms(RPC 签名 V1),HTTP 直调零 SDK。"""
    params = {
        "AccessKeyId": settings.sms_aliyun_ak_id,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou",
        "SignName": settings.sms_sign_name,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "TemplateCode": settings.sms_template_code,
        "TemplateParam": '{"code":"%s"}' % code,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }

    def enc(s: str) -> str:
        return urllib.parse.quote(s, safe="~")

    canonical = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
    to_sign = "GET&%2F&" + enc(canonical)
    sig = hmac.new(
        (settings.sms_aliyun_ak_secret + "&").encode(), to_sign.encode(), hashlib.sha1
    ).digest()
    import base64

    params["Signature"] = base64.b64encode(sig).decode()
    resp = httpx.get("https://dysmsapi.aliyuncs.com/", params=params, timeout=10)
    data = resp.json()
    if data.get("Code") != "OK":
        raise RuntimeError(f"aliyun sms error: {data.get('Code')} {data.get('Message')}")
