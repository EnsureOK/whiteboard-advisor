# WhiteboardAdvisor 工程规格（SPEC）

> **版本**: v1.0 · 更新日期: 2026-09-06
> **产品形态**: 保险经纪人的 AI 办公工作台（原"AI 白板规划演示"已演化为工作台内的一个视图）
> **配套文档**: [PRD_WhiteboardAdvisor_v0.1.md](../PRD_WhiteboardAdvisor_v0.1.md)（白板期历史 PRD，仅覆盖白板视图）· [desktop/DISTRIBUTE.md](../desktop/DISTRIBUTE.md)（打包分发）· [cloudbase/README.md](../cloudbase/README.md)（计费 serverless 化）

---

## 1. 产品定义与形态

一句话：**资深保险经纪人的智能助理**——以客户/家庭为单位组织全部业务数据，对话式驱动任务规划与执行，产出可交付工件（方案书、检视矩阵、图表报告、办公文档），并内建展业合规审核与积分计费。

仓库内并存四个顶层前端应用，由 URL 参数分发（`frontend/src/main.tsx`，无路由库）：

| 入口 | 应用 | 说明 |
|---|---|---|
| `?view=workbench` | **工作台**（主产品） | 三栏布局：客户树 / 对话任务 / 工件区 |
| 无参数 | 白板演示 App | 白板期的客户 facing 演示（WebSocket 实时画板 + 语音解说） |
| `?portal=` | 经纪人门户 | 白板期 lead 领取 |
| `?share=` | 只读分享页 | 白板方案分享（脱敏） |

桌面壳：`desktop/`（pywebview + pyinstaller），五平台 CI 矩阵打 tag 出包，见 DISTRIBUTE.md。

## 2. 系统架构

```
前端 React18+TS+Vite ── HTTP/SSE/WS ──> FastAPI (backend/app)
                                          │
        ┌──────────────┬─────────────────┼──────────────┬─────────────┐
        │              │                 │              │             │
   SQLite(20表)   千帆 LLM/OpenAI    百度语音 ASR/TTS   企微自建应用    Stripe
   +本地文件      兼容接口(双模型)    (可选)            回调(双向)     Checkout+Webhook
```

- **后端**: FastAPI + SQLAlchemy + SQLite（`backend/data/app.db`，WAL 模式），7 个 router 挂载于 `main.py`：`session_ws / broker_portal / workbench / kb_admin / auth / billing / wecom`。
- **LLM**: 千帆 v2 OpenAI 兼容接口（`services/llm.py`），快模型（交互轮）与 deep 模型（规划）分流；无 key 自动降级 mock，保证全流程可演示可测试。
- **桌面打包**: `paths.py` 处理 pyinstaller frozen 资源；frozen 时数据目录迁移到 `~/Library/Application Support/WorkbenchAdvisor`。

## 3. 后端模块清单（backend/app）

### api/（路由层）
| 模块 | 职责 |
|---|---|
| `workbench.py`（核心，~1180 行） | bootstrap、客户/成员/保单/约谈/客户文件 CRUD、`/chat` SSE 流式对话、任务（创建/批量/step/approve/confirm/revise）、工件导出与修订、保障图表报告、合规审核、每日简报、待办 |
| `auth_api.py` | 短信验证码（发送/校验）、注册、登录、`/me`；JWT Bearer |
| `billing.py` | 套餐/权益状态/扣费流水/注册赠礼领取/兑换码/Stripe Checkout/webhook/订单 |
| `kb_admin.py` | 知识库文档上传（PDF/Word/TXT/MD/HTML）、URL 抓取、重建索引、检索测试 |
| `wecom_app.py` | 企微自建应用回调：GET 验签 + POST 双向消息（@客户名 触发助理干活并回推） |
| `session_ws.py` | 白板 WebSocket 会话通道（白板 App 专用） |
| `broker_portal.py` | 白板期遗留 lead 列表与领取 |

