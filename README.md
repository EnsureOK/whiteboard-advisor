# WhiteboardAdvisor

保险经纪人的 AI 办公工作台。以**客户/家庭**为单位组织全部业务数据，对话式驱动任务规划与执行（plan-first + DAG），产出可交付工件（方案书、保单检视矩阵、图表报告、Word/PPT/Excel），内建展业合规审核、知识库与积分计费。

> 产品从 v0.1 的"AI 实时画白板 + 语音解说"演示工具，演化为经纪人日常作业的工作台。白板演示保留为其中一个视图。
> **当前工程规格见 [docs/SPEC.md](./docs/SPEC.md)**（架构 / API / 数据模型 / 计费 / 打包）。

## 功能总览

- **工作台**（`?view=workbench`，主产品）：客户树（个人/家庭/企业）· 流式对话（知识库引用 + 联网信源角标 + 成员级聚焦）· 任务中心（批量 fan-out + DAG 并行 + 审批阻塞下游）· 工件区（检视矩阵/图表报告/办公文档导出）· 每日简报与自动待办
- **知识库**：PDF/Word/TXT/MD/HTML 上传、URL 抓取，千帆 embedding + 混合检索，全局/客户私有作用域，检索测试面板
- **账户与计费**：手机号验证码/用户名密码登录，三档套餐 + 积分（1 积分 = 2000 tokens）+ 兑换码 + Stripe Checkout；serverless 形态见 [cloudbase/README.md](./cloudbase/README.md)
- **展业合规**：话术规则库三层查重 + LLM 批审，对话与导出文档自动标注
- **企微集成**：群机器人单向推送（简报/任务完成）+ 自建应用双向对话（@客户名 让助理干活）
- **桌面版**：pywebview + pyinstaller，五平台 CI 矩阵，见 [desktop/DISTRIBUTE.md](./desktop/DISTRIBUTE.md)；官网下载页 `website/download.html`

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React 18 + TypeScript + Vite + framer-motion（无路由库，URL 参数分发） |
| 后端 | Python 3.9 + FastAPI + SQLAlchemy + SQLite（WAL） |
| LLM | 百度千帆 v2（OpenAI 兼容，快/深双模型分流，无 key 降级 mock） |
| 语音 | 百度智能云 ASR / TTS |
| 支付 | Stripe Checkout（webhook 履约）；serverless 计费云函数（CloudBase） |

## 本地运行

```bash
# 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 千帆/语音/Stripe/企微 key 均可选,缺省自动降级
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev
# 打开 http://localhost:5173/?view=workbench
```

演示数据（客户/知识库/兑换码）：`cd backend && .venv/bin/python scripts/seed_workbench.py`
测试：`.venv/bin/python -m pytest`（105 个测试）

## 目录结构

```
backend/app/
  api/        workbench(核心) auth billing kb_admin wecom_app session_ws broker_portal
  services/   agent task_engine(DAG) kb/embedding credits/compliance/scheduler/wecom
              dialogue+zone_engine+llm(白板四件套) office/coverage_report/chartkit ...
  db_models.py  20 张表(clients/policies/tasks/artifacts/users/credit_ledger/orders/...)
frontend/src/
  workbench/  Workbench(外壳) ClientPane ChatPane ArtifactPane TasksView BillingView KnowledgeView
  App.tsx + zones/ + hooks/   白板演示 App
cloudbase/    计费云函数(serverless, 与本地 billing.py 同构)
desktop/      pywebview 壳 + pyinstaller + CI 分发说明
docs/SPEC.md  ★ 工程规格(当前形态的完整说明)
```

## 历史文档

- [PRD_WhiteboardAdvisor_v0.1.md](./PRD_WhiteboardAdvisor_v0.1.md) — 白板期 PRD（V0.1 里程碑 M1–M4 与 Phase 0–9 已全部交付，产品形态此后升级为工作台）
- [docs/superpowers/plans/2026-05-22-prd-buildout.md](./docs/superpowers/plans/2026-05-22-prd-buildout.md) — 白板期实施计划
