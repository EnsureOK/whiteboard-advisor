"""千帆 LLM 编排层。

- 真实模式:调用千帆 v2 (OpenAI 兼容, Bearer 鉴权),要求 JSON 输出
- Mock 模式:无 key 时用规则生成可用的 TurnPlan,保证全流程可跑通
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Iterator, Optional

import httpx

from app.config import settings
from app.models.schemas import ActionType, IntentType, Language, Session, TurnPlan
from app.services import cost, rag
from app.templates import registry

logger = logging.getLogger("whiteboard-advisor.llm")

# ---- 计量收集器(积分扣减用) ----
# 入口用 `with collect_usage() as sink:` 包住业务调用,期间本协程及其派生子协程
# (asyncio 子任务继承 context,DAG 并行 gather 亦然)的千帆 usage 都落入同一 sink;
# 结束后 sum(sink) 即本次操作的 token 总量。未包裹时静默不记,零侵入。
_usage_sink: ContextVar[Optional[list]] = ContextVar("wb_usage_sink", default=None)


@contextmanager
def collect_usage() -> Iterator[list]:
    sink: list[int] = []
    token = _usage_sink.set(sink)
    try:
        yield sink
    finally:
        _usage_sink.reset(token)


def record_usage(usage: dict) -> None:
    """把一次 LLM/embedding 调用的 usage 记入当前收集器(若有)。"""
    sink = _usage_sink.get()
    if sink is None or not usage:
        return
    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        total = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    if total > 0:
        sink.append(total)


class TaskKind(str, Enum):
    turn = "turn"          # 交互轮:意图+zone+解说,用快模型
    deep_plan = "deep_plan"  # 复杂多 zone 规划,用 deep 模型


def _model_for(kind: TaskKind) -> str:
    return settings.model_deep if kind == TaskKind.deep_plan else settings.model_fast


# 简单内存 turn 缓存:相同 (模板 + zone 状态 + 用户话) 24h 内复用,省成本/延迟
_CACHE_TTL = 24 * 3600
_turn_cache: dict[str, tuple[float, str]] = {}


def _cache_key(session: Session, utterance: str) -> str:
    zones_state = {zid: z.data for zid, z in session.zones.items() if z.data}
    raw = json.dumps(
        {"t": session.template_id, "z": zones_state, "u": utterance},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    hit = _turn_cache.get(key)
    if not hit:
        return None
    ts, content = hit
    if time.time() - ts > _CACHE_TTL:
        _turn_cache.pop(key, None)
        return None
    return content


def _cache_put(key: str, content: str) -> None:
    if len(_turn_cache) > 500:
        _turn_cache.clear()
    _turn_cache[key] = (time.time(), content)

# 流式输出的分隔符:narration 文本在前,之后是结构化 JSON
PLAN_SENTINEL = "===PLAN==="

_PROMPT_HEADER = """你是一位资深的保险与财富规划顾问,正在通过"实时画白板"的方式帮客户做开局规划演示。

你的工作:根据客户的口语输入,在 family-protection 模板的若干 zone 上填充或修改**结构化数据**(不是 HTML)。

可用 zone 及其数据 JSON Schema:
{zone_schemas}
"""

_NARRATION_REQ = """★ narration 的核心要求(这是产品最关键的体验):
narration 不是泛泛的回应,而是**向客户解说你刚刚在白板上做了哪些改动、以及为什么这么分析**。
- update_zone:说清你在「哪个模块」新画/填了「什么内容」。例:"我在保障缺口这块画上了:你目前寿险是空白,按你 300 万资产和三个孩子,建议额度大概 300 万美金,所以这里有一个明显缺口。"
- modify_zone:对比改动前后,说清你把「什么」从「旧值」改成了「新值」,以及原因。例:"我把寿险保额从 300 万上调到了 500 万,因为你刚提到还要覆盖两个孩子的海外学费。"
- explain:不改白板时,指向对应模块解释客户的疑问。
让客户听着 narration,就能明白屏幕上刚刚长出来/变化的那部分是什么、为什么。"""

_RULES = """规则:
- 客户提供新信息时,把它整理进最相关的 zone,zone_data 给出该 zone 合并后的完整 data;narration 必须解说这次改动。
- 你能在上下文里看到该 zone 改动前的 data(zones_so_far),做 modify 时请据此对比出"改了什么"再说出来。
- 客户没说够时,礼貌追问(next_question),不要瞎编客户的资产数字。
- 始终用"一般性思路 (general guidance)"措辞,不给具体股票/基金代码,不预测市场涨跌。
- 涉及具体产品配置时,在 coverage_plan 的 disclaimer 里写明"具体产品请咨询持牌经纪人"。
- 语言:用 {language} 与客户交流(narration / next_question / 文案均用该语言)。"""

# 非流式:单个严格 JSON(含 narration 字段)
SYSTEM_PROMPT = (
    _PROMPT_HEADER
    + """
