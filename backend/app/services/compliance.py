"""展业合规审核:规则库(三层查重)+ 审核执行(LLM 单次批审 / 正则降级)+ 冲突消解。

规则模型与查重纯函数改造自 MIT 套件 insurance-business-operations 的
rule_db.py;审核执行链路按该套件 audit-workflow.md Step 5 与
audit-prompt.md Part 2/3 规范实现(套件本身无此部分代码)。

与套件规范的有意偏离:
- 存储 JSON 文件 -> SQLAlchemy(ComplianceRule 表)
- 推理"一条规则一次 LLM"-> 单次调用携带全部命中规则(积分成本可控)
- 引用文档库/标签注册表/飞书后端裁剪(经纪人场景不需要)
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from app.db_models import ComplianceRule

logger = logging.getLogger("whiteboard-advisor.compliance")

SIMILARITY_THRESHOLD = 0.85

# 报告尾部复核声明(规范要求不可省略)
REVIEW_DISCLAIMER = (
    "以上结论是基于规则库的机器初筛结果,不构成最终合规意见。"
    "物料对外使用前必须由合规专员复核确认。"
)

_PUNCTUATION = set("，。、；：？！“”‘’（）《》〈〉【】—…·「」『』"
                   ",.;:?!\"'()<>[]{}-_~`@#$%^&*+=|\\/")


# ---------------------------------------------------------------------------
# 纯函数(逐字搬自套件 rule_db.py,MIT)
# ---------------------------------------------------------------------------

def _normalize_rule_text(text: str) -> str:
    """归一化规则正文,用作内容指纹和相似度比较的输入。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return "".join(ch for ch in normalized if not ch.isspace() and ch not in _PUNCTUATION)


def content_hash(rule_text: str) -> str:
    """规则正文内容指纹:归一化 rule_text 的 sha256。"""
    normalized = _normalize_rule_text(rule_text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _char_bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a: str, b: str) -> float:
    """字面相似度:difflib ratio 与字符 bigram Jaccard 取较大值(输入应已归一化)。"""
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    bigrams_a, bigrams_b = _char_bigrams(a), _char_bigrams(b)
    union = bigrams_a | bigrams_b
    jaccard = len(bigrams_a & bigrams_b) / len(union) if union else 0.0
    return max(ratio, jaccard)


def risk_key_of(rule_set_id: str, risk_code: str) -> Optional[str]:
    if not rule_set_id or not risk_code:
        return None
    return f"{rule_set_id}:{risk_code}"


# ---------------------------------------------------------------------------
# 规则库
# ---------------------------------------------------------------------------

def _valid_rules(db: OrmSession) -> list[ComplianceRule]:
    return db.query(ComplianceRule).filter(ComplianceRule.valid_status == "valid").all()


