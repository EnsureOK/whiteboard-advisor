"""RAG 检索服务:从种子知识库按 (jurisdiction × query) 召回 chunk,注入 LLM prompt。

V0.1 用关键词重叠打分(无外部依赖、确定性、可测)。后续可替换为千帆 embedding + 向量检索。
来源可追溯:每条 chunk 带 source / confidence_level / effective_date(法务保护)。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.models.schemas import KnowledgeChunk

logger = logging.getLogger("whiteboard-advisor.rag")

from app.paths import resource_path

_SEED_PATH = Path(resource_path("app", "data", "knowledge_seed.json"))


@lru_cache(maxsize=1)
def _chunks() -> list[KnowledgeChunk]:
    try:
        raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("知识种子库未找到: %s", _SEED_PATH)
        return []
    return [KnowledgeChunk.model_validate(c) for c in raw]


def _score(chunk: KnowledgeChunk, query: str) -> int:
    q = query.lower()
    score = 0
    for kw in chunk.keywords:
        if kw.lower() in q:
            score += 2
    # 文本中的词也参与(粗粒度)
    for token in set(q.replace(",", " ").replace(",", " ").split()):
        if len(token) >= 2 and token in chunk.text.lower():
            score += 1
    return score


def retrieve(query: str, jurisdiction: str = "global", k: int = 3) -> list[KnowledgeChunk]:
    allowed = {jurisdiction, "global"}
    pool = [c for c in _chunks() if c.jurisdiction in allowed]
    scored = [(c, _score(c, query)) for c in pool]
    scored = [(c, s) for c, s in scored if s > 0]
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return [c for c, _ in scored[:k]]


def context_block(query: str, jurisdiction: str = "global", k: int = 3) -> str:
    """格式化为可注入 prompt 的参考资料块(含来源)。无命中返回空串。"""
    hits = retrieve(query, jurisdiction, k)
    if not hits:
        return ""
    lines = ["参考资料(用于提升准确性,引用时请提及来源,信息可能过时需以持牌人士确认为准):"]
    for c in hits:
        lines.append(f"- [{c.jurisdiction}/{c.category}] {c.text} (来源: {c.source})")
    return "\n".join(lines)