每一轮你必须输出**严格的 JSON**,字段如下:
{{
  "intent": 六选一: "provide_info" | "modify_request" | "clarification_question" | "topic_switch" | "out_of_scope" | "terminate",
  "action": "update_zone" | "modify_zone" | "explain" | "switch_focus" | "ask_next" | "finalize",
  "target_zone": zone 的 id,或 null,
  "zone_data": 当 action 为 update_zone/modify_zone 时,给出 target_zone 的**完整新 data**(必须匹配该 zone 的 schema);否则为 null,
  "narration": 你要用语音对客户说的话(口语、自然、简短,2-4 句),
  "next_question": 你想主动追问客户的下一个问题,或 null
}}

"""
    + _NARRATION_REQ
    + "\n\n"
    + _RULES
    + "\n- 只输出 JSON,不要任何额外文字或 markdown 代码块。"
)

# 流式:先输出 narration 纯文本,再输出分隔符,再输出 JSON(不含 narration)
STREAM_SYSTEM_PROMPT = (
    _PROMPT_HEADER
    + """
输出格式(严格遵守,便于实时呈现):
1) 先直接输出 narration 纯文本(口语、自然、简短,2-4 句),不要加引号或字段名。
2) 另起一行输出分隔符:""" + PLAN_SENTINEL + """
3) 分隔符之后输出结构化 JSON(不含 narration 字段):
{{
  "intent": "provide_info" | "modify_request" | "clarification_question" | "topic_switch" | "out_of_scope" | "terminate",
  "action": "update_zone" | "modify_zone" | "explain" | "switch_focus" | "ask_next" | "finalize",
  "target_zone": zone 的 id 或 null,
  "zone_data": update_zone/modify_zone 时给出 target_zone 的完整新 data(匹配 schema),否则 null,
  "next_question": 主动追问或 null
}}

