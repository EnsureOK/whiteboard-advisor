"""Broker Lead Funnel:lead 打分、匹配、定价(PRD §4.9)。mock broker 跑通软件闭环。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.models.schemas import AssetTier, Broker, Session
from app.templates import registry

from app.paths import resource_path

_BROKERS_PATH = Path(resource_path("app", "data", "brokers_mock.json"))

# 资产档定价 (USD per qualified lead)
PRICE_BY_TIER: dict[AssetTier, float] = {
    AssetTier.mass_affluent: 50.0,
    AssetTier.affluent: 150.0,
    AssetTier.hnw: 300.0,
}


@lru_cache(maxsize=1)
def brokers() -> list[Broker]:
    raw = json.loads(_BROKERS_PATH.read_text(encoding="utf-8"))
    return [Broker.model_validate(b) for b in raw]


def _estimate_investable(session: Session) -> Optional[float]:
    ia = session.zones.get("income_assets")
    if ia and ia.data:
        total = ia.data.get("total_investable")
        if isinstance(total, (int, float)):
            return float(total)
        accounts = ia.data.get("accounts") or []
        vals = [a.get("value") for a in accounts if isinstance(a.get("value"), (int, float))]
        if vals:
            return float(sum(vals))
    return None


def tier_from_amount(amount: Optional[float]) -> AssetTier:
    if amount is None:
        return AssetTier.unknown
    if amount >= 2_000_000:
        return AssetTier.hnw
    if amount >= 500_000:
        return AssetTier.affluent
    return AssetTier.mass_affluent


def completion_ratio(session: Session) -> float:
    ids = registry.template_zone_ids(session.template_id)
    if not ids:
        return 0.0
    filled = sum(1 for zid in ids if session.zones.get(zid) and session.zones[zid].data)
    return filled / len(ids)


def score_lead(session: Session) -> AssetTier:
    """综合资产档(主)与完成度(辅)给出 lead tier。"""
    tier = tier_from_amount(_estimate_investable(session))
    return tier


def match(tier: AssetTier, jurisdiction: str, language: str) -> Optional[Broker]:
    candidates = [
        b
        for b in brokers()
        if tier in b.accepted_lead_tiers
        and (jurisdiction in b.jurisdictions or not jurisdiction)
        and (language in b.languages or not language)
    ]
    if not candidates:
        return None
    # 经验更丰富者优先
    candidates.sort(key=lambda b: b.years_experience, reverse=True)
    return candidates[0]


def price_for(tier: AssetTier) -> float:
    return PRICE_BY_TIER.get(tier, 0.0)


def infer_jurisdiction(session: Session) -> str:
    text = " ".join(e.content for e in session.dialogue_history)
    fp = session.zones.get("family_profile")
    if fp and fp.data:
        text += json.dumps(fp.data, ensure_ascii=False)
    if any(k in text for k in ["香港", "HK", "粤"]):
        return "HK"
    if any(k in text for k in ["多伦多", "加拿大", "Toronto", "CA"]):
        return "CA"
    if any(k in text for k in ["美国", "US", "纽约", "加州"]):
        return "US"
    return "HK"  # 默认面向 HK 华人 HNW
