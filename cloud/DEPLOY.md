# 云端网关部署(WB_ROLE=cloud)

一台国内云服务器即可(2C4G 起步)。职责:账户(SMS 登录)+ 积分计费 +
LLM 代理网关 + 支付回调。客户数据永远留在经纪人本地,不上云。

## 启动

```bash
cd backend && pip install -r requirements.txt
WB_ROLE=cloud WB_DATA_DIR=/srv/workbench-cloud \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`/srv/workbench-cloud/.env` 必填:

```
QIANFAN_API_KEY=<团队千帆 key,只存在服务端>
QIANFAN_MODEL_FAST=glm-5.3-flash
QIANFAN_MODEL_DEEP=glm-5.3-flash
JWT_SECRET=<随机长串>
SMS_PROVIDER=aliyun          # 生产短信;不配则验证码写 outbox 日志
STRIPE_API_KEY=...           # 海外通道(可选)
STRIPE_WEBHOOK_SECRET=...
```

生产建议:Nginx/Caddy 反代 + HTTPS(备案域名),systemd 守护。

## 暴露的路由

- `/api/auth/*` 注册/SMS 登录 -> JWT
- `/api/billing/*` 套餐/积分包/流水/claim-welcome/Stripe(后续:支付宝/微信)
- `/llm/v2/chat/completions` `/llm/v2/embeddings` 千帆兼容代理:
  验 JWT -> 查余额(不足 402) -> 换真 key 转发(流式透传) -> usage 扣积分
- workbench/kb 等业务路由在 cloud 模式不挂载(404)

## 桌面端接入(零代码改动)

数据目录 `.env`:

```
QIANFAN_BASE_URL=https://<你的域名>/llm/v2
QIANFAN_API_KEY=<用户登录后获得的 JWT>
```

本地 llm.py / agents SDK / embedding 的调用协议与千帆一致,直接生效。
