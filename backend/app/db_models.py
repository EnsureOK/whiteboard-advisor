"""工作台 ORM 模型:客户/成员/保单/业务事项/任务/工件/待办/知识库。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from typing import Optional

from sqlalchemy import BLOB, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 客户类型
CLIENT_TYPES = ["personal", "family", "company"]

# 业务事项类型(可多个并存,替代线性服务阶段)
ENGAGEMENT_KINDS = {
    "consult": "咨询中",
    "proposal": "方案在谈",
    "underwriting": "投保中",
    "claim": "理赔中",
    "renewal": "续期跟进",
    "preservation": "保全办理",
}

# 保单状态
POLICY_STATUSES = {
    "active": "有效",
    "pending_renewal": "待续期",
    "claiming": "理赔中",
    "lapsed": "已失效",
}


def utcnow_iso() -> str:
    return _now().isoformat()


class Client(Base):
    """客户:个人 / 家庭 / 企业。寿险与财险业务共用。"""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    # personal / family / company
    client_type: Mapped[str] = mapped_column(String(20), default="family", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # 下次接触时间(ISO 日期或 null)
    next_contact: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso, onupdate=utcnow_iso)

    members: Mapped[list["Member"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="Member.seq"
    )
    policies: Mapped[list["Policy"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="Policy.created_at"
    )
    engagements: Mapped[list["Engagement"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="Engagement.created_at"
    )
    files: Mapped[list["ClientFile"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="ClientFile.created_at"
    )


class Member(Base):
    """家庭成员 / 企业联系人 / 个人客户本人。"""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    # 家庭:本人/配偶/子女/父母;企业:法人/联系人/HR ...
    relation: Mapped[str] = mapped_column(String(20), default="本人")
    birthday: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # 状态徽标: 待面谈/方案中/已承保 ...
    badge: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)

    client: Mapped[Client] = relationship(back_populates="members")


class Policy(Base):
    """托管保单:已成交、在管的真实保单,是检视/续期/理赔的基础数据。"""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    # 被保人(企业财险等无个人被保人时为 null)
    member_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 险种: 寿险/重疾险/医疗险/意外险/年金险/车险/家财险/企财险/雇主责任险/团体医疗 ...
    line: Mapped[str] = mapped_column(String(30), index=True)
    product_name: Mapped[str] = mapped_column(String(120), default="")
    insurer: Mapped[str] = mapped_column(String(60), default="")
    # 保额(元);医疗险等按报销额度
    amount: Mapped[int] = mapped_column(Integer, default=0)
    # 年缴保费(元)
    premium: Mapped[int] = mapped_column(Integer, default=0)
    effective_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    expiry_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # active / pending_renewal / claiming / lapsed
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    client: Mapped[Client] = relationship(back_populates="policies")


class Engagement(Base):
    """进行中的业务事项:咨询/方案在谈/投保/理赔/续期/保全,可多个并存。"""

    __tablename__ = "engagements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    # ENGAGEMENT_KINDS 之一
    kind: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # 关联险种(在谈的是什么险)
    line: Mapped[str] = mapped_column(String(30), default="")
    # 关联保单(理赔/续期时)
    policy_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # open / done / paused
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso, onupdate=utcnow_iso)

    client: Mapped[Client] = relationship(back_populates="engagements")


class ClientFile(Base):
    """客户资料附件:创建/维护客户时上传的文件与图片。

    可解析文档(pdf/docx/txt/md/html)同时进入该客户私有知识库(kb_doc_id 关联);
    图片先存档可预览,后续可接 OCR 入库。
    """

    __tablename__ = "client_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # image / document
    kind: Mapped[str] = mapped_column(String(10), default="document")
    # 磁盘相对路径(data/client_files/ 下)
    path: Mapped[str] = mapped_column(String(400), default="")
    # 入库后的知识库文档 id(图片或解析失败为 null)
    kb_doc_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    client: Mapped[Client] = relationship(back_populates="files")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    # policy_review / generate_plan / prepare_visit / followup / generic
    kind: Mapped[str] = mapped_column(String(32), default="generic", index=True)
    # planned -> approved -> running -> done / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)
    # 批量任务组 id(跨客户 fan-out 的任务共享同一 batch)
    batch_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # 创建者(user id);未登录的旧数据为 null
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # 计划卡内容(步骤列表,可被用户编辑后确认)
    plan_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso, onupdate=utcnow_iso)

    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.seq"
    )


class TaskEvent(Base):
    """任务执行时间线的一条记录:计划步骤/工具调用/审批请求/结果。"""

    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # plan / tool / approval / message / artifact
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200), default="")
    # 运行状态: pending / running / done / failed / waiting_confirm / confirmed
    status: Mapped[str] = mapped_column(String(20), default="done")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    task: Mapped[Task] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # plan_doc / review_matrix / checklist / report
    type: Mapped[str] = mapped_column(String(32), default="report", index=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    # 同一 (client, type) 下按 version 递增,列表页只取最新版
    __table_args__ = (Index("ix_artifact_client_type", "client_id", "type", "version"),)


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    # high / normal
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    # open / done
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    due: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class WorkMessage(Base):
    """工作台对话消息(与白板 session 的 dialogue 相互独立)。"""

    __tablename__ = "wb_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # 引用: [{"docId","docTitle","chunkId","quote","score"}]
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    # agent 工具过程: [{"name","label","summary"}]
    tool_events_json: Mapped[str] = mapped_column(Text, default="[]")
    task_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300), default="")
    # pdf / docx / txt / md / html / text / url
    doc_type: Mapped[str] = mapped_column(String(20), default="text")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    # global 或 client:<id>
    scope: Mapped[str] = mapped_column(String(60), default="global", index=True)
    # uploaded -> parsing -> indexed / failed
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso, onupdate=utcnow_iso)

    chunks: Mapped[list["KbChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="KbChunk.seq"
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    # numpy float32 向量的原始字节;无 embedding 时为 null(走 FTS)
    embedding: Mapped[Optional[bytes]] = mapped_column(BLOB, nullable=True)
    # 向量维度,便于校验
    dim: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    document: Mapped[KbDocument] = relationship(back_populates="chunks")


# ---------- 账户与计费 ----------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    # 手机号登录(正式版主路径);老账号密码登录的 username 用户此列为空
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)
    # pbkdf2_sha256$iterations$salt_hex$hash_hex;验证码登录创建的用户为空串
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(60), default="")
    # free / basic / pro / max
    plan: Mapped[str] = mapped_column(String(20), default="free", index=True)
    # ISO 时间;null 表示免费版无期限
    plan_expires_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # 积分余额,以 token 为最小记账单位(1 积分=2000 tokens,展示时换算)
    # plan 池:套餐月赠,发放时清零重置;pack 池:积分包购买,永不过期
    credit_tokens_plan: Mapped[int] = mapped_column(Integer, default=0)
    credit_tokens_pack: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class CreditLedger(Base):
    """积分流水:发放/消耗,以 token 计。"""

    __tablename__ = "credit_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    # 正=发放,负=消耗
    delta_tokens: Mapped[int] = mapped_column(Integer)
    # signup_grant / plan_grant / pack_grant / redeem_grant / consume
    source: Mapped[str] = mapped_column(String(20), index=True)
    # 关联说明: 订单 id / 消耗场景(chat/task/embedding)等
    ref: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class Order(Base):
    """会员订单。serverless 化后由云函数维护同名结构的集合。"""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    # pro / pro_year
    plan: Mapped[str] = mapped_column(String(20))
    # 分单位
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    # redeem / wechat_pay
    channel: Mapped[str] = mapped_column(String(20), default="redeem")
    # created / paid / refunded
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    # 兑换码 / 微信支付单号等
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    # Stripe Checkout Session id(幂等履约凭据)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    paid_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class DailyBriefing(Base):
    """每日简报:调度器汇总到期保单/理赔/待办,LLM 成文(或模板)。"""

    __tablename__ = "daily_briefings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 本地日期 YYYY-MM-DD,唯一
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class SchedulerRun(Base):
    """定时作业执行记录(按日幂等)。"""

    __tablename__ = "scheduler_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job: Mapped[str] = mapped_column(String(40), index=True)
    run_date: Mapped[str] = mapped_column(String(10), index=True)
    detail: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    __table_args__ = (Index("ix_scheduler_job_date", "job", "run_date", unique=True),)


class AgentMemory(Base):
    """agent 持久记忆:经纪人偏好(user 级)与客户关键事实(client 级),跨会话注入。"""

    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # broker 记忆归属的用户;未登录会话不写 broker 记忆
    user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # client 记忆归属的客户;broker 级记忆此列为空
    client_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # broker / client
    scope: Mapped[str] = mapped_column(String(10), index=True)
    content: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class SmsCode(Base):
    """手机验证码(登录):5 分钟有效,同号 60s 冷却,错 5 次作废。"""

    __tablename__ = "sms_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(8))
    expires_at: Mapped[str] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # pending / used / void
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class RedeemCode(Base):
    __tablename__ = "redeem_codes"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan: Mapped[str] = mapped_column(String(20), default="pro")
    days: Mapped[int] = mapped_column(Integer, default=30)
    # unused / used / disabled
    status: Mapped[str] = mapped_column(String(20), default="unused", index=True)
    used_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    used_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