def add_rules(db: OrmSession, rules: list[dict], on_conflict: str = "skip") -> dict:
    """批量录入,三层查重(语义照套件):

    1. risk_key:全库 valid 内唯一 -> 冲突按 on_conflict(skip/update)
    2. content_hash:同一 rule_set_id 内 valid 唯一 -> 同上
    3. 相似度 >= 0.85(同规则集内):仅告警,不阻塞写入

    update 策略走版本化:旧规则置 deprecated + superseded_by,新规则 version+1。
    """
    existing = _valid_rules(db)
    key_index = {r.risk_key: r for r in existing if r.risk_key}
    hash_index = {(r.rule_set_id, r.content_hash): r for r in existing if r.content_hash}

    added, skipped, updated, warnings = [], [], [], []
    batch_keys: set[str] = set()

    for raw in rules:
        rule_set_id = raw.get("rule_set_id", "")
        risk_code = str(raw.get("risk_code", ""))
        rkey = risk_key_of(rule_set_id, risk_code)
        chash = content_hash(raw.get("rule_text", ""))
        if not rkey or not raw.get("rule_text") or not raw.get("audit_point"):
            skipped.append({"riskCode": risk_code, "reason": "缺少必填字段"})
            continue
        if rkey in batch_keys:
            skipped.append({"riskKey": rkey, "reason": "批次内重复"})
            continue

        conflict = key_index.get(rkey) or hash_index.get((rule_set_id, chash))
        if conflict is not None:
            if on_conflict == "update" and conflict.content_hash != chash:
                new_row = _row_from_dict(raw, rkey, chash, version=conflict.version + 1,
                                         supersedes=conflict.id)
                conflict.valid_status = "deprecated"
                conflict.superseded_by = new_row.id
                db.add(new_row)
                key_index[rkey] = new_row
                hash_index[(rule_set_id, chash)] = new_row
                updated.append(rkey)
            else:
                skipped.append({"riskKey": rkey, "reason": "重复(risk_key 或内容指纹已存在)"})
            batch_keys.add(rkey)
            continue

        # 第三层:同规则集内字面相似度,仅告警
        norm = _normalize_rule_text(raw.get("rule_text", ""))
        for r in existing:
            if r.rule_set_id != rule_set_id:
                continue
            score = similarity(norm, _normalize_rule_text(r.rule_text))
            if score >= SIMILARITY_THRESHOLD:
                warnings.append({"riskKey": rkey, "similarTo": r.risk_key, "score": round(score, 3)})
                break

        new_row = _row_from_dict(raw, rkey, chash)
        db.add(new_row)
        existing.append(new_row)
        key_index[rkey] = new_row
        hash_index[(rule_set_id, chash)] = new_row
        batch_keys.add(rkey)
        added.append(rkey)

    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "warnings": warnings}


def _row_from_dict(raw: dict, rkey: str, chash: str, version: int = 1,
                   supersedes: Optional[str] = None) -> ComplianceRule:
    return ComplianceRule(
        rule_set_id=raw.get("rule_set_id", ""),
        rule_set_name=raw.get("rule_set_name", "")[:120],
        risk_code=str(raw.get("risk_code", ""))[:20],
        risk_key=rkey,
        rule_text=raw.get("rule_text", ""),
        audit_point=raw.get("audit_point", ""),
        content_hash=chash,
        pattern=raw.get("pattern"),
        scene_tags_json=json.dumps(raw.get("scene_tags", []), ensure_ascii=False),
        risk_level=raw.get("risk_level", "中")[:4],
        suggestion=raw.get("suggestion", ""),
        resolution_json=json.dumps(raw.get("resolution_policy", {}), ensure_ascii=False),
        version=version,
        supersedes=supersedes,
        source_doc=raw.get("source_doc", "")[:200],
    )


def search_rules(db: OrmSession, tags: Optional[list[str]] = None) -> list[ComplianceRule]:
    """valid 规则,可按场景标签过滤(任一命中即取)。"""
    rows = _valid_rules(db)
    if not tags:
        return rows
    want = set(tags)
    out = []
    for r in rows:
        try:
            rtags = set(json.loads(r.scene_tags_json or "[]"))
        except ValueError:
            rtags = set()
        if rtags & want:
            out.append(r)
    return out


def rule_out(r: ComplianceRule) -> dict:
    return {
        "id": r.id,
        "ruleSetId": r.rule_set_id,
        "riskCode": r.risk_code,
        "riskKey": r.risk_key,
        "ruleText": r.rule_text,
        "auditPoint": r.audit_point,
        "pattern": r.pattern,
        "sceneTags": json.loads(r.scene_tags_json or "[]"),
        "riskLevel": r.risk_level,
        "suggestion": r.suggestion,
        "validStatus": r.valid_status,
        "version": r.version,
        "sourceDoc": r.source_doc,
    }


# ---------------------------------------------------------------------------
# 冲突消解(按套件 audit-workflow.md Step 5 / audit-prompt.md Part 3 规范实现)
# ---------------------------------------------------------------------------

