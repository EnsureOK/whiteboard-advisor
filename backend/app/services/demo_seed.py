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
    ComplianceRule,
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
                  ClientFile, Engagement, Policy, Member, Client, ComplianceRule):
        db.query(model).delete()
    db.commit()


def seed() -> None:
    db = SessionLocal()
    try:
        reset(db)
        ensure_compliance_rules(db)

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
    """开发/演示环境:没有任何客户才灌 4 个演示客户。返回是否执行了 seed。"""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        if db.query(Client).count() > 0:
            return False
    finally:
        db.close()
    seed()
    return True


# ---------- 正式版首启:标准案例(单个示例客户) + 全局知识文档 ----------

STARTER_CLIENT = {
    "name": "示例·陈家明一家",
    "type": "family",
    "notes": "这是内置示例客户:可放心体验检视保单、生成方案、对话提问,不影响你的真实客户数据。",
    "next_contact": None,
    "members": [
        {"name": "陈家明", "relation": "本人", "badge": "已承保", "birthday": "1985-06-18", "notes": "企业中层,家庭支柱"},
        {"name": "林晓芸", "relation": "配偶", "badge": "方案中", "birthday": "1988-02-09", "notes": "关注重疾保障"},
        {"name": "陈天天", "relation": "子女", "badge": "", "birthday": "2016-09-01", "notes": ""},
    ],
    "policies": [
        (0, "重疾险", "康宁无忧重疾", "中国人寿", 500_000, 12_600, "2022-04-15", None, "active"),
        (0, "医疗险", "安享百万医疗", "人保健康", 2_000_000, 850, "2025-12-01", "2026-11-30", "active"),
        (1, "定期寿险", "守护家定期寿", "太平洋人寿", 1_000_000, 1_450, "2023-08-20", None, "active"),
    ],
    "engagements": [
        ("proposal", "林晓芸重疾方案在谈", "重疾险", None, "预算 8 千-1 万/年,倾向多次赔付"),
        ("renewal", "陈家明医疗险续期跟进", "医疗险", 1, "2026-11-30 到期,提前一个月确认续保"),
    ],
}


def ensure_starter_content() -> bool:
    """正式版首启:不灌演示客户群,只放 1 个标注清楚的示例客户 + 全局知识文档
    + 1 条体验待办。库里已有客户则跳过。返回是否执行。"""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        # 合规规则独立幂等:老库升级(已有客户)也要补上
        ensure_compliance_rules(db)
        if db.query(Client).count() > 0:
            return False

        spec = STARTER_CLIENT
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
        db.add(
            Todo(
                title="【体验】给示例客户做一次保单检视",
                detail="点开左侧「示例·陈家明一家」,发送快捷指令「检视保单」,看 AI 如何逐步执行并生成矩阵。",
                priority="normal",
                client_id=c.id,
            )
        )
        db.commit()

        # 全局知识文档(通用核保/产品/财险知识,正式使用同样需要;去掉演示标注)
        async def _kb():
            for doc_spec in SEED_KB_DOCS:
                text = doc_spec["text"].replace("(演示种子文档)", "").strip()
                doc = kb.create_document(
                    db,
                    title=doc_spec["title"],
                    doc_type="text",
                    raw=text.encode("utf-8"),
                    tags=doc_spec["tags"],
                    scope=doc_spec["scope"],
                )
                await kb.index_inline_text(db, doc, text)

        asyncio.run(_kb())
        return True
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 预置合规规则集(行业通用参考规则 · 上线前请合规专员复核)
# 依据人身险/财险销售宣传公开监管禁止性规定整理;pattern 为高置信正则,
# 供无 LLM 时本地扫描降级。rule_set_id 固定 rs_industry_base。
# ---------------------------------------------------------------------------

_RS = "rs_industry_base"
_SRC = "行业通用参考规则 · 上线前请合规专员复核"


def _rule(code, text, point, level, suggestion, pattern=None, tags=None, resolution=None):
    return {
        "rule_set_id": _RS,
        "rule_set_name": "保险销售宣传通用禁止性规则",
        "risk_code": code,
        "rule_text": text,
        "audit_point": point,
        "risk_level": level,
        "suggestion": suggestion,
        "pattern": pattern,
        "scene_tags": tags or ["营销物料", "话术"],
        "source_doc": _SRC,
        **({"resolution_policy": resolution} if resolution else {}),
    }


