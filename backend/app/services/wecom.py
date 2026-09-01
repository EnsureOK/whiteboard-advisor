"""企业微信群机器人推送(单向,零资质门槛)。

在企微群里添加"群机器人"即得 webhook 地址,.env 配置:
  WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
用于每日简报、任务完成等通知。双向对话通道(企微自建应用)另行接入。
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger("whiteboard-advisor.wecom")


async def push_markdown(content: str) -> bool:
    """推送 markdown 消息到企微群;未配置 webhook 返回 False。"""
    url = settings.wecom_webhook_url
    if not url:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            json={"msgtype": "markdown", "markdown": {"content": content[:4000]}},
        )
        data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"wecom push error: {data}")
    return True
