"""演示数据 seed(可调用模块):4 个客户 + 保单 + 事项 + 待办 + 种子知识文档。

- seed(): 清空工作台相关表后重建(不影响白板 sessions/leads 与用户/订单/积分)
- ensure_seeded(): 库里没有客户时才 seed(桌面版首启初始化,幂等)
"""

from __future__ import annotations

import asyncio

from app.db import SessionLocal, init_db
from app.db_models import (
    Artifact,
    Client,
    ClientFile,
    Engagement,
    KbChunk,
    KbDocument,
    Member,
    Policy,
    Task,
    TaskEvent,
    Todo,
    WorkMessage,
)
from app.services import kb


SEED_CLIENTS = [
    {
        "name": "张伟一家",
        "type": "family",
        "notes": "关注子女海外教育与重疾保障;太太对年金险有兴趣。",
        "next_contact": "2026-09-02",
        "members": [
            {"name": "张伟", "relation": "本人", "badge": "已承保", "birthday": "1980-03-12", "notes": "企业主,年入约 200 万"},
            {"name": "李娜", "relation": "配偶", "badge": "方案中", "birthday": "1983-07-08", "notes": "关注重疾与医疗"},
            {"name": "张小明", "relation": "子女", "badge": "已承保", "birthday": "2015-11-20", "notes": "计划赴美读高中"},
            {"name": "张小花", "relation": "子女", "badge": "", "birthday": "2019-02-14", "notes": ""},
        ],
        # (member_idx, line, product, insurer, amount, premium, effective, expiry, status)
        "policies": [
            (0, "终身寿险", "传世金生终身寿", "平安人寿", 3_000_000, 86_000, "2022-06-01", None, "active"),
            (0, "重疾险", "守卫者6号", "友邦保险", 500_000, 15_800, "2021-03-15", None, "active"),
            (1, "医疗险", "尊享e生2026", "众安保险", 2_000_000, 1_260, "2025-11-02", "2026-11-01", "active"),
            (2, "重疾险", "大黄蜂10号(少儿)", "北京人寿", 300_000, 1_880, "2023-09-10", None, "active"),
        ],
        # (kind, title, line, policy_idx, note)
        "engagements": [
            ("proposal", "李娜重疾方案在谈", "重疾险", None, "预算 1.5 万/年,倾向多次赔付"),
            ("consult", "张小明教育金咨询", "教育金", None, "目标美国高中+本科,约 400 万"),
        ],
    },
    {
        "name": "周敏",
        "type": "personal",
        "notes": "外企高管,常出差;对高端医疗有兴趣。",
        "next_contact": "2026-09-05",
        "members": [
            {"name": "周敏", "relation": "本人", "badge": "已承保", "birthday": "1992-12-03", "notes": "年收入约 80 万"},
        ],
        "policies": [
            (0, "重疾险", "达尔文9号", "信泰人寿", 500_000, 9_600, "2023-05-20", None, "active"),
            (0, "医疗险", "好医保长期医疗", "人保健康", 2_000_000, 680, "2025-09-18", "2026-09-17", "pending_renewal"),
        ],
        "engagements": [
            ("proposal", "高端医疗方案在谈", "医疗险", None, "希望含特需部与直付网络"),
            ("renewal", "好医保 9 月续期", "医疗险", 1, "9 月 17 日到期,提前两周提醒"),
        ],
    },
    {
        "name": "华兴商贸有限公司",
        "type": "company",
        "notes": "小微贸易公司,45 名员工;仓库在郊区物流园。",
        "next_contact": "2026-09-10",
        "members": [
            {"name": "王海峰", "relation": "法人", "badge": "", "birthday": "1975-04-22", "notes": "决策人"},
            {"name": "刘会计", "relation": "联系人", "badge": "", "birthday": None, "notes": "对接投保与发票"},
        ],
        "policies": [
            (None, "企财险", "财产综合险", "人保财险", 5_000_000, 12_500, "2026-01-01", "2026-12-31", "active"),
            (None, "雇主责任险", "雇主责任险(45人)", "平安产险", 1_000_000, 9_000, "2026-03-01", "2027-02-28", "active"),
        ],
        "engagements": [
            ("consult", "团体医疗补充咨询", "团体医疗", None, "老板想给核心员工加福利"),
        ],
    },
    {
        "name": "王芳一家",
        "type": "family",
        "notes": "王芳住院理赔进行中;重疾险核保有甲状腺结节告知。",
        "next_contact": "2026-08-31",
        "members": [
            {"name": "王芳", "relation": "本人", "badge": "理赔中", "birthday": "1978-09-30", "notes": "甲状腺结节 R2"},
            {"name": "陈强", "relation": "配偶", "badge": "已承保", "birthday": "1976-05-01", "notes": ""},
        ],
        "policies": [
            (0, "医疗险", "e生保2025", "平安健康", 2_000_000, 960, "2025-04-12", "2026-04-11", "claiming"),
            (1, "重疾险", "超级玛丽9号", "君龙人寿", 300_000, 6_200, "2022-08-01", None, "active"),
        ],
        "engagements": [
            ("claim", "王芳住院医疗理赔", "医疗险", 0, "已提交资料,等待保司审核,预计赔付 2.4 万"),
            ("underwriting", "王芳重疾险核保中", "重疾险", None, "甲状腺结节告知,等待核保结论"),
        ],
    },
]

