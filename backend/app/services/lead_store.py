"""Lead 存储 + handoff + 反作弊。文件持久化(与 session 一致的轻量方案)。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.models.schemas import ContactInfo, Lead, LeadStatus, Session
from app.services import broker, session_store

logger = logging.getLogger("whiteboard-advisor.lead")

from app.paths import DATA_DIR as _WB_DATA_DIR

_BASE = Path(_WB_DATA_DIR)
_LEAD_DIR = _BASE / "leads"
_LEAD_DIR.mkdir(parents=True, exist_ok=True)
_CANCEL_PATH = _BASE / "cancel_counts.json"

CANCEL_RISK_THRESHOLD = 2
SLA_HOURS = 48

_leads: dict[str, Lead] = {}


def _path(lead_id: str) -> Path:
    return _LEAD_DIR / f"{lead_id}.json"


def _save(lead: Lead) -> None:
    _leads[lead.id] = lead
    _path(lead.id).write_text(lead.model_dump_json(indent=2), encoding="utf-8")


def _load_all() -> list[Lead]:
    out = list(_leads.values())
    seen = {l.id for l in out}
    for f in _LEAD_DIR.glob("*.json"):
        if f.stem not in seen:
            try:
                lead = Lead.model_validate_json(f.read_text(encoding="utf-8"))
                _leads[lead.id] = lead
                out.append(lead)
            except Exception:  # noqa: BLE001
                logger.exception("lead 读取失败: %s", f)
    return out


def _cancel_counts() -> dict[str, int]:
    if _CANCEL_PATH.exists():
        try:
            return json.loads(_CANCEL_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _contact_keys(contact: ContactInfo) -> list[str]:
    return [k for k in (contact.email.strip().lower(), contact.phone.strip()) if k]


def is_risky(contact: ContactInfo) -> bool:
    counts = _cancel_counts()
    return any(counts.get(k, 0) >= CANCEL_RISK_THRESHOLD for k in _contact_keys(contact))


def record_cancel(contact: ContactInfo) -> None:
    counts = _cancel_counts()
    for k in _contact_keys(contact):
        counts[k] = counts.get(k, 0) + 1
    _CANCEL_PATH.write_text(json.dumps(counts), encoding="utf-8")


def capture(session: Session, contact: ContactInfo) -> tuple[Lead, Optional[object]]:
    """创建 lead、打分、匹配 broker。返回 (lead, broker|None)。"""
    tier = broker.score_lead(session)
    lead = Lead(session_id=session.id, tier=tier, contact=contact)

    if is_risky(contact):
        lead.risky = True
        lead.status = LeadStatus.pending
        _save(lead)
        return lead, None

    jurisdiction = broker.infer_jurisdiction(session)
    matched = broker.match(tier, jurisdiction, session.language.value)
    if matched:
        lead.matched_broker_id = matched.id
        lead.status = LeadStatus.matched
        lead.price_charged = broker.price_for(tier)
    _save(lead)
    return lead, matched


def list_leads() -> list[Lead]:
    return sorted(_load_all(), key=lambda l: l.created_at, reverse=True)


def get(lead_id: str) -> Optional[Lead]:
    leads = {l.id: l for l in _load_all()}
    return leads.get(lead_id)


def claim(lead_id: str) -> Optional[Lead]:
    lead = get(lead_id)
    if not lead:
        return None
    lead.status = LeadStatus.contacted
    lead.claimed_at = datetime.now(timezone.utc)
    lead.sla_due_at = lead.claimed_at + timedelta(hours=SLA_HOURS)
    _save(lead)
    return lead


def session_zone_data(session_id: str) -> dict:
    s = session_store.get(session_id)
    if not s:
        return {}
    return {zid: z.data for zid, z in s.zones.items() if z.data}
