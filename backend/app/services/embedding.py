"""千帆 Embedding 服务(OpenAI 兼容 /embeddings 接口)。

- 真实模式: 千帆 v2 embeddings (默认 bge-large-zh)
- Mock 模式: 无 key 时用稳定的哈希伪向量,保证入库/检索流程可跑通与测试
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Optional

import httpx
import numpy as np

from app.config import settings

logger = logging.getLogger("whiteboard-advisor.embedding")

# 伪向量维度(mock 模式),不影响 FTS 降级路径
FAKE_DIM = 256


def has_embedding_api() -> bool:
    return bool(settings.qianfan_api_key)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。无 key 时返回确定性伪向量(便于本地演示与测试)。"""
    if not texts:
        return []
    if not has_embedding_api():
        return [_fake_embed(t) for t in texts]

    url = f"{settings.qianfan_base_url}/embeddings"
    headers = {"Authorization": f"Bearer {settings.qianfan_api_key}"}
    out: list[list[float]] = []
    # 千帆单次最多 16 条输入
    for i in range(0, len(texts), 16):
        batch = texts[i : i + 16]
        payload = {"model": settings.qianfan_embedding_model, "input": batch}
        timeout = httpx.Timeout(connect=10, read=60, write=30, pool=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        batch_vecs = [item["embedding"] for item in data["data"]]
        if len(batch_vecs) != len(batch):
            raise ValueError(f"embedding 返回数量不符: {len(batch_vecs)} != {len(batch)}")
        out.extend(batch_vecs)
    return out


def _fake_embed(text: str) -> list[float]:
    """基于多级哈希的确定性伪向量:同文本同向量,相似文本不保证相似,仅供流程演示。"""
    vec = np.zeros(FAKE_DIM, dtype=np.float32)
    tokens = [text[i : i + 2] for i in range(0, min(len(text), 400), 2)]
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % FAKE_DIM] += 1.0
        vec[(h >> 8) % FAKE_DIM] += 0.5
    norm = float(np.linalg.norm(vec)) or 1.0
    return (vec / norm).tolist()


def cosine_topk(
    query_vec: list[float],
    rows: list[tuple[str, Optional[bytes]]],
    top_k: int = 8,
) -> list[tuple[str, float]]:
    """在 (chunk_id, embedding_blob) 上做暴力余弦 top-k。本地千级 chunk 内足够快。"""
    q = np.asarray(query_vec, dtype=np.float32)
    qn = float(np.linalg.norm(q)) or 1.0
    scored: list[tuple[str, float]] = []
    for chunk_id, blob in rows:
        if not blob:
            continue
        v = np.frombuffer(blob, dtype=np.float32)
        if v.shape[0] != q.shape[0]:
            continue
        denom = float(np.linalg.norm(v)) * qn
        if denom <= 0:
            continue
        scored.append((chunk_id, float(np.dot(q, v) / denom)))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]