def resolve_conflicts(candidates: list[dict], policies: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """互斥/优先级消解。candidates 每项须含 riskKey/ruleSetId/eventKey。

    返回 (violations, suppressed)。被压制项保留 suppressedBy/suppressionReason 供审计。
    规范边界:不同 event_key 不互斥;same_rule_set_only 时不跨规则集;
    无 resolution_policy 的候选不参与消解;缺 riskKey 的保留并标 requiresHumanReview。
    """
    violations: list[dict] = []
    suppressed: list[dict] = []

    # 1. 按 event_key 分组
    by_event: dict[str, list[dict]] = {}
    for c in candidates:
        if not c.get("riskKey"):
            c["requiresHumanReview"] = True
            violations.append(c)
            continue
        by_event.setdefault(c.get("eventKey") or "", []).append(c)

    for _event, group in by_event.items():
        # 2. 同一 event 内按 rule_set 再分组(same_rule_set_only 默认 true,不跨集互斥)
        by_set: dict[str, list[dict]] = {}
        for c in group:
            by_set.setdefault(c.get("ruleSetId") or "", []).append(c)
        for _sid, members in by_set.items():
            # 3. 按 priority 从高到低
            def prio(c: dict) -> int:
                return int((policies.get(c["riskKey"]) or {}).get("priority", 0) or 0)

            members.sort(key=prio, reverse=True)
            dead: set[int] = set()
            for i, high in enumerate(members):
                if i in dead:
                    continue
                hp = policies.get(high["riskKey"]) or {}
                for j in range(i + 1, len(members)):
                    if j in dead:
                        continue
                    low = members[j]
                    lp = policies.get(low["riskKey"]) or {}
                    # 4/5. key 版覆盖关系(两个方向)
                    hit = (low["riskKey"] in (hp.get("suppresses_keys") or [])
                           or high["riskKey"] in (lp.get("suppressed_by_keys") or []))
                    if hit:
                        dead.add(j)
                        suppressed.append({
                            **low,
                            "suppressedBy": high["riskKey"],
                            "suppressionReason": f"同一事项同时命中 {low['riskCode']} 与 {high['riskCode']},按优先级仅输出 {high['riskCode']}",
                        })
            for i, c in enumerate(members):
                if i not in dead:
                    violations.append(c)

    return violations, suppressed


# ---------------------------------------------------------------------------
# 审核执行
# ---------------------------------------------------------------------------

_LEVEL_ORDER = {"高": 3, "中": 2, "低": 1}


def _pattern_scan(text: str, rules: list[ComplianceRule]) -> list[dict]:
    """无 LLM 降级:按规则 pattern 正则扫描,命中即候选。"""
    out = []
    for r in rules:
        if not r.pattern:
            continue
        try:
            m = re.search(r.pattern, text)
        except re.error:
            logger.warning("规则 %s 的 pattern 非法,跳过", r.risk_key)
            continue
        if m:
            snippet_start = max(0, m.start() - 12)
            out.append({
                "riskKey": r.risk_key,
                "ruleSetId": r.rule_set_id,
                "riskCode": r.risk_code,
                "ruleText": r.rule_text,
                "hitContent": text[snippet_start:m.end() + 12].strip(),
                "eventKey": m.group(0),
                "reasoning": f"命中禁用表述「{m.group(0)}」",
                "confidence": 0.95,
                "riskLevel": r.risk_level,
                "suggestion": r.suggestion,
            })
    return out


async def _llm_audit(text: str, rules: list[ComplianceRule]) -> Optional[list[dict]]:
    """LLM 单次批审(改造自套件 Part 2 单条模板)。失败返回 None 由调用方降级。"""
    from app.config import settings

    if not settings.has_llm:
        return None
    lines = []
    for i, r in enumerate(rules, 1):
        lines.append(
            f"[{i}] riskKey={r.risk_key} 风险等级={r.risk_level}\n"
            f"    规则原文: {r.rule_text[:200]}\n"
            f"    检查说明: {r.audit_point[:200]}"
        )
    prompt = (
        "你是保险展业合规审核专家,负责判断物料内容是否违反审核要点。\n\n"
        f"## 审核要点(共 {len(rules)} 条)\n" + "\n".join(lines) + "\n\n"
        f"## 待审核物料\n{text[:3000]}\n\n"
        "## 推理要求\n"
        "- 逐条判断该要点是否适用于本物料、物料是否存在违反该要点的内容\n"
        "- 只报告确实违规的;hitContent 摘录物料原文片段,不得改写\n"
        "- eventKey 用一短语概括涉事表述/活动(同一事项的多条命中用相同 eventKey)\n"
        "- confidence 为 0~1 数值;吃不准时调低并如实说明\n\n"
        '只输出 JSON: {"violations": [{"riskKey": "...", "hitContent": "...", '
        '"eventKey": "...", "reasoning": "...", "confidence": 0.9, "riskLevel": "高"}]}'
    )
    try:
        from app.services.llm import _call_qianfan

        raw, _usage = await _call_qianfan([{"role": "user", "content": prompt}], settings.model_fast)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        items = data.get("violations")
        if not isinstance(items, list):
            return None
        by_key = {r.risk_key: r for r in rules}
        out = []
        for v in items:
            r = by_key.get(str(v.get("riskKey", "")))
            if not r:
                continue  # 幻觉 riskKey 丢弃
            conf = float(v.get("confidence", 0.5) or 0.5)
            out.append({
                "riskKey": r.risk_key,
                "ruleSetId": r.rule_set_id,
                "riskCode": r.risk_code,
                "ruleText": r.rule_text,
                "hitContent": str(v.get("hitContent", ""))[:200],
                "eventKey": str(v.get("eventKey", ""))[:80],
                "reasoning": str(v.get("reasoning", ""))[:300],
                "confidence": max(0.0, min(1.0, conf)),
                "riskLevel": r.risk_level,
                "suggestion": r.suggestion,
                **({"requiresHumanReview": True} if conf < 0.6 else {}),
            })
        return out
    except Exception as e:  # noqa: BLE001 LLM 审核失败降级 pattern
        logger.warning("llm audit failed, fallback to pattern: %s", e)
        return None


async def audit_text(db: OrmSession, text: str, tags: Optional[list[str]] = None,
                     mode: str = "auto") -> dict:
    """审核一段物料文本。mode: auto(有 LLM 用 LLM)/pattern(强制本地正则)。"""
    rules = search_rules(db, tags)
    checked_at = datetime.now(timezone.utc).isoformat()
    if not rules:
        return {"overallRisk": "无", "violations": [], "suppressed": [],
                "rulesChecked": 0, "mode": "none", "checkedAt": checked_at,
                "disclaimer": REVIEW_DISCLAIMER}

    used_mode = "pattern"
    candidates = None
    if mode == "auto":
        candidates = await _llm_audit(text, rules)
        if candidates is not None:
            used_mode = "llm"
    if candidates is None:
        candidates = _pattern_scan(text, rules)

    policies = {}
    for r in rules:
        try:
            p = json.loads(r.resolution_json or "{}")
        except ValueError:
            p = {}
        if p:
            policies[r.risk_key] = p

    violations, suppressed = resolve_conflicts(candidates, policies)
    violations.sort(key=lambda v: _LEVEL_ORDER.get(v.get("riskLevel", "低"), 0), reverse=True)
    overall = violations[0]["riskLevel"] if violations else "无"
    return {
        "overallRisk": overall,
        "violations": violations,
        "suppressed": suppressed,
        "rulesChecked": len(rules),
        "mode": used_mode,
        "checkedAt": checked_at,
        "disclaimer": REVIEW_DISCLAIMER,
    }