"""
    + _NARRATION_REQ
    + "\n\n"
    + _RULES
    + "\n- 严格按上述三段格式输出:narration 文本、分隔符、JSON。JSON 之后不要再有其它内容。"
)


def _infer_jurisdiction(session: Session, utterance: str) -> str:
    text = utterance + " ".join(e.content for e in session.dialogue_history[-6:])
    if any(k in text for k in ["香港", "HK", "港", "粤"]):
        return "HK"
    if any(k in text for k in ["美国", "US", "美籍", "纽约", "加州"]):
        return "US"
    return "global"


def _zone_schemas_text(template_id: str) -> str:
    parts = []
    for z in registry.template_zone_defs(template_id):
        parts.append(f"- {z['id']} ({z['title']['zh']}):\n{json.dumps(z['schema'], ensure_ascii=False)}")
    return "\n".join(parts)


def _build_messages(
    session: Session, utterance: str, repair_hint: Optional[str] = None, stream: bool = False
) -> list[dict]:
    lang = "中文" if session.language == Language.zh else "English"
    prompt = STREAM_SYSTEM_PROMPT if stream else SYSTEM_PROMPT
    system = prompt.format(zone_schemas=_zone_schemas_text(session.template_id), language=lang)

    zones_state = {zid: z.data for zid, z in session.zones.items() if z.data}
    context = {
        "current_zone_focus": session.current_zone_focus,
        "zones_so_far": zones_state,
        "recent_dialogue": [
            {"role": e.role, "content": e.content} for e in session.dialogue_history[-8:]
        ],
    }
    jurisdiction = _infer_jurisdiction(session, utterance)
    kb = rag.context_block(utterance, jurisdiction)
    kb_section = f"\n\n{kb}\n" if kb else ""

    tail = "\n请先输出 narration,再输出分隔符与 JSON。" if stream else "\n请输出本轮的 JSON。"
    user_content = (
        f"当前白板状态:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"客户最新说的话:「{utterance}」{kb_section}{tail}"
    )
    if repair_hint:
        user_content += f"\n\n注意:上一次输出的 zone_data 未通过 schema 校验,错误:{repair_hint}。请修正后重新输出完整 JSON。"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


async def _call_qianfan(messages: list[dict], model: str) -> tuple[str, dict]:
    url = f"{settings.qianfan_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.qianfan_api_key}"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    # deepseek 等推理型模型较慢,且上下文增长后更慢,给足读超时
    timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage", {})
    record_usage(usage)
    return data["choices"][0]["message"]["content"], usage


def _parse_turn(raw: str) -> TurnPlan:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    obj = json.loads(text)
    return TurnPlan.model_validate(obj)


async def generate_turn(
    session: Session,
    utterance: str,
    repair_hint: Optional[str] = None,
    kind: TaskKind = TaskKind.turn,
) -> TurnPlan:
    if not settings.has_llm:
        return _mock_turn(session, utterance)
    cost.check_budget(session.llm_cost)

    cache_key = _cache_key(session, utterance) if repair_hint is None else None
    if cache_key:
        cached = _cache_get(cache_key)
        if cached:
            return _parse_turn(cached)

    messages = _build_messages(session, utterance, repair_hint)
    model = _model_for(kind)
    try:
        raw, usage = await _call_qianfan(messages, model)
    except httpx.HTTPStatusError as e:
        # 快模型不可用(如未开通)时回退到 deep 模型
        if kind == TaskKind.turn and model != settings.model_deep:
            logger.warning("快模型 %s 调用失败(%s),回退 deep 模型", model, e.response.status_code)
            model = settings.model_deep
            raw, usage = await _call_qianfan(messages, model)
        else:
            raise
    session.llm_cost += cost.estimate(
        model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    )
    if cache_key:
        _cache_put(cache_key, raw)
    return _parse_turn(raw)


# ---- 流式 ----

_stream_cache: dict[str, tuple[float, str]] = {}


def _parse_combined(full_text: str, fallback_narration: str = "") -> TurnPlan:
    """解析 '<narration>===PLAN===<json>' 形式的流式输出。"""
    if PLAN_SENTINEL in full_text:
        narration_part, json_part = full_text.split(PLAN_SENTINEL, 1)
    else:
        # 没给分隔符:尽量从文本里抠出 JSON
        narration_part, json_part = full_text, full_text
    narration = narration_part.strip() or fallback_narration
    text = json_part.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        # 没有可解析 JSON:退化为纯解释
        return TurnPlan(
            intent=IntentType.clarification_question,
            action=ActionType.explain,
            target_zone=None,
            narration=narration,
        )
    obj = json.loads(text[start : end + 1])
    obj.setdefault("narration", narration)
    obj["narration"] = narration
    return TurnPlan.model_validate(obj)


async def _call_qianfan_stream(messages: list[dict], model: str):
    """异步生成器:yield ('delta', text) / ('usage', dict)。"""
    url = f"{settings.qianfan_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.qianfan_api_key}"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if choices:
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield ("delta", delta)
                if obj.get("usage"):
                    record_usage(obj["usage"])
                    yield ("usage", obj["usage"])


async def generate_turn_stream(session: Session, utterance: str, kind: TaskKind = TaskKind.turn):
    """异步生成器:先 yield ('narration', delta) 多次,最后 yield ('plan', TurnPlan)。"""
    if not settings.has_llm:
        plan = _mock_turn(session, utterance)
        # mock:把 narration 分两段"流式"出去
        mid = max(1, len(plan.narration) // 2)
        yield ("narration", plan.narration[:mid])
        yield ("narration", plan.narration[mid:])
        yield ("plan", plan)
        return

    cost.check_budget(session.llm_cost)
    cache_key = _cache_key(session, utterance)
    cached = _cache_get_stream(cache_key)
    if cached:
        narration = cached.split(PLAN_SENTINEL, 1)[0].strip()
        yield ("narration", narration)
        yield ("plan", _parse_combined(cached))
        return

    messages = _build_messages(session, utterance, stream=True)
    model = _model_for(kind)

    # 增量切分 narration / json,边收边吐 narration
    buffer = ""
    emitted = 0
    sentinel_found = False
    usage: dict = {}
    sn = len(PLAN_SENTINEL)

    async for typ, payload in _call_qianfan_stream(messages, model):
        if typ == "usage":
            usage = payload
            continue
        buffer += payload
        if not sentinel_found:
            idx = buffer.find(PLAN_SENTINEL)
            if idx != -1:
                if idx > emitted:
                    yield ("narration", buffer[emitted:idx])
                sentinel_found = True
            else:
                safe = max(0, len(buffer) - (sn - 1))
                if safe > emitted:
                    yield ("narration", buffer[emitted:safe])
                    emitted = safe

    session.llm_cost += cost.estimate(
        model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    )
    _cache_put_stream(cache_key, buffer)
    yield ("plan", _parse_combined(buffer))


def _cache_get_stream(key: str) -> Optional[str]:
    hit = _stream_cache.get(key)
    if not hit:
        return None
    ts, content = hit
    if time.time() - ts > _CACHE_TTL:
        _stream_cache.pop(key, None)
        return None
    return content


def _cache_put_stream(key: str, content: str) -> None:
    if len(_stream_cache) > 500:
        _stream_cache.clear()
    _stream_cache[key] = (time.time(), content)


# ---- Mock 模式:无 key 时跑通流程 ----

def _mock_turn(session: Session, utterance: str) -> TurnPlan:
    zone_ids = registry.template_zone_ids(session.template_id)
    filled = [z for z in zone_ids if session.zones.get(z) and session.zones[z].data]
    next_zone = next((z for z in zone_ids if z not in filled), None)

    if next_zone == "family_profile" or (next_zone is None and not filled):
        return TurnPlan(
            intent=IntentType.provide_info,
            action=ActionType.update_zone,
            target_zone="family_profile",
            zone_data={
                "members": [
                    {"role": "本人", "age": 52, "location": "香港"},
                    {"role": "子女", "location": "多伦多"},
                    {"role": "子女", "location": "伦敦"},
                    {"role": "子女", "location": "上海"},
                ],
                "summary": "(mock) 三孩跨三地,需考虑跨境保障与传承。",
            },
            narration="(mock 模式) 我先帮你把家庭结构画出来:你和三个分别在多伦多、伦敦、上海的孩子。",
            next_question="你目前手头大概有多少可投资资产?",
        )
    if next_zone == "protection_gap":
        return TurnPlan(
            intent=IntentType.provide_info,
            action=ActionType.update_zone,
            target_zone="protection_gap",
            zone_data={
                "items": [
                    {"category": "寿险", "current": 0, "recommended": 3000000, "gap": 3000000, "unit": "USD"},
                    {"category": "重疾", "current": 0, "recommended": 1000000, "gap": 1000000, "unit": "USD"},
                ],
                "summary": "(mock) 寿险与重疾保障基本为空白,是首要缺口。",
            },
            narration="(mock 模式) 看保障缺口,你目前寿险和重疾几乎是空白,这是最该补的两块。",
            next_question="想优先解决哪一块?",
        )
    if next_zone == "coverage_plan":
        return TurnPlan(
            intent=IntentType.provide_info,
            action=ActionType.update_zone,
            target_zone="coverage_plan",
            zone_data={
                "products": [
                    {"type": "定期寿险", "coverage": 3000000, "term": "20年", "rationale": "覆盖跨境家庭的收入替代缺口。", "unit": "USD"},
                    {"type": "重疾险", "coverage": 1000000, "term": "终身", "rationale": "应对重疾医疗与收入中断。", "unit": "USD"},
                ],
                "disclaimer": "这是一般性思路,具体产品请咨询持牌经纪人。",
            },
            narration="(mock 模式) 配置上,我建议一份定期寿险打底,加一份终身重疾。这只是方向,落地要找持牌经纪人。",
            next_question="要不要让一位专业经纪人帮你深入做一版?",
        )
    if next_zone is not None:
        # 非 family 模板的 zone:mock 下仅追问,不杜撰数据
        title = registry.zl.ALL_ZONE_BY_ID.get(next_zone, {}).get("title", {}).get("zh", next_zone)
        return TurnPlan(
            intent=IntentType.clarification_question,
            action=ActionType.ask_next,
            target_zone=next_zone,
            narration=f"(mock 模式) 接下来看「{title}」,需要你补充一些信息。",
            next_question=f"关于{title},能跟我说说你的情况吗?",
        )
    return TurnPlan(
        intent=IntentType.terminate,
        action=ActionType.finalize,
        target_zone=None,
        narration="(mock 模式) 我们已经过了一遍各个模块。",
        next_question=None,
    )