SEED_COMPLIANCE_RULES = [
    _rule("C01", "不得承诺保证收益或以保本保息、稳赚不赔等表述宣传保险产品。",
          "检查是否出现保证收益、保本、稳赚、零风险等承诺性表述(分红/万能/投连的收益均不确定)。",
          "高", "改为「历史结算利率仅供参考,未来收益以保单实际为准」类表述。",
          pattern=r"保本保息|保证收益|稳赚(不赔)?|包赚|零风险|无风险高收益|稳赔不亏",
          resolution={"priority": 100, "suppresses": ["C02", "C22"],
                      "suppresses_keys": [f"{_RS}:C02", f"{_RS}:C22"]}),
    _rule("C02", "不得将保险产品与银行存款、储蓄、理财收益直接类比混淆。",
          "检查是否用存款、利息、储蓄等概念类比保险,误导客户将保险理解为存款替代品。",
          "高", "说明保险的保障属性与流动性约束,勿与存款作收益对比。",
          pattern=r"(比|像|当|等于|跟)(银行)?存(款|钱)|利息更高|高于(银行)?利息|活期还划算",
          resolution={"priority": 30, "suppressed_by": ["C01"],
                      "suppressed_by_keys": [f"{_RS}:C01"]}),
    _rule("C03", "不得夸大保险责任范围,宣称「什么都赔」「任何情况都赔」。",
          "检查是否夸大保障范围、淡化除外责任与等待期。",
          "高", "列明保障责任与主要除外责任,提示以条款为准。",
          pattern=r"什么都(赔|保)|全部都能赔|任何情况都赔|100%赔付|无所不保"),
    _rule("C04", "不得使用最好、第一、唯一等绝对化用语宣传产品或公司。",
          "检查是否出现绝对化排名或程度用语且无权威依据。",
          "中", "改用可核实的客观表述,或注明数据来源与统计口径。",
          pattern=r"(全国|行业|全网)第一|最好的保险|最优秀|唯一一款|绝无仅有|性价比之王"),
    _rule("C05", "不得诋毁、贬低同业公司或同业产品。",
          "检查是否出现对其他保险公司/产品的贬损性对比。",
          "中", "客观罗列产品差异,不作贬损性评价。",
          pattern=r"(其他|别的|某些)(保险)?公司.{0,10}(不赔|骗|坑|垃圾|不靠谱)"),
    _rule("C06", "不得以产品停售、限时涨价进行炒作销售。",
          "检查是否利用停售/涨价制造紧迫感诱导投保。",
          "高", "如产品确将停售,仅作事实告知,不得作为销售诱导。",
          pattern=r"(即将|马上|全面)停售|停售(涨价)?倒计时|绝版(产品|保险)|最后.{0,3}(天|机会).{0,6}(买|投保|上车)"),
    _rule("C07", "不得向客户返还佣金、赠送现金或变相利益输送。",
          "检查是否承诺返佣、返现、送红包等利益诱导。",
          "高", "删除一切返佣返现表述;赠品须符合公司与监管小额规定。",
          pattern=r"返佣|返现金?|退.{0,3}佣金|买保险(送|返)(现金|红包|礼金)"),
    _rule("C08", "不得承诺理赔结果或宣称理赔无条件。",
          "检查是否承诺「保证理赔」「肯定赔」等结果性表述。",
          "高", "改为说明理赔流程与所需资料,结果以核赔为准。",
          pattern=r"(保证|肯定|一定|百分百)(能)?(理赔|赔付|赔到)|理赔无忧.{0,4}必赔"),
    _rule("C09", "不得将保险产品混淆为理财产品、基金、信托等其他金融产品。",
          "检查是否把保险表述为理财/基金/信托,模糊产品性质。",
          "高", "明确表述为保险产品,收益演示遵守监管口径。",
          pattern=r"(就是|等于|类似|当作)一?(款|个)?(高息)?(理财|基金|信托)(产品)?"),
    _rule("C10", "分红、万能、投连产品不得以历史最高档收益作确定性演示。",
          "检查收益演示是否用最高档/历史高点并暗示可持续。",
          "中", "按监管要求的演示利率档位展示,注明不确定性。",
          pattern=r"(按|以)最高(档|收益|利率)演示|历史(最高)?收益.{0,6}(保证|承诺|肯定)"),
    _rule("C11", "销售时须提示犹豫期权利与如实告知义务,不得隐瞒重要信息。",
          "检查面向客户的成交话术是否隐瞒犹豫期、如实告知等关键信息。",
          "低", "在方案书/成交沟通中补充犹豫期与如实告知提示。"),
    _rule("C12", "不得冒用银行、政府、监管机构名义为产品背书。",
          "检查是否出现银行/政府/监管推出、担保、背书等表述。",
          "高", "删除背书表述;银保渠道仅可作事实性代销说明。",
          pattern=r"(银行|政府|国家|银?保监会?)(推出|担保|背书|指定)"),
    _rule("C13", "不得以限量发售、名额稀缺等饥饿营销话术诱导投保。",
          "检查是否使用限量/仅剩名额等稀缺性话术。",
          "中", "删除稀缺性诱导,以产品价值本身沟通。",
          pattern=r"限量(发售|抢购|\d+份)|仅剩.{0,4}(名额|份|席)"),
    _rule("C14", "不得使用无法核实的理赔数据作宣传(如理赔率100%)。",
          "检查理赔数据是否有官方来源与统计口径。",
          "中", "引用公司官方披露的理赔年报数据并注明出处。",
          pattern=r"理赔率\s*100\s*%|100\s*%(获赔|理赔成功)"),
    _rule("C15", "医疗险宣传不得隐瞒免赔额、报销范围与续保条件。",
          "检查医疗险表述是否淡化免赔额/既往症除外/非保证续保。",
          "中", "标明免赔额、报销比例与续保条款要点。"),
    _rule("C16", "不得诱导客户带病投保时隐瞒健康告知。",
          "检查是否暗示客户不如实告知也能承保/获赔。",
          "高", "强调如实告知义务及不实告知的解除合同与拒赔后果。",
          pattern=r"带病(也能|照样|一样)(投保|买|承保)|不用(做)?(健康)?告知|隐瞒.{0,4}(病史|告知).{0,4}没(关系|事)"),
    _rule("C17", "重疾险宣传不得夸大轻症/中症/重疾的赔付条件宽松程度。",
          "检查是否宣称「确诊即赔」适用于全部病种(多数病种有状态/手术要求)。",
          "中", "区分确诊即赔/达到状态/实施手术三类赔付条件。",
          pattern=r"(所有|全部)(病种|重疾).{0,6}确诊(即|就)赔"),
    _rule("C18", "不得将保额、现金价值与投资收益概念混用误导。",
          "检查是否把保额增长表述为投资回报率。",
          "低", "区分保额、现金价值、IRR 概念,演示遵守口径。"),
    _rule("C19", "不得诱导客户退保旧单转购新单(退旧买新)。",
          "检查是否怂恿退保原有保单以购买新产品,未提示退保损失与等待期风险。",
          "高", "如确需保单调整,须完整提示现金价值损失、等待期重算与健康再核保风险。",
          pattern=r"退(了|掉)?(旧|原来|之前)的?(保单)?.{0,8}(换|转|再)?(买|购|投)|退保.{0,4}(换|转)购"),
    _rule("C20", "不得冒充官方通知口吻(保单失效、系统提醒)诱导客户联系。",
          "检查是否伪装成系统/官方通知制造恐慌。",
          "中", "以个人服务身份沟通,勿使用官方通知式话术。",
          pattern=r"【?(官方|系统)(通知|提醒)】?|保单(即将|已经)?(失效|作废).{0,8}(点击|联系|扫码)"),
    _rule("C21", "增员宣传不得承诺高额固定收入。",
          "检查增员物料是否承诺月入过万等固定收入。",
          "中", "以真实的基本法制度与成长路径说明,不承诺收入。",
          pattern=r"(轻松|保底)?月(入|薪)(过|破)?([3-9]|[1-9]\d)万?千?元?(以上)?|躺赚"),
    _rule("C22", "不得使用绝对安全、万无一失等绝对化风险表述。",
          "检查是否将产品或资金安全性绝对化。",
          "中", "改为客观描述监管保障机制(如保险保障基金)。",
          pattern=r"绝对安全|万无一失|百分之?百安全",
          resolution={"priority": 30, "suppressed_by": ["C01"],
                      "suppressed_by_keys": [f"{_RS}:C01"]}),
    _rule("C23", "不得虚称国家规定、政府要求人人必须购买商业保险。",
          "检查是否假借政策名义制造购买义务。",
          "高", "如涉政策(如个税递延),引用文件原文并注明适用条件。",
          pattern=r"(国家|政府)(规定|要求|强制).{0,8}(必须|人人|每人).{0,4}(买|投保)"),
    _rule("C24", "低价对比话术(一杯咖啡钱)不得掩盖缴费期限与总保费。",
          "检查日均保费话术是否隐瞒缴费年限与保费总额。",
          "低", "同时披露年缴保费、缴费期与保障期限。"),
]


def ensure_compliance_rules(db) -> int:
    """预置行业合规规则(幂等:规则集已存在即跳过)。返回新增条数。"""
    if db.query(ComplianceRule).filter(ComplianceRule.rule_set_id == _RS).count() > 0:
        return 0
    from app.services import compliance

    result = compliance.add_rules(db, SEED_COMPLIANCE_RULES)
    return len(result["added"])
