"""联网搜索(agent 工具后端)。

Provider:
- bocha(推荐,配 BOCHA_API_KEY): 博查 API,面向 LLM 的中文搜索,生产用
- ddg(兜底,零 key): DuckDuckGo html 轻量版抓取,可用性一般,开发/内测用
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from urllib.parse import unquote

import httpx

from app.config import settings

logger = logging.getLogger("whiteboard-advisor.websearch")


async def search(query: str, count: int = 5) -> list[dict]:
    """返回 [{title, url, snippet}];失败返回空列表(工具层报'搜索不可用')。"""
    if settings.bocha_api_key:
        try:
            return await _search_bocha(query, count)
        except Exception as e:  # noqa: BLE001 博查失败落 ddg
            logger.warning("bocha search failed: %s", e)
    try:
        return await _search_ddg(query, count)
    except Exception as e:  # noqa: BLE001
        logger.warning("ddg search failed: %s", e)
        return []


async def _search_bocha(query: str, count: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {settings.bocha_api_key}"},
            json={"query": query, "count": count, "summary": True},
        )
        resp.raise_for_status()
        data = resp.json()
    pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    return [
        {
            "title": p.get("name", ""),
            "url": p.get("url", ""),
            "snippet": (p.get("summary") or p.get("snippet") or "")[:300],
        }
        for p in pages[:count]
    ]


_DDG_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)?',
    re.S,
)


def _strip(t: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


async def _search_ddg(query: str, count: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; WorkbenchAgent/0.1)"},
        )
        resp.raise_for_status()
    out = []
    for m in _DDG_RE.finditer(resp.text):
        url = m.group(1)
        # ddg 重定向链接:uddg 参数里是真实 URL
        um = re.search(r"[?&]uddg=([^&]+)", url)
        if um:
            url = unquote(um.group(1))
        out.append({"title": _strip(m.group(2)), "url": url, "snippet": _strip(m.group(3))[:300]})
        if len(out) >= count:
            break
    return out