### services/（领域层，29 个模块）
- **对话与 agent**: `agent.py`（OpenAI Agents SDK，工具=知识库/客户档案/保单/缺口计算，失败降级纯 RAG）· `soul.md`（助理人格与准则，热加载）· `websearch.py`（联网检索，引用结构化 `[Wn]` 角标）· `guardrails.py`
- **任务编排**: `task_engine.py`（~700 行）——plan-first 计划生成（LLM 产出可并行 DAG）→ `normalize_plan/ready_nodes/execute_node` 执行引擎 → 审批节点阻塞下游、并行分支互不影响、上游产物传递下游；批量 fan-out（多客户 `asyncio.gather` 并行出计划）
- **定时与推送**: `scheduler.py`（定时作业/每日简报/自动待办）· `wecom.py`（群机器人 webhook 单向推送）
- **知识库**: `kb.py`（上传→解析→切分→入库→混合检索）· `embedding.py`（千帆 embeddings，无 key 用确定性 mock 向量）· `rag.py`（白板期关键词种子库，仍在服务白板）
- **计量计费**: `credits.py`（1 积分 = 2000 tokens；双池：plan 月赠池到期清零 + pack 永不过期，先扣 plan 后扣 pack）· `cost.py`（LLM 成本估算与预算上限）
- **产出物**: `coverage_report.py`+`chartkit.py`+`report_html.py`（确定性保障图表报告）· `office.py`（Excel/Word/PPT 导出）· `pdf_export.py`
- **合规**: `compliance.py`（展业话术合规规则库三层查重 + LLM 批审，改造自 MIT 项目 insurance-business-operations）
- **白板期四件套**: `dialogue.py` `zone_engine.py` `llm.py` `templates/`（9 zone × 4 模板）
- **其他**: `auth.py`（JWT + pbkdf2）· `sms.py` · `session_store.py` · `broker.py`/`lead_store.py` · `speech.py` · `demo_seed.py` · `workbench_store.py`（序列化）

### 数据模型（db_models.py，20 张表）
- **业务域**: `clients`（客户：个人/家庭/企业）· `members` · `policies` · `engagements` · `client_files`
- **任务域**: `tasks`（含 DAG plan_json、created_by）· `task_events` · `artifacts` · `todos`
- **知识库**: `kb_documents` · `kb_chunks`（向量存 BLOB）
- **账户计费**: `users`（手机号/用户名 + JWT）· `credit_ledger` · `orders` · `redeem_codes` · `sms_codes`
- **运营**: `daily_briefings` · `scheduler_runs` · `agent_memories` · `compliance_rules`
- 白板期遗留: `households`（已被 clients 取代，暂保留）· `wb_messages`

## 4. 关键流程

### 4.1 任务生命周期（plan-first + DAG）
```
创建(单个/批量 fan-out) → LLM 生成计划(节点带 deps/可声明需审批)
  → normalize_plan 补 id/deps → 循环 ready_nodes 逐节点执行
  → 节点事件落 task_events(SSE 推前端) → 审批节点 waiting_confirmation 阻塞下游
  → confirm 放行 → 全部完成 → 工件落 artifacts → 积分按 token 扣减
```
服务端自动执行（任务中心，3 秒轮询）；计划可 `/revise` 调整后重新执行。

### 4.2 对话（SSE）
`POST /api/workbench/chat` → agent.py 编排（客户上下文 + 知识库检索 + 联网搜索 + 记忆）→ 流式 token + 结构化引用 → 前端流式渲染，支持成员级聚焦（点选成员后问题默认针对 TA）。

### 4.3 知识库
上传/URL 抓取 → 解析 → ~500 字切分 → 千帆 embedding（无 key 降级 mock）→ SQLite BLOB 存储 + numpy 余弦 top-k + 关键词命中混合排序（0.7/0.3）→ 按作用域过滤（全局 / 客户私有）。前端知识库视图含**检索测试面板**（可见召回片段与分数）。

### 4.4 认证与计费
- **认证**: 手机号短信验证（`sms_codes`）+ 用户名密码（pbkdf2）双通道，JWT（`services/auth.py`）；chat/tasks 用 optional user——未登录可演示，登录后计量扣费生效。
- **计费**: 三档套餐（免费版/基础版 ¥59/专业版 ¥99，月赠 0/10,000/30,000 积分）+ 积分包 + 注册赠 2,000 积分 + 兑换码 + Stripe Checkout（webhook 履约 `_fulfill_order`）。扣费流水 ref 人话化（"任务执行"等）。

### 4.5 企微双向通道
`GET /api/wecom/callback` 验签 → `POST` 收消息 → 解析 @客户名 命中客户 → 走同一 agent 链路 → 回推企微；单向推送（每日简报/任务完成）走群机器人 webhook。

### 4.6 展业合规
对话与文档自动过 `compliance.py` 规则库（三层查重 + LLM 批审），违规标注出现在对话侧与导出文档上。

## 5. Serverless 计费（cloudbase/）

