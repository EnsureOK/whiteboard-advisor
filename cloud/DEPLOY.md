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

## 支付宝当面付(国内扫码)

沙箱联调(现在就能做):
1. https://open.alipay.com -> 控制台 -> 沙箱环境,拿沙箱 APPID
2. 用「密钥工具」生成 RSA2 应用密钥对,上传应用公钥,得到「支付宝公钥」
3. 云端 .env 追加(网关默认已指沙箱):

```
ALIPAY_APPID=<沙箱 APPID>
ALIPAY_APP_PRIVATE_KEY=<应用私钥,纯 base64 体即可>
ALIPAY_PUBLIC_KEY=<支付宝公钥>
ALIPAY_NOTIFY_URL=https://<域名>/api/billing/alipay/notify   # 本地联调可不配,轮询兜底
```

4. 手机装「沙箱版支付宝」App(开放平台下载),用沙箱买家账号扫码付
5. 生产切换:正式 APPID/密钥 + ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do
   (需企业支付宝完成「当面付」产品签约,费率 0.38%)

前端行为:配置了支付宝后,计费面板「开通/购买」自动弹二维码 + 3s 轮询到账;
未配置则回落 Stripe(海外)或演示通道。

## 微信支付 Native(骨架)

config 已留 WXPAY_* 字段;待商户号(pay.weixin.qq.com,需营业执照)下来后
补 transactions/native 下单与回调验签,结构与支付宝对称。
