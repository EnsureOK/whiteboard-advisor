/**
 * 会员计费云函数(CloudBase serverless)。
 *
 * 部署后前端直连云函数 HTTP 访问入口(或 SDK 调用),无需自建后端。
 * 集合: billing_users / billing_orders / billing_codes
 *
 * actions:
 *  - status   { userId }              -> 当前套餐与配额
 *  - redeem   { userId, code }        -> 兑换码开通会员
 *  - order    { userId, plan, channel } -> 创建订单(微信支付预留统一下单参数)
 *  - payCallback (微信支付回调,验签后标记 paid 并延长会员)
 */

const cloud = require("@cloudbase/node-sdk");

const PLANS = {
  free: { name: "免费版", priceCents: 0, taskQuota: 3, days: 0 },
  pro: { name: "专业版", priceCents: 2900, taskQuota: -1, days: 30 },
  pro_year: { name: "专业版(年付)", priceCents: 26800, taskQuota: -1, days: 365 },
};

let app;
function sdk() {
  if (!app) app = cloud.init({ env: cloud.SYMBOL_CURRENT_ENV });
  return app;
}

function monthStartIso() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString();
}

async function getUser(db, userId) {
  const res = await db.collection("billing_users").doc(userId).get();
  return res.data && res.data[0];
}

async function grantPlan(db, userId, planKey) {
  const plan = PLANS[planKey];
  const user = await getUser(db, userId);
  const now = Date.now();
  let base = now;
  if (user && user.planExpiresAt && new Date(user.planExpiresAt).getTime() > now) {
    base = new Date(user.planExpiresAt).getTime();
  }
  const expires = new Date(base + plan.days * 86400000).toISOString();
  await db.collection("billing_users").doc(userId).set({ plan: planKey, planExpiresAt: expires });
  return expires;
}

exports.main = async (event) => {
  const db = sdk().database();
  const action = event.action;

  try {
    if (action === "status") {
      const user = await getUser(db, event.userId);
      const active =
        user && user.plan !== "free" && (!user.planExpiresAt || new Date(user.planExpiresAt) > new Date());
      const key = active ? user.plan : "free";
      const plan = PLANS[key] || PLANS.free;
      const usedRes = await db
        .collection("billing_orders")
        .where({ userId: event.userId, month: monthStartIso().slice(0, 7) })
        .count();
      return {
        ok: true,
        plan: key,
        planName: plan.name,
        active: !!active,
        planExpiresAt: active ? user.planExpiresAt : null,
        taskQuota: plan.taskQuota,
        taskUsed: usedRes.total,
      };
    }

    if (action === "redeem") {
      const code = String(event.code || "").trim().toUpperCase();
      const res = await db.collection("billing_codes").where({ code, status: "unused" }).get();
      if (!res.data.length) return { ok: false, error: "兑换码无效或已被使用" };
      const rc = res.data[0];
      const expires = await grantPlan(db, event.userId, rc.plan);
      await db.collection("billing_codes").doc(rc._id).update({
        status: "used",
        usedBy: event.userId,
        usedAt: new Date().toISOString(),
      });
      await db.collection("billing_orders").add({
        userId: event.userId,
        plan: rc.plan,
        amountCents: 0,
        channel: "redeem",
        status: "paid",
        meta: { code },
        createdAt: new Date().toISOString(),
      });
      return { ok: true, plan: rc.plan, planExpiresAt: expires };
    }

    if (action === "order") {
      const plan = PLANS[event.plan];
      if (!plan || event.plan === "free") return { ok: false, error: "无效套餐" };
      const order = {
        userId: event.userId,
        plan: event.plan,
        amountCents: plan.priceCents,
        channel: event.channel || "wechat_pay",
        status: "created",
        createdAt: new Date().toISOString(),
      };
      const added = await db.collection("billing_orders").add(order);
      // 生产版:此处调用微信支付统一下单,返回 payParams 给前端
      return { ok: true, orderId: added.id, payParams: null };
    }

    if (action === "payCallback") {
      // 生产版:验签微信支付回调后:
      // 1. 订单标记 paid  2. grantPlan 延长会员
      return { ok: true, todo: "接入微信支付后实现验签" };
    }

    return { ok: false, error: `unknown action: ${action}` };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
};
