import { useCallback, useEffect, useState } from "react";
import type { AuthUser, BillingStatus, PackDef, PlanDef } from "./api";
import { api, authToken } from "./api";
import { Icon, Spinner } from "./icons";

interface Props {
  onToast: (msg: string) => void;
  /** 登录态变化(登录/退出/余额变动)时通知外层刷新积分条 */
  onAuthChange: (user: AuthUser | null, status: BillingStatus | null) => void;
}

const yuan = (cents: number) => `¥${(cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)}`;

const SOURCE_LABEL: Record<string, string> = {
  signup_grant: "注册赠礼",
  plan_grant: "套餐积分",
  pack_grant: "积分包",
  redeem_grant: "兑换码",
  plan_expire: "套餐到期清零",
  consume: "消耗",
};

/** 计费视图:登录 / 余额 / 套餐与积分包 / 兑换码 / 订单流水 */
export default function BillingView({ onToast, onAuthChange }: Props) {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [plans, setPlans] = useState<PlanDef[]>([]);
  const [packs, setPacks] = useState<PackDef[]>([]);
  const [stripeOn, setStripeOn] = useState(false);
  const [ledger, setLedger] = useState<Awaited<ReturnType<typeof api.billingLedger>>>([]);
  const [buying, setBuying] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const refresh = useCallback(async () => {
    const p = await api.billingPlans();
    setPlans(p.plans.filter((x) => x.id !== "free"));
    setPacks(p.packs);
    setStripeOn(p.stripe);
    if (!authToken.get()) {
      setMe(null);
      setStatus(null);
      onAuthChange(null, null);
      return;
    }
    try {
      const user = await api.authMe();
      const st = await api.billingStatus();
      setMe(user);
      setStatus(st);
      setLedger(await api.billingLedger());
      onAuthChange(user, st);
    } catch {
      authToken.clear();
      setMe(null);
      setStatus(null);
      onAuthChange(null, null);
    }
  }, [onAuthChange]);

  useEffect(() => {
    refresh().catch((e) => onToast(`加载计费信息失败: ${e.message}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const buy = async (item: string) => {
    setBuying(item);
    try {
      const r = await api.billingCheckout(item);
      if (r.demo) {
        onToast("演示通道:已直接开通(配置 Stripe 后为真实支付)");
        await refresh();
      } else if (r.checkoutUrl) {
        window.location.href = r.checkoutUrl; // 跳 Stripe Checkout(支付宝/微信)
      }
    } catch (e: any) {
      onToast(`下单失败: ${e.message}`);
    } finally {
      setBuying(null);
    }
  };

  const redeem = async () => {
    if (!code.trim()) return;
    try {
      await api.billingRedeem(code.trim());
      setCode("");
      onToast("兑换成功");
      await refresh();
    } catch (e: any) {
      onToast(`兑换失败: ${e.message}`);
    }
  };

  return (
    <div className="wb-billing">
      <div className="wb-billing-inner">
        {!me ? (
          <LoginCard
            onDone={async () => {
              await refresh();
              onToast("登录成功");
            }}
            onToast={onToast}
          />
        ) : (
          <>
            <div className="wb-bill-head">
              <div>
                <div className="wb-bill-user">{me.displayName || me.username}</div>
                <div className="wb-bill-plan">
                  {status?.planName}
                  {status?.active && status.planExpiresAt && (
                    <span className="wb-mono"> · {status.planExpiresAt.slice(0, 10)} 到期</span>
                  )}
                </div>
              </div>
              <button
                className="wb-btn ghost"
                onClick={() => {
                  authToken.clear();
                  refresh();
                  onToast("已退出登录");
                }}
              >
                退出
              </button>
            </div>

            {status && !status.welcomeClaimed && (
              <div className="wb-claim-card">
                <div>
                  <div className="wb-claim-title">🎁 新用户礼:{status.welcomeCredits.toLocaleString()} 免费积分</div>
                  <div className="wb-claim-sub">约 {(status.welcomeCredits * 2000 / 10000).toLocaleString()} 万 tokens,永不过期,点击即到账</div>
                </div>
                <button
                  className="wb-btn"
                  disabled={buying === "__welcome__"}
                  onClick={async () => {
                    setBuying("__welcome__");
                    try {
                      const r = await api.billingClaimWelcome();
                      onToast(r.claimed ? `已领取 ${status.welcomeCredits.toLocaleString()} 积分 🎉` : "你已领取过了");
                      await refresh();
                    } catch (e: any) {
                      onToast(`领取失败: ${e.message}`);
                    } finally {
                      setBuying(null);
                    }
                  }}
                >
                  {buying === "__welcome__" ? <Spinner size={12} /> : null}
                  立即领取
                </button>
              </div>
            )}

            {status && (
              <div className="wb-bill-balance">
                <div className="wb-bill-total">
                  <span className="wb-mono">{status.credits.totalCredits.toLocaleString()}</span>
                  <label>可用积分</label>
                </div>
                <div className="wb-bill-rows">
                  <div>
                    套餐积分 <span className="wb-mono">{status.credits.planCredits.toLocaleString()}</span>
                    <em>(随套餐到期清零)</em>
                  </div>
                  <div>
                    积分包 <span className="wb-mono">{status.credits.packCredits.toLocaleString()}</span>
                    <em>(永不过期)</em>
                  </div>
                  <div>
                    本月消耗 <span className="wb-mono">{status.monthConsumedCredits.toLocaleString()}</span>
                    <em>(1 积分 ≈ 2000 tokens)</em>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        <div className="wb-bill-section">月付套餐 <span className="wb-bill-note">一次支付开通 30 天,到期提醒续费{stripeOn ? " · 支持支付宝/微信" : " · 演示通道(未配置 Stripe)"}</span></div>
        <div className="wb-plan-cards">
          {plans.map((p) => {
            const current = status?.active && status.plan === p.id;
            return (
              <div key={p.id} className={"wb-plan-card" + (current ? " current" : "")}>
                <div className="wb-plan-name">{p.name}</div>
                <div className="wb-plan-price">
                  {yuan(p.priceCents)}
                  <span>/月</span>
                </div>
                <div className="wb-plan-credits wb-mono">{p.monthlyCredits.toLocaleString()} 积分/月</div>
                <ul>
                  {p.features.map((f) => (
                    <li key={f}>
                      <Icon name="check" size={11} strokeWidth={2.4} />
                      {f}
                    </li>
                  ))}
                </ul>
                <button
                  className={"wb-btn" + (current ? " ghost" : "")}
                  disabled={buying !== null || !me}
                  onClick={() => buy(p.id)}
                >
                  {buying === p.id ? <Spinner size={12} /> : null}
                  {current ? "续费 30 天" : me ? "开通" : "先登录"}
                </button>
              </div>
            );
          })}
        </div>

        <div className="wb-bill-section">积分包 <span className="wb-bill-note">不过期,随用随充</span></div>
        <div className="wb-pack-cards">
          {packs.map((p) => (
            <div key={p.id} className="wb-pack-card">
              <span className="wb-pack-name">{p.name}</span>
              <span className="wb-mono">{p.credits.toLocaleString()} 积分</span>
              <button className="wb-btn ghost" disabled={buying !== null || !me} onClick={() => buy(p.id)}>
                {buying === p.id ? <Spinner size={12} /> : null}
                {yuan(p.priceCents)}
              </button>
            </div>
          ))}
          <div className="wb-pack-card">
            <span className="wb-pack-name">兑换码</span>
            <input
              className="wb-text-input"
              placeholder="PRO-XXXX-XXXX"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && redeem()}
              disabled={!me}
            />
            <button className="wb-btn ghost" disabled={!me || !code.trim()} onClick={redeem}>
              兑换
            </button>
          </div>
        </div>

        {me && ledger.length > 0 && (
          <>
            <div className="wb-bill-section">积分流水</div>
            <div className="wb-ledger">
              {ledger.slice(0, 12).map((r) => (
                <div key={r.id} className="wb-ledger-row">
                  <span className={"wb-ledger-delta wb-mono" + (r.deltaCredits < 0 ? " neg" : " pos")}>
                    {r.deltaCredits > 0 ? "+" : ""}
                    {r.deltaCredits.toLocaleString()}
                  </span>
                  <span>{SOURCE_LABEL[r.source] || r.source}</span>
                  <span className="wb-ledger-ref">{r.ref}</span>
                  <span className="wb-ledger-time wb-mono">{r.createdAt.slice(5, 16).replace("T", " ")}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function LoginCard({ onDone, onToast }: { onDone: () => void; onToast: (m: string) => void }) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const [busy, setBusy] = useState(false);
  const [pwMode, setPwMode] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const phoneOk = /^1[3-9]\d{9}$/.test(phone.replace(/\s/g, ""));

  const sendCode = async () => {
    if (!phoneOk || cooldown > 0) return;
    try {
      const r = await api.authSmsSend(phone.replace(/\s/g, ""));
      setCooldown(r.cooldown || 60);
      onToast(
        r.provider === "outbox"
          ? "验证码已生成(内测期:请向管理员索取)"
          : "验证码已发送,请查收短信"
      );
    } catch (e: any) {
      onToast(`发送失败: ${e.message}`);
    }
  };

  const submitSms = async () => {
    if (!phoneOk || code.trim().length < 4 || busy) return;
    setBusy(true);
    try {
      const r = await api.authSmsVerify(phone.replace(/\s/g, ""), code.trim());
      authToken.set(r.token);
      onDone();
    } catch (e: any) {
      onToast(`登录失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const submitPw = async () => {
    if (!username.trim() || password.length < 8 || busy) return;
    setBusy(true);
    try {
      const r = await api.authLogin(username.trim(), password);
      authToken.set(r.token);
      onDone();
    } catch (e: any) {
      onToast(`登录失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wb-login-card">
      <div className="wb-login-head">
        <div className="wb-login-title">登录工作台</div>
        <div className="wb-login-sub">首次登录自动创建账号,并可领取 2,000 免费积分</div>
      </div>
      {!pwMode ? (
        <>
          <input
            className="wb-text-input"
            inputMode="numeric"
            placeholder="手机号"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="wb-text-input"
              style={{ flex: 1 }}
              inputMode="numeric"
              placeholder="短信验证码"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitSms()}
            />
            <button className="wb-btn ghost" disabled={!phoneOk || cooldown > 0} onClick={sendCode}>
              {cooldown > 0 ? <span className="wb-mono">{cooldown}s</span> : "获取验证码"}
            </button>
          </div>
          <button className="wb-btn" disabled={busy || !phoneOk || code.trim().length < 4} onClick={submitSms}>
            {busy ? <Spinner size={12} /> : null}
            登录 / 注册
          </button>
        </>
      ) : (
        <>
          <input
            className="wb-text-input"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            className="wb-text-input"
            type="password"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitPw()}
          />
          <button className="wb-btn" disabled={busy || !username.trim() || password.length < 8} onClick={submitPw}>
            {busy ? <Spinner size={12} /> : null}
            登录
          </button>
        </>
      )}
      <button className="wb-login-switch" onClick={() => setPwMode(!pwMode)}>
        {pwMode ? "← 使用手机号登录" : "使用账号密码登录"}
      </button>
    </div>
  );
}
