"""知识抓取「可运行小样例」——非全量爬虫。

抓取单个公开监管/科普页面,抽取正文文本,清洗为 1 条 KnowledgeChunk 追加到
app/data/knowledge_seed.json。

⚠️ 合规说明:
- 全量、定期的 carrier/监管数据抓取,以及随之而来的版权/ToS/法务 review,属于本仓库
  之外的工程与法务工作,不在 V0.1 范围内。
- 本脚本仅做单页样例:请只对允许抓取的公开页面使用,遵守目标站点 robots.txt 与频率限制,
  不要并发批量抓取。

用法:
    python -m scripts.scrape_sample <URL> --jurisdiction US --category regulation \
        --keywords 遗产税,estate
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

SEED_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "knowledge_seed.json"
_SKIP_TAGS = {"script", "style", "head", "nav", "footer"}


def _assert_public_http_url(url: str) -> None:
    """SSRF 防护:仅允许 http(s) 公网地址,拒绝内网/回环/链路本地/保留网段。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SystemExit("仅允许 http(s) URL")
    host = parsed.hostname or ""
    if not host:
        raise SystemExit("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise SystemExit(f"域名解析失败: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise SystemExit(f"拒绝访问非公网地址(SSRF 防护): {ip}")


def _fetch_public_text(url: str) -> str:
    """抓取公网页面正文:协议/目标 IP 校验 + 禁止重定向(防 DNS rebinding 绕过)。"""
    _assert_public_http_url(url)
    resp = httpx.get(
        url,
        timeout=20,
        headers={"User-Agent": "WhiteboardAdvisor-sample/0.1"},
        follow_redirects=False,
    )
    resp.raise_for_status()
    return resp.text


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)


def extract_text(html: str, max_chars: int = 600) -> str:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return text[:max_chars]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--jurisdiction", default="global")
    ap.add_argument("--category", default="regulation")
    ap.add_argument("--keywords", default="", help="逗号分隔")
    args = ap.parse_args()

    text = extract_text(_fetch_public_text(args.url))
    if not text:
        raise SystemExit("未能抽取到正文文本")

    chunk = {
        "id": "scraped-" + hashlib.sha256(args.url.encode()).hexdigest()[:10],
        "jurisdiction": args.jurisdiction,
        "category": args.category,
        "text": text,
        "source": args.url,
        "confidence_level": "scraped",
        "effective_date": None,
        "keywords": [k.strip() for k in args.keywords.split(",") if k.strip()],
    }

    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if any(c["id"] == chunk["id"] for c in data):
        print("该 URL 已抓取过,跳过。")
        return
    data.append(chunk)
    SEED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已追加 chunk {chunk['id']},正文 {len(text)} 字。")


if __name__ == "__main__":
    main()