`cloudbase/billing/index.js` 云函数与本地 `api/billing.py` **同构**（同一套餐配置、同一兑换码/订单模型）。前端配 `VITE_BILLING_URL` 后直连云函数（订单/权益完全托管，无自建后端）；未配置走本地适配器。微信支付在 `order/payCallback` 两个 action 预留，接入步骤见 [cloudbase/README.md](../cloudbase/README.md)。**注意：该目录当前未入库（git untracked），需提交。**

## 6. 前端结构（frontend/src）

```
main.tsx(URL 参数分发)
├── workbench/           主产品
│   ├── Workbench.tsx    外壳: 左 rail(ZCode 风格图标栏) + 三栏
│   ├── ClientPane.tsx   客户列表(个人/家庭/企业分类)+待办+每日简报+成员聚焦
│   ├── ChatPane.tsx     SSE 流式对话 + 任务事件流 + 引用角标
│   ├── ArtifactPane.tsx 工件区(窄屏抽屉)
│   ├── TasksView.tsx    任务中心 + 批量 fan-out(3s 轮询)
│   ├── BillingView.tsx  登录/注册 + 套餐/积分/兑换码/订单流水
│   ├── KnowledgeView.tsx 知识库管理(上传/详情/检索测试)
│   └── api.ts           fetch 封装 + token 管理 + chatStream(SSE 解析)
├── App.tsx + zones/ + components/ + hooks/   白板演示 App
└── ShareView / BrokerPortal                  白板期遗留
```

## 7. 环境变量（backend/.env）

| 变量 | 必需 | 说明 |
|---|---|---|
| `QIANFAN_API_KEY` | 否 | 千帆 LLM + embedding；缺省降级 mock 模式 |
| `QIANFAN_MODEL_FAST/DEEP` | 否 | 快/深模型分流 |
| `BAIDU_SPECH_API_KEY/SECRET_KEY` | 否 | 语音 ASR/TTS |
| `JWT_SECRET` | 生产必需 | 登录态签名；缺省用开发密钥 |
| `STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET / STRIPE_PRICE_*` | 收款必需 | Stripe Checkout |
| 企微应用凭据 | 否 | 双向通道 |

## 8. 运行 / 测试 / 打包

```bash
# 后端
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/python scripts/seed_workbench.py   # 演示数据(家庭/知识库/兑换码)
.venv/bin/python -m pytest                   # 105 个测试, 24 个文件

# 前端
cd frontend && npm run dev                   # http://localhost:5173/?view=workbench

# 桌面打包(五平台 CI 矩阵)
./desktop/build.sh                           # 本地 mac 出包
git tag v0.x && git push --tags              # GitHub Actions 自动 Release
```

## 9. 安全评估（2026-09-06 深度扫描）与遗留事项

Mimosa deep 扫描 + pip-audit 完成，分诊结论：

**已修复**
- 企微回调 XXE（CWE-776，扫描定级 HIGH）→ `_parse_xml()` 守卫：限 1MB、拒绝 DOCTYPE/ENTITY、defusedxml 禁实体扩展；恶意报文有测试覆盖（`tests/test_security_hardening.py`）。
- `python-multipart` 0.0.12 → 0.0.20（上传通道，py3.9 内上限）。

**已加固开关（P1）**
- `AUTH_REQUIRED` 环境变量（默认 false）：置 true 时工作台/知识库整条路由强制登录（`services/auth.py::auth_gate`）。多人 Web 部署前置 true；桌面版/单机演示保持 false。
- 尚未做**资源归属绑定**（clients 表无 owner 字段）：门禁只解决"必须登录"，多租户下还需校验资源归属者，见遗留。

**已评估接受（记录在案）**
- `desktop/launcher.py` 路径/SSRF 告警：本地壳读写自身数据目录、健康检查本机后端，无可信输入源。
- 白板 `/api/session/{id}/pdf|share`：capability URL 模型（32 位 hex 不可枚举），share 本为公开只读链接。
- `wecom_app.py` SHA1 签名：企微 WXBizMsgCrypt 协议规定，不可更换。

**遗留（按优先级）**
1. **Python 3.9 → 3.10+ 运行时升级**：pip-audit 残留的 multipart/urllib3/requests/starlette 修复版均已放弃 3.9，这是根治路径；升级后把 CI `dependency-audit` 作业改为阻塞。
2. **多租户资源归属**：clients 等表加 owner 字段 + 端点校验（与 AUTH_REQUIRED 配套）。
3. `api/workbench.py` 单文件 ~1180 行，建议按域拆分。
4. 白板 App 与工作台入口分散，长期合并导航或归档。
5. 文档：本 SPEC 与 README 已对齐现状（2026-09-06），后续大改动同步更新。
