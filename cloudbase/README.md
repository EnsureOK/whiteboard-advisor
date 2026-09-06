# 会员计费 serverless 部署(CloudBase 云函数)

付费功能的"无后端"形态:订单、权益、兑换码全部托管在腾讯云开发 CloudBase 的云函数 + 云数据库,
前端直连云函数,不依赖自建 FastAPI。`backend/app/api/billing.py` 是同构的本地适配器,
用于开发调试与降级。

## 部署步骤

1. 登录 CloudBase(本机 MCP 已装,或用 CLI):

   ```bash
   tcb login            # 扫码登录腾讯云
   tcb env list         # 找到你的环境 ID
   ```

2. 创建云函数并部署:

   ```bash
   cd cloudbase/billing
   npm install
   tcb fn deploy billing --dir . --env <你的环境ID> --force
   ```

3. 初始化云数据库集合(CloudBase 控制台或 CLI):
   `billing_users`、`billing_orders`、`billing_codes`;
   并写入兑换码文档,如:

   ```json
   { "code": "PRO-DEMO-0001", "plan": "pro", "days": 30, "status": "unused" }
   ```

4. 配置 HTTP 访问服务,拿到形如
   `https://<envId>.service.tcloudbase.com/billing` 的入口地址,
   在前端 `.env` 里配置:

   ```
   VITE_BILLING_URL=https://<envId>.service.tcloudbase.com/billing
   ```

   配置后前端会员中心直连云函数(serverless);未配置时走本地
   `/api/billing/*` 适配器,功能一致。

## 微信支付接入

`order` / `payCallback` 两个 action 已预留:
- `order`:创建订单后调用微信支付统一下单(API v3),把 `payParams`
  返回给前端拉起支付;
- `payCallback`:配置为支付回调通知地址,验签后把订单标记 `paid`
  并调用 `grantPlan` 延长会员。

需要准备:微信支付商户号(mchid)、商户 API 证书、APIv3 密钥,
在 CloudBase 控制台「集成中心」开通微信支付后可免验签直连。