SEED_TODOS = [
    {"title": "王芳重疾险核保结论跟进", "detail": "已提交 5 个工作日,致电保险公司核保部。", "priority": "high", "client": 3},
    {"title": "张伟家庭方案书第二次讲解", "detail": "带利益演示表,重点讲教育金部分。", "priority": "normal", "client": 0},
    {"title": "华兴商贸团体医疗报价", "detail": "45 人清单已拿到,对比两家保司方案。", "priority": "normal", "client": 2},
]

SEED_KB_DOCS = [
    {
        "title": "重疾险核保常见问题手册",
        "tags": ["核保", "重疾"],
        "scope": "global",
        "text": """重疾险核保常见问题手册(演示种子文档)

甲状腺结节核保要点:
- TI-RADS 1-2 类:通常标准体承保,重疾与医疗均可正常费率。
- TI-RADS 3 类:重疾险常见除外甲状腺癌责任,医疗险可能除外或加费。
- TI-RADS 4 类及以上:通常延期至穿刺病理明确后评估。
- 已手术切除且病理为良性、复查超声正常满 6 个月,可申请复议承保。

高血压核保要点:
- 服药后血压稳定在 140/90 以下,重疾险多可加费或标准体承保。
- 合并蛋白尿或心电图异常时,通常需要体检复查后评估。

糖尿病核保要点:
- 2 型糖尿病控制良好(糖化<7)部分产品可承保,多数重疾险会拒保或延期。
- 妊娠期糖尿病产后恢复正常满一年,一般可标准体承保。""",
    },
    {
        "title": "高端医疗险产品对比(2026 版)",
        "tags": ["产品", "医疗"],
        "scope": "global",
        "text": """高端医疗险产品对比(演示种子文档)

住院责任:
- A 产品:年度保额 1200 万,含特需部与国际部,直付网络 900 家。
- B 产品:年度保额 800 万,含门诊责任可选,直付网络 600 家。
- C 产品:年度保额 2000 万,含全球除美,孕产责任等待期 12 个月。

门诊责任:
- A 产品门诊限额 8 万/年,0 免赔。
- B 产品门诊限额 5 万/年,次免赔 300 元。

常见除外:
- 既往症一般除外;先天性疾病的住院责任通常不含。
- 理赔时需要提供原始发票与费用清单,直付医院无需垫付。""",
    },
    {
        "title": "子女海外教育金规划思路",
        "tags": ["教育金", "规划"],
        "scope": "global",
        "text": """子女海外教育金规划思路(演示种子文档)

目标测算:
- 美国高中+本科:约 300-450 万人民币(按当前学费与 5% 年通胀估算)。
- 英国本科:约 180-250 万人民币。

工具选择:
- 增额终身寿/年金:确定性高,适合打底;注意缴费期与用钱期限匹配。
- 美元保单:天然对冲留学货币敞口,但需关注汇率与分红实现率。
- 教育金信托/保险金信托:适合大额且有多子女分配需求的家庭。

常见误区:
- 只看演示收益不看保证利益;把流动性差的产品当成短期储蓄。""",
    },
    {
        "title": "小微企业财险投保要点",
        "tags": ["财险", "企业"],
        "scope": "global",
        "text": """小微企业财险投保要点(演示种子文档)

企财险(财产综合险):
- 保险金额按重置价值投保,避免不足额投保导致按比例赔付。
- 仓储企业注意约定堆放高度与消防条件,现场查勘影响费率。
- 水淹、盗抢通常为附加险,按需勾选。

雇主责任险:
- 按在册员工人数与工种类别定价;高空、驾驶类工种费率上浮。
- 与团体意外的区别:雇主责任险赔给企业(转移用工风险),团意险赔给员工个人。
- 建议限额:死亡伤残每人不低于 80-100 万,含误工与医疗费用责任。

团体补充医疗:
- 5 人以上即可起投,可含门诊、住院、体检模块;
- 免赔与报销比例可与社保衔接设计,注意既往症约定。""",
    },
]


