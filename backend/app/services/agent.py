"""对话 agent:OpenAI Agents SDK + 千帆(OpenAI 兼容协议)。

LLM 拿着工具清单自主决定调用顺序;每次工具调用通过 SSE 实时推到前端,
对话区能看到 agent 正在做什么(工具过程可视化)。

- 人格基底: backend/soul.md(热加载,改文件即生效)
- 工具: 检索知识库 / 客户档案 / 托管保单 / 保障缺口(均为只读)
- 降级: agent loop 失败时由调用方回退到纯 RAG 流式
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from sqlalchemy.orm import Session as OrmSession

from app.config import settings
from app.db_models import ENGAGEMENT_KINDS, POLICY_STATUSES, Client

# 模块级条件导入:function_tool 通过 get_type_hints 在模块全局解析
# "RunContextWrapper[AgentDeps]" 字符串注解(本文件启用了 future annotations)。
try:
    from agents.run_context import RunContextWrapper
except ImportError:  # SDK 未安装时 agent_available() 返回 False,不会走到工具定义
    RunContextWrapper = None  # type: ignore[assignment]

logger = logging.getLogger("whiteboard-advisor.agent")

_SOUL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "soul.md"
)
_soul_cache: tuple[float, str] = (0.0, "")

_TYPE_LABEL = {"personal": "个人客户", "family": "家庭客户", "company": "企业客户"}

# 工具名 -> 前端展示的中文标签
TOOL_LABELS = {
    "search_knowledge": "检索知识库",
    "get_client_profile": "调取客户档案",
    "list_policies": "调取托管保单",
    "calc_coverage_gaps": "计算保障缺口",
    "generate_document": "生成文档工件",
}


def load_soul() -> str:
    """读取 soul.md,按 mtime 缓存:改文件即生效,无需重启。"""
    global _soul_cache
    try:
        mtime = os.path.getmtime(_SOUL_PATH)
        if mtime != _soul_cache[0]:
            with open(_SOUL_PATH, encoding="utf-8") as f:
                _soul_cache = (mtime, f.read())
    except OSError:
        return "你是一位资深保险经纪人身边的智能助理,基于事实与工具回答,不编造数据。"
    return _soul_cache[1]


@dataclass
class AgentDeps:
    """工具运行上下文:db 会话 + 当前客户 + 过程记录。"""

    db: OrmSession
    client: Client
    citations: list[dict] = field(default_factory=list)
    # 工具过程记录(持久化+SSE 摘要): {"name","label","summary"}
    tool_events: list[dict] = field(default_factory=list)


def _client_brief(c: Client) -> str:
    members = ";".join(f"{m.name}({m.relation})" for m in c.members) or "未录入"
    engagements = (
        ";".join(
            f"{ENGAGEMENT_KINDS.get(e.kind, e.kind)}:{e.title}"
            for e in c.engagements
            if e.status == "open"
        )
        or "无"
    )
    return (
        f"当前客户: {c.name}({_TYPE_LABEL.get(c.client_type, c.client_type)}) | "
        f"成员: {members} | 进行中事项: {engagements} | 备注: {c.notes or '无'}"
    )


def _build_instructions(client: Client) -> str:
    return (
        load_soul()
        + "\n\n## 当前上下文\n"
        + _client_brief(client)
        + "\n\n## 工具使用\n"
        "- 回答涉及保单、保障、客户情况时,先调用相应工具获取事实。\n"
        "- 需要条款、核保规则、理赔材料等资料时,必须真正调用 search_knowledge,拿到结果再回答。\n"
        "- 绝不在回答文本中写出工具名、JSON 调用格式或模拟的调用结果;要用工具就直接调用。\n"
        "- 引用知识库检索结果时在句末标注 [n](与检索返回的编号一致)。\n"
        "- 工具没有返回的信息不要编造;查不到就说明查不到。"
    )


_FAKE_CALL_RE = re.compile(
    r"^\s*(\[\d+\][::]?\s*)?\{\s*\"name\"\s*:\s*\"[\w-]+\".*\}\s*$", re.MULTILINE
)


def strip_fake_tool_calls(text: str) -> str:
    """剔除模型偶尔写进正文的伪工具调用 JSON 行(ernie tool call 不稳的兜底)。"""
    cleaned = _FAKE_CALL_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# ---------- Agents SDK 装配(延迟导入,未装 SDK 时由调用方降级) ----------

def build_agent():
    """构建 Agent 实例(工具通过 RunContextWrapper 拿 AgentDeps)。"""
    from agents import Agent, function_tool, set_tracing_disabled
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    set_tracing_disabled(True)

    client = AsyncOpenAI(base_url=settings.qianfan_base_url, api_key=settings.qianfan_api_key)
    model = OpenAIChatCompletionsModel(model=settings.model_fast, openai_client=client)

    @function_tool
    async def search_knowledge(ctx: RunContextWrapper[AgentDeps], query: str) -> str:
        """在保险知识库中检索条款、核保规则、产品资料(含当前客户私有资料)。

        Args:
            query: 要检索的问题或关键词。
        """
        from app.services import kb

        deps = ctx.context
        hits = await kb.search_async(deps.db, query, client_id=deps.client.id, top_k=4)
        start = len(deps.citations)
        deps.citations.extend(hits)
        deps.tool_events.append(
            {
                "name": "search_knowledge",
                "label": TOOL_LABELS["search_knowledge"],
                "summary": (
                    f"“{query[:24]}” 命中 {len(hits)} 条"
                    + (f" · 最高 {hits[0]['score']:.2f}" if hits else "")
                ),
            }
        )
        if not hits:
            return f"知识库中没有检索到与“{query}”相关的资料。"
        lines = []
        for i, h in enumerate(hits, start + 1):
            lines.append(f"[{i}]《{h['docTitle']}》: {h['text'][:280]}")
        return "\n".join(lines)

    @function_tool
    def get_client_profile(ctx: RunContextWrapper[AgentDeps]) -> str:
        """查看当前客户的完整档案:成员/联系人、进行中业务事项、备注。"""
        deps = ctx.context
        c = deps.client
        deps.tool_events.append(
            {
                "name": "get_client_profile",
                "label": TOOL_LABELS["get_client_profile"],
                "summary": f"{len(c.members)} 位成员 · {sum(1 for e in c.engagements if e.status == 'open')} 项进行中",
            }
        )
        lines = [f"客户: {c.name} ({_TYPE_LABEL.get(c.client_type, c.client_type)})"]
        for m in c.members:
            lines.append(
                f"- {m.name} | {m.relation} | 生日: {m.birthday or '未知'} | 状态: {m.badge or '无'} | {m.notes or ''}"
            )
        open_es = [e for e in c.engagements if e.status == "open"]
        if open_es:
            lines.append("进行中事项:")
            for e in open_es:
                lines.append(f"- {ENGAGEMENT_KINDS.get(e.kind, e.kind)}: {e.title} ({e.line or '不限险种'}) {e.note or ''}")
        lines.append(f"备注: {c.notes or '无'}")
        return "\n".join(lines)

    @function_tool
    def list_policies(ctx: RunContextWrapper[AgentDeps]) -> str:
        """查看当前客户的托管保单明细(险种/产品/保额/保费/到期日/状态)。"""
        deps = ctx.context
        c = deps.client
        deps.tool_events.append(
            {
                "name": "list_policies",
                "label": TOOL_LABELS["list_policies"],
                "summary": f"{len(c.policies)} 份托管保单",
            }
        )
        if not c.policies:
            return "该客户暂无托管保单。"
        member_by_id = {m.id: m.name for m in c.members}
        lines = []
        for p in c.policies:
            who = member_by_id.get(p.member_id or "", "整体")
            lines.append(
                f"- {p.line}《{p.product_name or '未名产品'}》 被保人:{who} 保额:{p.amount // 10000}万 "
                f"年缴:{p.premium}元 承保:{p.insurer or '未知'} 到期:{p.expiry_date or '长期'} "
                f"状态:{POLICY_STATUSES.get(p.status, p.status)}"
            )
        return "\n".join(lines)

    @function_tool
    def calc_coverage_gaps(ctx: RunContextWrapper[AgentDeps]) -> str:
        """基于托管保单与建议基线,逐成员计算各保障维度的缺口。"""
        from app.services import task_engine

        deps = ctx.context
        gap = task_engine._run_gap_calc(deps.db, None, deps.client)  # noqa: SLF001 复用任务引擎的计算
        deps.tool_events.append(
            {
                "name": "calc_coverage_gaps",
                "label": TOOL_LABELS["calc_coverage_gaps"],
                "summary": f"{len(gap['rows'])} 行 × {len(gap['cols'])} 维度",
            }
        )
        lines = []
        for row in gap["rows"]:
            cells = "; ".join(f"{col}:{cell['text']}" for col, cell in row["cells"].items())
            lines.append(f"- {row['member']}: {cells}")
        if gap["extras"]:
            lines.append("未计入矩阵: " + "; ".join(f"{e['line']}{e['amount'] // 10000}万" for e in gap["extras"]))
        if gap["policyCount"] == 0:
            lines.append("(该客户暂无托管保单,以上按零保障与建议基线计算)")
        return "\n".join(lines)

    @function_tool
    async def generate_document(ctx: RunContextWrapper[AgentDeps], kind: str) -> str:
        """为当前客户生成一份文档工件,产出后经纪人可在右侧工作区下载 Word/PPT。

        Args:
            kind: 文档类型,plan=保障方案书 / visit=面谈提纲 / followup=跟进消息。
        """
        from app.services import task_engine

        deps = ctx.context
        kind_map = {"plan": "generate_plan", "visit": "prepare_visit", "followup": "followup"}
        task_kind = kind_map.get(kind, "followup")
        doc_type, title, content = await task_engine.compose_doc(deps.db, deps.client, task_kind)
        artifact = task_engine._save_artifact(  # noqa: SLF001 复用任务引擎的版本管理
            deps.db, deps.client, None, doc_type, title, content
        )
        deps.tool_events.append(
            {
                "name": "generate_document",
                "label": TOOL_LABELS["generate_document"],
                "summary": f"《{artifact.title}》 v{artifact.version}",
            }
        )
        return (
            f"已生成工件《{artifact.title}》第 {artifact.version} 版,包含 {len(content['sections'])} 个小节,"
            "经纪人可在右侧工作区查看并下载 Word/PPT。"
        )

    from agents import Agent as _Agent

    return _Agent(
        name="broker-assistant",
        instructions=lambda ctx, agent: _build_instructions(ctx.context.client),
        model=model,
        tools=[search_knowledge, get_client_profile, list_policies, calc_coverage_gaps, generate_document],
    )


async def run_agent_stream(
    db: OrmSession,
    client: Client,
    history: list[dict],
    message: str,
) -> AsyncIterator[dict]:
    """跑 agent loop,产出 SSE 友好的事件字典流。

    事件: {"type":"tool_start"|"tool_end"|"delta"|"final"} ;
    final 携带 citations 与 tool_events(供持久化)。
    抛出的异常由调用方捕获并降级。
    """
    from agents import Runner

    deps = AgentDeps(db=db, client=client)
    agent = build_agent()

    input_items: list[Any] = []
    for m in history[-8:]:
        input_items.append({"role": m["role"], "content": m["content"]})
    if not input_items or input_items[-1].get("content") != message:
        input_items.append({"role": "user", "content": message})

    result = Runner.run_streamed(agent, input_items, context=deps, max_turns=6)

    call_names: dict[str, str] = {}
    emitted_ends = 0
    async for ev in result.stream_events():
        if ev.type == "run_item_stream_event":
            item = ev.item
            if item.type == "tool_call_item":
                raw = item.raw_item
                name = getattr(raw, "name", "") or ""
                call_id = getattr(raw, "call_id", None) or getattr(raw, "id", "") or name
                call_names[str(call_id)] = name
                yield {
                    "type": "tool_start",
                    "name": name,
                    "label": TOOL_LABELS.get(name, name),
                }
            elif item.type == "tool_call_output_item":
                # 工具已执行完,取该工具写入的摘要(按顺序对应)
                summary = ""
                if emitted_ends < len(deps.tool_events):
                    evt = deps.tool_events[emitted_ends]
                    summary = evt.get("summary", "")
                    name = evt.get("name", "")
                    label = evt.get("label", name)
                else:
                    name, label = "", ""
                emitted_ends += 1
                yield {"type": "tool_end", "name": name, "label": label, "summary": summary}
        elif ev.type == "raw_response_event":
            data = ev.data
            if getattr(data, "type", "") == "response.output_text.delta":
                delta = getattr(data, "delta", "")
                if delta:
                    yield {"type": "delta", "text": delta}

    usage = getattr(result.context_wrapper, "usage", None)
    yield {
        "type": "final",
        "content": str(result.final_output or ""),
        "citations": deps.citations,
        "toolEvents": deps.tool_events,
        "usageTokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def agent_available() -> bool:
    """SDK 已安装且配置了千帆 key。"""
    if not settings.has_llm:
        return False
    try:
        import agents  # noqa: F401
        return True
    except ImportError:
        return False


def get_optional_client(db: OrmSession, client_id: str) -> Optional[Client]:
    return db.get(Client, client_id)
