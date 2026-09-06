"""支付宝当面付(扫码支付):预下单出二维码 + 主动查单 + 异步通知验签。

自实现 RSA2(SHA256withRSA)签名,只依赖已有的 cryptography,不引第三方 SDK。
沙箱联调:开放平台沙箱应用的 APPID/密钥 + 沙箱网关(config 默认);
生产切换:换正式凭证 + 网关 https://openapi.alipay.com/gateway.do,代码零改动。

响应验签说明:同步响应经 HTTPS 返回,联调期信任传输层不验响应签名;
异步通知(notify)是入账依据,必须验签(verify_notify)。
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings

logger = logging.getLogger("whiteboard-advisor.alipay")

_CST = timezone(timedelta(hours=8))  # 支付宝要求北京时间


def _pem_wrap(key_body: str, kind: str) -> bytes:
    """兼容纯 base64 体(开放平台工具默认给的格式)与完整 PEM。"""
    body = key_body.strip()
    if "BEGIN" in body:
        return body.encode()
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {kind}-----\n{lines}\n-----END {kind}-----\n".encode()


def _sign(unsigned: str) -> str:
    key = serialization.load_pem_private_key(
        _pem_wrap(settings.alipay_app_private_key, "PRIVATE KEY"), password=None
    )
    sig = key.sign(unsigned.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _verify(unsigned: str, sign_b64: str) -> bool:
    try:
        pub = serialization.load_pem_public_key(_pem_wrap(settings.alipay_public_key, "PUBLIC KEY"))
        pub.verify(base64.b64decode(sign_b64), unsigned.encode("utf-8"),
                   padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:  # noqa: BLE001 验签失败一律 False
        return False


def _ordered_query(params: dict) -> str:
    return "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None and params[k] != "")


async def _call(method: str, biz_content: dict) -> dict:
    params = {
        "app_id": settings.alipay_appid,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if settings.alipay_notify_url:
        params["notify_url"] = settings.alipay_notify_url
    params["sign"] = _sign(_ordered_query(params))
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            settings.alipay_gateway,
            content=urlencode(params).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
    data = resp.json()
    node = method.replace(".", "_") + "_response"
    return data.get(node) or {}


async def precreate(out_trade_no: str, amount_cents: int, subject: str) -> dict:
    """预下单,返回 {qrCode, qrImage(base64 png)} 或抛 ValueError。"""
    r = await _call("alipay.trade.precreate", {
        "out_trade_no": out_trade_no,
        "total_amount": f"{amount_cents / 100:.2f}",
        "subject": subject[:256],
        "timeout_express": "30m",
    })
    if r.get("code") != "10000":
        raise ValueError(f"支付宝下单失败: {r.get('code')} {r.get('sub_msg') or r.get('msg')}")
    qr = r["qr_code"]
    return {"qrCode": qr, "qrImage": _qr_png_base64(qr)}


async def query(out_trade_no: str) -> dict:
    """主动查单;返回 {status: paid|pending|closed, tradeNo}。"""
    r = await _call("alipay.trade.query", {"out_trade_no": out_trade_no})
    trade_status = r.get("trade_status", "")
    if r.get("code") == "10000" and trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return {"status": "paid", "tradeNo": r.get("trade_no", "")}
    if trade_status == "TRADE_CLOSED":
        return {"status": "closed", "tradeNo": r.get("trade_no", "")}
    return {"status": "pending", "tradeNo": r.get("trade_no", "")}


def verify_notify(form: dict) -> bool:
    """异步通知验签(入账依据,必须通过)。"""
    sign = form.get("sign", "")
    unsigned = "&".join(
        f"{k}={v}" for k, v in sorted(form.items()) if k not in ("sign", "sign_type") and v != ""
    )
    return bool(sign) and _verify(unsigned, sign)


def _qr_png_base64(text: str) -> str:
    import qrcode

    img = qrcode.make(text, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