def reset(db) -> None:
    for model in (WorkMessage, TaskEvent, Task, Artifact, Todo, KbChunk, KbDocument,
                  ClientFile, Engagement, Policy, Member, Client):
        db.query(model).delete()
    db.commit()


def seed() -> None:
    db = SessionLocal()
    try:
        reset(db)

        # 演示账号 + 兑换码
        from app.db_models import RedeemCode, User
        from app.services.auth import hash_password

        demo_user = db.query(User).filter(User.username == "demo").first()
        if not demo_user:
            demo_user = User(
                username="demo",
                password_hash=hash_password("demo123456"),
                display_name="演示经纪人",
                plan="free",
            )
            db.add(demo_user)
            db.commit()
            db.refresh(demo_user)
        from app.api.billing import grant_signup_bonus

        grant_signup_bonus(db, demo_user)
        demo_codes = ["PRO-DEMO-0001", "PRO-DEMO-0002", "PRO-YEAR-0001"]
        for code, plan, days in [
            (demo_codes[0], "pro", 30),
            (demo_codes[1], "pro", 30),
            (demo_codes[2], "pro_year", 365),
        ]:
            if not db.get(RedeemCode, code):
                db.add(RedeemCode(code=code, plan=plan, days=days))

        for spec in SEED_CLIENTS:
            c = Client(
                name=spec["name"],
                client_type=spec["type"],
                notes=spec["notes"],
                next_contact=spec["next_contact"],
            )
            db.add(c)
            db.flush()
            members: list[Member] = []
            for i, m in enumerate(spec["members"]):
                member = Member(client_id=c.id, seq=i, **m)
                db.add(member)
                members.append(member)
            db.flush()
            policies: list[Policy] = []
            for (midx, line, product, insurer, amount, premium, eff, exp, status) in spec["policies"]:
                p = Policy(
                    client_id=c.id,
                    member_id=members[midx].id if midx is not None else None,
                    line=line,
                    product_name=product,
                    insurer=insurer,
                    amount=amount,
                    premium=premium,
                    effective_date=eff,
                    expiry_date=exp,
                    status=status,
                )
                db.add(p)
                policies.append(p)
            db.flush()
            for (kind, title, line, pidx, note) in spec["engagements"]:
                db.add(
                    Engagement(
                        client_id=c.id,
                        kind=kind,
                        title=title,
                        line=line,
                        policy_id=policies[pidx].id if pidx is not None else None,
                        note=note,
                    )
                )
        db.commit()

        # 待办挂到对应客户
        clients = db.query(Client).order_by(Client.created_at).all()
        for todo_spec in SEED_TODOS:
            idx = todo_spec.get("client")
            db.add(
                Todo(
                    title=todo_spec["title"],
                    detail=todo_spec["detail"],
                    priority=todo_spec["priority"],
                    client_id=clients[idx].id if idx is not None and idx < len(clients) else None,
                )
            )
        db.commit()

        # 知识库种子文档(真实走入库管线,无 key 时用 mock 向量)
        async def _seed_kb():
            for spec in SEED_KB_DOCS:
                doc = kb.create_document(
                    db,
                    title=spec["title"],
                    doc_type="text",
                    raw=spec["text"].encode("utf-8"),
                    tags=spec["tags"],
                    scope=spec["scope"],
                )
                await kb.index_inline_text(db, doc, spec["text"])
                print(f"  kb indexed: {doc.title} ({doc.chunk_count} chunks, status={doc.status})")

        asyncio.run(_seed_kb())
        print(
            f"seeded {db.query(Client).count()} clients, "
            f"{db.query(Policy).count()} policies, "
            f"{db.query(Engagement).count()} engagements, "
            f"{db.query(Todo).count()} todos"
        )
    finally:
        db.close()


def ensure_seeded() -> bool:
    """首启初始化:没有任何客户才灌演示数据。返回是否执行了 seed。"""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        if db.query(Client).count() > 0:
            return False
    finally:
        db.close()
    seed()
    return True
